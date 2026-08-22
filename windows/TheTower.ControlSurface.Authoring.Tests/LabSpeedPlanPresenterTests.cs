namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class LabSpeedPlanPresenterTests
{
    [Fact]
    public void PresentsHistoricalIncomeAndBothPlansWithoutGrantingAutomation()
    {
        var status = Ready();

        var presentation = LabSpeedPlanPresenter.Present(status);

        Assert.True(presentation.Visible);
        Assert.Equal("Planner ready", presentation.Badge);
        Assert.Equal("200K/h · 8 battles", presentation.HistoricalGross);
        Assert.Equal("-15K/h", presentation.ActualNet);
        Assert.Equal("251.9K/h burn · -51.9K/h net", presentation.NormalProjection);
        Assert.Equal("59.5K/h burn · +140.5K/h net", presentation.ReserveProjection);
        Assert.Equal(
            "Normal 6x/6x/6x/6x/5x · -51.9K/h net",
            presentation.StatusSummary);
        Assert.False(presentation.Warning);
        Assert.Contains("automatic application is disabled", presentation.Detail);
        Assert.False(status.AutomaticApplicationEnabled);
        Assert.False(status.UiActionAuthority);
    }

    [Fact]
    public void HighlightsAReservePlanThatStillDeclines()
    {
        var status = Ready();
        status.ReservePlan.ProjectedNetPerHourDecimal = "-100";
        status.Recommendation = new LabSpeedRecommendationStatus
        {
            Status = "reserve_plan_still_declines",
            Reason = "Historical gross Cell income does not cover the reserve plan",
        };

        var presentation = LabSpeedPlanPresenter.Present(status);

        Assert.True(presentation.Warning);
        Assert.Equal("59.5K/h burn · -100/h net", presentation.ReserveProjection);
    }

    [Fact]
    public void HighlightsObservedDeclineThatConflictsWithTheForecast()
    {
        var status = Ready();
        status.Recommendation = new LabSpeedRecommendationStatus
        {
            Status = "observed_decline_despite_forecast",
            Reason = "Historical income covers normal, but the balance is falling",
        };

        Assert.True(LabSpeedPlanPresenter.Present(status).Warning);
    }

    [Fact]
    public void HighlightsObservedDeclineWhileReservePlanRecovers()
    {
        var status = Ready();
        status.Recommendation = new LabSpeedRecommendationStatus
        {
            Status = "observed_decline_reserve_plan_recovers",
            Reason = "The reserve plan should recover the falling balance",
        };

        Assert.True(LabSpeedPlanPresenter.Present(status).Warning);
    }

    [Fact]
    public void SummarizesIncompleteAndInvalidPoliciesWithoutInventingTargets()
    {
        var incomplete = Ready();
        incomplete.Status = "incomplete";
        incomplete.Policy.Labs[4].NormalSpeed = null;
        incomplete.NormalPlan.Complete = false;
        Assert.Equal(
            "Plan incomplete",
            LabSpeedPlanPresenter.Present(incomplete).StatusSummary);

        var invalid = Ready();
        invalid.Status = "invalid_policy";
        Assert.Equal(
            "Policy invalid",
            LabSpeedPlanPresenter.Present(invalid).StatusSummary);
    }

    [Fact]
    public void HidesActionableOrMalformedPlannerPayloads()
    {
        Assert.False(LabSpeedPlanPresenter.Present(null).Visible);

        var actionable = Ready();
        actionable.AutomaticApplicationEnabled = true;
        Assert.False(LabSpeedPlanPresenter.Present(actionable).Visible);

        var malformed = Ready();
        malformed.Policy.Labs.RemoveAt(0);
        Assert.False(LabSpeedPlanPresenter.Present(malformed).Visible);
    }

    private static LabSpeedPlanStatus Ready() => new()
    {
        SchemaVersion = 1,
        Status = "ready",
        Policy = new CellBalancePolicyStatus
        {
            SchemaVersion = 1,
            AutomaticReductionEnabled = false,
            Labs = Enumerable.Range(1, 5).Select(lab => new LabSpeedPolicyItem
            {
                Lab = lab,
                NormalSpeed = lab == 5 ? "5" : "6",
                ReserveSpeed = "5",
            }).ToList(),
        },
        Income = new HistoricalCellIncomeStatus
        {
            Status = "observed",
            SampleCount = 8,
            CellsPerHourDecimal = "200000",
        },
        ActualBalanceNetPerHourDecimal = "-15000",
        NormalPlan = new LabSpeedProjectionStatus
        {
            Complete = true,
            BurnPerHourDecimal = "251900",
            ProjectedNetPerHourDecimal = "-51900",
        },
        ReservePlan = new LabSpeedProjectionStatus
        {
            Complete = true,
            BurnPerHourDecimal = "59500",
            ProjectedNetPerHourDecimal = "+140500",
        },
        Recommendation = new LabSpeedRecommendationStatus
        {
            Status = "reserve_plan_recovers",
            Reason = "The reserve plan changes projected Cell flow to nonnegative",
        },
        AutomaticApplicationEnabled = false,
        UiActionAuthority = false,
    };
}
