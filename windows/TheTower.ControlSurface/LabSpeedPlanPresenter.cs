using System.Globalization;

namespace TheTower.ControlSurface;

internal sealed record LabSpeedPlanPresentation(
    bool Visible,
    string Badge,
    bool Warning,
    string StatusSummary,
    string HistoricalGross,
    string ActualNet,
    string NormalProjection,
    string ReserveProjection,
    string Recommendation,
    string Detail);

internal static class LabSpeedPlanPresenter
{
    private static readonly (double Threshold, string Suffix)[] Magnitudes =
    [
        (1e33, "D"), (1e30, "N"), (1e27, "O"), (1e24, "S"),
        (1e21, "s"), (1e18, "Q"), (1e15, "q"), (1e12, "T"),
        (1e9, "B"), (1e6, "M"), (1e3, "K"),
    ];

    private static readonly LabSpeedPlanPresentation Hidden = new(
        false, "Unavailable", true, "", "-", "-", "-", "-", "", "");

    public static LabSpeedPlanPresentation Present(LabSpeedPlanStatus? status)
    {
        if (status?.SchemaVersion != 1
            || status.AutomaticApplicationEnabled
            || status.UiActionAuthority
            || status.Policy.SchemaVersion != 1
            || status.Policy.AutomaticReductionEnabled
            || status.Policy.Labs.Count != 5
            || status.Policy.Labs.Select(item => item.Lab).Distinct().Count() != 5)
        {
            return Hidden;
        }
        var state = (status.Status ?? "").Trim().ToLowerInvariant();
        if (state is not ("ready" or "incomplete" or "invalid_policy"))
        {
            return Hidden;
        }
        var historical = Rate(
            status.Income.CellsPerHourDecimal,
            signed: false,
            fallback: "No usable history");
        if (status.Income.Status == "observed")
        {
            historical += $" · {status.Income.SampleCount:N0} battle"
                + (status.Income.SampleCount == 1 ? "" : "s");
        }
        var actual = Rate(
            status.ActualBalanceNetPerHourDecimal,
            signed: true,
            fallback: "Collecting");
        var normal = Projection(status.NormalPlan);
        var reserve = Projection(status.ReservePlan);
        var recommendation = string.IsNullOrWhiteSpace(status.Recommendation.Reason)
            ? "Complete all five Lab targets to calculate the plan."
            : status.Recommendation.Reason.Trim();
        var recommendationStatus = (status.Recommendation.Status ?? "").Trim();
        var warning = state == "invalid_policy"
            || recommendationStatus is "reserve_plan_still_declines"
                or "reserve_floor_breached"
                or "observed_decline_despite_forecast"
                or "observed_decline_reserve_plan_recovers";
        var configuredNormalSpeeds = Enumerable.Range(1, 5)
            .Select(lab => status.Policy.Labs
                .FirstOrDefault(item => item.Lab == lab)
                ?.NormalSpeed)
            .ToArray();
        var statusSummary = state == "invalid_policy"
            ? "Policy invalid"
            : configuredNormalSpeeds.All(
                speed => !string.IsNullOrWhiteSpace(speed))
                ? "Normal " + string.Join(
                    "/",
                    configuredNormalSpeeds.Select(speed => speed + "x"))
                    + (Number(
                        status.NormalPlan.ProjectedNetPerHourDecimal,
                        signed: true) is double normalNet
                        ? " · " + SignedCompact(normalNet) + "/h net"
                        : "")
                : "Plan incomplete";
        var detail = "Completed-battle income is duration weighted. 1x means no "
            + "renewal. Active boosts continue to expiry; automatic application "
            + "is disabled.";
        return new LabSpeedPlanPresentation(
            true,
            state switch
            {
                "ready" => "Planner ready",
                "invalid_policy" => "Policy invalid",
                _ => "Plan incomplete",
            },
            warning,
            statusSummary,
            historical,
            actual,
            normal,
            reserve,
            recommendation,
            detail);
    }

    private static string Projection(LabSpeedProjectionStatus projection)
    {
        if (!projection.Complete)
        {
            return "Choose all Labs";
        }
        var burn = Number(projection.BurnPerHourDecimal, signed: false);
        if (burn is null)
        {
            return "Unavailable";
        }
        var net = Number(projection.ProjectedNetPerHourDecimal, signed: true);
        return Compact(burn.Value) + "/h burn · "
            + (net is null ? "net pending" : SignedCompact(net.Value) + "/h net");
    }

    private static string Rate(string? value, bool signed, string fallback)
    {
        var number = Number(value, signed);
        return number is null
            ? fallback
            : (signed ? SignedCompact(number.Value) : Compact(number.Value)) + "/h";
    }

    private static double? Number(string? value, bool signed)
    {
        if (!double.TryParse(
            value,
            NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
            CultureInfo.InvariantCulture,
            out var number)
            || !double.IsFinite(number)
            || (!signed && number < 0))
        {
            return null;
        }
        return number;
    }

    internal static string Compact(double number)
    {
        var absolute = Math.Abs(number);
        foreach (var (threshold, suffix) in Magnitudes)
        {
            if (absolute >= threshold)
            {
                return (absolute / threshold).ToString("0.##", CultureInfo.InvariantCulture)
                    + suffix;
            }
        }
        return absolute.ToString("0.##", CultureInfo.InvariantCulture);
    }

    internal static string SignedCompact(double number) =>
        number > 0 ? "+" + Compact(number) : number < 0 ? "-" + Compact(number) : "0";
}
