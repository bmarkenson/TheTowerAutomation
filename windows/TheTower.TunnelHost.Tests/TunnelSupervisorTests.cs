using System.Collections.Concurrent;
using TheTower.TunnelHost.Core;
using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost.Tests;

public sealed class TunnelSupervisorTests
{
    [Fact]
    public async Task IndependentApiTunnelSurvivesAdbConflict()
    {
        var apiProcess = new FakeProcess(101);
        var adbProcess = new FakeProcess(202);
        adbProcess.Exit(
            255,
            "Error: remote port forwarding failed for listen port 5565");
        var api = Supervisor(TunnelKind.Api, new FakeFactory(apiProcess));
        var adb = Supervisor(TunnelKind.Adb, new FakeFactory(adbProcess));

        var apiState = await api.StartAsync(
            ProtocolTests.TestConfiguration(),
            CancellationToken.None);
        var adbState = await adb.StartAsync(
            ProtocolTests.TestConfiguration(),
            CancellationToken.None);

        Assert.Equal(TunnelObservedState.Running, apiState.ObservedState);
        Assert.True(apiState.Desired);
        Assert.Equal(101, apiState.ProcessId);
        Assert.Equal(TunnelObservedState.Conflict, adbState.ObservedState);
        Assert.True(adbState.Desired);
        Assert.Null(adbState.RetryAt);
        Assert.Equal(
            SshFailureKind.ForwardConflict,
            adbState.LastDiagnostic?.FailureKind);
        Assert.Equal(TunnelObservedState.Running, api.Snapshot().ObservedState);

        await api.DisposeAsync();
        await adb.DisposeAsync();
    }

    [Fact]
    public async Task UnexpectedExitReconnectsWithoutChangingOtherTunnel()
    {
        var delay = new ControlledDelay();
        var firstAdb = new FakeProcess(201);
        var secondAdb = new FakeProcess(202);
        var apiProcess = new FakeProcess(101);
        var adbFactory = new FakeFactory(firstAdb, secondAdb);
        var api = Supervisor(TunnelKind.Api, new FakeFactory(apiProcess));
        var adb = new TunnelSupervisor(
            TunnelKind.Adb,
            adbFactory,
            delay,
            [TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(30)],
            TimeSpan.Zero);
        await api.StartAsync(ProtocolTests.TestConfiguration(), CancellationToken.None);
        await adb.StartAsync(ProtocolTests.TestConfiguration(), CancellationToken.None);

        firstAdb.Exit(255, "Connection timed out");
        await EventuallyAsync(() =>
            adb.Snapshot().ObservedState == TunnelObservedState.RetryWaiting);
        var waiting = adb.Snapshot();
        Assert.True(waiting.Desired);
        Assert.Equal(1, waiting.RetryAttempt);
        Assert.Equal(delay.UtcNow + TimeSpan.FromSeconds(5), waiting.RetryAt);
        Assert.Equal(TunnelObservedState.Running, api.Snapshot().ObservedState);

        delay.ReleaseNext();
        await EventuallyAsync(() =>
            adb.Snapshot().ObservedState == TunnelObservedState.Running);
        Assert.Equal(202, adb.Snapshot().ProcessId);
        Assert.Equal(TunnelObservedState.Running, api.Snapshot().ObservedState);

        await api.DisposeAsync();
        await adb.DisposeAsync();
    }

    [Fact]
    public async Task StopCancelsPendingReconnectAndClearsDesire()
    {
        var delay = new ControlledDelay();
        var process = new FakeProcess(301);
        var supervisor = new TunnelSupervisor(
            TunnelKind.Adb,
            new FakeFactory(process),
            delay,
            [TimeSpan.FromSeconds(5)],
            TimeSpan.Zero);
        await supervisor.StartAsync(
            ProtocolTests.TestConfiguration(),
            CancellationToken.None);
        process.Exit(255, "Connection refused");
        await EventuallyAsync(() =>
            supervisor.Snapshot().ObservedState == TunnelObservedState.RetryWaiting);

        var stopped = await supervisor.StopAsync(CancellationToken.None);
        delay.ReleaseAll();

        Assert.False(stopped.Desired);
        Assert.Equal(TunnelObservedState.Stopped, stopped.ObservedState);
        Assert.Null(stopped.RetryAt);
        await supervisor.DisposeAsync();
    }

    private static TunnelSupervisor Supervisor(
        TunnelKind kind,
        ISshTunnelProcessFactory factory) => new(
            kind,
            factory,
            new SystemAsyncDelay(),
            [TimeSpan.FromMilliseconds(1)],
            TimeSpan.Zero);

    internal static async Task EventuallyAsync(Func<bool> predicate)
    {
        for (var attempt = 0; attempt < 100; attempt++)
        {
            if (predicate())
            {
                return;
            }
            await Task.Delay(10);
        }
        Assert.True(predicate(), "The expected asynchronous state was not reached.");
    }

    internal sealed class FakeFactory : ISshTunnelProcessFactory
    {
        private readonly ConcurrentQueue<FakeProcess> _processes;

        public FakeFactory(params FakeProcess[] processes)
        {
            _processes = new ConcurrentQueue<FakeProcess>(processes);
        }

        public Task<IManagedSshProcess> StartAsync(
            TunnelKind kind,
            TunnelEndpoint endpoint,
            CancellationToken cancellationToken)
        {
            if (!_processes.TryDequeue(out var process))
            {
                throw new InvalidOperationException("No fake SSH process remains.");
            }
            return Task.FromResult<IManagedSshProcess>(process);
        }
    }

    internal sealed class FakeProcess : IManagedSshProcess
    {
        private readonly TaskCompletionSource<ManagedSshProcessExit> _completion =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        public FakeProcess(int processId)
        {
            ProcessId = processId;
        }

        public int ProcessId { get; }
        public Task<ManagedSshProcessExit> Completion => _completion.Task;
        public bool StopRequested { get; private set; }

        public void Exit(int exitCode, string stderr, bool expected = false) =>
            _completion.TrySetResult(new ManagedSshProcessExit(
                exitCode,
                stderr,
                expected,
                DateTimeOffset.UtcNow));

        public Task StopAsync(CancellationToken cancellationToken)
        {
            StopRequested = true;
            Exit(0, "", expected: true);
            return Task.CompletedTask;
        }

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }

    internal sealed class ControlledDelay : IAsyncDelay
    {
        private readonly ConcurrentQueue<TaskCompletionSource> _pending = new();

        public DateTimeOffset UtcNow { get; } =
            new(2026, 8, 1, 12, 0, 0, TimeSpan.Zero);

        public Task Delay(TimeSpan delay, CancellationToken cancellationToken)
        {
            if (delay == TimeSpan.Zero)
            {
                return Task.CompletedTask;
            }
            var completion = new TaskCompletionSource(
                TaskCreationOptions.RunContinuationsAsynchronously);
            cancellationToken.Register(() =>
                completion.TrySetCanceled(cancellationToken));
            _pending.Enqueue(completion);
            return completion.Task;
        }

        public void ReleaseNext()
        {
            Assert.True(_pending.TryDequeue(out var completion));
            completion.TrySetResult();
        }

        public void ReleaseAll()
        {
            while (_pending.TryDequeue(out var completion))
            {
                completion.TrySetResult();
            }
        }
    }
}
