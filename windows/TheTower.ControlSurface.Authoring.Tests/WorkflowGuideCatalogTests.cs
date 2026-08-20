namespace TheTower.ControlSurface;

public sealed class WorkflowGuideCatalogTests
{
    [Fact]
    public void CatalogHasStableUniqueTopicsAndNavigationTargets()
    {
        Assert.Equal(
            [
                WorkflowGuideIds.Controls,
                WorkflowGuideIds.MoveEmulator,
                WorkflowGuideIds.RestartBlueStacks,
                WorkflowGuideIds.EditStrategy,
            ],
            WorkflowGuideCatalog.All.Select(guide => guide.Id));
        Assert.Equal(
            WorkflowGuideCatalog.All.Count,
            WorkflowGuideCatalog.All.Select(guide => guide.Id).Distinct().Count());
        Assert.All(
            WorkflowGuideCatalog.All,
            guide =>
            {
                Assert.NotEmpty(guide.Sections);
                Assert.False(string.IsNullOrWhiteSpace(guide.CanonicalSource));
                Assert.False(string.IsNullOrWhiteSpace(guide.NavigationLabel));
            });
        Assert.Equal(
            Enum.GetValues<WorkflowGuideDestination>().Order(),
            WorkflowGuideCatalog.All
                .Select(guide => guide.Destination)
                .Order());
    }

    [Fact]
    public void MoveGuideRetainsTheSafeMidBattleHandoffSequence()
    {
        var text = FullText(WorkflowGuideIds.MoveEmulator);

        Assert.Contains("indefinite Automation Paused", text);
        Assert.Contains("Do not use a timed Pause", text);
        Assert.Contains("acknowledged", text);
        Assert.Contains("Stop ADB forward", text);
        Assert.Contains("wait for ADB SSH Active", text);
        Assert.Contains("Use this PC's emulator", text);
        Assert.Contains("fresh correct screen and Screen Age", text);
        Assert.Contains("Automation Enabled", text);
        Assert.Contains("Do not Completely stop automation", text);
        Assert.Contains("Surrender", text);
        Assert.Contains("mixed-host", text);
    }

    [Fact]
    public void RestartGuideSeparatesCorroboratedEvidenceFromNoise()
    {
        var text = FullText(WorkflowGuideIds.RestartBlueStacks);

        Assert.Contains("eligible restart or would trigger (disabled)", text);
        Assert.Contains("ten-minute handle median", text);
        Assert.Contains("three consecutive save-backed intervals", text);
        Assert.Contains("What is not enough by itself", text);
        Assert.Contains("Restart BlueStacks…", text);
        Assert.Contains("possible non-earning replay", text);
        Assert.Contains("Do not kill a generic BlueStacks PID", text);
    }

    [Fact]
    public void StrategyGuidePreservesReviewedPublicationSemantics()
    {
        var text = FullText(WorkflowGuideIds.EditStrategy);

        Assert.Contains("Tools → Strategy profiles…", text);
        Assert.Contains("Clone Strategy", text);
        Assert.Contains("Validate draft", text);
        Assert.Contains("Review & Publish…", text);
        Assert.Contains("current battle must remain unchanged", text);
        Assert.Contains("queues ordinary next-boundary use", text);
        Assert.Contains("Publishing a Base submits no control request", text);
        Assert.Contains("Capture current setup as…", text);
    }

    [Fact]
    public void ControlGuideKeepsAuthorityLayersIndependent()
    {
        var text = FullText(WorkflowGuideIds.Controls);

        Assert.Contains("Process lifecycle", text);
        Assert.Contains("Action authority", text);
        Assert.Contains("Battle workflow", text);
        Assert.Contains("Connections", text);
        Assert.Contains("Strategy scope", text);
        Assert.Contains("Do not infer across layers", text);
    }

    [Fact]
    public void UnknownGuideIdFailsExplicitly()
    {
        var exception = Assert.Throws<ArgumentOutOfRangeException>(
            () => WorkflowGuideCatalog.Get("missing"));

        Assert.Equal("id", exception.ParamName);
    }

    private static string FullText(string id)
    {
        var guide = WorkflowGuideCatalog.Get(id);
        return string.Join(
            "\n",
            new[] { guide.Title, guide.Summary, guide.CanonicalSource }
                .Concat(guide.Sections.SelectMany(section =>
                    new[] { section.Heading, section.Introduction }
                        .Concat(section.Items.Select(item => item.Text)))));
    }
}
