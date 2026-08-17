namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class ActiveRunMetricPresenterTests
{
    private const string RoundIdentity =
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    [Fact]
    public void FormatsUsefulWholeRunAndIntervalMetrics()
    {
        var presentation = ActiveRunMetricPresenter.Present(
            new ActiveRunMetricStatus
            {
                SchemaVersion = 1,
                Status = "partial",
                Reason = "one_or_more_metric_claims_unavailable",
                ActiveRoundIdentityFingerprint = RoundIdentity,
                CapturedAt = "2026-08-17T20:00:00+00:00",
                AgeSeconds = 75,
                SaveRevision = 321,
                CheckpointWave = 4321,
                WholeRun = new ActiveRunRates
                {
                    CoinsPerHour = "1780000000000000000",
                    CellsPerHour = "590000",
                    WavesPerHour = "1250.5",
                    EffectiveGameSpeed = "4.984",
                },
                Interval = new ActiveRunRates
                {
                    CoinsPerHour = "1810000000000000000",
                },
            },
            observedRoundIdentity: RoundIdentity,
            activeBattleAvailable: true);

        Assert.Equal("1.78Q", presentation.WholeRunCph);
        Assert.Equal("1.81Q", presentation.IntervalCph);
        Assert.Equal("590K", presentation.CellsPerHour);
        Assert.Equal("1.25K", presentation.WavesPerHour);
        Assert.Equal("x4.984", presentation.EffectiveSpeed);
        Assert.Equal(
            "W4,321 · 1m ago · Partial",
            presentation.Checkpoint);
        Assert.Contains("Captured", presentation.CheckpointDetail);
        Assert.Contains("using Linux server time", presentation.CheckpointDetail);
        Assert.Contains("Save revision: 321.", presentation.CheckpointDetail);
        Assert.Contains(
            "Reason: one or more metric claims unavailable.",
            presentation.CheckpointDetail);
    }

    [Fact]
    public void KeepsAnObservedCheckpointCompactForTheStatusColumn()
    {
        var presentation = ActiveRunMetricPresenter.Present(
            new ActiveRunMetricStatus
            {
                SchemaVersion = 1,
                Status = "observed",
                ActiveRoundIdentityFingerprint = RoundIdentity,
                CapturedAt = "2026-08-17T20:00:00+00:00",
                AgeSeconds = 120,
                CheckpointWave = 5765,
            },
            observedRoundIdentity: RoundIdentity,
            activeBattleAvailable: true);

        Assert.Equal("W5,765 · 2m ago", presentation.Checkpoint);
        Assert.Contains("Metric status: Observed.", presentation.CheckpointDetail);
    }

    [Theory]
    [InlineData("unavailable")]
    [InlineData("conflict")]
    public void SemanticFailureKeepsProvenanceButClearsEveryRate(string status)
    {
        var presentation = ActiveRunMetricPresenter.Present(
            new ActiveRunMetricStatus
            {
                SchemaVersion = 1,
                Status = status,
                Reason = "rate_clock_invalid",
                ActiveRoundIdentityFingerprint = RoundIdentity,
                CapturedAt = "2026-08-17T20:00:00+00:00",
                AgeSeconds = 10,
                CheckpointWave = 4321,
                WholeRun = new ActiveRunRates
                {
                    CoinsPerHour = "1780000000000000000",
                    CellsPerHour = "590000",
                    WavesPerHour = "1250.5",
                    EffectiveGameSpeed = "4.984",
                },
                Interval = new ActiveRunRates
                {
                    CoinsPerHour = "1810000000000000000",
                },
            },
            observedRoundIdentity: RoundIdentity,
            activeBattleAvailable: true);

        Assert.Null(presentation.WholeRunCph);
        Assert.Null(presentation.IntervalCph);
        Assert.Null(presentation.CellsPerHour);
        Assert.Null(presentation.WavesPerHour);
        Assert.Null(presentation.EffectiveSpeed);
        Assert.EndsWith(
            status == "conflict" ? "Conflict" : "Unavailable",
            presentation.Checkpoint);
    }

    [Fact]
    public void PartialCheckpointOmitsOnlyItsUnavailableRates()
    {
        var presentation = ActiveRunMetricPresenter.Present(
            new ActiveRunMetricStatus
            {
                SchemaVersion = 1,
                Status = "partial",
                ActiveRoundIdentityFingerprint = RoundIdentity,
                CapturedAt = "2026-08-17T20:00:00+00:00",
                AgeSeconds = 20,
                WholeRun = new ActiveRunRates
                {
                    CoinsPerHour = null,
                    CellsPerHour = "600000",
                    WavesPerHour = "not-a-rate",
                    EffectiveGameSpeed = "5.01",
                },
                Interval = new ActiveRunRates
                {
                    CoinsPerHour = "1900000000000000000",
                },
            },
            observedRoundIdentity: RoundIdentity,
            activeBattleAvailable: true);

        Assert.Null(presentation.WholeRunCph);
        Assert.Equal("1.9Q", presentation.IntervalCph);
        Assert.Equal("600K", presentation.CellsPerHour);
        Assert.Null(presentation.WavesPerHour);
        Assert.Equal("x5.01", presentation.EffectiveSpeed);
    }

    [Theory]
    [InlineData(false, 1, "2026-08-17T20:00:00+00:00")]
    [InlineData(true, 2, "2026-08-17T20:00:00+00:00")]
    [InlineData(true, 1, "not-a-timestamp")]
    public void HidesAnUnboundOrMalformedProjection(
        bool activeBattleAvailable,
        int schemaVersion,
        string capturedAt)
    {
        var presentation = ActiveRunMetricPresenter.Present(
            new ActiveRunMetricStatus
            {
                SchemaVersion = schemaVersion,
                Status = "observed",
                ActiveRoundIdentityFingerprint = RoundIdentity,
                CapturedAt = capturedAt,
                WholeRun = new ActiveRunRates { CoinsPerHour = "1000" },
            },
            observedRoundIdentity: RoundIdentity,
            activeBattleAvailable);

        Assert.Null(presentation.WholeRunCph);
        Assert.Null(presentation.Checkpoint);
        Assert.Null(presentation.CheckpointDetail);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")]
    [InlineData("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")]
    public void HidesProjectionNotBoundToExactObservedRound(
        string? observedRoundIdentity)
    {
        var presentation = ActiveRunMetricPresenter.Present(
            new ActiveRunMetricStatus
            {
                SchemaVersion = 1,
                Status = "observed",
                ActiveRoundIdentityFingerprint = RoundIdentity,
                CapturedAt = "2026-08-17T20:00:00+00:00",
                WholeRun = new ActiveRunRates { CoinsPerHour = "1000" },
            },
            observedRoundIdentity,
            activeBattleAvailable: true);

        Assert.Null(presentation.WholeRunCph);
        Assert.Null(presentation.Checkpoint);
    }
}
