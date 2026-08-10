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

internal sealed record BetterControlWorkflowPresentation(
    string Status,
    string Label,
    bool Pending,
    bool Terminal);

internal sealed record ConfirmedLocalMappingPresentation(
    bool Visible,
    string Severity,
    string Title,
    string Detail);

internal enum SetupCaptureOpenAction
{
    Unavailable,
    Request,
    Progress,
    Review,
    Inspect,
}

internal static class ControlSurfaceCompatibility
{
    public const int RequiredApiVersion = 1;
    // Advance this when the client depends on the matching newer Linux
    // CONTROL_SURFACE_REVISION; older clients may retain a lower minimum.
    public const int MinimumServerRevision = 34;

    private static readonly string[] RequiredCapabilities =
    [
        "active_battle_strategy_adoption",
        "advisory_preflight_decisions",
        "better_control_model_v2",
        "completed_battle_discard",
        "confirmed_local_mapping_status_v1",
        "current_battle_perks_v1",
        "current_run_activity_scope",
        "exclusive_strategy_validation_status",
        "explicit_strategy_disposition",
        "game_speed_target",
        "host_performance_gpu_v1",
        "host_performance_telemetry_v1",
        "managed_custom_module_presets_v1",
        "observed_game_speed",
        "selected_strategy_process_start",
        "save_backed_setup_capture_v2",
        "save_mapping_review_status_v1",
        "strategy_action_gate_v1",
        "strategy_authoring_local_loadout_editors_v1",
        "strategy_authoring_preset_local_copy_v1",
        "strategy_authoring_profile_lifecycle_v1",
        "strategy_authoring_specialized_editors_v1",
        "strategy_authoring_v1",
        "strategy_profile_catalog_v1",
        "strategy_profile_editor_v2",
        "strategy_revision_history_v1",
        "terminal_dispositions_v2",
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

    public static bool CanOpenSetupCapture(
        ControlSurfaceCompatibilityResult? compatibility,
        BetterControlModelStatus? model)
    {
        if (compatibility?.IsCompatible != true || model is null)
        {
            return false;
        }
        return SetupCaptureAction(model) != SetupCaptureOpenAction.Unavailable;
    }

    public static ConfirmedLocalMappingPresentation ConfirmedLocalMapping(
        ConfirmedLocalMappingStatus? status)
    {
        if (status is null)
        {
            return new(
                true,
                "danger",
                "Local save mapping status is unavailable",
                "The connected API does not publish the required mapping-status contract. "
                    + "Update or restart the Linux API; automation authority is unchanged.");
        }
        if (!status.Available)
        {
            return new(
                true,
                "danger",
                "Local save mapping status is unavailable",
                string.IsNullOrWhiteSpace(status.Reason)
                    ? "The local confirmation store could not be read. "
                        + "Affected checks continue through UI fallback."
                    : status.Reason);
        }

        var visibleStates = new HashSet<string>(StringComparer.Ordinal)
        {
            "active_local",
            "authority_pending",
            "mirror_pending",
            "canonical_conflict",
            "reconfirmation_required",
            "invalid_local_store",
            "identity_conflict",
            "review_required",
            "more_evidence_required",
            "evidence_ambiguous",
        };
        var visibleItems = (status.Items ?? [])
            .Where(item => visibleStates.Contains(item.State))
            .OrderBy(item => ConfirmedLocalMappingStatePriority(item.State))
            .ToArray();
        if (visibleItems.Length == 0)
        {
            return new(false, "neutral", "", "");
        }

        var states = visibleItems
            .Select(item => item.State)
            .ToHashSet(StringComparer.Ordinal);
        var dangerous = states.Overlaps(
            [
                "canonical_conflict",
                "reconfirmation_required",
                "invalid_local_store",
                "identity_conflict",
                "evidence_ambiguous",
            ]);
        var severity = dangerous
            ? "danger"
            : states.Contains("authority_pending")
                || states.Contains("active_local")
                || states.Contains("review_required")
                || states.Contains("more_evidence_required")
                ? "warning"
                : "info";
        var title = dangerous
            ? "A local save mapping needs attention"
            : states.Contains("authority_pending")
                ? "Canonical save-mapping authority is still pending"
                : states.Contains("active_local")
                    ? "A locally confirmed save mapping needs integration"
                    : states.Contains("review_required")
                        || states.Contains("more_evidence_required")
                        ? "A save mapping observation needs review"
                    : "Exact-version save-mapping mirror is pending";
        var first = visibleItems[0];
        var slot = first.Scope is not null
            && first.Scope.TryGetValue("slot_key", out var slotKey)
            && !string.IsNullOrWhiteSpace(slotKey)
                ? $" {slotKey}"
                : "";
        var value = first.RawValue is double rawValue
            ? $": save value {rawValue} = "
                + (string.IsNullOrWhiteSpace(first.SemanticValue)
                    ? "unknown"
                    : first.SemanticValue)
            : "";
        var mapping = string.IsNullOrWhiteSpace(first.MappingId)
            ? ""
            : $" for {first.MappingId}";
        var reason = string.IsNullOrWhiteSpace(first.Reason)
            ? "Canonical integration is pending."
            : first.Reason;
        var count = visibleItems.Length > 1
            ? $" {visibleItems.Length} records require review."
            : "";
        var subject = string.Equals(first.CheckId, "modules", StringComparison.Ordinal)
            ? $"Module{slot}"
            : string.IsNullOrWhiteSpace(first.CheckId)
                ? $"Save mapping{slot}"
                : $"{first.CheckId.Replace('_', ' ')}{slot}";
        return new(
            true,
            severity,
            title,
            $"{subject}{value}{mapping}. {reason}{count}");
    }

    private static int ConfirmedLocalMappingStatePriority(string? state) =>
        state switch
        {
            "canonical_conflict" or "identity_conflict"
                or "invalid_local_store" or "reconfirmation_required"
                or "evidence_ambiguous" => 0,
            "authority_pending" or "active_local" or "review_required"
                or "more_evidence_required" => 1,
            "mirror_pending" => 2,
            _ => 99,
        };

    public static SetupCaptureOpenAction SetupCaptureAction(
        BetterControlModelStatus model)
    {
        var status = model.SetupCapture?.Status ?? "";
        if (status == "ready")
        {
            return SetupCaptureOpenAction.Review;
        }
        if (status is "requested" or "acknowledged" or "capturing")
        {
            return SetupCaptureOpenAction.Progress;
        }
        if (status is "saved" or "cancelled" or "unavailable"
            or "interrupted" or "failed")
        {
            return SetupCaptureOpenAction.Inspect;
        }
        var available = model.Actions.TryGetValue(
                "capture_current_setup",
                out var availability)
            && availability.Available;
        return available
            ? SetupCaptureOpenAction.Request
            : SetupCaptureOpenAction.Unavailable;
    }

    public static BetterControlWorkflowPresentation PresentWorkflow(
        string? status)
    {
        var normalized = (status ?? "unknown").Trim().ToLowerInvariant();
        var label = normalized switch
        {
            "requested" => "Requested",
            "pending" => "Pending acknowledgement",
            "acknowledged" => "Acknowledged",
            "action_dispatched" => "Action dispatched",
            "validating_save" => "Validating fresh save",
            "ready" => "Ready",
            "completed" => "Completed",
            "no_op" => "No change needed",
            "stale" => "Stale",
            "rejected" => "Rejected",
            "unavailable" => "Unavailable",
            "interrupted" => "Interrupted",
            "failed" => "Failed",
            "cancelled" => "Cancelled",
            _ => string.Join(
                " ",
                normalized.Split(
                    '_',
                    StringSplitOptions.RemoveEmptyEntries)
                    .Select(token => char.ToUpperInvariant(token[0]) + token[1..])),
        };
        var pending = normalized is "requested"
            or "pending"
            or "acknowledged"
            or "action_dispatched"
            or "validating_save";
        var terminal = normalized is "completed"
            or "no_op"
            or "stale"
            or "rejected"
            or "unavailable"
            or "interrupted"
            or "failed"
            or "cancelled";
        return new BetterControlWorkflowPresentation(
            normalized,
            label,
            pending,
            terminal);
    }
}
