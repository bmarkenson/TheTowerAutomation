using System.Collections.Concurrent;
using System.IO.Pipes;
using TheTower.TunnelHost.Core;
using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost;

internal sealed class TunnelHostNamedPipeServer
{
    private readonly UserScopedIpcIdentity _identity;
    private readonly TunnelHostCoordinator _coordinator;
    private readonly CancellationTokenSource _shutdown;
    private readonly TunnelHostIdlePolicy _idlePolicy = new();
    private readonly ConcurrentDictionary<int, Task> _connections = new();
    private int _connectionSequence;

    public TunnelHostNamedPipeServer(
        UserScopedIpcIdentity identity,
        TunnelHostCoordinator coordinator,
        CancellationTokenSource shutdown)
    {
        _identity = identity;
        _coordinator = coordinator;
        _shutdown = shutdown;
    }

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var idleTask = MonitorIdleAsync(cancellationToken);
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                var pipe = new NamedPipeServerStream(
                    _identity.PipeName,
                    PipeDirection.InOut,
                    NamedPipeServerStream.MaxAllowedServerInstances,
                    PipeTransmissionMode.Byte,
                    TunnelHostPipeSecurity.ServerOptions,
                    inBufferSize: 16 * 1024,
                    outBufferSize: 16 * 1024);
                try
                {
                    await pipe.WaitForConnectionAsync(cancellationToken);
                }
                catch
                {
                    pipe.Dispose();
                    throw;
                }

                var id = Interlocked.Increment(ref _connectionSequence);
                var task = HandleConnectionAsync(pipe, cancellationToken);
                _connections[id] = task;
                _ = task.ContinueWith(
                    completedTask => _connections.TryRemove(id, out _),
                    CancellationToken.None,
                    TaskContinuationOptions.ExecuteSynchronously,
                    TaskScheduler.Default);
            }
        }
        finally
        {
            try
            {
                await idleTask;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                // Expected host shutdown.
            }
            await Task.WhenAll(_connections.Values);
        }
    }

    private async Task HandleConnectionAsync(
        NamedPipeServerStream pipe,
        CancellationToken hostCancellation)
    {
        await using (pipe)
        {
            _coordinator.ClientConnected();
            try
            {
                while (pipe.IsConnected && !hostCancellation.IsCancellationRequested)
                {
                    TunnelHostRequest? request;
                    try
                    {
                        request = await ProtocolFraming.ReadAsync<TunnelHostRequest>(
                            pipe,
                            hostCancellation);
                    }
                    catch (Exception exc) when (
                        exc is IOException or InvalidDataException)
                    {
                        return;
                    }
                    if (request is null)
                    {
                        return;
                    }

                    var response = !TunnelHostProtocol.IsSupportedVersion(
                        request.ProtocolVersion)
                            ? ProtocolMismatch(request)
                            : await HandleRequestAsync(request, hostCancellation);
                    try
                    {
                        await ProtocolFraming.WriteAsync(
                            pipe,
                            response,
                            hostCancellation);
                    }
                    catch (IOException)
                    {
                        return;
                    }
                    if (response.ShutdownRequested)
                    {
                        _shutdown.Cancel();
                        return;
                    }
                }
            }
            finally
            {
                _coordinator.ClientDisconnected();
            }
        }
    }

    private async Task<TunnelHostResponse> HandleRequestAsync(
        TunnelHostRequest request,
        CancellationToken cancellationToken)
    {
        try
        {
            return await _coordinator.HandleAsync(request, cancellationToken);
        }
        catch (TunnelHostCommandException exc)
        {
            return new TunnelHostResponse
            {
                RequestId = request.RequestId,
                Ok = false,
                ErrorCode = exc.ErrorCode,
                ErrorMessage = exc.Message,
                Snapshot = exc.Snapshot ?? _coordinator.Snapshot(),
            };
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            return new TunnelHostResponse
            {
                RequestId = request.RequestId,
                Ok = false,
                ErrorCode = "host_error",
                ErrorMessage = exc.Message,
                Snapshot = _coordinator.Snapshot(),
            };
        }
    }

    private TunnelHostResponse ProtocolMismatch(TunnelHostRequest request) => new()
    {
        RequestId = request.RequestId,
        Ok = false,
        ErrorCode = "protocol_mismatch",
        ErrorMessage =
            $"Protocol {request.ProtocolVersion} is unsupported by this tunnel host.",
        Compatibility = new TunnelHostCompatibility
        {
            MinimumProtocolVersion = TunnelHostProtocol.MinimumSupportedVersion,
            MaximumProtocolVersion = TunnelHostProtocol.MaximumSupportedVersion,
            HostVersion = _coordinator.HostVersion,
            HostProcessId = _coordinator.HostProcessId,
            HostInstanceId = _coordinator.HostInstanceId,
            HostStartedAt = _coordinator.HostStartedAt,
            HostExecutablePath = Environment.ProcessPath ?? "",
        },
    };

    private async Task MonitorIdleAsync(CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
        while (await timer.WaitForNextTickAsync(cancellationToken))
        {
            if (_idlePolicy.ShouldExit(
                    _coordinator.Snapshot(),
                    DateTimeOffset.UtcNow))
            {
                _shutdown.Cancel();
                return;
            }
        }
    }
}
