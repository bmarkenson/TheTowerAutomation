namespace TheTower.ControlSurface;

internal sealed record ControlSurfaceCompatibilityResult(
    int ApiVersion,
    int ServerRevision,
    IReadOnlyList<string> MissingCapabilities)
{
    public bool ApiVersionSupported =>
        ApiVersion == ControlSurfaceCompatibility.RequiredApiVersion;

    public bool ServerRevisionSupported =>
        ServerRevision >= ControlSurfaceCompatibility.MinimumServerRevision;

    public bool IsCompatible =>
        ApiVersionSupported
        && ServerRevisionSupported
        && MissingCapabilities.Count == 0;
}

internal static class ControlSurfaceCompatibility
{
    public const int RequiredApiVersion = 1;
    // Advance this when the client depends on the matching newer Linux
    // CONTROL_SURFACE_REVISION; older clients may retain a lower minimum.
    public const int MinimumServerRevision = 15;

    private static readonly string[] RequiredCapabilities =
    [
        "active_battle_strategy_adoption",
        "advisory_preflight_decisions",
        "attached_automation_restart",
        "automatic_battle_attachment",
        "completed_battle_discard",
        "current_run_activity_scope",
        "exclusive_strategy_validation_status",
        "explicit_strategy_disposition",
        "game_speed_target",
        "host_performance_gpu_v1",
        "host_performance_telemetry_v1",
        "selected_strategy_process_start",
        "tournament_launch_confirmation",
    ];

    public static ControlSurfaceCompatibilityResult Evaluate(StatusResponse status)
    {
        var capabilities = status.Capabilities ?? [];
        var missingCapabilities = RequiredCapabilities
            .Where(capability =>
                !capabilities.Contains(capability, StringComparer.Ordinal))
            .ToArray();
        return new ControlSurfaceCompatibilityResult(
            status.ApiVersion,
            status.ServerRevision,
            missingCapabilities);
    }
}
