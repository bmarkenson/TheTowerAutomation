namespace TheTower.ControlSurface;

internal sealed class BlueStacksMaintenanceCoordinator
{
    private readonly ControlSurfaceApi _api;
    private readonly BlueStacksInstanceController _controller;
    private readonly Func<ClientSettings> _settings;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly Dictionary<string, BlueStacksRestartResult> _pendingReports = [];
    private string? _lastRequestedAssessmentAt;

    public BlueStacksMaintenanceCoordinator(
        ControlSurfaceApi api,
        BlueStacksInstanceController controller,
        Func<ClientSettings> settings)
    {
        _api = api;
        _controller = controller;
        _settings = settings;
    }

    public event EventHandler<string>? StateChanged;

    public async Task ObserveStatusAsync(
        StatusResponse status,
        CancellationToken cancellationToken)
    {
        var settings = _settings();
        var maintenance = status.HostMaintenance;
        var request = maintenance.Request;
        if (request is null)
        {
            await RequestIfReadyAsync(
                status.EmulatorDegradation,
                settings,
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
            return;
        }

        // The preference gates creation only. Once Linux accepts a request,
        // Windows must reconcile it even if the preference is later cleared.
        if (!await _gate.WaitAsync(0, cancellationToken))
        {
            return;
        }
        try
        {
            await AdvanceAsync(
                settings,
                maintenance,
                request,
                cancellationToken);
        }
        finally
        {
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
        try
        {
            _ = BlueStacksInstanceController.ValidateExecutablePath(
                settings.BlueStacksPlayerExecutablePath);
            _ = BlueStacksInstanceController.ValidateInstanceName(
                settings.BlueStacksInstanceName);
            if (settings.WindowsBlueStacksAdbPort is < 1 or > 65535)
            {
                throw new ArgumentException(
                    "The Windows BlueStacks ADB port must be between 1 and 65535.");
            }
            await _api.PostHostMaintenanceAsync(
                new { operation = "request" },
                cancellationToken);
            _lastRequestedAssessmentAt = degradation.AssessedAt;
            StateChanged?.Invoke(
                this,
                "BlueStacks recovery requested · waiting for runtime quiescence");
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
            _gate.Release();
        }
    }

    private async Task AdvanceAsync(
        ClientSettings settings,
        HostMaintenanceStatus maintenance,
        HostMaintenanceRequest request,
        CancellationToken cancellationToken)
    {
        if (_pendingReports.TryGetValue(request.RequestId, out var pending))
        {
            await ReportCompletionOrRetainAsync(
                request.RequestId,
                pending,
                cancellationToken);
            return;
        }

        BlueStacksProcessIdentity? previous = null;
        var hostAcknowledged = request.State == "host_acknowledged";
        try
        {
            if (request.State == "requested")
            {
                if (!maintenance.HostRestartAuthorized)
                {
                    return;
                }
                previous = _controller.Inspect(
                    settings.BlueStacksPlayerExecutablePath,
                    settings.WindowsBlueStacksAdbPort);
                StateChanged?.Invoke(
                    this,
                    $"Quiesced · acknowledging PID {previous.ProcessId}");
                try
                {
                    await _api.PostHostMaintenanceAsync(
                        new
                        {
                            operation = "acknowledge",
                            request_id = request.RequestId,
                            host_id = previous.HostId,
                            adb_port = previous.AdbPort,
                            process_id = previous.ProcessId,
                            process_started_at = previous.ProcessStartedAtText,
                        },
                        cancellationToken);
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch (Exception exception)
                {
                    // The response can be lost after Linux commits the ack.
                    // Do not mutate the host or claim failure from uncertainty.
                    StateChanged?.Invoke(
                        this,
                        "BlueStacks acknowledgement result pending · "
                            + exception.Message);
                    return;
                }
                hostAcknowledged = true;
                StateChanged?.Invoke(
                    this,
                    $"Acknowledged · restarting exact PID {previous.ProcessId}");
                var restarted = await _controller.RestartAcknowledgedAsync(
                    previous,
                    settings.BlueStacksPlayerExecutablePath,
                    settings.BlueStacksInstanceName,
                    settings.WindowsBlueStacksAdbPort,
                    cancellationToken);
                await ReportCompletionOrRetainAsync(
                    request.RequestId,
                    restarted,
                    cancellationToken);
                return;
            }

            if (request.State != "host_acknowledged"
                || request.HostAcknowledgement is not { } acknowledged)
            {
                return;
            }
            previous = AcknowledgedIdentity(settings, acknowledged);
            BlueStacksRestartResult result;
            try
            {
                var current = _controller.Inspect(
                    settings.BlueStacksPlayerExecutablePath,
                    settings.WindowsBlueStacksAdbPort);
                result = current.ProcessId == previous.ProcessId
                    && current.ProcessStartedAtUtc == previous.ProcessStartedAtUtc
                    ? await _controller.RestartAcknowledgedAsync(
                        previous,
                        settings.BlueStacksPlayerExecutablePath,
                        settings.BlueStacksInstanceName,
                        settings.WindowsBlueStacksAdbPort,
                        cancellationToken)
                    : await _controller.ConfirmReplacementAsync(
                        previous,
                        settings.BlueStacksPlayerExecutablePath,
                        settings.WindowsBlueStacksAdbPort,
                        cancellationToken);
            }
            catch (BlueStacksListenerUnavailableException)
            {
                result = await _controller.StartAfterAcknowledgedStopAsync(
                    previous,
                    settings.BlueStacksPlayerExecutablePath,
                    settings.BlueStacksInstanceName,
                    settings.WindowsBlueStacksAdbPort,
                    cancellationToken);
            }
            await ReportCompletionOrRetainAsync(
                request.RequestId,
                result,
                cancellationToken);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception exception)
        {
            StateChanged?.Invoke(this, $"Recovery interrupted · {exception.Message}");
            var oldProcessIntact = previous is not null
                && ExactOldProcessStillOwnsListener(previous, settings);
            if (!hostAcknowledged || oldProcessIntact)
            {
                await ReportFailureBestEffortAsync(
                    request.RequestId,
                    exception.Message,
                    cancellationToken);
                return;
            }
            StateChanged?.Invoke(
                this,
                "Recovery state is uncertain after host acknowledgement; "
                    + "Linux remains held and reconciliation will retry");
        }
    }

    private BlueStacksProcessIdentity AcknowledgedIdentity(
        ClientSettings settings,
        BlueStacksHostProcessIdentity acknowledged)
    {
        if (!string.Equals(
                acknowledged.HostId,
                Environment.MachineName,
                StringComparison.OrdinalIgnoreCase)
            || acknowledged.AdbPort != settings.WindowsBlueStacksAdbPort
            || !DateTimeOffset.TryParse(
                acknowledged.ProcessStartedAt,
                out var processStartedAt))
        {
            throw new InvalidOperationException(
                "The durable BlueStacks acknowledgement does not match this Windows host.");
        }
        return new BlueStacksProcessIdentity(
            acknowledged.HostId,
            acknowledged.AdbPort,
            acknowledged.ProcessId,
            processStartedAt.ToUniversalTime(),
            BlueStacksInstanceController.ValidateExecutablePath(
                settings.BlueStacksPlayerExecutablePath));
    }

    private bool ExactOldProcessStillOwnsListener(
        BlueStacksProcessIdentity previous,
        ClientSettings settings)
    {
        try
        {
            var current = _controller.Inspect(
                settings.BlueStacksPlayerExecutablePath,
                settings.WindowsBlueStacksAdbPort);
            return current.ProcessId == previous.ProcessId
                && current.ProcessStartedAtUtc == previous.ProcessStartedAtUtc;
        }
        catch
        {
            return false;
        }
    }

    private async Task ReportCompletionOrRetainAsync(
        string requestId,
        BlueStacksRestartResult result,
        CancellationToken cancellationToken)
    {
        _pendingReports[requestId] = result;
        try
        {
            await ReportCompletionAsync(requestId, result, cancellationToken);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception exception)
        {
            StateChanged?.Invoke(
                this,
                "Replacement listener is ready; Linux completion report is pending · "
                    + exception.Message);
            return;
        }
        _pendingReports.Remove(requestId);
        StateChanged?.Invoke(
            this,
            $"Replacement listener ready · PID {result.Replacement.ProcessId}");
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
            StateChanged?.Invoke(this, $"Recovery failed safely · {reason}");
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception reportingException)
        {
            StateChanged?.Invoke(
                this,
                "Recovery failed and the Linux result report is pending · "
                    + reportingException.Message);
        }
    }

    private Task<StatusResponse> ReportCompletionAsync(
        string requestId,
        BlueStacksRestartResult result,
        CancellationToken cancellationToken) =>
        _api.PostHostMaintenanceAsync(
            new
            {
                operation = "complete",
                request_id = requestId,
                host_id = result.Replacement.HostId,
                adb_port = result.Replacement.AdbPort,
                process_id = result.Replacement.ProcessId,
                process_started_at = result.Replacement.ProcessStartedAtText,
                previous_process_id = result.Previous.ProcessId,
                previous_process_started_at = result.Previous.ProcessStartedAtText,
            },
            cancellationToken);
}
