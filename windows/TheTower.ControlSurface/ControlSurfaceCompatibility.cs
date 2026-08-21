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

internal sealed record StrategyScopePresentation(
    string? StartupDefault,
    string? CurrentStrategy,
    string? PendingNextBoundary,
    string? PendingActiveBattle,
    bool Authoritative,
    bool Degraded)
{
    public string? PendingStrategy =>
        PendingActiveBattle ?? PendingNextBoundary;

    public string PendingLabel => PendingActiveBattle is not null
        ? "Pending active adoption"
        : "Pending boundary";
}

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
    public const int MinimumServerRevision = 54;

    private static readonly string[] RequiredCapabilities =
    [
        "active_battle_screen_metrics_v1",
        "active_battle_strategy_adoption",
        "active_run_metrics_v1",
        "advisory_preflight_decisions",
        "better_control_model_v2",
        "bounded_idle_timeout_v1",
        "cell_balance_tracking_v1",
        "lab_speed_reserve_planner_v1",
        "completed_battle_discard",
        "confirmed_local_mapping_status_v2",
        "current_battle_perks_v1",
        "current_run_activity_scope",
        "current_run_phase_timing_v1",
        "exclusive_strategy_validation_status",
        "emulator_host_selection_v1",
        "explicit_strategy_disposition",
        "game_speed_target",
        "host_performance_gpu_v1",
        "host_performance_process_attribution_v1",
        "host_performance_telemetry_v1",
        "bluestacks_maintenance_v2",
        "bluestacks_operator_restart_v1",
        "bluestacks_listener_lifetime_telemetry_v1",
        "bluestacks_maintenance_policy_v1",
        "managed_custom_module_presets_v1",
        "observed_game_speed",
        "paused_terminal_save_refresh_v1",
        "runtime_control_acknowledgements_v1",
        "selected_strategy_process_start",
        "save_backed_setup_capture_v2",
        "save_mapping_staged_candidate_v1",
        "save_mapping_candidate_disposition_v1",
        "save_mapping_automatic_promotion_v1",
        "save_mapping_machine_verification_v1",
        "save_mapping_review_status_v2",
        "strategy_aware_attach_v1",
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

    public static bool CanOpenSaveMappingIntegration(
        ControlSurfaceCompatibilityResult? compatibility) =>
        compatibility?.IsCompatible == true;

    public static bool IsActivePauseAcknowledged(
        StatusResponse status,
        bool processActive) =>
        processActive
        && string.Equals(
            status.Control.State,
            "PAUSED",
            StringComparison.OrdinalIgnoreCase)
        && status.Control.RemainingSeconds is null or > 0
        && status.Acknowledgements.State is { AcknowledgesCurrent: true };

    public static BetterControlActionAvailability ResolveAttachAvailability(
        BetterControlActionAvailability serverAvailability,
        bool strategySelectionDirty,
        bool strategyRequestInFlight)
    {
        if (!serverAvailability.Available)
        {
            return serverAvailability;
        }
        if (strategyRequestInFlight)
        {
            return new BetterControlActionAvailability
            {
                Available = false,
                Code = "strategy_selection_pending",
                Reason = "Wait for Linux to accept the selected Strategy before attaching.",
            };
        }
        if (strategySelectionDirty)
        {
            return new BetterControlActionAvailability
            {
                Available = false,
                Code = "strategy_selection_unaccepted",
                Reason = "Attach uses the accepted Strategy, so retry this selection or reselect the accepted Strategy first.",
            };
        }
        return serverAvailability;
    }

    public static StrategyScopePresentation ResolveStrategyScope(
        StatusResponse status,
        bool processActive,
        string? configuredStrategy)
    {
        var authoritative = (status.Capabilities ?? []).Contains(
            "better_control_model_v2",
            StringComparer.Ordinal);
        if (authoritative)
        {
            var scope = status.ControlModel?.StrategyScope;
            return new StrategyScopePresentation(
                NormalizeStrategy(scope?.StartupDefault),
                processActive
                    ? NormalizeStrategy(scope?.ActiveBattle)
                    : null,
                processActive
                    ? NormalizeStrategy(scope?.PendingNextBoundary)
                    : null,
                processActive
                    ? NormalizeStrategy(scope?.PendingActiveBattle)
                    : null,
                true,
                processActive && scope?.Degradation is not null);
        }

        var configured = NormalizeStrategy(configuredStrategy);
        var requested = NormalizeStrategy(status.Control.Strategy)
            ?? configured;
        var pending = processActive
            && status.Control.Strategy is not null
            && status.Acknowledgements.Strategy is not
                { AcknowledgesCurrent: true };
        var current = !processActive
            ? null
            : status.Control.Strategy is null
                ? configured
                : NormalizeStrategy(
                    status.Acknowledgements.Strategy?.Value);
        var activeRequest = pending
            && string.Equals(
                status.Control.StrategyApplyMode,
                "active_battle",
                StringComparison.OrdinalIgnoreCase);
        return new StrategyScopePresentation(
            configured,
            current,
            pending && !activeRequest ? requested : null,
            activeRequest ? requested : null,
            false,
            false);
    }

    public static ConfirmedLocalMappingPresentation ConfirmedLocalMapping(
        ConfirmedLocalMappingStatus? status)
    {
        if (status is null || status.SchemaVersion != 2 || status.Items is null)
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
            "integration_unconfirmed",
            "integration_recovery_required",
            "restaging_required",
            "promotion_pending",
            "automatic_integration_pending",
            "promotion_cleanup_pending",
            "remote_publication_pending",
            "production_validation_pending",
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
                "integration_unconfirmed",
            ]);
        var winningState = visibleItems[0].State;
        var severity = dangerous
            ? "danger"
            : winningState == "integration_recovery_required"
                || winningState == "restaging_required"
                || winningState == "authority_pending"
                || winningState == "active_local"
                || winningState == "review_required"
                || winningState == "more_evidence_required"
                ? "warning"
                : "info";
        var title = dangerous
            ? "A local save mapping needs attention"
            : winningState == "integration_recovery_required"
                ? "Save-mapping integration recovery requires direction"
            : winningState == "restaging_required"
                ? "Save mapping must be restaged on current main"
            : winningState == "promotion_pending"
                ? "Verified save mapping queued for automatic promotion"
            : winningState == "automatic_integration_pending"
                ? "Verified save mapping queued for automatic integration"
            : winningState == "promotion_cleanup_pending"
                ? "Published save mapping awaiting automatic cleanup"
            : winningState == "remote_publication_pending"
                ? "Save mapping awaiting automatic publication"
            : winningState == "production_validation_pending"
                ? "Deployed save mapping awaiting fresh validation"
            : winningState == "authority_pending"
                ? "Canonical Module identity owner is still pending"
                : winningState == "active_local"
                    ? "A locally confirmed Module identity needs integration"
                    : winningState == "review_required"
                        || winningState == "more_evidence_required"
                        ? "A save mapping observation needs review"
                    : "Exact-version Module identity mirror is pending";
        var first = visibleItems[0];
        var slot = first.Scope is not null
            && first.Scope.TryGetValue("slot_key", out var slotKey)
            && !string.IsNullOrWhiteSpace(slotKey)
                ? $" {slotKey}"
                : "";
        var value = first.RawValue is decimal rawValue
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
            ? $" {visibleItems.Length} records remain pending."
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
                or "evidence_ambiguous" or "integration_unconfirmed" => 0,
            "integration_recovery_required" or "restaging_required" => 1,
            "promotion_pending" or "automatic_integration_pending" => 2,
            "promotion_cleanup_pending" or "remote_publication_pending" => 3,
            "production_validation_pending" => 4,
            "authority_pending" => 4,
            "active_local" => 5,
            "review_required" => 6,
            "more_evidence_required" => 7,
            "mirror_pending" => 8,
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

    private static string? NormalizeStrategy(string? strategy)
    {
        var normalized = strategy?.Trim().ToLowerInvariant();
        return string.IsNullOrWhiteSpace(normalized) ? null : normalized;
    }
}
