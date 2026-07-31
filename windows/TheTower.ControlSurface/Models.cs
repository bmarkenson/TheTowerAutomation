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
