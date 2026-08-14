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

    [Fact]
    public void ListenerLifetimeChangeClosesAggregateWindow()
    {
        var listener = new HostPerformanceBlueStacksListener
        {
            HostId = "TEST-HOST",
            AdbPort = 5555,
            ProcessId = 90,
            ProcessStartedAt = "2026-08-13T00:00:00+00:00",
            ExecutablePath = @"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
            InstanceName = "Nougat32",
        };

        Assert.False(
            HostPerformanceAggregateWindow.HasListenerDiscontinuity(
                listener,
                listener with { }));
        Assert.True(
            HostPerformanceAggregateWindow.HasListenerDiscontinuity(
                listener,
                listener with { ProcessId = 91 }));
        Assert.True(
            HostPerformanceAggregateWindow.HasListenerDiscontinuity(
                listener,
                listener with
                {
                    ProcessStartedAt = "2026-08-13T00:00:00.0000001+00:00",
                }));
        Assert.True(
            HostPerformanceAggregateWindow.HasListenerDiscontinuity(
                listener,
                listener with { ExecutablePath = @"D:\BlueStacks\HD-Player.exe" }));
        Assert.True(
            HostPerformanceAggregateWindow.HasListenerDiscontinuity(
                listener,
                listener with { InstanceName = "Pie64" }));
        Assert.True(
            HostPerformanceAggregateWindow.HasListenerDiscontinuity(
                listener,
                listener with { AdbPort = 5565 }));
        Assert.True(
            HostPerformanceAggregateWindow.HasListenerDiscontinuity(
                listener,
                null));
        Assert.True(
            HostPerformanceAggregateWindow.HasListenerDiscontinuity(
                null,
                listener));
    }
}
