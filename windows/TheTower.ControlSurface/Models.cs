using System.Text.Json;
using System.Text.Json.Serialization;

namespace TheTower.ControlSurface;

public sealed class StatusResponse
{
    [JsonPropertyName("api_version")]
    public int ApiVersion { get; set; }

    [JsonPropertyName("server_revision")]
    public int ServerRevision { get; set; }

    [JsonPropertyName("capabilities")]
    public List<string> Capabilities { get; set; } = [];

    [JsonPropertyName("healthy")]
    public bool Healthy { get; set; }

    [JsonPropertyName("control")]
    public ControlStatus Control { get; set; } = new();

    [JsonPropertyName("acknowledgements")]
    public AcknowledgementStatus Acknowledgements { get; set; } = new();

    [JsonPropertyName("observation")]
    public ObservationStatus? Observation { get; set; }

    [JsonPropertyName("prior_transition")]
    public ObservationStatus? PriorTransition { get; set; }

    [JsonPropertyName("current_run")]
    public CurrentRunStatus? CurrentRun { get; set; }

    [JsonPropertyName("current_battle_perks")]
    public CurrentBattlePerksStatus CurrentBattlePerks { get; set; } = new();

    [JsonPropertyName("strategy_action_gate")]
    public StrategyActionGateStatus? StrategyActionGate { get; set; }

    [JsonPropertyName("control_model")]
    public BetterControlModelStatus? ControlModel { get; set; }

    [JsonPropertyName("runtime")]
    public RuntimeStatus Runtime { get; set; } = new();

    [JsonPropertyName("process_service")]
    public ProcessServiceStatus? ProcessService { get; set; }

    [JsonPropertyName("request")]
    public RequestStatus? Request { get; set; }
}

public sealed class StrategyActionGateStatus
{
    [JsonPropertyName("available")]
    public bool Available { get; set; }

    [JsonPropertyName("active")]
    public bool Active { get; set; }

    [JsonPropertyName("stale")]
    public bool Stale { get; set; }

    [JsonPropertyName("age_seconds")]
    public int? AgeSeconds { get; set; }

    [JsonPropertyName("strategy")]
    public string? Strategy { get; set; }

    [JsonPropertyName("battle_scope")]
    public string? BattleScope { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("phase")]
    public string? Phase { get; set; }

    [JsonPropertyName("reason")]
    public string Reason { get; set; } = "";

    [JsonPropertyName("failed_check_ids")]
    public List<string> FailedCheckIds { get; set; } = [];

    [JsonPropertyName("allowed_auxiliary_collectors")]
    public List<string> AllowedAuxiliaryCollectors { get; set; } = [];

    [JsonPropertyName("activated_at")]
    public string? ActivatedAt { get; set; }

    [JsonPropertyName("observation_authority")]
    public RuntimeActionAuthorityStatus ObservationAuthority { get; set; } = new();

    [JsonPropertyName("auxiliary_collection_authority")]
    public RuntimeActionAuthorityStatus AuxiliaryCollectionAuthority { get; set; } = new();

    [JsonPropertyName("strategy_action_authority")]
    public RuntimeActionAuthorityStatus StrategyActionAuthority { get; set; } = new();

    [JsonPropertyName("lifecycle_action_authority")]
    public RuntimeActionAuthorityStatus LifecycleActionAuthority { get; set; } = new();
}

public sealed class RuntimeActionAuthorityStatus
{
    [JsonPropertyName("action_class")]
    public string ActionClass { get; set; } = "";

    [JsonPropertyName("allowed")]
    public bool Allowed { get; set; }

    [JsonPropertyName("reason")]
    public string Reason { get; set; } = "";
}

public sealed class BetterControlModelStatus
{
    [JsonPropertyName("process")]
    public BetterControlProcessStatus Process { get; set; } = new();

    [JsonPropertyName("action_authority")]
    public BetterControlAuthorityStatus ActionAuthority { get; set; } = new();

    [JsonPropertyName("observation")]
    public BetterControlObservationStatus Observation { get; set; } = new();

    [JsonPropertyName("strategy_scope")]
    public BetterControlStrategyScopeStatus StrategyScope { get; set; } = new();

    [JsonPropertyName("when_battle_ends")]
    public BetterControlTerminalPolicyStatus WhenBattleEnds { get; set; } = new();

    [JsonPropertyName("battle_workflow")]
    public BetterControlBattleWorkflowStatus? BattleWorkflow { get; set; }

    [JsonPropertyName("manual_control")]
    public BetterControlManualStatus? ManualControl { get; set; }

    [JsonPropertyName("setup_capture")]
    public SetupCaptureStatus? SetupCapture { get; set; }

    [JsonPropertyName("actions")]
    public Dictionary<string, BetterControlActionAvailability> Actions { get; set; } = [];
}

public sealed class BetterControlProcessStatus
{
    [JsonPropertyName("state")]
    public string State { get; set; } = "unknown";

    [JsonPropertyName("live")]
    public bool Live { get; set; }
}

public sealed class BetterControlAuthorityStatus
{
    [JsonPropertyName("effective")]
    public string Effective { get; set; } = "unknown";

    [JsonPropertyName("acknowledged")]
    public bool Acknowledged { get; set; }

    [JsonPropertyName("meaning")]
    public string Meaning { get; set; } = "";
}

public sealed class BetterControlObservationStatus
{
    [JsonPropertyName("available")]
    public bool Available { get; set; }

    [JsonPropertyName("game_state")]
    public string GameState { get; set; } = "unknown";

    [JsonPropertyName("freshness")]
    public string Freshness { get; set; } = "unavailable";

    [JsonPropertyName("reason")]
    public string Reason { get; set; } = "";
}

public sealed class BetterControlStrategyScopeStatus
{
    [JsonPropertyName("startup_default")]
    public string? StartupDefault { get; set; }

    [JsonPropertyName("active_battle")]
    public string? ActiveBattle { get; set; }

    [JsonPropertyName("pending_next_boundary")]
    public string? PendingNextBoundary { get; set; }
}

public sealed class BetterControlTerminalPolicyStatus
{
    [JsonPropertyName("value")]
    public string Value { get; set; } = "unknown";

    [JsonPropertyName("compatibility_value")]
    public string CompatibilityValue { get; set; } = "UNKNOWN";

    [JsonPropertyName("request_id")]
    public string? RequestId { get; set; }

    [JsonPropertyName("requested_at")]
    public string? RequestedAt { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = "unknown";

    [JsonPropertyName("reason")]
    public string Reason { get; set; } = "";

    [JsonPropertyName("acknowledgement")]
    public DirectiveAcknowledgement? Acknowledgement { get; set; }
}

public sealed class BetterControlBattleWorkflowStatus
{
    [JsonPropertyName("request_id")]
    public string RequestId { get; set; } = "";

    [JsonPropertyName("intent")]
    public string Intent { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("reason")]
    public string? Reason { get; set; }
}

public sealed class BetterControlManualStatus
{
    [JsonPropertyName("manual_control_id")]
    public string ManualControlId { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("detail")]
    public string? Detail { get; set; }

    [JsonPropertyName("refresh_status")]
    public string? RefreshStatus { get; set; }

    [JsonPropertyName("surrender_collection")]
    public string SurrenderCollection { get; set; } = "minimal";
}

public sealed class SetupCaptureResponse
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("server_revision")]
    public int ServerRevision { get; set; }

    [JsonPropertyName("capability")]
    public string Capability { get; set; } = "";

    [JsonPropertyName("capture")]
    public SetupCaptureStatus? Capture { get; set; }

    [JsonPropertyName("availability")]
    public BetterControlActionAvailability Availability { get; set; } = new();

    [JsonPropertyName("bases")]
    public StrategyBaseCatalog Bases { get; set; } = new();

    [JsonPropertyName("request")]
    public SetupCaptureRequestStatus? Request { get; set; }

    [JsonPropertyName("review")]
    public CapturedStrategyReview? Review { get; set; }
}

public sealed class SetupCaptureStatus
{
    [JsonPropertyName("request_id")]
    public string RequestId { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("reason")]
    public string? Reason { get; set; }

    [JsonPropertyName("authority_outcome")]
    public string? AuthorityOutcome { get; set; }

    [JsonPropertyName("acquisition_source")]
    public string AcquisitionSource { get; set; } = "";

    [JsonPropertyName("source_manual_control_id")]
    public string? SourceManualControlId { get; set; }

    [JsonPropertyName("preview_fingerprint")]
    public string PreviewFingerprint { get; set; } = "";

    [JsonPropertyName("preview")]
    public SetupCapturePreview? Preview { get; set; }

    [JsonPropertyName("saved_result")]
    public SetupCaptureSavedResult? SavedResult { get; set; }
}

public sealed class SetupCapturePreview
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("captured_at")]
    public string? CapturedAt { get; set; }

    [JsonPropertyName("mapping_id")]
    public string MappingId { get; set; } = "";

    [JsonPropertyName("mapping_maturity")]
    public string MappingMaturity { get; set; } = "";

    [JsonPropertyName("settings")]
    public Dictionary<string, JsonElement> Settings { get; set; } = [];

    [JsonPropertyName("unresolved")]
    public List<SetupCaptureUnresolved> Unresolved { get; set; } = [];

    [JsonPropertyName("capture_origin")]
    public SetupCaptureOrigin CaptureOrigin { get; set; } = new();
}

public sealed class SetupCaptureOrigin
{
    [JsonPropertyName("acquisition_source")]
    public string AcquisitionSource { get; set; } = "";

    [JsonPropertyName("source_manual_control_fingerprint")]
    public string? SourceManualControlFingerprint { get; set; }
}

public sealed class SetupCaptureUnresolved
{
    [JsonPropertyName("setting_id")]
    public string SettingId { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "unresolved";

    [JsonPropertyName("reason")]
    public string Reason { get; set; } = "";

    [JsonPropertyName("observed_value")]
    public JsonElement? ObservedValue { get; set; }
}

public sealed class CapturedStrategyReview
{
    [JsonPropertyName("review_fingerprint")]
    public string ReviewFingerprint { get; set; } = "";

    [JsonPropertyName("source")]
    public StrategyAuthoringSource Source { get; set; } = new();

    [JsonPropertyName("resolution")]
    public StrategyAuthoringResolution Resolution { get; set; } = new();

    [JsonPropertyName("captured_vs_base")]
    public JsonElement CapturedVsBase { get; set; }

    [JsonPropertyName("unresolved")]
    public List<SetupCaptureUnresolved> Unresolved { get; set; } = [];

    [JsonPropertyName("saving_activates_strategy")]
    public bool SavingActivatesStrategy { get; set; }

    [JsonPropertyName("publication_activates_strategy")]
    public bool PublicationActivatesStrategy { get; set; }
}

public sealed class SetupCaptureRequestStatus
{
    [JsonPropertyName("accepted")]
    public bool Accepted { get; set; }

    [JsonPropertyName("operation")]
    public string Operation { get; set; } = "";

    [JsonPropertyName("request_id")]
    public string? RequestId { get; set; }

    [JsonPropertyName("disposition")]
    public string? Disposition { get; set; }

    [JsonPropertyName("saved_result")]
    public SetupCaptureSavedResult? SavedResult { get; set; }
}

public sealed class SetupCaptureSavedResult
{
    [JsonPropertyName("kind")]
    public string Kind { get; set; } = "";

    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("artifact_disposition")]
    public string ArtifactDisposition { get; set; } = "";

    [JsonPropertyName("published")]
    public bool Published { get; set; }

    [JsonPropertyName("selected")]
    public bool Selected { get; set; }

    [JsonPropertyName("activated")]
    public bool Activated { get; set; }

    [JsonPropertyName("queued")]
    public bool Queued { get; set; }

    [JsonPropertyName("applied")]
    public bool Applied { get; set; }
}

public sealed class BetterControlActionAvailability
{
    [JsonPropertyName("available")]
    public bool Available { get; set; }

    [JsonPropertyName("code")]
    public string Code { get; set; } = "unavailable";

    [JsonPropertyName("reason")]
    public string Reason { get; set; } = "";
}

public sealed class CurrentRunStatus
{
    [JsonPropertyName("run_id")]
    public string RunId { get; set; } = "";

    [JsonPropertyName("started_at")]
    public string? StartedAt { get; set; }
}

public sealed class CurrentBattlePerksStatus
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = "unavailable";

    [JsonPropertyName("reason")]
    public string Reason { get; set; } = "";

    [JsonPropertyName("source")]
    public string Source { get; set; } = "";

    [JsonPropertyName("order_semantics")]
    public string OrderSemantics { get; set; } = "";

    [JsonPropertyName("captured_at")]
    public string? CapturedAt { get; set; }

    [JsonPropertyName("saved_wave")]
    public int? SavedWave { get; set; }

    [JsonPropertyName("picked_count")]
    public int PickedCount { get; set; }

    [JsonPropertyName("unique_count")]
    public int UniqueCount { get; set; }

    [JsonPropertyName("items")]
    public List<CurrentBattlePerkItem> Items { get; set; } = [];
}

public sealed class CurrentBattlePerkItem
{
    [JsonPropertyName("perk_key")]
    public string PerkKey { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("level")]
    public int Level { get; set; }

    [JsonPropertyName("last_selected_wave")]
    public int LastSelectedWave { get; set; }

    [JsonPropertyName("last_selected_sequence")]
    public int LastSelectedSequence { get; set; }
}

public sealed class AcknowledgementStatus
{
    [JsonPropertyName("state")]
    public DirectiveAcknowledgement? State { get; set; }

    [JsonPropertyName("mode")]
    public DirectiveAcknowledgement? Mode { get; set; }

    [JsonPropertyName("game_speed_target")]
    public DirectiveAcknowledgement? GameSpeedTarget { get; set; }

    [JsonPropertyName("adb_target")]
    public DirectiveAcknowledgement? AdbTarget { get; set; }

    [JsonPropertyName("strategy")]
    public DirectiveAcknowledgement? Strategy { get; set; }
}

public sealed class RequestStatus
{
    [JsonPropertyName("accepted")]
    public bool Accepted { get; set; }

    [JsonPropertyName("action")]
    public string? Action { get; set; }

    [JsonPropertyName("strategy")]
    public string? Strategy { get; set; }

    [JsonPropertyName("disposition")]
    public string? Disposition { get; set; }

    [JsonPropertyName("warning")]
    public string? Warning { get; set; }

    [JsonPropertyName("request_id")]
    public string? RequestId { get; set; }

    [JsonPropertyName("decision_id")]
    public string? DecisionId { get; set; }

    [JsonPropertyName("previous_pid")]
    public int? PreviousPid { get; set; }

    [JsonPropertyName("replacement_pid")]
    public int? ReplacementPid { get; set; }

    [JsonPropertyName("restored_state")]
    public string? RestoredState { get; set; }

    [JsonPropertyName("startup_gate_policy")]
    public string? StartupGatePolicy { get; set; }

    [JsonPropertyName("manual_control_id")]
    public string? ManualControlId { get; set; }
}

public sealed class DirectiveAcknowledgement
{
    [JsonPropertyName("value")]
    public string? Value { get; set; }

    [JsonPropertyName("at")]
    public string? At { get; set; }

    [JsonPropertyName("request_id")]
    public string? RequestId { get; set; }

    [JsonPropertyName("acknowledges_current")]
    public bool AcknowledgesCurrent { get; set; }
}

public sealed class ControlStatus
{
    [JsonPropertyName("state")]
    public string State { get; set; } = "UNKNOWN";

    [JsonPropertyName("mode")]
    public string Mode { get; set; } = "UNKNOWN";

    [JsonPropertyName("state_updated_at")]
    public string? StateUpdatedAt { get; set; }

    [JsonPropertyName("state_request_id")]
    public string? StateRequestId { get; set; }

    [JsonPropertyName("mode_updated_at")]
    public string? ModeUpdatedAt { get; set; }

    [JsonPropertyName("mode_request_id")]
    public string? ModeRequestId { get; set; }

    [JsonPropertyName("game_speed_target")]
    public double GameSpeedTarget { get; set; } = 6.3;

    [JsonPropertyName("remaining_seconds")]
    public int? RemainingSeconds { get; set; }

    [JsonPropertyName("adb_port")]
    public int? AdbPort { get; set; }

    [JsonPropertyName("adb_port_updated_at")]
    public string? AdbPortUpdatedAt { get; set; }

    [JsonPropertyName("strategy")]
    public string? Strategy { get; set; }

    [JsonPropertyName("strategy_apply_mode")]
    public string StrategyApplyMode { get; set; } = "next_boundary";

    [JsonPropertyName("strategy_updated_at")]
    public string? StrategyUpdatedAt { get; set; }

    [JsonPropertyName("gate_decision")]
    public GateDecisionStatus? GateDecision { get; set; }

    [JsonPropertyName("startup_gate_waivers")]
    public Dictionary<string, StartupGateWaiverStatus> StartupGateWaivers { get; set; } = [];

    [JsonPropertyName("startup_gate_context")]
    public StartupGateContext? StartupGateContext { get; set; }

    [JsonPropertyName("exclusive_validation")]
    public ExclusiveValidationLedgerStatus? ExclusiveValidation { get; set; }

    [JsonPropertyName("updated_at")]
    public string? UpdatedAt { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

public sealed class ExclusiveValidationLedgerStatus
{
    [JsonPropertyName("current_request_id")]
    public string? CurrentRequestId { get; set; }

    [JsonPropertyName("receipts")]
    public Dictionary<string, ExclusiveValidationReceiptStatus> Receipts { get; set; } = [];
}

public sealed class ExclusiveValidationReceiptStatus
{
    [JsonPropertyName("request_id")]
    public string RequestId { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "unknown";

    [JsonPropertyName("outcome")]
    public string? Outcome { get; set; }

    [JsonPropertyName("reason")]
    public string? Reason { get; set; }

    [JsonPropertyName("launch_policy")]
    public ExclusiveValidationLaunchPolicyStatus? LaunchPolicy { get; set; }

    [JsonPropertyName("launch")]
    public ExclusiveValidationLaunchStatus? Launch { get; set; }
}

public sealed class ExclusiveValidationLaunchPolicyStatus
{
    [JsonPropertyName("kind")]
    public string Kind { get; set; } = "";

    [JsonPropertyName("prompt_title")]
    public string PromptTitle { get; set; } = "";

    [JsonPropertyName("prompt_message")]
    public string PromptMessage { get; set; } = "";

    [JsonPropertyName("reminder")]
    public string Reminder { get; set; } = "";
}

public sealed class ExclusiveValidationLaunchStatus
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = "unknown";

    [JsonPropertyName("reason")]
    public string? Reason { get; set; }
}

public sealed class GateDecisionStatus
{
    [JsonPropertyName("request_id")]
    public string RequestId { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "unknown";

    [JsonPropertyName("strategy")]
    public string Strategy { get; set; } = "";

    [JsonPropertyName("phase")]
    public string Phase { get; set; } = "";

    [JsonPropertyName("check_id")]
    public string CheckId { get; set; } = "";

    [JsonPropertyName("reason")]
    public string Reason { get; set; } = "";

    [JsonPropertyName("blocking")]
    public bool Blocking { get; set; } = true;

    [JsonPropertyName("expected")]
    public string? Expected { get; set; }

    [JsonPropertyName("decision_id")]
    public string? DecisionId { get; set; }

    [JsonPropertyName("options")]
    public List<GateDecisionOption> Options { get; set; } = [];
}

public sealed class GateDecisionOption
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("description")]
    public string? Description { get; set; }

    [JsonPropertyName("action")]
    public string Action { get; set; } = "";

    [JsonPropertyName("kind")]
    public string Kind { get; set; } = "";

    [JsonPropertyName("value")]
    public string? Value { get; set; }
}

public sealed class StartupGateContext
{
    [JsonPropertyName("strategy")]
    public string Strategy { get; set; } = "none";

    [JsonPropertyName("checks")]
    public List<StartupGateCheck> Checks { get; set; } = [];
}

public sealed class StartupGateCheck
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("expected")]
    public string? Expected { get; set; }
}

public sealed class StartupGateWaiverStatus
{
    [JsonPropertyName("request_id")]
    public string RequestId { get; set; } = "";

    [JsonPropertyName("check_id")]
    public string CheckId { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("strategy")]
    public string Strategy { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "pending";
}

public sealed class ObservationStatus
{
    [JsonPropertyName("state_label")]
    public string StateLabel { get; set; } = "UNKNOWN";

    [JsonPropertyName("wave")]
    public int? Wave { get; set; }

    [JsonPropertyName("coins_per_minute")]
    public string? CoinsPerMinute { get; set; }

    [JsonPropertyName("game_speed")]
    public double? GameSpeed { get; set; }

    [JsonPropertyName("menu")]
    public string? Menu { get; set; }

    [JsonPropertyName("secondary")]
    public List<string> Secondary { get; set; } = [];

    [JsonPropertyName("overlays")]
    public List<string> Overlays { get; set; } = [];

    [JsonPropertyName("observed_at")]
    public string? ObservedAt { get; set; }

    [JsonPropertyName("age_seconds")]
    public int? AgeSeconds { get; set; }

    [JsonPropertyName("stale")]
    public bool Stale { get; set; }
}

public sealed class RuntimeStatus
{
    [JsonPropertyName("active")]
    public bool Active { get; set; }

    [JsonPropertyName("instances")]
    public List<RuntimeInstance> Instances { get; set; } = [];
}

public sealed class RuntimeInstance
{
    [JsonPropertyName("file")]
    public string? File { get; set; }

    [JsonPropertyName("active")]
    public bool Active { get; set; }

    [JsonPropertyName("lock_held")]
    public bool? LockHeld { get; set; }

    [JsonPropertyName("pid_alive")]
    public bool? PidAlive { get; set; }

    [JsonPropertyName("pid")]
    public int? Pid { get; set; }

    [JsonPropertyName("target")]
    public string? Target { get; set; }

    [JsonPropertyName("started_at")]
    public string? StartedAt { get; set; }
}

public sealed class ProcessServiceStatus
{
    [JsonPropertyName("service")]
    public string? Service { get; set; }

    [JsonPropertyName("available")]
    public bool Available { get; set; }

    [JsonPropertyName("active")]
    public bool Active { get; set; }

    [JsonPropertyName("load_state")]
    public string? LoadState { get; set; }

    [JsonPropertyName("active_state")]
    public string? ActiveState { get; set; }

    [JsonPropertyName("sub_state")]
    public string? SubState { get; set; }

    [JsonPropertyName("unit_file_state")]
    public string? UnitFileState { get; set; }

    [JsonPropertyName("main_pid")]
    public int? MainPid { get; set; }

    [JsonPropertyName("exit_status")]
    public int? ExitStatus { get; set; }

    [JsonPropertyName("adb_port")]
    public int? AdbPort { get; set; }

    [JsonPropertyName("adb_target")]
    public string? AdbTarget { get; set; }

    [JsonPropertyName("adb_port_source")]
    public string? AdbPortSource { get; set; }

    [JsonPropertyName("adb_environment_file")]
    public string? AdbEnvironmentFile { get; set; }

    [JsonPropertyName("service_environment_files")]
    public string? ServiceEnvironmentFiles { get; set; }

    [JsonPropertyName("automation_environment_file_loaded")]
    public bool? AutomationEnvironmentFileLoaded { get; set; }

    [JsonPropertyName("adb_port_error")]
    public string? AdbPortError { get; set; }

    [JsonPropertyName("strategy")]
    public string? Strategy { get; set; }

    [JsonPropertyName("strategy_source")]
    public string? StrategySource { get; set; }

    [JsonPropertyName("strategy_environment_file")]
    public string? StrategyEnvironmentFile { get; set; }

    [JsonPropertyName("strategy_error")]
    public string? StrategyError { get; set; }

    [JsonPropertyName("strategy_options")]
    public List<string> StrategyOptions { get; set; } = [];

    [JsonPropertyName("startup_gate_policy")]
    public string? StartupGatePolicy { get; set; }

    [JsonPropertyName("startup_gate_policy_source")]
    public string? StartupGatePolicySource { get; set; }

    [JsonPropertyName("startup_gate_policy_options")]
    public List<string> StartupGatePolicyOptions { get; set; } = [];

    [JsonPropertyName("startup_gate_policy_error")]
    public string? StartupGatePolicyError { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

public sealed class StrategyProfileCatalogResponse
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("policy_modes")]
    public List<string> PolicyModes { get; set; } = [];

    [JsonPropertyName("presets")]
    public Dictionary<string, List<StrategyPresetOption>> Presets { get; set; } = [];

    [JsonPropertyName("setup_checks")]
    public List<StrategyPresetOption> SetupChecks { get; set; } = [];

    [JsonPropertyName("perks")]
    public List<StrategyPresetOption> Perks { get; set; } = [];

    [JsonPropertyName("items")]
    public List<StrategyProfileItem> Items { get; set; } = [];

    [JsonPropertyName("errors")]
    public List<StrategyProfileCatalogError> Errors { get; set; } = [];
}

public sealed class StrategyPresetOption
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";
}

public sealed class StrategyProfileCatalogError
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("error")]
    public string Error { get; set; } = "";
}

public sealed class StrategyProfileItem
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("family")]
    public string Family { get; set; } = "";

    [JsonPropertyName("tier")]
    public int? Tier { get; set; }

    [JsonPropertyName("version")]
    public int Version { get; set; }

    [JsonPropertyName("built_in")]
    public bool BuiltIn { get; set; }

    [JsonPropertyName("editable")]
    public bool Editable { get; set; }

    [JsonPropertyName("published_at")]
    public string? PublishedAt { get; set; }

    [JsonPropertyName("source_fingerprint")]
    public string? SourceFingerprint { get; set; }

    [JsonPropertyName("plan_fingerprint")]
    public string? PlanFingerprint { get; set; }

    [JsonPropertyName("loadout")]
    public StrategyProfileLoadout? Loadout { get; set; }

    [JsonPropertyName("setup")]
    public StrategyProfileSetup? Setup { get; set; }

    [JsonIgnore]
    public string OriginLabel => BuiltIn ? "Bundled • read-only" : "Custom • editable";
}

public sealed class StrategyProfileSetup
{
    [JsonPropertyName("skipped_checks")]
    public List<string> SkippedChecks { get; set; } = [];

    [JsonPropertyName("settings")]
    public Dictionary<string, JsonElement> Settings { get; set; } = [];
}

public sealed class StrategyProfileLoadout
{
    [JsonPropertyName("modules")]
    public StrategyProfilePolicy Modules { get; set; } = new();

    [JsonPropertyName("damage_slider")]
    public StrategyProfilePolicy DamageSlider { get; set; } = new();

    [JsonPropertyName("orb_distance")]
    public StrategyProfilePolicy OrbDistance { get; set; } = new();

    [JsonPropertyName("target_priority")]
    public StrategyProfilePolicy TargetPriority { get; set; } = new();
}

public sealed class StrategyProfilePolicy
{
    [JsonPropertyName("mode")]
    public string Mode { get; set; } = "preserve";

    [JsonPropertyName("preset")]
    public string? Preset { get; set; }

    [JsonPropertyName("value")]
    public string? Value { get; set; }
}

public sealed class StrategyProfileMutationResponse
{
    [JsonPropertyName("action")]
    public string Action { get; set; } = "";

    [JsonPropertyName("valid")]
    public bool Valid { get; set; }

    [JsonPropertyName("published")]
    public bool Published { get; set; }

    [JsonPropertyName("profile")]
    public StrategyProfileItem Profile { get; set; } = new();

    [JsonPropertyName("rule_count")]
    public int RuleCount { get; set; }

    [JsonPropertyName("summary")]
    public List<string> Summary { get; set; } = [];

    [JsonPropertyName("catalog")]
    public StrategyProfileCatalogResponse? Catalog { get; set; }

    [JsonPropertyName("warning")]
    public string? Warning { get; set; }
}

public sealed class StrategyAuthoringCatalogResponse
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("setting_registry")]
    public List<StrategySettingDefinition> SettingRegistry { get; set; } = [];

    [JsonPropertyName("capabilities")]
    public StrategyAuthoringCapabilities Capabilities { get; set; } = new();

    [JsonPropertyName("editor_options")]
    public StrategyAuthoringEditorOptions EditorOptions { get; set; } = new();

    [JsonPropertyName("bases")]
    public StrategyBaseCatalog Bases { get; set; } = new();

    [JsonPropertyName("strategies")]
    public StrategyAuthoringStrategyCatalog Strategies { get; set; } = new();

    [JsonPropertyName("module_presets")]
    public ModulePresetCatalog ModulePresets { get; set; } = new();

    [JsonPropertyName("captured_drafts")]
    public CapturedStrategyDraftCatalog CapturedDrafts { get; set; } = new();

    [JsonPropertyName("latest_compatible_base_revisions")]
    public List<CompatibleBaseRevision> LatestCompatibleBaseRevisions { get; set; } = [];

    [JsonPropertyName("catalog_errors")]
    public List<StrategyAuthoringCatalogError> CatalogErrors { get; set; } = [];
}

public sealed class CapturedStrategyDraftCatalog
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("items")]
    public List<CapturedStrategyDraftSummary> Items { get; set; } = [];

    [JsonPropertyName("errors")]
    public List<StrategyProfileCatalogError> Errors { get; set; } = [];
}

public sealed class CapturedStrategyDraftSummary
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("tier")]
    public int Tier { get; set; }

    [JsonPropertyName("unresolved_count")]
    public int UnresolvedCount { get; set; }

    [JsonPropertyName("saved_at")]
    public string? SavedAt { get; set; }

    [JsonPropertyName("acquisition_source")]
    public string AcquisitionSource { get; set; } = "";

    [JsonIgnore]
    public string DetailLabel =>
        $"Tier {Tier} • {UnresolvedCount} unresolved • "
        + (AcquisitionSource == "retained_return_control_refresh"
            ? "retained Return Control save"
            : "new capture refresh");
}

public sealed class CapturedStrategyDraftResponse
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("server_revision")]
    public int ServerRevision { get; set; }

    [JsonPropertyName("capability")]
    public string Capability { get; set; } = "";

    [JsonPropertyName("draft")]
    public CapturedStrategyDraftDocument Draft { get; set; } = new();
}

public sealed class CapturedStrategyDraftDocument
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("source")]
    public StrategyAuthoringSource Source { get; set; } = new();

    [JsonPropertyName("saved_at")]
    public string? SavedAt { get; set; }

    [JsonPropertyName("capture")]
    public SetupCapturePreview Capture { get; set; } = new();

    [JsonPropertyName("review")]
    public CapturedStrategyStoredReview Review { get; set; } = new();
}

public sealed class CapturedStrategyStoredReview
{
    [JsonPropertyName("captured_vs_base")]
    public JsonElement CapturedVsBase { get; set; }

    [JsonPropertyName("unresolved")]
    public List<SetupCaptureUnresolved> Unresolved { get; set; } = [];

    [JsonPropertyName("review_fingerprint")]
    public string ReviewFingerprint { get; set; } = "";
}

public sealed class StrategyAuthoringCapabilities
{
    [JsonPropertyName("operations")]
    public List<string> Operations { get; set; } = [];

    [JsonPropertyName("base_source_states")]
    public List<AuthoringSourceStateDefinition> BaseSourceStates { get; set; } = [];

    [JsonPropertyName("strategy_source_states")]
    public List<AuthoringSourceStateDefinition> StrategySourceStates { get; set; } = [];

    [JsonPropertyName("publication_activates_strategy")]
    public bool PublicationActivatesStrategy { get; set; }

    [JsonPropertyName("expanded_plan_exposed")]
    public bool ExpandedPlanExposed { get; set; }

    [JsonPropertyName("unknown_values_round_trip")]
    public bool UnknownValuesRoundTrip { get; set; }

    [JsonPropertyName("reviewed_rebase_required")]
    public bool ReviewedRebaseRequired { get; set; }

    [JsonPropertyName("profile_local_loadout_editors")]
    public bool ProfileLocalLoadoutEditors { get; set; }

    [JsonPropertyName("preset_local_copy")]
    public bool PresetLocalCopy { get; set; }

    [JsonPropertyName("managed_custom_module_presets")]
    public bool ManagedCustomModulePresets { get; set; }
}

public sealed class ModulePresetCatalog
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("items")]
    public List<ModulePresetDetail> Items { get; set; } = [];

    [JsonPropertyName("errors")]
    public List<ModulePresetCatalogError> Errors { get; set; } = [];
}

public sealed class ModulePresetDetail
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("origin")]
    public string Origin { get; set; } = "";

    [JsonPropertyName("editable")]
    public bool Editable { get; set; }

    [JsonPropertyName("can_create_variant")]
    public bool CanCreateVariant { get; set; }

    [JsonPropertyName("definition")]
    public Dictionary<string, string> Definition { get; set; } = [];

    [JsonPropertyName("slots")]
    public List<ModulePresetSlot> Slots { get; set; } = [];

    [JsonIgnore]
    public string LifecycleLabel => Origin switch
    {
        "bundled" => "Bundled preset • read-only",
        "custom" => "Custom preset • immutable; duplicate or edit a local copy",
        _ => "Preset origin unavailable • read-only",
    };
}

public sealed class ModulePresetSlot
{
    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("family")]
    public string Family { get; set; } = "";

    [JsonPropertyName("role")]
    public string Role { get; set; } = "";

    [JsonPropertyName("module")]
    public string Module { get; set; } = "";
}

public sealed class ModulePresetCatalogError
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("code")]
    public string Code { get; set; } = "";

    [JsonPropertyName("error")]
    public string Error { get; set; } = "";
}

public sealed class ModulePresetCreationRequest
{
    [JsonPropertyName("operation")]
    public string Operation { get; set; } = "create_module_preset";

    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("source")]
    public ModulePresetCreationSource Source { get; set; } = new();
}

public sealed class ModulePresetCreationSource
{
    [JsonPropertyName("preset")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Preset { get; set; }

    [JsonPropertyName("local")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement? Local { get; set; }
}

public sealed class LoadoutPresetMaterializationRequest
{
    [JsonPropertyName("operation")]
    public string Operation { get; set; } = "materialize_loadout_preset";

    [JsonPropertyName("setting_id")]
    public string SettingId { get; set; } = "";

    [JsonPropertyName("preset")]
    public string Preset { get; set; } = "";

    [JsonPropertyName("expected_catalog_fingerprint")]
    public string ExpectedCatalogFingerprint { get; set; } = "";
}

public sealed class LoadoutPresetMaterialization
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("setting_id")]
    public string SettingId { get; set; } = "";

    [JsonPropertyName("preset")]
    public string Preset { get; set; } = "";

    [JsonPropertyName("catalog_fingerprint")]
    public string CatalogFingerprint { get; set; } = "";

    [JsonPropertyName("definition")]
    public JsonElement Definition { get; set; }

    [JsonPropertyName("definition_fingerprint")]
    public string DefinitionFingerprint { get; set; } = "";
}

public sealed class AuthoringSourceStateDefinition
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("policy")]
    public string? Policy { get; set; }
}

public sealed class StrategyAuthoringEditorOptions
{
    [JsonPropertyName("presets")]
    public Dictionary<string, List<StrategyPresetOption>> Presets { get; set; } = [];

    [JsonPropertyName("perks")]
    public List<StrategyPresetOption> Perks { get; set; } = [];
}

public sealed class StrategySettingDefinition
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("section")]
    public string Section { get; set; } = "";

    [JsonPropertyName("editor_type")]
    public string EditorType { get; set; } = "";

    [JsonPropertyName("allowed_policies")]
    public List<string> AllowedPolicies { get; set; } = [];

    [JsonPropertyName("dependencies")]
    public List<string> Dependencies { get; set; } = [];

    [JsonPropertyName("dependency_display_names")]
    public List<string> DependencyDisplayNames { get; set; } = [];

    [JsonPropertyName("runtime_destination")]
    public string RuntimeDestination { get; set; } = "";

    [JsonPropertyName("observation_supported")]
    public bool ObservationSupported { get; set; }

    [JsonPropertyName("repair_supported")]
    public bool RepairSupported { get; set; }

    [JsonPropertyName("initial_value")]
    public JsonElement? InitialValue { get; set; }

    [JsonPropertyName("editor")]
    public StrategyEditorMetadata Editor { get; set; } = new();
}

public sealed class StrategyEditorMetadata
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("value_kind")]
    public string ValueKind { get; set; } = "";

    [JsonPropertyName("fixed")]
    public bool Fixed { get; set; }

    [JsonPropertyName("help_text")]
    public string HelpText { get; set; } = "";

    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("initial_value")]
    public JsonElement? InitialValue { get; set; }

    [JsonPropertyName("server_normalized_text")]
    public bool ServerNormalizedText { get; set; }

    [JsonPropertyName("preserve_unknown_fields")]
    public bool PreserveUnknownFields { get; set; }

    [JsonPropertyName("unique_field_values")]
    public bool UniqueFieldValues { get; set; }

    [JsonPropertyName("allow_group_selection")]
    public bool AllowGroupSelection { get; set; }

    [JsonPropertyName("minimum_selected_groups")]
    public int MinimumSelectedGroups { get; set; }

    [JsonPropertyName("options")]
    public List<StrategyEditorOption> Options { get; set; } = [];

    [JsonPropertyName("fields")]
    public List<StrategyEditorField> Fields { get; set; } = [];

    [JsonPropertyName("list_constraints")]
    public StrategyListConstraints? ListConstraints { get; set; }

    [JsonPropertyName("groups")]
    public List<StrategyEditorGroup> Groups { get; set; } = [];

    [JsonPropertyName("local_editor")]
    public StrategyEditorMetadata? LocalEditor { get; set; }

    [JsonPropertyName("preset_catalog")]
    public string? PresetCatalog { get; set; }

    [JsonPropertyName("preset_catalog_fingerprint")]
    public string PresetCatalogFingerprint { get; set; } = "";
}

public sealed class StrategyEditorOption
{
    [JsonPropertyName("value")]
    public JsonElement Value { get; set; }

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonIgnore]
    public string ValueKey => Value.GetRawText();
}

public sealed class StrategyEditorField
{
    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("required")]
    public bool Required { get; set; }

    [JsonPropertyName("fixed")]
    public bool Fixed { get; set; }

    [JsonPropertyName("initial_value")]
    public JsonElement InitialValue { get; set; }

    [JsonPropertyName("options")]
    public List<StrategyEditorOption> Options { get; set; } = [];
}

public sealed class StrategyListConstraints
{
    [JsonPropertyName("minimum_items")]
    public int MinimumItems { get; set; }

    [JsonPropertyName("maximum_items")]
    public int MaximumItems { get; set; }

    [JsonPropertyName("unique_items")]
    public bool UniqueItems { get; set; }

    [JsonPropertyName("allow_add")]
    public bool AllowAdd { get; set; }

    [JsonPropertyName("allow_remove")]
    public bool AllowRemove { get; set; }

    [JsonPropertyName("allow_reorder")]
    public bool AllowReorder { get; set; }

    [JsonPropertyName("order_significant")]
    public bool OrderSignificant { get; set; }

    [JsonPropertyName("exact_items")]
    public List<string> ExactItems { get; set; } = [];
}

public sealed class StrategyEditorGroup
{
    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("initially_included")]
    public bool InitiallyIncluded { get; set; }

    [JsonPropertyName("allow_selection")]
    public bool AllowSelection { get; set; }

    [JsonPropertyName("minimum_selected_fields")]
    public int MinimumSelectedFields { get; set; }

    [JsonPropertyName("preserve_unknown_fields")]
    public bool PreserveUnknownFields { get; set; }

    [JsonPropertyName("fields")]
    public List<StrategyEditorField> Fields { get; set; } = [];
}

public sealed class StrategyBaseCatalog
{
    [JsonPropertyName("items")]
    public List<StrategyBaseItem> Items { get; set; } = [];

    [JsonPropertyName("errors")]
    public List<StrategyProfileCatalogError> Errors { get; set; } = [];
}

public sealed class StrategyBaseItem
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("family")]
    public string Family { get; set; } = "";

    [JsonPropertyName("built_in")]
    public bool BuiltIn { get; set; }

    [JsonPropertyName("editable")]
    public bool Editable { get; set; }

    [JsonPropertyName("latest_revision")]
    public int LatestRevision { get; set; }

    [JsonPropertyName("published_at")]
    public string? PublishedAt { get; set; }

    [JsonPropertyName("source_fingerprint")]
    public string? SourceFingerprint { get; set; }

    [JsonPropertyName("source")]
    public StrategyAuthoringSource Source { get; set; } = new();

    [JsonPropertyName("resolution")]
    public StrategyAuthoringResolution Resolution { get; set; } = new();

    [JsonPropertyName("revisions")]
    public List<StrategyBaseRevisionSummary> Revisions { get; set; } = [];

    [JsonIgnore]
    public string RevisionLabel => $"Revision {LatestRevision} • immutable";
}

public sealed class StrategyBaseRevisionSummary
{
    [JsonPropertyName("revision")]
    public int Revision { get; set; }

    [JsonPropertyName("published_at")]
    public string? PublishedAt { get; set; }

    [JsonPropertyName("source_fingerprint")]
    public string? SourceFingerprint { get; set; }

    [JsonPropertyName("setting_count")]
    public int SettingCount { get; set; }
}

public sealed class StrategyAuthoringStrategyCatalog
{
    [JsonPropertyName("items")]
    public List<StrategyAuthoringStrategyItem> Items { get; set; } = [];

    [JsonPropertyName("errors")]
    public List<StrategyProfileCatalogError> Errors { get; set; } = [];
}

public sealed class StrategyAuthoringStrategyItem
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("family")]
    public string Family { get; set; } = "";

    [JsonPropertyName("tier")]
    public int? Tier { get; set; }

    [JsonPropertyName("version")]
    public int Version { get; set; }

    [JsonPropertyName("built_in")]
    public bool BuiltIn { get; set; }

    [JsonPropertyName("editable")]
    public bool Editable { get; set; }

    [JsonPropertyName("authoring_supported")]
    public bool AuthoringSupported { get; set; }

    [JsonPropertyName("published_at")]
    public string? PublishedAt { get; set; }

    [JsonPropertyName("source_fingerprint")]
    public string? SourceFingerprint { get; set; }

    [JsonPropertyName("normalized_source_fingerprint")]
    public string? NormalizedSourceFingerprint { get; set; }

    [JsonPropertyName("legacy_converted")]
    public bool LegacyConverted { get; set; }

    [JsonPropertyName("source")]
    public StrategyAuthoringSource? Source { get; set; }

    [JsonPropertyName("resolution")]
    public StrategyAuthoringResolution? Resolution { get; set; }

    [JsonPropertyName("compatible_base_revisions")]
    public List<CompatibleBaseRevision> CompatibleBaseRevisions { get; set; } = [];

    [JsonPropertyName("base_update")]
    public StrategyBaseUpdate? BaseUpdate { get; set; }

    [JsonPropertyName("read_only_reason")]
    public string? ReadOnlyReason { get; set; }

    [JsonIgnore]
    public string VersionLabel => BuiltIn
        ? $"Version {Version} • bundled read-only"
        : $"Version {Version} • custom";
}

public sealed class CompatibleBaseRevision
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("family")]
    public string? Family { get; set; }

    [JsonPropertyName("revision")]
    public int Revision { get; set; }

    [JsonPropertyName("source_fingerprint")]
    public string? SourceFingerprint { get; set; }
}

public sealed class StrategyBaseUpdate
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("pinned_revision")]
    public int PinnedRevision { get; set; }

    [JsonPropertyName("latest_revision")]
    public int LatestRevision { get; set; }

    [JsonPropertyName("source_fingerprint")]
    public string? SourceFingerprint { get; set; }
}

public sealed class StrategyAuthoringSource
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 3;

    [JsonPropertyName("kind")]
    public string Kind { get; set; } = "strategy";

    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("family")]
    public string Family { get; set; } = "farm";

    [JsonPropertyName("tier")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? Tier { get; set; }

    [JsonPropertyName("version")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? Version { get; set; }

    [JsonPropertyName("revision")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? Revision { get; set; }

    [JsonPropertyName("base")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public StrategyBaseReference? Base { get; set; }

    [JsonPropertyName("settings")]
    public Dictionary<string, StrategyAuthoringDirective> Settings { get; set; } = [];
}

public sealed class StrategyBaseReference
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("revision")]
    public int Revision { get; set; }
}

public sealed class StrategyAuthoringDirective
{
    [JsonPropertyName("policy")]
    public string Policy { get; set; } = "";

    [JsonPropertyName("value")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement? Value { get; set; }
}

public sealed class StrategyAuthoringResolution
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("family")]
    public string Family { get; set; } = "";

    [JsonPropertyName("settings")]
    public Dictionary<string, StrategyResolvedSetting> Settings { get; set; } = [];
}

public sealed class StrategyResolvedSetting
{
    [JsonPropertyName("state")]
    public string State { get; set; } = "unmanaged";

    [JsonPropertyName("policy")]
    public string? Policy { get; set; }

    [JsonPropertyName("value")]
    public JsonElement? Value { get; set; }

    [JsonPropertyName("provenance")]
    public StrategySettingProvenance Provenance { get; set; } = new();

    [JsonPropertyName("overridden_base")]
    public StrategyAuthoringDirective? OverriddenBase { get; set; }

    [JsonPropertyName("masked_base")]
    public StrategyAuthoringDirective? MaskedBase { get; set; }
}

public sealed class StrategySettingProvenance
{
    [JsonPropertyName("kind")]
    public string Kind { get; set; } = "unmanaged";

    [JsonPropertyName("base_id")]
    public string? BaseId { get; set; }

    [JsonPropertyName("revision")]
    public int? Revision { get; set; }
}

public sealed class StrategyAuthoringMutationResponse
{
    [JsonPropertyName("operation")]
    public string Operation { get; set; } = "";

    [JsonPropertyName("valid")]
    public bool Valid { get; set; }

    [JsonPropertyName("published")]
    public bool Published { get; set; }

    [JsonPropertyName("source")]
    public StrategyAuthoringSource Source { get; set; } = new();

    [JsonPropertyName("source_fingerprint")]
    public string? SourceFingerprint { get; set; }

    [JsonPropertyName("expected_latest_fingerprint")]
    public string? ExpectedLatestFingerprint { get; set; }

    [JsonPropertyName("profile")]
    public StrategyProfileItem? Profile { get; set; }

    [JsonPropertyName("resolution")]
    public StrategyAuthoringResolution? Resolution { get; set; }

    [JsonPropertyName("rule_count")]
    public int RuleCount { get; set; }

    [JsonPropertyName("summary")]
    public List<string> Summary { get; set; } = [];

    [JsonPropertyName("fingerprints")]
    public Dictionary<string, string> Fingerprints { get; set; } = [];

    [JsonPropertyName("review")]
    public StrategyAuthoringReview Review { get; set; } = new();

    [JsonPropertyName("rebase")]
    public StrategyRebasePreview? Rebase { get; set; }

    [JsonPropertyName("reviewed_rebase_fingerprint")]
    public string? ReviewedRebaseFingerprint { get; set; }

    [JsonPropertyName("catalog")]
    public StrategyAuthoringCatalogResponse? Catalog { get; set; }

    [JsonPropertyName("preset")]
    public ModulePresetDetail? Preset { get; set; }

    [JsonPropertyName("materialization")]
    public LoadoutPresetMaterialization? Materialization { get; set; }

    [JsonPropertyName("retired")]
    public bool Retired { get; set; }

    [JsonPropertyName("retirement")]
    public StrategyRetirement? Retirement { get; set; }

    [JsonPropertyName("warning")]
    public string? Warning { get; set; }

    [JsonPropertyName("strategy_id")]
    public string? StrategyId { get; set; }

    [JsonPropertyName("historical_logical_version")]
    public int HistoricalLogicalVersion { get; set; }

    [JsonPropertyName("historical_revision_fingerprint")]
    public string? HistoricalRevisionFingerprint { get; set; }

    [JsonPropertyName("current_latest_source_fingerprint")]
    public string? CurrentLatestSourceFingerprint { get; set; }

    [JsonPropertyName("next_logical_version")]
    public int NextLogicalVersion { get; set; }

    [JsonPropertyName("candidate")]
    public StrategyRestoreCandidate? Candidate { get; set; }

    [JsonPropertyName("comparison")]
    public StrategyRevisionComparison? Comparison { get; set; }

    [JsonPropertyName("reviewed_restore_fingerprint")]
    public string? ReviewedRestoreFingerprint { get; set; }

    [JsonPropertyName("restore_publishes_new_revision")]
    public bool RestorePublishesNewRevision { get; set; }

    [JsonPropertyName("publication_activates_strategy")]
    public bool PublicationActivatesStrategy { get; set; }

    [JsonPropertyName("restored")]
    public bool Restored { get; set; }

    [JsonPropertyName("restored_from")]
    public StrategyRestoredFrom? RestoredFrom { get; set; }

    [JsonPropertyName("history")]
    public StrategyHistoryCatalogResponse? History { get; set; }
}

public sealed class StrategyRetirement
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("version")]
    public int Version { get; set; }

    [JsonPropertyName("source_fingerprint")]
    public string SourceFingerprint { get; set; } = "";

    [JsonPropertyName("retired_at")]
    public string RetiredAt { get; set; } = "";

    [JsonPropertyName("archive_name")]
    public string ArchiveName { get; set; } = "";

    [JsonPropertyName("recoverable")]
    public bool Recoverable { get; set; }
}

public sealed class StrategyHistoryCatalogResponse
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("lineages")]
    public List<StrategyHistoryLineage> Lineages { get; set; } = [];

    [JsonPropertyName("errors")]
    public List<StrategyProfileCatalogError> Errors { get; set; } = [];

    [JsonPropertyName("newest_first")]
    public bool NewestFirst { get; set; }

    [JsonPropertyName("expanded_plan_exposed")]
    public bool ExpandedPlanExposed { get; set; }
}

public sealed class StrategyHistoryLineage
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("family")]
    public string Family { get; set; } = "";

    [JsonPropertyName("tier")]
    public int? Tier { get; set; }

    [JsonPropertyName("active_latest")]
    public bool ActiveLatest { get; set; }

    [JsonPropertyName("retired")]
    public bool Retired { get; set; }

    [JsonPropertyName("latest_version")]
    public int LatestVersion { get; set; }

    [JsonPropertyName("current_latest_version")]
    public int? CurrentLatestVersion { get; set; }

    [JsonPropertyName("latest_source_fingerprint")]
    public string? LatestSourceFingerprint { get; set; }

    [JsonPropertyName("latest_publication_fingerprint")]
    public string? LatestPublicationFingerprint { get; set; }

    [JsonPropertyName("lineage_fingerprint")]
    public string LineageFingerprint { get; set; } = "";

    [JsonPropertyName("revisions")]
    public List<StrategyRevisionSummary> Revisions { get; set; } = [];

    [JsonPropertyName("warnings")]
    public List<string> Warnings { get; set; } = [];

    [JsonIgnore]
    public string StatusLabel => Retired
        ? $"Retired lineage • {Revisions.Count} revision(s)"
        : $"Current version {CurrentLatestVersion} • {Revisions.Count} revision(s)";
}

public sealed class StrategyRevisionSummary
{
    [JsonPropertyName("strategy_id")]
    public string StrategyId { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("logical_version")]
    public int LogicalVersion { get; set; }

    [JsonPropertyName("published_at")]
    public string PublishedAt { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "historical";

    [JsonPropertyName("active_latest")]
    public bool ActiveLatest { get; set; }

    [JsonPropertyName("retired_lineage")]
    public bool RetiredLineage { get; set; }

    [JsonPropertyName("source_fingerprint")]
    public string SourceFingerprint { get; set; } = "";

    [JsonPropertyName("normalized_source_fingerprint")]
    public string NormalizedSourceFingerprint { get; set; } = "";

    [JsonPropertyName("base_fingerprint")]
    public string BaseFingerprint { get; set; } = "";

    [JsonPropertyName("resolution_fingerprint")]
    public string ResolutionFingerprint { get; set; } = "";

    [JsonPropertyName("plan_fingerprint")]
    public string PlanFingerprint { get; set; } = "";

    [JsonPropertyName("publication_fingerprint")]
    public string PublicationFingerprint { get; set; } = "";

    [JsonPropertyName("revision_fingerprint")]
    public string RevisionFingerprint { get; set; } = "";

    [JsonPropertyName("pinned_base_id")]
    public string? PinnedBaseId { get; set; }

    [JsonPropertyName("pinned_base_revision")]
    public int? PinnedBaseRevision { get; set; }

    [JsonPropertyName("tier")]
    public int Tier { get; set; }

    [JsonPropertyName("family")]
    public string Family { get; set; } = "";

    [JsonPropertyName("publication_origin")]
    public string PublicationOrigin { get; set; } = "";

    [JsonPropertyName("audit_identity")]
    public StrategyAuditIdentity AuditIdentity { get; set; } = new();

    [JsonPropertyName("publication_schema_version")]
    public int PublicationSchemaVersion { get; set; }

    [JsonPropertyName("rule_count")]
    public int RuleCount { get; set; }

    [JsonPropertyName("current_validation_valid")]
    public bool CurrentValidationValid { get; set; }

    [JsonPropertyName("validation_errors")]
    public List<AuthoringValidationError> ValidationErrors { get; set; } = [];

    [JsonPropertyName("warnings")]
    public List<string> Warnings { get; set; } = [];

    [JsonIgnore]
    public string VersionLabel => $"Version {LogicalVersion} • {Status.Replace('_', ' ')}";

    [JsonIgnore]
    public string BaseLabel => string.IsNullOrWhiteSpace(PinnedBaseId)
        ? "No Base"
        : $"{PinnedBaseId}@{PinnedBaseRevision}";

    [JsonIgnore]
    public string ValidationLabel => CurrentValidationValid
        ? "Valid under current resolver/builder"
        : "Current validation failed";
}

public sealed class StrategyAuditIdentity
{
    [JsonPropertyName("authority")]
    public string Authority { get; set; } = "";

    [JsonPropertyName("event_id")]
    public string EventId { get; set; } = "";
}

public sealed class StrategyRevisionDetailResponse
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("revision")]
    public StrategyRevisionSummary Revision { get; set; } = new();

    [JsonPropertyName("source")]
    public StrategyAuthoringSource Source { get; set; } = new();

    [JsonPropertyName("base_snapshot")]
    public StrategyAuthoringSource? BaseSnapshot { get; set; }

    [JsonPropertyName("resolution")]
    public StrategyAuthoringResolution Resolution { get; set; } = new();

    [JsonPropertyName("expanded_plan_exposed")]
    public bool ExpandedPlanExposed { get; set; }
}

public sealed class StrategyRestoreCandidate
{
    [JsonPropertyName("source_fingerprint")]
    public string SourceFingerprint { get; set; } = "";

    [JsonPropertyName("base_fingerprint")]
    public string BaseFingerprint { get; set; } = "";

    [JsonPropertyName("resolution_fingerprint")]
    public string ResolutionFingerprint { get; set; } = "";

    [JsonPropertyName("plan_fingerprint")]
    public string PlanFingerprint { get; set; } = "";

    [JsonPropertyName("rule_count")]
    public int RuleCount { get; set; }
}

public sealed class StrategyRestoredFrom
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("logical_version")]
    public int LogicalVersion { get; set; }

    [JsonPropertyName("revision_fingerprint")]
    public string RevisionFingerprint { get; set; } = "";
}

public sealed class StrategyRevisionComparison
{
    [JsonPropertyName("source_changes")]
    public AuthoringSourceDiff SourceChanges { get; set; } = new();

    [JsonPropertyName("effective_changes")]
    public AuthoringResolutionDiff EffectiveChanges { get; set; } = new();

    [JsonPropertyName("base_snapshot_changes")]
    public StrategyBaseSnapshotDiff BaseSnapshotChanges { get; set; } = new();

    [JsonPropertyName("local_override_changes")]
    public StrategyDirectiveDiff LocalOverrideChanges { get; set; } = new();

    [JsonPropertyName("explicit_ignore_changes")]
    public StrategyDirectiveDiff ExplicitIgnoreChanges { get; set; } = new();

    [JsonPropertyName("generated_plan_changes")]
    public StrategyGeneratedPlanDiff GeneratedPlanChanges { get; set; } = new();

    [JsonPropertyName("metadata_only")]
    public bool MetadataOnly { get; set; }

    [JsonPropertyName("validation")]
    public AuthoringValidationResult Validation { get; set; } = new();

    [JsonPropertyName("historical_intent_preserved")]
    public bool HistoricalIntentPreserved { get; set; }

    [JsonPropertyName("restore_publishes_new_revision")]
    public bool RestorePublishesNewRevision { get; set; }

    [JsonPropertyName("publication_activates_strategy")]
    public bool PublicationActivatesStrategy { get; set; }
}

public sealed class StrategyBaseSnapshotDiff
{
    [JsonPropertyName("changed")]
    public bool Changed { get; set; }

    [JsonPropertyName("before_reference")]
    public StrategyBaseReference? BeforeReference { get; set; }

    [JsonPropertyName("after_reference")]
    public StrategyBaseReference? AfterReference { get; set; }

    [JsonPropertyName("before_fingerprint")]
    public string BeforeFingerprint { get; set; } = "";

    [JsonPropertyName("after_fingerprint")]
    public string AfterFingerprint { get; set; } = "";

    [JsonPropertyName("embedded_snapshot_changed")]
    public bool EmbeddedSnapshotChanged { get; set; }
}

public sealed class StrategyDirectiveDiff
{
    [JsonPropertyName("added")]
    public List<AuthoringDiffItem> Added { get; set; } = [];

    [JsonPropertyName("removed")]
    public List<AuthoringDiffItem> Removed { get; set; } = [];

    [JsonPropertyName("changed")]
    public List<AuthoringDiffItem> Changed { get; set; } = [];

    [JsonPropertyName("change_count")]
    public int ChangeCount { get; set; }
}

public sealed class StrategyGeneratedPlanDiff
{
    [JsonPropertyName("changed")]
    public bool Changed { get; set; }

    [JsonPropertyName("before_fingerprint")]
    public string? BeforeFingerprint { get; set; }

    [JsonPropertyName("after_fingerprint")]
    public string AfterFingerprint { get; set; } = "";

    [JsonPropertyName("before_rule_count")]
    public int BeforeRuleCount { get; set; }

    [JsonPropertyName("after_rule_count")]
    public int AfterRuleCount { get; set; }

    [JsonPropertyName("rule_count_change")]
    public int RuleCountChange { get; set; }
}

public sealed class StrategyAuthoringReview
{
    [JsonPropertyName("source_changes")]
    public AuthoringSourceDiff? SourceChanges { get; set; }

    [JsonPropertyName("effective_changes")]
    public AuthoringResolutionDiff? EffectiveChanges { get; set; }

    [JsonPropertyName("validation")]
    public AuthoringValidationResult Validation { get; set; } = new();

    [JsonPropertyName("rule_count")]
    public int RuleCount { get; set; }

    [JsonPropertyName("fingerprints")]
    public Dictionary<string, string> Fingerprints { get; set; } = [];

    [JsonPropertyName("publication_activates_strategy")]
    public bool PublicationActivatesStrategy { get; set; }
}

public sealed class AuthoringValidationResult
{
    [JsonPropertyName("valid")]
    public bool Valid { get; set; }

    [JsonPropertyName("errors")]
    public List<AuthoringValidationError> Errors { get; set; } = [];
}

public sealed class AuthoringValidationError
{
    [JsonPropertyName("code")]
    public string Code { get; set; } = "";

    [JsonPropertyName("setting_id")]
    public string? SettingId { get; set; }

    [JsonPropertyName("message")]
    public string Message { get; set; } = "";
}

public sealed class AuthoringSourceDiff
{
    [JsonPropertyName("added")]
    public List<AuthoringDiffItem> Added { get; set; } = [];

    [JsonPropertyName("removed")]
    public List<AuthoringDiffItem> Removed { get; set; } = [];

    [JsonPropertyName("changed")]
    public List<AuthoringDiffItem> Changed { get; set; } = [];

    [JsonPropertyName("unchanged")]
    public List<AuthoringDiffItem> Unchanged { get; set; } = [];

    [JsonPropertyName("metadata_changes")]
    public List<AuthoringMetadataChange> MetadataChanges { get; set; } = [];

    [JsonPropertyName("change_count")]
    public int ChangeCount { get; set; }

    [JsonPropertyName("created")]
    public bool Created { get; set; }
}

public sealed class AuthoringDiffItem
{
    [JsonPropertyName("setting_id")]
    public string SettingId { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("before")]
    public JsonElement? Before { get; set; }

    [JsonPropertyName("after")]
    public JsonElement? After { get; set; }
}

public sealed class AuthoringMetadataChange
{
    [JsonPropertyName("field")]
    public string Field { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";

    [JsonPropertyName("before")]
    public JsonElement? Before { get; set; }

    [JsonPropertyName("after")]
    public JsonElement? After { get; set; }
}

public sealed class AuthoringResolutionDiff
{
    [JsonPropertyName("changed")]
    public List<AuthoringResolutionChange> Changed { get; set; } = [];

    [JsonPropertyName("provenance_changed")]
    public List<AuthoringResolutionChange> ProvenanceChanged { get; set; } = [];

    [JsonPropertyName("change_count")]
    public int ChangeCount { get; set; }
}

public sealed class AuthoringResolutionChange
{
    [JsonPropertyName("setting_id")]
    public string SettingId { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("before")]
    public StrategyResolvedSetting Before { get; set; } = new();

    [JsonPropertyName("after")]
    public StrategyResolvedSetting After { get; set; } = new();
}

public sealed class StrategyRebasePreview
{
    [JsonPropertyName("base_changes")]
    public AuthoringSourceDiff BaseChanges { get; set; } = new();

    [JsonPropertyName("inherited_effective_changes")]
    public List<AuthoringResolutionChange> InheritedEffectiveChanges { get; set; } = [];

    [JsonPropertyName("local_overrides_unchanged")]
    public List<AuthoringStableSetting> LocalOverridesUnchanged { get; set; } = [];

    [JsonPropertyName("explicit_ignores_unchanged")]
    public List<AuthoringStableSetting> ExplicitIgnoresUnchanged { get; set; } = [];

    [JsonPropertyName("validation_errors")]
    public List<AuthoringValidationError> ValidationErrors { get; set; } = [];

    [JsonPropertyName("review_fingerprint")]
    public string ReviewFingerprint { get; set; } = "";

    [JsonPropertyName("summary")]
    public Dictionary<string, int> Summary { get; set; } = [];
}

public sealed class AuthoringStableSetting
{
    [JsonPropertyName("setting_id")]
    public string SettingId { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";
}

public sealed class StrategyAuthoringCatalogError
{
    [JsonPropertyName("catalog")]
    public string Catalog { get; set; } = "";

    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("error")]
    public string Error { get; set; } = "";
}

public sealed class BattleListResponse
{
    [JsonPropertyName("items")]
    public List<BattleSummary> Items { get; set; } = [];

    [JsonPropertyName("total")]
    public int Total { get; set; }
}

public sealed class DiscardBattleResponse
{
    [JsonPropertyName("battle_id")]
    public string BattleId { get; set; } = "";

    [JsonPropertyName("discarded_at")]
    public string? DiscardedAt { get; set; }

    [JsonPropertyName("purge_after")]
    public string? PurgeAfter { get; set; }

    [JsonPropertyName("quarantine_path")]
    public string? QuarantinePath { get; set; }

    [JsonPropertyName("files")]
    public List<string> Files { get; set; } = [];
}

public sealed class BattleSummary
{
    [JsonPropertyName("battle_id")]
    public string BattleId { get; set; } = "";

    [JsonPropertyName("captured_at")]
    public string? CapturedAt { get; set; }

    [JsonPropertyName("strategy")]
    public string? Strategy { get; set; }

    [JsonPropertyName("battle_type")]
    public string BattleType { get; set; } = "unknown";

    [JsonPropertyName("battle_type_label")]
    public string? BattleTypeLabel { get; set; }

    [JsonPropertyName("battle_type_confidence")]
    public string? BattleTypeConfidence { get; set; }

    [JsonPropertyName("profile")]
    public string? Profile { get; set; }

    [JsonPropertyName("tier")]
    public int? Tier { get; set; }

    [JsonPropertyName("wave")]
    public int? Wave { get; set; }

    [JsonPropertyName("killed_by")]
    public string? KilledBy { get; set; }

    [JsonPropertyName("league")]
    public string? League { get; set; }

    [JsonPropertyName("rank")]
    public int? Rank { get; set; }

    [JsonPropertyName("real_time")]
    public string? RealTime { get; set; }

    [JsonPropertyName("coins_earned")]
    public string? CoinsEarned { get; set; }

    [JsonPropertyName("coins_per_hour")]
    public string? CoinsPerHour { get; set; }

    [JsonPropertyName("cells_earned")]
    public string? CellsEarned { get; set; }

    [JsonPropertyName("cells_per_hour")]
    public string? CellsPerHour { get; set; }

    [JsonPropertyName("quality")]
    public BattleQuality Quality { get; set; } = new();

    public string CapturedDisplay => FormatDate(CapturedAt);
    public string StrategyDisplay => Strategy ?? Profile ?? "-";
    public string BattleTypeDisplay => BattleTypeLabel ?? BattleType.ToUpperInvariant();
    public string QualityDisplay => Quality.Valid == true ? "Valid" : "Review";

    private static string FormatDate(string? value) =>
        DateTimeOffset.TryParse(value, out var parsed)
            ? parsed.LocalDateTime.ToString("g")
            : value ?? "-";
}

public sealed class BattleQuality
{
    [JsonPropertyName("valid")]
    public bool? Valid { get; set; }
}

public sealed class ActivityResponse
{
    [JsonPropertyName("items")]
    public List<ActivityEntry> Items { get; set; } = [];

    [JsonPropertyName("available_levels")]
    public List<string> AvailableLevels { get; set; } = [];

    [JsonPropertyName("source_file_id")]
    public string? SourceFileId { get; set; }

    [JsonPropertyName("end_cursor")]
    public string? EndCursor { get; set; }

    [JsonPropertyName("scope")]
    public string Scope { get; set; } = "all";

    [JsonPropertyName("scope_available")]
    public bool ScopeAvailable { get; set; }

    [JsonPropertyName("scope_id")]
    public string? ScopeId { get; set; }

    [JsonPropertyName("scope_started_at")]
    public string? ScopeStartedAt { get; set; }
}

public sealed class ActivityEntry
{
    [JsonPropertyName("timestamp")]
    public string Timestamp { get; set; } = "";

    [JsonPropertyName("level")]
    public string Level { get; set; } = "";

    [JsonPropertyName("message")]
    public string Message { get; set; } = "";

    [JsonPropertyName("activity_kind")]
    public string? ActivityKind { get; set; }

    [JsonPropertyName("display_message")]
    public string? ActivityDisplayMessage { get; set; }

    [JsonPropertyName("detail_items")]
    public List<ActivityDetailItem> DetailItems { get; set; } = [];

    public string DisplayMessage =>
        string.IsNullOrWhiteSpace(ActivityDisplayMessage)
            ? Message
            : ActivityDisplayMessage;

    public string ExpandedMessage
    {
        get
        {
            var fullLine = $"[{Level} {Timestamp}] {Message}";
            if (DetailItems.Count == 0)
            {
                return fullLine;
            }

            var separatorIndex = Message.IndexOf(
                " — ",
                StringComparison.Ordinal);
            var heading = separatorIndex < 0
                ? Message
                : Message[..separatorIndex];
            var items = DetailItems.Select(item =>
                string.IsNullOrWhiteSpace(item.Alias)
                    ? $"• {item.Label}"
                    : $"• {item.Alias} — {item.Label}");
            return $"[{Level} {Timestamp}] {heading}"
                + Environment.NewLine
                + Environment.NewLine
                + string.Join(Environment.NewLine, items);
        }
    }
}

public sealed class ActivityDetailItem
{
    [JsonPropertyName("alias")]
    public string Alias { get; set; } = "";

    [JsonPropertyName("label")]
    public string Label { get; set; } = "";
}

public sealed record ReportRow(string Category, string Name, string Value);
public sealed record ReportSection(string Name, IReadOnlyList<ReportRow> Rows);
public sealed record PerkRow(string Rank, string Color, string Perk, string Confidence);

public sealed class ClientSettings
{
    public string BaseUrl { get; set; } = "http://127.0.0.1:8787";
    public string SshDestination { get; set; } = "";
    public int LocalTunnelPort { get; set; } = 8787;
    public int RemoteApiPort { get; set; } = 8787;
    public int WindowsBlueStacksAdbPort { get; set; } = 5555;
    public int LinuxAdbForwardPort { get; set; } = 5555;
    public bool HostPerformanceSamplingEnabled { get; set; } = true;
    public WindowPlacementSettings? MainWindowPlacement { get; set; }
    public WindowPlacementSettings? BattleHistoryWindowPlacement { get; set; }
    public MainWindowLayoutSettings MainWindowLayout { get; set; } = new();
}

public sealed class WindowPlacementSettings
{
    public double Left { get; set; }
    public double Top { get; set; }
    public double Width { get; set; }
    public double Height { get; set; }
    public bool Maximized { get; set; }
}

public sealed class MainWindowLayoutSettings
{
    public double SidebarWidth { get; set; } = 380;
    public double LatestBattleHeight { get; set; } = 205;
    public bool PreviousStateExpanded { get; set; } = true;
    public bool HostHealthExpanded { get; set; }
    public bool LatestBattleExpanded { get; set; } = true;
    public int SidebarTabIndex { get; set; }
}
