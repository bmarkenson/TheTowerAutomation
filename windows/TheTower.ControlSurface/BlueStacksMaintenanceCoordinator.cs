namespace TheTower.ControlSurface;

internal sealed record BlueStacksPendingCompletion(
    BlueStacksRestartResult Result,
    BlueStacksRecoveryTarget Target);

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
                return _operationActive || !string.IsNullOrWhiteSpace(_activeRequestId);
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

    public async Task ObserveStatusAsync(
        StatusResponse status,
        CancellationToken cancellationToken)
    {
        if (status.ServerRevision < ControlSurfaceCompatibility.MinimumServerRevision
            || !status.Capabilities.Contains(
                "bluestacks_maintenance_v1",
                StringComparer.Ordinal))
        {
            StateChanged?.Invoke(
                this,
                "BlueStacks recovery unavailable · Linux revision "
                    + $"{ControlSurfaceCompatibility.MinimumServerRevision} with "
                    + "bluestacks_maintenance_v1 is required");
            return;
        }

        var maintenance = status.HostMaintenance;
        var request = maintenance.Request;
        if (request is null)
        {
            SetActiveRequest(null);
            await RequestIfReadyAsync(
                status.EmulatorDegradation,
                _settings(),
                cancellationToken);
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
            SetActiveRequest(null);
            StateChanged?.Invoke(
                this,
                $"BlueStacks recovery terminal · {request.Reason}");
            return;
        }

        // The preference gates request creation only.  A durable request must
        // reconcile after restart or preference changes until Linux records a
        // terminal source-restoration outcome.
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
            StateChanged?.Invoke(
                this,
                $"BlueStacks degradation confirmed · target "
                    + $"{target.InstanceName} on {target.AdbPort}, PID "
                    + $"{identity.ProcessId}");
            var response = await _api.PostHostMaintenanceAsync(
                new { operation = "request" },
                cancellationToken);
            var request = response.HostMaintenance.Request;
            if (request is null || string.IsNullOrWhiteSpace(request.RequestId))
            {
                throw new InvalidOperationException(
                    "Linux accepted no durable BlueStacks maintenance request.");
            }
            _requestTargets[request.RequestId] = target;
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
                if (!_requestTargets.TryGetValue(request.RequestId, out target))
                {
                    target = BlueStacksRecoveryTarget.Capture(_settings());
                    _requestTargets[request.RequestId] = target;
                }
                previous = _controller.Inspect(target);
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
            (target, previous) = AcknowledgedTarget(acknowledged);
            _requestTargets[request.RequestId] = target;
            StateChanged?.Invoke(
                this,
                $"BlueStacks reconciliation · {target.InstanceName} · "
                    + $"acknowledged PID {previous.ProcessId}");
            BlueStacksRestartResult result;
            try
            {
                var current = _controller.Inspect(target);
                result = ExactIdentity(current, previous)
                    ? await _controller.RestartAcknowledgedAsync(
                        previous,
                        target,
                        CancellationToken.None)
                    : await _controller.ConfirmReplacementAsync(
                        previous,
                        target,
                        CancellationToken.None);
            }
            catch (BlueStacksListenerUnavailableException)
            {
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

    private static (BlueStacksRecoveryTarget Target, BlueStacksProcessIdentity Previous)
        AcknowledgedTarget(BlueStacksHostProcessIdentity acknowledged)
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
                "The durable BlueStacks acknowledgement does not match this "
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
            return ExactIdentity(_controller.Inspect(target), previous);
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
}
