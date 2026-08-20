using System.Globalization;

namespace TheTower.ControlSurface;

internal sealed record CellBalancePresentation(
    string? Total,
    string? Trend,
    string? Buffer,
    string? Detail,
    bool TrendFalling,
    bool BufferWarning);

internal static class CellBalancePresenter
{
    private static readonly (double Threshold, string Suffix)[] Magnitudes =
    [
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
    ];

    private static readonly CellBalancePresentation Hidden = new(
        null,
        null,
        null,
        null,
        false,
        false);

    public static CellBalancePresentation Present(CellBalanceStatus? status)
    {
        if (status?.SchemaVersion != 1 || status.UiActionAuthority)
        {
            return Hidden;
        }
        var bufferStatus = status.Buffer;
        var history = status.History;
        if (bufferStatus is null
            || history is null
            || bufferStatus.AutomaticReductionEnabled)
        {
            return Hidden;
        }

        var normalizedStatus = (status.Status ?? "").Trim().ToLowerInvariant();
        if (normalizedStatus == "unavailable")
        {
            var unavailableBuffer = BufferLabel(
                bufferStatus,
                out var unavailableWarning);
            return new CellBalancePresentation(
                "Not observed",
                "Collecting",
                unavailableBuffer,
                UnavailableDetail(status, history),
                false,
                unavailableWarning);
        }
        if (normalizedStatus != "observed"
            || !string.Equals(status.Unit, "cells", StringComparison.Ordinal)
            || !DateTimeOffset.TryParse(
                status.CapturedAt,
                CultureInfo.InvariantCulture,
                DateTimeStyles.RoundtripKind,
                out var capturedAt)
            || !TryNumber(status.BalanceDecimal, signed: false, out var balance)
            || status.Trend is null)
        {
            return Hidden;
        }

        var trend = TrendLabel(
            status.Trend,
            out var trendFalling,
            out var trendDetail);
        var buffer = BufferLabel(bufferStatus, out var bufferWarning);
        if (trend is null || buffer is null)
        {
            return Hidden;
        }

        var age = status.AgeSeconds is >= 0
            ? FormatAge(status.AgeSeconds.Value)
            : "unknown age";
        var detailParts = new List<string>
        {
            $"Observed Cell balance: {balance.ToString("0.################", CultureInfo.InvariantCulture)}.",
            $"Captured {capturedAt.LocalDateTime:g} ({age} ago, using Linux server time).",
            trendDetail,
            BufferDetail(bufferStatus),
            $"History: {history.ComparableSampleCount:N0} comparable of {history.SampleCount:N0} retained samples.",
            "This is structural observation only; automatic Lab Speedup reduction is disabled.",
        };
        var reason = HumanizeReason(status.Reason);
        if (reason is not null)
        {
            detailParts.Add($"Reason: {reason}.");
        }

        return new CellBalancePresentation(
            Compact(balance),
            trend,
            buffer,
            string.Join(" ", detailParts),
            trendFalling,
            bufferWarning);
    }

    private static string? TrendLabel(
        CellBalanceTrendStatus trend,
        out bool falling,
        out string detail)
    {
        falling = false;
        detail = "Trend history is still being collected.";
        var direction = (trend.Direction ?? "").Trim().ToLowerInvariant();
        var basis = (trend.Basis ?? "").Trim().ToLowerInvariant();
        if (direction == "unknown" && basis == "insufficient_history")
        {
            return "Collecting";
        }
        if (direction is not ("rising" or "falling" or "flat")
            || basis is not ("24h_window" or "since_comparable_start")
            || !TryNumber(trend.ChangeDecimal, signed: true, out var change)
            || !TryNumber(
                trend.ElapsedHoursDecimal,
                signed: false,
                out var elapsedHours)
            || !TryNumber(
                trend.NetPerHourDecimal,
                signed: true,
                out var rate)
            || elapsedHours <= 0
            || direction == "rising" && change <= 0
            || direction == "rising" && rate <= 0
            || direction == "falling" && change >= 0
            || direction == "falling" && rate >= 0
            || direction == "flat" && (change != 0 || rate != 0))
        {
            return null;
        }

        falling = direction == "falling";
        var prefix = direction switch
        {
            "rising" => "↑ +",
            "falling" => "↓ -",
            _ => "Flat ",
        };
        var period = basis == "24h_window"
            ? "24h"
            : FormatHours(elapsedHours);
        detail = $"Comparable net change: {SignedCompact(change)} over {period} "
            + $"({SignedCompact(rate)} Cells/hour).";
        return prefix + Compact(Math.Abs(change)) + $" / {period}";
    }

    private static string? BufferLabel(
        CellBalanceBufferStatus buffer,
        out bool warning)
    {
        warning = false;
        if (buffer.AutomaticReductionEnabled)
        {
            return null;
        }
        var state = (buffer.Status ?? "").Trim().ToLowerInvariant();
        if (state == "not_configured")
        {
            return "Not set";
        }
        if (state == "unavailable")
        {
            return "Unavailable";
        }
        if (!TryNumber(buffer.FloorDecimal, signed: false, out _)
            || !TryNumber(buffer.HeadroomDecimal, signed: true, out var headroom))
        {
            return null;
        }
        if (state == "above" && headroom > 0)
        {
            return Compact(headroom) + " above";
        }
        if (state == "at" && headroom == 0)
        {
            return "At floor";
        }
        if (state == "below" && headroom < 0)
        {
            warning = true;
            return Compact(Math.Abs(headroom)) + " below";
        }
        return null;
    }

    private static string BufferDetail(CellBalanceBufferStatus buffer)
    {
        var state = (buffer.Status ?? "").Trim().ToLowerInvariant();
        if (state == "not_configured")
        {
            return "No reserve floor is configured.";
        }
        if (!TryNumber(buffer.FloorDecimal, signed: false, out var floor))
        {
            return "Reserve state is unavailable.";
        }
        var detail = $"Reserve floor: {Compact(floor)} Cells; state: {state}.";
        if (TryNumber(
            buffer.EstimatedHoursToFloorDecimal,
            signed: false,
            out var estimate))
        {
            detail += $" At the observed net rate, estimated time to floor: {FormatHours(estimate)}.";
        }
        return detail;
    }

    private static string UnavailableDetail(
        CellBalanceStatus status,
        CellBalanceHistoryStatus history)
    {
        var reason = HumanizeReason(status.Reason) ?? "no accepted observation";
        return $"Cell balance unavailable: {reason}. "
            + $"{history.SampleCount:N0} samples are retained. "
            + "Automatic Lab Speedup reduction is disabled.";
    }

    private static bool TryNumber(
        string? value,
        bool signed,
        out double number) =>
        double.TryParse(
            value,
            NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
            CultureInfo.InvariantCulture,
            out number)
        && double.IsFinite(number)
        && (signed || number >= 0);

    private static string Compact(double number)
    {
        foreach (var (threshold, suffix) in Magnitudes)
        {
            if (number >= threshold)
            {
                return (number / threshold).ToString(
                    "0.##",
                    CultureInfo.InvariantCulture) + suffix;
            }
        }
        return number.ToString("0.##", CultureInfo.InvariantCulture);
    }

    private static string SignedCompact(double number) =>
        number > 0 ? "+" + Compact(number) : number < 0 ? "-" + Compact(-number) : "0";

    private static string FormatHours(double hours) => hours switch
    {
        < 1 => $"{Math.Max(1, Math.Round(hours * 60)):0}m",
        < 48 => $"{hours:0.#}h",
        _ => $"{hours / 24:0.#}d",
    };

    private static string FormatAge(int seconds) => seconds switch
    {
        < 60 => $"{seconds}s",
        < 3600 => $"{seconds / 60}m",
        < 86400 => $"{seconds / 3600}h {seconds % 3600 / 60}m",
        _ => $"{seconds / 86400}d {seconds % 86400 / 3600}h",
    };

    private static string? HumanizeReason(string? value)
    {
        var normalized = string.Join(
            " ",
            (value ?? "")
                .Replace('_', ' ')
                .Split(' ', StringSplitOptions.RemoveEmptyEntries));
        return normalized.Length == 0 ? null : normalized;
    }
}
