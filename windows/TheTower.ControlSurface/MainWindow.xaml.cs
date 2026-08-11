using System.Collections.ObjectModel;
using System.Globalization;
using System.Net;
using System.Net.NetworkInformation;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using TheTower.TunnelProtocol;

namespace TheTower.ControlSurface;

public partial class MainWindow : Window
{
    private const string OverviewPageId = "overview";
    private const string ActivityPageId = "activity";
    private const string PerksPageId = "perks";
    private const string SystemPageId = "system";
    private const string ServicesSystemPageId = "services";
    private const string ConnectionsSystemPageId = "connections";
    private const string DiagnosticsSystemPageId = "diagnostics";
    private readonly ControlSurfaceApi _api = new();
    private readonly HostPerformanceTracker _hostPerformance;
    private readonly TunnelHostConnection _tunnelHost = new();
    private readonly ClientSettings _settings;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(5) };
    private readonly DispatcherTimer _activityTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly DispatcherTimer _serviceStatusTimer = new()
    {
        Interval = TimeSpan.FromSeconds(15),
    };
    private readonly SemaphoreSlim _refreshGate = new(1, 1);
    private readonly SemaphoreSlim _controlMutationGate = new(1, 1);
    private readonly SemaphoreSlim _battleRefreshGate = new(1, 1);
    private readonly SemaphoreSlim _activityRefreshGate = new(1, 1);
    private readonly SemaphoreSlim _serviceStatusGate = new(1, 1);
    private readonly SemaphoreSlim _tunnelHostRefreshGate = new(1, 1);
    private readonly ObservableCollection<ActivityEntry> _activity = [];
    private readonly ObservableCollection<CurrentBattlePerkItem> _currentBattlePerks = [];
    private readonly StrategySelectionCoordinator _strategySelection = new();
    private BetterControlActionAvailability _attachBattleAvailability = new()
    {
        Available = false,
        Reason = "Better Control Model status is unavailable.",
    };
    private BattleListResponse _latestBattles = new();
    private BattleHistoryWindow? _battleHistoryWindow;
    private CancellationTokenSource? _refreshCancellation;
    private CancellationTokenSource? _battleRefreshCancellation;
    private CancellationTokenSource? _activityRefreshCancellation;
    private CancellationTokenSource? _serviceStatusCancellation;
    private ActivityEntry? _expandedActivityEntry;
    private string? _activitySourceFileId;
    private string? _activityEndCursor;
    private string? _activityClearCursor;
    private string? _activityScopeId;
    private string _strategyRequestMessage = "";
    private bool _updatingStrategySelection;
    private bool _strategyLifecycleAvailable;
    private bool _strategyProcessActive;
    private bool _strategyDegradedObserver;
    private string? _configuredStrategy;
    private string? _currentStrategy;
    private string? _requestedStrategy;
    private string? _pendingStrategy;
    private string _strategyApplyMode = "next_boundary";
    private double _gameSpeedTarget = 6.3;
    private bool _updatingGameSpeedTargetSelection;
    private bool _gameSpeedTargetRequestInFlight;
    private ControlSurfaceCompatibilityResult? _serverCompatibility;
    private LinuxApiServiceSnapshot? _controlSurfaceServiceState;
    private TunnelHostSnapshot? _tunnelHostSnapshot;
    private TunnelHostProtocolMismatchException? _tunnelHostProtocolMismatch;
    private bool _controlSurfaceServiceActionInFlight;
    private bool _controlSurfaceRestartInFlight;
    private bool _apiTunnelActionInFlight;
    private bool _adbTunnelRestartInFlight;
    private bool _adbForwardStarting;
    private string _apiToken = "";
    private bool _updatingAdbPortDraft;
    private bool _adbPortDraftDirty;
    private bool _adbPortRequestInFlight;
    private int? _configuredAdbPort;
    private int? _requestedAdbPort;
    private string? _activeAdbTarget;
    private bool _adbLifecycleAvailable;
    private bool _adbProcessActive;
    private bool _adbPausedAndAcknowledged;
    private StartupGateContext? _startupGateContext;
    private IReadOnlyDictionary<string, StartupGateWaiverStatus> _startupGateWaivers
        = new Dictionary<string, StartupGateWaiverStatus>();
    private GateDecisionStatus? _currentGateDecision;
    private string? _autoPromptedGateRequestId;
    private bool _gateDecisionDialogOpen;
    private ExclusiveValidationReceiptStatus? _currentTournamentLaunch;
    private string? _autoPromptedTournamentLaunchRequestId;
    private bool _tournamentLaunchDialogOpen;
    private bool _tournamentLaunchCanStart;
    public MainWindow()
    {
        InitializeComponent();
        ActivityGrid.ItemsSource = _activity;
        CurrentPerksGrid.ItemsSource = _currentBattlePerks;

        _settings = SettingsStore.Load();
        RenderConnectionPreferences();
        WindowPlacementStore.Restore(this, _settings.MainWindowPlacement);
        RestoreMainWindowLayout();
        _api.Configure(_settings.BaseUrl, _apiToken);
        _hostPerformance = new HostPerformanceTracker(_api);
        _hostPerformance.SetSamplingEnabled(
            _settings.HostPerformanceSamplingEnabled);
        _hostPerformance.SnapshotUpdated += HostPerformance_SnapshotUpdated;
        _timer.Tick += async (_, _) =>
        {
            RefreshWindowsAdbListenerStatus();
            await Task.WhenAll(
                RefreshTunnelHostStatusAsync(),
                RefreshStatusAsync(),
                RefreshBattlesAsync());
        };
        _activityTimer.Tick += async (_, _) => await RefreshActivityAsync();
        _serviceStatusTimer.Tick += async (_, _) =>
            await RefreshControlSurfaceServiceStatusAsync();
        ActivityLevelFilter.SelectionChanged += ActivityLevelFilter_SelectionChanged;
        ActivityScopeFilter.SelectionChanged += ActivityScopeFilter_SelectionChanged;
        Loaded += async (_, _) =>
        {
            RefreshWindowsAdbListenerStatus();
            _hostPerformance.Start();
            _timer.Start();
            _activityTimer.Start();
            _serviceStatusTimer.Start();
            await InitializeTunnelHostAsync();
            await Task.WhenAll(
                RefreshStatusAsync(),
                RefreshBattlesAsync(),
                RefreshActivityAsync(),
                RefreshControlSurfaceServiceStatusAsync(force: true));
        };
        Closing += (_, _) =>
        {
            CaptureWindowPlacement();
            CaptureMainWindowLayout();
            SaveSettingsBestEffort();
        };
        Closed += async (_, _) =>
        {
            _timer.Stop();
            _activityTimer.Stop();
            _serviceStatusTimer.Stop();
            _refreshCancellation?.Cancel();
            _battleRefreshCancellation?.Cancel();
            _activityRefreshCancellation?.Cancel();
            _serviceStatusCancellation?.Cancel();
            _battleHistoryWindow?.Close();
            await _tunnelHost.DisposeAsync();
            _hostPerformance.SnapshotUpdated -= HostPerformance_SnapshotUpdated;
            _hostPerformance.Dispose();
            _api.Dispose();
        };
    }

    private void ShowControls_Click(object sender, RoutedEventArgs e) =>
        SelectPage(SidebarTabs, OverviewPageId);

    private void ShowActivity_Click(object sender, RoutedEventArgs e) =>
        SelectPage(SidebarTabs, ActivityPageId);

    private void ShowPerks_Click(object sender, RoutedEventArgs e) =>
        SelectPage(SidebarTabs, PerksPageId);

    private void ShowSystem_Click(object sender, RoutedEventArgs e) =>
        SelectPage(SidebarTabs, SystemPageId);

    private void ShowConnections_Click(object sender, RoutedEventArgs e)
    {
        SelectPage(SidebarTabs, SystemPageId);
        SelectPage(SystemTabs, ConnectionsSystemPageId);
    }

    private async void ShowPreferences_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new PreferencesWindow(_settings, _apiToken)
        {
            Owner = this,
        };
        if (dialog.ShowDialog() != true || dialog.Result is null)
        {
            return;
        }

        await ApplyPreferencesAsync(
            dialog.Result,
            dialog.ResetLayoutRequested);
    }

    private async Task ApplyPreferencesAsync(
        PreferencesResult preferences,
        bool resetLayout)
    {
        var apiChanged = !string.Equals(
                _settings.BaseUrl,
                preferences.BaseUrl,
                StringComparison.OrdinalIgnoreCase)
            || !string.Equals(
                _apiToken,
                preferences.InMemoryToken,
                StringComparison.Ordinal);
        var destinationChanged = !string.Equals(
            _settings.SshDestination,
            preferences.TunnelConfiguration.SshDestination,
            StringComparison.OrdinalIgnoreCase);

        _settings.BaseUrl = preferences.BaseUrl;
        _apiToken = preferences.InMemoryToken;
        _settings.SshDestination =
            preferences.TunnelConfiguration.SshDestination;
        _settings.LocalTunnelPort =
            preferences.TunnelConfiguration.LocalApiPort;
        _settings.RemoteApiPort =
            preferences.TunnelConfiguration.RemoteApiPort;
        _settings.WindowsBlueStacksAdbPort =
            preferences.TunnelConfiguration.WindowsBlueStacksAdbPort;
        _settings.LinuxAdbForwardPort =
            preferences.TunnelConfiguration.LinuxAdbPort;
        _settings.HostPerformanceSamplingEnabled =
            preferences.HostPerformanceSamplingEnabled;
        _api.Configure(_settings.BaseUrl, _apiToken);
        _hostPerformance.SetSamplingEnabled(
            _settings.HostPerformanceSamplingEnabled);
        if (resetLayout)
        {
            _settings.MainWindowLayout = new MainWindowLayoutSettings();
            RestoreMainWindowLayout();
        }
        SaveSettingsBestEffort();
        RenderConnectionPreferences();
        RefreshWindowsAdbListenerStatus();

        if (apiChanged)
        {
            SetHttpConnectionStatus(
                "Not checked — preferences changed",
                new SolidColorBrush(Color.FromRgb(241, 191, 91)),
                "Use Connect now to probe the saved API URL with the current in-memory token.");
        }
        if (destinationChanged)
        {
            SetUnqueriedControlSurfaceServiceState(
                "Service state has not been queried for this SSH destination.");
        }

        if (_tunnelHostSnapshot is not null
            && _tunnelHostProtocolMismatch is null
            && TunnelHostConfigurationValidator.IsValidDestination(
                _settings.SshDestination))
        {
            try
            {
                var snapshot = await _tunnelHost.SendAsync(
                    new TunnelHostRequest
                    {
                        Command = TunnelHostCommand.Configure,
                        Configuration = BuildTunnelHostConfiguration(),
                    },
                    CancellationToken.None);
                RenderTunnelHostSnapshot(snapshot);
                await RefreshControlSurfaceServiceStatusAsync(force: true);
            }
            catch (Exception exc)
            {
                LastErrorText.Text =
                    $"Preferences were saved locally, but the tunnel host could not accept them: {exc.Message}";
            }
        }
        UpdateControlSurfaceServiceControls();
        UpdateControlSurfaceCompatibility();
    }

    private void SidebarTabs_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (!ReferenceEquals(e.OriginalSource, SidebarTabs))
        {
            return;
        }
        if (ReferenceEquals(SidebarTabs.SelectedItem, ActivityTab)
            && ActivityAutoFollowBox.IsChecked == true
            && _activity.Count > 0)
        {
            _ = Dispatcher.InvokeAsync(
                () => ActivityGrid.ScrollIntoView(_activity[0]),
                DispatcherPriority.Background);
        }
        if (!IsLoaded)
        {
            return;
        }
        _settings.MainWindowLayout.DashboardPage = SelectedPageId(
            SidebarTabs,
            OverviewPageId);
        SaveSettingsBestEffort();
    }

    private void PreviousStateToggle_Click(object sender, RoutedEventArgs e)
    {
        var expanded = PreviousStatePanel.Visibility != Visibility.Visible;
        SetPreviousStateExpanded(expanded);
        _settings.MainWindowLayout.PreviousStateExpanded = expanded;
        SaveSettingsBestEffort();
    }

    private void HostHealthToggle_Click(object sender, RoutedEventArgs e)
    {
        var expanded = HostPerformancePanel.Visibility != Visibility.Visible;
        SetHostHealthExpanded(expanded);
        _settings.MainWindowLayout.HostHealthExpanded = expanded;
        SaveSettingsBestEffort();
    }

    private void LatestBattleToggle_Click(object sender, RoutedEventArgs e)
    {
        var expanded = LatestBattleTitleRow.Height.Value == 0;
        SetLatestBattleExpanded(expanded);
        _settings.MainWindowLayout.LatestBattleExpanded = expanded;
        SaveSettingsBestEffort();
    }

    private void ResetLayout_Click(object sender, RoutedEventArgs e)
    {
        _settings.MainWindowLayout = new MainWindowLayoutSettings();
        RestoreMainWindowLayout();
        SaveSettingsBestEffort();
    }

    private void RestoreMainWindowLayout()
    {
        _settings.MainWindowLayout ??= new MainWindowLayoutSettings();
        var layout = _settings.MainWindowLayout;
        if (string.IsNullOrWhiteSpace(layout.DashboardPage))
        {
            (layout.DashboardPage, layout.SystemPage) = layout.SidebarTabIndex switch
            {
                1 => (SystemPageId, ServicesSystemPageId),
                2 => (SystemPageId, ConnectionsSystemPageId),
                3 => (SystemPageId, DiagnosticsSystemPageId),
                4 => (PerksPageId, ServicesSystemPageId),
                _ => (OverviewPageId, ServicesSystemPageId),
            };
            layout.PreviousStateExpanded = false;
            layout.HostHealthExpanded = false;
            layout.LatestBattleExpanded = false;
        }
        SelectPage(SidebarTabs, layout.DashboardPage, OverviewPageId);
        SelectPage(SystemTabs, layout.SystemPage, ServicesSystemPageId);
        SetPreviousStateExpanded(layout.PreviousStateExpanded);
        SetHostHealthExpanded(layout.HostHealthExpanded);
        SetLatestBattleExpanded(layout.LatestBattleExpanded);
    }

    private void CaptureMainWindowLayout()
    {
        var layout = _settings.MainWindowLayout;
        layout.PreviousStateExpanded =
            PreviousStatePanel.Visibility == Visibility.Visible;
        layout.HostHealthExpanded =
            HostPerformancePanel.Visibility == Visibility.Visible;
        layout.LatestBattleExpanded = LatestBattleTitleRow.Height.Value > 0;
        layout.DashboardPage = SelectedPageId(SidebarTabs, OverviewPageId);
        layout.SystemPage = SelectedPageId(SystemTabs, ServicesSystemPageId);
    }

    private static void SelectPage(
        TabControl tabs,
        string? requestedPage,
        string? fallbackPage = null)
    {
        var page = tabs.Items
            .OfType<TabItem>()
            .FirstOrDefault(item => string.Equals(
                item.Tag?.ToString(),
                requestedPage,
                StringComparison.OrdinalIgnoreCase));
        if (page is null && fallbackPage is not null)
        {
            page = tabs.Items
                .OfType<TabItem>()
                .FirstOrDefault(item => string.Equals(
                    item.Tag?.ToString(),
                    fallbackPage,
                    StringComparison.OrdinalIgnoreCase));
        }
        tabs.SelectedItem = page ?? tabs.Items.OfType<TabItem>().FirstOrDefault();
    }

    private static string SelectedPageId(TabControl tabs, string fallback) =>
        tabs.SelectedItem is TabItem item
            && item.Tag is string pageId
            && !string.IsNullOrWhiteSpace(pageId)
                ? pageId
                : fallback;

    private void SetPreviousStateExpanded(bool expanded)
    {
        PreviousStatePanel.Visibility = expanded
            ? Visibility.Visible
            : Visibility.Collapsed;
        PreviousStateToggleButton.Content = expanded
            ? "Hide previous state"
            : "Show previous state";
    }

    private void SetHostHealthExpanded(bool expanded)
    {
        HostPerformancePanel.Visibility = expanded
            ? Visibility.Visible
            : Visibility.Collapsed;
        HostHealthToggleButton.Content = expanded
            ? "Hide host health"
            : "Show host health";
    }

    private void SetLatestBattleExpanded(bool expanded)
    {
        LatestBattleRow.MinHeight = 48;
        LatestBattleRow.Height = GridLength.Auto;
        LatestBattleSplitterRow.Height = new GridLength(0);
        LatestBattleSplitter.Visibility = Visibility.Collapsed;
        LatestBattleTitleRow.Height = expanded
            ? GridLength.Auto
            : new GridLength(0);
        LatestBattleMetricsRow.Height = expanded
            ? GridLength.Auto
            : new GridLength(0);
        LatestBattleToggleButton.Content = expanded
            ? "Hide details"
            : "Details";
    }

    private async void Connect_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            _api.Configure(_settings.BaseUrl, _apiToken);
            SaveSettings();
            await Task.WhenAll(
                RefreshStatusAsync(force: true),
                RefreshBattlesAsync(force: true),
                RefreshActivityAsync(force: true),
                RefreshControlSurfaceServiceStatusAsync(force: true));
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
    }

    private async Task InitializeTunnelHostAsync()
    {
        try
        {
            var snapshot = await _tunnelHost.EnsureConnectedAsync(
                startIfMissing: true,
                CancellationToken.None);
            if (string.IsNullOrWhiteSpace(snapshot.Configuration.SshDestination)
                && TunnelHostConfigurationValidator.IsValidDestination(
                    _settings.SshDestination))
            {
                snapshot = await _tunnelHost.SendAsync(
                    new TunnelHostRequest
                    {
                        Command = TunnelHostCommand.Configure,
                        Configuration = BuildTunnelHostConfiguration(),
                    },
                    CancellationToken.None);
            }
            else if (!string.IsNullOrWhiteSpace(
                         snapshot.Configuration.SshDestination))
            {
                ApplyTunnelHostConfiguration(snapshot.Configuration);
            }
            RenderTunnelHostSnapshot(snapshot);
        }
        catch (TunnelHostProtocolMismatchException exc)
        {
            RenderTunnelHostProtocolMismatch(exc);
        }
        catch (Exception exc)
        {
            RenderTunnelHostUnavailable(exc.Message);
        }
    }

    private async Task RefreshTunnelHostStatusAsync(bool force = false)
    {
        var entered = force
            ? await WaitForTunnelHostRefreshAsync()
            : await _tunnelHostRefreshGate.WaitAsync(0);
        if (!entered)
        {
            return;
        }
        try
        {
            var snapshot = await _tunnelHost.EnsureConnectedAsync(
                startIfMissing: false,
                CancellationToken.None);
            RenderTunnelHostSnapshot(snapshot);
        }
        catch (TunnelHostProtocolMismatchException exc)
        {
            RenderTunnelHostProtocolMismatch(exc);
        }
        catch (Exception exc)
        {
            RenderTunnelHostUnavailable(exc.Message);
        }
        finally
        {
            _tunnelHostRefreshGate.Release();
        }
    }

    private async Task<bool> WaitForTunnelHostRefreshAsync()
    {
        await _tunnelHostRefreshGate.WaitAsync();
        return true;
    }

    private TunnelHostConfiguration BuildTunnelHostConfiguration() =>
        TunnelHostConfigurationValidator.Validate(new TunnelHostConfiguration
        {
            SshDestination = _settings.SshDestination,
            LocalApiPort = _settings.LocalTunnelPort,
            RemoteApiPort = _settings.RemoteApiPort,
            WindowsBlueStacksAdbPort =
                _settings.WindowsBlueStacksAdbPort,
            LinuxAdbPort = _settings.LinuxAdbForwardPort,
        });

    private void ApplyTunnelHostConfiguration(
        TunnelHostConfiguration configuration)
    {
        _settings.SshDestination = configuration.SshDestination;
        _settings.LocalTunnelPort = configuration.LocalApiPort;
        _settings.RemoteApiPort = configuration.RemoteApiPort;
        _settings.WindowsBlueStacksAdbPort =
            configuration.WindowsBlueStacksAdbPort;
        _settings.LinuxAdbForwardPort = configuration.LinuxAdbPort;
        _settings.BaseUrl = $"http://127.0.0.1:{configuration.LocalApiPort}";
        _api.Configure(_settings.BaseUrl, _apiToken);
        RenderConnectionPreferences();
    }

    private void RenderConnectionPreferences()
    {
        ConfiguredApiUrlText.Text = _settings.BaseUrl;
        TokenStateText.Text = string.IsNullOrWhiteSpace(_apiToken)
            ? "None (normal loopback route)"
            : "Present for this app session (never saved)";
        ConfiguredSshDestinationText.Text = string.IsNullOrWhiteSpace(
            _settings.SshDestination)
                ? "Not configured"
                : _settings.SshDestination;
        ConfiguredApiForwardText.Text =
            $"127.0.0.1:{_settings.LocalTunnelPort} → Linux "
            + $"127.0.0.1:{_settings.RemoteApiPort}";
        ConfiguredAdbForwardText.Text =
            $"Linux 127.0.0.1:{_settings.LinuxAdbForwardPort} → Windows "
            + $"127.0.0.1:{_settings.WindowsBlueStacksAdbPort}";
    }

    private void RenderTunnelHostSnapshot(TunnelHostSnapshot snapshot)
    {
        _tunnelHostSnapshot = snapshot;
        _tunnelHostProtocolMismatch = null;
        TunnelHostStatusText.Text =
            $"Connected to per-user host PID {snapshot.HostProcessId} "
            + $"(protocol v{snapshot.ProtocolVersion}, host {snapshot.HostVersion}).";
        TunnelHostStatusText.Foreground =
            new SolidColorBrush(Color.FromRgb(73, 214, 157));
        TunnelHostStatusText.ToolTip =
            $"Instance {snapshot.HostInstanceId}; started "
            + $"{snapshot.HostStartedAt.LocalDateTime:g}. Closing this GUI "
            + "does not stop a desired tunnel.";
        RestartTunnelHostButton.IsEnabled = true;
        RenderApiTunnelState(snapshot.ApiTunnel);
        RenderAdbTunnelState(snapshot.AdbTunnel);
        if (snapshot.LinuxApiService.ObservedAt is not null)
        {
            if (snapshot.LinuxApiService.QuerySucceeded)
            {
                RenderControlSurfaceServiceState(snapshot.LinuxApiService);
            }
            else if (!string.IsNullOrWhiteSpace(
                         snapshot.LinuxApiService.LastDiagnostic))
            {
                SetUnknownControlSurfaceServiceState(
                    snapshot.LinuxApiService.LastDiagnostic);
            }
        }
        else
        {
            SetUnqueriedControlSurfaceServiceState(
                "Linux API service state has not been queried by this host.");
        }
        UpdateControlSurfaceServiceControls();
        UpdateRestartSshControls();
    }

    private void RenderTunnelHostProtocolMismatch(
        TunnelHostProtocolMismatchException exception)
    {
        _tunnelHostProtocolMismatch = exception;
        _tunnelHostSnapshot = null;
        TunnelHostStatusText.Text =
            "Protocol mismatch — explicit tunnel-host restart required. "
            + exception.Message;
        TunnelHostStatusText.Foreground =
            new SolidColorBrush(Color.FromRgb(255, 113, 135));
        TunnelHostStatusText.ToolTip =
            "Restarting the companion stops its owned SSH children and starts "
            + "the packaged host with both tunnel desires cleared.";
        RestartTunnelHostButton.IsEnabled = true;
        SetApiTunnelTopStatus(
            "Host mismatch",
            exception.Message,
            new SolidColorBrush(Color.FromRgb(255, 113, 135)));
        SetAdbTunnelTopStatus(
            "Host mismatch",
            exception.Message,
            new SolidColorBrush(Color.FromRgb(255, 113, 135)));
        StartTunnelButton.IsEnabled = false;
        StopTunnelButton.IsEnabled = false;
        StartAdbForwardButton.IsEnabled = false;
        StopAdbForwardButton.IsEnabled = false;
        UpdateControlSurfaceServiceControls();
        UpdateRestartSshControls();
    }

    private void RenderTunnelHostUnavailable(string detail)
    {
        _tunnelHostSnapshot = null;
        TunnelHostStatusText.Text = $"Tunnel host unavailable: {detail}";
        TunnelHostStatusText.Foreground =
            new SolidColorBrush(Color.FromRgb(255, 113, 135));
        TunnelHostStatusText.ToolTip = detail;
        RestartTunnelHostButton.IsEnabled = true;
        SetApiTunnelTopStatus(
            "Host unavailable",
            detail,
            new SolidColorBrush(Color.FromRgb(255, 113, 135)));
        SetAdbTunnelTopStatus(
            "Host unavailable",
            detail,
            new SolidColorBrush(Color.FromRgb(255, 113, 135)));
        StartTunnelButton.IsEnabled = false;
        StopTunnelButton.IsEnabled = false;
        StartAdbForwardButton.IsEnabled = false;
        StopAdbForwardButton.IsEnabled = false;
        UpdateControlSurfaceServiceControls();
        UpdateRestartSshControls();
    }

    private async void RestartTunnelHost_Click(
        object sender,
        RoutedEventArgs e)
    {
        if (MessageBox.Show(
                this,
                "Restart the per-user tunnel host?\n\nThis explicitly stops "
                + "both host-owned SSH processes. The replacement loads saved "
                + "configuration but does not replay either tunnel until you "
                + "start it again.",
                "Restart tunnel host",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning) != MessageBoxResult.Yes)
        {
            return;
        }

        RestartTunnelHostButton.IsEnabled = false;
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(15));
            var snapshot = await _tunnelHost.RestartHostAsync(
                _tunnelHostProtocolMismatch,
                cancellation.Token);
            ApplyTunnelHostConfiguration(snapshot.Configuration);
            RenderTunnelHostSnapshot(snapshot);
            SetHttpConnectionStatus(
                "Unavailable — tunnel host restarted",
                new SolidColorBrush(Color.FromRgb(241, 191, 91)));
        }
        catch (Exception exc)
        {
            RenderTunnelHostUnavailable(exc.Message);
            ShowError(exc);
        }
        finally
        {
            RestartTunnelHostButton.IsEnabled = true;
        }
    }

    private async void ToggleControlSurfaceService_Click(
        object sender,
        RoutedEventArgs e)
    {
        if (_controlSurfaceServiceState is null)
        {
            return;
        }
        await ChangeControlSurfaceServiceAsync(
            _controlSurfaceServiceState.IsActive
                ? LinuxApiServiceAction.Stop
                : LinuxApiServiceAction.Start);
    }

    private async void RestartControlSurface_Click(object sender, RoutedEventArgs e)
    {
        await ChangeControlSurfaceServiceAsync(LinuxApiServiceAction.Restart);
    }

    private async Task ChangeControlSurfaceServiceAsync(
        LinuxApiServiceAction action)
    {
        var destination = _settings.SshDestination.Trim();
        if (!TunnelHostConfigurationValidator.IsValidDestination(destination))
        {
            ShowError(new InvalidOperationException(
                $"Enter a valid Linux SSH destination before {ServiceActionPresentParticiple(action).ToLowerInvariant()} the service."));
            return;
        }
        var confirmation = action switch
        {
            LinuxApiServiceAction.Stop =>
                "Stop the fixed Linux API service "
                + "(thetower-control-surface.service)?\n\n"
                + "The Windows GUI will lose HTTP API access until the service "
                + "is started again. Main automation and both SSH tunnels are unchanged.",
            LinuxApiServiceAction.Restart =>
                "Restart the fixed Linux API service "
                + "(thetower-control-surface.service)?\n\n"
                + "This briefly interrupts the control API. It does not restart "
                + "the main automation process or alter the active battle.",
            _ => null,
        };
        if (confirmation is not null
            && MessageBox.Show(
                    this,
                    confirmation,
                    $"{ServiceActionLabel(action)} Linux API service",
                    MessageBoxButton.YesNo,
                    action == LinuxApiServiceAction.Stop
                        ? MessageBoxImage.Warning
                        : MessageBoxImage.Question) != MessageBoxResult.Yes)
        {
            return;
        }

        _controlSurfaceServiceActionInFlight = true;
        _controlSurfaceRestartInFlight =
            action == LinuxApiServiceAction.Restart;
        LinuxApiServiceStatusText.Text = ServiceActionPresentParticiple(action);
        LinuxApiServiceStatusText.Foreground =
            new SolidColorBrush(Color.FromRgb(241, 191, 91));
        LinuxApiServiceStatusText.ToolTip =
            $"{ServiceActionLabel(action)} command is in progress over SSH.";
        UpdateControlSurfaceServiceControls();
        UpdateControlSurfaceCompatibility();
        var commandCompleted = false;
        try
        {
            using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(40));
            var snapshot = await _tunnelHost.SendAsync(
                new TunnelHostRequest
                {
                    Command = TunnelHostCommand.ChangeLinuxApiService,
                    Configuration = BuildTunnelHostConfiguration(),
                    ServiceAction = action,
                },
                cancellation.Token);
            RenderTunnelHostSnapshot(snapshot);
            commandCompleted = true;
            await RefreshControlSurfaceServiceStatusAsync(force: true);
            if (action == LinuxApiServiceAction.Stop)
            {
                _serverCompatibility = null;
                UpdateControlSurfaceCompatibility();
                SetHttpConnectionStatus(
                    "Unavailable — service stopped",
                    new SolidColorBrush(Color.FromRgb(241, 191, 91)));
                SaveSettings();
                return;
            }

            LinuxServiceCompatibilityText.Text =
                $"Linux service {ServiceActionPastTense(action)}; waiting for a compatible API...";
            CompatibilityBannerText.Text =
                $"Linux API service {ServiceActionPastTense(action)}; waiting for revision and capability "
                + "verification before enabling automation Start.";
            RenderStatus(await WaitForCompatibleServerAsync(cancellation.Token));
            SetHttpConnectionStatus(
                "Connected",
                new SolidColorBrush(Color.FromRgb(73, 214, 157)));
            SaveSettings();
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            if (exc is TunnelHostCommandException { Snapshot: not null } hostError)
            {
                RenderTunnelHostSnapshot(hostError.Snapshot);
            }
            if (commandCompleted)
            {
                await RefreshControlSurfaceServiceStatusAsync(force: true);
            }
            else
            {
                SetUnknownControlSurfaceServiceState(exc.Message);
            }
            LastErrorText.Text = exc.Message;
            ShowError(exc);
        }
        finally
        {
            _controlSurfaceServiceActionInFlight = false;
            _controlSurfaceRestartInFlight = false;
            UpdateControlSurfaceServiceControls();
            UpdateControlSurfaceCompatibility();
        }
    }

    private static string ServiceActionLabel(LinuxApiServiceAction action) =>
        action switch
        {
            LinuxApiServiceAction.Start => "Start",
            LinuxApiServiceAction.Stop => "Stop",
            LinuxApiServiceAction.Restart => "Restart",
            _ => throw new ArgumentOutOfRangeException(nameof(action)),
        };

    private static string ServiceActionPresentParticiple(
        LinuxApiServiceAction action) =>
        action switch
        {
            LinuxApiServiceAction.Start => "Starting",
            LinuxApiServiceAction.Stop => "Stopping",
            LinuxApiServiceAction.Restart => "Restarting",
            _ => throw new ArgumentOutOfRangeException(nameof(action)),
        };

    private static string ServiceActionPastTense(LinuxApiServiceAction action) =>
        action switch
        {
            LinuxApiServiceAction.Start => "started",
            LinuxApiServiceAction.Restart => "restarted",
            _ => throw new ArgumentOutOfRangeException(nameof(action)),
        };

    private async Task<StatusResponse> WaitForCompatibleServerAsync(
        CancellationToken cancellationToken)
    {
        Exception? lastFailure = null;
        for (var attempt = 0; attempt < 20; attempt++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try
            {
                var status = await _api.GetStatusAsync(cancellationToken);
                var compatibility = ControlSurfaceCompatibility.Evaluate(status);
                RenderStatus(status);
                if (compatibility.IsCompatible)
                {
                    return status;
                }
                lastFailure = new InvalidOperationException(
                    $"The restarted Linux service is still incompatible: "
                    + DescribeCompatibilityProblems(compatibility));
            }
            catch (Exception exc) when (exc is not OperationCanceledException)
            {
                lastFailure = exc;
            }
            await Task.Delay(TimeSpan.FromMilliseconds(750), cancellationToken);
        }
        throw new InvalidOperationException(
            "The Linux control service did not return with an API compatible with this client.",
            lastFailure);
    }

    private async Task RefreshControlSurfaceServiceStatusAsync(bool force = false)
    {
        bool entered;
        if (force)
        {
            _serviceStatusCancellation?.Cancel();
            await _serviceStatusGate.WaitAsync();
            entered = true;
        }
        else
        {
            entered = await _serviceStatusGate.WaitAsync(0);
        }
        if (!entered)
        {
            return;
        }

        _serviceStatusCancellation?.Dispose();
        _serviceStatusCancellation =
            new CancellationTokenSource(TimeSpan.FromSeconds(12));
        try
        {
            var destination = _settings.SshDestination.Trim();
            if (!TunnelHostConfigurationValidator.IsValidDestination(destination))
            {
                _controlSurfaceServiceState = null;
                LinuxApiServiceStatusText.Text = "SSH destination needed";
                LinuxApiServiceStatusText.Foreground =
                    new SolidColorBrush(Color.FromRgb(241, 191, 91));
                LinuxApiServiceStatusText.ToolTip =
                    "Enter a valid Linux SSH destination in Preferences.";
                return;
            }
            var snapshot = await _tunnelHost.SendAsync(
                new TunnelHostRequest
                {
                    Command = TunnelHostCommand.QueryLinuxApiService,
                    Configuration = BuildTunnelHostConfiguration(),
                },
                _serviceStatusCancellation.Token);
            RenderTunnelHostSnapshot(snapshot);
            RenderControlSurfaceServiceState(snapshot.LinuxApiService);
        }
        catch (OperationCanceledException)
        {
            if (!force)
            {
                SetUnknownControlSurfaceServiceState(
                    "Service status query timed out.");
            }
        }
        catch (Exception exc)
        {
            if (exc is TunnelHostCommandException { Snapshot: not null } hostError)
            {
                RenderTunnelHostSnapshot(hostError.Snapshot);
            }
            SetUnknownControlSurfaceServiceState(exc.Message);
        }
        finally
        {
            UpdateControlSurfaceServiceControls();
            _serviceStatusGate.Release();
        }
    }

    private void RenderControlSurfaceServiceState(LinuxApiServiceSnapshot state)
    {
        _controlSurfaceServiceState = state;
        var (label, color) = state.ActiveState switch
        {
            "active" => ("Running", Color.FromRgb(73, 214, 157)),
            "activating" => ("Starting", Color.FromRgb(241, 191, 91)),
            "deactivating" => ("Stopping", Color.FromRgb(241, 191, 91)),
            "failed" => ("Failed", Color.FromRgb(255, 113, 135)),
            "inactive" => ("Stopped", Color.FromRgb(147, 164, 187)),
            _ => ($"{state.ActiveState}/{state.SubState}", Color.FromRgb(241, 191, 91)),
        };
        LinuxApiServiceStatusText.Text = label;
        LinuxApiServiceStatusText.Foreground = new SolidColorBrush(color);
        LinuxApiServiceStatusText.ToolTip =
            $"thetower-control-surface.service: {state.LoadState}/"
            + $"{state.ActiveState}/{state.SubState}; result={state.Result}; "
            + $"exit={state.ExecMainStatus?.ToString(CultureInfo.InvariantCulture) ?? "unknown"}.";
    }

    private void SetUnknownControlSurfaceServiceState(string detail)
    {
        _controlSurfaceServiceState = null;
        LinuxApiServiceStatusText.Text = "Unknown — SSH unavailable";
        LinuxApiServiceStatusText.Foreground =
            new SolidColorBrush(Color.FromRgb(255, 113, 135));
        LinuxApiServiceStatusText.ToolTip = detail;
    }

    private void SetUnqueriedControlSurfaceServiceState(string detail)
    {
        _controlSurfaceServiceState = null;
        LinuxApiServiceStatusText.Text = "Unknown — not queried";
        LinuxApiServiceStatusText.Foreground =
            new SolidColorBrush(Color.FromRgb(241, 191, 91));
        LinuxApiServiceStatusText.ToolTip = detail;
    }

    private void UpdateControlSurfaceServiceControls()
    {
        var destinationValid = TunnelHostConfigurationValidator.IsValidDestination(
            _settings.SshDestination);
        var hostAvailable = _tunnelHostSnapshot is not null
            && _tunnelHostProtocolMismatch is null;
        var stableState = _controlSurfaceServiceState is not null
            && _controlSurfaceServiceState.ActiveState is "active" or "inactive" or "failed";
        ToggleControlSurfaceServiceButton.IsEnabled = destinationValid
            && hostAvailable
            && stableState
            && !_controlSurfaceServiceActionInFlight;
        ToggleControlSurfaceServiceButton.Content =
            _controlSurfaceServiceState?.IsActive == true
                ? "Stop API"
                : "Start API";
        ToggleControlSurfaceServiceButton.ToolTip = stableState
            ? _controlSurfaceServiceState?.IsActive == true
                ? "Stop only the fixed Linux control API service; automation and SSH tunnels are unchanged."
                : "Start only the fixed Linux control API service; automation and SSH tunnels are unchanged."
            : "Wait for a Linux API service-status query over SSH.";
        RestartControlSurfaceTopButton.IsEnabled = destinationValid
            && hostAvailable
            && !_controlSurfaceServiceActionInFlight;
        RestartControlSurfaceTopButton.ToolTip = destinationValid
            ? "Restart only the fixed Linux control API service; automation and SSH tunnels are unchanged."
            : "Enter a valid Linux SSH destination in Preferences.";
    }

    private async void StartTunnel_Click(object sender, RoutedEventArgs e)
    {
        await RunApiTunnelStartAsync();
    }

    private async Task RunApiTunnelStartAsync()
    {
        if (_apiTunnelActionInFlight)
        {
            return;
        }
        _apiTunnelActionInFlight = true;
        UpdateRestartSshControls();
        try
        {
            await StartApiTunnelCoreAsync();
        }
        finally
        {
            _apiTunnelActionInFlight = false;
            if (_tunnelHostSnapshot is not null)
            {
                RenderApiTunnelState(_tunnelHostSnapshot.ApiTunnel);
            }
            UpdateRestartSshControls();
        }
    }

    private async Task StartApiTunnelCoreAsync()
    {
        try
        {
            var configuration = BuildTunnelHostConfiguration();
            TunnelStatusText.Text = "Starting Windows OpenSSH API tunnel...";
            SetApiTunnelTopStatus(
                "Starting",
                TunnelStatusText.Text,
                new SolidColorBrush(Color.FromRgb(241, 191, 91)));
            StartTunnelButton.IsEnabled = false;
            var snapshot = await _tunnelHost.SendAsync(
                new TunnelHostRequest
                {
                    Command = TunnelHostCommand.StartTunnel,
                    Tunnel = TunnelKind.Api,
                    Configuration = configuration,
                },
                CancellationToken.None);
            RenderTunnelHostSnapshot(snapshot);

            _settings.BaseUrl =
                $"http://127.0.0.1:{configuration.LocalApiPort}";
            _api.Configure(_settings.BaseUrl, _apiToken);
            RenderConnectionPreferences();

            using var probeCancellation = new CancellationTokenSource(TimeSpan.FromSeconds(12));
            var status = await _api.GetStatusAsync(probeCancellation.Token);
            RenderStatus(status);
            SaveSettings();
            SetHttpConnectionStatus(
                "Connected",
                new SolidColorBrush(Color.FromRgb(73, 214, 157)));
            await RefreshControlSurfaceServiceStatusAsync(force: true);
            await Task.WhenAll(
                RefreshBattlesAsync(force: true),
                RefreshActivityAsync(force: true));
        }
        catch (Exception exc)
        {
            if (exc is TunnelHostCommandException { Snapshot: not null } hostError)
            {
                RenderTunnelHostSnapshot(hostError.Snapshot);
            }
            var tunnelRunning = _tunnelHostSnapshot?.ApiTunnel.ObservedState
                == TunnelObservedState.Running;
            TunnelStatusText.Text = tunnelRunning
                ? $"Tunnel running, but the Linux API is unavailable: {exc.Message}"
                : exc.Message;
            TunnelStatusText.Foreground = new SolidColorBrush(Color.FromRgb(255, 113, 135));
            SetApiTunnelTopStatus(
                tunnelRunning ? "Active" : "Start failed",
                TunnelStatusText.Text,
                tunnelRunning
                    ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
                    : new SolidColorBrush(Color.FromRgb(255, 113, 135)));
            SetHttpConnectionStatus(
                "Unavailable",
                new SolidColorBrush(Color.FromRgb(255, 113, 135)));
            ShowError(exc);
        }
    }

    private async void StopTunnel_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            StopTunnelButton.IsEnabled = false;
            RenderTunnelHostSnapshot(await _tunnelHost.SendAsync(
                new TunnelHostRequest
                {
                    Command = TunnelHostCommand.StopTunnel,
                    Tunnel = TunnelKind.Api,
                },
                CancellationToken.None));
            SetHttpConnectionStatus(
                "Unavailable — tunnel stopped",
                new SolidColorBrush(Color.FromRgb(241, 191, 91)));
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
        finally
        {
            UpdateRestartSshControls();
        }
    }

    private void RestartSshMenu_Click(object sender, RoutedEventArgs e)
    {
        UpdateRestartSshControls();
        RestartSshMenu.PlacementTarget = RestartSshButton;
        RestartSshMenu.Placement =
            System.Windows.Controls.Primitives.PlacementMode.Bottom;
        RestartSshMenu.IsOpen = true;
    }

    private async void RestartApiTunnel_Click(object sender, RoutedEventArgs e)
    {
        if (_apiTunnelActionInFlight)
        {
            return;
        }
        _apiTunnelActionInFlight = true;
        UpdateRestartSshControls();
        SetApiTunnelTopStatus(
            "Restarting",
            "Restarting only the Windows-local API SSH tunnel.",
            new SolidColorBrush(Color.FromRgb(241, 191, 91)));
        try
        {
            StartTunnelButton.IsEnabled = false;
            StopTunnelButton.IsEnabled = false;
            var configuration = BuildTunnelHostConfiguration();
            var snapshot = await _tunnelHost.SendAsync(
                new TunnelHostRequest
                {
                    Command = TunnelHostCommand.RestartTunnel,
                    Tunnel = TunnelKind.Api,
                    Configuration = configuration,
                },
                CancellationToken.None);
            RenderTunnelHostSnapshot(snapshot);
            _settings.BaseUrl =
                $"http://127.0.0.1:{configuration.LocalApiPort}";
            _api.Configure(_settings.BaseUrl, _apiToken);
            RenderConnectionPreferences();
            SaveSettings();
            await RefreshStatusAsync(force: true);
        }
        catch (Exception exc)
        {
            if (exc is TunnelHostCommandException { Snapshot: not null } hostError)
            {
                RenderTunnelHostSnapshot(hostError.Snapshot);
            }
            ShowError(exc);
        }
        finally
        {
            _apiTunnelActionInFlight = false;
            if (_tunnelHostSnapshot is not null)
            {
                RenderApiTunnelState(_tunnelHostSnapshot.ApiTunnel);
            }
            UpdateRestartSshControls();
        }
    }

    private async void RestartAdbTunnel_Click(object sender, RoutedEventArgs e)
    {
        if (_adbTunnelRestartInFlight || _adbForwardStarting)
        {
            return;
        }
        _adbTunnelRestartInFlight = true;
        UpdateRestartSshControls();
        SetAdbTunnelTopStatus(
            "Restarting",
            "Restarting only the ADB reverse-forward SSH tunnel.",
            new SolidColorBrush(Color.FromRgb(241, 191, 91)));
        try
        {
            StartAdbForwardButton.IsEnabled = false;
            StopAdbForwardButton.IsEnabled = false;
            var configuration = BuildTunnelHostConfiguration();
            RenderTunnelHostSnapshot(await _tunnelHost.SendAsync(
                new TunnelHostRequest
                {
                    Command = TunnelHostCommand.RestartTunnel,
                    Tunnel = TunnelKind.Adb,
                    Configuration = configuration,
                },
                CancellationToken.None));
        }
        catch (Exception exc)
        {
            if (exc is TunnelHostCommandException { Snapshot: not null } hostError)
            {
                RenderTunnelHostSnapshot(hostError.Snapshot);
            }
            ShowError(exc);
        }
        finally
        {
            _adbTunnelRestartInFlight = false;
            if (_tunnelHostSnapshot is not null)
            {
                RenderAdbTunnelState(_tunnelHostSnapshot.AdbTunnel);
            }
            UpdateRestartSshControls();
        }
    }

    private void SetApiTunnelTopStatus(string summary, string detail, Brush foreground)
    {
        ApiTunnelTopStatusText.Text = summary;
        ApiTunnelTopStatusText.Foreground = foreground;
        ApiTunnelTopStatusText.ToolTip = detail;
    }

    private void SetHttpConnectionStatus(
        string summary,
        Brush foreground,
        string? detail = null)
    {
        ConnectionText.Text = summary;
        ConnectionText.Foreground = foreground;
        ConnectionText.ToolTip = detail ?? summary;
    }

    private void SetAdbTunnelTopStatus(string summary, string detail, Brush foreground)
    {
        AdbTunnelTopStatusText.Text = summary;
        AdbTunnelTopStatusText.Foreground = foreground;
        AdbTunnelTopStatusText.ToolTip = detail;
    }

    private void UpdateRestartSshControls()
    {
        var hostAvailable = _tunnelHostSnapshot is not null
            && _tunnelHostProtocolMismatch is null;
        RestartApiTunnelMenuItem.IsEnabled =
            hostAvailable && !_apiTunnelActionInFlight;
        RestartAdbTunnelMenuItem.IsEnabled =
            hostAvailable
            && !_adbTunnelRestartInFlight
            && !_adbForwardStarting;
        RestartSshButton.IsEnabled =
            RestartApiTunnelMenuItem.IsEnabled
            || RestartAdbTunnelMenuItem.IsEnabled;
    }

    private async void StartAdbForward_Click(object sender, RoutedEventArgs e)
    {
        await StartAdbForwardAsync();
    }

    private async Task StartAdbForwardAsync()
    {
        if (_adbForwardStarting)
        {
            return;
        }

        _adbForwardStarting = true;
        StartAdbForwardButton.IsEnabled = false;
        StopAdbForwardButton.IsEnabled = false;
        try
        {
            var configuration = BuildTunnelHostConfiguration();
            var linuxPort = configuration.LinuxAdbPort;
            RefreshWindowsAdbListenerStatus();
            AdbForwardStatusText.Text =
                $"Starting reverse forward on Linux loopback port {linuxPort}...";
            AdbForwardStatusText.Foreground =
                new SolidColorBrush(Color.FromRgb(241, 191, 91));
            SetAdbTunnelTopStatus(
                "Starting",
                AdbForwardStatusText.Text,
                AdbForwardStatusText.Foreground);

            RenderTunnelHostSnapshot(await _tunnelHost.SendAsync(
                new TunnelHostRequest
                {
                    Command = TunnelHostCommand.StartTunnel,
                    Tunnel = TunnelKind.Adb,
                    Configuration = configuration,
                },
                CancellationToken.None));
        }
        catch (ArgumentException exc)
        {
            StartAdbForwardButton.IsEnabled = true;
            StopAdbForwardButton.IsEnabled = false;
            AdbForwardStatusText.Text = exc.Message;
            AdbForwardStatusText.Foreground =
                new SolidColorBrush(Color.FromRgb(255, 113, 135));
            SetAdbTunnelTopStatus(
                "Invalid settings",
                AdbForwardStatusText.Text,
                AdbForwardStatusText.Foreground);
            LastErrorText.Text = exc.Message;
            ShowError(exc);
        }
        catch (Exception exc)
        {
            if (exc is TunnelHostCommandException { Snapshot: not null } hostError)
            {
                RenderTunnelHostSnapshot(hostError.Snapshot);
            }
            LastErrorText.Text = exc.Message;
            ShowError(exc);
        }
        finally
        {
            _adbForwardStarting = false;
            if (_tunnelHostSnapshot is not null)
            {
                RenderAdbTunnelState(_tunnelHostSnapshot.AdbTunnel);
            }
            UpdateRestartSshControls();
        }
    }

    private async void StopAdbForward_Click(object sender, RoutedEventArgs e)
    {
        StopAdbForwardButton.IsEnabled = false;
        try
        {
            RenderTunnelHostSnapshot(await _tunnelHost.SendAsync(
                new TunnelHostRequest
                {
                    Command = TunnelHostCommand.StopTunnel,
                    Tunnel = TunnelKind.Adb,
                },
                CancellationToken.None));
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
        finally
        {
            UpdateRestartSshControls();
        }
    }

    private void RenderApiTunnelState(TunnelStateSnapshot state)
    {
        var (summary, color) = TunnelStatePresentation(state);
        var detail = TunnelStateDetail(state);
        TunnelStatusText.Text = detail;
        TunnelStatusText.Foreground = color;
        TunnelStatusText.ToolTip = state.LastDiagnostic?.RawDetail;
        SetApiTunnelTopStatus(summary, detail, color);
        var canRetry = state.ObservedState is
            TunnelObservedState.Conflict or TunnelObservedState.Faulted;
        StartTunnelButton.Content = canRetry
            ? "Retry API tunnel"
            : "Start API tunnel";
        StartTunnelButton.IsEnabled = !_apiTunnelActionInFlight
            && _tunnelHostProtocolMismatch is null
            && (!state.Desired || canRetry);
        StopTunnelButton.IsEnabled = !_apiTunnelActionInFlight && state.Desired;
    }

    private void RenderAdbTunnelState(TunnelStateSnapshot state)
    {
        var (summary, color) = TunnelStatePresentation(state);
        var detail = TunnelStateDetail(state) + " The API tunnel is unchanged.";
        AdbForwardStatusText.Text = detail;
        AdbForwardStatusText.Foreground = color;
        AdbForwardStatusText.ToolTip = state.LastDiagnostic?.RawDetail;
        SetAdbTunnelTopStatus(summary, detail, color);
        var canRetry = state.ObservedState is
            TunnelObservedState.Conflict or TunnelObservedState.Faulted;
        StartAdbForwardButton.Content = canRetry
            ? "Retry ADB forward"
            : "Start ADB forward";
        StartAdbForwardButton.IsEnabled = !_adbForwardStarting
            && !_adbTunnelRestartInFlight
            && _tunnelHostProtocolMismatch is null
            && (!state.Desired || canRetry);
        StopAdbForwardButton.IsEnabled = !_adbForwardStarting
            && !_adbTunnelRestartInFlight
            && state.Desired;
    }

    private (string Summary, Brush Color) TunnelStatePresentation(
        TunnelStateSnapshot state)
    {
        var muted = (Brush)FindResource("MutedBrush");
        return state.ObservedState switch
        {
            TunnelObservedState.Stopped => ("Stopped", muted),
            TunnelObservedState.Starting => (
                "Starting",
                new SolidColorBrush(Color.FromRgb(241, 191, 91))),
            TunnelObservedState.Running => (
                "Active",
                new SolidColorBrush(Color.FromRgb(73, 214, 157))),
            TunnelObservedState.Stopping => (
                "Stopping",
                new SolidColorBrush(Color.FromRgb(241, 191, 91))),
            TunnelObservedState.RetryWaiting => (
                RetrySummary(state),
                new SolidColorBrush(Color.FromRgb(241, 191, 91))),
            TunnelObservedState.Conflict => (
                "Conflict",
                new SolidColorBrush(Color.FromRgb(255, 113, 135))),
            TunnelObservedState.Faulted => (
                "Faulted",
                new SolidColorBrush(Color.FromRgb(255, 113, 135))),
            _ => (state.ObservedState.ToString(), muted),
        };
    }

    private static string RetrySummary(TunnelStateSnapshot state)
    {
        var seconds = state.RetryAt is null
            ? 0
            : Math.Max(
                0,
                (int)Math.Ceiling(
                    (state.RetryAt.Value - DateTimeOffset.UtcNow).TotalSeconds));
        return $"Retry in {seconds}s";
    }

    private static string TunnelStateDetail(TunnelStateSnapshot state)
    {
        var endpoint = state.ActiveEndpoint?.Display ?? "No active endpoint.";
        var pid = state.ProcessId is null ? "no SSH PID" : $"SSH PID {state.ProcessId}";
        var diagnostic = state.LastDiagnostic is null
            ? ""
            : $" Last SSH diagnostic: {state.LastDiagnostic.Summary}";
        return state.ObservedState switch
        {
            TunnelObservedState.Stopped =>
                $"Stopped; desired state is off. {diagnostic}".Trim(),
            TunnelObservedState.Running =>
                $"Desired and active: {endpoint} ({pid}).{diagnostic}",
            TunnelObservedState.RetryWaiting =>
                $"Desired but disconnected; {RetrySummary(state).ToLowerInvariant()} "
                + $"(attempt {state.RetryAttempt}). {endpoint}.{diagnostic}",
            TunnelObservedState.Conflict =>
                "Desired, but retry is paused for a bind or SSH-policy conflict. "
                + $"{endpoint}.{diagnostic}",
            TunnelObservedState.Faulted =>
                $"Desired but faulted. {endpoint}.{diagnostic}",
            _ =>
                $"{state.ObservedState}: desired={state.Desired}. "
                + $"{endpoint} ({pid}).{diagnostic}",
        };
    }

    private void RefreshWindowsAdbListenerStatus()
    {
        var port = _settings.WindowsBlueStacksAdbPort;
        if (port is < 1 or > 65535)
        {
            WindowsAdbListenerStatusText.Text =
                "Enter a Windows BlueStacks ADB port between 1 and 65535.";
            WindowsAdbListenerStatusText.Foreground =
                new SolidColorBrush(Color.FromRgb(255, 113, 135));
            return;
        }

        try
        {
            var listening = IsWindowsLoopbackPortListening(port);
            WindowsAdbListenerStatusText.Text = listening
                ? $"Windows ADB listener detected for 127.0.0.1:{port}."
                : $"No Windows TCP listener detected for 127.0.0.1:{port}; "
                    + "the reverse forward can stay active, but ADB connections "
                    + "will fail until BlueStacks listens.";
            WindowsAdbListenerStatusText.Foreground = listening
                ? new SolidColorBrush(Color.FromRgb(73, 214, 157))
                : new SolidColorBrush(Color.FromRgb(241, 191, 91));
        }
        catch (Exception exc)
        {
            WindowsAdbListenerStatusText.Text =
                $"Unable to inspect the Windows listener: {exc.Message}";
            WindowsAdbListenerStatusText.Foreground =
                new SolidColorBrush(Color.FromRgb(255, 113, 135));
        }
    }

    private static bool IsWindowsLoopbackPortListening(int port) =>
        IPGlobalProperties
            .GetIPGlobalProperties()
            .GetActiveTcpListeners()
            .Any(endpoint =>
                endpoint.Port == port
                && (endpoint.Address.Equals(IPAddress.Loopback)
                    || endpoint.Address.Equals(IPAddress.Any)));

    private async void Control_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string tag)
        {
            return;
        }
        var enteredControlGate = false;
        var enteredRefreshGate = false;
        var reconcileAfterFailure = false;
        Exception? requestFailure = null;
        var requestSucceeded = false;
        try
        {
            // Control POSTs are serialized independently from status GETs.
            // Cancel an older GET and transmit the mutation immediately; only
            // response rendering waits for that GET to drain, so stale status
            // cannot overwrite the authoritative POST response.
            await _controlMutationGate.WaitAsync();
            enteredControlGate = true;
            _refreshCancellation?.Cancel();
            StatusResponse response;
            if (tag.StartsWith("pause:", StringComparison.Ordinal))
            {
                var minutes = int.Parse(tag.Split(':')[1], CultureInfo.InvariantCulture);
                response = await _api.PostControlAsync(
                    new { action = "pause", minutes },
                    CancellationToken.None);
            }
            else
            {
                response = string.Equals(
                    tag,
                    "take_manual_control",
                    StringComparison.Ordinal)
                    ? await _api.PostControlAsync(
                        new
                        {
                            action = tag,
                            manual_surrender_collection =
                                (ManualSurrenderCollectionBox.SelectedItem as ComboBoxItem)
                                    ?.Tag?.ToString()
                                ?? "minimal",
                        },
                        CancellationToken.None)
                    : await _api.PostControlAsync(
                        new { action = tag },
                        CancellationToken.None);
            }
            await _refreshGate.WaitAsync();
            enteredRefreshGate = true;
            RenderStatus(response);
            requestSucceeded = true;
        }
        catch (Exception exc)
        {
            // The server may have durably committed the request even when its
            // response was lost.  Reconcile immediately instead of leaving a
            // modal error as the only visible outcome until the next poll.
            requestFailure = exc;
            reconcileAfterFailure = true;
        }
        finally
        {
            if (enteredRefreshGate)
            {
                _refreshGate.Release();
            }
        }
        if (reconcileAfterFailure)
        {
            await RefreshStatusAsync(force: true);
        }
        if (enteredControlGate)
        {
            _controlMutationGate.Release();
        }
        if (requestFailure is not null)
        {
            ShowError(requestFailure);
            return;
        }
        if (requestSucceeded)
        {
            await RefreshActivityAsync(force: true);
        }
    }

    private async void Mode_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string mode)
        {
            return;
        }
        var enteredControlGate = false;
        var enteredRefreshGate = false;
        Exception? requestFailure = null;
        var requestSucceeded = false;
        try
        {
            await _controlMutationGate.WaitAsync();
            enteredControlGate = true;
            _refreshCancellation?.Cancel();
            var response = await _api.PostControlAsync(
                new { action = "terminal_policy", policy = mode },
                CancellationToken.None);
            await _refreshGate.WaitAsync();
            enteredRefreshGate = true;
            RenderStatus(response);
            requestSucceeded = true;
        }
        catch (Exception exc)
        {
            requestFailure = exc;
        }
        finally
        {
            if (enteredRefreshGate)
            {
                _refreshGate.Release();
            }
        }
        if (requestFailure is not null)
        {
            await RefreshStatusAsync(force: true);
        }
        if (enteredControlGate)
        {
            _controlMutationGate.Release();
        }
        if (requestFailure is not null)
        {
            ShowError(requestFailure);
            return;
        }
        if (requestSucceeded)
        {
            await RefreshActivityAsync(force: true);
        }
    }

    private async void GameSpeedTargetBox_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_updatingGameSpeedTargetSelection
            || _gameSpeedTargetRequestInFlight
            || GameSpeedTargetBox.SelectedItem is not ComboBoxItem item
            || !double.TryParse(
                item.Tag?.ToString(),
                NumberStyles.Float,
                CultureInfo.InvariantCulture,
                out var target))
        {
            return;
        }
        _gameSpeedTargetRequestInFlight = true;
        GameSpeedTargetBox.IsEnabled = false;
        try
        {
            var response = await _api.PostControlAsync(
                new { action = "game_speed", target },
                CancellationToken.None);
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
            await RefreshStatusAsync(force: true);
        }
        finally
        {
            _gameSpeedTargetRequestInFlight = false;
            GameSpeedTargetBox.IsEnabled = true;
        }
    }

    private async void ConfigureRun_Click(object sender, RoutedEventArgs e)
    {
        if (_startupGateContext is not { Checks.Count: > 0 } context)
        {
            return;
        }
        try
        {
            var dialog = new ConfigureRunWindow(
                context,
                _startupGateWaivers)
            {
                Owner = this,
            };
            if (dialog.ShowDialog() != true)
            {
                return;
            }
            var response = await _api.PostControlAsync(
                new
                {
                    action = "configure_run",
                    skip_checks = dialog.SelectedSkipIds,
                },
                CancellationToken.None);
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
            await RefreshStatusAsync(force: true);
        }
    }

    private async void StrategyProfiles_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var dialog = new StrategyProfilesWindow(
                _api,
                UsePublishedStrategyAsync)
            {
                Owner = this,
            };
            dialog.ShowDialog();
            await RefreshStatusAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
    }

    private async void SaveMappingIntegration_Click(
        object sender,
        RoutedEventArgs e)
    {
        if (!ControlSurfaceCompatibility.CanOpenSaveMappingIntegration(
            _serverCompatibility))
        {
            ShowError(new InvalidOperationException(
                "Linux API revision 35 with save_mapping_integration_v1 is required."));
            return;
        }
        try
        {
            var dialog = new SaveMappingIntegrationWindow(_api)
            {
                Owner = this,
            };
            dialog.ShowDialog();
            await RefreshStatusAsync(force: true);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
    }

    private async void GateDecision_Click(object sender, RoutedEventArgs e)
    {
        if (_currentGateDecision is not { Status: "pending" } decision)
        {
            return;
        }
        await ShowGateDecisionAsync(decision);
    }

    private async Task ShowGateDecisionAsync(GateDecisionStatus decision)
    {
        if (_gateDecisionDialogOpen)
        {
            return;
        }
        _gateDecisionDialogOpen = true;
        try
        {
            var dialog = new GateDecisionWindow(decision) { Owner = this };
            if (dialog.ShowDialog() != true || dialog.SelectedOption is null)
            {
                return;
            }
            var response = await _api.PostControlAsync(
                new
                {
                    action = "resolve_gate",
                    request_id = decision.RequestId,
                    decision_id = dialog.SelectedOption.Id,
                },
                CancellationToken.None);
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
            await RefreshStatusAsync(force: true);
        }
        finally
        {
            _gateDecisionDialogOpen = false;
        }
    }

    private async void TournamentLaunch_Click(
        object sender,
        RoutedEventArgs e)
    {
        if (_currentTournamentLaunch is not { Launch.Status: "awaiting_operator" }
            receipt)
        {
            return;
        }
        await ShowTournamentLaunchAsync(receipt);
    }

    private async Task ShowTournamentLaunchAsync(
        ExclusiveValidationReceiptStatus receipt)
    {
        if (_tournamentLaunchDialogOpen)
        {
            return;
        }
        _tournamentLaunchDialogOpen = true;
        try
        {
            var dialog = new TournamentLaunchWindow(
                receipt,
                _tournamentLaunchCanStart)
            {
                Owner = this,
            };
            if (dialog.ShowDialog() != true
                || string.IsNullOrWhiteSpace(dialog.Decision))
            {
                return;
            }
            var response = await _api.PostControlAsync(
                new
                {
                    action = "resolve_tournament_launch",
                    request_id = receipt.RequestId,
                    decision = dialog.Decision,
                },
                CancellationToken.None);
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
            await RefreshStatusAsync(force: true);
        }
        finally
        {
            _tournamentLaunchDialogOpen = false;
        }
    }

    private async void StrategySelectionBox_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_updatingStrategySelection)
        {
            return;
        }

        SetStrategyRequestFeedback("", (Brush)FindResource("MutedBrush"));
        var attempt = _strategySelection.SelectionChanged(
            CurrentStrategySelectionContext(),
            SelectedStrategy(),
            userDriven: true);
        UpdateStrategyActionAvailability();
        if (attempt is not null)
        {
            await SubmitStrategyRequestAsync(attempt, showErrorDialog: false);
        }
    }

    private async void QueueStrategy_Click(object sender, RoutedEventArgs e)
    {
        var context = CurrentStrategySelectionContext();
        var selected = SelectedStrategy();
        var attempt = context.ProcessActive
            ? _strategySelection.TryBeginRetry(context, selected)
            : _strategySelection.TryBeginStartupSave(context, selected);
        if (attempt is not null)
        {
            await SubmitStrategyRequestAsync(attempt, showErrorDialog: true);
        }
    }

    private async void AdoptStrategy_Click(object sender, RoutedEventArgs e)
    {
        var attempt = _strategySelection.TryBeginActiveAdoption(
            CurrentStrategySelectionContext(),
            SelectedStrategy());
        if (attempt is not null)
        {
            await SubmitStrategyRequestAsync(attempt, showErrorDialog: true);
        }
    }

    private async Task<StrategyRequestOutcome> SubmitStrategyRequestAsync(
        StrategyRequestAttempt attempt,
        bool showErrorDialog)
    {
        var strategy = attempt.Strategy;
        UpdateStrategyActionAvailability();
        try
        {
            var action = attempt.Kind switch
            {
                StrategyRequestKind.ActiveBattle => "active-battle adoption",
                StrategyRequestKind.StartupDefault => "next-start save",
                _ => "boundary queue",
            };
            SetStrategyRequestFeedback(
                $"Sending {StrategyDisplayName(strategy)} {action} request...",
                new SolidColorBrush(Color.FromRgb(241, 191, 91)));
            object payload = attempt.ApplyToActiveRun
                ? new { action = "set_strategy", strategy, apply_to_active_run = true }
                : new { action = "set_strategy", strategy };
            var response = await _api.PostProcessAsync(
                payload,
                CancellationToken.None);
            if (response.Request is not { Accepted: true } request)
            {
                if (!_strategySelection.CompleteFailed(
                        attempt,
                        SelectedStrategy()))
                {
                    return new StrategyRequestOutcome(
                        false,
                        "Ignored a superseded Strategy rejection.");
                }
                var reason = response.Request?.Warning
                    ?? response.Request?.Disposition
                    ?? "Linux did not confirm request acceptance.";
                var message = $"Strategy request was not accepted: {reason}";
                SetStrategyRequestFeedback(
                    message,
                    new SolidColorBrush(Color.FromRgb(255, 113, 135)));
                await RefreshAfterStrategyResponseAsync(response);
                if (showErrorDialog)
                {
                    ShowError(new InvalidOperationException(message));
                }
                return new StrategyRequestOutcome(false, message);
            }

            var requested = NormalizeStrategy(request.Strategy) ?? strategy;
            var requestedLabel = StrategyDisplayName(requested);
            var acceptedMessage = request.Disposition switch
            {
                "queued" => $"Accepted {requestedLabel}; queued for the next confirmed run boundary.",
                "saved" => $"Accepted {requestedLabel}; saved for the next process start.",
                "active_battle_requested" => $"Accepted {requestedLabel}; waiting for active-battle adoption.",
                _ => $"Accepted {requestedLabel} strategy request.",
            };
            if (!_strategySelection.CompleteAccepted(
                    attempt,
                    SelectedStrategy()))
            {
                return new StrategyRequestOutcome(
                    true,
                    "Linux accepted a superseded Strategy request; its stale response was ignored.");
            }
            if (!string.IsNullOrWhiteSpace(request.Warning))
            {
                acceptedMessage += $" Audit warning: {request.Warning}";
            }
            SetStrategyRequestFeedback(
                acceptedMessage,
                new SolidColorBrush(Color.FromRgb(101, 230, 166)));
            await RefreshAfterStrategyResponseAsync(response);
            return new StrategyRequestOutcome(true, acceptedMessage);
        }
        catch (Exception exc)
        {
            if (!_strategySelection.CompleteFailed(
                    attempt,
                    SelectedStrategy()))
            {
                return new StrategyRequestOutcome(
                    false,
                    "A superseded Strategy request failed after a newer request took ownership.");
            }
            var message = $"Strategy request was not accepted: {exc.Message}";
            SetStrategyRequestFeedback(
                message,
                new SolidColorBrush(Color.FromRgb(255, 113, 135)));
            if (showErrorDialog)
            {
                ShowError(exc);
            }
            await RefreshStatusAsync(force: true);
            return new StrategyRequestOutcome(false, message);
        }
        finally
        {
            var hadDeferredPublication =
                _strategySelection.HasDeferredPublication;
            var deferred = _strategySelection.TryBeginDeferredPublication(
                CurrentStrategySelectionContext());
            UpdateStrategyActionAvailability();
            if (deferred is not null)
            {
                _ = SubmitStrategyRequestAsync(deferred, showErrorDialog: false);
            }
            else if (hadDeferredPublication
                && _strategyProcessActive
                && _strategySelection.RetryAvailable(SelectedStrategy()))
            {
                SetStrategyRequestFeedback(
                    "The deferred publication request could not start because "
                        + "Strategy control is unavailable; the selection is retained for Retry.",
                    new SolidColorBrush(Color.FromRgb(241, 191, 91)));
            }
        }
    }

    private async Task RefreshAfterStrategyResponseAsync(StatusResponse response)
    {
        try
        {
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            LastErrorText.Text =
                "The Strategy response was received, but the dashboard refresh failed: "
                + exc.Message;
            await RefreshStatusAsync(force: true);
        }
    }

    private async Task<StrategyPublicationUseResult> UsePublishedStrategyAsync(
        StrategyPublicationNotice publication)
    {
        if (_strategySelection.HasHandledPublication(publication))
        {
            return new StrategyPublicationUseResult(
                true,
                "This publication notice was already handled; no duplicate selection or request was made.");
        }

        await RefreshStatusAsync(force: true);
        EnsureStrategyOption(publication.StrategyId);
        SelectStrategy(publication.StrategyId);
        var attempt = _strategySelection.Published(
            CurrentStrategySelectionContext(),
            publication);
        if (!_strategyProcessActive)
        {
            var message =
                $"Selected {StrategyDisplayName(publication.StrategyId)} version "
                + $"{publication.LogicalVersion} for Start Automation. "
                + "The saved startup default was not changed.";
            SetStrategyRequestFeedback(
                message,
                new SolidColorBrush(Color.FromRgb(101, 230, 166)));
            UpdateStrategyActionAvailability();
            return new StrategyPublicationUseResult(true, message);
        }
        if (attempt is null)
        {
            var deferred = _strategySelection.HasDeferredPublication;
            var unavailable = !_strategyLifecycleAvailable;
            if (unavailable)
            {
                _strategySelection.MarkAutomaticFailure(publication.StrategyId);
            }
            var message = deferred
                ? "Its next-boundary request will follow the Strategy request already in flight."
                : unavailable
                    ? "The automatic next-boundary request could not start because Strategy control is unavailable; the selection is retained for Retry."
                    : "This publication notice was already handled; no duplicate next-boundary request was sent.";
            SetStrategyRequestFeedback(
                message,
                deferred || unavailable
                    ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
                    : (Brush)FindResource("MutedBrush"));
            UpdateStrategyActionAvailability();
            return new StrategyPublicationUseResult(!unavailable, message);
        }

        var outcome = await SubmitStrategyRequestAsync(
            attempt,
            showErrorDialog: false);
        return new StrategyPublicationUseResult(outcome.Accepted, outcome.Message);
    }

    private void SetStrategyRequestFeedback(string message, Brush foreground)
    {
        _strategyRequestMessage = message;
        StrategySelectionText.Text = message;
        StrategySelectionText.Foreground = foreground;
        StrategySelectionText.Visibility = string.IsNullOrWhiteSpace(message)
            ? Visibility.Collapsed
            : Visibility.Visible;
    }

    private async void Process_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string tag)
        {
            return;
        }
        if (tag == "stop" && MessageBox.Show(
                this,
                "Persist STOPPED and stop the managed Linux automation service?",
                "Stop automation",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning) != MessageBoxResult.Yes)
        {
            return;
        }
        if (tag == "start"
            && _gameSpeedTarget < 6.3
            && MessageBox.Show(
                this,
                $"A custom game-speed target is active. Start automation with "
                + $"battle speed held at x{_gameSpeedTarget:F1}?",
                "Custom game speed",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning) != MessageBoxResult.Yes)
        {
            return;
        }

        try
        {
            StatusResponse response;
            if (tag == "start")
            {
                var strategy = SelectedStrategy()
                    ?? throw new InvalidOperationException(
                        "Select a strategy before starting automation.");
                response = await _api.PostProcessAsync(
                    new
                    {
                        action = "start",
                        strategy,
                    },
                    CancellationToken.None);
                _strategySelection.MarkExternalAcceptance(strategy);
                SetStrategyRequestFeedback(
                    $"Started Paused with selected {StrategyDisplayName(strategy)} strategy.",
                    new SolidColorBrush(Color.FromRgb(101, 230, 166)));
            }
            else
            {
                response = await _api.PostProcessAsync(
                    new { action = "stop" },
                    CancellationToken.None);
            }
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
            await RefreshStatusAsync(force: true);
        }
    }

    private void AdbPortBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (!IsLoaded || _updatingAdbPortDraft)
        {
            return;
        }
        _adbPortDraftDirty = !TryParsePort(
                AdbPortBox.Text,
                out var draftPort)
            || _configuredAdbPort is null
            || draftPort != _configuredAdbPort.Value;
        UpdateAdbPortDraftControls();
    }

    private void RevertAdbPortDraft_Click(object sender, RoutedEventArgs e)
    {
        var target = _configuredAdbPort ?? _requestedAdbPort;
        SetAdbPortDraftText(target);
        _adbPortDraftDirty = false;
        UpdateAdbPortDraftControls();
    }

    private async void SetAdbPort_Click(object sender, RoutedEventArgs e)
    {
        if (_adbPortRequestInFlight)
        {
            return;
        }
        _adbPortRequestInFlight = true;
        UpdateAdbPortDraftControls();
        try
        {
            var adbPort = ParsePort(AdbPortBox.Text, "ADB port");
            var response = await _api.PostProcessAsync(
                new { action = "set_adb_port", adb_port = adbPort },
                CancellationToken.None);
            _adbPortDraftDirty = false;
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
            await RefreshStatusAsync(force: true);
        }
        finally
        {
            _adbPortRequestInFlight = false;
            UpdateAdbPortDraftControls();
        }
    }

    private void SetAdbPortDraftText(int? port)
    {
        _updatingAdbPortDraft = true;
        try
        {
            AdbPortBox.Text = port?.ToString(CultureInfo.InvariantCulture) ?? "";
        }
        finally
        {
            _updatingAdbPortDraft = false;
        }
    }

    private void UpdateAdbPortDraftControls()
    {
        var valid = TryParsePort(AdbPortBox.Text, out var draftPort);
        if (_adbPortDraftDirty
            && valid
            && _configuredAdbPort == draftPort)
        {
            _adbPortDraftDirty = false;
        }
        var applyEligible = _adbLifecycleAvailable
            && (!_adbProcessActive || _adbPausedAndAcknowledged);
        SetAdbPortButton.Content = _adbProcessActive
            ? "Switch live runtime"
            : "Save for next start";
        SetAdbPortButton.IsEnabled = _adbPortDraftDirty
            && valid
            && applyEligible
            && !_adbPortRequestInFlight;
        RevertAdbPortButton.IsEnabled = _adbPortDraftDirty
            && !_adbPortRequestInFlight;

        if (_adbPortRequestInFlight)
        {
            AdbDraftStateText.Text = _adbProcessActive
                ? "Applying the validated live handoff request…"
                : "Saving the validated next-start target…";
            AdbDraftStateText.Foreground =
                new SolidColorBrush(Color.FromRgb(241, 191, 91));
            return;
        }
        if (!valid)
        {
            AdbDraftStateText.Text = _adbPortDraftDirty
                ? "Draft retained locally; enter a TCP port from 1 through 65535."
                : "Waiting for a configured ADB target.";
            AdbDraftStateText.Foreground = _adbPortDraftDirty
                ? new SolidColorBrush(Color.FromRgb(255, 113, 135))
                : (Brush)FindResource("MutedBrush");
            return;
        }
        if (!_adbPortDraftDirty && _configuredAdbPort is null)
        {
            AdbDraftStateText.Text =
                "Configured target is unavailable; this local value has not been changed.";
            AdbDraftStateText.Foreground = (Brush)FindResource("MutedBrush");
            return;
        }
        if (!_adbPortDraftDirty)
        {
            AdbDraftStateText.Text =
                "Local draft matches the configured next-start target.";
            AdbDraftStateText.Foreground = (Brush)FindResource("MutedBrush");
            return;
        }
        if (!_adbLifecycleAvailable)
        {
            AdbDraftStateText.Text =
                "Draft retained locally; process lifecycle control is unavailable.";
        }
        else if (_adbProcessActive && !_adbPausedAndAcknowledged)
        {
            AdbDraftStateText.Text =
                "Draft retained locally; indefinitely Pause and wait for runtime acknowledgement before applying it.";
        }
        else
        {
            AdbDraftStateText.Text = _adbProcessActive
                ? "Unsaved local draft; Switch live runtime performs the guarded handoff and keeps automation Paused."
                : "Unsaved local draft; Save for next start records it without starting automation.";
        }
        AdbDraftStateText.Foreground =
            new SolidColorBrush(Color.FromRgb(241, 191, 91));
    }

    private async Task RefreshStatusAsync(bool force = false)
    {
        if (!force
            && _controlSurfaceServiceState is { IsActive: false } serviceState
            && serviceState.ActiveState is "inactive" or "failed")
        {
            SetHttpConnectionStatus(
                serviceState.ActiveState == "failed"
                    ? "Unavailable — service failed"
                    : "Unavailable — service stopped",
                new SolidColorBrush(
                    serviceState.ActiveState == "failed"
                        ? Color.FromRgb(255, 113, 135)
                        : Color.FromRgb(241, 191, 91)));
            return;
        }
        bool entered;
        if (force)
        {
            _refreshCancellation?.Cancel();
            await _refreshGate.WaitAsync();
            entered = true;
        }
        else
        {
            entered = await _refreshGate.WaitAsync(0);
        }
        if (!entered)
        {
            return;
        }
        _refreshCancellation?.Dispose();
        _refreshCancellation = new CancellationTokenSource(TimeSpan.FromSeconds(14));
        var cancellationToken = _refreshCancellation.Token;
        try
        {
            RenderStatus(await _api.GetStatusAsync(cancellationToken));
            SetHttpConnectionStatus(
                "Connected",
                new SolidColorBrush(Color.FromRgb(73, 214, 157)));
        }
        catch (OperationCanceledException)
        {
            if (!force)
            {
                SetHttpConnectionStatus(
                    "Timed out",
                    new SolidColorBrush(Color.FromRgb(241, 191, 91)));
            }
        }
        catch (Exception exc)
        {
            var serviceStopped = _controlSurfaceServiceState is
                { IsActive: false, ActiveState: "inactive" };
            SetHttpConnectionStatus(
                serviceStopped
                    ? "Unavailable — service stopped"
                    : "Connection failed",
                new SolidColorBrush(
                    serviceStopped
                        ? Color.FromRgb(241, 191, 91)
                        : Color.FromRgb(255, 113, 135)),
                exc.Message);
            LastErrorText.Text = exc.Message;
        }
        finally
        {
            _refreshGate.Release();
        }
    }

    private async Task RefreshBattlesAsync(bool force = false)
    {
        bool entered;
        if (force)
        {
            _battleRefreshCancellation?.Cancel();
            await _battleRefreshGate.WaitAsync();
            entered = true;
        }
        else
        {
            entered = await _battleRefreshGate.WaitAsync(0);
        }
        if (!entered)
        {
            return;
        }

        _battleRefreshCancellation?.Dispose();
        _battleRefreshCancellation = new CancellationTokenSource(TimeSpan.FromSeconds(14));
        try
        {
            RenderBattles(await _api.GetBattlesAsync(_battleRefreshCancellation.Token));
        }
        catch (OperationCanceledException)
        {
            // A later forced refresh owns the completed-battle view.
        }
        catch (Exception exc)
        {
            LatestBattleTitleText.Text = "Completed battles unavailable";
            LatestBattleCompactText.Text = "Completed battles unavailable";
            LatestBattleCompactText.ToolTip = exc.Message;
            LastErrorText.Text = $"Battle history: {exc.Message}";
        }
        finally
        {
            _battleRefreshGate.Release();
        }
    }

    private async Task RefreshActivityAsync(bool force = false)
    {
        bool entered;
        if (force)
        {
            _activityRefreshCancellation?.Cancel();
            await _activityRefreshGate.WaitAsync();
            entered = true;
        }
        else
        {
            entered = await _activityRefreshGate.WaitAsync(0);
        }
        if (!entered)
        {
            return;
        }

        _activityRefreshCancellation?.Dispose();
        _activityRefreshCancellation = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        try
        {
            var response = await _api.GetActivityAsync(
                SelectedActivityLevels(),
                SelectedActivityScope(),
                _activityClearCursor,
                _activityRefreshCancellation.Token);
            var selected = ActivityGrid.SelectedItems
                .OfType<ActivityEntry>()
                .ToList();
            var sourceChanged = _activitySourceFileId is not null
                && response.SourceFileId is not null
                && !string.Equals(
                    _activitySourceFileId,
                    response.SourceFileId,
                    StringComparison.Ordinal);
            var scopeChanged = _activityScopeId is not null
                && response.ScopeId is not null
                && !string.Equals(
                    _activityScopeId,
                    response.ScopeId,
                    StringComparison.Ordinal);
            var clearResetMessage = default(string);
            if (_activityClearCursor is not null && (sourceChanged || scopeChanged))
            {
                ResetActivityClear();
                clearResetMessage = scopeChanged
                    ? "New run started; cleared-history cutoff reset"
                    : "Activity log rotated; cleared-history cutoff reset";
            }
            var selectionStillAvailable = SelectedActivityStillAvailable(
                selected,
                response.Items);
            if (selected.Count > 0 && !sourceChanged && selectionStillAvailable)
            {
                ActivityStatusText.Text =
                    $"Selection held ({selected.Count}); copy or clear it to resume";
                return;
            }
            var selectionResetMessage = selected.Count == 0
                ? null
                : sourceChanged
                    ? "Activity log rotated; selection cleared"
                    : "Selected activity left the current log tail; selection cleared";
            if (selected.Count > 0)
            {
                CollapseExpandedActivity();
                ActivityGrid.UnselectAll();
            }
            RenderActivity(response);
            var refreshSummary = ActivityRefreshSummary(response);
            ActivityStatusText.Text = clearResetMessage
                ?? (selectionResetMessage is null
                    ? refreshSummary
                    : $"{selectionResetMessage} | {refreshSummary}");
        }
        catch (OperationCanceledException)
        {
            if (!force)
            {
                ActivityStatusText.Text = "Activity refresh timed out";
            }
        }
        catch (Exception exc)
        {
            ActivityStatusText.Text = $"Activity unavailable: {exc.Message}";
        }
        finally
        {
            _activityRefreshGate.Release();
        }
    }

    private async void ActivityLevelFilter_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e) => await RefreshActivityAsync(force: true);

    private async void ActivityScopeFilter_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        ResetActivityClear();
        await RefreshActivityAsync(force: true);
    }

    private async void RefreshActivity_Click(object sender, RoutedEventArgs e) =>
        await RefreshActivityAsync(force: true);

    private async void ClearActivity_Click(object sender, RoutedEventArgs e)
    {
        if (_activityClearCursor is null)
        {
            if (string.IsNullOrWhiteSpace(_activityEndCursor))
            {
                return;
            }
            _activityClearCursor = _activityEndCursor;
            CollapseExpandedActivity();
            ActivityGrid.UnselectAll();
            _activity.Clear();
            ClearActivityButton.Content = "Show cleared";
            ActivityStatusText.Text =
                "View cleared; new activity will continue to appear";
        }
        else
        {
            ResetActivityClear();
            ActivityStatusText.Text = "Restoring cleared activity...";
        }
        await RefreshActivityAsync(force: true);
    }

    private void ActivityGrid_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_expandedActivityEntry is not null
            && !ActivityGrid.SelectedItems.Contains(_expandedActivityEntry))
        {
            CollapseExpandedActivity();
        }
        CopyActivityButton.IsEnabled = ActivityGrid.SelectedItems.Count > 0;
    }

    private void ActivityGrid_MouseDoubleClick(
        object sender,
        MouseButtonEventArgs e)
    {
        var row = ItemsControl.ContainerFromElement(
            ActivityGrid,
            e.OriginalSource as DependencyObject) as DataGridRow;
        if (row?.Item is not ActivityEntry entry)
        {
            return;
        }

        if (ReferenceEquals(_expandedActivityEntry, entry))
        {
            CollapseExpandedActivity();
            ActivityGrid.UnselectAll();
        }
        else
        {
            CollapseExpandedActivity();
            ActivityGrid.UnselectAll();
            ActivityGrid.SelectedItem = entry;
            row.DetailsVisibility = Visibility.Visible;
            _expandedActivityEntry = entry;
            row.BringIntoView();
        }
        e.Handled = true;
    }

    private void CollapseExpandedActivity()
    {
        if (_expandedActivityEntry is not null
            && ActivityGrid.ItemContainerGenerator.ContainerFromItem(
                _expandedActivityEntry) is DataGridRow row)
        {
            row.DetailsVisibility = Visibility.Collapsed;
        }
        _expandedActivityEntry = null;
    }

    private static bool SelectedActivityStillAvailable(
        IReadOnlyCollection<ActivityEntry> selected,
        IReadOnlyCollection<ActivityEntry> available)
    {
        var unmatched = available.ToList();
        foreach (var selectedEntry in selected)
        {
            var matchIndex = unmatched.FindIndex(entry =>
                string.Equals(
                    entry.Timestamp,
                    selectedEntry.Timestamp,
                    StringComparison.Ordinal)
                && string.Equals(
                    entry.Level,
                    selectedEntry.Level,
                    StringComparison.Ordinal)
                && string.Equals(
                    entry.Message,
                    selectedEntry.Message,
                    StringComparison.Ordinal));
            if (matchIndex < 0)
            {
                return false;
            }
            unmatched.RemoveAt(matchIndex);
        }
        return true;
    }

    private void ActivityGrid_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.C || !Keyboard.Modifiers.HasFlag(ModifierKeys.Control))
        {
            return;
        }
        CopySelectedActivity();
        e.Handled = true;
    }

    private void CopyActivity_Click(object sender, RoutedEventArgs e) =>
        CopySelectedActivity();

    private void CopySelectedActivity()
    {
        var selected = ActivityGrid.SelectedItems
            .OfType<ActivityEntry>()
            .OrderBy(entry => ActivityGrid.Items.IndexOf(entry))
            .ToList();
        if (selected.Count == 0)
        {
            return;
        }

        var text = string.Join(
            Environment.NewLine,
            selected.Select(entry =>
                $"[{entry.Level} {entry.Timestamp}] {entry.Message}"));
        try
        {
            Clipboard.SetText(text);
            ActivityGrid.UnselectAll();
            ActivityStatusText.Text =
                $"Copied {selected.Count} entr{(selected.Count == 1 ? "y" : "ies")}";
        }
        catch (Exception exc)
        {
            LastErrorText.Text = $"Unable to copy activity: {exc.Message}";
        }
    }

    private IReadOnlyCollection<string> SelectedActivityLevels()
    {
        var value = (ActivityLevelFilter.SelectedItem as ComboBoxItem)?.Tag?.ToString();
        return string.IsNullOrWhiteSpace(value)
            ? Array.Empty<string>()
            : value.Split(
                ',',
                StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    }

    private string SelectedActivityScope() =>
        (ActivityScopeFilter.SelectedItem as ComboBoxItem)?.Tag?.ToString()
        ?? "current_run";

    private void ResetActivityClear()
    {
        _activityClearCursor = null;
        ClearActivityButton.Content = "Clear view";
    }

    private string ActivityRefreshSummary(ActivityResponse response)
    {
        var scopeSummary = string.Equals(
            response.Scope,
            "current_run",
            StringComparison.Ordinal)
            ? response.ScopeAvailable
                ? $"Current run since {FormatActivityScopeStart(response.ScopeStartedAt)}"
                : "No current-run boundary yet; showing all recent"
            : "All recent";
        var clearedSummary = _activityClearCursor is null ? "" : " | cleared view";
        return $"{scopeSummary} | Updated {DateTime.Now:T} | "
            + $"{_activity.Count} shown{clearedSummary}";
    }

    private static string FormatActivityScopeStart(string? value) =>
        DateTimeOffset.TryParse(value, out var parsed)
            ? parsed.LocalDateTime.ToString("t")
            : "unknown";

    private void RenderStatus(StatusResponse status)
    {
        _serverCompatibility = ControlSurfaceCompatibility.Evaluate(status);
        UpdateControlSurfaceCompatibility();
        DirectiveText.Text = FormatActionAuthority(
            status.ControlModel,
            status.Control);
        DirectiveRequestText.Text =
            $"Requested: {FormatAutomationState(status.Control)}";
        ModeText.Text = FormatExecutionMode(status.Control.Mode);
        RenderConfirmedLocalMappings(status.ConfirmedLocalMappings);
        var strategyGate = status.StrategyActionGate;
        var strategyGateVisible = strategyGate is
            { Available: true, Active: true, Stale: false };
        StrategyActionGateBanner.Visibility = strategyGateVisible
            ? Visibility.Visible
            : Visibility.Collapsed;
        if (strategyGateVisible && strategyGate is not null)
        {
            StrategyActionGateReasonText.Text = string.IsNullOrWhiteSpace(
                strategyGate.Reason)
                ? "The running-battle strategy validation did not pass."
                : strategyGate.Reason;
            StrategyActionGateChecksText.Text = "Failed checks: "
                + Join(strategyGate.FailedCheckIds.Select(FormatStatusToken));
            StrategyActionGateCollectorsText.Text = "Allowed collectors: "
                + Join(
                    strategyGate.AllowedAuxiliaryCollectors.Select(
                        FormatStatusToken));
        }
        _gameSpeedTarget = status.Control.GameSpeedTarget;
        SelectGameSpeedTarget(_gameSpeedTarget);
        TargetSpeedText.Text = _gameSpeedTarget >= 6.3
            ? "Maximum (x6.3)"
            : $"x{_gameSpeedTarget:F1}";
        var observedGameSpeed = status.Observation?.GameSpeed;
        ObservedSpeedText.Text = observedGameSpeed is double observed
            ? $"x{observed:F1}"
            : "Not observed";
        var exactSpeedReached = observedGameSpeed is double exactObserved
            && Math.Abs(exactObserved - _gameSpeedTarget) <= 0.06;
        var maximumSpeedReached = observedGameSpeed is double maximumObserved
            && (
                Math.Abs(maximumObserved - 5.0) <= 0.06
                || Math.Abs(maximumObserved - 6.3) <= 0.06
            );
        if (_gameSpeedTarget < 6.3)
        {
            GameSpeedTargetText.Text = observedGameSpeed is not double current
                ? $"Target x{_gameSpeedTarget:F1} · observed speed unavailable"
                : exactSpeedReached
                    ? $"Target x{_gameSpeedTarget:F1} · observed x{current:F1}"
                    : $"Target x{_gameSpeedTarget:F1} · observed x{current:F1} "
                        + "· correction pending";
        }
        else
        {
            GameSpeedTargetText.Text = observedGameSpeed is double current
                ? maximumSpeedReached
                    ? $"Maximum available · observed x{current:F1}"
                    : $"Maximum available · observed x{current:F1} "
                        + "· increase pending"
                : "Maximum available · observed speed unavailable";
        }
        GameSpeedTargetText.Foreground = _gameSpeedTarget < 6.3
            ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
            : (Brush)FindResource("MutedBrush");
        ObservedSpeedText.Foreground = observedGameSpeed is null
            ? (Brush)FindResource("MutedBrush")
            : exactSpeedReached
                || (_gameSpeedTarget >= 6.3 && maximumSpeedReached)
                ? new SolidColorBrush(Color.FromRgb(101, 230, 166))
                : new SolidColorBrush(Color.FromRgb(241, 191, 91));
        ObservedStateText.Text = status.ControlModel?.Observation.Available == true
            ? FormatStatusToken(status.ControlModel.Observation.GameState)
            : FormatGameScreen(status.Observation?.StateLabel);
        WaveText.Text = status.Observation?.Wave?.ToString(CultureInfo.InvariantCulture) ?? "-";
        CoinsMinuteText.Text = status.Observation?.CoinsPerMinute ?? "-";
        HeartbeatText.Text = status.Observation is null
            ? "Missing"
            : status.Observation.Stale
                ? $"Stale ({FormatAge(status.Observation.AgeSeconds)})"
                : $"Fresh ({FormatAge(status.Observation.AgeSeconds)})";
        PriorTransitionText.Text = status.PriorTransition is null
            ? "No earlier state transition in the current log tail"
            : FormatObservation(status.PriorTransition);
        RenderCurrentBattlePerks(status.CurrentBattlePerks);

        var runtime = status.Runtime.Instances.FirstOrDefault(instance => instance.Active)
            ?? status.Runtime.Instances.FirstOrDefault();
        var service = status.ProcessService;
        UpdateStrategyOptions(
            service?.StrategyOptions,
            service?.Strategy,
            status.Control.Strategy,
            status.Acknowledgements.Strategy?.Value,
            status.ControlModel?.StrategyScope.StartupDefault,
            status.ControlModel?.StrategyScope.ActiveBattle,
            status.ControlModel?.StrategyScope.PendingNextBoundary,
            status.ControlModel?.StrategyScope.PendingActiveBattle);
        _hostPerformance.UpdateServerContext(
            status.Control.AdbPort ?? service?.AdbPort,
            status.CurrentRun?.RunId,
            status.Capabilities.Contains(
                "host_performance_telemetry_v1",
                StringComparer.Ordinal)
            && status.Capabilities.Contains(
                "host_performance_gpu_v1",
                StringComparer.Ordinal)
            && status.Capabilities.Contains(
                "host_performance_process_attribution_v1",
                StringComparer.Ordinal));
        ServiceText.Text = service is null
            ? "API restart needed"
            : service.Available
                ? $"{service.ActiveState}/{service.SubState}"
                : "Unavailable";
        var processPid = service?.Active == true
            ? service.MainPid
            : runtime?.Active == true
                ? runtime.Pid
                : null;
        ProcessPidText.Text = processPid?.ToString(CultureInfo.InvariantCulture) ?? "-";
        var lifecycleAvailable = service?.Available == true;
        var processActive = service?.Active == true || status.Runtime.Active;
        var battleObservationActive = status.Observation is { Stale: false }
            && (status.ControlModel?.Observation is
                    { Available: true, GameState: "active_battle" }
                || (status.ControlModel is null
                    && string.Equals(
                        status.Observation.StateLabel,
                        "RUNNING",
                        StringComparison.OrdinalIgnoreCase)));
        WaveMetricPanel.Visibility = processActive
            && battleObservationActive
            && status.Observation?.Wave is not null
                ? Visibility.Visible
                : Visibility.Collapsed;
        CoinsMinuteMetricPanel.Visibility = processActive
            && battleObservationActive
            && !string.IsNullOrWhiteSpace(status.Observation?.CoinsPerMinute)
                ? Visibility.Visible
                : Visibility.Collapsed;
        var runElapsed = FormatRunElapsed(
            status.CurrentRun,
            status.ServerTime);
        RunElapsedText.Text = runElapsed ?? "";
        RunElapsedMetricPanel.Visibility = processActive && runElapsed is not null
            ? Visibility.Visible
            : Visibility.Collapsed;
        ProcessStateText.Text = processActive
            ? "Running"
            : lifecycleAvailable
                ? "Stopped"
                : "Unavailable";
        ProcessStateText.Foreground = processActive
            ? new SolidColorBrush(Color.FromRgb(101, 230, 166))
            : lifecycleAvailable
                ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
                : (Brush)FindResource("MutedBrush");
        StartAutomationButton.IsEnabled = lifecycleAvailable
            && !processActive
            && string.IsNullOrWhiteSpace(status.Control.Error)
            && _serverCompatibility.IsCompatible;
        var startBlocker = StartBlockerDescription(
            lifecycleAvailable,
            processActive);
        StartAutomationButton.ToolTip = startBlocker;
        CompleteStopButton.IsEnabled = lifecycleAvailable && service?.Active == true;
        var pausedAndAcknowledged = processActive
            && string.Equals(
                status.Control.State,
                "PAUSED",
                StringComparison.OrdinalIgnoreCase)
            && status.Control.RemainingSeconds is null
            && status.Acknowledgements.State?.AcknowledgesCurrent == true;
        _startupGateContext = status.Control.StartupGateContext;
        _startupGateWaivers = status.Control.StartupGateWaivers;
        var canConfigureRun = !processActive
            || string.Equals(
                status.Control.State,
                "PAUSED",
                StringComparison.OrdinalIgnoreCase);
        ConfigureRunButton.IsEnabled = canConfigureRun
            && _startupGateContext?.Checks.Count > 0;
        ConfigureRunButton.ToolTip = _startupGateContext?.Checks.Count > 0
            ? canConfigureRun
                ? "Choose one-run startup-gate skips without changing Strategy defaults."
                : "Pause automation before configuring one-run startup-gate skips."
            : "No configurable startup checks are available for the current Strategy.";
        var configuredSkips = _startupGateContext is null
            ? new List<string>()
            : _startupGateContext.Checks
                .Where(check => _startupGateWaivers.TryGetValue(check.Id, out var waiver)
                    && string.Equals(
                        waiver.Strategy,
                        _startupGateContext.Strategy,
                        StringComparison.OrdinalIgnoreCase))
                .Select(check => check.Label)
                .ToList();
        ConfigureRunText.Text = configuredSkips.Count > 0
            ? "Skip once: " + string.Join(", ", configuredSkips)
            : !canConfigureRun
                ? "Pause automation to configure one-run skips."
            : "Strategy defaults; no one-run skips staged.";
        ConfigureRunText.Visibility = configuredSkips.Count > 0
            ? Visibility.Visible
            : Visibility.Collapsed;
        _currentGateDecision = status.Control.GateDecision;
        var pendingGate = status.Control.GateDecision is
            { Status: "pending" } gate ? gate : null;
        GateDecisionButton.IsEnabled = pendingGate is not null;
        GateDecisionButton.Visibility = pendingGate is null
            ? Visibility.Collapsed
            : Visibility.Visible;
        GateDecisionText.Visibility = pendingGate is null
            ? Visibility.Collapsed
            : Visibility.Visible;
        GateDecisionText.Text = status.Control.GateDecision switch
        {
            { Status: "pending" } decision =>
                $"{decision.CheckId}: {decision.Reason}",
            { Status: "resolved" } decision =>
                $"{decision.CheckId}: {decision.DecisionId}; waiting for runtime.",
            _ => "No preflight decision is waiting for direction.",
        };
        if (pendingGate is { Blocking: true }
            && pendingGate.RequestId != _autoPromptedGateRequestId)
        {
            _autoPromptedGateRequestId = pendingGate.RequestId;
            Dispatcher.BeginInvoke(new Action(async () =>
                await ShowGateDecisionAsync(pendingGate)));
        }
        GateDecisionButton.Content = pendingGate is { Blocking: false }
            ? "Review preflight advisory"
            : "Review preflight decision";
        var adbTargetPending = processActive
            && status.Control.AdbPort is not null
            && status.Acknowledgements.AdbTarget is not
                { AcknowledgesCurrent: true };
        _configuredAdbPort = service?.AdbPort;
        _requestedAdbPort = status.Control.AdbPort;
        _activeAdbTarget = processActive && runtime?.Active == true
            ? runtime.Target
            : null;
        _adbLifecycleAvailable = lifecycleAvailable;
        _adbProcessActive = processActive;
        _adbPausedAndAcknowledged = pausedAndAcknowledged;
        ConfiguredAdbTargetText.Text = service?.AdbTarget
            ?? (_configuredAdbPort is int configuredPort
                ? $"localhost:{configuredPort}"
                : "Unavailable");
        RequestedAdbTargetText.Text = _requestedAdbPort is not int requestedPort
            ? "No separate request"
            : !processActive
                ? $"localhost:{requestedPort} · saved"
                : adbTargetPending
                    ? $"localhost:{requestedPort} · awaiting runtime"
                    : $"localhost:{requestedPort} · acknowledged";
        ActiveAdbTargetText.Text = !processActive
            ? "No active runtime"
            : string.IsNullOrWhiteSpace(_activeAdbTarget)
                ? "Awaiting runtime evidence"
                : _activeAdbTarget;
        AdbPortHelpText.Text = !processActive
            ? "Applying a valid draft changes only the configured target for the next managed start."
            : pausedAndAcknowledged
                ? "A valid draft may hand off the live runtime in place; it remains Paused and does not rerun startup gates."
                : "Indefinitely pause automation and wait for its acknowledgement before switching the live ADB port.";
        if (_adbPortDraftDirty
            && TryParsePort(AdbPortBox.Text, out var currentDraft)
            && _configuredAdbPort == currentDraft)
        {
            _adbPortDraftDirty = false;
        }
        if (!_adbPortDraftDirty && _configuredAdbPort is not null)
        {
            SetAdbPortDraftText(_configuredAdbPort);
        }
        UpdateAdbPortDraftControls();
        var statePending = processActive
            && status.Acknowledgements.State is not { AcknowledgesCurrent: true };
        var modePending = processActive
            && status.Acknowledgements.Mode is not { AcknowledgesCurrent: true };
        var gameSpeedTargetPending = processActive
            && status.Acknowledgements.GameSpeedTarget is not
                { AcknowledgesCurrent: true };
        SetSelectionStyle(
            PauseButton,
            string.Equals(status.Control.State, "PAUSED", StringComparison.OrdinalIgnoreCase),
            statePending);
        SetSelectionStyle(
            ResumeButton,
            string.Equals(status.Control.State, "RUNNING", StringComparison.OrdinalIgnoreCase),
            statePending);
        RenderBetterControlModel(status.ControlModel, processActive);
        var terminalPolicyAvailable = _serverCompatibility.IsCompatible
            && string.IsNullOrWhiteSpace(status.Control.Error);
        NextBattleModeButton.IsEnabled = terminalPolicyAvailable;
        WaitModeButton.IsEnabled = terminalPolicyAvailable;
        HomeModeButton.IsEnabled = terminalPolicyAvailable;
        SetSelectionStyle(
            NextBattleModeButton,
            string.Equals(
                status.Control.Mode,
                "NEXT_BATTLE",
                StringComparison.OrdinalIgnoreCase),
            modePending);
        SetSelectionStyle(
            WaitModeButton,
            string.Equals(status.Control.Mode, "WAIT", StringComparison.OrdinalIgnoreCase),
            modePending);
        SetSelectionStyle(
            HomeModeButton,
            string.Equals(status.Control.Mode, "HOME", StringComparison.OrdinalIgnoreCase),
            modePending);
        GameSpeedTargetBox.BorderBrush = gameSpeedTargetPending
            ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
            : (Brush)FindResource("BorderBrush");

        var strategyScope = ControlSurfaceCompatibility.ResolveStrategyScope(
            status,
            processActive,
            service?.Strategy);
        var configuredStrategy = strategyScope.StartupDefault;
        var requestedStrategy = NormalizeStrategy(status.Control.Strategy)
            ?? configuredStrategy;
        var currentStrategy = strategyScope.CurrentStrategy;
        var pendingStrategy = strategyScope.PendingStrategy;
        var pendingStrategyLabel = strategyScope.PendingLabel;
        _strategyLifecycleAvailable = lifecycleAvailable;
        _strategyProcessActive = processActive;
        _configuredStrategy = configuredStrategy;
        _currentStrategy = currentStrategy;
        _requestedStrategy = requestedStrategy;
        _pendingStrategy = pendingStrategy;
        _strategyApplyMode = status.Control.StrategyApplyMode;
        _strategyDegradedObserver = strategyScope.Degraded
            && string.Equals(
                currentStrategy,
                "none",
                StringComparison.OrdinalIgnoreCase);
        if (!_strategySelection.Dirty)
        {
            SelectStrategy(
                pendingStrategy
                ?? (processActive ? currentStrategy : configuredStrategy)
                ?? requestedStrategy);
        }
        UpdateStrategyActionAvailability();
        var selectedStrategy = SelectedStrategy();
        var configuredStrategyLabel = configuredStrategy is null
            ? "unknown"
            : StrategyDisplayName(configuredStrategy);
        var currentStrategyLabel = currentStrategy is null
            ? "awaiting runtime evidence"
            : StrategyDisplayName(currentStrategy);
        var selectedStrategyLabel = StrategyDisplayName(selectedStrategy);
        StrategyScopeText.Text = !processActive
            ? $"Next: {configuredStrategyLabel}"
            : pendingStrategy is not null
                ? $"Current: {currentStrategyLabel} · "
                    + (strategyScope.PendingActiveBattle is not null
                        ? "Pending active: "
                        : "Pending next: ")
                    + StrategyDisplayName(pendingStrategy)
                : $"Current: {currentStrategyLabel}";
        if (strategyScope.Degraded)
        {
            StrategyScopeText.Text += " · Degraded";
        }
        CurrentStrategyValueText.Text = processActive
            ? currentStrategyLabel
            : "No active process";
        NextStrategyLabelText.Text = processActive
            ? strategyScope.PendingActiveBattle is not null
                ? "PENDING ACTIVE"
                : "PENDING NEXT"
            : "NEXT START";
        NextStrategyValueText.Text = processActive
            ? pendingStrategy is null
                ? "None queued"
                : StrategyDisplayName(pendingStrategy)
            : configuredStrategyLabel;
        SelectedStrategyValueText.Text = selectedStrategyLabel;
        StrategySelectionText.Text = _strategyRequestMessage;
        StrategySelectionText.Visibility = string.IsNullOrWhiteSpace(
            _strategyRequestMessage)
                ? Visibility.Collapsed
                : Visibility.Visible;
        var exclusiveValidation = status.Control.ExclusiveValidation;
        TournamentValidationText.Text = FormatExclusiveValidation(
            exclusiveValidation);
        var validationRelevant = exclusiveValidation is not null
            && (!string.IsNullOrWhiteSpace(exclusiveValidation.CurrentRequestId)
                || exclusiveValidation.Receipts.Values.Any(receipt =>
                    receipt.Status is "claimed" or "running" or "cleanup"));
        TournamentValidationText.Visibility = validationRelevant
            ? Visibility.Visible
            : Visibility.Collapsed;
        _currentTournamentLaunch = CurrentTournamentLaunch(
            exclusiveValidation);
        _tournamentLaunchCanStart = processActive
            && _serverCompatibility?.IsCompatible == true
            && string.Equals(
                status.Control.State,
                "RUNNING",
                StringComparison.OrdinalIgnoreCase)
            && status.Observation is { Stale: false }
            && status.Observation.StateLabel is "HOME_SCREEN" or "TOURNAMENT_SCREEN";
        TournamentLaunchButton.IsEnabled =
            _currentTournamentLaunch is { Launch.Status: "awaiting_operator" }
            && _serverCompatibility?.IsCompatible == true;
        TournamentLaunchButton.Visibility =
            _currentTournamentLaunch is { Launch.Status: "awaiting_operator" }
                ? Visibility.Visible
                : Visibility.Collapsed;
        if (_currentTournamentLaunch is
                { Launch.Status: "awaiting_operator" } launchReceipt
            && launchReceipt.RequestId
                != _autoPromptedTournamentLaunchRequestId
            && _serverCompatibility?.IsCompatible == true)
        {
            _autoPromptedTournamentLaunchRequestId = launchReceipt.RequestId;
            Dispatcher.BeginInvoke(new Action(async () =>
                await ShowTournamentLaunchAsync(launchReceipt)));
        }
        var modeDisposition = !processActive
            ? "saved for next terminal"
            : modePending ? "awaiting runtime" : "active directive";
        var terminalPolicyStatus = status.ControlModel?.WhenBattleEnds;
        var terminalPolicyDisposition = terminalPolicyStatus is null
            ? modeDisposition
            : $"{modeDisposition}; {FormatStatusToken(terminalPolicyStatus.Status)}"
                + (string.IsNullOrWhiteSpace(terminalPolicyStatus.Reason)
                    ? ""
                    : $" — {terminalPolicyStatus.Reason}");
        TerminalPolicyText.Text =
            $"{FormatExecutionMode(status.Control.Mode)} · "
            + terminalPolicyDisposition;
        TerminalPolicyText.Foreground = modePending
            ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
            : (Brush)FindResource("MutedBrush");
        ControlSelectionText.Text = !processActive
            ? $"Saved request: {FormatAutomationState(status.Control)} · process stopped"
            : $"Requested: {FormatAutomationState(status.Control)} · Runtime: "
                + $"{FormatActionAuthority(status.ControlModel, status.Control)} · "
                + (statePending ? "awaiting acknowledgement" : "acknowledged");
        var requestedAdbTarget = status.Control.AdbPort is not null
            ? $"localhost:{status.Control.AdbPort.Value}"
            : service?.AdbTarget ?? "unknown";

        var pidAgreement = service?.MainPid is not null && runtime?.Pid is not null
            ? service.MainPid == runtime.Pid ? "match" : "MISMATCH"
            : "not comparable";
        var targetAgreement = service?.AdbTarget is not null && runtime?.Target is not null
            ? string.Equals(
                service.AdbTarget,
                runtime.Target,
                StringComparison.OrdinalIgnoreCase)
                ? "match"
                : "MISMATCH"
            : "not comparable";
        RuntimeDetailText.Text = string.Join(
            Environment.NewLine,
            new[]
            {
                $"Managed unit: {service?.Service ?? "-"}",
                $"Systemd state: {service?.LoadState ?? "-"}/{service?.ActiveState ?? "-"}/{service?.SubState ?? "-"}",
                $"Unit file state: {service?.UnitFileState ?? "-"}",
                $"Systemd MainPID: {service?.MainPid?.ToString() ?? "-"}",
                $"Last service exit status: {service?.ExitStatus?.ToString() ?? "-"}",
                $"Requested ADB target: {requestedAdbTarget}",
                $"Configured ADB target: {service?.AdbTarget ?? "-"}",
                $"ADB target source: {service?.AdbPortSource ?? "-"}",
                $"ADB target file: {service?.AdbEnvironmentFile ?? "-"}",
                $"Installed unit reads target file: {YesNo(service?.AutomationEnvironmentFileLoaded)}",
                $"Systemd EnvironmentFiles: {service?.ServiceEnvironmentFiles ?? "-"}",
                $"Current runtime strategy: {currentStrategy ?? "-"}",
                $"Strategy Action Gate: {FormatStrategyActionGate(strategyGate)}",
                $"Strategy Gate failed checks: {Join(strategyGate?.FailedCheckIds)}",
                $"Strategy Gate allowed collectors: {Join(strategyGate?.AllowedAuxiliaryCollectors)}",
                $"{pendingStrategyLabel}: {pendingStrategy ?? "-"}",
                $"Strategy request mode: {status.Control.StrategyApplyMode}",
                $"Configured next-start strategy: {configuredStrategy ?? "-"}",
                $"Strategy source: {service?.StrategySource ?? "-"}",
                $"Strategy file: {service?.StrategyEnvironmentFile ?? "-"}",
                $"Next-start gate policy: {service?.StartupGatePolicy ?? "-"}",
                $"Gate policy source: {service?.StartupGatePolicySource ?? "-"}",
                $"Runtime lock: {runtime?.File ?? "-"}",
                $"Runtime lock PID: {runtime?.Pid?.ToString() ?? "-"}",
                $"Lock held / PID alive: {YesNo(runtime?.LockHeld)} / {YesNo(runtime?.PidAlive)}",
                $"Systemd/lock PID identity: {pidAgreement}",
                $"Active/stale runtime target: {runtime?.Target ?? "-"}",
                $"Configured/runtime target identity: {targetAgreement}",
                $"Runtime started: {runtime?.StartedAt ?? "-"}",
                $"Menu: {status.Observation?.Menu ?? "-"}",
                $"Secondary: {Join(status.Observation?.Secondary)}",
                $"Overlays: {Join(status.Observation?.Overlays)}",
            });
        LastErrorText.Text = status.Control.Error
            ?? service?.Error
            ?? service?.AdbPortError
            ?? service?.StrategyError
            ?? service?.StartupGatePolicyError
            ?? "";
    }

    private void RenderConfirmedLocalMappings(
        ConfirmedLocalMappingStatus? status)
    {
        var presentation =
            ControlSurfaceCompatibility.ConfirmedLocalMapping(status);
        ConfirmedLocalMappingBanner.Visibility = presentation.Visible
            ? Visibility.Visible
            : Visibility.Collapsed;
        var compatible =
            ControlSurfaceCompatibility.CanOpenSaveMappingIntegration(
                _serverCompatibility);
        SaveMappingIntegrationMenuItem.IsEnabled = compatible;
        SaveMappingIntegrationMenuItem.ToolTip = compatible
            ? "Review exact proposals and prepare one in an owned feature worktree."
            : "Linux API revision 35 with save_mapping_integration_v1 is required.";
        ReviewSaveMappingsButton.IsEnabled = compatible;
        ReviewSaveMappingsButton.ToolTip =
            SaveMappingIntegrationMenuItem.ToolTip;
        if (!presentation.Visible)
        {
            return;
        }
        ConfirmedLocalMappingTitleText.Text = presentation.Title;
        ConfirmedLocalMappingDetailText.Text = presentation.Detail;
        var color = presentation.Severity switch
        {
            "danger" => Color.FromRgb(255, 113, 135),
            "info" => Color.FromRgb(98, 213, 255),
            _ => Color.FromRgb(241, 191, 91),
        };
        var brush = new SolidColorBrush(color);
        ConfirmedLocalMappingBanner.BorderBrush = brush;
        ConfirmedLocalMappingTitleText.Foreground = brush;
    }

    private void RenderCurrentBattlePerks(CurrentBattlePerksStatus? perks)
    {
        perks ??= new CurrentBattlePerksStatus();
        var items = perks.Items ?? [];
        if (!CurrentBattlePerksMatch(_currentBattlePerks, items))
        {
            _currentBattlePerks.Clear();
            foreach (var item in items)
            {
                _currentBattlePerks.Add(item);
            }
        }
        if (string.Equals(perks.Status, "available", StringComparison.Ordinal))
        {
            CurrentPerksSummaryText.Text = perks.UniqueCount == 0
                ? "No perks in the latest save checkpoint"
                : $"{perks.UniqueCount} current perk"
                    + (perks.UniqueCount == 1 ? "" : "s")
                    + $" · {perks.PickedCount} total pick"
                    + (perks.PickedCount == 1 ? "" : "s");
            CurrentPerksSummaryText.Foreground = new SolidColorBrush(
                Color.FromRgb(101, 230, 166));
            var wave = perks.SavedWave?.ToString(CultureInfo.InvariantCulture)
                ?? "unknown";
            var captured = DateTimeOffset.TryParse(
                perks.CapturedAt,
                out var capturedAt)
                ? capturedAt.LocalDateTime.ToString("g")
                : "unknown time";
            CurrentPerksCheckpointText.Text =
                $"Player-save checkpoint at wave {wave} · captured {captured}.";
            return;
        }

        CurrentPerksSummaryText.Text = string.Equals(
            perks.Status,
            "awaiting_save_checkpoint",
            StringComparison.Ordinal)
            ? "Waiting for a player-save checkpoint"
            : "Current battle perks unavailable";
        CurrentPerksSummaryText.Foreground = new SolidColorBrush(
            string.Equals(
                perks.Status,
                "awaiting_save_checkpoint",
                StringComparison.Ordinal)
                ? Color.FromRgb(241, 191, 91)
                : Color.FromRgb(255, 113, 135));
        CurrentPerksCheckpointText.Text = perks.Reason switch
        {
            "current_run_unavailable" =>
                "No current battle scope is available yet.",
            "timeline_checkpoint_unavailable" =>
                "The running battle has not persisted its timeline yet.",
            "current_run_checkpoint_unavailable" =>
                "Waiting for a timeline checkpoint bound to this battle.",
            "timeline_checkpoint_too_large" or
            "timeline_checkpoint_invalid" or
            "current_perks_projection_invalid" =>
                "The persisted Perk presentation could not be read safely.",
            _ => "The list updates from the running battle's passive save monitor.",
        };
    }

    private static bool CurrentBattlePerksMatch(
        IReadOnlyList<CurrentBattlePerkItem> current,
        IReadOnlyList<CurrentBattlePerkItem> updated)
    {
        if (current.Count != updated.Count)
        {
            return false;
        }
        for (var index = 0; index < current.Count; index++)
        {
            var left = current[index];
            var right = updated[index];
            if (!string.Equals(
                    left.PerkKey,
                    right.PerkKey,
                    StringComparison.Ordinal)
                || !string.Equals(
                    left.Label,
                    right.Label,
                    StringComparison.Ordinal)
                || left.Level != right.Level
                || left.LastSelectedWave != right.LastSelectedWave
                || left.LastSelectedSequence != right.LastSelectedSequence)
            {
                return false;
            }
        }
        return true;
    }

    private void RenderBetterControlModel(
        BetterControlModelStatus? model,
        bool processActive)
    {
        static BetterControlActionAvailability Unavailable(string reason) =>
            new() { Available = false, Reason = reason };

        BetterControlActionAvailability Action(string name)
        {
            if (_serverCompatibility?.IsCompatible != true)
            {
                return Unavailable(
                    "Linux API revision 38 with the required control-surface capabilities is required."
                );
            }
            if (model is not null
                && model.Actions.TryGetValue(name, out var availability))
            {
                return availability;
            }
            return Unavailable("Better Control Model status is unavailable.");
        }

        var start = Action("start_battle");
        StartBattleButton.IsEnabled = start.Available;
        StartBattleButton.ToolTip = start.Reason;
        var attach = Action("attach_battle");
        _attachBattleAvailability = attach;
        UpdateAttachBattleAvailability();
        StartBattleButton.Visibility = start.Available
            ? Visibility.Visible
            : Visibility.Collapsed;
        AttachBattleButton.Visibility = attach.Available
            ? Visibility.Visible
            : Visibility.Collapsed;
        var take = Action("take_manual_control");
        TakeManualControlButton.IsEnabled = take.Available;
        TakeManualControlButton.ToolTip = take.Reason;
        var giveBack = Action("return_control");
        ReturnControlButton.IsEnabled = giveBack.Available;
        ReturnControlButton.ToolTip = giveBack.Reason;
        var manual = model?.ManualControl;
        var manualOngoing = manual is not null
            && manual.Status is not ("completed" or "interrupted" or "failed");
        var showReturnControl = giveBack.Available || manualOngoing;
        var showTakeManualControl = take.Available && !showReturnControl;
        TakeManualControlButton.Visibility = showTakeManualControl
            ? Visibility.Visible
            : Visibility.Collapsed;
        ReturnControlButton.Visibility = showReturnControl
            ? Visibility.Visible
            : Visibility.Collapsed;
        ManualSurrenderPanel.Visibility = showTakeManualControl
            ? Visibility.Visible
            : Visibility.Collapsed;
        var captureAction = Action("capture_current_setup");
        var captureReady = model?.SetupCapture?.Status == "ready";
        var captureOpenAction = model is null
            ? SetupCaptureOpenAction.Unavailable
            : ControlSurfaceCompatibility.SetupCaptureAction(model);
        CaptureSetupButton.IsEnabled =
            ControlSurfaceCompatibility.CanOpenSetupCapture(
                _serverCompatibility,
                model);
        CaptureSetupButton.Content = captureOpenAction switch
        {
            SetupCaptureOpenAction.Review => "Review captured setup…",
            SetupCaptureOpenAction.Progress => "View capture progress…",
            SetupCaptureOpenAction.Inspect => "View capture result…",
            _ => "Capture current setup as…",
        };
        CaptureSetupButton.ToolTip = _serverCompatibility?.IsCompatible != true
            ? captureAction.Reason
            : captureReady
            ? "Review the fresh capture and save a new inactive artifact."
            : captureOpenAction == SetupCaptureOpenAction.Inspect
            ? "Inspect the completed result; retry requires a separate explicit action."
            : captureAction.Reason;
        CaptureSetupMenuItem.IsEnabled = CaptureSetupButton.IsEnabled;
        CaptureSetupMenuItem.Header = CaptureSetupButton.Content;
        CaptureSetupMenuItem.ToolTip = CaptureSetupButton.ToolTip;
        var enable = Action("enable");
        ResumeButton.IsEnabled = processActive && enable.Available;
        ResumeButton.ToolTip = enable.Reason;
        var pause = Action("pause");
        PauseButton.IsEnabled = pause.Available;
        Pause15Button.IsEnabled = pause.Available;
        Pause30Button.IsEnabled = pause.Available;
        PauseButton.ToolTip = pause.Reason;
        Pause15Button.ToolTip = pause.Reason;
        Pause30Button.ToolTip = pause.Reason;

        BattleWorkflowText.Text = model?.BattleWorkflow is { } workflow
            ? $"{FormatStatusToken(workflow.Intent)} · "
                + ControlSurfaceCompatibility.PresentWorkflow(
                    workflow.Status).Label
                + (string.IsNullOrWhiteSpace(workflow.Reason)
                    ? ""
                    : $" — {workflow.Reason}")
            : start.Available
                ? "Verified New Battle boundary."
                : attach.Available
                    ? "Verified resumable or active battle boundary."
            : model is null
                ? "Waiting for Better Control Model status."
                : $"{FormatStatusToken(model.Observation.GameState)} · "
                    + (string.IsNullOrWhiteSpace(model.Observation.Reason)
                        ? "No matching battle action is currently available."
                        : model.Observation.Reason);
        ManualControlText.Text = manual is not null
            ? $"{FormatStatusToken(manual.Status)}"
                + (string.IsNullOrWhiteSpace(manual.Detail)
                    ? ""
                    : $" — {manual.Detail}")
                + (string.IsNullOrWhiteSpace(manual.RefreshStatus)
                    ? ""
                    : $" ({FormatStatusToken(manual.RefreshStatus)})")
                + $" · manual Surrender collection: "
                + $"{FormatStatusToken(manual.SurrenderCollection)}"
            : take.Reason;
        ManualControlText.Visibility = manual is not null
            || !showTakeManualControl
                ? Visibility.Visible
                : Visibility.Collapsed;
        CaptureSetupText.Text = model?.SetupCapture is { } capture
            ? $"{FormatStatusToken(capture.Status)}"
                + (string.IsNullOrWhiteSpace(capture.Reason)
                    ? ""
                    : $" — {capture.Reason}")
            : captureAction.Reason;
        CaptureSetupText.Visibility = model?.SetupCapture is not null
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private void CaptureSetup_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new SetupCaptureWindow(_api, UsePublishedStrategyAsync)
        {
            Owner = this,
        };
        dialog.ShowDialog();
        _ = RefreshStatusAsync(force: true);
    }

    private void HostPerformance_SnapshotUpdated(
        object? sender,
        HostPerformanceSnapshot snapshot)
    {
        if (Dispatcher.HasShutdownStarted || Dispatcher.HasShutdownFinished)
        {
            return;
        }
        _ = Dispatcher.BeginInvoke(
            () => RenderHostPerformance(snapshot),
            DispatcherPriority.Background);
    }

    private void HostSamplingToggle_Click(object sender, RoutedEventArgs e)
    {
        var enabled = !_hostPerformance.SamplingEnabled;
        _hostPerformance.SetSamplingEnabled(enabled);
        _settings.HostPerformanceSamplingEnabled = enabled;
        SaveSettingsBestEffort();
    }

    private void RenderHostPerformance(HostPerformanceSnapshot snapshot)
    {
        HostHealthText.Text = $"{snapshot.StateLabel} · {snapshot.HostName}";
        HostHealthText.Foreground = snapshot.State switch
        {
            HostPerformanceHealthState.Healthy =>
                new SolidColorBrush(Color.FromRgb(73, 214, 157)),
            HostPerformanceHealthState.Paused =>
                new SolidColorBrush(Color.FromRgb(98, 213, 255)),
            HostPerformanceHealthState.Critical =>
                new SolidColorBrush(Color.FromRgb(255, 113, 135)),
            HostPerformanceHealthState.Attention
                or HostPerformanceHealthState.Stale
                or HostPerformanceHealthState.BlueStacksNotDetected =>
                new SolidColorBrush(Color.FromRgb(241, 191, 91)),
            _ => (Brush)FindResource("MutedBrush"),
        };
        HostSamplingToggleButton.Content = snapshot.SamplingEnabled
            ? "Pause sampling"
            : "Resume sampling";
        if (snapshot.SamplingEnabled)
        {
            HostSamplingToggleButton.ClearValue(StyleProperty);
        }
        else
        {
            HostSamplingToggleButton.Style =
                (Style)FindResource("PrimaryButton");
        }
        HostSamplingToggleButton.ToolTip = snapshot.SamplingEnabled
            ? "Pause one-second host and BlueStacks sampling. Queued aggregates continue uploading."
            : "Resume one-second host and BlueStacks sampling.";
        HostCpuText.Text = FormatPercent(snapshot.HostCpuPercent);
        HostMemoryText.Text = snapshot.HostMemoryUsedPercent is null
            ? "-"
            : $"{FormatPercent(snapshot.HostMemoryUsedPercent)} · "
                + $"{FormatBytes(snapshot.HostAvailableMemoryBytes)} free";
        HostClockText.Text = snapshot.HostCpuFrequencyMhz is null
            ? "-"
            : $"{snapshot.HostCpuFrequencyMhz.Value / 1000.0:F2} GHz"
                + (snapshot.HostCpuFrequencyRatio is null
                    ? ""
                    : $" · {snapshot.HostCpuFrequencyRatio.Value:P0}");
        BlueStacksCpuText.Text = snapshot.BlueStacksCpuPercent is null
            ? snapshot.BlueStacksProcessCount == 0 ? "Not detected" : "-"
            : $"{snapshot.BlueStacksCpuPercent.Value:F1}% host"
                + (snapshot.BlueStacksCpuCorePercent is null
                    ? ""
                    : $" · {snapshot.BlueStacksCpuCorePercent.Value / 100.0:F1} cores");
        BlueStacksMemoryText.Text = snapshot.BlueStacksProcessCount == 0
            ? "-"
            : $"{FormatBytes(snapshot.BlueStacksWorkingSetBytes)} · "
                + $"{snapshot.BlueStacksProcessCount} proc";
        HostGpuText.Text = !snapshot.GpuCountersAvailable
            ? "Unavailable"
            : FormatPercent(snapshot.HostGpuPercent);
        HostGpuMemoryText.Text = !snapshot.GpuCountersAvailable
            ? "-"
            : $"{FormatBytes(snapshot.HostGpuDedicatedMemoryBytes)} dedicated"
                + $" · {FormatBytes(snapshot.HostGpuSharedMemoryBytes)} shared";
        BlueStacksGpuText.Text = !snapshot.GpuCountersAvailable
            ? "-"
            : snapshot.BlueStacksProcessCount == 0
                ? "Not detected"
                : $"{FormatPercent(snapshot.BlueStacksGpuPercent)}"
                    + $" · {FormatBytes(snapshot.BlueStacksGpuDedicatedMemoryBytes)}";
        var topGpuCompetitor = snapshot.GpuCompetitors.FirstOrDefault();
        GpuCompetitorText.Text = !snapshot.GpuCountersAvailable
            ? "-"
            : topGpuCompetitor is null
                ? "None detected"
                : $"{topGpuCompetitor.ProcessName} · "
                    + $"{topGpuCompetitor.GpuPercentAverage:F1}%";
        GpuCompetitorText.Foreground =
            topGpuCompetitor?.GpuPercentMaximum >= 20.0
                ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
                : new SolidColorBrush(Color.FromRgb(237, 242, 247));
        OtherWindowsCpuText.Text = FormatPercent(
            snapshot.OtherWindowsCpuPercent);
        OtherWindowsCpuText.Foreground = snapshot.OtherWindowsCpuPercent >= 50.0
            ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
            : new SolidColorBrush(Color.FromRgb(237, 242, 247));
        var attributedApplications = snapshot.ProcessAttribution
            .GroupBy(
                process => process.ProcessName,
                StringComparer.OrdinalIgnoreCase)
            .Select(group => new
            {
                ProcessName = group.First().ProcessName,
                ProcessCount = group.Count(),
                CpuPercentAverage = group.Any(process =>
                    process.CpuPercentAverage is not null)
                    ? group.Sum(process =>
                        process.CpuPercentAverage ?? 0.0)
                    : (double?)null,
                WorkingSetBytes = group.Sum(process =>
                    process.WorkingSetBytesMaximum),
            })
            .ToArray();
        var topCpuProcess = attributedApplications
            .Where(process => process.CpuPercentAverage is not null)
            .OrderByDescending(process => process.CpuPercentAverage)
            .FirstOrDefault();
        var topMemoryProcess = attributedApplications
            .OrderByDescending(process => process.WorkingSetBytes)
            .FirstOrDefault();
        var attributionCollecting = snapshot.ProcessAttributionState
            is HostProcessAttributionState.Active
                or HostProcessAttributionState.Recovering;
        TopCpuProcessText.Text = topCpuProcess is null
            ? attributionCollecting ? "Warming up" : "-"
            : $"{topCpuProcess.ProcessName} · "
                + $"{topCpuProcess.CpuPercentAverage:F1}%"
                + (topCpuProcess.ProcessCount > 1
                    ? $" · {topCpuProcess.ProcessCount} proc"
                    : "");
        TopMemoryProcessText.Text = topMemoryProcess is null
            ? attributionCollecting ? "Warming up" : "-"
            : $"{topMemoryProcess.ProcessName} · "
                + $"{FormatBytes(topMemoryProcess.WorkingSetBytes)} working"
                + (topMemoryProcess.ProcessCount > 1
                    ? $" · {topMemoryProcess.ProcessCount} proc"
                    : "");
        ProcessAttributionStateText.Text = snapshot.ProcessAttributionState switch
        {
            HostProcessAttributionState.Arming => "Pressure detected · arming",
            HostProcessAttributionState.Active =>
                $"Active · {snapshot.ProcessAttributionProcessCount} inspected",
            HostProcessAttributionState.Recovering =>
                $"Recovery watch · {snapshot.ProcessAttributionProcessCount} inspected",
            _ => "Idle · threshold-triggered",
        };
        ProcessAttributionStateText.Foreground = snapshot.ProcessAttributionState
            is HostProcessAttributionState.Active
                or HostProcessAttributionState.Recovering
            ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
            : (Brush)FindResource("MutedBrush");
        HostTelemetryQueueText.Text = !snapshot.UploadEnabled
            ? $"Local only · {snapshot.PendingAggregateCount} queued"
            : snapshot.PendingAggregateCount == 0
                ? snapshot.LastUploadedAtUtc is null
                    ? "Buffering"
                    : "Published"
                : $"{snapshot.PendingAggregateCount} queued";
        if (snapshot.DroppedAggregateCount > 0)
        {
            HostTelemetryQueueText.Text +=
                $" · {snapshot.DroppedAggregateCount} dropped";
        }

        var details = new List<string>
        {
            snapshot.SamplingEnabled
                ? "Host sampling is enabled."
                : "Host sampling is paused; queued aggregates still upload.",
            snapshot.SampledAtUtc is null
                ? "No host sample is available yet."
                : $"Sampled {snapshot.SampledAtUtc.Value.ToLocalTime():T}.",
            $"Control Surface CPU: "
                + $"{FormatPercent(snapshot.ControlSurfaceCpuPercent)}; "
                + $"other Windows CPU: "
                + $"{FormatPercent(snapshot.OtherWindowsCpuPercent)}.",
            $"Sampler cost: "
                + $"{snapshot.SampleDurationMilliseconds?.ToString("F2", CultureInfo.InvariantCulture) ?? "-"} ms/sample.",
            $"Process attribution: {snapshot.ProcessAttributionState}; "
                + $"{snapshot.ProcessAttributionProcessCount} processes inspected; "
                + $"scan cost "
                + $"{snapshot.ProcessAttributionSampleDurationMilliseconds?.ToString("F2", CultureInfo.InvariantCulture) ?? "-"} ms.",
            $"BlueStacks I/O: read "
                + $"{FormatRate(snapshot.BlueStacksIoReadBytesPerSecond)}, write "
                + $"{FormatRate(snapshot.BlueStacksIoWriteBytesPerSecond)}.",
            snapshot.GpuCountersAvailable
                ? $"GPU counter cost: "
                    + $"{snapshot.GpuSampleDurationMilliseconds?.ToString("F2", CultureInfo.InvariantCulture) ?? "-"} ms/sample."
                : "Windows GPU performance counters are unavailable.",
            $"BlueStacks GPU memory: "
                + $"{FormatBytes(snapshot.BlueStacksGpuDedicatedMemoryBytes)} dedicated, "
                + $"{FormatBytes(snapshot.BlueStacksGpuSharedMemoryBytes)} shared.",
            snapshot.LastUploadedAtUtc is null
                ? "No aggregate has been acknowledged by Linux in this session."
                : $"Last Linux acknowledgement: "
                    + $"{snapshot.LastUploadedAtUtc.Value.ToLocalTime():T}.",
        };
        if (!string.IsNullOrWhiteSpace(snapshot.SamplerError))
        {
            details.Add($"Sampler: {snapshot.SamplerError}");
        }
        foreach (var competitor in snapshot.GpuCompetitors)
        {
            details.Add(
                $"Other GPU: {competitor.ProcessName} "
                + $"(PID {competitor.ProcessId}) — "
                + $"{competitor.GpuPercentAverage:F1}% avg, "
                + $"{competitor.GpuPercentMaximum:F1}% max, "
                + $"{FormatBytes(competitor.DedicatedMemoryBytesMaximum)} "
                + "dedicated, "
                + $"{FormatBytes(competitor.SharedMemoryBytesMaximum)} "
                + "shared.");
        }
        foreach (var process in snapshot.ProcessAttribution)
        {
            details.Add(
                $"Other process: {process.ProcessName} "
                + $"(PID {process.ProcessId}) — CPU "
                + $"{process.CpuPercentAverage?.ToString("F1", CultureInfo.InvariantCulture) ?? "-"}% avg, "
                + $"{process.CpuPercentMaximum?.ToString("F1", CultureInfo.InvariantCulture) ?? "-"}% max; "
                + $"{FormatBytes(process.WorkingSetBytesMaximum)} working set, "
                + $"{FormatBytes(process.PrivateBytesMaximum)} private.");
        }
        if (!string.IsNullOrWhiteSpace(snapshot.GpuError))
        {
            details.Add($"GPU counters: {snapshot.GpuError}");
        }
        if (!string.IsNullOrWhiteSpace(snapshot.StorageError))
        {
            details.Add($"Local spool: {snapshot.StorageError}");
        }
        if (!string.IsNullOrWhiteSpace(snapshot.UploadError))
        {
            details.Add($"Upload: {snapshot.UploadError}");
        }
        HostPerformancePanel.ToolTip = string.Join(
            Environment.NewLine,
            details);
    }

    private void RenderBattles(BattleListResponse response)
    {
        _latestBattles = response;
        _battleHistoryWindow?.UpdateBattles(response);

        var battle = response.Items.FirstOrDefault();
        if (battle is null)
        {
            LatestBattleTitleText.Text = "No completed battles found";
            LatestBattleCompactText.Text = "No completed battles found";
            LatestBattleCompactText.ToolTip = null;
            LatestCapturedText.Text = "-";
            LatestStrategyText.Text = "-";
            LatestTypeText.Text = "-";
            LatestTierText.Text = "-";
            LatestWaveText.Text = "-";
            LatestTimeText.Text = "-";
            LatestCoinsText.Text = "-";
            LatestCoinsHourText.Text = "-";
            LatestCellsText.Text = "-";
            LatestCellsHourText.Text = "-";
            LatestQualityText.Text = "-";
            return;
        }

        LatestBattleTitleText.Text = battle.BattleId;
        LatestCapturedText.Text = battle.CapturedDisplay;
        LatestStrategyText.Text = battle.StrategyDisplay;
        LatestTypeText.Text = battle.BattleTypeDisplay;
        LatestTierText.Text = battle.Tier?.ToString(CultureInfo.InvariantCulture) ?? "-";
        LatestWaveText.Text = battle.Wave?.ToString(CultureInfo.InvariantCulture) ?? "-";
        LatestTimeText.Text = battle.RealTime ?? "-";
        LatestCoinsText.Text = battle.CoinsEarned ?? "-";
        LatestCoinsHourText.Text = battle.CoinsPerHour ?? "-";
        LatestCellsText.Text = battle.CellsEarned ?? "-";
        LatestCellsHourText.Text = battle.CellsPerHour ?? "-";
        LatestQualityText.Text = battle.QualityDisplay;
        LatestBattleCompactText.Text =
            $"{battle.BattleTypeDisplay} · "
            + $"{(battle.Tier is int tier ? $"T{tier}" : "Tier -")} · "
            + $"Wave {battle.Wave?.ToString(CultureInfo.InvariantCulture) ?? "-"} · "
            + $"{battle.CoinsEarned ?? "-"} coins · "
            + $"{battle.CoinsPerHour ?? "-"}/h";
        LatestBattleCompactText.ToolTip =
            $"{battle.BattleId} · captured {battle.CapturedDisplay} · "
            + $"Strategy {battle.StrategyDisplay} · {battle.QualityDisplay}";
    }

    private void RenderActivity(ActivityResponse response)
    {
        CollapseExpandedActivity();
        _activitySourceFileId = response.SourceFileId;
        _activityEndCursor = response.EndCursor;
        _activityScopeId = response.ScopeId;
        ClearActivityButton.IsEnabled = !string.IsNullOrWhiteSpace(_activityEndCursor);
        _activity.Clear();
        foreach (var entry in response.Items.AsEnumerable().Reverse())
        {
            _activity.Add(entry);
        }
        if (ReferenceEquals(SidebarTabs.SelectedItem, ActivityTab)
            && ActivityAutoFollowBox.IsChecked == true
            && _activity.Count > 0)
        {
            _ = Dispatcher.InvokeAsync(
                () => ActivityGrid.ScrollIntoView(_activity[0]),
                DispatcherPriority.Background);
        }
    }

    private void OpenBattleHistory_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (_battleHistoryWindow is not null)
            {
                if (_battleHistoryWindow.WindowState == WindowState.Minimized)
                {
                    _battleHistoryWindow.WindowState = WindowState.Normal;
                }
                _battleHistoryWindow.Activate();
                return;
            }

            var historyWindow = new BattleHistoryWindow(_api)
            {
                Owner = this,
            };
            WindowPlacementStore.Restore(
                historyWindow,
                _settings.BattleHistoryWindowPlacement);
            historyWindow.UpdateBattles(_latestBattles);
            historyWindow.Closing += (_, _) =>
            {
                var placement = WindowPlacementStore.Capture(historyWindow);
                if (placement is not null)
                {
                    _settings.BattleHistoryWindowPlacement = placement;
                    SaveSettingsBestEffort();
                }
            };
            historyWindow.Closed += (_, _) => _battleHistoryWindow = null;
            historyWindow.Show();
            _battleHistoryWindow = historyWindow;
        }
        catch (Exception exc)
        {
            _battleHistoryWindow = null;
            ShowError(new InvalidOperationException(
                $"Unable to open Battle History: {exc.Message}",
                exc));
        }
    }

    private static string Join(IEnumerable<string>? values) =>
        values is null || !values.Any() ? "-" : string.Join(", ", values);

    private void SetSelectionStyle(Button button, bool selected, bool pending = false)
    {
        if (!selected)
        {
            button.ClearValue(StyleProperty);
            return;
        }
        button.Style = (Style)FindResource(
            pending ? "PendingSelectionButton" : "ActiveSelectionButton");
    }

    private string? SelectedStrategy() =>
        StrategySelectionBox.SelectedItem is ComboBoxItem item
            ? NormalizeStrategy(item.Tag?.ToString())
            : null;

    private void UpdateStrategyOptions(
        IEnumerable<string>? options,
        params string?[] retainedValues)
    {
        var selected = SelectedStrategy();
        IEnumerable<string> dirtySelection =
            _strategySelection.Dirty && selected is not null
                ? [selected]
                : [];
        var desired = (options ?? [])
            .Concat(retainedValues.Where(value => !string.IsNullOrWhiteSpace(value))!)
            .Concat(dirtySelection)
            .Select(NormalizeStrategy)
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Cast<string>()
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();
        if (desired.Count == 0)
        {
            desired.AddRange(["farm_t18", "farm_t19", "tournament", "none"]);
        }
        var existing = StrategySelectionBox.Items
            .OfType<ComboBoxItem>()
            .Select(item => NormalizeStrategy(item.Tag?.ToString()))
            .Where(value => value is not null)
            .Cast<string>()
            .ToList();
        if (existing.SequenceEqual(desired, StringComparer.OrdinalIgnoreCase))
        {
            return;
        }
        _updatingStrategySelection = true;
        try
        {
            StrategySelectionBox.Items.Clear();
            foreach (var identifier in desired)
            {
                StrategySelectionBox.Items.Add(new ComboBoxItem
                {
                    Content = StrategyDisplayName(identifier),
                    Tag = identifier,
                });
            }
            var selection = desired.FirstOrDefault(identifier => string.Equals(
                    identifier,
                    selected,
                    StringComparison.OrdinalIgnoreCase))
                ?? desired[0];
            StrategySelectionBox.SelectedItem = StrategySelectionBox.Items
                .OfType<ComboBoxItem>()
                .First(item => string.Equals(
                    NormalizeStrategy(item.Tag?.ToString()),
                    selection,
                    StringComparison.OrdinalIgnoreCase));
        }
        finally
        {
            _updatingStrategySelection = false;
        }
    }

    private void EnsureStrategyOption(string strategy)
    {
        var normalized = NormalizeStrategy(strategy);
        if (normalized is null
            || StrategySelectionBox.Items
                .OfType<ComboBoxItem>()
                .Any(item => string.Equals(
                    NormalizeStrategy(item.Tag?.ToString()),
                    normalized,
                    StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }

        _updatingStrategySelection = true;
        try
        {
            StrategySelectionBox.Items.Add(new ComboBoxItem
            {
                Content = StrategyDisplayName(normalized),
                Tag = normalized,
            });
        }
        finally
        {
            _updatingStrategySelection = false;
        }
    }

    private void SelectGameSpeedTarget(double target)
    {
        var item = GameSpeedTargetBox.Items
            .OfType<ComboBoxItem>()
            .FirstOrDefault(candidate =>
                double.TryParse(
                    candidate.Tag?.ToString(),
                    NumberStyles.Float,
                    CultureInfo.InvariantCulture,
                    out var candidateTarget)
                && Math.Abs(candidateTarget - target) < 0.001);
        if (item is null || ReferenceEquals(GameSpeedTargetBox.SelectedItem, item))
        {
            return;
        }

        _updatingGameSpeedTargetSelection = true;
        try
        {
            GameSpeedTargetBox.SelectedItem = item;
        }
        finally
        {
            _updatingGameSpeedTargetSelection = false;
        }
    }

    private void SelectStrategy(string? strategy)
    {
        var normalized = NormalizeStrategy(strategy);
        var item = StrategySelectionBox.Items
            .OfType<ComboBoxItem>()
            .FirstOrDefault(candidate => string.Equals(
                NormalizeStrategy(candidate.Tag?.ToString()),
                normalized,
                StringComparison.OrdinalIgnoreCase));
        if (item is null || ReferenceEquals(StrategySelectionBox.SelectedItem, item))
        {
            return;
        }

        _updatingStrategySelection = true;
        try
        {
            StrategySelectionBox.SelectedItem = item;
        }
        finally
        {
            _updatingStrategySelection = false;
        }
    }

    private void UpdateStrategyActionAvailability()
    {
        var selected = SelectedStrategy();
        var hasSelection = selected is not null;
        var context = CurrentStrategySelectionContext();
        var hasPending = _pendingStrategy is not null;
        var queueAlreadyRequested =
            StrategySelectionCoordinator.IsNextBoundaryNoOp(context, selected);
        var adoptionAlreadyRequested = hasPending
            && string.Equals(
                selected,
                _requestedStrategy,
                StringComparison.OrdinalIgnoreCase)
            && string.Equals(
                _strategyApplyMode,
                "active_battle",
                StringComparison.OrdinalIgnoreCase);
        var requestInFlight = _strategySelection.RequestInFlight;
        var retryAvailable = _strategyProcessActive
            && _strategySelection.RetryAvailable(selected);

        StrategySelectionBox.IsEnabled =
            _strategyLifecycleAvailable && !requestInFlight;
        StrategyProfilesButton.IsEnabled =
            _serverCompatibility?.IsCompatible == true && !requestInFlight;
        StrategyProfilesMenuItem.IsEnabled = StrategyProfilesButton.IsEnabled;
        QueueStrategyButton.Content = _strategyProcessActive
            ? "Retry next battle"
            : "Save startup default";
        QueueStrategyButton.ToolTip = _strategyProcessActive
            ? "Retry the failed automatic next-boundary request for this retained selection."
            : "Remember this strategy without starting automation. System > Services Start Automation already uses the current selection.";
        QueueStrategyButton.Visibility = !_strategyProcessActive || retryAvailable
            ? Visibility.Visible
            : Visibility.Collapsed;
        Grid.SetColumnSpan(
            QueueStrategyButton,
            _strategyProcessActive ? 1 : 2);
        QueueStrategyButton.IsEnabled = _strategyLifecycleAvailable
            && hasSelection
            && !requestInFlight
            && (_strategyProcessActive
                ? retryAvailable
                : !queueAlreadyRequested);
        AdoptStrategyButton.IsEnabled = _strategyLifecycleAvailable
            && _strategyProcessActive
            && !_strategyDegradedObserver
            && _serverCompatibility?.IsCompatible == true
            && hasSelection
            && !string.Equals(
                selected,
                _currentStrategy,
                StringComparison.OrdinalIgnoreCase)
            && !adoptionAlreadyRequested
            && !requestInFlight;
        AdoptStrategyButton.Visibility = _strategyProcessActive
            ? Visibility.Visible
            : Visibility.Collapsed;
        Grid.SetColumn(AdoptStrategyButton, retryAvailable ? 1 : 0);
        Grid.SetColumnSpan(AdoptStrategyButton, retryAvailable ? 1 : 2);
        AdoptStrategyButton.ToolTip = _strategyDegradedObserver
            ? "This attached battle must remain a degraded observer. The selected Strategy is queued for the next battle."
            : "Request this strategy for the current battle. New-run setup still waits for a genuine boundary.";
        SelectedStrategyValueText.Text = StrategyDisplayName(selected);
        StrategyActionHelpText.Text = _strategyDegradedObserver
            ? "This attached battle remains observation-only because its Strategy compatibility was not proved. Changes apply at the next safe battle boundary."
            : _strategyProcessActive
            ? retryAvailable
                ? "The automatic next-boundary request failed. The selection is retained for Retry; Switch this battle remains a separate action."
                : "Changing the selection queues it for the next battle. Switch this battle changes normal strategy behavior now; startup setup still waits for the next real boundary."
            : "Start already uses this selection. Save startup default only if it should be remembered without starting automation.";
        StrategyActionHelpText.Visibility = _strategySelection.Dirty
            || _strategyDegradedObserver
            ? Visibility.Visible
            : Visibility.Collapsed;
        if (string.IsNullOrWhiteSpace(_strategyRequestMessage)
            && !requestInFlight)
        {
            StrategySelectionText.Visibility = Visibility.Collapsed;
        }
        UpdateAttachBattleAvailability();
    }

    private void UpdateAttachBattleAvailability()
    {
        var availability = ControlSurfaceCompatibility.ResolveAttachAvailability(
            _attachBattleAvailability,
            _strategySelection.Dirty,
            _strategySelection.RequestInFlight);
        AttachBattleButton.IsEnabled = availability.Available;
        AttachBattleButton.ToolTip = availability.Reason;
    }

    private StrategySelectionContext CurrentStrategySelectionContext() =>
        new(
            _strategyLifecycleAvailable,
            _strategyProcessActive,
            _configuredStrategy,
            _currentStrategy,
            _requestedStrategy,
            _pendingStrategy,
            _strategyApplyMode);

    private void UpdateControlSurfaceCompatibility()
    {
        if (_serverCompatibility is null)
        {
            LinuxServiceCompatibilityText.Text =
                "Waiting for Linux API compatibility status.";
            LinuxServiceCompatibilityText.Foreground =
                (Brush)FindResource("MutedBrush");
            RestartControlSurfaceButton.Visibility = Visibility.Collapsed;
            RestartControlSurfaceButton.IsEnabled = false;
            CompatibilityBanner.Visibility = Visibility.Collapsed;
            RestartControlSurfaceBannerButton.IsEnabled = false;
            RestartControlSurfaceBannerButton.ToolTip = null;
            return;
        }
        if (_serverCompatibility.IsCompatible)
        {
            LinuxServiceCompatibilityText.Text =
                $"Linux API v{_serverCompatibility.ApiVersion}, server revision "
                + $"{_serverCompatibility.ServerRevision}, supports this client.";
            LinuxServiceCompatibilityText.Foreground =
                new SolidColorBrush(Color.FromRgb(73, 214, 157));
            RestartControlSurfaceButton.Visibility = Visibility.Collapsed;
            RestartControlSurfaceButton.IsEnabled = false;
            CompatibilityBanner.Visibility = Visibility.Collapsed;
            RestartControlSurfaceBannerButton.IsEnabled = false;
            RestartControlSurfaceBannerButton.ToolTip = null;
            return;
        }

        var destinationValid = TunnelHostConfigurationValidator.IsValidDestination(
            _settings.SshDestination);
        var hostAvailable = _tunnelHostSnapshot is not null
            && _tunnelHostProtocolMismatch is null;
        CompatibilityBanner.Visibility = Visibility.Visible;
        CompatibilityBannerTitle.Text = _controlSurfaceRestartInFlight
            ? "RESTARTING LINUX API — AUTOMATION START REMAINS DISABLED"
            : "LINUX API UPDATE REQUIRED — AUTOMATION START IS DISABLED";
        if (_controlSurfaceRestartInFlight)
        {
            const string progress =
                "Restarting the fixed Linux control API service over SSH. "
                + "Automation is not being started or restarted.";
            CompatibilityBannerText.Text = progress;
            LinuxServiceCompatibilityText.Text = progress;
            LinuxServiceCompatibilityText.Foreground =
                new SolidColorBrush(Color.FromRgb(241, 191, 91));
            RestartControlSurfaceButton.Visibility = Visibility.Visible;
            RestartControlSurfaceButton.IsEnabled = false;
            RestartControlSurfaceBannerButton.IsEnabled = false;
            RestartControlSurfaceBannerButton.ToolTip =
                "The Linux API restart is already in progress.";
            return;
        }

        var problems = DescribeCompatibilityProblems(_serverCompatibility);
        var incompatibility =
            "Start Automation is disabled because the Linux control "
            + "API is older than or incompatible with this Windows client ("
            + problems
            + "). Restart the Linux API service to reload the current Linux code.";
        LinuxServiceCompatibilityText.Text = destinationValid
            ? incompatibility
                + " Click Restart Linux API service below."
            : incompatibility
                + " Enter the Linux SSH destination in Preferences to enable "
                + "Restart Linux API service, or run "
                + "'systemctl --user restart thetower-control-surface.service' "
                + "on Linux. If this warning remains, update the Linux checkout "
                + "and restart the service again.";
        CompatibilityBannerText.Text = destinationValid
            ? $"Connected Linux API is incompatible ({problems}). Select Restart "
                + "Linux API service, wait for this banner to disappear, then use "
                + "Start Automation. This restarts only the control "
                + "API; it does not start automation or alter the game."
            : $"Connected Linux API is incompatible ({problems}). Enter a valid "
                + "Linux SSH destination to enable the restart button, or run "
                + "'systemctl --user restart "
                + "thetower-control-surface.service' on Linux. Wait for this "
                + "banner to disappear, then retry Start. If it remains, update "
                + "the Linux checkout and restart the API again.";
        LinuxServiceCompatibilityText.Foreground =
            new SolidColorBrush(Color.FromRgb(241, 191, 91));
        RestartControlSurfaceButton.Visibility = Visibility.Visible;
        RestartControlSurfaceButton.IsEnabled =
            destinationValid
            && hostAvailable
            && !_controlSurfaceServiceActionInFlight;
        RestartControlSurfaceBannerButton.IsEnabled =
            destinationValid
            && hostAvailable
            && !_controlSurfaceServiceActionInFlight;
        RestartControlSurfaceBannerButton.ToolTip = destinationValid && hostAvailable
            ? "Restart only the fixed Linux control API, verify compatibility, "
                + "and leave game automation stopped."
            : !destinationValid
                ? "Enter a valid Linux SSH destination in the SSH Tunnel panel to "
                    + "enable this mitigation."
                : "Reconnect or restart the per-user tunnel host first.";
    }

    private string? StartBlockerDescription(
        bool lifecycleAvailable,
        bool processActive)
    {
        if (!lifecycleAvailable)
        {
            return "Start is disabled because the managed Linux automation "
                + "service is unavailable. Review Runtime Evidence.";
        }
        if (processActive)
        {
            return "Start is disabled because automation is already active.";
        }
        if (_serverCompatibility is { IsCompatible: false } compatibility)
        {
            return "Start is disabled because the connected Linux API is "
                + $"incompatible ({DescribeCompatibilityProblems(compatibility)}). "
                + "Use the prominent Restart Linux API service banner above, "
                + "wait for it to disappear, then retry Start.";
        }
        return null;
    }

    private static string DescribeCompatibilityProblems(
        ControlSurfaceCompatibilityResult compatibility)
    {
        var problems = new List<string>();
        if (!compatibility.ApiVersionSupported)
        {
            problems.Add(
                $"API version {compatibility.ApiVersion}; requires "
                + ControlSurfaceCompatibility.RequiredApiVersion);
        }
        if (!compatibility.ServerRevisionSupported)
        {
            problems.Add(
                $"server revision {compatibility.ServerRevision}; requires "
                + $"{ControlSurfaceCompatibility.MinimumServerRevision} or newer");
        }
        if (compatibility.MissingCapabilities.Count > 0)
        {
            problems.Add(
                "missing capabilities: "
                + string.Join(", ", compatibility.MissingCapabilities));
        }
        return string.Join("; ", problems);
    }

    private static string? NormalizeStrategy(string? strategy) =>
        strategy?.Trim().ToLowerInvariant() switch
        {
            "farm" => "farm_t18",
            "farm_t18" => "farm_t18",
            "farm_t19" => "farm_t19",
            "farm_t19_experiment" => "farm_t19",
            "tournament" => "tournament",
            "none" => "none",
            _ => strategy?.Trim().ToLowerInvariant(),
        };

    private static string FormatExclusiveValidation(
        ExclusiveValidationLedgerStatus? ledger)
    {
        if (ledger is null || ledger.Receipts.Count == 0)
        {
            return "No exclusive strategy validation request.";
        }
        ExclusiveValidationReceiptStatus? receipt =
            ledger.Receipts.Values.FirstOrDefault(candidate =>
                candidate.Status is "claimed" or "running" or "cleanup");
        if (receipt is null && !string.IsNullOrWhiteSpace(ledger.CurrentRequestId))
        {
            ledger.Receipts.TryGetValue(ledger.CurrentRequestId, out receipt);
        }
        if (receipt is null)
        {
            return "No exclusive strategy validation request.";
        }
        if (string.Equals(receipt.Status, "result", StringComparison.OrdinalIgnoreCase))
        {
            if (!string.Equals(
                    receipt.Outcome,
                    "ready",
                    StringComparison.OrdinalIgnoreCase))
            {
                return $"Tournament validation {receipt.Outcome ?? "failed"}: "
                    + $"{receipt.Reason ?? "reason unavailable"}";
            }
            return receipt.Launch?.Status switch
            {
                "awaiting_operator" =>
                    "Tournament validation passed; waiting for Start Tournament or Cancel.",
                "requested" =>
                    "Tournament Start is authorized and waiting for the runtime.",
                "claimed" =>
                    "Tournament launch is in progress under the current runtime owner.",
                "started" =>
                    receipt.Launch.Reason ?? "Tournament was started.",
                "cancelled" =>
                    receipt.Launch.Reason ?? "Automatic Tournament launch was cancelled.",
                "failed" =>
                    $"Tournament launch failed: "
                    + $"{receipt.Launch.Reason ?? "reason unavailable"}",
                _ =>
                    "Tournament validation passed before automatic launch "
                    + "confirmation was available; start manually.",
            };
        }
        var disposition = receipt.Status.ToLowerInvariant() switch
        {
            "pending" => "waiting for completed Home preflight",
            "claimed" => "ordinary New Battle ownership recorded",
            "running" => "checking Damage Slider and Ultimate Weapons",
            "cleanup" => "returning the owned validation battle to Home",
            _ => receipt.Status,
        };
        return $"Tournament validation: {disposition}.";
    }

    private static ExclusiveValidationReceiptStatus? CurrentTournamentLaunch(
        ExclusiveValidationLedgerStatus? ledger)
    {
        if (ledger is null || string.IsNullOrWhiteSpace(ledger.CurrentRequestId))
        {
            return null;
        }
        if (!ledger.Receipts.TryGetValue(ledger.CurrentRequestId, out var receipt))
        {
            return null;
        }
        return string.Equals(
                receipt.Outcome,
                "ready",
                StringComparison.OrdinalIgnoreCase)
            && receipt.Launch is not null
            ? receipt
            : null;
    }

    private static string StrategyDisplayName(string? strategy) =>
        NormalizeStrategy(strategy) switch
        {
            "farm_t18" => "Farm T18",
            "farm_t19" => "Farm T19",
            "tournament" => "Tournament",
            "none" => "No strategy",
            null => "none",
            var value => string.Join(
                " ",
                value.Split('_', StringSplitOptions.RemoveEmptyEntries)
                    .Select(part => part.Length > 1
                        && part[0] == 't'
                        && part[1..].All(char.IsDigit)
                            ? part.ToUpperInvariant()
                            : CultureInfo.InvariantCulture.TextInfo.ToTitleCase(part))),
        };

    private static string YesNo(bool? value) => value switch
    {
        true => "yes",
        false => "no",
        null => "unknown",
    };

    private static string FormatAge(int? seconds)
    {
        if (seconds is null)
        {
            return "unknown";
        }
        return seconds < 60
            ? $"{seconds}s"
            : seconds < 3600
                ? $"{seconds / 60}m"
                : $"{seconds / 3600}h {seconds % 3600 / 60}m";
    }

    private static string? FormatRunElapsed(
        CurrentRunStatus? currentRun,
        string? serverTime)
    {
        if (!DateTimeOffset.TryParse(currentRun?.StartedAt, out var startedAt)
            || !DateTimeOffset.TryParse(serverTime, out var observedAt)
            || observedAt < startedAt)
        {
            return null;
        }
        var seconds = (int)Math.Min(
            Math.Floor((observedAt - startedAt).TotalSeconds),
            int.MaxValue);
        return FormatAge(seconds);
    }

    private static string FormatPercent(double? value) =>
        value is null
            ? "-"
            : $"{value.Value:F1}%";

    private static string FormatBytes(ulong? value) =>
        value is null ? "-" : FormatByteValue(value.Value);

    private static string FormatBytes(long? value) =>
        value is null ? "-" : FormatByteValue(Math.Max(0, value.Value));

    private static string FormatByteValue(double value)
    {
        string[] units = ["B", "KiB", "MiB", "GiB", "TiB"];
        var unit = 0;
        while (value >= 1024.0 && unit < units.Length - 1)
        {
            value /= 1024.0;
            unit++;
        }
        return $"{value:F1} {units[unit]}";
    }

    private static string FormatRate(double? bytesPerSecond) =>
        bytesPerSecond is null
            ? "-"
            : $"{FormatByteValue(Math.Max(0, bytesPerSecond.Value))}/s";

    private static string FormatAutomationState(ControlStatus control)
    {
        var state = FormatStatusToken(control.State);
        return string.Equals(
                control.State,
                "PAUSED",
                StringComparison.OrdinalIgnoreCase)
            && control.RemainingSeconds is not null
                ? $"{state} · {FormatAge(control.RemainingSeconds)} left"
                : state;
    }

    private static string FormatActionAuthority(
        BetterControlModelStatus? model,
        ControlStatus control)
    {
        if (model is null)
        {
            return FormatAutomationState(control);
        }
        var label = model.ActionAuthority.Effective.ToLowerInvariant() switch
        {
            "enabled" => "Automation Enabled",
            "paused" => "Automation Paused",
            "pending" => "Authority Pending",
            "stopped" => "Automation Stopped",
            "unavailable" => "Authority Unavailable",
            _ => "Authority Unknown",
        };
        return model.ActionAuthority.Effective == "paused"
            && control.RemainingSeconds is not null
                ? $"{label} · {FormatAge(control.RemainingSeconds)} left"
                : label;
    }

    private static string FormatExecutionMode(string? value) =>
        value?.ToUpperInvariant() switch
        {
            "NEXT_BATTLE" => "Continue automatically",
            "HOME" => "Return / stay Home",
            _ => FormatStatusToken(value),
        };

    private static string FormatStrategyActionGate(
        StrategyActionGateStatus? gate)
    {
        if (gate is null || !gate.Available)
        {
            return "unavailable";
        }
        if (gate.Stale)
        {
            return $"stale ({FormatAge(gate.AgeSeconds)})";
        }
        return gate.Active
            ? $"active — {gate.Reason}"
            : "inactive";
    }

    private static string FormatGameScreen(string? stateLabel)
    {
        if (string.IsNullOrWhiteSpace(stateLabel))
        {
            return "-";
        }

        var primaryState = stateLabel.Split('/', 2)[0];
        return primaryState.ToUpperInvariant() switch
        {
            "RUNNING" => "Battle",
            "HOME_SCREEN" => "Home",
            "NEW_BATTLE" => "New battle",
            "GAME_OVER" => "Game over",
            "TOURNAMENT_SCREEN" => "Tournament",
            "BATTLE_HISTORY" => "Battle history",
            _ => FormatStatusToken(primaryState),
        };
    }

    private static string FormatStatusToken(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "-";
        }

        return CultureInfo.InvariantCulture.TextInfo.ToTitleCase(
            value.Replace('_', ' ').ToLowerInvariant());
    }

    private static string FormatObservation(ObservationStatus observation)
    {
        var wave = observation.Wave is null
            ? ""
            : $" · wave {observation.Wave.Value.ToString(CultureInfo.InvariantCulture)}";
        var observedAt = DateTimeOffset.TryParse(
            observation.ObservedAt,
            out var parsed)
            ? parsed.LocalDateTime.ToString("g", CultureInfo.CurrentCulture)
            : observation.ObservedAt ?? "-";
        return $"{FormatGameScreen(observation.StateLabel)}{wave} · {observedAt}";
    }

    private static int ParsePort(string value, string label)
    {
        if (!TryParsePort(value, out var port))
        {
            throw new ArgumentException($"{label} must be between 1 and 65535.");
        }
        return port;
    }

    private static bool TryParsePort(string value, out int port) =>
        int.TryParse(
            value.Trim(),
            NumberStyles.None,
            CultureInfo.InvariantCulture,
            out port)
        && port is >= 1 and <= 65535;

    private void SaveSettings()
    {
        SettingsStore.Save(_settings);
    }

    private void CaptureWindowPlacement()
    {
        var mainPlacement = WindowPlacementStore.Capture(this);
        if (mainPlacement is not null)
        {
            _settings.MainWindowPlacement = mainPlacement;
        }

        if (_battleHistoryWindow is not null)
        {
            var historyPlacement = WindowPlacementStore.Capture(_battleHistoryWindow);
            if (historyPlacement is not null)
            {
                _settings.BattleHistoryWindowPlacement = historyPlacement;
            }
        }
    }

    private void SaveSettingsBestEffort()
    {
        try
        {
            SettingsStore.Save(_settings);
        }
        catch (Exception exc)
        {
            LastErrorText.Text = $"Unable to save local window settings: {exc.Message}";
        }
    }

    private void ShowError(Exception exception)
    {
        LastErrorText.Text = exception.Message;
        MessageBox.Show(
            this,
            exception.Message,
            "TheTower control surface",
            MessageBoxButton.OK,
            MessageBoxImage.Error);
    }
}
