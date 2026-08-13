namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class HostPerformanceAggregateWindowTests
{
    [Fact]
    public void NominalSamplingRemainsInOneAggregateWindow()
    {
        var previous = DateTimeOffset.Parse(
            "2026-08-13T00:00:00.000+00:00");

        Assert.False(HostPerformanceAggregateWindow.HasDiscontinuity(
            previous,
            previous.AddSeconds(1)));
        Assert.False(HostPerformanceAggregateWindow.HasDiscontinuity(
            previous,
            previous + HostPerformanceAggregateWindow.MaximumContinuousSampleGap));
    }

    [Fact]
    public void SleepOrClockRegressionClosesAggregateWindow()
    {
        var previous = DateTimeOffset.Parse(
            "2026-08-13T00:00:00.000+00:00");

        Assert.True(HostPerformanceAggregateWindow.HasDiscontinuity(
            previous,
            previous.AddMinutes(40)));
        Assert.True(HostPerformanceAggregateWindow.HasDiscontinuity(
            previous,
            previous.AddSeconds(-1)));
        Assert.True(HostPerformanceAggregateWindow.HasDiscontinuity(
            previous,
            previous));
    }
}
