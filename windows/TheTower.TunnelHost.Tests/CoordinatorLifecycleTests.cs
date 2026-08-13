using System.Diagnostics;
using TheTower.TunnelHost.Core;
using TheTower.TunnelProtocol;
using static TheTower.TunnelHost.Tests.TunnelSupervisorTests;

namespace TheTower.TunnelHost.Tests;

public sealed class CoordinatorLifecycleTests
{
    [Fact]
    public async Task GuiReattachmentRecoversDesiredObservedPidAndEndpoint()
    {
        var apiProcess = new FakeProcess(901);
        var configurationStore = new MemoryConfigurationStore();
        var coordinator = Coordinator(
            new FakeFactory(apiProcess),
            new FakeFactory(new FakeProcess(902)),
            configurationStore);
        coordinator.ClientConnected();
        await coordinator.HandleAsync(
            Request(
                TunnelHostCommand.StartTunnel,
                tunnel: TunnelKind.Api,
                configuration: ProtocolTests.TestConfiguration()),
            CancellationToken.None);
        coordinator.ClientDisconnected();

        coordinator.ClientConnected();
        var response = await coordinator.HandleAsync(
            Request(TunnelHostCommand.GetStatus, client: "reopened-gui"),
            CancellationToken.None);

        Assert.NotNull(response.Snapshot);
        Assert.True(response.Snapshot.ApiTunnel.Desired);
        Assert.Equal(
            TunnelObservedState.Running,
            response.Snapshot.ApiTunnel.ObservedState);
        Assert.Equal(901, response.Snapshot.ApiTunnel.ProcessId);
        Assert.Equal(
            "8787:127.0.0.1:8787",
            response.Snapshot.ApiTunnel.ActiveEndpoint?.ForwardSpecification);
        Assert.Equal(1, response.Snapshot.ConnectedGuiClients);
        await coordinator.DisposeAsync();
    }

    [Fact]
    public async Task ConfigureUpdatesDefaultsWithoutRestartingActiveTunnel()
    {
        var apiProcess = new FakeProcess(903);
        var store = new MemoryConfigurationStore();
        var coordinator = Coordinator(
            new FakeFactory(apiProcess),
            new FakeFactory(new FakeProcess(904)),
            store);
        var original = ProtocolTests.TestConfiguration();
        await coordinator.HandleAsync(
            Request(
                TunnelHostCommand.StartTunnel,
                tunnel: TunnelKind.Api,
                configuration: original),
            CancellationToken.None);
        var changed = original with
        {
            LocalApiPort = 8877,
            RemoteApiPort = 8877,
        };

        var response = await coordinator.HandleAsync(
            Request(
                TunnelHostCommand.Configure,
                configuration: changed),
            CancellationToken.None);

        Assert.Equal(changed, store.Configuration);
        Assert.Equal(changed, response.Snapshot?.Configuration);
        Assert.True(response.Snapshot?.ApiTunnel.Desired);
        Assert.Equal(903, response.Snapshot?.ApiTunnel.ProcessId);
        Assert.Equal(
            "8787:127.0.0.1:8787",
            response.Snapshot?.ApiTunnel.ActiveEndpoint?.ForwardSpecification);
        Assert.False(apiProcess.StopRequested);
        await coordinator.DisposeAsync();
    }

    [Fact]
    public async Task NewHostLoadsConfigurationButDoesNotReplayDesiredTunnels()
    {
        var store = new MemoryConfigurationStore
        {
            Configuration = ProtocolTests.TestConfiguration(),
        };
        var coordinator = Coordinator(
            new FakeFactory(new FakeProcess(101)),
            new FakeFactory(new FakeProcess(102)),
            store);

        var snapshot = coordinator.Snapshot();

        Assert.Equal(5565, snapshot.Configuration.LinuxAdbPort);
        Assert.False(snapshot.ApiTunnel.Desired);
        Assert.False(snapshot.AdbTunnel.Desired);
        Assert.Equal(TunnelObservedState.Stopped, snapshot.ApiTunnel.ObservedState);
        Assert.Equal(TunnelObservedState.Stopped, snapshot.AdbTunnel.ObservedState);
        await coordinator.DisposeAsync();
    }

    [Fact]
    public async Task ExplicitHostShutdownStopsBothOwnedChildren()
    {
        var apiProcess = new FakeProcess(401);
        var adbProcess = new FakeProcess(402);
        var coordinator = Coordinator(
            new FakeFactory(apiProcess),
            new FakeFactory(adbProcess),
            new MemoryConfigurationStore());
        await coordinator.HandleAsync(
            Request(
                TunnelHostCommand.StartTunnel,
                TunnelKind.Api,
                ProtocolTests.TestConfiguration()),
            CancellationToken.None);
        await coordinator.HandleAsync(
            Request(
                TunnelHostCommand.StartTunnel,
                TunnelKind.Adb,
                ProtocolTests.TestConfiguration()),
            CancellationToken.None);

        var response = await coordinator.HandleAsync(
            Request(TunnelHostCommand.ShutdownHost) with
            {
                ConfirmShutdown = true,
            },
            CancellationToken.None);

        Assert.True(response.ShutdownRequested);
        Assert.True(apiProcess.StopRequested);
        Assert.True(adbProcess.StopRequested);
        Assert.False(response.Snapshot?.ApiTunnel.Desired);
        Assert.False(response.Snapshot?.AdbTunnel.Desired);
        await coordinator.DisposeAsync();
    }

    [Fact]
    public async Task ExplicitTunnelRestartAndStopRemainIndependent()
    {
        var firstApi = new FakeProcess(501);
        var replacementApi = new FakeProcess(502);
        var adb = new FakeProcess(601);
        var coordinator = Coordinator(
            new FakeFactory(firstApi, replacementApi),
            new FakeFactory(adb),
            new MemoryConfigurationStore());
        await coordinator.HandleAsync(
            Request(
                TunnelHostCommand.StartTunnel,
                TunnelKind.Api,
                ProtocolTests.TestConfiguration()),
            CancellationToken.None);
        await coordinator.HandleAsync(
            Request(
                TunnelHostCommand.StartTunnel,
                TunnelKind.Adb,
                ProtocolTests.TestConfiguration()),
            CancellationToken.None);

        var restarted = await coordinator.HandleAsync(
            Request(
                TunnelHostCommand.RestartTunnel,
                TunnelKind.Api,
                ProtocolTests.TestConfiguration()),
            CancellationToken.None);

        Assert.True(firstApi.StopRequested);
        Assert.Equal(502, restarted.Snapshot?.ApiTunnel.ProcessId);
        Assert.Equal(601, restarted.Snapshot?.AdbTunnel.ProcessId);
        Assert.False(adb.StopRequested);

        var stopped = await coordinator.HandleAsync(
            Request(TunnelHostCommand.StopTunnel, TunnelKind.Adb),
            CancellationToken.None);

        Assert.True(adb.StopRequested);
        Assert.True(stopped.Snapshot?.ApiTunnel.Desired);
        Assert.Equal(
            TunnelObservedState.Running,
            stopped.Snapshot?.ApiTunnel.ObservedState);
        Assert.False(stopped.Snapshot?.AdbTunnel.Desired);
        await coordinator.DisposeAsync();
    }

    [Fact]
    public void IdleShutdownRequiresNoClientsAndNeitherTunnelDesired()
    {
        var policy = new TunnelHostIdlePolicy(TimeSpan.FromSeconds(15));
        var start = new DateTimeOffset(2026, 8, 1, 12, 0, 0, TimeSpan.Zero);
        var idle = new TunnelHostSnapshot();

        Assert.False(policy.ShouldExit(idle, start));
        Assert.False(policy.ShouldExit(idle, start + TimeSpan.FromSeconds(14)));
        Assert.True(policy.ShouldExit(idle, start + TimeSpan.FromSeconds(15)));
        var connected = idle with { ConnectedGuiClients = 1 };
        Assert.False(policy.ShouldExit(connected, start + TimeSpan.FromSeconds(30)));
        Assert.False(policy.ShouldExit(idle, start + TimeSpan.FromSeconds(31)));
    }

    [Fact]
    public void JobObjectUsesKillOnCloseAndKillsAssignedChildOnWindows()
    {
        Assert.Equal(0x00002000u, WindowsKillOnCloseJob.KillOnJobCloseLimit);
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var job = WindowsKillOnCloseJob.Create();
        using var child = Process.Start(new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = "/c ping -n 30 127.0.0.1 > nul",
            UseShellExecute = false,
            CreateNoWindow = true,
        }) ?? throw new InvalidOperationException("Unable to start cleanup fixture.");
        job.AssignProcess(child);
        job.Dispose();

        Assert.True(child.WaitForExit(5000));
    }

    private static TunnelHostCoordinator Coordinator(
        ISshTunnelProcessFactory apiFactory,
        ISshTunnelProcessFactory adbFactory,
        ITunnelConfigurationStore store) => new(
            new TunnelSupervisor(
                TunnelKind.Api,
                apiFactory,
                startupSettleDelay: TimeSpan.Zero),
            new TunnelSupervisor(
                TunnelKind.Adb,
                adbFactory,
                startupSettleDelay: TimeSpan.Zero),
            store,
            new FakeServiceController(),
            hostProcessId: 77,
            hostVersion: "test");

    private static TunnelHostRequest Request(
        TunnelHostCommand command,
        TunnelKind? tunnel = null,
        TunnelHostConfiguration? configuration = null,
        string client = "gui-1") => new()
    {
        RequestId = Guid.NewGuid().ToString("N"),
        ClientInstanceId = client,
        Command = command,
        Tunnel = tunnel,
        Configuration = configuration,
    };

    private sealed class MemoryConfigurationStore : ITunnelConfigurationStore
    {
        public TunnelHostConfiguration Configuration { get; set; } = new();
        public TunnelHostConfiguration Load() => Configuration;
        public void Save(TunnelHostConfiguration configuration) =>
            Configuration = configuration;
    }

    private sealed class FakeServiceController : ILinuxApiServiceController
    {
        public Task<LinuxApiServiceSnapshot> QueryAsync(
            TunnelHostConfiguration configuration,
            CancellationToken cancellationToken) =>
            Task.FromResult(new LinuxApiServiceSnapshot
            {
                QuerySucceeded = true,
                ActiveState = "active",
            });

        public Task<LinuxApiServiceSnapshot> ChangeAsync(
            TunnelHostConfiguration configuration,
            LinuxApiServiceAction action,
            CancellationToken cancellationToken) =>
            QueryAsync(configuration, cancellationToken);
    }
}
