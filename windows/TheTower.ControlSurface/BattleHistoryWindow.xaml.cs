using System.Collections.ObjectModel;
using System.Globalization;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;

namespace TheTower.ControlSurface;

public partial class BattleHistoryWindow : Window
{
    private readonly ControlSurfaceApi _api;
    private readonly ObservableCollection<BattleSummary> _battles = [];
    private readonly ObservableCollection<ReportRow> _reportRows = [];
    private readonly ObservableCollection<ReportRow> _settingsRows = [];
    private readonly ObservableCollection<PerkRow> _perks = [];
    private readonly System.ComponentModel.ICollectionView _battleView;
    private CancellationTokenSource? _detailCancellation;
    private bool _updatingBattles;
    private string? _selectedBattleId;
    private string? _loadedBattleId;
    private string _typeFilter = "all";
    private int? _tierFilter;
    private int? _minWaveFilter;
    private int? _maxWaveFilter;
    private string _strategyFilter = "";
    private string _qualityFilter = "all";

    public BattleHistoryWindow(ControlSurfaceApi api)
    {
        InitializeComponent();
        _api = api;
        _battleView = CollectionViewSource.GetDefaultView(_battles);
        _battleView.Filter = BattleMatchesFilter;
        BattlesGrid.ItemsSource = _battleView;
        ReportRowsGrid.ItemsSource = _reportRows;
        SettingsGrid.ItemsSource = _settingsRows;
        PerksGrid.ItemsSource = _perks;
        Closed += (_, _) => _detailCancellation?.Cancel();
    }

    public void UpdateBattles(BattleListResponse response)
    {
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
            UpdateFilterCount();

            var selected = requestedId is null
                ? _battleView.Cast<BattleSummary>().FirstOrDefault()
                : _battleView.Cast<BattleSummary>()
                    .FirstOrDefault(battle => battle.BattleId == requestedId)
                    ?? _battleView.Cast<BattleSummary>().FirstOrDefault();
            BattlesGrid.SelectedItem = selected;
            _selectedBattleId = selected?.BattleId;
            if (selected is not null && selected.BattleId != _loadedBattleId)
            {
                _ = LoadBattleAsync(selected);
            }
        }
        finally
        {
            _updatingBattles = false;
        }
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            HistoryStatusText.Text = "Refreshing...";
            var response = await _api.GetBattlesAsync(CancellationToken.None);
            UpdateBattles(response);
            HistoryStatusText.Text = "Updated";
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
            _strategyFilter = StrategyFilterBox.Text.Trim();
            _battleView.Refresh();
            UpdateFilterCount();
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
        StrategyFilterBox.Clear();
        _typeFilter = "all";
        _qualityFilter = "all";
        _tierFilter = null;
        _minWaveFilter = null;
        _maxWaveFilter = null;
        _strategyFilter = "";
        _battleView.Refresh();
        UpdateFilterCount();
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
        if (!string.IsNullOrEmpty(_strategyFilter)
            && !battle.StrategyDisplay.Contains(_strategyFilter, StringComparison.OrdinalIgnoreCase))
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
        FilterCountText.Text = $"{_battleView.Cast<object>().Count()} of {_battles.Count}";
    }

    private async void BattlesGrid_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
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
        }
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

    private static string SelectedText(ComboBox comboBox) =>
        (comboBox.SelectedItem as ComboBoxItem)?.Content?.ToString() ?? "All";

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
