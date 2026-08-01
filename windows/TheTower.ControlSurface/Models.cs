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

    [JsonPropertyName("runtime")]
    public RuntimeStatus Runtime { get; set; } = new();

    [JsonPropertyName("process_service")]
    public ProcessServiceStatus? ProcessService { get; set; }

    [JsonPropertyName("request")]
    public RequestStatus? Request { get; set; }
}

public sealed class CurrentRunStatus
{
    [JsonPropertyName("run_id")]
    public string RunId { get; set; } = "";

    [JsonPropertyName("started_at")]
    public string? StartedAt { get; set; }
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
}

public sealed class DirectiveAcknowledgement
{
    [JsonPropertyName("value")]
    public string? Value { get; set; }

    [JsonPropertyName("at")]
    public string? At { get; set; }

    [JsonPropertyName("acknowledges_current")]
    public bool AcknowledgesCurrent { get; set; }
}

public sealed class ControlStatus
{
    [JsonPropertyName("state")]
    public string State { get; set; } = "UNKNOWN";

    [JsonPropertyName("mode")]
    public string Mode { get; set; } = "UNKNOWN";

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

    [JsonPropertyName("latest_compatible_base_revisions")]
    public List<CompatibleBaseRevision> LatestCompatibleBaseRevisions { get; set; } = [];

    [JsonPropertyName("catalog_errors")]
    public List<StrategyAuthoringCatalogError> CatalogErrors { get; set; } = [];
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

    [JsonPropertyName("runtime_destination")]
    public string RuntimeDestination { get; set; } = "";

    [JsonPropertyName("observation_supported")]
    public bool ObservationSupported { get; set; }

    [JsonPropertyName("repair_supported")]
    public bool RepairSupported { get; set; }
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
    public int SchemaVersion { get; set; } = 2;

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

    [JsonPropertyName("warning")]
    public string? Warning { get; set; }
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
