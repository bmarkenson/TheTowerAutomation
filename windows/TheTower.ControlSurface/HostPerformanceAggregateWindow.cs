namespace TheTower.ControlSurface;

internal static class HostPerformanceAggregateWindow
{
    // A long scheduler, sleep, or wall-clock discontinuity is evidence of a
    // sampling gap, not a continuous aggregate window. Splitting well below
    // the Linux validator's five-minute ceiling also makes the gap explicit.
    internal static readonly TimeSpan MaximumContinuousSampleGap =
        TimeSpan.FromSeconds(5);

    internal static bool HasDiscontinuity(
        DateTimeOffset previousSampleAtUtc,
        DateTimeOffset currentSampleAtUtc)
    {
        var gap = currentSampleAtUtc - previousSampleAtUtc;
        return gap <= TimeSpan.Zero || gap > MaximumContinuousSampleGap;
    }

    internal static bool HasListenerDiscontinuity(
        HostPerformanceBlueStacksListener? previous,
        HostPerformanceBlueStacksListener? current) =>
        !Equals(previous, current);
}
