namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class RunPhaseElapsedPresenterTests
{
    [Fact]
    public void ShowsBetweenRunActivityThenResetsAtBattleStart()
    {
        var betweenRuns = RunPhaseElapsedPresenter.Present(
            new CurrentRunStatus
            {
                StartedAt = "2026-08-21T10:00:00-07:00",
            },
            "2026-08-21T10:23:00-07:00");
        var running = RunPhaseElapsedPresenter.Present(
            new CurrentRunStatus
            {
                StartedAt = "2026-08-21T10:00:00-07:00",
                BattleStartedAt = "2026-08-21T10:22:00-07:00",
            },
            "2026-08-21T10:23:00-07:00");

        Assert.Equal("ACTIVITY ELAPSED", betweenRuns.Label);
        Assert.Equal("23m", betweenRuns.Elapsed);
        Assert.Contains("Home, setup, and Pause", betweenRuns.Detail);
        Assert.Equal("RUN ELAPSED", running.Label);
        Assert.Equal("1m", running.Elapsed);
        Assert.Contains("began 22m earlier", running.Detail);
        Assert.Contains("Save-backed CPH uses game run time", running.Detail);
    }

    [Theory]
    [InlineData("not-a-timestamp")]
    [InlineData("2026-08-21T09:59:00-07:00")]
    [InlineData("2026-08-21T10:24:00-07:00")]
    public void InvalidBattleMarkerRetainsActivityTiming(string battleStartedAt)
    {
        var presentation = RunPhaseElapsedPresenter.Present(
            new CurrentRunStatus
            {
                StartedAt = "2026-08-21T10:00:00-07:00",
                BattleStartedAt = battleStartedAt,
            },
            "2026-08-21T10:23:00-07:00");

        Assert.Equal("ACTIVITY ELAPSED", presentation.Label);
        Assert.Equal("23m", presentation.Elapsed);
    }

    [Theory]
    [InlineData(null, "2026-08-21T10:23:00-07:00")]
    [InlineData("not-a-timestamp", "2026-08-21T10:23:00-07:00")]
    [InlineData("2026-08-21T10:00:00-07:00", "not-a-timestamp")]
    [InlineData("2026-08-21T10:24:00-07:00", "2026-08-21T10:23:00-07:00")]
    public void HidesUnavailableActivityTiming(string? startedAt, string serverTime)
    {
        var presentation = RunPhaseElapsedPresenter.Present(
            new CurrentRunStatus { StartedAt = startedAt },
            serverTime);

        Assert.Equal("ACTIVITY ELAPSED", presentation.Label);
        Assert.Null(presentation.Elapsed);
    }
}
