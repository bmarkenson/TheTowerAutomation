using System.Globalization;

namespace TheTower.ControlSurface;

internal sealed record ActiveRunMetricPresentation(
    string? WholeRunCph,
    string? IntervalCph,
    string? CellsPerHour,
    string? WavesPerHour,
    string? EffectiveSpeed,
    string? Checkpoint,
    string? CheckpointDetail);

internal static class ActiveRunMetricPresenter
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

    private static readonly ActiveRunMetricPresentation Hidden = new(
        null,
        null,
        null,
        null,
        null,
        null,
        null);

    public static ActiveRunMetricPresentation Present(
        ActiveRunMetricStatus? metrics,
        string? observedRoundIdentity,
        bool activeBattleAvailable)
    {
        if (!activeBattleAvailable
            || metrics?.SchemaVersion != 1
            || !ValidIdentity(observedRoundIdentity)
            || !string.Equals(
                observedRoundIdentity,
                metrics.ActiveRoundIdentityFingerprint,
                StringComparison.Ordinal)
            || !DateTimeOffset.TryParse(
                metrics.CapturedAt,
                CultureInfo.InvariantCulture,
                DateTimeStyles.RoundtripKind,
                out var capturedAt))
        {
            return Hidden;
        }

        var status = (metrics.Status ?? "").Trim().ToLowerInvariant();
        var statusLabel = status switch
        {
            "observed" => "Observed",
            "partial" => "Partial",
            "unavailable" => "Unavailable",
            "conflict" => "Conflict",
            _ => null,
        };
        if (statusLabel is null)
        {
            return Hidden;
        }

        var ratesAvailable = status is "observed" or "partial";
        var wholeRun = ratesAvailable ? metrics.WholeRun : null;
        var interval = ratesAvailable ? metrics.Interval : null;
        var age = metrics.AgeSeconds is >= 0
            ? FormatAge(metrics.AgeSeconds.Value)
            : null;
        var checkpointParts = new List<string>();
        if (metrics.CheckpointWave is >= 0)
        {
            checkpointParts.Add(
                $"W{metrics.CheckpointWave.Value.ToString("N0", CultureInfo.InvariantCulture)}");
        }
        if (age is not null)
        {
            checkpointParts.Add($"{age} ago");
        }
        if (status != "observed")
        {
            checkpointParts.Add(statusLabel);
        }

        var detailParts = new List<string>
        {
            $"Metric status: {statusLabel}.",
            $"Captured {capturedAt.LocalDateTime:g}"
                + (age is null ? "." : $" ({age} ago, using Linux server time)."),
        };
        if (metrics.CheckpointWave is >= 0)
        {
            detailParts.Add($"Checkpoint wave: {metrics.CheckpointWave.Value}.");
        }
        if (metrics.SaveRevision is >= 0)
        {
            detailParts.Add($"Save revision: {metrics.SaveRevision.Value}.");
        }
        var reason = HumanizeReason(metrics.Reason);
        if (reason is not null)
        {
            detailParts.Add($"Reason: {reason}.");
        }

        return new ActiveRunMetricPresentation(
            CompactRate(wholeRun?.CoinsPerHour),
            CompactRate(interval?.CoinsPerHour),
            CompactRate(wholeRun?.CellsPerHour),
            CompactRate(wholeRun?.WavesPerHour),
            Multiplier(wholeRun?.EffectiveGameSpeed),
            string.Join(" · ", checkpointParts),
            string.Join(" ", detailParts));
    }

    private static bool ValidIdentity(string? value) =>
        value is { Length: 64 }
        && value.All(character =>
            character is >= '0' and <= '9'
                or >= 'a' and <= 'f');

    private static string? CompactRate(string? value)
    {
        if (!TryRate(value, out var number))
        {
            return null;
        }
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

    private static string? Multiplier(string? value) =>
        TryRate(value, out var number)
            ? "x" + number.ToString("0.###", CultureInfo.InvariantCulture)
            : null;

    private static bool TryRate(string? value, out double number) =>
        double.TryParse(
            value,
            NumberStyles.Float,
            CultureInfo.InvariantCulture,
            out number)
        && double.IsFinite(number)
        && number >= 0;

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
