namespace TheTower.ControlSurface;

internal sealed record BlueStacksPendingCompletion(
    BlueStacksRestartResult Result,
    BlueStacksRecoveryTarget Target);

internal sealed record BlueStacksOperatorRestartPreview(
    BlueStacksRecoveryTarget Target,
    BlueStacksProcessIdentity Identity);

internal sealed class BlueStacksMaintenanceCoordinator
{
    private readonly IHostMaintenanceApi _api;
    private readonly IBlueStacksInstanceController _controller;
    private readonly Func<ClientSettings> _settings;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly object _stateGate = new();
    private readonly Dictionary<string, BlueStacksRecoveryTarget> _requestTargets = [];
    private readonly Dictionary<string, BlueStacksPendingCompletion> _pendingReports = [];
    private string? _lastRequestedAssessmentAt;
    private string? _activeRequestId;
    private bool _operationActive;
    private bool _requestOutcomeUnknown;

    public BlueStacksMaintenanceCoordinator(
        IHostMaintenanceApi api,
        IBlueStacksInstanceController controller,
        Func<ClientSettings> settings)
    {
        _api = api;
        _controller = controller;
        _settings = settings;
    }

    public event EventHandler<string>? StateChanged;
    public event EventHandler? RestartBoundaryCrossed;

    public bool TargetEditsLocked
    {
        get
        {
            lock (_stateGate)
            {
                return _operationActive
                    || _requestOutcomeUnknown
                    || !string.IsNullOrWhiteSpace(_activeRequestId);
            }
        }
    }

    internal string? ActiveRequestId
    {
        get
        {
            lock (_stateGate)
            {
                return _activeRequestId;
            }
        }
    }

    internal bool RequestOutcomeUnknown
    {
        get
        {
            lock (_stateGate)
            {
                return _requestOutcomeUnknown;
            }
        }
    }

    public BlueStacksOperatorRestartPreview PrepareOperatorRestart()
    {
        if (TargetEditsLocked)
        {
            throw new InvalidOperationException(
                "BlueStacks maintenance is already active.");
        }
        var target = BlueStacksRecoveryTarget.Capture(_settings());
        var identity = _controller.Inspect(
            target,
            requireSingleActiveInstance: false);
        return new BlueStacksOperatorRestartPreview(target, identity);
    }

    internal static BlueStacksRecoveryTarget? ResolveTelemetryTarget(
        StatusResponse status,
        ClientSettings settings)
    {
        var request = status.HostMaintenance.Request;
        try
        {
            if (request is not null && request.State != "terminal")
            {
                var durable = request.HostCompletion
                    ?? request.HostAcknowledgement
                    ?? request.HostTarget;
                return durable is null
                    ? null
                    : BlueStacksRecoveryTarget.FromAcknowledgement(durable);
            }
            return BlueStacksRecoveryTarget.Capture(settings);
        }
        catch (ArgumentException)
        {
            return null;
        }
    }

    internal static bool CanAdoptTunnelHostPort(
        StatusResponse status,
        bool targetEditsLocked) =>
        !targetEditsLocked
        && status.HostMaintenance.Request is not
            { State: not "terminal" };

    public async Task RequestOperatorRestartAsync(
        BlueStacksOperatorRestartPreview preview,
        CancellationToken cancellationToken)
    {
        if (!await _gate.WaitAsync(0, cancellationToken))
        {
            throw new InvalidOperationException(
                "BlueStacks maintenance is already active.");
        }
        SetOperationActive(true);
        try
        {
            if (RequestOutcomeUnknown
                || !string.IsNullOrWhiteSpace(ActiveRequestId))
            {
                throw new InvalidOperationException(
                    "BlueStacks maintenance is already active.");
            }
            var current = _controller.Inspect(
                preview.Target,
                requireSingleActiveInstance: false);
            if (!ExactHostIdentity(current, preview.Identity, preview.Target))
            {
                throw new InvalidOperationException(
                    "The exact BlueStacks listener changed after confirmation; "
                        + "no restart was requested.");
            }
            StateChanged?.Invoke(
                this,
                $"Requesting operator restart · {preview.Target.InstanceName} "
                    + $"on {preview.Target.AdbPort} · PID {current.ProcessId}");
            StatusResponse response;
            try
            {
                response = await _api.PostHostMaintenanceAsync(
                    new
                    {
                        operation = "request_operator",
                        host_id = current.HostId,
                        adb_port = preview.Target.AdbPort,
                        process_id = current.ProcessId,
                        process_started_at = current.ProcessStartedAtText,
                        executable_path = preview.Target.ExecutablePath,
                        instance_name = preview.Target.InstanceName,
                    },
                    cancellationToken);
            }
            catch (Exception exception)
                when (!IsDefinitePrecommitRejection(exception))
            {
                SetRequestOutcomeUnknown(true);
                StateChanged?.Invoke(
                    this,
                    "Operator restart request result is unknown; target edits "
                        + "stay locked until Linux status reconciles");
                throw;
            }
            var request = response.HostMaintenance.Request;
            try
            {
                if (request is null
                    || string.IsNullOrWhiteSpace(request.RequestId))
                {
                    throw new InvalidOperationException(
                        "Linux accepted no durable BlueStacks maintenance request.");
                }
                RequireRequestedTarget(request, current, preview.Target);
            }
            catch
            {
                SetRequestOutcomeUnknown(true);
                throw;
            }
            _requestTargets[request.RequestId] = preview.Target;
            SetRequestOutcomeUnknown(false);
            SetActiveRequest(request.RequestId);
            StateChanged?.Invoke(
                this,
                $"Operator restart requested · {request.RequestId} · "
                    + "waiting for runtime quiescence");
        }
        finally
        {
            SetOperationActive(false);
            _gate.Release();
        }
    }

    public async Task ObserveStatusAsync(
        StatusResponse status,
        CancellationToken cancellationToken,
        bool allowRequestCreation = true)
    {
        if (!HasMaintenanceReconciliationContract(status))
        {
            StateChanged?.Invoke(
                this,
                "BlueStacks recovery unavailable · Linux revision "
                    + $"{ControlSurfaceCompatibility.MinimumServerRevision} with "
                    + "the exact-listener BlueStacks maintenance capabilities "
                    + "are required");
            return;
        }

        var maintenance = status.HostMaintenance;
        var request = maintenance.Request;
        if (request is null)
        {
            SetRequestOutcomeUnknown(false);
            SetActiveRequest(null);
            if (allowRequestCreation)
            {
                await RequestIfReadyAsync(
                    status.EmulatorDegradation,
                    _settings(),
                    cancellationToken);
            }
            return;
        }
        if (string.IsNullOrWhiteSpace(request.RequestId))
        {
            return;
        }
        if (request.State == "terminal")
        {
            _pendingReports.Remove(request.RequestId);
            _requestTargets.Remove(request.RequestId);
            SetRequestOutcomeUnknown(false);
            SetActiveRequest(null);
            var terminalDisposition = string.IsNullOrWhiteSpace(
                request.TerminalDisposition)
                    ? "terminal"
                    : request.TerminalDisposition;
            var terminalReason = request.TerminalReason
                ?? maintenance.Reason;
            StateChanged?.Invoke(
                this,
                $"BlueStacks recovery {terminalDisposition} · {terminalReason}");
            if (allowRequestCreation)
            {
                await RequestIfReadyAsync(
                    status.EmulatorDegradation,
                    _settings(),
                    cancellationToken);
            }
            return;
        }

        // The preference gates request creation only.  A durable request must
        // reconcile after restart or preference changes until Linux records a
        // terminal source-restoration outcome.
        SetRequestOutcomeUnknown(false);
        SetActiveRequest(request.RequestId);
        if (!await _gate.WaitAsync(0, cancellationToken))
        {
            return;
        }
        SetOperationActive(true);
        try
        {
            await AdvanceAsync(maintenance, request, cancellationToken);
        }
        finally
        {
            SetOperationActive(false);
            _gate.Release();
        }
    }

    private async Task RequestIfReadyAsync(
        EmulatorDegradationStatus degradation,
        ClientSettings settings,
        CancellationToken cancellationToken)
    {
        if (!settings.BlueStacksAutomaticRecoveryEnabled
            || !degradation.AutomaticReady
            || string.IsNullOrWhiteSpace(degradation.AssessedAt)
            || string.Equals(
                _lastRequestedAssessmentAt,
                degradation.AssessedAt,
                StringComparison.Ordinal))
        {
            return;
        }
        if (!await _gate.WaitAsync(0, cancellationToken))
        {
            return;
        }
        SetOperationActive(true);
        try
        {
            var target = BlueStacksRecoveryTarget.Capture(settings);
            var identity = _controller.Inspect(target);
            RequireDetectorIdentity(degradation, identity, target);
            StateChanged?.Invoke(
                this,
                $"BlueStacks degradation confirmed · target "
                    + $"{target.InstanceName} on {target.AdbPort}, PID "
                    + $"{identity.ProcessId}");
            StatusResponse response;
            try
            {
                response = await _api.PostHostMaintenanceAsync(
                    new
                    {
                        operation = "request",
                        host_id = identity.HostId,
                        adb_port = target.AdbPort,
                        process_id = identity.ProcessId,
                        process_started_at = identity.ProcessStartedAtText,
                        executable_path = target.ExecutablePath,
                        instance_name = target.InstanceName,
                    },
                    cancellationToken);
            }
            catch (Exception exception)
                when (!IsDefinitePrecommitRejection(exception))
            {
                SetRequestOutcomeUnknown(true);
                throw;
            }
            var request = response.HostMaintenance.Request;
            try
            {
                if (request is null
                    || string.IsNullOrWhiteSpace(request.RequestId))
                {
                    throw new InvalidOperationException(
                        "Linux accepted no durable BlueStacks maintenance request.");
                }
                RequireRequestedTarget(request, identity, target);
            }
            catch
            {
                SetRequestOutcomeUnknown(true);
                throw;
            }
            _requestTargets[request.RequestId] = target;
            SetRequestOutcomeUnknown(false);
            SetActiveRequest(request.RequestId);
            _lastRequestedAssessmentAt = degradation.AssessedAt;
            StateChanged?.Invoke(
                this,
                $"BlueStacks recovery requested · {request.RequestId} · "
                    + "waiting for runtime quiescence");
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception exception)
        {
            StateChanged?.Invoke(
                this,
                $"BlueStacks recovery request deferred · {exception.Message}");
        }
        finally
        {
            SetOperationActive(false);
            _gate.Release();
        }
    }

    private async Task AdvanceAsync(
        HostMaintenanceStatus maintenance,
        HostMaintenanceRequest request,
        CancellationToken cancellationToken)
    {
        if (_pendingReports.TryGetValue(request.RequestId, out var pending))
        {
            await ReportCompletionOrRetainAsync(
                request.RequestId,
                pending,
                CancellationToken.None);
            return;
        }

        BlueStacksRecoveryTarget? target = null;
        BlueStacksProcessIdentity? previous = null;
        var hostAcknowledged = request.State == "host_acknowledged";
        try
        {
            if (request.State == "requested")
            {
                if (!maintenance.HostRestartAuthorized)
                {
                    StateChanged?.Invoke(
                        this,
                        $"BlueStacks recovery {request.RequestId} · "
                            + $"waiting for runtime hold · {maintenance.Reason}");
                    return;
                }
                if (request.HostTarget is not { } requestedTarget)
                {
                    throw new InvalidOperationException(
                        "The durable request has no exact BlueStacks target; "
                            + "host mutation is not authorized.");
                }
                (target, var expected) = StoredTarget(requestedTarget);
                _requestTargets[request.RequestId] = target;
                previous = _controller.Inspect(
                    target,
                    requireSingleActiveInstance: false);
                if (!ExactHostIdentity(previous, expected, target))
                {
                    throw new InvalidOperationException(
                        "The exact BlueStacks listener changed before host "
                            + "acknowledgement; no process was stopped.");
                }
                StateChanged?.Invoke(
                    this,
                    $"BlueStacks quiesced · {target.InstanceName} · PID "
                        + $"{previous.ProcessId} · acknowledging exact target");
                StatusResponse acknowledgement;
                try
                {
                    acknowledgement = await _api.PostHostMaintenanceAsync(
                        new
                        {
                            operation = "acknowledge",
                            request_id = request.RequestId,
                            host_id = previous.HostId,
                            adb_port = target.AdbPort,
                            process_id = previous.ProcessId,
                            process_started_at = previous.ProcessStartedAtText,
                            executable_path = target.ExecutablePath,
                            instance_name = target.InstanceName,
                        },
                        cancellationToken);
                }
                catch (Exception exception)
                {
                    // A response can be lost after Linux commits the durable
                    // acknowledgement.  Never infer mutation authority from
                    // the failed response; the next status poll reconciles it.
                    StateChanged?.Invoke(
                        this,
                        "BlueStacks acknowledgement result pending · "
                            + exception.Message);
                    return;
                }
                RequireAcknowledgedResponse(
                    acknowledgement,
                    request.RequestId,
                    previous,
                    target);
                hostAcknowledged = true;
                RestartBoundaryCrossed?.Invoke(this, EventArgs.Empty);
                StateChanged?.Invoke(
                    this,
                    $"BlueStacks acknowledged · {target.InstanceName} · exact "
                        + $"PID {previous.ProcessId} · restarting");
                var restarted = await _controller.RestartAcknowledgedAsync(
                    previous,
                    target,
                    CancellationToken.None);
                await ReportCompletionOrRetainAsync(
                    request.RequestId,
                    new BlueStacksPendingCompletion(restarted, target),
                    CancellationToken.None);
                return;
            }

            if (request.State != "host_acknowledged"
                || request.HostAcknowledgement is not { } acknowledged)
            {
                return;
            }
            (target, previous) = StoredTarget(acknowledged);
            _requestTargets[request.RequestId] = target;
            StateChanged?.Invoke(
                this,
                $"BlueStacks reconciliation · {target.InstanceName} · "
                    + $"acknowledged PID {previous.ProcessId}");
            BlueStacksRestartResult result;
            try
            {
                var current = _controller.Inspect(
                    target,
                    requireSingleActiveInstance: false);
                if (ExactIdentity(current, previous))
                {
                    RestartBoundaryCrossed?.Invoke(this, EventArgs.Empty);
                    result = await _controller.RestartAcknowledgedAsync(
                        previous,
                        target,
                        CancellationToken.None);
                }
                else
                {
                    result = await _controller.ConfirmReplacementAsync(
                        previous,
                        target,
                        CancellationToken.None);
                }
            }
            catch (BlueStacksListenerUnavailableException)
            {
                RestartBoundaryCrossed?.Invoke(this, EventArgs.Empty);
                result = await _controller.StartAfterAcknowledgedStopAsync(
                    previous,
                    target,
                    CancellationToken.None);
            }
            await ReportCompletionOrRetainAsync(
                request.RequestId,
                new BlueStacksPendingCompletion(result, target),
                CancellationToken.None);
        }
        catch (OperationCanceledException) when (!hostAcknowledged)
        {
            throw;
        }
        catch (Exception exception)
        {
            StateChanged?.Invoke(
                this,
                $"BlueStacks recovery interrupted · {exception.Message}");
            if (!hostAcknowledged)
            {
                await ReportFailureBestEffortAsync(
                    request.RequestId,
                    exception.Message,
                    cancellationToken);
                return;
            }
            var oldProcessIntact = previous is not null
                && target is not null
                && ExactOldProcessStillOwnsListener(previous, target);
            StateChanged?.Invoke(
                this,
                oldProcessIntact
                    ? "Acknowledged old BlueStacks process remains intact; "
                        + "Linux stays held and the exact restart will retry"
                    : "BlueStacks state is unresolved after acknowledgement; "
                        + "Linux stays held and reconciliation will retry");
        }
    }

    private static void RequireAcknowledgedResponse(
        StatusResponse response,
        string requestId,
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target)
    {
        var acknowledged = response.HostMaintenance.Request;
        var identity = acknowledged?.HostAcknowledgement;
        if (acknowledged?.RequestId != requestId
            || acknowledged.State != "host_acknowledged"
            || identity is null
            || !string.Equals(
                identity.HostId,
                previous.HostId,
                StringComparison.OrdinalIgnoreCase)
            || identity.AdbPort != target.AdbPort
            || identity.ProcessId != previous.ProcessId
            || !DateTimeOffset.TryParse(
                identity.ProcessStartedAt,
                out var acknowledgedStartedAt)
            || acknowledgedStartedAt.ToUniversalTime()
                != previous.ProcessStartedAtUtc
            || !string.Equals(
                identity.ExecutablePath,
                target.ExecutablePath,
                StringComparison.OrdinalIgnoreCase)
            || !string.Equals(
                identity.InstanceName,
                target.InstanceName,
                StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                "Linux did not return the exact durable BlueStacks "
                    + "acknowledgement; host mutation was not started.");
        }
    }

    private static void RequireRequestedTarget(
        HostMaintenanceRequest request,
        BlueStacksProcessIdentity identity,
        BlueStacksRecoveryTarget target)
    {
        if (request.HostTarget is not { } stored)
        {
            throw new InvalidOperationException(
                "Linux did not durably bind the exact BlueStacks target.");
        }
        var (storedTarget, storedIdentity) = StoredTarget(stored);
        if (!Equals(storedTarget, target)
            || !ExactHostIdentity(identity, storedIdentity, target))
        {
            throw new InvalidOperationException(
                "Linux returned a different durable BlueStacks target; host "
                    + "mutation was not started.");
        }
    }

    private static void RequireDetectorIdentity(
        EmulatorDegradationStatus degradation,
        BlueStacksProcessIdentity identity,
        BlueStacksRecoveryTarget target)
    {
        var evidence = degradation.HostEvidence;
        var listener = evidence?.ListenerIdentity;
        if (!string.Equals(
                evidence?.IdentityScope,
                "exact_listener_lifetime",
                StringComparison.Ordinal)
            || listener is null
            || !DateTimeOffset.TryParse(
                listener.ProcessStartedAt,
                out var processStartedAt))
        {
            throw new InvalidOperationException(
                "Automatic recovery has no exact BlueStacks listener-lifetime "
                    + "evidence.");
        }
        var expectedTarget = BlueStacksRecoveryTarget.Create(
            listener.ExecutablePath,
            listener.InstanceName,
            listener.AdbPort);
        var expected = new BlueStacksProcessIdentity(
            listener.HostId,
            listener.AdbPort,
            listener.ProcessId,
            processStartedAt.ToUniversalTime(),
            listener.ExecutablePath);
        if (!Equals(expectedTarget, target)
            || !ExactHostIdentity(identity, expected, target))
        {
            throw new InvalidOperationException(
                "The live BlueStacks listener does not match the exact process "
                    + "lifetime that authorized automatic recovery.");
        }
    }

    private static (BlueStacksRecoveryTarget Target, BlueStacksProcessIdentity Previous)
        StoredTarget(BlueStacksHostProcessIdentity acknowledged)
    {
        if (!string.Equals(
                acknowledged.HostId,
                Environment.MachineName,
                StringComparison.OrdinalIgnoreCase)
            || !DateTimeOffset.TryParse(
                acknowledged.ProcessStartedAt,
                out var processStartedAt))
        {
            throw new InvalidOperationException(
                "The durable BlueStacks process identity does not match this "
                    + "Windows host.");
        }
        var target = BlueStacksRecoveryTarget.FromAcknowledgement(acknowledged);
        return (
            target,
            new BlueStacksProcessIdentity(
                acknowledged.HostId,
                target.AdbPort,
                acknowledged.ProcessId,
                processStartedAt.ToUniversalTime(),
                target.ExecutablePath));
    }

    private bool ExactOldProcessStillOwnsListener(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target)
    {
        try
        {
            return ExactIdentity(
                _controller.Inspect(
                    target,
                    requireSingleActiveInstance: false),
                previous);
        }
        catch
        {
            return false;
        }
    }

    private static bool ExactIdentity(
        BlueStacksProcessIdentity left,
        BlueStacksProcessIdentity right) =>
        left.ProcessId == right.ProcessId
        && left.ProcessStartedAtUtc == right.ProcessStartedAtUtc;

    private static bool ExactHostIdentity(
        BlueStacksProcessIdentity left,
        BlueStacksProcessIdentity right,
        BlueStacksRecoveryTarget target) =>
        ExactIdentity(left, right)
        && left.AdbPort == target.AdbPort
        && right.AdbPort == target.AdbPort
        && string.Equals(
            left.HostId,
            right.HostId,
            StringComparison.OrdinalIgnoreCase)
        && string.Equals(
            left.ExecutablePath,
            target.ExecutablePath,
            StringComparison.OrdinalIgnoreCase)
        && string.Equals(
            right.ExecutablePath,
            target.ExecutablePath,
            StringComparison.OrdinalIgnoreCase);

    internal static bool HasCapability(StatusResponse status, string value) =>
        status.Capabilities.Contains(value, StringComparer.Ordinal);

    internal static bool HasOperatorRestartContract(StatusResponse status) =>
        HasMaintenanceReconciliationContract(status)
        && HasCapability(status, "bluestacks_operator_restart_v1");

    internal static bool HasMaintenanceReconciliationContract(
        StatusResponse status) =>
        status.ApiVersion == ControlSurfaceCompatibility.RequiredApiVersion
        && status.ServerRevision
            >= ControlSurfaceCompatibility.MinimumServerRevision
        && HasCapability(status, "bluestacks_maintenance_v2")
        && HasCapability(
            status,
            "bluestacks_listener_lifetime_telemetry_v1");

    private async Task ReportCompletionOrRetainAsync(
        string requestId,
        BlueStacksPendingCompletion pending,
        CancellationToken cancellationToken)
    {
        _pendingReports[requestId] = pending;
        try
        {
            await ReportCompletionAsync(requestId, pending, cancellationToken);
        }
        catch (Exception exception)
        {
            StateChanged?.Invoke(
                this,
                "Replacement listener is ready; Linux completion report is "
                    + $"pending · {exception.Message}");
            return;
        }
        _pendingReports.Remove(requestId);
        StateChanged?.Invoke(
            this,
            $"BlueStacks replacement ready · {pending.Target.InstanceName} · "
                + $"PID {pending.Result.Replacement.ProcessId}");
    }

    private async Task ReportFailureBestEffortAsync(
        string requestId,
        string reason,
        CancellationToken cancellationToken)
    {
        try
        {
            await _api.PostHostMaintenanceAsync(
                new
                {
                    operation = "fail",
                    request_id = requestId,
                    reason,
                },
                cancellationToken);
            StateChanged?.Invoke(this, $"BlueStacks recovery failed safely · {reason}");
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception reportingException)
        {
            StateChanged?.Invoke(
                this,
                "BlueStacks pre-ack failure report is pending · "
                    + reportingException.Message);
        }
    }

    private Task<StatusResponse> ReportCompletionAsync(
        string requestId,
        BlueStacksPendingCompletion pending,
        CancellationToken cancellationToken) =>
        _api.PostHostMaintenanceAsync(
            new
            {
                operation = "complete",
                request_id = requestId,
                host_id = pending.Result.Replacement.HostId,
                adb_port = pending.Target.AdbPort,
                process_id = pending.Result.Replacement.ProcessId,
                process_started_at =
                    pending.Result.Replacement.ProcessStartedAtText,
                executable_path = pending.Target.ExecutablePath,
                instance_name = pending.Target.InstanceName,
                previous_process_id = pending.Result.Previous.ProcessId,
                previous_process_started_at =
                    pending.Result.Previous.ProcessStartedAtText,
            },
            cancellationToken);

    private void SetActiveRequest(string? requestId)
    {
        lock (_stateGate)
        {
            _activeRequestId = requestId;
        }
    }

    private void SetOperationActive(bool active)
    {
        lock (_stateGate)
        {
            _operationActive = active;
        }
    }

    private void SetRequestOutcomeUnknown(bool value)
    {
        lock (_stateGate)
        {
            _requestOutcomeUnknown = value;
        }
    }

    private static bool IsDefinitePrecommitRejection(Exception exception) =>
        exception is ControlSurfaceApiException
        {
            StatusCode: >= 400 and < 500,
        };
}
