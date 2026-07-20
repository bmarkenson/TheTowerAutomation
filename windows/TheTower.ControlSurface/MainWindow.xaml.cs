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
    private bool _startupGatePolicyDirty;
    private string _strategyRequestMessage = "";

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

    private async void Strategy_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string strategy)
        {
            return;
        }
        try
        {
            StrategySelectionText.Text = $"Sending {strategy} strategy request...";
            var response = await _api.PostProcessAsync(
                new { action = "set_strategy", strategy },
                CancellationToken.None);
            if (response.Request is { Accepted: true } request)
            {
                var requested = NormalizeStrategy(request.Strategy) ?? strategy;
                _strategyRequestMessage = request.Disposition switch
                {
                    "queued" => $"Accepted {requested}; queued for the next confirmed run boundary.",
                    "saved" => $"Accepted {requested}; saved for the next process start.",
                    _ => $"Accepted {requested} strategy request.",
                };
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
                var startupGatePolicy = AttachCurrentBattleBox.IsChecked == true
                    ? "next_run"
                    : "immediate";
                response = await _api.PostProcessAsync(
                    new
                    {
                        action = "start",
                        run_state = runState,
                        startup_gate_policy = startupGatePolicy,
                    },
                    CancellationToken.None);
                _startupGatePolicyDirty = false;
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
            if (ActivityGrid.SelectedItems.Count > 0)
            {
                ActivityStatusText.Text =
                    $"Selection held ({ActivityGrid.SelectedItems.Count}); copy or clear it to resume";
                return;
            }
            RenderActivity(response);
            ActivityStatusText.Text =
                $"Updated {DateTime.Now:T} | {_activity.Count} shown";
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
        CopyActivityButton.IsEnabled = ActivityGrid.SelectedItems.Count > 0;
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
        StartPausedButton.IsEnabled = lifecycleAvailable && !processActive;
        StartRunningButton.IsEnabled = lifecycleAvailable && !processActive;
        AttachCurrentBattleBox.IsEnabled = lifecycleAvailable && !processActive;
        CompleteStopButton.IsEnabled = lifecycleAvailable && service?.Active == true;
        var pausedAndAcknowledged = processActive
            && string.Equals(
                status.Control.State,
                "PAUSED",
                StringComparison.OrdinalIgnoreCase)
            && status.Control.RemainingSeconds is null
            && status.Acknowledgements.State?.AcknowledgesCurrent == true;
        SetAdbPortButton.IsEnabled = lifecycleAvailable
            && (!processActive || pausedAndAcknowledged);
        SetAdbPortButton.Content = processActive ? "Switch" : "Save";
        AdbPortHelpText.Text = !processActive
            ? "The ADB port is saved for the next automation start."
            : pausedAndAcknowledged
                ? "Switches the live runtime in place; it remains paused and does not rerun startup gates."
                : "Indefinitely pause automation and wait for its acknowledgement before switching the live ADB port.";
        FarmT18StrategyButton.IsEnabled = lifecycleAvailable;
        FarmT19StrategyButton.IsEnabled = lifecycleAvailable;
        TournamentStrategyButton.IsEnabled = lifecycleAvailable;
        NoStrategyButton.IsEnabled = lifecycleAvailable;
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
        SetStrategySelectionStyle(
            FarmT18StrategyButton,
            "farm_t18",
            processActive,
            configuredStrategy,
            currentStrategy,
            pendingStrategy);
        SetStrategySelectionStyle(
            FarmT19StrategyButton,
            "farm_t19_experiment",
            processActive,
            configuredStrategy,
            currentStrategy,
            pendingStrategy);
        SetStrategySelectionStyle(
            TournamentStrategyButton,
            "tournament",
            processActive,
            configuredStrategy,
            currentStrategy,
            pendingStrategy);
        SetStrategySelectionStyle(
            NoStrategyButton,
            "none",
            processActive,
            configuredStrategy,
            currentStrategy,
            pendingStrategy);
        var strategyState = !processActive
            ? $"Process inactive | Next start: {configuredStrategy ?? "unknown"}"
            : $"Current: {currentStrategy ?? "awaiting runtime evidence"} | "
                + $"Pending: {pendingStrategy ?? "none"}";
        StrategySelectionText.Text = string.IsNullOrWhiteSpace(_strategyRequestMessage)
            ? strategyState
            : $"{strategyState} | {_strategyRequestMessage}";
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
            + $"Startup gates: {service?.StartupGatePolicy ?? "unknown"}";

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
                $"Pending boundary strategy: {pendingStrategy ?? "-"}",
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

    private void SetStrategySelectionStyle(
        Button button,
        string strategy,
        bool processActive,
        string? configuredStrategy,
        string? currentStrategy,
        string? pendingStrategy)
    {
        var pending = string.Equals(
            pendingStrategy,
            strategy,
            StringComparison.OrdinalIgnoreCase);
        var selected = pending || string.Equals(
            processActive ? currentStrategy : configuredStrategy,
            strategy,
            StringComparison.OrdinalIgnoreCase);
        SetSelectionStyle(button, selected, pending);
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
