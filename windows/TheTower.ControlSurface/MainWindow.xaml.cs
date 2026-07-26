using System.Collections.ObjectModel;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace TheTower.ControlSurface;

public partial class MainWindow : Window
{
    private readonly ControlSurfaceApi _api = new();
    private readonly SshTunnelManager _sshTunnel = new();
    private readonly ClientSettings _settings;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(5) };
    private readonly DispatcherTimer _activityTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly SemaphoreSlim _refreshGate = new(1, 1);
    private readonly SemaphoreSlim _battleRefreshGate = new(1, 1);
    private readonly SemaphoreSlim _activityRefreshGate = new(1, 1);
    private readonly ObservableCollection<ActivityEntry> _activity = [];
    private BattleListResponse _latestBattles = new();
    private BattleHistoryWindow? _battleHistoryWindow;
    private CancellationTokenSource? _refreshCancellation;
    private CancellationTokenSource? _battleRefreshCancellation;
    private CancellationTokenSource? _activityRefreshCancellation;
    private ActivityEntry? _expandedActivityEntry;
    private string? _activitySourceFileId;
    private bool _startupGatePolicyDirty;
    private string _strategyRequestMessage = "";
    private bool _updatingStrategySelection;
    private bool _strategySelectionDirty;
    private bool _strategyRequestInFlight;
    private bool _strategyLifecycleAvailable;
    private bool _strategyProcessActive;
    private string? _configuredStrategy;
    private string? _currentStrategy;
    private string? _requestedStrategy;
    private string? _pendingStrategy;
    private string _strategyApplyMode = "next_boundary";
    private ControlSurfaceCompatibilityResult? _serverCompatibility;
    private bool _controlSurfaceRestartInFlight;
    private bool _automationRestartInFlight;
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

        _settings = SettingsStore.Load();
        BaseUrlBox.Text = _settings.BaseUrl;
        SshDestinationBox.Text = _settings.SshDestination;
        LocalTunnelPortBox.Text = _settings.LocalTunnelPort.ToString(CultureInfo.InvariantCulture);
        RemoteApiPortBox.Text = _settings.RemoteApiPort.ToString(CultureInfo.InvariantCulture);
        WindowPlacementStore.Restore(this, _settings.MainWindowPlacement);
        _api.Configure(_settings.BaseUrl, "");
        _sshTunnel.Exited += Tunnel_Exited;
        _timer.Tick += async (_, _) => await Task.WhenAll(
            RefreshStatusAsync(),
            RefreshBattlesAsync());
        _activityTimer.Tick += async (_, _) => await RefreshActivityAsync();
        ActivityLevelFilter.SelectionChanged += ActivityLevelFilter_SelectionChanged;
        Loaded += async (_, _) =>
        {
            _timer.Start();
            _activityTimer.Start();
            await Task.WhenAll(
                RefreshStatusAsync(),
                RefreshBattlesAsync(),
                RefreshActivityAsync());
        };
        Closing += (_, _) =>
        {
            CaptureWindowPlacement();
            SaveSettingsBestEffort();
        };
        Closed += (_, _) =>
        {
            _timer.Stop();
            _activityTimer.Stop();
            _refreshCancellation?.Cancel();
            _battleRefreshCancellation?.Cancel();
            _activityRefreshCancellation?.Cancel();
            _battleHistoryWindow?.Close();
            _sshTunnel.Dispose();
            _api.Dispose();
        };
    }

    private async void Connect_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            _api.Configure(BaseUrlBox.Text, TokenBox.Password);
            SaveSettings();
            await Task.WhenAll(
                RefreshStatusAsync(force: true),
                RefreshBattlesAsync(force: true),
                RefreshActivityAsync(force: true));
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
    }

    private void SshDestinationBox_TextChanged(
        object sender,
        TextChangedEventArgs e) => UpdateControlSurfaceCompatibility();

    private async void RestartControlSurface_Click(object sender, RoutedEventArgs e)
    {
        var destination = SshDestinationBox.Text.Trim();
        if (!SshTunnelManager.IsValidDestination(destination))
        {
            ShowError(new InvalidOperationException(
                "Enter a valid Linux SSH destination before restarting the service."));
            return;
        }
        if (MessageBox.Show(
                this,
                "Restart the fixed thetower-control-surface.service on Linux?\n\n"
                + "This briefly interrupts the control API. It does not restart "
                + "the main automation process or alter the active battle.",
                "Restart Linux control service",
                MessageBoxButton.YesNo,
                MessageBoxImage.Question) != MessageBoxResult.Yes)
        {
            return;
        }

        _controlSurfaceRestartInFlight = true;
        UpdateControlSurfaceCompatibility();
        LinuxServiceCompatibilityText.Text =
            "Restarting the fixed Linux control service over SSH...";
        try
        {
            using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(35));
            await _sshTunnel.RestartControlSurfaceServiceAsync(
                destination,
                cancellation.Token);
            LinuxServiceCompatibilityText.Text =
                "Linux service restarted; waiting for a compatible API...";
            var status = await WaitForCompatibleServerAsync(cancellation.Token);
            RenderStatus(status);
            ConnectionText.Text = "Linux service connected";
            ConnectionText.Foreground = new SolidColorBrush(Color.FromRgb(73, 214, 157));
            SaveSettings();
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            LastErrorText.Text = exc.Message;
            ShowError(exc);
        }
        finally
        {
            _controlSurfaceRestartInFlight = false;
            UpdateControlSurfaceCompatibility();
        }
    }

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

    private async void StartTunnel_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var localPort = ParsePort(LocalTunnelPortBox.Text, "Local tunnel port");
            var remotePort = ParsePort(RemoteApiPortBox.Text, "Remote API port");
            TunnelStatusText.Text = "Starting Windows OpenSSH...";
            StartTunnelButton.IsEnabled = false;
            await _sshTunnel.StartAsync(
                SshDestinationBox.Text,
                localPort,
                remotePort,
                CancellationToken.None);

            BaseUrlBox.Text = $"http://127.0.0.1:{localPort}";
            _api.Configure(BaseUrlBox.Text, TokenBox.Password);

            using var probeCancellation = new CancellationTokenSource(TimeSpan.FromSeconds(12));
            var status = await _api.GetStatusAsync(probeCancellation.Token);
            RenderStatus(status);
            SaveSettings();
            TunnelStatusText.Text = $"Connected: localhost:{localPort} -> {SshDestinationBox.Text.Trim()}:{remotePort}";
            TunnelStatusText.Foreground = new SolidColorBrush(Color.FromRgb(73, 214, 157));
            ConnectionText.Text = "Linux service connected";
            ConnectionText.Foreground = new SolidColorBrush(Color.FromRgb(73, 214, 157));
            StopTunnelButton.IsEnabled = true;
            await Task.WhenAll(
                RefreshBattlesAsync(force: true),
                RefreshActivityAsync(force: true));
        }
        catch (Exception exc)
        {
            var tunnelRunning = _sshTunnel.IsRunning;
            StartTunnelButton.IsEnabled = !tunnelRunning;
            StopTunnelButton.IsEnabled = tunnelRunning;
            TunnelStatusText.Text = tunnelRunning
                ? $"Tunnel running, but the Linux API is unavailable: {exc.Message}"
                : exc.Message;
            TunnelStatusText.Foreground = new SolidColorBrush(Color.FromRgb(255, 113, 135));
            ConnectionText.Text = "Tunnel API unavailable";
            ConnectionText.Foreground = new SolidColorBrush(Color.FromRgb(255, 113, 135));
            ShowError(exc);
        }
    }

    private async void StopTunnel_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            StopTunnelButton.IsEnabled = false;
            await _sshTunnel.StopAsync();
            TunnelStatusText.Text = "Stopped";
            TunnelStatusText.Foreground = (Brush)FindResource("MutedBrush");
            ConnectionText.Text = "Tunnel stopped";
            ConnectionText.Foreground = new SolidColorBrush(Color.FromRgb(241, 191, 91));
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
        finally
        {
            StartTunnelButton.IsEnabled = true;
        }
    }

    private void Tunnel_Exited(object? sender, TunnelExitedEventArgs args)
    {
        _ = Dispatcher.InvokeAsync(() =>
        {
            StartTunnelButton.IsEnabled = true;
            StopTunnelButton.IsEnabled = false;
            TunnelStatusText.Text = args.Message;
            TunnelStatusText.Foreground = args.Expected
                ? (Brush)FindResource("MutedBrush")
                : new SolidColorBrush(Color.FromRgb(255, 113, 135));
            if (!args.Expected)
            {
                LastErrorText.Text = args.Message;
                ConnectionText.Text = "Tunnel exited";
                ConnectionText.Foreground = new SolidColorBrush(Color.FromRgb(255, 113, 135));
            }
        });
    }

    private async void Control_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string tag)
        {
            return;
        }
        try
        {
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
                response = await _api.PostControlAsync(
                    new { action = tag },
                    CancellationToken.None);
            }
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
    }

    private async void Mode_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string mode)
        {
            return;
        }
        try
        {
            var response = await _api.PostControlAsync(
                new { action = "mode", mode },
                CancellationToken.None);
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            ShowError(exc);
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

    private void StrategySelectionBox_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_updatingStrategySelection)
        {
            return;
        }

        _strategySelectionDirty = true;
        _strategyRequestMessage = "";
        UpdateStrategyActionAvailability();
    }

    private async void QueueStrategy_Click(object sender, RoutedEventArgs e) =>
        await SubmitSelectedStrategyAsync(adoptActiveBattle: false);

    private async void AdoptStrategy_Click(object sender, RoutedEventArgs e) =>
        await SubmitSelectedStrategyAsync(adoptActiveBattle: true);

    private async Task SubmitSelectedStrategyAsync(bool adoptActiveBattle)
    {
        var strategy = SelectedStrategy();
        if (strategy is null)
        {
            return;
        }

        _strategyRequestInFlight = true;
        UpdateStrategyActionAvailability();
        try
        {
            var action = adoptActiveBattle
                ? "active-battle adoption"
                : _strategyProcessActive ? "boundary queue" : "next-start save";
            StrategySelectionText.Text =
                $"Sending {StrategyDisplayName(strategy)} {action} request...";
            object payload = adoptActiveBattle
                ? new { action = "set_strategy", strategy, apply_to_active_run = true }
                : new { action = "set_strategy", strategy };
            var response = await _api.PostProcessAsync(
                payload,
                CancellationToken.None);
            if (response.Request is { Accepted: true } request)
            {
                var requested = NormalizeStrategy(request.Strategy) ?? strategy;
                var requestedLabel = StrategyDisplayName(requested);
                _strategyRequestMessage = request.Disposition switch
                {
                    "queued" => $"Accepted {requestedLabel}; queued for the next confirmed run boundary.",
                    "saved" => $"Accepted {requestedLabel}; saved for the next process start.",
                    "active_battle_requested" => $"Accepted {requestedLabel}; waiting for active-battle adoption.",
                    _ => $"Accepted {requestedLabel} strategy request.",
                };
                _strategySelectionDirty = false;
                if (!string.IsNullOrWhiteSpace(request.Warning))
                {
                    _strategyRequestMessage += $" Audit warning: {request.Warning}";
                }
            }
            RenderStatus(response);
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            _strategyRequestMessage = $"Strategy request was not accepted: {exc.Message}";
            ShowError(exc);
            await RefreshStatusAsync(force: true);
        }
        finally
        {
            _strategyRequestInFlight = false;
            UpdateStrategyActionAvailability();
        }
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

        try
        {
            StatusResponse response;
            if (tag.StartsWith("start:", StringComparison.Ordinal))
            {
                var runState = tag.Split(':')[1];
                var strategy = SelectedStrategy()
                    ?? throw new InvalidOperationException(
                        "Select a strategy before starting automation.");
                var startupGatePolicy = AttachCurrentBattleBox.IsChecked == true
                    ? "next_run"
                    : "immediate";
                response = await _api.PostProcessAsync(
                    new
                    {
                        action = "start",
                        run_state = runState,
                        startup_gate_policy = startupGatePolicy,
                        strategy,
                    },
                    CancellationToken.None);
                _startupGatePolicyDirty = false;
                _strategySelectionDirty = false;
                _strategyRequestMessage =
                    $"Started with selected {StrategyDisplayName(strategy)} strategy.";
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

    private async void ReloadAutomation_Click(object sender, RoutedEventArgs e)
    {
        if (MessageBox.Show(
                this,
                "Reload the main Python automation process for the current battle?\n\n"
                + "Automation will pause, start a replacement in attachment mode, "
                + "verify its PID, lock, startup policy, control acknowledgement, "
                + "and first observation, then restore the current Running or "
                + "Paused state. Startup and session gates remain deferred until "
                + "the next battle boundary.",
                "Reload automation",
                MessageBoxButton.YesNo,
                MessageBoxImage.Question) != MessageBoxResult.Yes)
        {
            return;
        }

        _automationRestartInFlight = true;
        ReloadAutomationButton.IsEnabled = false;
        ControlSelectionText.Text =
            "Pausing and replacing the main automation process...";
        try
        {
            using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(120));
            var response = await _api.PostProcessAsync(
                new { action = "restart_attached" },
                cancellation.Token);
            RenderStatus(response);
            var request = response.Request;
            ControlSelectionText.Text = request is null
                ? "Automation reload completed."
                : $"Automation reloaded: PID {request.PreviousPid} → "
                    + $"{request.ReplacementPid}; restored {request.RestoredState}.";
            await RefreshActivityAsync(force: true);
        }
        catch (Exception exc)
        {
            LastErrorText.Text = exc.Message;
            ShowError(exc);
            await RefreshStatusAsync(force: true);
        }
        finally
        {
            _automationRestartInFlight = false;
        }
    }

    private void StartupGatePolicy_Click(object sender, RoutedEventArgs e) =>
        _startupGatePolicyDirty = true;

    private async void SetAdbPort_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var adbPort = ParsePort(AdbPortBox.Text, "ADB port");
            var response = await _api.PostProcessAsync(
                new { action = "set_adb_port", adb_port = adbPort },
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

    private async Task RefreshStatusAsync(bool force = false)
    {
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
            ConnectionText.Text = "Linux service connected";
            ConnectionText.Foreground = new SolidColorBrush(Color.FromRgb(73, 214, 157));
        }
        catch (OperationCanceledException)
        {
            if (!force)
            {
                ConnectionText.Text = "Connection timed out";
                ConnectionText.Foreground = new SolidColorBrush(Color.FromRgb(241, 191, 91));
            }
        }
        catch (Exception exc)
        {
            ConnectionText.Text = "Connection failed";
            ConnectionText.Foreground = new SolidColorBrush(Color.FromRgb(255, 113, 135));
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
            ActivityStatusText.Text = selectionResetMessage is null
                ? $"Updated {DateTime.Now:T} | {_activity.Count} shown"
                : $"{selectionResetMessage} | {_activity.Count} shown";
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

    private async void RefreshActivity_Click(object sender, RoutedEventArgs e) =>
        await RefreshActivityAsync(force: true);

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

    private void RenderStatus(StatusResponse status)
    {
        _serverCompatibility = ControlSurfaceCompatibility.Evaluate(status);
        UpdateControlSurfaceCompatibility();
        DirectiveText.Text = status.Control.State;
        ModeText.Text = status.Control.Mode;
        ObservedStateText.Text = status.Observation?.StateLabel ?? "-";
        WaveText.Text = status.Observation?.Wave?.ToString(CultureInfo.InvariantCulture) ?? "-";
        CoinsMinuteText.Text = status.Observation?.CoinsPerMinute ?? "-";
        HeartbeatText.Text = status.Observation is null
            ? "Missing"
            : status.Observation.Stale
                ? $"Stale ({FormatAge(status.Observation.AgeSeconds)})"
                : $"Fresh ({FormatAge(status.Observation.AgeSeconds)})";

        var runtime = status.Runtime.Instances.FirstOrDefault(instance => instance.Active)
            ?? status.Runtime.Instances.FirstOrDefault();
        var service = status.ProcessService;
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
        StartPausedButton.IsEnabled = lifecycleAvailable
            && !processActive
            && _serverCompatibility.IsCompatible;
        StartRunningButton.IsEnabled = lifecycleAvailable
            && !processActive
            && _serverCompatibility.IsCompatible;
        AttachCurrentBattleBox.IsEnabled = lifecycleAvailable && !processActive;
        CompleteStopButton.IsEnabled = lifecycleAvailable && service?.Active == true;
        ReloadAutomationButton.IsEnabled = lifecycleAvailable
            && service?.Active == true
            && _serverCompatibility?.IsCompatible == true
            && (status.Observation is null
                || status.Observation.Stale
                || status.Observation.StateLabel.StartsWith(
                    "RUNNING",
                    StringComparison.OrdinalIgnoreCase))
            && !_automationRestartInFlight;
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
        _currentGateDecision = status.Control.GateDecision;
        var pendingGate = status.Control.GateDecision is
            { Status: "pending" } gate ? gate : null;
        GateDecisionButton.IsEnabled = pendingGate is not null;
        GateDecisionText.Text = status.Control.GateDecision switch
        {
            { Status: "pending" } decision =>
                $"{decision.CheckId}: {decision.Reason}",
            { Status: "resolved" } decision =>
                $"{decision.CheckId}: {decision.DecisionId}; waiting for runtime.",
            _ => "No preflight decision is waiting for direction.",
        };
        if (pendingGate is not null
            && pendingGate.RequestId != _autoPromptedGateRequestId)
        {
            _autoPromptedGateRequestId = pendingGate.RequestId;
            Dispatcher.BeginInvoke(new Action(async () =>
                await ShowGateDecisionAsync(pendingGate)));
        }
        SetAdbPortButton.IsEnabled = lifecycleAvailable
            && (!processActive || pausedAndAcknowledged);
        SetAdbPortButton.Content = processActive ? "Switch" : "Save";
        AdbPortHelpText.Text = !processActive
            ? "The ADB port is saved for the next automation start."
            : pausedAndAcknowledged
                ? "Switches the live runtime in place; it remains paused and does not rerun startup gates."
                : "Indefinitely pause automation and wait for its acknowledgement before switching the live ADB port.";
        if (!AdbPortBox.IsKeyboardFocusWithin && service?.AdbPort is not null)
        {
            AdbPortBox.Text = service.AdbPort.Value.ToString(CultureInfo.InvariantCulture);
        }
        if (!_startupGatePolicyDirty && !processActive)
        {
            AttachCurrentBattleBox.IsChecked = string.Equals(
                service?.StartupGatePolicy,
                "next_run",
                StringComparison.OrdinalIgnoreCase);
        }

        var statePending = processActive
            && status.Acknowledgements.State is not { AcknowledgesCurrent: true };
        var modePending = processActive
            && status.Acknowledgements.Mode is not { AcknowledgesCurrent: true };
        var adbTargetPending = processActive
            && status.Control.AdbPort is not null
            && status.Acknowledgements.AdbTarget is not { AcknowledgesCurrent: true };
        SetSelectionStyle(
            PauseButton,
            string.Equals(status.Control.State, "PAUSED", StringComparison.OrdinalIgnoreCase),
            statePending);
        SetSelectionStyle(
            ResumeButton,
            string.Equals(status.Control.State, "RUNNING", StringComparison.OrdinalIgnoreCase),
            statePending);
        SetSelectionStyle(
            RetryModeButton,
            string.Equals(status.Control.Mode, "RETRY", StringComparison.OrdinalIgnoreCase),
            modePending);
        SetSelectionStyle(
            WaitModeButton,
            string.Equals(status.Control.Mode, "WAIT", StringComparison.OrdinalIgnoreCase),
            modePending);
        SetSelectionStyle(
            HomeModeButton,
            string.Equals(status.Control.Mode, "HOME", StringComparison.OrdinalIgnoreCase),
            modePending);

        var configuredStrategy = NormalizeStrategy(service?.Strategy);
        var requestedStrategy = NormalizeStrategy(status.Control.Strategy)
            ?? configuredStrategy;
        var strategyPending = processActive
            && status.Control.Strategy is not null
            && status.Acknowledgements.Strategy is not { AcknowledgesCurrent: true };
        var currentStrategy = !processActive
            ? null
            : status.Control.Strategy is null
                ? configuredStrategy
                : NormalizeStrategy(status.Acknowledgements.Strategy?.Value);
        var pendingStrategy = strategyPending ? requestedStrategy : null;
        var pendingStrategyLabel = strategyPending && string.Equals(
            status.Control.StrategyApplyMode,
            "active_battle",
            StringComparison.OrdinalIgnoreCase)
            ? "Pending active adoption"
            : "Pending boundary";
        _strategyLifecycleAvailable = lifecycleAvailable;
        _strategyProcessActive = processActive;
        _configuredStrategy = configuredStrategy;
        _currentStrategy = currentStrategy;
        _requestedStrategy = requestedStrategy;
        _pendingStrategy = pendingStrategy;
        _strategyApplyMode = status.Control.StrategyApplyMode;
        if (!_strategySelectionDirty)
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
        var strategyState = !processActive
            ? $"Process inactive | Next start: {configuredStrategyLabel}"
            : $"Current: {currentStrategyLabel} | "
                + $"{pendingStrategyLabel}: {StrategyDisplayName(pendingStrategy)}";
        strategyState += $" | Selected: {StrategyDisplayName(selectedStrategy)}";
        StrategySelectionText.Text = string.IsNullOrWhiteSpace(_strategyRequestMessage)
            ? strategyState
            : $"{strategyState} | {_strategyRequestMessage}";
        TournamentValidationText.Text = FormatExclusiveValidation(
            status.Control.ExclusiveValidation);
        _currentTournamentLaunch = CurrentTournamentLaunch(
            status.Control.ExclusiveValidation);
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
        var stateDisposition = !processActive
            ? "saved; process inactive"
            : statePending ? "awaiting runtime" : "active directive";
        var modeDisposition = !processActive
            ? "saved for next terminal"
            : modePending ? "awaiting runtime" : "active directive";
        var requestedAdbTarget = status.Control.AdbPort is not null
            ? $"localhost:{status.Control.AdbPort.Value}"
            : service?.AdbTarget ?? "unknown";
        var adbDisposition = adbTargetPending
            ? "handoff pending"
            : processActive ? "active" : "next start";
        ControlSelectionText.Text =
            $"State: {status.Control.State} ({stateDisposition}) | "
            + $"Mode: {status.Control.Mode} ({modeDisposition}) | "
            + $"ADB target: {requestedAdbTarget} ({adbDisposition}) | "
            + $"Startup gates: {service?.StartupGatePolicy ?? "unknown"} | "
            + $"One-run skips: {configuredSkips.Count} | "
            + $"Gate decision: {status.Control.GateDecision?.Status ?? "none"}";

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
                $"{pendingStrategyLabel}: {pendingStrategy ?? "-"}",
                $"Strategy request mode: {status.Control.StrategyApplyMode}",
                $"Configured next-start strategy: {service?.Strategy ?? "-"}",
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

    private void RenderBattles(BattleListResponse response)
    {
        _latestBattles = response;
        _battleHistoryWindow?.UpdateBattles(response);

        var battle = response.Items.FirstOrDefault();
        if (battle is null)
        {
            LatestBattleTitleText.Text = "No completed battles found";
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
    }

    private void RenderActivity(ActivityResponse response)
    {
        CollapseExpandedActivity();
        _activitySourceFileId = response.SourceFileId;
        _activity.Clear();
        foreach (var entry in response.Items.AsEnumerable().Reverse())
        {
            _activity.Add(entry);
        }
        if (ActivityAutoFollowBox.IsChecked == true && _activity.Count > 0)
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
        var hasPending = _pendingStrategy is not null;
        var queueAlreadyRequested = _strategyProcessActive
            ? hasPending
                ? string.Equals(
                    selected,
                    _requestedStrategy,
                    StringComparison.OrdinalIgnoreCase)
                    && string.Equals(
                        _strategyApplyMode,
                        "next_boundary",
                        StringComparison.OrdinalIgnoreCase)
                : string.Equals(
                    selected,
                    _currentStrategy,
                    StringComparison.OrdinalIgnoreCase)
            : string.Equals(
                selected,
                _configuredStrategy,
                StringComparison.OrdinalIgnoreCase);
        var adoptionAlreadyRequested = hasPending
            && string.Equals(
                selected,
                _requestedStrategy,
                StringComparison.OrdinalIgnoreCase)
            && string.Equals(
                _strategyApplyMode,
                "active_battle",
                StringComparison.OrdinalIgnoreCase);

        StrategySelectionBox.IsEnabled =
            _strategyLifecycleAvailable && !_strategyRequestInFlight;
        QueueStrategyButton.Content = _strategyProcessActive
            ? "Queue for next boundary"
            : "Save for next start";
        QueueStrategyButton.IsEnabled = _strategyLifecycleAvailable
            && hasSelection
            && !queueAlreadyRequested
            && !_strategyRequestInFlight;
        AdoptStrategyButton.IsEnabled = _strategyLifecycleAvailable
            && _strategyProcessActive
            && _serverCompatibility?.IsCompatible == true
            && hasSelection
            && !string.Equals(
                selected,
                _currentStrategy,
                StringComparison.OrdinalIgnoreCase)
            && !adoptionAlreadyRequested
            && !_strategyRequestInFlight;
    }

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
            return;
        }

        var destinationValid = SshTunnelManager.IsValidDestination(
            SshDestinationBox.Text);
        var incompatibility =
            "Linux control service is older than or incompatible with this Windows client ("
            + DescribeCompatibilityProblems(_serverCompatibility)
            + "). Update or restart the Linux control service.";
        LinuxServiceCompatibilityText.Text = destinationValid
            ? incompatibility
            : incompatibility
                + " Enter an SSH destination to enable the fixed-service restart.";
        LinuxServiceCompatibilityText.Foreground =
            new SolidColorBrush(Color.FromRgb(241, 191, 91));
        RestartControlSurfaceButton.Visibility = Visibility.Visible;
        RestartControlSurfaceButton.IsEnabled =
            destinationValid && !_controlSurfaceRestartInFlight;
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
            "farm_t19_experiment" => "farm_t19_experiment",
            "tournament" => "tournament",
            "none" => "none",
            _ => strategy,
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
            "farm_t19_experiment" => "Farm T19 experiment",
            "tournament" => "Tournament",
            "none" => "No strategy",
            null => "none",
            var value => value,
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

    private static int ParsePort(string value, string label)
    {
        if (!int.TryParse(value.Trim(), NumberStyles.None, CultureInfo.InvariantCulture, out var port)
            || port is < 1 or > 65535)
        {
            throw new ArgumentException($"{label} must be between 1 and 65535.");
        }
        return port;
    }

    private void SaveSettings()
    {
        var localPort = ParsePort(LocalTunnelPortBox.Text, "Local tunnel port");
        var remotePort = ParsePort(RemoteApiPortBox.Text, "Remote API port");
        _settings.BaseUrl = BaseUrlBox.Text.Trim();
        _settings.SshDestination = SshDestinationBox.Text.Trim();
        _settings.LocalTunnelPort = localPort;
        _settings.RemoteApiPort = remotePort;
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
