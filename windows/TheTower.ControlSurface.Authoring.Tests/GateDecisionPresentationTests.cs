namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class GateDecisionPresentationTests
{
    [Fact]
    public void BlockingSessionGateUsesRunningSessionLanguage()
    {
        var presentation = GateDecisionPresentation.From(
            new GateDecisionStatus
            {
                Blocking = true,
                Phase = "session_preflight",
                CheckId = "auto_pick_perks",
            });

        Assert.Equal("Session preflight needs direction", presentation.Title);
        Assert.Equal(
            "A running-session requirement could not be satisfied",
            presentation.Heading);
        Assert.Equal(
            "Check: Auto Pick Perks (Session Preflight)",
            presentation.CheckText);
        Assert.Contains(
            "safe status and diagnostic collection",
            presentation.Disposition);
    }

    [Fact]
    public void BlockingHomeGateKeepsStartupLanguageAndHumanizesCheck()
    {
        var presentation = GateDecisionPresentation.From(
            new GateDecisionStatus
            {
                Blocking = true,
                Phase = "home_setup",
                CheckId = "free_upgrade_locks",
            });

        Assert.Equal("Startup gate needs direction", presentation.Title);
        Assert.Equal(
            "A startup requirement could not be satisfied",
            presentation.Heading);
        Assert.Equal(
            "Check: Free Upgrade Locks (Home Setup)",
            presentation.CheckText);
    }

    [Fact]
    public void AdvisorySessionGateKeepsWarningLanguage()
    {
        var presentation = GateDecisionPresentation.From(
            new GateDecisionStatus
            {
                Blocking = false,
                Phase = "session_preflight",
                CheckId = "ultimate_weapons",
            });

        Assert.Equal("Preflight warning needs direction", presentation.Title);
        Assert.Equal("A read-only preflight found a mismatch", presentation.Heading);
        Assert.Contains("Tournament observation continues", presentation.Disposition);
    }
}
