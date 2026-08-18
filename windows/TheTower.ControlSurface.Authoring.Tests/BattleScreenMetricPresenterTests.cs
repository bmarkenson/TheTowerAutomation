namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class BattleScreenMetricPresenterTests
{
    private const string RoundIdentity =
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    [Fact]
    public void PresentsCurrentFrameWaveAndCompactScreenAge()
    {
        var presentation = BattleScreenMetricPresenter.Present(
            Metrics(
                waveObservationId: "runtime-1:12",
                waveAge: 2,
                coinAge: 75),
            Observation(
                observationId: "runtime-1:12",
                gameState: "active_battle",
                age: 2,
                wave: 4321),
            processActive: true);

        Assert.Equal("4321", presentation.Wave);
        Assert.False(presentation.WaveRetained);
        Assert.Equal("1.23T", presentation.CoinsPerMinute);
        Assert.False(presentation.CoinsPerMinuteRetained);
        Assert.Equal("2s ago", presentation.ScreenAge);
        Assert.True(presentation.ScreenObservationFresh);
        Assert.Contains("current canonical observation", presentation.WaveDetail);
        Assert.Contains("periodic Coins/min", presentation.CoinsPerMinuteDetail);
    }

    [Fact]
    public void MarksSameBattleValuesRetainedOnAnOffBattleScreen()
    {
        var presentation = BattleScreenMetricPresenter.Present(
            Metrics(
                waveObservationId: "runtime-1:10",
                waveAge: 8,
                coinAge: 75),
            Observation(
                observationId: "runtime-1:12",
                gameState: "unknown",
                age: 3,
                wave: null),
            processActive: true);

        Assert.Equal("4321*", presentation.Wave);
        Assert.True(presentation.WaveRetained);
        Assert.Equal("1.23T*", presentation.CoinsPerMinute);
        Assert.True(presentation.CoinsPerMinuteRetained);
        Assert.Contains("Last proven battle-screen value", presentation.WaveDetail);
        Assert.Contains(
            "Last proven battle-screen value",
            presentation.CoinsPerMinuteDetail);
    }

    [Fact]
    public void MarksWaveRetainedWhenCurrentBattleFrameMissedOcr()
    {
        var presentation = BattleScreenMetricPresenter.Present(
            Metrics(
                waveObservationId: "runtime-1:11",
                waveAge: 7,
                coinAge: 75),
            Observation(
                observationId: "runtime-1:12",
                gameState: "active_battle",
                age: 2,
                wave: null),
            processActive: true);

        Assert.Equal("4321*", presentation.Wave);
        Assert.True(presentation.WaveRetained);
        Assert.Equal("1.23T", presentation.CoinsPerMinute);
        Assert.False(presentation.CoinsPerMinuteRetained);
    }

    [Fact]
    public void ExactBattleMismatchHidesMetricsButKeepsObservationAge()
    {
        var observation = Observation(
            observationId: "runtime-1:12",
            gameState: "active_battle",
            age: 4,
            wave: 4321);
        observation.ActiveRoundIdentityFingerprint = new string('b', 64);

        var presentation = BattleScreenMetricPresenter.Present(
            Metrics(
                waveObservationId: "runtime-1:12",
                waveAge: 4,
                coinAge: 75),
            observation,
            processActive: true);

        Assert.Null(presentation.Wave);
        Assert.Null(presentation.CoinsPerMinute);
        Assert.Equal("4s ago", presentation.ScreenAge);
        Assert.True(presentation.ScreenObservationFresh);
    }

    [Fact]
    public void StoppedProcessClearsMetricsAndScreenAge()
    {
        var presentation = BattleScreenMetricPresenter.Present(
            Metrics(
                waveObservationId: "runtime-1:12",
                waveAge: 2,
                coinAge: 75),
            Observation(
                observationId: "runtime-1:12",
                gameState: "active_battle",
                age: 2,
                wave: 4321),
            processActive: false);

        Assert.Null(presentation.Wave);
        Assert.Null(presentation.CoinsPerMinute);
        Assert.Equal("-", presentation.ScreenAge);
        Assert.False(presentation.ScreenObservationFresh);
    }

    private static ActiveBattleScreenMetricStatus Metrics(
        string waveObservationId,
        int waveAge,
        int coinAge) => new()
        {
            SchemaVersion = 1,
            ActiveRoundIdentityFingerprint = RoundIdentity,
            Wave = new ActiveBattleWaveMetricStatus
            {
                Value = 4321,
                ObservationId = waveObservationId,
                ObservedAt = "2026-08-18T20:00:00+00:00",
                AgeSeconds = waveAge,
            },
            CoinsPerMinute = new ActiveBattleTextMetricStatus
            {
                Value = "1.23T",
                ObservationId = "runtime-1:periodic-7",
                ObservedAt = "2026-08-18T19:59:00+00:00",
                AgeSeconds = coinAge,
            },
        };

    private static BetterControlObservationStatus Observation(
        string observationId,
        string gameState,
        int age,
        int? wave) => new()
        {
            Available = true,
            ObservationId = observationId,
            ObservedAt = "2026-08-18T20:00:00+00:00",
            GameState = gameState,
            ActiveBattle = true,
            Wave = wave,
            ActiveRoundIdentityFingerprint = RoundIdentity,
            AgeSeconds = age,
        };
}
