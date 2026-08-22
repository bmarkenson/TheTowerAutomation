namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class CellBalancePresenterTests
{
    [Fact]
    public void FormatsFallingTrendAndConfiguredHeadroom()
    {
        var presentation = CellBalancePresenter.Present(
            Observed(
                direction: "falling",
                basis: "24h_window",
                change: "-500000",
                elapsedHours: "25",
                rate: "-20000",
                bufferStatus: "above",
                headroom: "5000000"));

        Assert.Equal("25M", presentation.Total);
        Assert.Equal("↓ -500K / 24h", presentation.Trend);
        Assert.Equal("20M floor · 5M above", presentation.Buffer);
        Assert.True(presentation.TrendFalling);
        Assert.False(presentation.BufferWarning);
        Assert.Contains("Comparable net change: -500K", presentation.Detail);
        Assert.Contains("no Cells are set aside", presentation.Detail);
        Assert.Contains("structural observation only", presentation.Detail);
        Assert.Contains("automatic Lab Speedup reduction is disabled", presentation.Detail);
    }

    [Fact]
    public void HighlightsABreachedReserveWithoutGrantingAutomation()
    {
        var status = Observed(
            direction: "rising",
            basis: "since_comparable_start",
            change: "100000",
            elapsedHours: "2",
            rate: "50000",
            bufferStatus: "below",
            headroom: "-250000");

        var presentation = CellBalancePresenter.Present(status);

        Assert.Equal("↑ +100K / 2h", presentation.Trend);
        Assert.Equal("20M floor · 250K below", presentation.Buffer);
        Assert.False(presentation.TrendFalling);
        Assert.True(presentation.BufferWarning);
        Assert.False(status.Buffer!.AutomaticReductionEnabled);
    }

    [Fact]
    public void ShowsCollectionStateForTheFirstObservation()
    {
        var status = Observed(
            direction: "unknown",
            basis: "insufficient_history",
            change: null,
            elapsedHours: null,
            rate: null,
            bufferStatus: "not_configured",
            headroom: null);
        status.Buffer!.FloorDecimal = null;

        var presentation = CellBalancePresenter.Present(status);

        Assert.Equal("25M", presentation.Total);
        Assert.Equal("Collecting", presentation.Trend);
        Assert.Equal("Not set", presentation.Buffer);
        Assert.False(presentation.TrendFalling);
        Assert.False(presentation.BufferWarning);
    }

    [Fact]
    public void KeepsAValidUnavailableTrackerVisible()
    {
        var presentation = CellBalancePresenter.Present(
            new CellBalanceStatus
            {
                SchemaVersion = 1,
                Status = "unavailable",
                Reason = "cell_balance_history_empty",
                Buffer = new CellBalanceBufferStatus
                {
                    Status = "not_configured",
                    AutomaticReductionEnabled = false,
                },
                History = new CellBalanceHistoryStatus
                {
                    RetentionDays = 90,
                    MaxSamples = 30000,
                },
            });

        Assert.Equal("Not observed", presentation.Total);
        Assert.Equal("Collecting", presentation.Trend);
        Assert.Equal("Not set", presentation.Buffer);
        Assert.Contains("cell balance history empty", presentation.Detail);
    }

    [Fact]
    public void HidesMalformedOrActionableProjection()
    {
        Assert.Null(CellBalancePresenter.Present(null).Total);

        var actionable = Observed(
            direction: "flat",
            basis: "24h_window",
            change: "0",
            elapsedHours: "24",
            rate: "0",
            bufferStatus: "above",
            headroom: "5000000");
        actionable.UiActionAuthority = true;
        Assert.Null(CellBalancePresenter.Present(actionable).Total);

        var automatic = Observed(
            direction: "flat",
            basis: "24h_window",
            change: "0",
            elapsedHours: "24",
            rate: "0",
            bufferStatus: "above",
            headroom: "5000000");
        automatic.Buffer!.AutomaticReductionEnabled = true;
        Assert.Null(CellBalancePresenter.Present(automatic).Total);

        var malformed = Observed(
            direction: "falling",
            basis: "24h_window",
            change: "500",
            elapsedHours: "24",
            rate: "-20",
            bufferStatus: "above",
            headroom: "5000000");
        Assert.Null(CellBalancePresenter.Present(malformed).Total);

        var inconsistentRate = Observed(
            direction: "falling",
            basis: "24h_window",
            change: "-500",
            elapsedHours: "24",
            rate: "20",
            bufferStatus: "above",
            headroom: "5000000");
        Assert.Null(CellBalancePresenter.Present(inconsistentRate).Total);
    }

    private static CellBalanceStatus Observed(
        string direction,
        string basis,
        string? change,
        string? elapsedHours,
        string? rate,
        string bufferStatus,
        string? headroom) =>
        new()
        {
            SchemaVersion = 1,
            Status = "observed",
            CapturedAt = "2026-08-20T20:00:00+00:00",
            AgeSeconds = 75,
            BalanceDecimal = "25000000",
            Unit = "cells",
            Trend = new CellBalanceTrendStatus
            {
                Direction = direction,
                Basis = basis,
                ChangeDecimal = change,
                ElapsedHoursDecimal = elapsedHours,
                NetPerHourDecimal = rate,
            },
            Buffer = new CellBalanceBufferStatus
            {
                Status = bufferStatus,
                FloorDecimal = "20000000",
                HeadroomDecimal = headroom,
                AutomaticReductionEnabled = false,
            },
            History = new CellBalanceHistoryStatus
            {
                SampleCount = 200,
                ComparableSampleCount = 195,
                RetentionDays = 90,
                MaxSamples = 30000,
            },
            Provenance = new CellBalanceProvenanceStatus
            {
                AcquisitionType = "passive_stable_read",
                MappingId = "data-9-game-1101",
                GameVersion = 1101,
                EvidenceLevel = "structural_observation",
            },
            UiActionAuthority = false,
        };
}
