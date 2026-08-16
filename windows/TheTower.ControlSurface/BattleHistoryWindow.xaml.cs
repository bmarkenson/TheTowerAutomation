using System.Collections.ObjectModel;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Threading;
using Microsoft.Win32;

namespace TheTower.ControlSurface;

public partial class BattleHistoryWindow : Window
{
    private readonly ControlSurfaceApi _api;
    private readonly ObservableCollection<BattleSummary> _battles = [];
    private readonly ObservableCollection<ReportRow> _reportRows = [];
    private readonly ObservableCollection<ReportSection> _reportSections = [];
    private readonly ObservableCollection<ReportRow> _settingsRows = [];
    private readonly ObservableCollection<PerkRow> _perks = [];
    private readonly ObservableCollection<string> _strategyOptions = ["All"];
    private readonly System.ComponentModel.ICollectionView _battleView;
    private CancellationTokenSource? _detailCancellation;
    private BattleListResponse? _pendingBattleResponse;
    private bool _updatingBattles;
    private string? _selectedBattleId;
    private string? _loadedBattleId;
    private string _typeFilter = "all";
    private int? _tierFilter;
    private int? _minWaveFilter;
    private int? _maxWaveFilter;
    private string _strategyFilter = "all";
    private string _qualityFilter = "all";

    public BattleHistoryWindow(ControlSurfaceApi api)
    {
        InitializeComponent();
        _api = api;
        _battleView = CollectionViewSource.GetDefaultView(_battles);
        _battleView.Filter = BattleMatchesFilter;
        BattlesGrid.ItemsSource = _battleView;
        ReportTree.ItemsSource = _reportSections;
        SettingsGrid.ItemsSource = _settingsRows;
        PerksGrid.ItemsSource = _perks;
        StrategyFilterBox.ItemsSource = _strategyOptions;
        StrategyFilterBox.SelectedIndex = 0;
        Closed += (_, _) => _detailCancellation?.Cancel();
    }

    public bool UpdateBattles(BattleListResponse response)
    {
        if (AnyBattleFilterDropDownOpen())
        {
            _pendingBattleResponse = response;
            return false;
        }

        _pendingBattleResponse = null;
        ApplyBattleUpdate(response);
        return true;
    }

    private void ApplyBattleUpdate(BattleListResponse response)
    {
        if (BattleListsEqual(_battles, response.Items))
        {
            return;
        }

        var requestedId = (BattlesGrid.SelectedItem as BattleSummary)?.BattleId
            ?? _selectedBattleId;
        _updatingBattles = true;
        try
        {
            _battles.Clear();
            foreach (var battle in response.Items)
            {
                _battles.Add(battle);
            }
            _battleView.Refresh();
            UpdateStrategyFilterOptions();
            UpdateFilterCount();

            var selected = requestedId is null
                ? _battleView.Cast<BattleSummary>().FirstOrDefault()
                : _battleView.Cast<BattleSummary>()
                    .FirstOrDefault(battle => battle.BattleId == requestedId)
                    ?? _battleView.Cast<BattleSummary>().FirstOrDefault();
            if (!ReferenceEquals(BattlesGrid.SelectedItem, selected))
            {
                BattlesGrid.SelectedItem = selected;
            }
            _selectedBattleId = selected?.BattleId;
            if (selected is not null && selected.BattleId != _loadedBattleId)
            {
                _ = LoadBattleAsync(selected);
            }
            else if (selected is not null)
            {
                RenderBattleBanner(selected);
            }
            else if (selected is null)
            {
                ClearBattleReport();
            }
        }
        finally
        {
            _updatingBattles = false;
            UpdateDiscardButton();
        }
    }

    private void UpdateStrategyFilterOptions()
    {
        var selected = SelectedText(StrategyFilterBox);
        var strategies = _battles
            .Select(battle => battle.StrategyDisplay)
            .Where(strategy => !string.IsNullOrWhiteSpace(strategy) && strategy != "-")
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(strategy => strategy, StringComparer.OrdinalIgnoreCase)
            .ToList();
        PreserveStrategyOption(strategies, selected);
        PreserveStrategyOption(strategies, _strategyFilter);
        strategies.Sort(StringComparer.OrdinalIgnoreCase);

        var desired = new[] { "All" }.Concat(strategies).ToList();
        if (!_strategyOptions.SequenceEqual(desired, StringComparer.Ordinal))
        {
            _strategyOptions.Clear();
            foreach (var strategy in desired)
            {
                _strategyOptions.Add(strategy);
            }
        }

        StrategyFilterBox.SelectedItem = desired.FirstOrDefault(
            strategy => string.Equals(strategy, selected, StringComparison.OrdinalIgnoreCase))
            ?? "All";
    }

    private static void PreserveStrategyOption(ICollection<string> strategies, string strategy)
    {
        if (string.IsNullOrWhiteSpace(strategy)
            || string.Equals(strategy, "all", StringComparison.OrdinalIgnoreCase)
            || strategies.Contains(strategy, StringComparer.OrdinalIgnoreCase))
        {
            return;
        }
        strategies.Add(strategy);
    }

    private static bool BattleListsEqual(
        IReadOnlyList<BattleSummary> current,
        IReadOnlyList<BattleSummary> incoming)
    {
        if (current.Count != incoming.Count)
        {
            return false;
        }
        for (var index = 0; index < current.Count; index++)
        {
            if (!BattleSummariesEqual(current[index], incoming[index]))
            {
                return false;
            }
        }
        return true;
    }

    private static bool BattleSummariesEqual(BattleSummary left, BattleSummary right) =>
        string.Equals(left.BattleId, right.BattleId, StringComparison.Ordinal)
        && string.Equals(left.CapturedAt, right.CapturedAt, StringComparison.Ordinal)
        && string.Equals(left.Strategy, right.Strategy, StringComparison.Ordinal)
        && string.Equals(left.BattleType, right.BattleType, StringComparison.Ordinal)
        && string.Equals(left.BattleTypeLabel, right.BattleTypeLabel, StringComparison.Ordinal)
        && string.Equals(
            left.BattleTypeConfidence,
            right.BattleTypeConfidence,
            StringComparison.Ordinal)
        && string.Equals(left.Profile, right.Profile, StringComparison.Ordinal)
        && left.Tier == right.Tier
        && left.Wave == right.Wave
        && string.Equals(left.KilledBy, right.KilledBy, StringComparison.Ordinal)
        && string.Equals(left.League, right.League, StringComparison.Ordinal)
        && left.Rank == right.Rank
        && string.Equals(left.RealTime, right.RealTime, StringComparison.Ordinal)
        && string.Equals(left.CoinsEarned, right.CoinsEarned, StringComparison.Ordinal)
        && string.Equals(left.CoinsPerHour, right.CoinsPerHour, StringComparison.Ordinal)
        && string.Equals(left.CellsEarned, right.CellsEarned, StringComparison.Ordinal)
        && string.Equals(left.CellsPerHour, right.CellsPerHour, StringComparison.Ordinal)
        && left.Quality?.Valid == right.Quality?.Valid;

    private void BattleFilter_DropDownClosed(object sender, EventArgs e)
    {
        _ = Dispatcher.BeginInvoke(ApplyPendingBattleUpdate, DispatcherPriority.ContextIdle);
    }

    private void ApplyPendingBattleUpdate()
    {
        if (_pendingBattleResponse is null || AnyBattleFilterDropDownOpen())
        {
            return;
        }

        var response = _pendingBattleResponse;
        _pendingBattleResponse = null;
        ApplyBattleUpdate(response);
        if (HistoryStatusText.Text == "Update pending while a filter menu is open")
        {
            HistoryStatusText.Text = "Updated";
        }
    }

    private bool AnyBattleFilterDropDownOpen() =>
        BattleTypeFilter.IsDropDownOpen
        || StrategyFilterBox.IsDropDownOpen
        || QualityFilter.IsDropDownOpen;

    private async void Refresh_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            HistoryStatusText.Text = "Refreshing...";
            var response = await _api.GetBattlesAsync(CancellationToken.None);
            HistoryStatusText.Text = UpdateBattles(response)
                ? "Updated"
                : "Update pending while a filter menu is open";
        }
        catch (Exception exc)
        {
            HistoryStatusText.Text = exc.Message;
            ShowError(exc);
        }
    }

    private void ApplyBattleFilters_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            _typeFilter = SelectedText(BattleTypeFilter).ToLowerInvariant();
            _qualityFilter = SelectedText(QualityFilter).ToLowerInvariant();
            _tierFilter = ParseOptionalNonNegativeInteger(TierFilterBox.Text, "Tier");
            _minWaveFilter = ParseOptionalNonNegativeInteger(MinWaveFilterBox.Text, "Minimum wave");
            _maxWaveFilter = ParseOptionalNonNegativeInteger(MaxWaveFilterBox.Text, "Maximum wave");
            if (_minWaveFilter is not null
                && _maxWaveFilter is not null
                && _minWaveFilter > _maxWaveFilter)
            {
                throw new ArgumentException("Minimum wave cannot be greater than maximum wave.");
            }
            _strategyFilter = SelectedText(StrategyFilterBox);
            _battleView.Refresh();
            UpdateFilterCount();
            EnsureFilteredSelection();
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
    }

    private void ClearBattleFilters_Click(object sender, RoutedEventArgs e)
    {
        BattleTypeFilter.SelectedIndex = 0;
        QualityFilter.SelectedIndex = 0;
        TierFilterBox.Clear();
        MinWaveFilterBox.Clear();
        MaxWaveFilterBox.Clear();
        StrategyFilterBox.SelectedIndex = 0;
        _typeFilter = "all";
        _qualityFilter = "all";
        _tierFilter = null;
        _minWaveFilter = null;
        _maxWaveFilter = null;
        _strategyFilter = "all";
        _battleView.Refresh();
        UpdateFilterCount();
        EnsureFilteredSelection();
    }

    private bool BattleMatchesFilter(object item)
    {
        if (item is not BattleSummary battle)
        {
            return false;
        }
        if (_typeFilter != "all"
            && !string.Equals(battle.BattleType, _typeFilter, StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        if (_tierFilter is not null && battle.Tier != _tierFilter)
        {
            return false;
        }
        if (_minWaveFilter is not null
            && (battle.Wave is null || battle.Wave.Value < _minWaveFilter.Value))
        {
            return false;
        }
        if (_maxWaveFilter is not null
            && (battle.Wave is null || battle.Wave.Value > _maxWaveFilter.Value))
        {
            return false;
        }
        if (_strategyFilter != "all"
            && !string.Equals(
                battle.StrategyDisplay,
                _strategyFilter,
                StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        return _qualityFilter switch
        {
            "valid" => battle.Quality.Valid == true,
            "review" => battle.Quality.Valid != true,
            _ => true,
        };
    }

    private void UpdateFilterCount()
    {
        var filteredCount = _battleView.Cast<object>().Count();
        FilterCountText.Text = $"{filteredCount} of {_battles.Count}";
        ExportCsvButton.IsEnabled = filteredCount > 0;
        UpdateDiscardButton();
    }

    private void UpdateDiscardButton()
    {
        DiscardBattleButton.IsEnabled =
            !_updatingBattles
            && BattlesGrid.SelectedItem is BattleSummary;
    }

    private void EnsureFilteredSelection()
    {
        if (BattlesGrid.SelectedItem is BattleSummary selected
            && _battleView.Cast<BattleSummary>().Contains(selected))
        {
            return;
        }

        var first = _battleView.Cast<BattleSummary>().FirstOrDefault();
        BattlesGrid.SelectedItem = first;
        if (first is null)
        {
            ClearBattleReport();
        }
        UpdateDiscardButton();
    }

    private void ClearBattleReport()
    {
        _detailCancellation?.Cancel();
        _detailCancellation?.Dispose();
        _detailCancellation = null;
        _selectedBattleId = null;
        _loadedBattleId = null;
        DiscardBattleButton.IsEnabled = false;
        BattleTitleText.Text = "No completed battle matches the current filters";
        ReportTypeText.Text = "-";
        ReportTierText.Text = "-";
        ReportWaveText.Text = "-";
        ReportTimeText.Text = "-";
        ReportCoinsText.Text = "-";
        ReportCoinsHourText.Text = "-";
        ReportCellsText.Text = "-";
        ReportCellsHourText.Text = "-";
        ReportQualityText.Text = "-";
        _reportRows.Clear();
        _settingsRows.Clear();
        _perks.Clear();
    }

    private async void DiscardBattle_Click(object sender, RoutedEventArgs e)
    {
        if (BattlesGrid.SelectedItem is not BattleSummary battle)
        {
            return;
        }

        var tier = battle.Tier?.ToString(CultureInfo.InvariantCulture) ?? "-";
        var wave = battle.Wave?.ToString(CultureInfo.InvariantCulture) ?? "-";
        var confirmation = MessageBox.Show(
            this,
            "Discard this completed record?\n\n"
            + $"{battle.BattleId}\n"
            + $"{battle.BattleTypeDisplay} | Tier {tier} | Wave {wave}\n\n"
            + "Its JSON and Markdown files will move into Linux quarantine. "
            + "They remain manually recoverable until the configured purge "
            + "deadline (30 days by default), then are permanently deleted.",
            "Discard completed battle",
            MessageBoxButton.YesNo,
            MessageBoxImage.Warning,
            MessageBoxResult.No);
        if (confirmation != MessageBoxResult.Yes)
        {
            return;
        }

        DiscardBattleButton.IsEnabled = false;
        HistoryStatusText.Text = $"Discarding {battle.BattleId}...";
        _detailCancellation?.Cancel();
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(30));
            var discarded = await _api.DiscardBattleAsync(
                battle.BattleId,
                cancellation.Token);
            var listing = await _api.GetBattlesAsync(cancellation.Token);
            UpdateBattles(listing);

            var deadline = DateTimeOffset.TryParse(
                discarded.PurgeAfter,
                CultureInfo.InvariantCulture,
                DateTimeStyles.RoundtripKind,
                out var purgeAfter)
                ? purgeAfter.ToLocalTime().ToString("g", CultureInfo.CurrentCulture)
                : discarded.PurgeAfter ?? "the configured deadline";
            HistoryStatusText.Text =
                $"Discarded {battle.BattleId}; permanent deletion after {deadline}";
        }
        catch (Exception exc)
        {
            HistoryStatusText.Text = exc.Message;
            ShowError(exc);
        }
        finally
        {
            UpdateDiscardButton();
        }
    }

    private void ExportCsv_Click(object sender, RoutedEventArgs e)
    {
        var rows = _battleView.Cast<BattleSummary>().ToList();
        if (rows.Count == 0)
        {
            return;
        }

        var dialog = new SaveFileDialog
        {
            AddExtension = true,
            DefaultExt = ".csv",
            FileName = $"thetower-battles-{DateTime.Now:yyyyMMdd-HHmmss}.csv",
            Filter = "CSV files (*.csv)|*.csv|All files (*.*)|*.*",
            OverwritePrompt = true,
            Title = "Export completed battles",
        };
        if (dialog.ShowDialog(this) != true)
        {
            return;
        }

        try
        {
            var csv = new StringBuilder();
            AppendCsvRow(
                csv,
                "Captured",
                "Battle ID",
                "Type",
                "Strategy",
                "Tier",
                "Wave",
                "Real time",
                "Coins",
                "Coins/hour",
                "Cells",
                "Cells/hour",
                "Quality",
                "Killed by",
                "League",
                "Rank");
            foreach (var battle in rows)
            {
                AppendCsvRow(
                    csv,
                    battle.CapturedDisplay,
                    battle.BattleId,
                    battle.BattleTypeDisplay,
                    battle.StrategyDisplay,
                    battle.Tier?.ToString(CultureInfo.InvariantCulture),
                    battle.Wave?.ToString(CultureInfo.InvariantCulture),
                    battle.RealTime,
                    battle.CoinsEarned,
                    battle.CoinsPerHour,
                    battle.CellsEarned,
                    battle.CellsPerHour,
                    battle.QualityDisplay,
                    battle.KilledBy,
                    battle.League,
                    battle.Rank?.ToString(CultureInfo.InvariantCulture));
            }
            File.WriteAllText(dialog.FileName, csv.ToString(), new UTF8Encoding(true));
            HistoryStatusText.Text = $"Exported {rows.Count} rows";
        }
        catch (Exception exc)
        {
            HistoryStatusText.Text = exc.Message;
            ShowError(exc);
        }
    }

    private static void AppendCsvRow(StringBuilder destination, params string?[] values)
    {
        destination.AppendJoin(
            ',',
            values.Select(value => $"\"{(value ?? "").Replace("\"", "\"\"")}\""));
        destination.Append("\r\n");
    }

    private async void BattlesGrid_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        UpdateDiscardButton();
        if (_updatingBattles || BattlesGrid.SelectedItem is not BattleSummary battle)
        {
            return;
        }
        _selectedBattleId = battle.BattleId;
        await LoadBattleAsync(battle);
    }

    private async Task LoadBattleAsync(BattleSummary battle)
    {
        _detailCancellation?.Cancel();
        _detailCancellation?.Dispose();
        _detailCancellation = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        var cancellationToken = _detailCancellation.Token;
        RenderBattleBanner(battle);
        HistoryStatusText.Text = "Loading details...";
        try
        {
            using var document = await _api.GetBattleAsync(battle.BattleId, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            RenderBattleRecord(document.RootElement);
            _loadedBattleId = battle.BattleId;
            HistoryStatusText.Text = "";
        }
        catch (OperationCanceledException)
        {
            // A newer selection owns the detail panel.
        }
        catch (Exception exc)
        {
            HistoryStatusText.Text = exc.Message;
            ShowError(exc);
        }
    }

    private void RenderBattleBanner(BattleSummary battle)
    {
        BattleTitleText.Text = $"{battle.BattleId} | {battle.BattleTypeDisplay} | {battle.StrategyDisplay}";
        ReportTypeText.Text = battle.BattleTypeDisplay;
        ReportTierText.Text = battle.Tier?.ToString(CultureInfo.InvariantCulture) ?? "-";
        ReportWaveText.Text = battle.Wave?.ToString(CultureInfo.InvariantCulture) ?? "-";
        ReportTimeText.Text = battle.RealTime ?? "-";
        ReportCoinsText.Text = battle.CoinsEarned ?? "-";
        ReportCoinsHourText.Text = battle.CoinsPerHour ?? "-";
        ReportCellsText.Text = battle.CellsEarned ?? "-";
        ReportCellsHourText.Text = battle.CellsPerHour ?? "-";
        ReportQualityText.Text = battle.QualityDisplay;
    }

    private void RenderBattleRecord(JsonElement root)
    {
        _reportRows.Clear();
        _reportSections.Clear();
        _settingsRows.Clear();
        _perks.Clear();

        JsonElement stats = default;
        var hasStats = root.TryGetProperty("more_stats", out stats)
            || root.TryGetProperty("detailed_stats", out stats);
        if (hasStats && stats.TryGetProperty("sections", out var sections))
        {
            foreach (var section in sections.EnumerateArray())
            {
                var sectionName = GetString(section, "name") ?? "Other";
                if (!section.TryGetProperty("rows", out var rows))
                {
                    continue;
                }
                foreach (var row in rows.EnumerateArray())
                {
                    _reportRows.Add(new ReportRow(
                        sectionName,
                        GetString(row, "label") ?? Humanize(GetString(row, "key") ?? "stat"),
                        JsonValue(row, "value_raw")));
                }
            }
        }

        if (root.TryGetProperty("game_stats", out var gameStats)
            && gameStats.TryGetProperty("fields", out var gameFields))
        {
            foreach (var key in new[]
            {
                "highest_wave",
                "death_defies",
                "base_coins_earned",
                "ad_coins_earned",
            })
            {
                if (!gameFields.TryGetProperty(key, out var field))
                {
                    continue;
                }
                var value = JsonValue(field, "raw");
                if (value == "-")
                {
                    value = JsonValue(field, "value");
                }
                _reportRows.Add(new ReportRow("Game Stats source", Humanize(key), value));
            }
        }

        if (root.TryGetProperty("derived", out var derived))
        {
            Flatten(derived, "", "Derived", _reportRows);
        }

        if (root.TryGetProperty("summary", out var summary)
            && summary.TryGetProperty("fields", out var summaryFields))
        {
            foreach (var field in summaryFields.EnumerateObject())
            {
                _reportRows.Insert(
                    0,
                    new ReportRow(
                        "Tournament result",
                        Humanize(field.Name),
                        JsonValue(field.Value, "raw")));
            }
        }

        if (root.TryGetProperty("battle_type_analysis", out var classification))
        {
            _settingsRows.Add(new ReportRow(
                "Classification",
                "Type",
                JsonValue(classification, "label")));
            _settingsRows.Add(new ReportRow(
                "Classification",
                "Confidence",
                JsonValue(classification, "confidence")));
            _settingsRows.Add(new ReportRow(
                "Classification",
                "Reason",
                JsonValue(classification, "reason")));
        }

        if (root.TryGetProperty("perks", out var perks)
            && perks.TryGetProperty("selected", out var selectedPerks))
        {
            foreach (var perk in selectedPerks.EnumerateArray())
            {
                _perks.Add(new PerkRow(
                    JsonValue(perk, "latest_selection_rank"),
                    JsonValue(perk, "color"),
                    JsonValue(perk, "display_text"),
                    Confidence(perk)));
            }
        }

        if (root.TryGetProperty("run_configuration", out var configuration))
        {
            Flatten(configuration, "", "Configured", _settingsRows);
        }
        if (root.TryGetProperty("runtime", out var runtime))
        {
            foreach (var key in new[] { "terminal_state", "last_wave", "last_wave_confidence" })
            {
                if (runtime.TryGetProperty(key, out var value))
                {
                    _settingsRows.Add(new ReportRow(
                        "Runtime evidence",
                        Humanize(key),
                        DisplayJsonValue(value)));
                }
            }
            if (runtime.TryGetProperty("session_preflight_evidence", out var evidence))
            {
                Flatten(evidence, "", "Observed evidence", _settingsRows);
            }
            if (runtime.TryGetProperty(
                    "game_speed_control",
                    out var gameSpeedControl))
            {
                Flatten(
                    gameSpeedControl,
                    "",
                    "Game speed control",
                    _settingsRows);
            }
            AppendEmulatorLocationRows(runtime, _settingsRows);
            AppendSurvivalAbilityActivationRows(runtime, _reportRows);
            if (runtime.TryGetProperty("coin_rate_samples", out var rateSamples)
                && rateSamples.ValueKind == JsonValueKind.Array)
            {
                foreach (var sample in rateSamples.EnumerateArray())
                {
                    var capturedAt = JsonValue(sample, "captured_at");
                    var wave = JsonValue(sample, "wave");
                    var rate = JsonValue(sample, "display");
                    var confidence = JsonValue(sample, "confidence");
                    var label = wave == "-"
                        ? capturedAt
                        : $"Wave {wave} at {capturedAt}";
                    _reportRows.Add(new ReportRow(
                        "Coins/min progression",
                        label,
                        $"{rate}/min ({confidence}% OCR)"));
                }
            }
            AppendActiveRunMetricRows(runtime, _reportRows);
        }
        RebuildReportSections();
    }

    private static void AppendEmulatorLocationRows(
        JsonElement runtime,
        ICollection<ReportRow> destination)
    {
        if (!runtime.TryGetProperty("emulator_location", out var evidence)
            || evidence.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        destination.Add(new ReportRow(
            "Emulator location",
            "Attribution",
            Humanize(JsonValue(evidence, "status"))));
        destination.Add(new ReportRow(
            "Emulator location",
            "CPH cohort",
            JsonValue(evidence, "analytics_host_id") == "-"
                ? "Excluded from host-specific comparison"
                : "Single Windows host"));
        if (evidence.TryGetProperty("locations_truncated", out var truncated)
            && truncated.ValueKind == JsonValueKind.True)
        {
            destination.Add(new ReportRow(
                "Emulator location",
                "Selections",
                $"{JsonValue(evidence, "selection_count")} total; "
                + "bounded location detail was truncated"));
        }
        if (!evidence.TryGetProperty("locations", out var locations)
            || locations.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        var sequence = 1;
        foreach (var location in locations.EnumerateArray())
        {
            if (location.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            var listener = location.TryGetProperty(
                "bluestacks_listener",
                out var candidate)
                && candidate.ValueKind == JsonValueKind.Object
                    ? candidate
                    : default;
            var host = JsonValue(location, "host_name");
            var linuxPort = JsonValue(location, "linux_adb_port");
            var windowsPort = listener.ValueKind == JsonValueKind.Object
                ? JsonValue(listener, "adb_port")
                : "-";
            var instance = listener.ValueKind == JsonValueKind.Object
                ? JsonValue(listener, "instance_name")
                : "-";
            var processId = listener.ValueKind == JsonValueKind.Object
                ? JsonValue(listener, "process_id")
                : "-";
            var applied = JsonValue(location, "applied_at");
            destination.Add(new ReportRow(
                "Emulator location",
                $"Selection {sequence}",
                $"{host} at {applied}; Linux localhost:{linuxPort} → "
                + $"Windows localhost:{windowsPort}; {instance}; PID {processId}"));
            sequence++;
        }
    }

    private static void AppendActiveRunMetricRows(
        JsonElement runtime,
        ICollection<ReportRow> destination)
    {
        if (!runtime.TryGetProperty("active_run_metrics", out var evidence)
            || evidence.ValueKind != JsonValueKind.Object
            || !evidence.TryGetProperty("components", out var components)
            || components.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        destination.Add(new ReportRow(
            "Save-backed metric status",
            "Overall",
            ActiveRunStatusSummary(evidence)));
        if (evidence.TryGetProperty("terminal_relation", out var terminalRelation)
            && terminalRelation.ValueKind == JsonValueKind.Object)
        {
            destination.Add(new ReportRow(
                "Save-backed capability status",
                "Terminal relation",
                ActiveRunStatusSummary(terminalRelation)));
        }
        foreach (var component in components.EnumerateObject())
        {
            if (component.Value.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            destination.Add(new ReportRow(
                "Save-backed capability status",
                Humanize(component.Name),
                ActiveRunStatusSummary(component.Value)));
        }
        if (evidence.TryGetProperty("wave_claim", out var waveClaim)
            && waveClaim.ValueKind == JsonValueKind.Object)
        {
            destination.Add(new ReportRow(
                "Save-backed capability status",
                "Wave-derived rates",
                ActiveRunStatusSummary(waveClaim)));
        }
        destination.Add(new ReportRow(
            "Save-backed metric interpretation",
            "Coin sources",
            "Counters can overlap; compare the same source across runs and do not sum rates."));
        destination.Add(new ReportRow(
            "Save-backed metric interpretation",
            "Realized Coins/hour",
            "Calculated from cumulative save coins and real time; OCR Coins/min remains separate and is never multiplied into a realized hourly result."));

        if (components.TryGetProperty("economy", out var economy)
            && economy.ValueKind == JsonValueKind.Object
            && economy.TryGetProperty("samples", out var economySamples)
            && economySamples.ValueKind == JsonValueKind.Array)
        {
            foreach (var sample in economySamples.EnumerateArray())
            {
                if (sample.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }
                var wave = JsonValue(sample, "saved_wave");
                var capturedAt = JsonValue(sample, "captured_at");
                if (sample.TryGetProperty("whole_run", out var wholeRun)
                    && wholeRun.ValueKind == JsonValueKind.Object)
                {
                    destination.Add(new ReportRow(
                        "Save-backed realized-rate progression",
                        $"Whole run through wave {wave} at {capturedAt}",
                        ActiveRunRateSummary(wholeRun)));
                }
                if (sample.TryGetProperty("interval", out var interval)
                    && interval.ValueKind == JsonValueKind.Object)
                {
                    destination.Add(new ReportRow(
                        "Save-backed realized-rate progression",
                        $"Interval ending wave {wave} at {capturedAt}",
                        ActiveRunRateSummary(interval)));
                }
            }
        }

        foreach (var component in components.EnumerateObject())
        {
            if (component.Value.ValueKind != JsonValueKind.Object
                || !component.Value.TryGetProperty(
                    "samples",
                    out var componentSamples)
                || componentSamples.ValueKind != JsonValueKind.Array)
            {
                continue;
            }
            var latest = componentSamples.EnumerateArray().LastOrDefault();
            if (latest.ValueKind != JsonValueKind.Object
                || !latest.TryGetProperty("metrics", out var metrics)
                || metrics.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            latest.TryGetProperty("whole_run", out var wholeRun);
            JsonElement wholeRunPerHour = default;
            var hasWholeRunPerHour = wholeRun.ValueKind == JsonValueKind.Object
                && wholeRun.TryGetProperty("per_hour", out wholeRunPerHour)
                && wholeRunPerHour.ValueKind == JsonValueKind.Object;
            latest.TryGetProperty("interval", out var interval);
            JsonElement perHour = default;
            var hasPerHour = interval.ValueKind == JsonValueKind.Object
                && interval.TryGetProperty("per_hour", out perHour)
                && perHour.ValueKind == JsonValueKind.Object;
            foreach (var metric in metrics.EnumerateObject())
            {
                var value = CompactMetric(DisplayJsonValue(metric.Value));
                if (hasWholeRunPerHour
                    && wholeRunPerHour.TryGetProperty(metric.Name, out var wholeRate))
                {
                    value += $"; whole-run {CompactMetric(DisplayJsonValue(wholeRate))}/hour";
                }
                if (hasPerHour && perHour.TryGetProperty(metric.Name, out var rate))
                {
                    value += $"; interval {CompactMetric(DisplayJsonValue(rate))}/hour";
                }
                destination.Add(new ReportRow(
                    "Latest save-backed " + Humanize(component.Name),
                    Humanize(metric.Name),
                    value));
            }
        }

        if (evidence.TryGetProperty("terminal", out var terminal)
            && terminal.ValueKind == JsonValueKind.Object)
        {
            destination.Add(new ReportRow(
                "Save-backed realized-rate progression",
                "Terminal reconciliation",
                JsonValue(terminal, "status")
                + (JsonValue(terminal, "reason") is var reason && reason != "-"
                    ? $" ({reason})"
                    : string.Empty)));
            if (terminal.TryGetProperty("wave_claim", out var terminalWaveClaim)
                && terminalWaveClaim.ValueKind == JsonValueKind.Object)
            {
                destination.Add(new ReportRow(
                    "Terminal save-backed claim status",
                    "Wave-derived rates",
                    ActiveRunStatusSummary(terminalWaveClaim)));
            }
            if (terminal.TryGetProperty("components", out var terminalComponents)
                && terminalComponents.ValueKind == JsonValueKind.Object
                && terminalComponents.TryGetProperty("economy", out var terminalEconomy)
                && terminalEconomy.ValueKind == JsonValueKind.Object)
            {
                if (terminalEconomy.TryGetProperty("whole_run", out var wholeRun)
                    && wholeRun.ValueKind == JsonValueKind.Object)
                {
                    destination.Add(new ReportRow(
                        "Save-backed realized-rate progression",
                        "Terminal whole run",
                        ActiveRunRateSummary(wholeRun)));
                }
                if (terminalEconomy.TryGetProperty("tail_interval", out var tail)
                    && tail.ValueKind == JsonValueKind.Object)
                {
                    destination.Add(new ReportRow(
                        "Save-backed realized-rate progression",
                        "Final checkpoint to terminal interval",
                        ActiveRunRateSummary(tail)));
                }
            }
            if (terminal.TryGetProperty("components", out terminalComponents)
                && terminalComponents.ValueKind == JsonValueKind.Object)
            {
                AppendTerminalActiveRunComponentRows(
                    terminalComponents,
                    destination);
            }
        }
    }

    private static void AppendTerminalActiveRunComponentRows(
        JsonElement terminalComponents,
        ICollection<ReportRow> destination)
    {
        foreach (var component in terminalComponents.EnumerateObject())
        {
            if (component.Value.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            destination.Add(new ReportRow(
                "Terminal save-backed claim status",
                Humanize(component.Name),
                ActiveRunStatusSummary(
                    component.Value,
                    missingProperty: "missing")));
            if (!component.Value.TryGetProperty("matched", out var matched)
                || matched.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            component.Value.TryGetProperty("whole_run", out var wholeRun);
            JsonElement wholePerHour = default;
            var hasWholePerHour = wholeRun.ValueKind == JsonValueKind.Object
                && wholeRun.TryGetProperty("per_hour", out wholePerHour)
                && wholePerHour.ValueKind == JsonValueKind.Object;
            component.Value.TryGetProperty("tail_interval", out var tailInterval);
            JsonElement tailPerHour = default;
            var hasTailPerHour = tailInterval.ValueKind == JsonValueKind.Object
                && tailInterval.TryGetProperty("per_hour", out tailPerHour)
                && tailPerHour.ValueKind == JsonValueKind.Object;
            foreach (var metric in matched.EnumerateObject())
            {
                var value = CompactMetric(DisplayJsonValue(metric.Value));
                if (hasWholePerHour
                    && wholePerHour.TryGetProperty(metric.Name, out var wholeRate))
                {
                    value += $"; whole-run {CompactMetric(DisplayJsonValue(wholeRate))}/hour";
                }
                if (hasTailPerHour
                    && tailPerHour.TryGetProperty(metric.Name, out var tailRate))
                {
                    value += $"; final interval {CompactMetric(DisplayJsonValue(tailRate))}/hour";
                }
                destination.Add(new ReportRow(
                    "Terminal save-backed " + Humanize(component.Name),
                    Humanize(metric.Name),
                    value));
            }
        }
    }

    private static string ActiveRunStatusSummary(
        JsonElement evidence,
        string missingProperty = "unavailable_claims")
    {
        var summary = JsonValue(evidence, "status");
        var reason = JsonValue(evidence, "reason");
        if (reason != "-")
        {
            summary += $" ({reason})";
        }
        if (evidence.TryGetProperty(missingProperty, out var unavailable))
        {
            var unavailableSummary = JsonIssueSummary(unavailable);
            if (unavailableSummary != "-")
            {
                var label = missingProperty == "missing"
                    ? "missing"
                    : "unavailable";
                summary += $"; {label}: {unavailableSummary}";
            }
        }
        if (evidence.TryGetProperty("metric_conflicts", out var metricConflicts))
        {
            var conflictSummary = JsonIssueSummary(metricConflicts);
            if (conflictSummary != "-")
            {
                summary += $"; conflicts: {conflictSummary}";
            }
        }
        else if (evidence.TryGetProperty("conflicts", out var conflicts))
        {
            var conflictSummary = JsonIssueSummary(conflicts);
            if (conflictSummary != "-")
            {
                summary += $"; conflicts: {conflictSummary}";
            }
        }
        if (evidence.TryGetProperty("claim_issues", out var claimIssues))
        {
            var issueSummary = JsonIssueSummary(claimIssues);
            if (issueSummary != "-")
            {
                summary += $"; claim issues: {issueSummary}";
            }
        }
        return summary;
    }

    private static string JsonIssueSummary(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Object)
        {
            var items = value.EnumerateObject()
                .Select(item => $"{Humanize(item.Name)}: {DisplayJsonValue(item.Value)}")
                .ToArray();
            return items.Length == 0 ? "-" : string.Join("; ", items);
        }
        if (value.ValueKind == JsonValueKind.Array)
        {
            var items = value.EnumerateArray()
                .Select(DisplayJsonValue)
                .Where(item => item != "-")
                .ToArray();
            return items.Length == 0 ? "-" : string.Join("; ", items.Select(Humanize));
        }
        return "-";
    }

    private static string ActiveRunRateSummary(JsonElement rates) =>
        $"realized CPH {CompactMetric(JsonValue(rates, "coins_per_hour"))}; "
        + $"cells {CompactMetric(JsonValue(rates, "cells_per_hour"))}/hour; "
        + $"cash {CompactMetric(JsonValue(rates, "cash_per_hour"))}/hour; "
        + $"waves {CompactMetric(JsonValue(rates, "waves_per_hour"))}/hour; "
        + $"speed {MultiplierMetric(JsonValue(rates, "effective_game_speed"))}";

    private static string CompactMetric(string value)
    {
        if (!double.TryParse(
                value,
                NumberStyles.Float,
                CultureInfo.InvariantCulture,
                out var number)
            || !double.IsFinite(number))
        {
            return value;
        }
        var magnitudes = new[]
        {
            (1e33, "D"),
            (1e30, "N"),
            (1e27, "O"),
            (1e24, "S"),
            (1e21, "s"),
            (1e18, "Q"),
            (1e15, "q"),
            (1e12, "T"),
            (1e9, "B"),
            (1e6, "M"),
            (1e3, "K"),
        };
        foreach (var (threshold, suffix) in magnitudes)
        {
            if (Math.Abs(number) >= threshold)
            {
                return $"{number / threshold:0.##}{suffix}";
            }
        }
        return $"{number:0.##}";
    }

    private static string MultiplierMetric(string value) =>
        double.TryParse(
            value,
            NumberStyles.Float,
            CultureInfo.InvariantCulture,
            out var number)
        && double.IsFinite(number)
            ? $"x{number:0.###}"
            : value;

    private void RebuildReportSections()
    {
        _reportSections.Clear();
        foreach (var group in _reportRows.GroupBy(row => row.Category))
        {
            _reportSections.Add(new ReportSection(group.Key, group.ToList()));
        }
    }

    private static void AppendSurvivalAbilityActivationRows(
        JsonElement runtime,
        ICollection<ReportRow> destination)
    {
        if (!runtime.TryGetProperty(
                "survival_ability_activations",
                out var activations)
            || activations.ValueKind != JsonValueKind.Object)
        {
            return;
        }

        if (activations.TryGetProperty(
                "second_wind_activations",
                out var secondWinds)
            && secondWinds.ValueKind == JsonValueKind.Array)
        {
            var fallbackSecondWindSequence = 1;
            foreach (var secondWind in secondWinds.EnumerateArray())
            {
                if (secondWind.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }
                var sequence = JsonValue(secondWind, "sequence");
                AppendSurvivalAbilityActivationRow(
                    secondWind,
                    sequence == "-"
                        ? $"Second Wind activation {fallbackSecondWindSequence}"
                        : $"Second Wind activation {sequence}",
                    destination,
                    includeEstimatedRearm: true);
                fallbackSecondWindSequence++;
            }
        }

        var appendedDemonModeList = false;
        if (activations.TryGetProperty(
                "demon_mode_activations",
                out var demonModes)
            && demonModes.ValueKind == JsonValueKind.Array)
        {
            var fallbackDemonModeSequence = 1;
            foreach (var demonMode in demonModes.EnumerateArray())
            {
                if (demonMode.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }
                var sequence = JsonValue(demonMode, "sequence");
                AppendSurvivalAbilityActivationRow(
                    demonMode,
                    sequence == "-"
                        ? $"Demon Mode activation {fallbackDemonModeSequence}"
                        : $"Demon Mode activation {sequence}",
                    destination);
                appendedDemonModeList = true;
                fallbackDemonModeSequence++;
            }
        }

        if (!appendedDemonModeList
            && activations.TryGetProperty(
                "demon_mode_first_activation",
                out var firstDemonMode)
            && firstDemonMode.ValueKind == JsonValueKind.Object)
        {
            AppendSurvivalAbilityActivationRow(
                firstDemonMode,
                "Demon Mode first activation",
                destination);
        }

        if (!activations.TryGetProperty("nuke_activations", out var nukes)
            || nukes.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        var fallbackSequence = 1;
        foreach (var nuke in nukes.EnumerateArray())
        {
            if (nuke.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            var sequence = JsonValue(nuke, "sequence");
            AppendSurvivalAbilityActivationRow(
                nuke,
                sequence == "-"
                    ? $"Nuke activation {fallbackSequence}"
                    : $"Nuke activation {sequence}",
                destination);
            fallbackSequence++;
        }
    }

    private static void AppendSurvivalAbilityActivationRow(
        JsonElement activation,
        string label,
        ICollection<ReportRow> destination,
        bool includeEstimatedRearm = false)
    {
        var precision = JsonValue(activation, "wave_precision");
        var exact = string.Equals(
            precision,
            "exact",
            StringComparison.OrdinalIgnoreCase);
        var saveTimer = exact || string.Equals(
            precision,
            "save_timer",
            StringComparison.OrdinalIgnoreCase);
        var observedAt = JsonValue(
            activation,
            saveTimer ? "save_observed_at" : "detected_at");
        if (observedAt == "-")
        {
            observedAt = JsonValue(activation, "detected_at");
        }
        var wave = JsonValue(
            activation,
            saveTimer ? "activation_wave" : "approximate_wave");
        if (wave == "-")
        {
            wave = JsonValue(activation, "approximate_wave");
        }
        var waveMinimum = JsonValue(activation, "activation_wave_min");
        var waveMaximum = JsonValue(activation, "activation_wave_max");
        var waveIsRange = saveTimer
            && waveMinimum != "-"
            && waveMaximum != "-"
            && waveMinimum != waveMaximum;
        if (saveTimer && waveMinimum != "-" && waveMaximum != "-")
        {
            wave = waveIsRange
                ? $"{waveMinimum}–{waveMaximum}"
                : waveMinimum;
        }
        var waveConfidence = JsonValue(activation, "wave_confidence");
        var name = observedAt == "-" ? label : $"{label} at {observedAt}";
        string value;
        if (exact)
        {
            value = wave == "-"
                ? "Exact wave unavailable"
                : $"Exact wave {wave} (save timer)";
        }
        else if (saveTimer)
        {
            value = wave == "-"
                ? "Save-derived wave unavailable"
                : waveIsRange
                    ? $"Save-derived waves {wave} (timer)"
                    : $"Save-derived wave {wave} (timer)";
        }
        else
        {
            value = wave == "-"
                ? "Approx. wave unknown"
                : $"Approx. wave {wave}";
        }
        if (includeEstimatedRearm)
        {
            var estimatedRearm = JsonValue(
                activation,
                "estimated_rearm_wave");
            var rearmMinimum = JsonValue(activation, "rearm_wave_min");
            var rearmMaximum = JsonValue(activation, "rearm_wave_max");
            if (saveTimer && rearmMinimum != "-" && rearmMaximum != "-")
            {
                estimatedRearm = rearmMinimum == rearmMaximum
                    ? rearmMinimum
                    : $"{rearmMinimum}–{rearmMaximum}";
            }
            if (estimatedRearm != "-")
            {
                value += saveTimer
                    ? $"; re-arm wave {estimatedRearm}"
                    : $"; estimated re-arm wave {estimatedRearm}";
            }
        }
        if (!saveTimer && waveConfidence != "-")
        {
            value += $" ({waveConfidence}% wave OCR)";
        }
        destination.Add(new ReportRow(
            "Survival ability activations",
            name,
            value));
    }

    private static void Flatten(
        JsonElement element,
        string prefix,
        string category,
        ICollection<ReportRow> destination)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        foreach (var property in element.EnumerateObject())
        {
            if (property.Name is "schema_version" or "profile_version" or "raw_text")
            {
                continue;
            }
            var label = string.IsNullOrEmpty(prefix)
                ? Humanize(property.Name)
                : $"{prefix} / {Humanize(property.Name)}";
            if (property.Value.ValueKind == JsonValueKind.Object)
            {
                Flatten(property.Value, label, category, destination);
            }
            else if (property.Value.ValueKind == JsonValueKind.Array)
            {
                var values = property.Value.EnumerateArray().Select(DisplayJsonValue);
                destination.Add(new ReportRow(category, label, string.Join(" -> ", values)));
            }
            else
            {
                destination.Add(new ReportRow(category, label, DisplayJsonValue(property.Value)));
            }
        }
    }

    private static string DisplayJsonValue(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString() ?? "-",
        JsonValueKind.True => "Yes",
        JsonValueKind.False => "No",
        JsonValueKind.Null => "-",
        JsonValueKind.Undefined => "-",
        _ => value.GetRawText(),
    };

    private static string? GetString(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static string JsonValue(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var value)
            ? DisplayJsonValue(value)
            : "-";

    private static string Confidence(JsonElement perk)
    {
        if (!perk.TryGetProperty("confidence", out var value)
            || !value.TryGetDouble(out var confidence))
        {
            return "-";
        }
        return $"{confidence:F1}%";
    }

    private static string Humanize(string value) => CultureInfo.InvariantCulture.TextInfo
        .ToTitleCase(value.Replace('_', ' '));

    private static string SelectedText(ComboBox comboBox) => comboBox.SelectedItem switch
    {
        ComboBoxItem item => item.Content?.ToString() ?? "All",
        string value => value,
        _ => "All",
    };

    private static int? ParseOptionalNonNegativeInteger(string value, string label)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return null;
        }
        if (!int.TryParse(value.Trim(), NumberStyles.None, CultureInfo.InvariantCulture, out var parsed)
            || parsed < 0)
        {
            throw new ArgumentException($"{label} must be a non-negative whole number.");
        }
        return parsed;
    }

    private void ShowError(Exception exception)
    {
        MessageBox.Show(
            this,
            exception.Message,
            "TheTower completed battles",
            MessageBoxButton.OK,
            MessageBoxImage.Error);
    }
}
