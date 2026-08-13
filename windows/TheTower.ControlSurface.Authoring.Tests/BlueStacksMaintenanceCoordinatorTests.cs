using System.Text.Json;
using TheTower.ControlSurface;

namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class BlueStacksMaintenanceCoordinatorTests : IDisposable
{
    private const string RequestId = "0123456789abcdef0123456789abcdef";
    private readonly string _directory;
    private readonly string _playerPath;
    private readonly string _otherPlayerPath;
    private readonly DateTimeOffset _startedAt =
        new DateTimeOffset(2026, 8, 12, 12, 0, 0, TimeSpan.Zero)
            .AddTicks(1_234_567);

    public BlueStacksMaintenanceCoordinatorTests()
    {
        _directory = Path.Combine(
            Path.GetTempPath(),
            "thetower-bluestacks-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_directory);
        _playerPath = Path.Combine(_directory, "HD-Player.exe");
        var otherDirectory = Path.Combine(_directory, "other");
        Directory.CreateDirectory(otherDirectory);
        _otherPlayerPath = Path.Combine(otherDirectory, "HD-Player.exe");
        File.WriteAllText(_playerPath, "test");
        File.WriteAllText(_otherPlayerPath, "test");
    }

    [Fact]
    public async Task RecoveryIsDefaultOff()
    {
        var settings = Settings(enabled: false);
        var api = new FakeHostMaintenanceApi();
        var controller = new FakeBlueStacksController();
        var coordinator = Coordinator(api, controller, settings);

        await coordinator.ObserveStatusAsync(
            Status(degradationReady: true),
            CancellationToken.None);

        Assert.Empty(api.Payloads);
        Assert.Empty(controller.InspectedTargets);
        Assert.False(coordinator.TargetEditsLocked);
    }

    [Fact]
    public async Task RequestBindsImmutableTargetBeforeAcknowledgement()
    {
        var settings = Settings(enabled: true);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var previous = Identity(target, 90, _startedAt);
        var replacement = Identity(target, 91, _startedAt.AddMinutes(1));
        var api = new FakeHostMaintenanceApi();
        api.Enqueue(Status(request: Request("requested")));
        api.Enqueue(AcknowledgedStatus(target, previous));
        api.Enqueue(Status(request: Request("terminal")));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => previous,
            RestartHandler = (_, _) =>
                new BlueStacksRestartResult(previous, replacement),
        };
        var coordinator = Coordinator(api, controller, settings);
        var resetEvents = 0;
        coordinator.RestartBoundaryCrossed += (_, _) => resetEvents++;

        await coordinator.ObserveStatusAsync(
            Status(degradationReady: true),
            CancellationToken.None);
        Assert.Equal(RequestId, coordinator.ActiveRequestId);
        Assert.True(coordinator.TargetEditsLocked);

        settings.BlueStacksPlayerExecutablePath = _otherPlayerPath;
        settings.BlueStacksInstanceName = "Pie64_Changed";
        settings.WindowsBlueStacksAdbPort = 5565;
        await coordinator.ObserveStatusAsync(
            Status(
                request: Request("requested"),
                hostRestartAuthorized: true),
            CancellationToken.None);

        Assert.All(
            controller.InspectedTargets,
            inspected => Assert.Equal(target, inspected));
        Assert.Equal(target, Assert.Single(controller.RestartedTargets));
        Assert.True(controller.InspectSingleInstanceRequirements[0]);
        var acknowledgement = api.Payloads[1];
        Assert.Equal(target.ExecutablePath, acknowledgement.GetProperty(
            "executable_path").GetString());
        Assert.Equal(target.InstanceName, acknowledgement.GetProperty(
            "instance_name").GetString());
        Assert.Equal(target.AdbPort, acknowledgement.GetProperty(
            "adb_port").GetInt32());
        Assert.Equal(1, resetEvents);
    }

    [Fact]
    public async Task DurableAcknowledgementOverridesChangedPreferences()
    {
        var boundSettings = Settings(enabled: true);
        var target = BlueStacksRecoveryTarget.Capture(boundSettings);
        var previous = Identity(target, 90, _startedAt);
        var replacement = Identity(target, 91, _startedAt.AddMinutes(1));
        var changed = Settings(enabled: false);
        changed.BlueStacksPlayerExecutablePath = _otherPlayerPath;
        changed.BlueStacksInstanceName = "Pie64_Changed";
        changed.WindowsBlueStacksAdbPort = 5565;
        var api = new FakeHostMaintenanceApi();
        api.Enqueue(Status(request: Request("terminal")));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => previous,
            RestartHandler = (_, _) =>
                new BlueStacksRestartResult(previous, replacement),
        };
        var coordinator = Coordinator(api, controller, changed);
        var resetEvents = 0;
        coordinator.RestartBoundaryCrossed += (_, _) => resetEvents++;

        await coordinator.ObserveStatusAsync(
            AcknowledgedStatus(target, previous),
            CancellationToken.None);

        Assert.Equal(target, Assert.Single(controller.InspectedTargets));
        Assert.Equal(target, Assert.Single(controller.RestartedTargets));
        Assert.Equal("complete", api.Payloads[0].GetProperty(
            "operation").GetString());
        Assert.Equal(1, resetEvents);
    }

    [Fact]
    public async Task LostAcknowledgementResponseDoesNotStartHostMutation()
    {
        var settings = Settings(enabled: true);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var previous = Identity(target, 90, _startedAt);
        var api = new FakeHostMaintenanceApi();
        api.EnqueueException(new IOException("response lost"));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => previous,
        };
        var coordinator = Coordinator(api, controller, settings);
        var resetEvents = 0;
        coordinator.RestartBoundaryCrossed += (_, _) => resetEvents++;

        await coordinator.ObserveStatusAsync(
            Status(
                request: Request("requested"),
                hostRestartAuthorized: true),
            CancellationToken.None);

        Assert.Empty(controller.RestartedTargets);
        Assert.Equal("acknowledge", api.Payloads[0].GetProperty(
            "operation").GetString());
        Assert.True(coordinator.TargetEditsLocked);
        Assert.Equal(0, resetEvents);
    }

    [Fact]
    public async Task CompletionFailureRetriesReceiptWithoutRestartingAgain()
    {
        var settings = Settings(enabled: false);
        var target = BlueStacksRecoveryTarget.Capture(Settings(enabled: true));
        var previous = Identity(target, 90, _startedAt);
        var replacement = Identity(target, 91, _startedAt.AddMinutes(1));
        var api = new FakeHostMaintenanceApi();
        api.EnqueueException(new IOException("report lost"));
        api.Enqueue(Status(request: Request("terminal")));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => previous,
            RestartHandler = (_, _) =>
                new BlueStacksRestartResult(previous, replacement),
        };
        var coordinator = Coordinator(api, controller, settings);
        var acknowledged = AcknowledgedStatus(target, previous);

        await coordinator.ObserveStatusAsync(acknowledged, CancellationToken.None);
        await coordinator.ObserveStatusAsync(acknowledged, CancellationToken.None);

        Assert.Single(controller.RestartedTargets);
        Assert.Equal(2, api.Payloads.Count);
        Assert.All(
            api.Payloads,
            payload => Assert.Equal(
                "complete",
                payload.GetProperty("operation").GetString()));
    }

    [Fact]
    public async Task PreAckFailureReportsSafeTerminalFailure()
    {
        var settings = Settings(enabled: true);
        var api = new FakeHostMaintenanceApi();
        api.Enqueue(Status(request: Request("terminal")));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => throw new InvalidOperationException(
                "listener is ambiguous"),
        };
        var coordinator = Coordinator(api, controller, settings);

        await coordinator.ObserveStatusAsync(
            Status(
                request: Request("requested"),
                hostRestartAuthorized: true),
            CancellationToken.None);

        Assert.Equal("fail", api.Payloads[0].GetProperty(
            "operation").GetString());
        Assert.Empty(controller.RestartedTargets);
    }

    [Fact]
    public async Task PostAckFailureNeverReportsFailureOrReleasesLinuxHold()
    {
        var settings = Settings(enabled: false);
        var target = BlueStacksRecoveryTarget.Capture(Settings(enabled: true));
        var previous = Identity(target, 90, _startedAt);
        var api = new FakeHostMaintenanceApi();
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => previous,
            RestartException = new IOException("stop outcome unknown"),
        };
        var coordinator = Coordinator(api, controller, settings);
        var resetEvents = 0;
        coordinator.RestartBoundaryCrossed += (_, _) => resetEvents++;

        await coordinator.ObserveStatusAsync(
            AcknowledgedStatus(target, previous),
            CancellationToken.None);

        Assert.Empty(api.Payloads);
        Assert.True(coordinator.TargetEditsLocked);
        Assert.Equal(RequestId, coordinator.ActiveRequestId);
        Assert.Equal(1, resetEvents);
    }

    [Fact]
    public async Task ExistingReplacementIsConfirmedWithoutStoppingAgain()
    {
        var settings = Settings(enabled: false);
        var target = BlueStacksRecoveryTarget.Capture(Settings(enabled: true));
        var previous = Identity(target, 90, _startedAt);
        var replacement = Identity(target, 91, _startedAt.AddMinutes(1));
        var api = new FakeHostMaintenanceApi();
        api.Enqueue(Status(request: Request("terminal")));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => replacement,
            ConfirmHandler = (_, _) =>
                new BlueStacksRestartResult(previous, replacement),
        };
        var coordinator = Coordinator(api, controller, settings);
        var resetEvents = 0;
        coordinator.RestartBoundaryCrossed += (_, _) => resetEvents++;

        await coordinator.ObserveStatusAsync(
            AcknowledgedStatus(target, previous),
            CancellationToken.None);

        Assert.Empty(controller.RestartedTargets);
        Assert.Empty(controller.StartedTargets);
        Assert.Equal(target, Assert.Single(controller.ConfirmedTargets));
        Assert.Equal("complete", api.Payloads[0].GetProperty(
            "operation").GetString());
        Assert.Equal(0, resetEvents);
    }

    [Fact]
    public async Task MissingAcknowledgedListenerStartsOnlyDurableTarget()
    {
        var settings = Settings(enabled: false);
        var target = BlueStacksRecoveryTarget.Capture(Settings(enabled: true));
        var previous = Identity(target, 90, _startedAt);
        var replacement = Identity(target, 91, _startedAt.AddMinutes(1));
        var api = new FakeHostMaintenanceApi();
        api.Enqueue(Status(request: Request("terminal")));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => throw new BlueStacksListenerUnavailableException(
                "listener stopped after acknowledgement"),
            StartHandler = (_, _) =>
                new BlueStacksRestartResult(previous, replacement),
        };
        var coordinator = Coordinator(api, controller, settings);
        var resetEvents = 0;
        coordinator.RestartBoundaryCrossed += (_, _) => resetEvents++;

        await coordinator.ObserveStatusAsync(
            AcknowledgedStatus(target, previous),
            CancellationToken.None);

        Assert.Empty(controller.RestartedTargets);
        Assert.Empty(controller.ConfirmedTargets);
        Assert.Equal(target, Assert.Single(controller.StartedTargets));
        Assert.Equal("complete", api.Payloads[0].GetProperty(
            "operation").GetString());
        Assert.Equal(1, resetEvents);
    }

    [Fact]
    public async Task MissingCompatibilityCapabilityNeverCreatesRequest()
    {
        var settings = Settings(enabled: true);
        var api = new FakeHostMaintenanceApi();
        var controller = new FakeBlueStacksController();
        var coordinator = Coordinator(api, controller, settings);
        var status = Status(degradationReady: true);
        status.Capabilities.Clear();

        await coordinator.ObserveStatusAsync(status, CancellationToken.None);

        Assert.Empty(api.Payloads);
        Assert.Empty(controller.InspectedTargets);
    }

    [Theory]
    [InlineData(2, 41, "")]
    [InlineData(1, 40, "")]
    [InlineData(1, 41, "bluestacks_maintenance_v2")]
    [InlineData(1, 41, "bluestacks_operator_restart_v1")]
    [InlineData(1, 41, "bluestacks_listener_lifetime_telemetry_v1")]
    public void OperatorContractRequiresExactApiRevisionAndCapabilities(
        int apiVersion,
        int serverRevision,
        string missingCapability)
    {
        var status = Status();
        status.ApiVersion = apiVersion;
        status.ServerRevision = serverRevision;
        if (!string.IsNullOrEmpty(missingCapability))
        {
            status.Capabilities.Remove(missingCapability);
        }

        Assert.False(
            BlueStacksMaintenanceCoordinator.HasOperatorRestartContract(status));
    }

    [Fact]
    public void OperatorContractAcceptsCurrentServerContract()
    {
        Assert.True(
            BlueStacksMaintenanceCoordinator.HasOperatorRestartContract(
                Status()));
    }

    [Fact]
    public void ReconciliationContractDoesNotRequireOperatorCapability()
    {
        var status = Status();
        status.Capabilities.Remove("bluestacks_operator_restart_v1");

        Assert.True(
            BlueStacksMaintenanceCoordinator
                .HasMaintenanceReconciliationContract(status));
        Assert.False(
            BlueStacksMaintenanceCoordinator.HasOperatorRestartContract(status));
    }

    [Fact]
    public async Task OperatorRequestWorksWithAutomaticRecoveryDisabled()
    {
        var settings = Settings(enabled: false);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var identity = Identity(target, 90, _startedAt);
        var accepted = Request("requested", target, identity);
        accepted.Initiator = "operator";
        var api = new FakeHostMaintenanceApi();
        api.Enqueue(Status(request: accepted));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => identity,
        };
        var coordinator = Coordinator(api, controller, settings);

        var preview = coordinator.PrepareOperatorRestart();
        await coordinator.RequestOperatorRestartAsync(
            preview,
            CancellationToken.None);

        Assert.Equal(RequestId, coordinator.ActiveRequestId);
        Assert.True(coordinator.TargetEditsLocked);
        Assert.Equal(
            "request_operator",
            api.Payloads[0].GetProperty("operation").GetString());
        Assert.Equal(
            identity.ProcessId,
            api.Payloads[0].GetProperty("process_id").GetInt32());
        Assert.Equal(
            [false, false],
            controller.InspectSingleInstanceRequirements);
    }

    [Fact]
    public async Task LostOperatorRequestResponseKeepsTargetLockedUntilActiveStatus()
    {
        var settings = Settings(enabled: false);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var identity = Identity(target, 90, _startedAt);
        var api = new FakeHostMaintenanceApi();
        api.EnqueueException(new IOException("response lost"));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => identity,
        };
        var coordinator = Coordinator(api, controller, settings);
        var preview = coordinator.PrepareOperatorRestart();

        await Assert.ThrowsAsync<IOException>(() =>
            coordinator.RequestOperatorRestartAsync(
                preview,
                CancellationToken.None));

        Assert.True(coordinator.RequestOutcomeUnknown);
        Assert.True(coordinator.TargetEditsLocked);
        Assert.Throws<InvalidOperationException>(() =>
            coordinator.PrepareOperatorRestart());

        await coordinator.ObserveStatusAsync(
            Status(request: Request("requested", target, identity)),
            CancellationToken.None);

        Assert.False(coordinator.RequestOutcomeUnknown);
        Assert.Equal(RequestId, coordinator.ActiveRequestId);
        Assert.True(coordinator.TargetEditsLocked);
    }

    [Fact]
    public async Task ServerFailureLeavesOperatorRequestOutcomeUnknown()
    {
        var settings = Settings(enabled: false);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var identity = Identity(target, 90, _startedAt);
        var api = new FakeHostMaintenanceApi();
        api.EnqueueException(new ControlSurfaceApiException(
            "service unavailable",
            503,
            "upstream_unavailable",
            null));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => identity,
        };
        var coordinator = Coordinator(api, controller, settings);
        var preview = coordinator.PrepareOperatorRestart();

        await Assert.ThrowsAsync<ControlSurfaceApiException>(() =>
            coordinator.RequestOperatorRestartAsync(
                preview,
                CancellationToken.None));

        Assert.True(coordinator.RequestOutcomeUnknown);
        Assert.True(coordinator.TargetEditsLocked);
        await Assert.ThrowsAsync<InvalidOperationException>(() =>
            coordinator.RequestOperatorRestartAsync(
                preview,
                CancellationToken.None));
        Assert.Single(api.Payloads);
    }

    [Fact]
    public async Task DefiniteOperatorRejectionDoesNotCreateUnknownLock()
    {
        var settings = Settings(enabled: false);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var identity = Identity(target, 90, _startedAt);
        var api = new FakeHostMaintenanceApi();
        api.EnqueueException(new ControlSurfaceApiException(
            "runtime changed",
            409,
            "maintenance_conflict",
            null));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => identity,
        };
        var coordinator = Coordinator(api, controller, settings);

        await Assert.ThrowsAsync<ControlSurfaceApiException>(() =>
            coordinator.RequestOperatorRestartAsync(
                coordinator.PrepareOperatorRestart(),
                CancellationToken.None));

        Assert.False(coordinator.RequestOutcomeUnknown);
        Assert.False(coordinator.TargetEditsLocked);
    }

    [Fact]
    public async Task IdleStatusClearsUnknownAutomaticRequestOutcome()
    {
        var settings = Settings(enabled: true);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var identity = Identity(target, 90, _startedAt);
        var api = new FakeHostMaintenanceApi();
        api.EnqueueException(new IOException("response lost"));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => identity,
        };
        var coordinator = Coordinator(api, controller, settings);

        await coordinator.ObserveStatusAsync(
            Status(degradationReady: true),
            CancellationToken.None);

        Assert.True(coordinator.RequestOutcomeUnknown);
        settings.BlueStacksAutomaticRecoveryEnabled = false;
        await coordinator.ObserveStatusAsync(Status(), CancellationToken.None);

        Assert.False(coordinator.RequestOutcomeUnknown);
        Assert.False(coordinator.TargetEditsLocked);
    }

    [Fact]
    public async Task ServerFailureLeavesAutomaticRequestOutcomeUnknown()
    {
        var settings = Settings(enabled: true);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var identity = Identity(target, 90, _startedAt);
        var api = new FakeHostMaintenanceApi();
        api.EnqueueException(new ControlSurfaceApiException(
            "service unavailable",
            503,
            "upstream_unavailable",
            null));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => identity,
        };
        var coordinator = Coordinator(api, controller, settings);

        await coordinator.ObserveStatusAsync(
            Status(degradationReady: true),
            CancellationToken.None);

        Assert.True(coordinator.RequestOutcomeUnknown);
        Assert.True(coordinator.TargetEditsLocked);
        Assert.Single(api.Payloads);
    }

    [Fact]
    public async Task ReconciliationOnlyIdleStatusNeverCreatesClosingRequest()
    {
        var settings = Settings(enabled: true);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var identity = Identity(target, 90, _startedAt);
        var api = new FakeHostMaintenanceApi();
        api.EnqueueException(new IOException("response lost"));
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => identity,
        };
        var coordinator = Coordinator(api, controller, settings);

        await coordinator.ObserveStatusAsync(
            Status(degradationReady: true),
            CancellationToken.None);
        Assert.Single(api.Payloads);

        await coordinator.ObserveStatusAsync(
            Status(degradationReady: true),
            CancellationToken.None,
            allowRequestCreation: false);

        Assert.Single(api.Payloads);
        Assert.False(coordinator.RequestOutcomeUnknown);
        Assert.False(coordinator.TargetEditsLocked);
    }

    [Fact]
    public void TelemetryTargetUsesActiveDurableRequestAndFailsClosed()
    {
        var settings = Settings(enabled: false);
        settings.BlueStacksPlayerExecutablePath = _otherPlayerPath;
        settings.BlueStacksInstanceName = "Pie64_Changed";
        settings.WindowsBlueStacksAdbPort = 5565;
        var durableSettings = Settings(enabled: true);
        var target = BlueStacksRecoveryTarget.Capture(durableSettings);
        var identity = Identity(target, 90, _startedAt);
        var active = Status(request: Request("requested", target, identity));

        Assert.Equal(
            target,
            BlueStacksMaintenanceCoordinator.ResolveTelemetryTarget(
                active,
                settings));

        active.HostMaintenance.Request!.HostTarget!.ExecutablePath =
            "relative.exe";
        Assert.Null(
            BlueStacksMaintenanceCoordinator.ResolveTelemetryTarget(
                active,
                settings));

        active.HostMaintenance.Request.State = "terminal";
        Assert.Equal(
            BlueStacksRecoveryTarget.Capture(settings),
            BlueStacksMaintenanceCoordinator.ResolveTelemetryTarget(
                active,
                settings));
    }

    [Fact]
    public void TunnelPortAdoptionRequiresReconciledIdleStatus()
    {
        var idle = Status();
        Assert.True(
            BlueStacksMaintenanceCoordinator.CanAdoptTunnelHostPort(
                idle,
                targetEditsLocked: false));
        Assert.False(
            BlueStacksMaintenanceCoordinator.CanAdoptTunnelHostPort(
                idle,
                targetEditsLocked: true));
        Assert.False(
            BlueStacksMaintenanceCoordinator.CanAdoptTunnelHostPort(
                Status(request: Request("requested")),
                targetEditsLocked: false));
    }

    [Fact]
    public async Task OperatorConfirmationCannotRetargetChangedProcess()
    {
        var settings = Settings(enabled: false);
        var target = BlueStacksRecoveryTarget.Capture(settings);
        var previous = Identity(target, 90, _startedAt);
        var replacement = Identity(target, 91, _startedAt.AddMinutes(1));
        var inspection = 0;
        var api = new FakeHostMaintenanceApi();
        var controller = new FakeBlueStacksController
        {
            InspectHandler = _ => inspection++ == 0 ? previous : replacement,
        };
        var coordinator = Coordinator(api, controller, settings);

        var preview = coordinator.PrepareOperatorRestart();
        var rejected = await Assert.ThrowsAsync<InvalidOperationException>(() =>
            coordinator.RequestOperatorRestartAsync(
                preview,
                CancellationToken.None));

        Assert.Contains("changed after confirmation", rejected.Message);
        Assert.Empty(api.Payloads);
        Assert.False(coordinator.TargetEditsLocked);
    }

    private BlueStacksMaintenanceCoordinator Coordinator(
        FakeHostMaintenanceApi api,
        FakeBlueStacksController controller,
        ClientSettings settings) =>
        new(api, controller, () => settings);

    private ClientSettings Settings(bool enabled) =>
        new()
        {
            BlueStacksAutomaticRecoveryEnabled = enabled,
            BlueStacksPlayerExecutablePath = _playerPath,
            BlueStacksInstanceName = "Nougat32",
            WindowsBlueStacksAdbPort = 5555,
        };

    private BlueStacksProcessIdentity Identity(
        BlueStacksRecoveryTarget target,
        int processId,
        DateTimeOffset startedAt) =>
        new(
            Environment.MachineName,
            target.AdbPort,
            processId,
            startedAt,
            target.ExecutablePath);

    private HostMaintenanceRequest Request(
        string state,
        BlueStacksRecoveryTarget? target = null,
        BlueStacksProcessIdentity? identity = null)
    {
        target ??= BlueStacksRecoveryTarget.Capture(Settings(enabled: true));
        identity ??= Identity(target, 90, _startedAt);
        return new()
        {
            RequestId = RequestId,
            State = state,
            Reason = state == "terminal" ? "finished" : "degraded",
            HostTarget = HostIdentity(target, identity),
        };
    }

    private StatusResponse Status(
        HostMaintenanceRequest? request = null,
        bool hostRestartAuthorized = false,
        bool degradationReady = false) =>
        new()
        {
            ApiVersion = ControlSurfaceCompatibility.RequiredApiVersion,
            ServerRevision = ControlSurfaceCompatibility.MinimumServerRevision,
            Capabilities =
            [
                "bluestacks_maintenance_v2",
                "bluestacks_operator_restart_v1",
                "bluestacks_listener_lifetime_telemetry_v1",
            ],
            HostMaintenance = new HostMaintenanceStatus
            {
                Request = request,
                Active = request is not null && request.State != "terminal",
                HostRestartAuthorized = hostRestartAuthorized,
                Reason = hostRestartAuthorized
                    ? "runtime authorized"
                    : "waiting",
            },
            EmulatorDegradation = new EmulatorDegradationStatus
            {
                AssessedAt = "2026-08-12T12:00:00+00:00",
                Status = degradationReady ? "automatic_ready" : "healthy",
                AutomaticReady = degradationReady,
                HostEvidence = new EmulatorHostEvidenceStatus
                {
                    Status = degradationReady ? "confirmed_growth" : "stable",
                    IdentityScope = "exact_listener_lifetime",
                    ListenerIdentity = ListenerIdentity(
                        BlueStacksRecoveryTarget.Capture(Settings(enabled: true)),
                        Identity(
                            BlueStacksRecoveryTarget.Capture(Settings(enabled: true)),
                            90,
                            _startedAt)),
                },
            },
        };

    private StatusResponse AcknowledgedStatus(
        BlueStacksRecoveryTarget target,
        BlueStacksProcessIdentity previous)
    {
        var request = Request("host_acknowledged");
        request.HostTarget = HostIdentity(target, previous);
        request.HostAcknowledgement = HostIdentity(target, previous);
        return Status(request: request);
    }

    private static BlueStacksHostProcessIdentity HostIdentity(
        BlueStacksRecoveryTarget target,
        BlueStacksProcessIdentity identity) =>
        new()
        {
            HostId = identity.HostId,
            AdbPort = target.AdbPort,
            ProcessId = identity.ProcessId,
            ProcessStartedAt = identity.ProcessStartedAtText,
            ExecutablePath = target.ExecutablePath,
            InstanceName = target.InstanceName,
        };

    private static HostPerformanceBlueStacksListener ListenerIdentity(
        BlueStacksRecoveryTarget target,
        BlueStacksProcessIdentity identity) =>
        new()
        {
            HostId = identity.HostId,
            AdbPort = target.AdbPort,
            ProcessId = identity.ProcessId,
            ProcessStartedAt = identity.ProcessStartedAtText,
            ExecutablePath = target.ExecutablePath,
            InstanceName = target.InstanceName,
        };

    public void Dispose()
    {
        Directory.Delete(_directory, recursive: true);
    }

    private sealed class FakeHostMaintenanceApi : IHostMaintenanceApi
    {
        private readonly Queue<Func<StatusResponse>> _responses = [];
        public List<JsonElement> Payloads { get; } = [];

        public void Enqueue(StatusResponse response) =>
            _responses.Enqueue(() => response);

        public void EnqueueException(Exception exception) =>
            _responses.Enqueue(() => throw exception);

        public Task<StatusResponse> PostHostMaintenanceAsync(
            object payload,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            Payloads.Add(JsonSerializer.SerializeToElement(payload));
            if (_responses.Count == 0)
            {
                throw new InvalidOperationException("No fake API response queued.");
            }
            return Task.FromResult(_responses.Dequeue()());
        }
    }

    private sealed class FakeBlueStacksController : IBlueStacksInstanceController
    {
        public Func<BlueStacksRecoveryTarget, BlueStacksProcessIdentity>?
            InspectHandler { get; init; }
        public Func<BlueStacksProcessIdentity, BlueStacksRecoveryTarget,
            BlueStacksRestartResult>? RestartHandler { get; init; }
        public Func<BlueStacksProcessIdentity, BlueStacksRecoveryTarget,
            BlueStacksRestartResult>? StartHandler { get; init; }
        public Func<BlueStacksProcessIdentity, BlueStacksRecoveryTarget,
            BlueStacksRestartResult>? ConfirmHandler { get; init; }
        public Exception? RestartException { get; init; }
        public List<BlueStacksRecoveryTarget> InspectedTargets { get; } = [];
        public List<bool> InspectSingleInstanceRequirements { get; } = [];
        public List<BlueStacksRecoveryTarget> RestartedTargets { get; } = [];
        public List<BlueStacksRecoveryTarget> StartedTargets { get; } = [];
        public List<BlueStacksRecoveryTarget> ConfirmedTargets { get; } = [];

        public BlueStacksProcessIdentity Inspect(
            BlueStacksRecoveryTarget target,
            bool requireSingleActiveInstance = true)
        {
            InspectedTargets.Add(target);
            InspectSingleInstanceRequirements.Add(requireSingleActiveInstance);
            return InspectHandler?.Invoke(target)
                ?? throw new InvalidOperationException("Inspect was not expected.");
        }

        public Task<BlueStacksRestartResult> RestartAcknowledgedAsync(
            BlueStacksProcessIdentity previous,
            BlueStacksRecoveryTarget target,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            RestartedTargets.Add(target);
            if (RestartException is not null)
            {
                throw RestartException;
            }
            return Task.FromResult(
                RestartHandler?.Invoke(previous, target)
                ?? throw new InvalidOperationException("Restart was not expected."));
        }

        public Task<BlueStacksRestartResult> StartAfterAcknowledgedStopAsync(
            BlueStacksProcessIdentity previous,
            BlueStacksRecoveryTarget target,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            StartedTargets.Add(target);
            return Task.FromResult(
                StartHandler?.Invoke(previous, target)
                ?? RestartHandler?.Invoke(previous, target)
                ?? throw new InvalidOperationException("Start was not expected."));
        }

        public Task<BlueStacksRestartResult> ConfirmReplacementAsync(
            BlueStacksProcessIdentity previous,
            BlueStacksRecoveryTarget target,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            ConfirmedTargets.Add(target);
            return Task.FromResult(
                ConfirmHandler?.Invoke(previous, target)
                ?? RestartHandler?.Invoke(previous, target)
                ?? throw new InvalidOperationException(
                    "Replacement confirmation was not expected."));
        }
    }
}
