using System.Globalization;

namespace TheTower.ControlSurface;

internal sealed record RunPhaseElapsedPresentation(
    string Label,
    string? Elapsed,
    string Detail);

internal static class RunPhaseElapsedPresenter
{
    private const string ActivityLabel = "ACTIVITY ELAPSED";
    private const string RunLabel = "RUN ELAPSED";

    public static RunPhaseElapsedPresentation Present(
        CurrentRunStatus? currentRun,
        string? serverTime)
    {
        if (!TryTimestamp(currentRun?.StartedAt, out var activityStartedAt)
            || !TryTimestamp(serverTime, out var observedAt)
            || observedAt < activityStartedAt)
        {
            return new(
                ActivityLabel,
                null,
                "Current activity timing is unavailable.");
        }

        var label = ActivityLabel;
        var phaseStartedAt = activityStartedAt;
        var detail =
            "Wall-clock time in the current activity/report segment, including "
            + "Home, setup, and Pause before a battle starts. It resets to run "
            + "elapsed when a genuine battle start is observed. Save-backed CPH "
            + "uses game run time, not this display clock.";
        if (TryTimestamp(currentRun?.BattleStartedAt, out var battleStartedAt)
            && battleStartedAt >= activityStartedAt
            && observedAt >= battleStartedAt)
        {
            label = RunLabel;
            phaseStartedAt = battleStartedAt;
            detail =
                "Wall-clock time since the current battle was observed running. "
                + "The activity/report segment began "
                + $"{FormatDuration(activityStartedAt, battleStartedAt)} "
                + "earlier, preserving between-run setup time separately. "
                + "Save-backed CPH uses game run time, not this display clock.";
        }

        return new(
            label,
            FormatDuration(phaseStartedAt, observedAt),
            detail);
    }

    private static bool TryTimestamp(string? value, out DateTimeOffset parsed) =>
        DateTimeOffset.TryParse(
            value,
            CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind,
            out parsed);

    private static string FormatDuration(
        DateTimeOffset startedAt,
        DateTimeOffset observedAt)
    {
        var seconds = (int)Math.Min(
            Math.Floor((observedAt - startedAt).TotalSeconds),
            int.MaxValue);
        return seconds switch
        {
            < 60 => $"{seconds}s",
            < 3600 => $"{seconds / 60}m",
            < 86400 => $"{seconds / 3600}h {seconds % 3600 / 60}m",
            _ => $"{seconds / 86400}d {seconds % 86400 / 3600}h",
        };
    }
}
