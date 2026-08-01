using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost.Core;

public interface ITunnelConfigurationStore
{
    TunnelHostConfiguration Load();
    void Save(TunnelHostConfiguration configuration);
}

public interface ILinuxApiServiceController
{
    Task<LinuxApiServiceSnapshot> QueryAsync(
        TunnelHostConfiguration configuration,
        CancellationToken cancellationToken);

    Task<LinuxApiServiceSnapshot> ChangeAsync(
        TunnelHostConfiguration configuration,
        LinuxApiServiceAction action,
        CancellationToken cancellationToken);
}

public sealed class TunnelHostCoordinator : IAsyncDisposable
{
    private readonly object _gate = new();
    private readonly object _configurationGate = new();
    private readonly TunnelSupervisor _apiTunnel;
    private readonly TunnelSupervisor _adbTunnel;
    private readonly ITunnelConfigurationStore _configurationStore;
    private readonly ILinuxApiServiceController _serviceController;
    private readonly SemaphoreSlim _serviceGate = new(1, 1);
    private readonly string _hostInstanceId = Guid.NewGuid().ToString("N");
    private readonly DateTimeOffset _hostStartedAt;
    private readonly int _hostProcessId;
    private readonly string _hostVersion;
    private TunnelHostConfiguration _configuration;
    private LinuxApiServiceSnapshot _serviceState = new();
    private int _connectedClients;
    private long _stateRevision;

    public TunnelHostCoordinator(
        TunnelSupervisor apiTunnel,
        TunnelSupervisor adbTunnel,
        ITunnelConfigurationStore configurationStore,
        ILinuxApiServiceController serviceController,
        int? hostProcessId = null,
        string? hostVersion = null,
        DateTimeOffset? hostStartedAt = null)
    {
        _apiTunnel = apiTunnel;
        _adbTunnel = adbTunnel;
        _configurationStore = configurationStore;
        _serviceController = serviceController;
        _hostProcessId = hostProcessId ?? Environment.ProcessId;
        _hostVersion = hostVersion ?? TunnelHostProtocol.ProductVersion;
        _hostStartedAt = hostStartedAt ?? DateTimeOffset.UtcNow;
        try
        {
            _configuration = TunnelHostConfigurationValidator.Validate(
                configurationStore.Load(),
                requireDestination: false);
        }
        catch
        {
            _configuration = new TunnelHostConfiguration();
        }
    }

    public string HostInstanceId => _hostInstanceId;
    public DateTimeOffset HostStartedAt => _hostStartedAt;
    public int HostProcessId => _hostProcessId;
    public string HostVersion => _hostVersion;

    public void ClientConnected()
    {
        lock (_gate)
        {
            _connectedClients++;
            _stateRevision++;
        }
    }

    public void ClientDisconnected()
    {
        lock (_gate)
        {
            _connectedClients = Math.Max(0, _connectedClients - 1);
            _stateRevision++;
        }
    }

    public TunnelHostSnapshot Snapshot()
    {
        TunnelHostConfiguration configuration;
        LinuxApiServiceSnapshot serviceState;
        int clients;
        long revision;
        lock (_gate)
        {
            configuration = _configuration;
            serviceState = _serviceState;
            clients = _connectedClients;
            revision = _stateRevision;
        }
        var api = _apiTunnel.Snapshot();
        var adb = _adbTunnel.Snapshot();
        return new TunnelHostSnapshot
        {
            HostVersion = _hostVersion,
            HostInstanceId = _hostInstanceId,
            HostProcessId = _hostProcessId,
            HostStartedAt = _hostStartedAt,
            StateRevision = revision + api.Revision + adb.Revision,
            ConnectedGuiClients = clients,
            Configuration = configuration,
            ApiTunnel = api,
            AdbTunnel = adb,
            LinuxApiService = serviceState,
        };
    }

    public async Task<TunnelHostResponse> HandleAsync(
        TunnelHostRequest request,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(request.RequestId)
            || request.RequestId.Length > 128
            || string.IsNullOrWhiteSpace(request.ClientInstanceId)
            || request.ClientInstanceId.Length > 128)
        {
            throw new TunnelHostCommandException(
                "invalid_request",
                "The request and client identifiers must be non-empty and bounded.");
        }

        var shutdown = false;
        switch (request.Command)
        {
            case TunnelHostCommand.GetStatus:
                break;
            case TunnelHostCommand.Configure:
                SaveConfiguration(RequireConfiguration(request));
                break;
            case TunnelHostCommand.StartTunnel:
            {
                var configuration = SaveConfiguration(RequireConfiguration(request));
                var supervisor = SupervisorFor(RequireTunnel(request));
                var state = await supervisor.StartAsync(configuration, cancellationToken);
                ThrowIfStartDidNotStabilize(state);
                break;
            }
            case TunnelHostCommand.StopTunnel:
                await SupervisorFor(RequireTunnel(request)).StopAsync(cancellationToken);
                break;
            case TunnelHostCommand.RestartTunnel:
            {
                var configuration = SaveConfiguration(RequireConfiguration(request));
                var supervisor = SupervisorFor(RequireTunnel(request));
                var state = await supervisor.RestartAsync(
                    configuration,
                    cancellationToken);
                ThrowIfStartDidNotStabilize(state);
                break;
            }
            case TunnelHostCommand.QueryLinuxApiService:
            {
                var configuration = SaveConfiguration(RequireConfiguration(request));
                await QueryServiceAsync(configuration, cancellationToken);
                break;
            }
            case TunnelHostCommand.ChangeLinuxApiService:
            {
                var configuration = SaveConfiguration(RequireConfiguration(request));
                var action = request.ServiceAction
                    ?? throw new TunnelHostCommandException(
                        "invalid_request",
                        "A fixed Linux API service action is required.");
                await ChangeServiceAsync(configuration, action, cancellationToken);
                break;
            }
            case TunnelHostCommand.ShutdownHost:
                if (!request.ConfirmShutdown)
                {
                    throw new TunnelHostCommandException(
                        "confirmation_required",
                        "Host shutdown requires explicit confirmation because it stops all desired tunnels.");
                }
                await StopAllAsync(cancellationToken);
                shutdown = true;
                break;
            default:
                throw new TunnelHostCommandException(
                    "unsupported_command",
                    $"Tunnel host command {request.Command} is unsupported.");
        }

        lock (_gate)
        {
            _stateRevision++;
        }
        return new TunnelHostResponse
        {
            RequestId = request.RequestId,
            Ok = true,
            Snapshot = Snapshot(),
            ShutdownRequested = shutdown,
        };
    }

    private TunnelHostConfiguration SaveConfiguration(
        TunnelHostConfiguration configuration)
    {
        configuration = TunnelHostConfigurationValidator.Validate(configuration);
        lock (_configurationGate)
        {
            TunnelHostConfiguration current;
            lock (_gate)
            {
                current = _configuration;
            }
            if (current != configuration)
            {
                _configurationStore.Save(configuration);
                lock (_gate)
                {
                    _configuration = configuration;
                    _serviceState = new LinuxApiServiceSnapshot();
                    _stateRevision++;
                }
            }
        }
        return configuration;
    }

    private async Task QueryServiceAsync(
        TunnelHostConfiguration configuration,
        CancellationToken cancellationToken)
    {
        await _serviceGate.WaitAsync(cancellationToken);
        try
        {
            SetServiceInFlight(true);
            try
            {
                SetServiceState(await _serviceController.QueryAsync(
                    configuration,
                    cancellationToken));
            }
            catch (Exception exc) when (exc is not OperationCanceledException)
            {
                SetServiceFailure(exc.Message);
                throw new TunnelHostCommandException(
                    "service_query_failed",
                    exc.Message,
                    innerException: exc);
            }
        }
        finally
        {
            SetServiceInFlight(false);
            _serviceGate.Release();
        }
    }

    private async Task ChangeServiceAsync(
        TunnelHostConfiguration configuration,
        LinuxApiServiceAction action,
        CancellationToken cancellationToken)
    {
        await _serviceGate.WaitAsync(cancellationToken);
        try
        {
            SetServiceInFlight(true);
            try
            {
                SetServiceState(await _serviceController.ChangeAsync(
                    configuration,
                    action,
                    cancellationToken));
            }
            catch (Exception exc) when (exc is not OperationCanceledException)
            {
                SetServiceFailure(exc.Message);
                throw new TunnelHostCommandException(
                    "service_command_failed",
                    exc.Message,
                    innerException: exc);
            }
        }
        finally
        {
            SetServiceInFlight(false);
            _serviceGate.Release();
        }
    }

    private void SetServiceInFlight(bool inFlight)
    {
        lock (_gate)
        {
            _serviceState = _serviceState with { CommandInFlight = inFlight };
            _stateRevision++;
        }
    }

    private void SetServiceState(LinuxApiServiceSnapshot state)
    {
        lock (_gate)
        {
            _serviceState = state with { CommandInFlight = true };
            _stateRevision++;
        }
    }

    private void SetServiceFailure(string detail)
    {
        lock (_gate)
        {
            _serviceState = new LinuxApiServiceSnapshot
            {
                QuerySucceeded = false,
                CommandInFlight = true,
                ObservedAt = DateTimeOffset.UtcNow,
                LastDiagnostic = detail.Length <= 4000
                    ? detail
                    : detail[..4000],
            };
            _stateRevision++;
        }
    }

    private static TunnelHostConfiguration RequireConfiguration(
        TunnelHostRequest request) =>
        request.Configuration
        ?? throw new TunnelHostCommandException(
            "invalid_request",
            "Validated tunnel configuration is required for this command.");

    private static TunnelKind RequireTunnel(TunnelHostRequest request) =>
        request.Tunnel
        ?? throw new TunnelHostCommandException(
            "invalid_request",
            "A tunnel kind is required for this command.");

    private TunnelSupervisor SupervisorFor(TunnelKind kind) => kind switch
    {
        TunnelKind.Api => _apiTunnel,
        TunnelKind.Adb => _adbTunnel,
        _ => throw new TunnelHostCommandException(
            "invalid_request",
            "Unknown tunnel kind."),
    };

    private void ThrowIfStartDidNotStabilize(TunnelStateSnapshot state)
    {
        if (state.ObservedState == TunnelObservedState.Running)
        {
            return;
        }
        var errorCode = state.ObservedState == TunnelObservedState.Conflict
            ? "forward_conflict"
            : "tunnel_start_failed";
        throw new TunnelHostCommandException(
            errorCode,
            state.LastDiagnostic?.Summary
                ?? $"The {state.Kind} tunnel did not reach the running state.",
            Snapshot());
    }

    public async Task StopAllAsync(CancellationToken cancellationToken)
    {
        await Task.WhenAll(
            _apiTunnel.StopAsync(cancellationToken),
            _adbTunnel.StopAsync(cancellationToken));
    }

    public async ValueTask DisposeAsync()
    {
        await StopAllAsync(CancellationToken.None);
        await _apiTunnel.DisposeAsync();
        await _adbTunnel.DisposeAsync();
        _serviceGate.Dispose();
    }
}

public sealed class TunnelHostIdlePolicy
{
    private readonly TimeSpan _idlePeriod;
    private DateTimeOffset? _idleSince;

    public TunnelHostIdlePolicy(TimeSpan? idlePeriod = null)
    {
        _idlePeriod = idlePeriod ?? TimeSpan.FromSeconds(15);
        if (_idlePeriod < TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(nameof(idlePeriod));
        }
    }

    public bool ShouldExit(TunnelHostSnapshot snapshot, DateTimeOffset now)
    {
        var idle = snapshot.ConnectedGuiClients == 0
            && !snapshot.ApiTunnel.Desired
            && !snapshot.AdbTunnel.Desired;
        if (!idle)
        {
            _idleSince = null;
            return false;
        }
        _idleSince ??= now;
        return now - _idleSince >= _idlePeriod;
    }
}
