using System.Globalization;

namespace TheTower.ControlSurface;

internal sealed record BattleScreenMetricPresentation(
    string? Wave,
    bool WaveRetained,
    string? WaveDetail,
    string? CoinsPerMinute,
    bool CoinsPerMinuteRetained,
    string? CoinsPerMinuteDetail,
    string ScreenAge,
    string ScreenAgeDetail,
    bool ScreenObservationFresh);

internal static class BattleScreenMetricPresenter
{
    public static BattleScreenMetricPresentation Present(
        ActiveBattleScreenMetricStatus? metrics,
        BetterControlObservationStatus? observation,
        bool processActive)
    {
        var screenAge = processActive && observation?.AgeSeconds is >= 0
            ? $"{FormatAge(observation.AgeSeconds.Value)} ago"
            : processActive ? "Missing" : "-";
        var screenDetail = processActive && observation?.AgeSeconds is >= 0
            ? "Latest canonical main-loop screen observation: "
                + $"{FormatAge(observation.AgeSeconds.Value)} ago."
                + (observation.Available
                    ? ""
                    : " The server no longer accepts it as fresh exact-owner evidence.")
            : processActive
                ? "No canonical main-loop screen observation is available."
                : "The automation process is not running.";
        var screenFresh = processActive
            && observation is { Available: true, AgeSeconds: >= 0 };

        if (!screenFresh
            || observation?.ActiveBattle != true
            || metrics?.SchemaVersion != 1
            || !ValidIdentity(observation.ActiveRoundIdentityFingerprint)
            || !string.Equals(
                observation.ActiveRoundIdentityFingerprint,
                metrics.ActiveRoundIdentityFingerprint,
                StringComparison.Ordinal))
        {
            return new BattleScreenMetricPresentation(
                null,
                false,
                null,
                null,
                false,
                null,
                screenAge,
                screenDetail,
                screenFresh);
        }

        var offBattleScreen = !string.Equals(
            observation.GameState,
            "active_battle",
            StringComparison.Ordinal);
        string? wave = null;
        string? waveDetail = null;
        var waveRetained = false;
        var waveMetric = metrics.Wave;
        if (waveMetric is not null && ValidWave(waveMetric))
        {
            waveRetained = offBattleScreen
                || !string.Equals(
                    observation.ObservationId,
                    waveMetric.ObservationId,
                    StringComparison.Ordinal)
                || observation.Wave != waveMetric.Value;
            wave = waveMetric.Value.ToString(CultureInfo.InvariantCulture)
                + (waveRetained ? "*" : "");
            waveDetail = $"Screen OCR observed wave {wave.TrimEnd('*')} "
                + $"{FormatAge(waveMetric.AgeSeconds!.Value)} ago."
                + (waveRetained
                    ? " * Last proven battle-screen value; it is not a live reading from the current frame."
                    : " This value comes from the current canonical observation frame.");
        }

        string? coins = null;
        string? coinsDetail = null;
        var coinsRetained = false;
        var coinMetric = metrics.CoinsPerMinute;
        if (coinMetric is not null && ValidCoins(coinMetric))
        {
            coinsRetained = offBattleScreen;
            coins = coinMetric.Value
                + (coinsRetained ? "*" : "");
            coinsDetail = "Latest accepted periodic Coins/min screen OCR: "
                + $"{coinMetric.Value}, "
                + $"{FormatAge(coinMetric.AgeSeconds!.Value)} ago."
                + (coinsRetained
                    ? " * Last proven battle-screen value; it is not a live reading from the current screen."
                    : " The sampling cadence is independent of the main-loop frame cadence.");
        }

        return new BattleScreenMetricPresentation(
            wave,
            waveRetained,
            waveDetail,
            coins,
            coinsRetained,
            coinsDetail,
            screenAge,
            screenDetail,
            screenFresh);
    }

    private static bool ValidIdentity(string? value) =>
        value is { Length: 64 }
        && value.All(character =>
            character is >= '0' and <= '9'
                or >= 'a' and <= 'f');

    private static bool ValidWave(ActiveBattleWaveMetricStatus? value) =>
        value is not null
        && value.Value >= 0
        && value.AgeSeconds is >= 0
        && !string.IsNullOrWhiteSpace(value.ObservationId)
        && value.ObservationId.Length <= 128
        && DateTimeOffset.TryParse(
            value.ObservedAt,
            CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind,
            out _);

    private static bool ValidCoins(ActiveBattleTextMetricStatus? value) =>
        value is not null
        && !string.IsNullOrWhiteSpace(value.Value)
        && value.Value.Length <= 96
        && value.AgeSeconds is >= 0
        && !string.IsNullOrWhiteSpace(value.ObservationId)
        && value.ObservationId.Length <= 128
        && DateTimeOffset.TryParse(
            value.ObservedAt,
            CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind,
            out _);

    private static string FormatAge(int seconds) => seconds switch
    {
        < 60 => $"{seconds}s",
        < 3600 => $"{seconds / 60}m",
        < 86400 => $"{seconds / 3600}h {seconds % 3600 / 60}m",
        _ => $"{seconds / 86400}d {seconds % 86400 / 3600}h",
    };
}
