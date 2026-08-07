namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class ControlSurfaceCompatibilityTests
{
    private static StatusResponse Status(
        int revision,
        params string[] capabilities)
    {
        return new StatusResponse
        {
            ApiVersion = 1,
            ServerRevision = revision,
            Capabilities = capabilities.ToList(),
        };
    }

    [Fact]
    public void BetterControlActionsRejectOldServerRevision()
    {
        var result = ControlSurfaceCompatibility.Evaluate(
            Status(27, "better_control_model_v1")
        );

        Assert.False(result.IsCompatible);
        Assert.False(result.ServerRevisionSupported);
    }

    [Fact]
    public void BetterControlActionsRejectMissingCapability()
    {
        var status = Status(28);
        var result = ControlSurfaceCompatibility.Evaluate(status);

        Assert.False(result.IsCompatible);
        Assert.Contains("better_control_model_v1", result.MissingCapabilities);
    }
}
