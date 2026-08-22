using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace TheTower.ControlSurface;

internal sealed record LabPolicyDraftItem(
    int Lab,
    string? NormalSpeed,
    string? ReserveSpeed);

public partial class LabSpeedPlannerWindow : Window
{
    private readonly Func<
        string?,
        IReadOnlyList<LabPolicyDraftItem>,
        Task<StatusResponse>> _savePolicy;
    private bool _initialized;
    private bool _updatingDraft;
    private bool _draftDirty;
    private bool _policyLoaded;
    private bool _requestInFlight;
    private bool _serverCompatible;
    private string? _policyRequestId;
    private LabSpeedPlanStatus? _latestPlan;

    internal LabSpeedPlannerWindow(
        Func<
            string?,
            IReadOnlyList<LabPolicyDraftItem>,
            Task<StatusResponse>> savePolicy)
    {
        InitializeComponent();
        _savePolicy = savePolicy;
        InitializeSpeedChoices();
    }

    public void UpdateStatus(
        LabSpeedPlanStatus? status,
        bool serverCompatible)
    {
        _latestPlan = status;
        _serverCompatible = serverCompatible;
        var presentation = LabSpeedPlanPresenter.Present(status);
        if (!presentation.Visible || status is null)
        {
            LabPlannerBadgeText.Text = "UNAVAILABLE";
            LabPlannerBadgeText.Foreground = new SolidColorBrush(
                Color.FromRgb(255, 113, 135));
            LabHistoricalGrossText.Text = "-";
            LabActualNetText.Text = "-";
            LabNormalProjectionText.Text = "-";
            LabReserveProjectionText.Text = "-";
            LabRecommendationText.Text =
                "A compatible Linux Lab planner status is unavailable.";
            SaveCellPolicyButton.IsEnabled = false;
            return;
        }

        if (!_draftDirty
            && (!_policyLoaded
                || !string.Equals(
                    _policyRequestId,
                    status.Policy.RequestId,
                    StringComparison.Ordinal)))
        {
            LoadPolicyDraft(status.Policy);
        }

        LabPlannerBadgeText.Text = presentation.Badge.ToUpperInvariant();
        LabPlannerBadgeText.Foreground = presentation.Warning
            ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
            : new SolidColorBrush(Color.FromRgb(101, 230, 166));
        LabHistoricalGrossText.Text = presentation.HistoricalGross;
        LabActualNetText.Text = presentation.ActualNet;
        LabActualNetText.Foreground = presentation.ActualNet.StartsWith(
            "-",
            StringComparison.Ordinal)
            ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
            : Brushes.White;
        LabNormalProjectionText.Text = presentation.NormalProjection;
        LabReserveProjectionText.Text = presentation.ReserveProjection;
        LabRecommendationText.Text = presentation.Recommendation;
        LabRecommendationText.Foreground = presentation.Warning
            ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
            : string.Equals(
                status.Recommendation.Status,
                "policy_incomplete",
                StringComparison.Ordinal)
                || string.Equals(
                    status.Recommendation.Status,
                    "income_history_unavailable",
                    StringComparison.Ordinal)
                ? new SolidColorBrush(Color.FromRgb(98, 213, 255))
                : new SolidColorBrush(Color.FromRgb(101, 230, 166));
        LabRecommendationBorder.ToolTip = presentation.Detail;

        var policyItems = status.Policy.Labs.ToDictionary(item => item.Lab);
        foreach (var row in PolicyRows())
        {
            policyItems.TryGetValue(row.Lab, out var item);
            row.NormalCost.Text = PolicyCost(item?.NormalCellsPerHourDecimal);
            row.ReserveCost.Text = PolicyCost(item?.ReserveCellsPerHourDecimal);
            row.Savings.Text = PolicyCost(item?.SavingsPerHourDecimal);
        }
        CellPolicyStatusText.Text = DateTimeOffset.TryParse(
            status.Policy.UpdatedAt,
            CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind,
            out var updatedAt)
            ? $"Saved {updatedAt.LocalDateTime:g} · automatic application disabled"
            : "No Lab plan saved yet · automatic application disabled";
        CellPolicyStatusText.Foreground = (Brush)FindResource("MutedBrush");
        SaveCellPolicyButton.IsEnabled = false;
        if (_draftDirty)
        {
            RenderDraft();
        }
    }

    private IReadOnlyList<(
        int Lab,
        ComboBox Normal,
        ComboBox Reserve,
        TextBlock NormalCost,
        TextBlock ReserveCost,
        TextBlock Savings)> PolicyRows() =>
    [
        (1, Lab1NormalSpeedBox, Lab1ReserveSpeedBox, Lab1NormalCostText, Lab1ReserveCostText, Lab1SavingsText),
        (2, Lab2NormalSpeedBox, Lab2ReserveSpeedBox, Lab2NormalCostText, Lab2ReserveCostText, Lab2SavingsText),
        (3, Lab3NormalSpeedBox, Lab3ReserveSpeedBox, Lab3NormalCostText, Lab3ReserveCostText, Lab3SavingsText),
        (4, Lab4NormalSpeedBox, Lab4ReserveSpeedBox, Lab4NormalCostText, Lab4ReserveCostText, Lab4SavingsText),
        (5, Lab5NormalSpeedBox, Lab5ReserveSpeedBox, Lab5NormalCostText, Lab5ReserveCostText, Lab5SavingsText),
    ];

    private void InitializeSpeedChoices()
    {
        _updatingDraft = true;
        try
        {
            string[] speeds = ["1", "1.5", "2", "3", "4", "5", "6", "7", "8"];
            foreach (var row in PolicyRows())
            {
                foreach (var box in new[] { row.Normal, row.Reserve })
                {
                    foreach (var speed in speeds)
                    {
                        box.Items.Add(new ComboBoxItem
                        {
                            Tag = speed,
                            Content = speed == "1"
                                ? "1x — no renewal"
                                : speed + "x",
                        });
                    }
                    box.SelectedIndex = -1;
                }
            }
        }
        finally
        {
            _updatingDraft = false;
            _initialized = true;
        }
    }

    private static string? SelectedSpeed(ComboBox box) =>
        (box.SelectedItem as ComboBoxItem)?.Tag?.ToString();

    private static void SelectSpeed(ComboBox box, string? speed)
    {
        box.SelectedItem = box.Items
            .OfType<ComboBoxItem>()
            .FirstOrDefault(item => string.Equals(
                item.Tag?.ToString(),
                speed,
                StringComparison.Ordinal));
    }

    private void LoadPolicyDraft(CellBalancePolicyStatus policy)
    {
        _updatingDraft = true;
        try
        {
            CellReserveFloorTextBox.Text = policy.BufferFloorDecimal ?? "";
            var labs = policy.Labs.ToDictionary(item => item.Lab);
            foreach (var row in PolicyRows())
            {
                labs.TryGetValue(row.Lab, out var item);
                SelectSpeed(row.Normal, item?.NormalSpeed);
                SelectSpeed(row.Reserve, item?.ReserveSpeed);
            }
            _policyRequestId = policy.RequestId;
            _policyLoaded = true;
        }
        finally
        {
            _updatingDraft = false;
        }
    }

    private bool TryDraft(
        out string? floor,
        out List<LabPolicyDraftItem> labs)
    {
        floor = CellReserveFloorTextBox.Text.Trim();
        if (floor.Length == 0)
        {
            floor = null;
        }
        else if (floor.Length > 36 || !floor.All(char.IsAsciiDigit))
        {
            labs = [];
            return false;
        }

        labs = [];
        foreach (var row in PolicyRows())
        {
            var normal = SelectedSpeed(row.Normal);
            var reserve = SelectedSpeed(row.Reserve);
            if (normal is null || reserve is null)
            {
                if (normal is not null || reserve is not null)
                {
                    labs = [];
                    return false;
                }
                labs.Add(new LabPolicyDraftItem(row.Lab, null, null));
                continue;
            }
            if (!double.TryParse(
                    normal,
                    NumberStyles.AllowDecimalPoint,
                    CultureInfo.InvariantCulture,
                    out var normalValue)
                || !double.TryParse(
                    reserve,
                    NumberStyles.AllowDecimalPoint,
                    CultureInfo.InvariantCulture,
                    out var reserveValue)
                || reserveValue > normalValue)
            {
                labs = [];
                return false;
            }
            labs.Add(new LabPolicyDraftItem(row.Lab, normal, reserve));
        }
        return labs.Count == 5;
    }

    private bool TryLabCost(string? speed, out double cost)
    {
        cost = 0;
        return speed is not null
            && _latestPlan?.CostModel.CellsPerHourBySpeed.TryGetValue(
                speed,
                out var text) == true
            && double.TryParse(
                text,
                NumberStyles.AllowDecimalPoint,
                CultureInfo.InvariantCulture,
                out cost)
            && double.IsFinite(cost)
            && cost >= 0;
    }

    private static string CostLabel(double value) =>
        LabSpeedPlanPresenter.Compact(value) + "/h";

    private static string PolicyCost(string? value) =>
        double.TryParse(
            value,
            NumberStyles.AllowDecimalPoint,
            CultureInfo.InvariantCulture,
            out var cost)
            && double.IsFinite(cost)
            && cost >= 0
            ? CostLabel(cost)
            : "-";

    private void RenderDraft()
    {
        if (_latestPlan is null)
        {
            SaveCellPolicyButton.IsEnabled = false;
            return;
        }

        var valid = TryDraft(out _, out var labs);
        var planComplete = valid && labs.All(
            item => item.NormalSpeed is not null && item.ReserveSpeed is not null);
        var normalBurn = 0d;
        var reserveBurn = 0d;
        foreach (var row in PolicyRows())
        {
            var item = labs.FirstOrDefault(candidate => candidate.Lab == row.Lab);
            var normalCost = 0d;
            var reserveCost = 0d;
            var normalValid = item is not null
                && TryLabCost(item.NormalSpeed, out normalCost);
            var reserveValid = item is not null
                && TryLabCost(item.ReserveSpeed, out reserveCost);
            row.NormalCost.Text = normalValid ? CostLabel(normalCost) : "-";
            row.ReserveCost.Text = reserveValid ? CostLabel(reserveCost) : "-";
            row.Savings.Text = normalValid && reserveValid
                ? CostLabel(normalCost - reserveCost)
                : "-";
            if (normalValid)
            {
                normalBurn += normalCost;
            }
            if (reserveValid)
            {
                reserveBurn += reserveCost;
            }
        }

        var historicalAvailable = double.TryParse(
            _latestPlan.Income.CellsPerHourDecimal,
            NumberStyles.AllowDecimalPoint,
            CultureInfo.InvariantCulture,
            out var historicalGross)
            && double.IsFinite(historicalGross)
            && historicalGross >= 0;
        var actualNetAvailable = double.TryParse(
            _latestPlan.ActualBalanceNetPerHourDecimal,
            NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
            CultureInfo.InvariantCulture,
            out var actualNet)
            && double.IsFinite(actualNet);
        if (!valid)
        {
            LabNormalProjectionText.Text = "Invalid draft";
            LabReserveProjectionText.Text = "Invalid draft";
            LabRecommendationText.Text = "For each Lab, choose both targets or leave both blank; keep reserve at or below normal and enter a whole-number Cell reserve.";
            LabRecommendationText.Foreground = new SolidColorBrush(
                Color.FromRgb(241, 191, 91));
        }
        else if (!planComplete)
        {
            LabNormalProjectionText.Text = "Choose all Labs";
            LabReserveProjectionText.Text = "Choose all Labs";
            LabRecommendationText.Text = "The Cell reserve can be saved now. Complete both targets for all five Labs to add the spending forecast.";
            LabRecommendationText.Foreground = new SolidColorBrush(
                Color.FromRgb(98, 213, 255));
        }
        else
        {
            LabNormalProjectionText.Text = CostLabel(normalBurn) + " burn · "
                + (historicalAvailable
                    ? LabSpeedPlanPresenter.SignedCompact(
                        historicalGross - normalBurn) + "/h net"
                    : "net pending");
            LabReserveProjectionText.Text = CostLabel(reserveBurn) + " burn · "
                + (historicalAvailable
                    ? LabSpeedPlanPresenter.SignedCompact(
                        historicalGross - reserveBurn) + "/h net"
                    : "net pending");
            if (!historicalAvailable)
            {
                LabRecommendationText.Text = "The draft is complete; a projected net will appear when completed-battle Cell history is available.";
                LabRecommendationText.Foreground = new SolidColorBrush(
                    Color.FromRgb(98, 213, 255));
            }
            else if (historicalGross - reserveBurn < 0)
            {
                LabRecommendationText.Text = "Draft reserve targets still spend faster than historical gross Cell income.";
                LabRecommendationText.Foreground = new SolidColorBrush(
                    Color.FromRgb(241, 191, 91));
            }
            else if (historicalGross - normalBurn >= 0
                && actualNetAvailable
                && actualNet < 0)
            {
                LabRecommendationText.Text = "Historical income covers the draft normal plan, but the observed Cell balance is currently falling.";
                LabRecommendationText.Foreground = new SolidColorBrush(
                    Color.FromRgb(241, 191, 91));
            }
            else if (actualNetAvailable && actualNet < 0)
            {
                LabRecommendationText.Text = "The observed Cell balance is falling; the draft reserve targets are projected to make Cell flow nonnegative.";
                LabRecommendationText.Foreground = new SolidColorBrush(
                    Color.FromRgb(241, 191, 91));
            }
            else if (historicalGross - normalBurn >= 0)
            {
                LabRecommendationText.Text = "Draft normal targets are covered by historical gross Cell income.";
                LabRecommendationText.Foreground = new SolidColorBrush(
                    Color.FromRgb(101, 230, 166));
            }
            else
            {
                LabRecommendationText.Text = "Draft reserve targets change projected Cell flow from declining to nonnegative.";
                LabRecommendationText.Foreground = new SolidColorBrush(
                    Color.FromRgb(101, 230, 166));
            }
        }

        SaveCellPolicyButton.IsEnabled = valid
            && !_requestInFlight
            && _serverCompatible;
        CellPolicyStatusText.Text = "Unsaved changes · planner only";
        CellPolicyStatusText.Foreground = (Brush)FindResource("MutedBrush");
    }

    private void CellPolicyDraft_Changed(object sender, RoutedEventArgs e)
    {
        if (!_initialized || _updatingDraft)
        {
            return;
        }
        _draftDirty = true;
        RenderDraft();
    }

    private async void SaveCellPolicy_Click(object sender, RoutedEventArgs e)
    {
        if (!TryDraft(out var floor, out var labs))
        {
            ShowError(new InvalidOperationException(
                "For each Lab, choose both targets or leave both blank, keep "
                    + "reserve no higher than normal, and enter a nonnegative "
                    + "whole-number Cell reserve."));
            return;
        }

        try
        {
            _requestInFlight = true;
            SaveCellPolicyButton.IsEnabled = false;
            var response = await _savePolicy(floor, labs);
            _draftDirty = false;
            _policyLoaded = false;
            UpdateStatus(response.LabSpeedPlan, _serverCompatible);
        }
        catch (Exception exc)
        {
            ShowError(exc);
        }
        finally
        {
            _requestInFlight = false;
            if (_draftDirty)
            {
                RenderDraft();
            }
        }
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();

    private void ShowError(Exception exception)
    {
        CellPolicyStatusText.Text = exception.Message;
        CellPolicyStatusText.Foreground = new SolidColorBrush(
            Color.FromRgb(255, 113, 135));
        MessageBox.Show(
            this,
            exception.Message,
            "Lab Speedup planner",
            MessageBoxButton.OK,
            MessageBoxImage.Error);
    }
}
