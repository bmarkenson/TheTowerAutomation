using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost.Core;

public sealed class TunnelSupervisor : IAsyncDisposable
{
    private static readonly TimeSpan[] DefaultRetrySchedule =
    [
        TimeSpan.FromSeconds(5),
        TimeSpan.FromSeconds(10),
        TimeSpan.FromSeconds(20),
        TimeSpan.FromSeconds(30),
    ];

    private readonly object _stateGate = new();
    private readonly SemaphoreSlim _operationGate = new(1, 1);
    private readonly TunnelKind _kind;
    private readonly ISshTunnelProcessFactory _processFactory;
    private readonly IAsyncDelay _delay;
    private readonly IReadOnlyList<TimeSpan> _retrySchedule;
    private readonly TimeSpan _startupSettleDelay;
    private bool _desired;
    private TunnelObservedState _observedState = TunnelObservedState.Stopped;
    private IManagedSshProcess? _process;
    private TunnelEndpoint? _activeEndpoint;
    private int _retryAttempt;
    private DateTimeOffset? _retryAt;
    private CancellationTokenSource? _retryCancellation;
    private SshDiagnostic? _lastDiagnostic;
    private DateTimeOffset _stateChangedAt;
    private long _revision;
    private bool _disposed;

    public TunnelSupervisor(
        TunnelKind kind,
        ISshTunnelProcessFactory processFactory,
        IAsyncDelay? delay = null,
        IReadOnlyList<TimeSpan>? retrySchedule = null,
        TimeSpan? startupSettleDelay = null)
    {
        _kind = kind;
        _processFactory = processFactory;
        _delay = delay ?? new SystemAsyncDelay();
        _retrySchedule = retrySchedule ?? DefaultRetrySchedule;
        if (_retrySchedule.Count == 0
            || _retrySchedule.Any(value => value < TimeSpan.Zero))
        {
            throw new ArgumentException(
                "At least one non-negative retry delay is required.",
                nameof(retrySchedule));
        }
        _startupSettleDelay = startupSettleDelay ?? TimeSpan.FromMilliseconds(750);
        _stateChangedAt = _delay.UtcNow;
    }

    public TunnelKind Kind => _kind;

    public TunnelStateSnapshot Snapshot()
    {
        lock (_stateGate)
        {
            return new TunnelStateSnapshot
            {
                Kind = _kind,
                Desired = _desired,
                ObservedState = _observedState,
                ProcessId = _process?.ProcessId,
                ActiveEndpoint = _activeEndpoint,
                RetryAttempt = _retryAttempt,
                RetryAt = _retryAt,
                LastDiagnostic = _lastDiagnostic,
                StateChangedAt = _stateChangedAt,
                Revision = _revision,
            };
        }
    }

    public async Task<TunnelStateSnapshot> StartAsync(
        TunnelHostConfiguration configuration,
        CancellationToken cancellationToken)
    {
        var endpoint = TunnelHostConfigurationValidator.EndpointFor(
            _kind,
            configuration);
        await _operationGate.WaitAsync(cancellationToken);
        try
        {
            ThrowIfDisposed();
            lock (_stateGate)
            {
                if (_process is not null)
                {
                    return Snapshot();
                }
                CancelRetryLocked();
                _desired = true;
                _retryAttempt = 0;
                _retryAt = null;
            }
            await StartProcessAsync(endpoint, cancellationToken);
            return Snapshot();
        }
        finally
        {
            _operationGate.Release();
        }
    }

    public async Task<TunnelStateSnapshot> StopAsync(
        CancellationToken cancellationToken)
    {
        await _operationGate.WaitAsync(cancellationToken);
        try
        {
            ThrowIfDisposed();
            await StopCoreAsync(cancellationToken);
            return Snapshot();
        }
        finally
        {
            _operationGate.Release();
        }
    }

    public async Task<TunnelStateSnapshot> RestartAsync(
        TunnelHostConfiguration configuration,
        CancellationToken cancellationToken)
    {
        var endpoint = TunnelHostConfigurationValidator.EndpointFor(
            _kind,
            configuration);
        await _operationGate.WaitAsync(cancellationToken);
        try
        {
            ThrowIfDisposed();
            await StopCoreAsync(cancellationToken);
            lock (_stateGate)
            {
                _desired = true;
                _retryAttempt = 0;
                _retryAt = null;
            }
            await StartProcessAsync(endpoint, cancellationToken);
            return Snapshot();
        }
        finally
        {
            _operationGate.Release();
        }
    }

    private async Task StopCoreAsync(CancellationToken cancellationToken)
    {
        IManagedSshProcess? process;
        lock (_stateGate)
        {
            _desired = false;
            CancelRetryLocked();
            _retryAttempt = 0;
            _retryAt = null;
            process = _process;
            if (process is null)
            {
                _activeEndpoint = null;
                SetStateLocked(TunnelObservedState.Stopped);
                return;
            }
            SetStateLocked(TunnelObservedState.Stopping);
        }

        ManagedSshProcessExit? exit = null;
        try
        {
            await process.StopAsync(cancellationToken);
            exit = await process.Completion.WaitAsync(cancellationToken);
        }
        finally
        {
            await process.DisposeAsync();
            lock (_stateGate)
            {
                if (ReferenceEquals(_process, process))
                {
                    _process = null;
                }
                _activeEndpoint = null;
                if (exit is not null)
                {
                    _lastDiagnostic = BuildDiagnostic(exit, expected: true);
                }
                SetStateLocked(TunnelObservedState.Stopped);
            }
        }
    }

    private async Task StartProcessAsync(
        TunnelEndpoint endpoint,
        CancellationToken cancellationToken)
    {
        lock (_stateGate)
        {
            _activeEndpoint = endpoint;
            SetStateLocked(TunnelObservedState.Starting);
        }

        IManagedSshProcess process;
        try
        {
            process = await _processFactory.StartAsync(
                _kind,
                endpoint,
                cancellationToken);
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            lock (_stateGate)
            {
                _lastDiagnostic = new SshDiagnostic
                {
                    ObservedAt = _delay.UtcNow,
                    FailureKind = SshFailureClassifier.Classify(exc.Message),
                    Summary = $"Unable to start ssh.exe for the {_kind} tunnel.",
                    RawDetail = LimitDetail(exc.ToString()),
                };
                if (_desired)
                {
                    ScheduleRetryLocked();
                }
                else
                {
                    _activeEndpoint = null;
                    SetStateLocked(TunnelObservedState.Stopped);
                }
            }
            return;
        }

        lock (_stateGate)
        {
            _process = process;
        }

        var settleTask = _delay.Delay(_startupSettleDelay, cancellationToken);
        var completed = await Task.WhenAny(process.Completion, settleTask);
        if (ReferenceEquals(completed, process.Completion))
        {
            var exit = await process.Completion;
            await process.DisposeAsync();
            lock (_stateGate)
            {
                if (ReferenceEquals(_process, process))
                {
                    _process = null;
                }
                HandleUnexpectedExitLocked(exit);
            }
            return;
        }

        lock (_stateGate)
        {
            if (!_desired)
            {
                return;
            }
            _retryAttempt = 0;
            _retryAt = null;
            SetStateLocked(TunnelObservedState.Running);
        }
        _ = ObserveExitAsync(process);
    }

    private async Task ObserveExitAsync(IManagedSshProcess process)
    {
        ManagedSshProcessExit exit;
        try
        {
            exit = await process.Completion;
        }
        catch (Exception exc)
        {
            exit = new ManagedSshProcessExit(
                -1,
                exc.ToString(),
                false,
                _delay.UtcNow);
        }

        await _operationGate.WaitAsync();
        try
        {
            lock (_stateGate)
            {
                if (!ReferenceEquals(_process, process))
                {
                    return;
                }
                _process = null;
                HandleUnexpectedExitLocked(exit);
            }
        }
        finally
        {
            _operationGate.Release();
            await process.DisposeAsync();
        }
    }

    private void HandleUnexpectedExitLocked(ManagedSshProcessExit exit)
    {
        _lastDiagnostic = BuildDiagnostic(exit, expected: !_desired || exit.Expected);
        if (!_desired || exit.Expected)
        {
            _desired = false;
            _activeEndpoint = null;
            _retryAttempt = 0;
            _retryAt = null;
            SetStateLocked(TunnelObservedState.Stopped);
            return;
        }
        if (_lastDiagnostic.FailureKind == SshFailureKind.ForwardConflict)
        {
            _retryAt = null;
            SetStateLocked(TunnelObservedState.Conflict);
            return;
        }
        ScheduleRetryLocked();
    }

    private void ScheduleRetryLocked()
    {
        if (!_desired || _disposed || _activeEndpoint is null)
        {
            _retryAt = null;
            SetStateLocked(_desired
                ? TunnelObservedState.Faulted
                : TunnelObservedState.Stopped);
            return;
        }
        CancelRetryLocked();
        _retryAttempt++;
        var delay = _retrySchedule[Math.Min(
            _retryAttempt - 1,
            _retrySchedule.Count - 1)];
        _retryAt = _delay.UtcNow + delay;
        SetStateLocked(TunnelObservedState.RetryWaiting);
        var cancellation = new CancellationTokenSource();
        _retryCancellation = cancellation;
        _ = RetryAfterDelayAsync(delay, cancellation);
    }

    private async Task RetryAfterDelayAsync(
        TimeSpan retryDelay,
        CancellationTokenSource cancellation)
    {
        try
        {
            await _delay.Delay(retryDelay, cancellation.Token);
            await _operationGate.WaitAsync(cancellation.Token);
            try
            {
                TunnelEndpoint? endpoint;
                lock (_stateGate)
                {
                    if (!_desired
                        || !ReferenceEquals(_retryCancellation, cancellation))
                    {
                        return;
                    }
                    _retryCancellation = null;
                    _retryAt = null;
                    endpoint = _activeEndpoint;
                }
                if (endpoint is not null)
                {
                    await StartProcessAsync(endpoint, cancellation.Token);
                }
            }
            finally
            {
                _operationGate.Release();
            }
        }
        catch (OperationCanceledException)
        {
            // Explicit Stop/Restart owns cancellation of a pending retry.
        }
        finally
        {
            lock (_stateGate)
            {
                if (ReferenceEquals(_retryCancellation, cancellation))
                {
                    _retryCancellation = null;
                }
            }
            cancellation.Dispose();
        }
    }

    private SshDiagnostic BuildDiagnostic(
        ManagedSshProcessExit exit,
        bool expected)
    {
        var raw = LimitDetail(exit.RawStderr.Trim());
        var summary = expected
            ? $"{_kind} tunnel stopped."
            : string.IsNullOrWhiteSpace(raw)
                ? $"{_kind} tunnel exited with SSH code {exit.ExitCode}."
                : $"{_kind} tunnel exited with SSH code {exit.ExitCode}: {raw}";
        return new SshDiagnostic
        {
            ObservedAt = exit.ExitedAt,
            FailureKind = expected
                ? SshFailureKind.None
                : SshFailureClassifier.Classify(raw, exit.ExitCode),
            ExitCode = exit.ExitCode,
            Summary = summary,
            RawDetail = raw,
            Expected = expected,
        };
    }

    private static string LimitDetail(string detail) =>
        detail.Length <= 4000 ? detail : detail[..4000];

    private void SetStateLocked(TunnelObservedState state)
    {
        _observedState = state;
        _stateChangedAt = _delay.UtcNow;
        _revision++;
    }

    private void CancelRetryLocked()
    {
        var cancellation = _retryCancellation;
        _retryCancellation = null;
        cancellation?.Cancel();
    }

    private void ThrowIfDisposed()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
    }

    public async ValueTask DisposeAsync()
    {
        if (_disposed)
        {
            return;
        }
        await _operationGate.WaitAsync();
        try
        {
            if (_disposed)
            {
                return;
            }
            await StopCoreAsync(CancellationToken.None);
            _disposed = true;
        }
        finally
        {
            _operationGate.Release();
        }
    }
}
