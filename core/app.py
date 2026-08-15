from __future__ import annotations

"""Primary application orchestration loop for the automation runtime."""

import copy
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Dict, Any, Mapping, Sequence, Set, Tuple

import cv2
import numpy as np
from numpy.typing import NDArray

from utils.logger import (
    ensure_activity_scope,
    get_activity_scope,
    log,
    log_action_intent,
    log_result,
    new_operation_id,
    set_mission_log_path,
    start_retry_activity_scope,
)
from core.watchdog import (
    CooperativeMutationGuard,
    bring_to_foreground,
    watchdog_process_check,
)
from core.adb_connection import AdbConnectionCoordinator
from core.adb_target_session import AdbTargetSession
from core.artifact_retention import RuntimeArtifactRetention
from core.activity_continuity import ActivityContinuityCoordinator
from core.action_authority import (
    ActionAuthorityDecision,
    AuthorityHold,
    AuthorityHoldState,
    AuxiliaryCollector,
    AuxiliaryRouteLease,
    RuntimeActionAuthority,
    RuntimeActionAuthorityPublisher,
    RuntimeActionClass,
    STRATEGY_GATE_AUXILIARY_ALLOWLIST,
    StrategyGateExitEvent,
)
from core.action_circuit_breaker import ActionCircuitBreaker
from core.input import TapDispatchOutcome, TapDispatchStatus
from core.ss_capture import (
    ScreenshotFailure,
    capture_and_save_screenshot,
    capture_and_save_screenshot_result,
)
from core.screen_geometry import SUPPORTED_DEVICE_SCREEN_SIZES
from core.state_detector import detect_state_and_overlays
from core.automation_supervisor import AutomationSupervisor
from core.daily_gem_scheduler import DailyGemScheduler
from core.event_mission_tracker import EventMissionTracker, format_warning
from core.emulator_recovery import (
    EMULATOR_HOME_POSTCONDITION_TIMEOUT_SECONDS,
    EMULATOR_HOST_ACK_TIMEOUT_SECONDS,
    RecoveryUiDispatchStatus,
    RestartReplayWindow,
)
from core.home_battle import detect_home_battle_control
from core.battle_lifecycle import HomeBattleControl
from core.control_model import (
    BATTLE_WORKFLOW_TERMINAL_STATUSES,
    MANUAL_CONTROL_TERMINAL_STATUSES,
    SETUP_CAPTURE_TERMINAL_STATUSES,
    build_home_ui_reconciliation_receipt,
    build_home_return_reconciliation_receipt,
    build_running_ui_reconciliation_receipt,
    build_running_save_reconciliation_receipt,
    build_terminal_ui_reconciliation_receipt,
    build_terminal_return_reconciliation_receipt,
    intent_matches_evidence,
    observed_game_state,
    ui_reconciliation_receipt_matches_evidence,
    validate_workflow_evidence,
)
from core.battle_stats import (
    attach_observed_run_configuration,
    build_minimal_battle_record_from_player_save,
    make_battle_id,
    persist_battle_record,
)
from core.battle_activation_tracker import BattleActivationTracker
from core.player_save import (
    PlayerSaveParser,
    reconcile_acquired_requirements,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
)
from core.player_save_audit import PlayerSaveAuditCollector
from core.player_save_passive_scheduler import PlayerSavePassiveScheduler
from core.active_run_metric_monitor import ActiveRunMetricMonitor
from core.perk_save_monitor import PerkSaveMonitor
from core.player_save_observation import PlayerSaveObservationContext
from core.player_save_preflight import (
    CarriedEvidenceState,
    PlayerSavePreflightContext,
    PlayerSavePreflightCoordinator,
    requested_player_save_check_ids,
)
from core.player_save_history import (
    PlayerSaveAttachmentContext,
    PlayerSaveHistoryReader,
)
from core.player_save_mapping_observer import (
    BoundPlayerSaveMappingObserver,
)
from core.player_save_mapping_candidates import (
    DEFAULT_MAPPING_CANDIDATE_RECEIPT_PATH,
)
from core.player_save_mapping_staged_candidate import (
    canonical_mapping_decode_start,
    canonical_mapping_runtime_commit,
    observe_canonical_mapping_decode,
)
from core.player_save_serialization import (
    GuardedPlayerSaveSerializer,
    GuardedSerializationStatus,
)
from core.player_save_setup_capture import (
    SetupCaptureError,
    project_forced_save_setup,
)
from core.player_save_temporal import (
    BoundRunningAttachmentSaveEvidence,
    PlayerSaveTemporalClass,
    RunningAttachmentSaveObservations,
    RunningAttachmentTemporalBinding,
)
from core.profile_progression import unavailable_profile_progression
from core.terminal_save_report import (
    terminal_save_report_complete,
    terminal_save_report_structural_complete,
    terminal_history_transition_from_acquisition,
    terminal_mapping_workflow_provenance,
    terminal_save_report_from_acquisition,
    unavailable_terminal_save_report,
)
from core.tournament_conditions import (
    tournament_conditions_complete,
    tournament_conditions_from_acquisition,
    unavailable_tournament_conditions,
)
from core.perk_timeline import PerkTimelineObserver
from core.no_strategy_inventory import (
    NoStrategyInventoryStatus,
    run_no_strategy_in_battle_inventory,
)
from core.no_strategy_observer import (
    DISSONANCE_BADGE_REGION,
    NoStrategyRunObserver,
    detect_dissonance_badge,
)
from core.no_strategy_post_run import (
    NoStrategyPostRunError,
    NoStrategyPostRunPaused,
    capture_post_run_perk_configuration,
    inspect_post_run_free_upgrade_locks,
    load_pending_no_strategy_record,
    open_perks_configuration_for_post_run_capture,
    open_perks_configuration_from_cards,
    restore_post_run_home,
)
from core.run_perk_selector import RunScopedPerkSelector
from core.gc_no_battle_setup import (
    GcNoBattleSetupStatus,
    recover_gc_no_battle_setup_home,
    run_gc_no_battle_setup,
)
from core.gc_preflight import summarize_gc_preflight_mismatch
from core.game_speed import GameSpeedGuard
from core.gate_decisions import (
    STARTUP_GATE_CHECK_LABELS,
    build_gate_decision_options,
    merge_profile_skip_waivers,
    startup_gate_check_catalog,
)
from core.exclusive_validation import (
    ExclusiveValidationDefinition,
    exclusive_validation_definition,
)
from core.menu_reward_badges import (
    measure_home_reward_badges,
    measure_menu_reward_badges,
    menu_reward_alert_visible,
)
from core.mission_reward_scheduler import (
    MissionRewardScheduler,
    WeeklyChestReviewState,
    daily_mission_claims_allowed,
)
from core.run_state import AUTOMATION, ExecMode, RunState
from core.runtime_failure_policy import (
    RuntimeFailureDisposition,
    RuntimeFailureKind,
    decide_runtime_failure,
)
from core.app_setup import AppConfig
from core.status_report import StateChangeTracker, StatusReporter
from core.recovery import handle_unknown_state, update_unknown_state
from core.run_controls import return_home_from_game_over, surrender_run
from automation.missions.manager import (
    MissionManager,
    RESTORED_SESSION_PREFLIGHT_REPORT_KEY,
)
from automation.missions import get_mission
from automation.missions.yaml_mission import YamlMission
from automation.strategies import get_strategy
from handlers.game_over_handler import (
    GameOverHandlingOutcome,
    handle_game_over,
    restore_game_stats_for_terminal_route,
)
from handlers.game_restarted_handler import (
    GameRestartedAction,
    handle_game_restarted,
)
from handlers.tournament_result_handler import (
    dismiss_tournament_results_to_home,
    handle_tournament_results,
)
from handlers.home_screen_handler import handle_home_screen, tap_verified_new_battle
from handlers.free_ticket_handler import (
    FreeTicketRecoveryStatus,
    handle_free_ticket_modal,
)
from handlers.tournament_launch_handler import dispatch_tournament_launch
from handlers.ad_gem_handler import (
    AdGemCollectionStatus,
    handle_ad_gem,
    handle_home_ad_gem,
    is_blind_gem_tapper_active,
    start_blind_gem_tapper,
    stop_blind_gem_tapper,
)
from handlers.daily_gem_handler import (
    DailyGemCleanupResult,
    DailyGemResult,
    handle_daily_gem,
    resume_daily_gem_cleanup,
)
from handlers.dismiss_uw_detail import handle_upgrade_detail_popup
from handlers.mission_reward_handler import (
    MissionRewardCleanupResult,
    MissionRewardResult,
    handle_mission_rewards,
    resume_mission_reward_cleanup,
)
from utils.wave_detector import detect_wave_number_from_image


Frame = NDArray[np.uint8]
HOME_SETUP_MAX_ATTEMPTS = 3
BATTLE_ACTION_DISPATCH_TIMEOUT_SECONDS = 20.0
_BATTLE_SCOPE_UNSET = object()
_GENERIC_SESSION_PREFLIGHT_REASONS = frozenset(
    {
        "configuration mismatch",
        "session preflight mismatch",
        "running-battle strategy validation failed",
    }
)


class App:
    """Main automation orchestrator wrapping capture → detect → dispatch."""

    @staticmethod
    def _flag_recoverable_runtime_failure(
        kind: RuntimeFailureKind,
        reason: str,
    ):
        """Record a recoverable failure without changing global authority."""

        decision = decide_runtime_failure(kind)
        if decision.disposition is not (
            RuntimeFailureDisposition.CONTINUE_DEGRADED
        ):
            raise ValueError(f"{kind.value} requires catastrophic handling")
        try:
            log(
                "[RUNTIME_POLICY] Automation continues degraded: "
                f"kind={kind.value} "
                f"reason={str(reason or 'unavailable').strip()}",
                "WARN",
                console=True,
            )
        except OSError:
            # Reporting is diagnostic, not action authority.  A full or
            # temporarily unavailable log destination must not turn a
            # recoverable runtime failure into an automation shutdown.
            pass
        return decision

    def __init__(
        self,
        config: AppConfig,
        *,
        adb_target_session: Optional[AdbTargetSession] = None,
        adb_connection_coordinator: Optional[AdbConnectionCoordinator] = None,
        gate_decision_prompt: Optional[
            Callable[[Mapping[str, Any]], Optional[str]]
        ] = None,
    ) -> None:
        self._config = config
        self._operator_battle_intent_required = bool(
            config.startup_gate_policy == "operator"
        )
        self._adb_target_session = adb_target_session
        self._adb_connection_coordinator = (
            adb_connection_coordinator
            or AdbConnectionCoordinator(
                manage_connections=config.adb_connection_owner == "runtime",
                emit_events=config.adb_connection_owner == "runtime",
            )
        )
        set_mission_log_path(config.mission_log_path)
        self._supervisor = AutomationSupervisor(
            control_file=config.control_file,
            auto_return_secs=config.auto_return_secs,
            auto_return_enabled=config.auto_return_enabled,
            auto_return_conf_threshold=config.auto_return_conf_threshold,
            coins_toggle_cooldown=config.coins_toggle_cooldown,
            coins_conf_floor=config.coins_conf_floor,
            coins_max_jump_factor=config.coins_max_jump_factor,
            coins_jump_conf_floor=config.coins_jump_conf_floor,
            adb_port_handoff=(
                self._handoff_adb_port if adb_target_session is not None else None
            ),
        )
        if adb_target_session is not None:
            adb_target_session.bind_runtime_owner(
                self._supervisor.runtime_id
            )
        self._mission_mgr = MissionManager(
            self._load_mission(config),
            self._load_strategy(config),
            defer_startup_gates_until_next_run=(
                config.startup_gate_policy
                in {"auto", "auto_validate", "next_run"}
            ),
            validate_attached_battle=(
                config.startup_gate_policy == "auto_validate"
            ),
            skip_attached_checks=(
                config.startup_gate_policy == "auto"
            ),
            await_initial_battle_intent=(
                config.startup_gate_policy == "operator"
            ),
            action_guard_fn=self._runtime_action_guard,
        )
        self._mission_mgr.start()
        self._gate_decision_prompt = gate_decision_prompt
        self._gate_prompted_request_id: Optional[str] = None
        self._startup_gate_waivers: Dict[str, Dict[str, Any]] = {}
        self._last_strategy_request: Optional[Tuple[str, object, str]] = None
        self._pending_strategy_request: Optional[Tuple[str, object, str]] = None
        self._strategy_boundary_confirmed = False
        self._active_exclusive_validation_request_id: Optional[str] = None
        self._active_exclusive_validation_launch_request_id: Optional[str] = None
        self._exclusive_validation_battle_dispatch_hold: Optional[str] = None
        self._exclusive_validation_launch_dispatch_hold: Optional[str] = None
        self._started_battle_workflow_completion: Optional[Dict[str, Any]] = None
        self._exclusive_validation_claimed_start_hold: Optional[str] = None
        self._exclusive_validation_launch_start_hold: Optional[str] = None
        self._exclusive_validation_passive_battle_hold: Optional[str] = None
        self._exclusive_validation_passive_battle_scope_id: Optional[str] = None
        self._exclusive_validation_terminal_hold: Optional[str] = None
        self._exclusive_validation_terminal_mode: Optional[str] = None
        self._exclusive_validation_terminal_outcome: Optional[str] = None
        self._exclusive_validation_terminal_reason: Optional[str] = None
        self._exclusive_validation_terminal_announced: Optional[str] = None
        self._exclusive_validation_terminal_proof_kind_value: Optional[str] = None
        self._exclusive_validation_ownership_hold = False
        self._observe_strategy_request()
        ensure_activity_scope(reason="automation_started")
        self._player_save_runtime_session_id = new_operation_id()
        self._perk_save_monitor_call_lock = threading.RLock()
        self._perk_save_monitor = PerkSaveMonitor()
        self._active_run_metric_monitor = ActiveRunMetricMonitor()
        self._player_save_preflight_session_id = ""
        self._player_save_preflight_result = None
        self._player_save_preflight_activity_scope_id = None
        self._player_save_history_baseline_outcome = None
        runtime_repository_root = Path(__file__).resolve().parents[1]
        runtime_mapping_main_commit = canonical_mapping_runtime_commit(
            repository_root=runtime_repository_root,
        )
        self._player_save_parser = PlayerSaveParser()
        self._player_save_acquirer = (
            StablePlayerSaveAcquirer(
                target_snapshot_fn=adb_target_session.snapshot,
                parser=self._player_save_parser,
                acquisition_start_observer=lambda started_at: (
                    canonical_mapping_decode_start(
                        runtime_main_commit=runtime_mapping_main_commit,
                        acquired_at=started_at,
                    )
                ),
                completion_observer=lambda bundle, start_evidence: (
                    observe_canonical_mapping_decode(
                        bundle.snapshot,
                        repository_root=runtime_repository_root,
                        candidate_store_path=(
                            DEFAULT_MAPPING_CANDIDATE_RECEIPT_PATH
                        ),
                        start_evidence=start_evidence,
                    )
                    if bundle.complete and bundle.snapshot is not None
                    else None
                ),
            )
            if adb_target_session is not None
            else None
        )
        self._player_save_preflight_coordinator = (
            PlayerSavePreflightCoordinator(
                acquirer=self._player_save_acquirer,
                context_fn=self._current_player_save_preflight_context,
                action_guard_fn=self._runtime_action_guard,
                capture_fn=self._capture_frame,
            )
            if adb_target_session is not None
            else None
        )
        save_history_reader = (
            PlayerSaveHistoryReader(
                acquirer=self._player_save_acquirer,
                capture_fn=self._capture_frame,
                attachment_context_fn=(
                    self._current_player_save_attachment_context
                ),
            )
            if adb_target_session is not None
            else None
        )
        self._player_save_history_reader = save_history_reader
        self._activity_continuity = ActivityContinuityCoordinator(
            save_history_reader=(
                save_history_reader.read
                if save_history_reader is not None
                else None
            )
        )
        self._runtime_shutting_down = False
        log(
            f"[RUN_INIT] Startup gate policy={config.startup_gate_policy}",
            "INFO",
        )

        self._state_tracker = StateChangeTracker()
        self._status_reporter = StatusReporter(
            interval_secs=config.status_interval,
            supervisor=self._supervisor,
            save_wave_samples=config.save_wave_samples,
            save_coin_samples=config.save_coin_samples,
        )
        self._artifact_retention = RuntimeArtifactRetention.for_repository(
            Path(__file__).resolve().parent.parent,
            extra_roots=(
                config.save_wave_samples,
                config.save_coin_samples,
            ),
        )

        self._match_trace = config.match_trace
        self._auto_start_enabled = config.auto_start_enabled

        # Structured battle records are the default for every run, including
        # mission-driven runs. Only the explicit fast flag may skip capture;
        # the legacy full flag remains an override when both are supplied.
        self._fast_game_over = config.fast_game_over and not config.full_game_over

        self._last_wave_value: Optional[int] = None
        self._last_wave_conf: float = -1.0
        self._last_wave_ts: float = 0.0
        self._battle_activation_tracker = BattleActivationTracker()
        self._player_save_audit_collector = None
        try:
            self._player_save_audit_collector = PlayerSaveAuditCollector(
                enabled=config.player_save_audit_enabled,
                interval_seconds=config.player_save_audit_interval_seconds,
                acquirer=self._player_save_acquirer,
                acquire_internally=False,
            )
        except Exception:
            log(
                "[PLAYER_SAVE_AUDIT] Collector initialization failed; normal "
                "automation is unaffected",
                "DEBUG",
            )
        # Process-local battle evidence is safe for a terminal record only
        # after this process has observed the active battle in the current
        # continuity scope.  A process starting directly on Game Over has no
        # authority to associate the selected strategy or a restored tracker
        # checkpoint with that completed battle.
        self._observed_active_battle_scope_id: Optional[str] = None
        self._last_unbound_terminal_signature: Optional[
            tuple[str, Optional[str], Optional[str]]
        ] = None
        control_path = Path(config.control_file)
        perk_timeline_state = control_path.with_name(
            f"{control_path.stem}.perk_timeline_state.json"
        )
        self._perk_timeline_observer = PerkTimelineObserver(
            state_path=perk_timeline_state
        )
        self._last_requested_perk_checkpoint_signature = None
        self._pending_perk_timeline_save_checkpoint = None
        self._player_save_passive_scheduler = None
        if self._player_save_acquirer is not None:
            try:
                self._player_save_passive_scheduler = PlayerSavePassiveScheduler(
                    acquirer=self._player_save_acquirer,
                    context_fn=self._current_player_save_observation_context,
                    consumers=(self._consume_passive_player_save_bundle,),
                    interval_seconds=config.player_save_audit_interval_seconds,
                )
            except Exception:
                log(
                    "[PLAYER_SAVE_PASSIVE] Normal passive scheduling was "
                    "unavailable; terminal Perks UI fallback remains active",
                    "WARN",
                )
        self._blind_tapper_suspended = False
        self._tournament_results_captured = False
        self._tournament_terminal_continuation_bound = False
        self._tournament_terminal_continuation_claim = None
        self._tournament_degradation_snapshot = None
        self._no_strategy_observer = NoStrategyRunObserver()
        self._no_strategy_observation_active = False
        self._no_strategy_attachment_boundary_id: Optional[str] = None
        self._no_strategy_inventory_complete = False
        self._no_strategy_inventory_retry_at = 0.0
        self._pending_no_strategy_record: Optional[Dict[str, Any]] = None
        self._no_strategy_post_run_stage: Optional[str] = None
        self._no_strategy_post_run_retry_at = 0.0
        self._no_strategy_post_run_recovery_checked = False
        perk_selector_state = (
            Path(config.control_file).parent / "run_perk_selector.json"
        )
        self._run_perk_selector = RunScopedPerkSelector(perk_selector_state)
        self._game_speed_guard = GameSpeedGuard()
        self._game_speed_guard.set_target(
            self._supervisor.game_speed_target
        )
        self._run_initialization_gate_logged = False
        self._session_preflight_gate_logged = False
        self._session_preflight_terminal_blocked_logged = False
        self._session_preflight_repair_denial_logged = False
        self._steady_run_entry_pending = False
        self._last_logged_home_battle_control: Optional[HomeBattleControl] = None
        self._last_home_policy_signature: Optional[Tuple[object, ...]] = None
        rollover_state = Path(config.control_file).parent / "daily_gem_state.json"
        self._daily_gem_scheduler = DailyGemScheduler(rollover_state)
        self._mission_reward_scheduler = MissionRewardScheduler()
        self._weekly_chest_review_state = WeeklyChestReviewState()
        event_mission_state = (
            Path(config.control_file).parent / "event_mission_tracker.json"
        )
        self._event_mission_tracker = EventMissionTracker(event_mission_state)
        self._action_authority = RuntimeActionAuthority()
        self._action_authority_publisher = RuntimeActionAuthorityPublisher(
            Path(config.control_file).with_name("strategy_action_gate.json"),
            owner=self._runtime_status_owner(),
        )
        self._authority_battle_active = False
        self._authority_primary_state = "UNKNOWN"
        self._authority_holds: tuple[AuthorityHoldState, ...] = ()
        self._external_development_hold_active = False
        self._interactive_development_ack: Optional[Dict[str, Any]] = None
        self._emulator_maintenance_hold_active = False
        self._emulator_recovery_ack: Optional[Dict[str, Any]] = None
        self._emulator_replay_window: Optional[RestartReplayWindow] = None
        self._emulator_recovery_request_id: Optional[str] = None
        self._emulator_recovery_durable_state: Optional[str] = None
        self._emulator_recovery_resume_attempts = 0
        self._emulator_recovery_end_run_attempts = 0
        self._emulator_recovery_launch_attempts = 0
        self._emulator_recovery_launch_dispatched_at: Optional[float] = None
        self._emulator_recovery_home_attempts = 0
        self._emulator_recovery_home_dispatch: Optional[Dict[str, Any]] = None
        self._emulator_recovery_next_action_at = 0.0
        self._emulator_recovery_force_new_battle = False
        self._emulator_recovery_action_logged = False
        self._emulator_recovery_terminal_pending: Optional[Dict[str, Any]] = None
        self._last_screenshot_capture_result = None
        self._control_observation_sequence = 0
        self._control_observation: Optional[Dict[str, Any]] = None
        self._terminal_home_continuation: Optional[Dict[str, Any]] = None
        self._action_circuit_breaker = ActionCircuitBreaker(failure_limit=1)
        self._home_ad_gem_absence_candidate: Optional[
            tuple[tuple[object, ...], str]
        ] = None
        self._home_ad_gem_nonreplayable_epochs: Set[
            tuple[object, ...]
        ] = set()
        self._free_ticket_recovery_attempts: Dict[str, int] = {}
        self._free_ticket_recovery_cleared: Set[str] = set()
        self._free_ticket_recovery_warnings: Set[str] = set()
        self._free_ticket_retry_home_candidates: Dict[str, str] = {}
        self._uncertain_lifecycle_actions: Set[str] = set()
        self._uncertain_legacy_home_launch_epochs: Set[
            tuple[object, ...]
        ] = set()
        self._blocking_primary_hold_active = False
        self._blocking_primary_state: Optional[str] = None
        self._watchdog_mutation_guard = CooperativeMutationGuard(
            lambda: self._runtime_action_guard(
                action_class=RuntimeActionClass.LIFECYCLE_ACTION
            )
        )
        self._pending_auxiliary_cleanup: Optional[
            tuple[str, AuxiliaryRouteLease]
        ] = None

    def _activation_tracker(self) -> BattleActivationTracker:
        """Return the run-scoped passive tracker, including in partial test apps."""

        tracker = getattr(self, "_battle_activation_tracker", None)
        if tracker is None:
            tracker = BattleActivationTracker()
            self._battle_activation_tracker = tracker
        return tracker

    def _observe_player_save_audit_screen(
        self,
        detection: Mapping[str, Any],
    ) -> None:
        """Forward passive boundary evidence without affecting App dispatch."""

        collector = getattr(self, "_player_save_audit_collector", None)
        if collector is None:
            return
        try:
            collector.observe_screen(detection)
        except Exception:
            log(
                "[PLAYER_SAVE_AUDIT] Boundary observation was rejected; "
                "normal automation is unaffected",
                "DEBUG",
            )

    def _observe_player_save_audit_visual_events(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> None:
        """Forward only tracker-confirmed metadata to the passive sidecar."""

        collector = getattr(self, "_player_save_audit_collector", None)
        if collector is None or not events:
            return
        try:
            collector.observe_visual_events(events)
        except Exception:
            log(
                "[PLAYER_SAVE_AUDIT] Visual metadata was rejected; normal "
                "automation is unaffected",
                "DEBUG",
            )

    def _observe_player_save_audit_perk_mapping_evidence(self) -> None:
        """Forward newly accepted UI batches to the passive save sidecar."""

        collector = getattr(self, "_player_save_audit_collector", None)
        if collector is None or not getattr(collector, "enabled", False):
            return
        try:
            batches = self._perk_timeline().drain_mapping_evidence()
            if batches:
                collector.observe_perk_mapping_evidence(batches)
        except Exception:
            log(
                "[PLAYER_SAVE_AUDIT] Perk ID calibration evidence was "
                "rejected; normal automation is unaffected",
                "DEBUG",
            )

    def _reset_player_save_audit_perk_mapping_evidence(self) -> None:
        """Close a passive UI/save correlation window at a run boundary."""

        collector = getattr(self, "_player_save_audit_collector", None)
        if collector is None:
            return
        try:
            collector.reset_perk_mapping_evidence()
        except Exception:
            log(
                "[PLAYER_SAVE_AUDIT] Perk ID calibration reset was rejected; "
                "normal automation is unaffected",
                "DEBUG",
            )

    def _current_player_save_observation_context(
        self,
    ) -> Optional[PlayerSaveObservationContext]:
        """Return the current exact active-round binding, or no authority."""

        runtime_session_id = str(
            getattr(self, "_player_save_runtime_session_id", "") or ""
        ).strip()
        scope_id = self._current_run_scope_id()
        if (
            not runtime_session_id
            or not scope_id
            or getattr(self, "_observed_active_battle_scope_id", None)
            != scope_id
        ):
            return None
        session = getattr(self, "_adb_target_session", None)
        try:
            target_snapshot = session.snapshot() if session is not None else None
        except Exception:
            return None
        target_binding = PlayerSaveTargetBinding.from_snapshot(target_snapshot)
        if target_binding is None:
            return None
        try:
            return PlayerSaveObservationContext(
                runtime_session_id=runtime_session_id,
                activity_scope_id=scope_id,
                target_binding=target_binding,
            )
        except (TypeError, ValueError):
            return None

    def _current_perk_save_monitor_context(
        self,
    ) -> Optional[PlayerSaveObservationContext]:
        """Compatibility alias for focused callers predating the shared API."""

        return self._current_player_save_observation_context()

    def _consume_passive_player_save_bundle(
        self,
        acquisition: PlayerSaveAcquisitionBundle,
        context: PlayerSaveObservationContext,
        reason_code: str,
    ) -> None:
        """Fan one scheduled passive read through the global observation API."""

        self._publish_player_save_observation(
            acquisition,
            context=context,
            reason_code=reason_code,
        )

    def _publish_player_save_observation(
        self,
        acquisition: PlayerSaveAcquisitionBundle,
        *,
        context: Optional[PlayerSaveObservationContext],
        reason_code: str,
        observe_perks: bool = True,
        bind_new_activity: bool = False,
    ) -> None:
        """Deliver one immutable parsed bundle to every eligible consumer."""

        monitor = getattr(self, "_perk_save_monitor", None)
        metric_monitor = getattr(self, "_active_run_metric_monitor", None)
        if context is not None and (monitor is not None or metric_monitor is not None):
            with self._perk_save_monitor_guard():
                if monitor is not None:
                    try:
                        perk_bound = bool(
                            not bind_new_activity
                            or monitor.bind_context(
                                context,
                                new_activity=True,
                            )
                        )
                        if perk_bound and observe_perks:
                            monitor.observe_bundle(
                                acquisition,
                                context=context,
                            )
                            self._retain_perk_timeline_save_checkpoint(
                                monitor,
                                context,
                            )
                    except Exception:
                        log(
                            "[PLAYER_SAVE_API] Perk projection rejected one "
                            "shared observation; other consumers are unaffected",
                            "DEBUG",
                        )
                if metric_monitor is not None:
                    try:
                        metric_bound = bool(
                            not bind_new_activity
                            or metric_monitor.bind_context(
                                context,
                                new_activity=True,
                            )
                        )
                        if metric_bound:
                            disposition = metric_monitor.observe_bundle(
                                acquisition,
                                context=context,
                            )
                            if disposition.startswith("accepted_"):
                                self._log_active_run_metric_summary(
                                    metric_monitor,
                                    context,
                                )
                    except Exception:
                        log(
                            "[PLAYER_SAVE_API] Active-metric projection rejected "
                            "one shared observation; other consumers are "
                            "unaffected",
                            "DEBUG",
                        )
        self._observe_shared_acquisition_for_audit(
            acquisition,
            reason_code=reason_code,
        )

    def _observe_shared_acquisition_for_audit(
        self,
        acquisition: PlayerSaveAcquisitionBundle,
        *,
        reason_code: str,
    ) -> None:
        collector = getattr(self, "_player_save_audit_collector", None)
        if collector is None:
            return
        try:
            collector.observe_acquisition(
                acquisition,
                reason_code=reason_code,
            )
        except Exception:
            log(
                "[PLAYER_SAVE_AUDIT] Shared acquisition projection was "
                "rejected; normal automation is unaffected",
                "DEBUG",
            )

    def _bind_new_perk_monitor_activity(self) -> None:
        monitor = getattr(self, "_perk_save_monitor", None)
        metric_monitor = getattr(self, "_active_run_metric_monitor", None)
        context = self._current_player_save_observation_context()
        if context is not None and (
            monitor is not None or metric_monitor is not None
        ):
            with self._perk_save_monitor_guard():
                self._pending_perk_timeline_save_checkpoint = None
                if monitor is not None:
                    monitor.bind_context(context, new_activity=True)
                if metric_monitor is not None:
                    metric_monitor.bind_context(context, new_activity=True)

    @staticmethod
    def _log_active_run_metric_summary(
        monitor: ActiveRunMetricMonitor,
        context: PlayerSaveObservationContext,
    ) -> None:
        """Log one compact checkpoint without exposing arbitrary save data."""

        summary = monitor.latest_summary(context)
        if not isinstance(summary, Mapping):
            return
        whole_run = summary.get("whole_run")
        interval = summary.get("interval")
        whole_run_cph = (
            whole_run.get("coins_per_hour")
            if isinstance(whole_run, Mapping)
            else None
        )
        interval_cph = (
            interval.get("coins_per_hour")
            if isinstance(interval, Mapping)
            else None
        )
        log(
            "[PLAYER_SAVE_METRICS] "
            f"wave={summary.get('saved_wave')} "
            f"revision={summary.get('save_revision')} "
            f"whole_run_cph={whole_run_cph or 'unavailable'} "
            f"interval_cph={interval_cph or 'unavailable'}",
            "INFO",
        )

    def _retain_perk_timeline_save_checkpoint(
        self,
        monitor: PerkSaveMonitor,
        context: PlayerSaveObservationContext,
    ) -> None:
        """Queue one detached positive prefix while holding the monitor lock."""

        checkpoint = monitor.bound_checkpoint_evidence(context)
        if checkpoint is not None:
            self._pending_perk_timeline_save_checkpoint = (
                context,
                checkpoint,
            )

    def _sync_perk_timeline_save_checkpoint(self) -> Optional[str]:
        """Apply worker-produced save evidence on the serialized App thread."""

        context = self._current_perk_save_monitor_context()
        with self._perk_save_monitor_guard():
            pending = getattr(
                self,
                "_pending_perk_timeline_save_checkpoint",
                None,
            )
            if not (
                isinstance(pending, tuple)
                and len(pending) == 2
                and isinstance(pending[0], PlayerSaveObservationContext)
                and isinstance(pending[1], Mapping)
            ):
                return None
            pending_context, checkpoint = pending
            if context is None:
                return None
            if pending_context != context:
                self._pending_perk_timeline_save_checkpoint = None
                return None
            self._pending_perk_timeline_save_checkpoint = None
        disposition = self._perk_timeline().observe_saved_checkpoint(
            checkpoint
        )
        if disposition.startswith("rejected_"):
            log(
                "[PERK_TIMELINE] Monitor-validated save checkpoint was "
                f"rejected by the timeline: {disposition}",
                "WARN",
            )
        return disposition

    def _perk_save_monitor_guard(self) -> threading.RLock:
        """Serialize domain-monitor calls at the App coordination boundary."""

        guard = getattr(self, "_perk_save_monitor_call_lock", None)
        if guard is None:
            guard = threading.RLock()
            self._perk_save_monitor_call_lock = guard
        return guard

    def _sync_perk_exhaustion_evidence(self) -> None:
        monitor = getattr(self, "_perk_save_monitor", None)
        context = self._current_player_save_observation_context()
        if monitor is None or context is None:
            return
        evidence = self._perk_timeline().exhaustion_evidence()
        if not isinstance(evidence, Mapping):
            return
        with self._perk_save_monitor_guard():
            if not monitor.observe_exhaustion(evidence, context=context):
                return
            bound = monitor.bound_exhaustion_evidence(context)
        identity = bound.get("active_round_identity") if bound else None
        if isinstance(identity, Mapping):
            self._perk_timeline().bind_exhaustion_identity(identity)

    def _request_perk_checkpoint_for_passive_boundary(self) -> None:
        """Coalesce stable top-bar events onto the normal passive scheduler."""

        snapshot = self._perk_timeline().snapshot()
        passive = snapshot.get("passive_top_bar")
        if not isinstance(passive, Mapping):
            return
        raw_boundaries = passive.get("selection_boundaries")
        boundary_waves = tuple(
            item.get("scheduled_wave")
            for item in raw_boundaries
            if isinstance(item, Mapping)
        ) if isinstance(raw_boundaries, Sequence) else ()
        exhaustion = passive.get("exhaustion")
        exhaustion_id = (
            exhaustion.get("event_id")
            if isinstance(exhaustion, Mapping)
            else None
        )
        if not boundary_waves and not exhaustion_id:
            return
        signature = (boundary_waves, exhaustion_id)
        if signature == getattr(
            self,
            "_last_requested_perk_checkpoint_signature",
            None,
        ):
            return
        scheduler = getattr(self, "_player_save_passive_scheduler", None)
        if scheduler is None:
            return
        reason = (
            "perk_exhaustion_boundary"
            if exhaustion_id
            else "perk_selection_boundary"
        )
        if scheduler.request_observation(reason):
            self._last_requested_perk_checkpoint_signature = signature

    def _perk_timeline(self) -> PerkTimelineObserver:
        """Return the run-scoped perk observer, including in partial test apps."""

        observer = getattr(self, "_perk_timeline_observer", None)
        if observer is None:
            observer = PerkTimelineObserver()
            self._perk_timeline_observer = observer
        return observer

    def _observe_terminal_run_binding(
        self,
        detection: Mapping[str, Any],
        *,
        continuity_pending: bool,
    ) -> None:
        """Bind process-local evidence only after a settled active observation."""

        state = str(detection.get("state") or "UNKNOWN").upper()
        if state == "RUNNING":
            if continuity_pending:
                return
            scope_id = self._current_run_scope_id()
            changed = (
                scope_id is not None
                and scope_id
                != getattr(self, "_observed_active_battle_scope_id", None)
            )
            self._observed_active_battle_scope_id = scope_id
            self._last_unbound_terminal_signature = None
            if changed:
                self._last_requested_perk_checkpoint_signature = None
                self._bind_new_perk_monitor_activity()
            return
        if state in {"HOME", "HOME_SCREEN"} and HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        ) is HomeBattleControl.NEW_BATTLE:
            self._observed_active_battle_scope_id = None
            self._last_unbound_terminal_signature = None

    def _terminal_run_binding(self) -> dict[str, Any]:
        """Describe whether process-local evidence belongs to this terminal run."""

        current_scope_id = self._current_run_scope_id()
        observed_scope_id = getattr(
            self,
            "_observed_active_battle_scope_id",
            None,
        )
        if current_scope_id is not None and observed_scope_id == current_scope_id:
            return {
                "schema_version": 1,
                "status": "bound",
                "reason": "active_battle_observed_in_current_scope",
                "activity_scope_run_id": current_scope_id,
            }
        if observed_scope_id is None:
            reason = "terminal_without_observed_active_battle"
        elif current_scope_id is None:
            reason = "current_activity_scope_unavailable"
        else:
            reason = "activity_scope_changed_after_active_observation"
        return {
            "schema_version": 1,
            "status": "unbound",
            "reason": reason,
            "activity_scope_run_id": current_scope_id,
            "observed_active_scope_run_id": observed_scope_id,
        }

    def _terminal_battle_context(
        self,
        terminal_state: str,
        *,
        observed_run_configuration: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build terminal context without crossing an unproven run boundary."""

        context, _acquisition, _mapping_observer = self._terminal_battle_bundle(
            terminal_state,
            observed_run_configuration=observed_run_configuration,
        )
        return context

    def _terminal_battle_bundle(
        self,
        terminal_state: str,
        *,
        observed_run_configuration: Optional[Mapping[str, Any]] = None,
    ) -> tuple[
        dict[str, Any],
        Optional[PlayerSaveAcquisitionBundle],
        Optional[BoundPlayerSaveMappingObserver],
    ]:
        """Build terminal context and return its one exact typed acquisition."""

        terminal = str(terminal_state or "UNKNOWN").upper()
        binding = self._terminal_run_binding()
        if terminal == "GAME_OVER" and binding["status"] == "bound":
            self._sync_perk_exhaustion_evidence()
        terminal_save = self._capture_terminal_player_save(
            terminal,
            run_binding=binding,
        )
        self._sync_perk_timeline_save_checkpoint()
        acquisition = terminal_save.get("_acquisition")
        typed_acquisition = (
            acquisition
            if isinstance(acquisition, PlayerSaveAcquisitionBundle)
            else None
        )
        context: dict[str, Any] = {
            "strategy": None,
            "terminal_state": terminal,
            "run_configuration": {},
            "run_binding": binding,
            "profile_progression": terminal_save["profile_progression"],
            "terminal_save_report": terminal_save["terminal_save_report"],
        }
        replay = getattr(self, "_emulator_replay_window", None)
        if (
            isinstance(replay, RestartReplayWindow)
            and replay.battle_scope
            and replay.battle_scope == binding.get("activity_scope_run_id")
        ):
            context["emulator_recovery"] = replay.as_dict()
        if terminal == "GAME_OVER":
            monitor = getattr(self, "_perk_save_monitor", None)
            monitor_context = (
                self._current_player_save_observation_context()
                if binding["status"] == "bound"
                else None
            )
            if monitor is not None:
                with self._perk_save_monitor_guard():
                    context["perk_save_monitoring"] = monitor.terminal_evidence(
                        context=monitor_context,
                        terminal_state=terminal,
                    )
        if binding["status"] == "bound" and terminal in {
            "GAME_OVER",
            "TOURNAMENT_RESULTS",
        }:
            metric_monitor = getattr(
                self,
                "_active_run_metric_monitor",
                None,
            )
            metric_context = self._current_player_save_observation_context()
            if metric_monitor is not None:
                with self._perk_save_monitor_guard():
                    context["active_run_metrics"] = (
                        metric_monitor.terminal_evidence(
                            context=metric_context,
                            terminal_save_report=terminal_save[
                                "terminal_save_report"
                            ],
                        )
                    )
        battle_conditions = terminal_save.get("battle_conditions")
        if isinstance(battle_conditions, Mapping):
            context["battle_conditions"] = dict(battle_conditions)
        if binding["status"] != "bound":
            signature = (
                terminal,
                binding.get("activity_scope_run_id"),
                binding.get("observed_active_scope_run_id"),
            )
            if getattr(self, "_last_unbound_terminal_signature", None) != signature:
                self._perk_timeline().reset(fresh_battle=False)
                self._reset_player_save_audit_perk_mapping_evidence()
                self._activation_tracker().reset()
                log(
                    "[RUN_BINDING] Terminal battle was not observed active in "
                    "the current process and activity scope; selected strategy "
                    "and process-local run evidence are omitted from the record",
                    "WARN",
                    console=True,
                )
                self._last_unbound_terminal_signature = signature
            return context, typed_acquisition, terminal_save.get(
                "_mapping_observer"
            )

        self._last_unbound_terminal_signature = None
        strategy = self._mission_mgr.strategy
        manager_data = self._mission_mgr.ctx.data
        if RESTORED_SESSION_PREFLIGHT_REPORT_KEY in manager_data:
            session_preflight_report = manager_data.get(
                RESTORED_SESSION_PREFLIGHT_REPORT_KEY
            )
        else:
            session_preflight_report = manager_data.get(
                "mission_vars",
                {},
            ).get("gc_session_preflight_evidence")
        context.update(
            {
                "strategy": strategy.name if strategy else None,
                "run_configuration": (
                    strategy.run_configuration() if strategy else {}
                ),
                "strategy_definition_fingerprint": (
                    strategy.definition_fingerprint() if strategy else None
                ),
                "last_wave": self._last_wave_value,
                "last_wave_confidence": self._last_wave_conf,
                "coin_rate_samples": self._status_reporter.coin_rate_samples,
                "game_speed_control": self._game_speed_control_snapshot(),
                "survival_ability_activations": (
                    self._activation_tracker().snapshot()
                ),
                "perk_selection_timeline": self._perk_timeline().snapshot(),
            }
        )
        if isinstance(session_preflight_report, Mapping) and session_preflight_report:
            context["session_preflight_evidence"] = copy.deepcopy(
                dict(session_preflight_report)
            )
        degradation_fn = getattr(
            self._mission_mgr,
            "running_configuration_degradation",
            None,
        )
        running_degradation = (
            degradation_fn() if callable(degradation_fn) else None
        )
        if isinstance(running_degradation, Mapping):
            context["running_configuration_degradation"] = copy.deepcopy(
                dict(running_degradation)
            )
        if isinstance(observed_run_configuration, Mapping):
            context["observed_run_configuration"] = dict(
                observed_run_configuration
            )
        return context, typed_acquisition, terminal_save.get(
            "_mapping_observer"
        )

    def _capture_terminal_player_save(
        self,
        terminal_state: str,
        *,
        run_binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Capture one stable save for progression and terminal battle stats."""

        captured_at = datetime.now(timezone.utc)
        terminal = str(terminal_state or "UNKNOWN").upper()

        def unavailable(reason: str, *, mapping_id: Optional[str] = None):
            result = {
                "profile_progression": unavailable_profile_progression(
                    reason,
                    captured_at=captured_at.isoformat(),
                    mapping_id=mapping_id,
                ),
                "terminal_save_report": unavailable_terminal_save_report(
                    reason,
                    terminal_state=terminal,
                    captured_at=captured_at.isoformat(),
                    mapping_id=mapping_id,
                ),
            }
            if terminal == "TOURNAMENT_RESULTS":
                result["battle_conditions"] = unavailable_tournament_conditions(
                    reason
                )
            return result

        session = getattr(self, "_adb_target_session", None)
        if session is None:
            return unavailable("adb_target_session_unavailable")
        acquirer = getattr(self, "_player_save_acquirer", None)
        if acquirer is None:
            return unavailable("shared_player_save_acquirer_unavailable")

        try:
            scope = get_activity_scope()
        except Exception:
            scope = None
        if terminal in {kind.value for kind in PlayerSaveBoundaryKind}:
            runtime_session_id = str(
                getattr(self, "_player_save_runtime_session_id", "") or ""
            ).strip()
            if not runtime_session_id:
                return unavailable("player_save_runtime_session_unavailable")
            boundary = PlayerSaveNaturalBoundary(
                kind=PlayerSaveBoundaryKind(terminal),
                observed_at=captured_at,
                runtime_session_id=runtime_session_id,
                activity_scope_id=(
                    str(scope.get("run_id") or "")
                    if isinstance(scope, Mapping)
                    else None
                ),
            )
            acquisition = acquirer.acquire(
                PlayerSaveAcquisitionType.NATURAL_BOUNDARY,
                boundary=boundary,
            )
        else:
            acquisition = acquirer.acquire(
                PlayerSaveAcquisitionType.PASSIVE_STABLE_READ
            )

        reason_code = (
            "game_over"
            if terminal == "GAME_OVER"
            else "tournament_results"
            if terminal == "TOURNAMENT_RESULTS"
            else "terminal_capture"
        )
        monitor_context = (
            self._current_player_save_observation_context()
            if terminal in {"GAME_OVER", "TOURNAMENT_RESULTS"}
            and run_binding.get("status") == "bound"
            else None
        )
        self._publish_player_save_observation(
            acquisition,
            context=monitor_context,
            reason_code=reason_code,
            observe_perks=terminal == "GAME_OVER",
        )

        if not acquisition.complete or acquisition.snapshot is None:
            reason = (
                "adb_target_changed_during_terminal_capture"
                if acquisition.status
                is PlayerSaveAcquisitionStatus.BINDING_LOST
                else "stable_terminal_save_unavailable"
            )
            log(
                "[TERMINAL_SAVE] Stable terminal save capture was unavailable "
                "without blocking battle stats: "
                f"reason={acquisition.reason}",
                "WARN",
            )
            return unavailable(reason)
        snapshot = acquisition.snapshot

        try:
            progression = snapshot.profile_progression
        except Exception:
            progression = None
        if not isinstance(progression, Mapping) or not progression:
            normalized_progression = unavailable_profile_progression(
                "exact_version_progression_projection_unavailable",
                captured_at=captured_at.isoformat(),
                mapping_id=getattr(snapshot, "mapping_id", None),
            )
        else:
            normalized_progression = dict(progression)
            source = dict(normalized_progression.get("source") or {})
            source["acquisition"] = acquisition.redacted_provenance()
            normalized_progression["source"] = source

        try:
            history_transition = terminal_history_transition_from_acquisition(
                acquisition,
                terminal_state=terminal,
                run_binding=run_binding,
                activity_scope=scope,
            )
        except Exception as exc:
            log(
                "[TERMINAL_SAVE] Structural Battle History projection failed "
                f"without blocking the terminal fallback: {exc}",
                "WARN",
            )
            history_transition = {
                "schema_version": 1,
                "status": "unavailable",
                "complete": False,
                "reason": "terminal_history_attachment_failed",
            }
        activity_continuity = getattr(self, "_activity_continuity", None)
        if activity_continuity is not None:
            activity_continuity.publish_terminal_history_handoff(
                history_transition
            )
        mapping_observer = None
        try:
            workflow_provenance = terminal_mapping_workflow_provenance(
                acquisition,
                terminal_state=terminal,
                run_binding=run_binding,
                activity_scope=scope,
                history_transition=history_transition,
                pid=max(1, os.getpid()),
            )
        except Exception:
            workflow_provenance = None
        if workflow_provenance is not None:
            boundary = acquisition.boundary
            expected_scope_id = (
                str(scope.get("run_id") or "")
                if isinstance(scope, Mapping)
                else ""
            )

            def terminal_context_guard() -> bool:
                try:
                    current_scope = get_activity_scope()
                    target_snapshot = session.snapshot()
                except Exception:
                    return False
                return bool(
                    boundary is not None
                    and str(
                        getattr(
                            self,
                            "_player_save_runtime_session_id",
                            "",
                        )
                        or ""
                    )
                    == boundary.runtime_session_id
                    and isinstance(current_scope, Mapping)
                    and str(current_scope.get("run_id") or "")
                    == expected_scope_id
                    and acquisition.matches_binding(
                        PlayerSaveTargetBinding.from_snapshot(target_snapshot)
                    )
                )

            mapping_observer = BoundPlayerSaveMappingObserver(
                snapshot=snapshot,
                context_guard_fn=terminal_context_guard,
                workflow_provenance=workflow_provenance,
            )
        try:
            terminal_report = terminal_save_report_from_acquisition(
                acquisition,
                terminal_state=terminal,
                run_binding=run_binding,
                activity_scope=scope,
                history_transition=history_transition,
            )
        except Exception as exc:
            log(
                "[TERMINAL_SAVE] Semantic battle report projection failed "
                f"without blocking the More Stats fallback: {exc}",
                "WARN",
            )
            terminal_report = unavailable_terminal_save_report(
                "terminal_history_attachment_failed",
                terminal_state=terminal,
                captured_at=captured_at.isoformat(),
                mapping_id=getattr(snapshot, "mapping_id", None),
                save_revision=getattr(snapshot, "save_revision", None),
            )
        result = {
            "profile_progression": normalized_progression,
            "terminal_save_report": terminal_report,
            "_acquisition": acquisition,
            "_mapping_observer": mapping_observer,
        }
        if terminal == "TOURNAMENT_RESULTS":
            try:
                conditions = tournament_conditions_from_acquisition(acquisition)
            except Exception:
                conditions = unavailable_tournament_conditions(
                    "condition_projection_failed",
                    source={"acquisition": acquisition.redacted_provenance()},
                )
            explicit_unavailable = bool(
                isinstance(conditions, Mapping)
                and conditions.get("status") == "unavailable"
                and conditions.get("complete") is False
                and isinstance(conditions.get("ui_fallback"), Mapping)
            )
            if not (
                tournament_conditions_complete(conditions)
                or explicit_unavailable
            ):
                conditions = unavailable_tournament_conditions(
                    "condition_projection_unavailable",
                    source={"acquisition": acquisition.redacted_provenance()},
                )
            result["battle_conditions"] = dict(conditions)
        log(
            "[TERMINAL_SAVE] Captured terminal account and battle projections "
            f"revision={getattr(snapshot, 'save_revision', None)} "
            f"progression={normalized_progression.get('status') or 'unknown'} "
            f"report={terminal_report.get('status') or 'unknown'}",
            "DEBUG",
        )
        return result

    def _accept_pending_terminal_history_handoff(self):
        """Consume a carried terminal tail for this exact process and target."""

        activity_continuity = getattr(self, "_activity_continuity", None)
        if activity_continuity is None:
            return None
        scope = get_activity_scope()
        if not isinstance(scope, Mapping) or not isinstance(
            scope.get("pending_terminal_history_handoff"), Mapping
        ):
            return None
        scope_id = str(scope.get("run_id") or "").strip()
        runtime_session_id = str(
            getattr(self, "_player_save_runtime_session_id", "") or ""
        ).strip()
        session = getattr(self, "_adb_target_session", None)
        try:
            target_snapshot = session.snapshot() if session is not None else None
        except Exception:
            target_snapshot = None
        outcome = activity_continuity.accept_pending_terminal_history_handoff(
            expected_scope_id=scope_id,
            runtime_session_id=runtime_session_id,
            target_snapshot=target_snapshot,
        )
        self._terminal_history_handoff_outcome = outcome
        self._terminal_history_handoff_scope_id = scope_id
        return outcome

    @staticmethod
    def _activity_scope_has_history_baseline(scope: Any) -> bool:
        if not isinstance(scope, Mapping):
            return False
        metadata = scope.get("latest_completed_battle")
        return bool(
            isinstance(metadata, Mapping)
            and str(metadata.get("fingerprint") or "").strip()
        )

    def _capture_terminal_profile_progression(self) -> dict[str, Any]:
        """Compatibility wrapper for callers that need only global progression."""

        return self._capture_terminal_player_save(
            "UNKNOWN",
            run_binding={},
        )["profile_progression"]

    def _get_action_authority(self) -> RuntimeActionAuthority:
        """Return the central authority, including for partial test instances."""

        authority = getattr(self, "_action_authority", None)
        if authority is None:
            authority = RuntimeActionAuthority()
            self._action_authority = authority
        return authority

    def _get_action_circuit_breaker(self) -> ActionCircuitBreaker:
        breaker = getattr(self, "_action_circuit_breaker", None)
        if breaker is None:
            breaker = ActionCircuitBreaker(failure_limit=1)
            self._action_circuit_breaker = breaker
        return breaker

    def _home_ad_gem_circuit_epoch(self) -> tuple[object, ...]:
        try:
            evidence = self._current_control_workflow_evidence()
        except Exception:
            evidence = None
        if not isinstance(evidence, Mapping):
            evidence = getattr(self, "_control_observation", None)
        if not isinstance(evidence, Mapping):
            evidence = {}
        return (
            evidence.get("runtime_id"),
            evidence.get("pid"),
            evidence.get("adb_target"),
            evidence.get("target_generation"),
            evidence.get("activity_scope_run_id"),
        )

    def _observe_action_circuits(self, detection: Mapping[str, Any]) -> None:
        """Reset process-local breakers only on material passive evidence."""

        action = "home_ad_gem"
        active = bool(
            str(detection.get("state") or "").upper() == "HOME_SCREEN"
            and "HOME_AD_GEMS_AVAILABLE"
            in set(detection.get("overlays") or ())
        )
        breaker = self._get_action_circuit_breaker()
        state = str(detection.get("state") or "").upper()
        if not active:
            if state in {"UNKNOWN", "FREE_TICKET"}:
                # UNKNOWN/incomplete pixels and a blocking modal that hides
                # Home are not material evidence that the failed source
                # disappeared.
                self._home_ad_gem_absence_candidate = None
                return
            previous = breaker.snapshot(action)
            if previous is None:
                self._home_ad_gem_absence_candidate = None
                return
            epoch = self._home_ad_gem_circuit_epoch()
            if previous.epoch != epoch:
                recovered = breaker.reset(action)
                self._home_ad_gem_nonreplayable_epoch_set().discard(
                    previous.epoch
                )
                self._home_ad_gem_absence_candidate = None
            elif not previous.tripped:
                recovered = breaker.reset(action)
                self._home_ad_gem_absence_candidate = None
            elif epoch in self._home_ad_gem_nonreplayable_epoch_set():
                # Accepted input without an authoritative result cannot be
                # rearmed by navigating away or by time alone.
                self._home_ad_gem_absence_candidate = None
                return
            else:
                observation = getattr(self, "_control_observation", None)
                observation_id = str(
                    (observation or {}).get("observation_id")
                    if isinstance(observation, Mapping)
                    else ""
                ) or f"sequence:{getattr(self, '_control_observation_sequence', 0)}"
                candidate = getattr(
                    self,
                    "_home_ad_gem_absence_candidate",
                    None,
                )
                if candidate is None or candidate[0] != epoch:
                    self._home_ad_gem_absence_candidate = (
                        epoch,
                        observation_id,
                    )
                    return
                if candidate[1] == observation_id:
                    return
                recovered = breaker.reset(action)
                self._home_ad_gem_absence_candidate = None
            if recovered:
                log(
                    "[ACTION_CIRCUIT] Home ad-gem circuit recovered after two "
                    "stable source-absent observations or a binding change",
                    "INFO",
                )
            return
        self._home_ad_gem_absence_candidate = None
        epoch = self._home_ad_gem_circuit_epoch()
        previous = breaker.snapshot(action)
        breaker.allows(action, epoch=epoch)
        if (
            previous is not None
            and previous.tripped
            and previous.epoch != epoch
        ):
            self._home_ad_gem_nonreplayable_epoch_set().discard(
                previous.epoch
            )
            log(
                "[ACTION_CIRCUIT] Home ad-gem circuit rearmed for a new "
                "runtime, process, target, generation, or battle-scope epoch",
                "INFO",
            )

    def _home_ad_gem_circuit_allows(
        self,
        *,
        epoch: Optional[tuple[object, ...]] = None,
    ) -> bool:
        resolved_epoch = epoch or self._home_ad_gem_circuit_epoch()
        if resolved_epoch in self._home_ad_gem_nonreplayable_epoch_set():
            return False
        return self._get_action_circuit_breaker().allows(
            "home_ad_gem",
            epoch=resolved_epoch,
        )

    def _home_ad_gem_nonreplayable_epoch_set(
        self,
    ) -> Set[tuple[object, ...]]:
        epochs = getattr(self, "_home_ad_gem_nonreplayable_epochs", None)
        if epochs is None:
            epochs = set()
            self._home_ad_gem_nonreplayable_epochs = epochs
        return epochs

    def _record_home_ad_gem_outcome(
        self,
        outcome: AdGemCollectionStatus | bool,
        *,
        epoch: Optional[tuple[object, ...]] = None,
    ) -> None:
        scheduled_epoch = epoch or self._home_ad_gem_circuit_epoch()
        if scheduled_epoch != self._home_ad_gem_circuit_epoch():
            # A final control/target guard changed the owner while the handler
            # was running. Never attribute that result to the new epoch.
            return
        normalized = (
            outcome
            if isinstance(outcome, AdGemCollectionStatus)
            else AdGemCollectionStatus.COLLECTED
            if outcome
            else AdGemCollectionStatus.EXHAUSTED
        )
        breaker = self._get_action_circuit_breaker()
        if normalized is AdGemCollectionStatus.COLLECTED:
            self._home_ad_gem_nonreplayable_epoch_set().discard(
                scheduled_epoch
            )
            if breaker.record_success("home_ad_gem", epoch=scheduled_epoch):
                log(
                    "[ACTION_CIRCUIT] Home ad-gem circuit recovered after a "
                    "verified collection",
                    "INFO",
                )
            return
        if normalized not in {
            AdGemCollectionStatus.EXHAUSTED,
            AdGemCollectionStatus.UNCERTAIN,
        }:
            return
        if normalized is AdGemCollectionStatus.UNCERTAIN:
            self._home_ad_gem_nonreplayable_epoch_set().add(
                scheduled_epoch
            )
        if breaker.record_failure(
            "home_ad_gem",
            epoch=scheduled_epoch,
            reason=(
                "the Home ad-gem input result was uncertain and cannot be replayed"
                if normalized is AdGemCollectionStatus.UNCERTAIN
                else "the bounded Home ad-gem transaction did not clear its target"
            ),
        ):
            log(
                "[ACTION_CIRCUIT] Home ad-gem retries are disabled for this "
                "exact observation epoch after one exhausted or uncertain "
                "bounded transaction; lifecycle handling remains available",
                "WARN",
                console=True,
            )

    def _get_watchdog_mutation_guard(self) -> CooperativeMutationGuard:
        """Return the guard shared by watchdog recovery and lease holds."""

        guard = getattr(self, "_watchdog_mutation_guard", None)
        if guard is None:
            guard = CooperativeMutationGuard(
                lambda: self._runtime_action_guard(
                    action_class=RuntimeActionClass.LIFECYCLE_ACTION
                )
            )
            self._watchdog_mutation_guard = guard
        return guard

    @staticmethod
    def _current_run_scope_id() -> Optional[str]:
        scope = get_activity_scope()
        if not isinstance(scope, Mapping):
            return None
        run_id = str(scope.get("run_id") or "").strip()
        return run_id or None

    def _observe_battle_authority_precondition(
        self,
        detection: Mapping[str, Any],
    ) -> bool:
        """Track same-battle authority independently of temporary UI screens."""

        state = str(detection.get("state") or "UNKNOWN").upper()
        control = HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        )
        active = bool(getattr(self, "_authority_battle_active", False))
        manager_method = getattr(
            getattr(self, "_mission_mgr", None),
            "active_battle_observed",
            None,
        )
        if callable(manager_method):
            try:
                manager_active = manager_method()
            except Exception:
                manager_active = None
            if isinstance(manager_active, bool):
                active = manager_active
        if state == "RUNNING":
            active = True
        elif state in {"GAME_OVER", "TOURNAMENT_RESULTS", "WORKSHOP"}:
            active = False
        elif state in {"HOME", "HOME_SCREEN"}:
            if control is HomeBattleControl.NEW_BATTLE:
                active = False
            elif control is HomeBattleControl.RESUME_BATTLE:
                active = True
        self._authority_battle_active = active
        self._authority_primary_state = state
        return active

    def _update_action_authority(
        self,
        *,
        detection: Optional[Mapping[str, Any]] = None,
        holds: Optional[tuple[AuthorityHoldState, ...]] = None,
        shutting_down: bool = False,
        observed_battle_scope: object = _BATTLE_SCOPE_UNSET,
    ) -> None:
        """Publish fresh Pause, ownership, battle, and screen inputs to the matrix."""

        if detection is not None:
            active_battle = self._observe_battle_authority_precondition(
                detection
            )
        else:
            active_battle = bool(
                getattr(self, "_authority_battle_active", False)
            )
        if holds is not None:
            self._authority_holds = tuple(
                item
                for item in holds
                if item.hold is not AuthorityHold.EXTERNAL_DEVELOPMENT
            )
        current_holds = tuple(getattr(self, "_authority_holds", ()))
        if bool(
            getattr(self, "_external_development_hold_active", False)
        ):
            current_holds += (
                AuthorityHoldState(
                    AuthorityHold.EXTERNAL_DEVELOPMENT,
                    "interactive development owns the cooperative input window",
                ),
            )
        if bool(getattr(self, "_emulator_maintenance_hold_active", False)):
            # Maintenance is the single healthy route owner. Passive
            # Start/Attach, ordinary continuity/initialization, and the
            # blocking-modal shell describe work superseded by this recovery
            # boundary and must hand off rather than stack. Explicit manual,
            # development, setup-capture, and exclusive owners remain
            # genuinely competing and therefore fail closed.
            current_holds = tuple(
                item
                for item in current_holds
                if item.hold
                not in {
                    AuthorityHold.ACTIVITY_CONTINUITY,
                    AuthorityHold.RUN_INITIALIZATION,
                    AuthorityHold.SESSION_PREFLIGHT,
                    AuthorityHold.OPERATOR_WORKFLOW,
                    AuthorityHold.BLOCKING_MODAL_RECOVERY,
                }
            )
            if not any(
                item.hold is AuthorityHold.EMULATOR_MAINTENANCE
                for item in current_holds
            ):
                replay_collectors = self._emulator_replay_auxiliary_collectors()
                current_holds += (
                    AuthorityHoldState(
                        AuthorityHold.EMULATOR_MAINTENANCE,
                        (
                            "BlueStacks maintenance owns recovery while "
                            "independent in-battle collectors remain available"
                            if replay_collectors
                            else (
                                "BlueStacks maintenance owns host and game "
                                "recovery"
                            )
                        ),
                        allowed_auxiliary_collectors=replay_collectors,
                    ),
                )
        supervisor = getattr(self, "_supervisor", None)
        paused = bool(
            supervisor is not None
            and getattr(supervisor, "is_paused", False)
        )
        control_state = getattr(AUTOMATION, "state", None)
        runtime_stopped = bool(
            control_state is RunState.STOPPED
            or str(control_state) in {"STOPPED", "RunState.STOPPED"}
        )
        battle_scope = (
            self._current_run_scope_id()
            if observed_battle_scope is _BATTLE_SCOPE_UNSET
            else (
                str(observed_battle_scope).strip() or None
                if observed_battle_scope is not None
                else None
            )
        )
        self._get_action_authority().update_context(
            global_pause=paused,
            active_battle=active_battle,
            battle_scope=battle_scope,
            primary_state=str(
                getattr(self, "_authority_primary_state", "UNKNOWN")
            ),
            holds=current_holds,
            runtime_stopped=runtime_stopped,
            shutting_down=shutting_down,
        )

    def _emulator_replay_auxiliary_collectors(
        self,
    ) -> tuple[AuxiliaryCollector, ...]:
        """Return collectors safe after Resume while replay accounting is held."""

        replay = getattr(self, "_emulator_replay_window", None)
        supervisor = getattr(self, "_supervisor", None)
        maintenance = getattr(supervisor, "emulator_maintenance", None)
        request_id = str(
            getattr(self, "_emulator_recovery_request_id", None) or ""
        )
        if not (
            bool(getattr(self, "_emulator_maintenance_hold_active", False))
            and isinstance(replay, RestartReplayWindow)
            and replay.resume_dispatched
            and not replay.caught_up
            and not bool(
                getattr(self, "_emulator_recovery_force_new_battle", False)
            )
            and isinstance(maintenance, Mapping)
            and maintenance.get("state") == "host_restarted"
            and str(maintenance.get("request_id") or "") == request_id
            and replay.request_id == request_id
            and getattr(self, "_authority_primary_state", "UNKNOWN")
            == "RUNNING"
            and bool(getattr(self, "_authority_battle_active", False))
        ):
            return ()
        current_scope = self._current_run_scope_id()
        if replay.battle_scope and current_scope != replay.battle_scope:
            return ()
        return STRATEGY_GATE_AUXILIARY_ALLOWLIST

    def _runtime_status_owner(self) -> Dict[str, object]:
        """Return the runtime owner bound to the held target generation."""

        owner = self._supervisor.current_exclusive_validation_owner()
        session = getattr(self, "_adb_target_session", None)
        if session is None:
            return owner
        try:
            target = session.snapshot()
        except Exception:
            return owner
        if target.owned:
            owner = {
                **owner,
                "adb_target": target.target,
                "target_generation": target.generation,
            }
        return owner

    def _publish_action_authority(
        self,
        *,
        runtime_active: bool = True,
    ) -> bool:
        publisher = getattr(self, "_action_authority_publisher", None)
        if publisher is None:
            return False
        supervisor = getattr(self, "_supervisor", None)
        owner = self._runtime_status_owner() if supervisor is not None else None
        manager = getattr(self, "_mission_mgr", None)
        awaiting_intent = getattr(
            manager, "awaiting_initial_battle_intent", None
        )
        active_battle_observed = getattr(
            manager, "active_battle_observed", None
        )
        strategy_request = getattr(
            supervisor,
            "strategy_request",
            None,
        )
        pending_strategy = getattr(self, "_pending_strategy_request", None)
        current_strategy = (
            self._current_strategy_name()
            if manager is not None
            else str(
                getattr(getattr(self, "_config", None), "strategy_name", "none")
                or "none"
            )
            .strip()
            .lower()
        )
        startup_default = (
            str(strategy_request[0]).strip().lower()
            if isinstance(strategy_request, tuple)
            and len(strategy_request) >= 1
            else current_strategy
        )
        degradation_fn = getattr(
            manager,
            "running_configuration_degradation",
            None,
        )
        running_degradation = (
            degradation_fn() if callable(degradation_fn) else None
        )
        return publisher.publish(
            self._get_action_authority().snapshot(),
            runtime_active=runtime_active,
            owner=owner,
            interactive_development_lease=getattr(
                self,
                "_interactive_development_ack",
                None,
            ),
            acknowledgements=getattr(
                supervisor,
                "control_acknowledgements",
                None,
            ),
            control_model={
                "schema_version": 1,
                "emulator_maintenance": copy.deepcopy(
                    getattr(self, "_emulator_recovery_ack", None)
                ),
                "catastrophic_pause_hold": (
                    supervisor.catastrophic_pause_hold
                    if supervisor is not None
                    else {"active": False, "reason": None}
                ),
                "startup_gate_policy": str(
                    getattr(
                        getattr(self, "_config", None),
                        "startup_gate_policy",
                        "",
                    )
                    or ""
                )
                .strip()
                .lower()
                or None,
                "observation": copy.deepcopy(
                    getattr(self, "_control_observation", None)
                ),
                "active_run_performance": (
                    self._active_run_performance_evidence(current_strategy)
                ),
                "battle_lifecycle": {
                    "awaiting_initial_intent": bool(
                        awaiting_intent()
                        if callable(awaiting_intent)
                        else False
                    ),
                    "active_battle_adopted": bool(
                        active_battle_observed()
                        if callable(active_battle_observed)
                        else False
                    ),
                    "explicit_home_intent_required": bool(
                        getattr(
                            self,
                            "_operator_battle_intent_required",
                            False,
                        )
                    ),
                    "terminal_home_continuation": (
                        self._terminal_home_continuation_status()
                    ),
                },
                "strategy_scope": {
                    "startup_default": startup_default,
                    "active_battle": (
                        current_strategy
                        if callable(active_battle_observed)
                        and active_battle_observed()
                        else None
                    ),
                    "pending_next_boundary": (
                        str(pending_strategy[0]).strip().lower()
                        if isinstance(pending_strategy, tuple)
                        and len(pending_strategy) >= 3
                        and pending_strategy[2] == "next_boundary"
                        else None
                    ),
                    "pending_active_battle": (
                        str(pending_strategy[0]).strip().lower()
                        if isinstance(pending_strategy, tuple)
                        and len(pending_strategy) >= 3
                        and pending_strategy[2] == "active_battle"
                        else None
                    ),
                    "request_id": (
                        str(strategy_request[1]).strip()
                        if isinstance(strategy_request, tuple)
                        and len(strategy_request) >= 2
                        and str(strategy_request[1] or "").strip()
                        else None
                    ),
                    "observation_only": bool(
                        callable(active_battle_observed)
                        and active_battle_observed()
                        and current_strategy == "none"
                    ),
                    "degradation": (
                        copy.deepcopy(dict(running_degradation))
                        if isinstance(running_degradation, Mapping)
                        else None
                    ),
                },
            },
        )

    def _active_run_performance_evidence(
        self,
        current_strategy: str,
    ) -> Optional[dict[str, Any]]:
        """Publish a bounded, passive, exact-regime performance projection."""

        monitor = getattr(self, "_active_run_metric_monitor", None)
        manager = getattr(self, "_mission_mgr", None)
        strategy = getattr(manager, "strategy", None)
        if monitor is None or strategy is None:
            return None
        context = self._current_player_save_observation_context()
        if context is None:
            return None
        try:
            with self._perk_save_monitor_guard():
                evidence = monitor.performance_evidence(context, limit=3)
            if not isinstance(evidence, Mapping):
                return None
            run_configuration = strategy.run_configuration()
            if not isinstance(run_configuration, Mapping):
                return None
            encoded = json.dumps(
                dict(run_configuration),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return {
                **copy.deepcopy(dict(evidence)),
                "strategy": str(current_strategy or "").strip().lower(),
                "strategy_definition_fingerprint": (
                    strategy.definition_fingerprint()
                ),
                "configuration_fingerprint": hashlib.sha256(encoded).hexdigest(),
                "tier": run_configuration.get("tier"),
            }
        except (TypeError, ValueError):
            return None

    def _record_control_observation(
        self,
        detection: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Bind one passive game observation to this runtime and ADB target."""

        sequence = int(getattr(self, "_control_observation_sequence", 0)) + 1
        self._control_observation_sequence = sequence
        state = str(detection.get("state") or "UNKNOWN").strip().upper()
        if state not in {
            "HOME",
            "HOME_SCREEN",
            "RUNNING",
            "GAME_OVER",
            "TOURNAMENT_RESULTS",
            "WORKSHOP",
        }:
            state = "UNKNOWN"
        home_control = HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        ).value
        active_battle = self._observe_battle_authority_precondition(detection)
        target_generation = None
        session = getattr(self, "_adb_target_session", None)
        if session is not None:
            try:
                target = session.snapshot()
            except Exception:
                target = None
            if target is not None and target.owned:
                target_generation = target.generation
        observation = {
            "schema_version": 1,
            "observation_id": (
                str(
                    self._supervisor.current_exclusive_validation_owner().get(
                        "runtime_id"
                    )
                    or "runtime"
                )
                + f":{sequence}"
            ),
            "observed_at": datetime.now(timezone.utc).astimezone().isoformat(
                timespec="seconds"
            ),
            "primary_state": state,
            "home_battle_control": home_control,
            "game_state": observed_game_state(
                state,
                home_control,
                active_battle=active_battle,
            ),
            "active_battle": active_battle,
            "activity_scope_run_id": self._current_run_scope_id(),
            "target_generation": target_generation,
        }
        self._prior_control_observation = getattr(
            self,
            "_control_observation",
            None,
        )
        self._control_observation = observation
        return dict(observation)

    def _yield_on_unexpected_manual_activity(self) -> bool:
        """Pause instead of competing after an unowned active-to-Home change."""

        previous = getattr(self, "_prior_control_observation", None)
        current = getattr(self, "_control_observation", None)
        if getattr(
            self._supervisor,
            "unexpected_manual_yield_emergency",
            False,
        ):
            evidence = self._current_control_workflow_evidence()
            if evidence is None:
                return True
            yielded = self._supervisor.yield_to_unexpected_manual_activity(
                evidence
            )
            if yielded is not None:
                self._clear_terminal_home_continuation(
                    "automation yielded to unexpected manual activity"
                )
            return yielded is not None
        if not isinstance(previous, Mapping) or not isinstance(current, Mapping):
            return False
        if not (
            previous.get("game_state") == "active_battle"
            and current.get("game_state") == "home_resume_battle"
            and self._supervisor.control_state == "RUNNING"
        ):
            return False
        manual = self._supervisor.manual_control
        if isinstance(manual, Mapping) and manual.get("status") not in (
            MANUAL_CONTROL_TERMINAL_STATUSES
        ):
            return False
        workflow = self._supervisor.battle_workflow
        if isinstance(workflow, Mapping) and workflow.get("status") not in (
            BATTLE_WORKFLOW_TERMINAL_STATUSES
        ):
            return False
        evidence = self._current_control_workflow_evidence()
        if evidence is None:
            return False
        yielded = self._supervisor.yield_to_unexpected_manual_activity(evidence)
        if yielded is None:
            return False
        self._clear_terminal_home_continuation(
            "automation yielded to unexpected manual activity"
        )
        manual_id = str(yielded.get("manual_control_id") or "")
        self._log_operator_workflow_result(
            manual_id,
            purpose="Yielding to unexpected manual activity",
            reason=(
                "Home now offers Resume Battle after an enabled active-battle "
                "observation"
            ),
            result="Automation Paused — manual input authority has priority",
        )
        return True

    def _current_control_workflow_evidence(self) -> Optional[Dict[str, Any]]:
        """Return current observation bound to this exact runtime owner."""

        observation = getattr(self, "_control_observation", None)
        owner = self._supervisor.current_exclusive_validation_owner()
        evidence = validate_workflow_evidence(
            {
                "schema_version": 1,
                "runtime_id": owner.get("runtime_id"),
                "pid": owner.get("pid"),
                "adb_target": owner.get("adb_target"),
                **(dict(observation) if isinstance(observation, Mapping) else {}),
            }
        )
        return dict(evidence) if evidence is not None else None

    def _current_control_request_identity(self) -> Dict[str, object]:
        """Return the exact operator state and terminal-policy request IDs."""

        identity = getattr(
            getattr(self, "_supervisor", None),
            "control_request_identity",
            None,
        )
        if not isinstance(identity, Mapping):
            return {}
        return {
            "state_request_id": identity.get("state_request_id"),
            "mode_request_id": identity.get("mode_request_id"),
        }

    @staticmethod
    def _degradation_requires_home_repair(
        degradation: object,
    ) -> bool:
        """Return whether a degraded run contains configuration work for Home."""

        if not isinstance(degradation, Mapping):
            return False
        raw_sources = degradation.get("sources", ())
        sources = {
            str(value).strip()
            for value in raw_sources
            if str(value).strip()
        } if isinstance(raw_sources, (list, tuple, set, frozenset)) else set()
        source = str(degradation.get("source") or "").strip()
        if source:
            sources.add(source)
        raw_checks = degradation.get("failed_checks", ())
        checks = {
            str(value).strip()
            for value in raw_checks
            if str(value).strip()
        } if isinstance(raw_checks, (list, tuple, set, frozenset)) else set()
        reporting_sources = {"attachment_reporting"}
        reporting_checks = {"workflow_reporting"}
        return not bool(
            sources
            and sources.issubset(reporting_sources)
            and checks.issubset(reporting_checks)
        )

    def _build_terminal_home_continuation_claim(
        self,
        *,
        source: str,
        evidence: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Freeze one future-policy grant at an exact terminal boundary."""

        if not bool(
            getattr(self, "_operator_battle_intent_required", False)
        ):
            return None
        if AUTOMATION.mode is not ExecMode.NEXT_BATTLE:
            return None
        if str(
            getattr(getattr(self, "_supervisor", None), "control_state", "")
        ).upper() != "RUNNING":
            return None
        expected_game_state = {
            "no_strategy_post_run": "game_over",
            "session_preflight_repair": "game_over",
            "degraded_battle_repair": "game_over",
            "tournament_results": "tournament_results",
        }.get(str(source))
        if expected_game_state is None:
            raise ValueError(
                f"Unsupported terminal Home continuation source {source!r}"
            )
        current = (
            dict(evidence)
            if isinstance(evidence, Mapping)
            else self._current_control_workflow_evidence()
        )
        if not isinstance(current, Mapping):
            return None
        if current.get("game_state") != expected_game_state:
            return None
        request_identity = self._current_control_request_identity()
        state_request_id = str(
            request_identity.get("state_request_id") or ""
        ).strip()
        mode_request_id = str(
            request_identity.get("mode_request_id") or ""
        ).strip()
        if not state_request_id or not mode_request_id:
            return None
        binding_fields = (
            "runtime_id",
            "pid",
            "adb_target",
            "target_generation",
            "activity_scope_run_id",
        )
        if any(current.get(field) in {None, ""} for field in binding_fields):
            return None
        return {
            "schema_version": 2,
            "source": str(source),
            "phase": "armed",
            "operation_id": new_operation_id(),
            "dispatch_count": 0,
            "modal_recovery_completed": False,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(
                timespec="seconds"
            ),
            "terminal_observation_id": str(
                current.get("observation_id") or ""
            ),
            "state_request_id": state_request_id,
            "mode_request_id": mode_request_id,
            "binding": {
                field: copy.deepcopy(current.get(field))
                for field in binding_fields
            },
        }

    def _clear_terminal_home_continuation(self, reason: str) -> bool:
        """Discard a process-local Home launch claim without sending input."""

        claim = getattr(self, "_terminal_home_continuation", None)
        if not isinstance(claim, Mapping):
            self._terminal_home_continuation = None
            return False
        recovery_key = self._terminal_home_continuation_recovery_key(claim)
        self._terminal_home_continuation = None
        self._free_ticket_recovery_cleared_set().discard(recovery_key)
        self._free_ticket_recovery_attempt_map().pop(recovery_key, None)
        log(
            "[HOME] Cleared terminal-bound continuation authority — " + reason,
            "INFO",
        )
        return True

    def _commit_terminal_home_continuation(
        self,
        claim: Optional[Mapping[str, Any]],
    ) -> bool:
        """Arm an exact terminal claim only while its requests are unchanged."""

        if not isinstance(claim, Mapping):
            return False
        if AUTOMATION.mode is not ExecMode.NEXT_BATTLE:
            return False
        if str(
            getattr(getattr(self, "_supervisor", None), "control_state", "")
        ).upper() != "RUNNING":
            return False
        identity = self._current_control_request_identity()
        if (
            str(identity.get("state_request_id") or "")
            != str(claim.get("state_request_id") or "")
            or str(identity.get("mode_request_id") or "")
            != str(claim.get("mode_request_id") or "")
        ):
            return False
        committed = copy.deepcopy(dict(claim))
        committed.setdefault("schema_version", 2)
        committed.setdefault("phase", "armed")
        committed.setdefault("operation_id", new_operation_id())
        committed.setdefault("dispatch_count", 0)
        committed.setdefault("modal_recovery_completed", False)
        self._terminal_home_continuation = committed
        log(
            "[HOME] Armed one terminal-bound Continue automatically launch "
            f"from {claim.get('source')}",
            "INFO",
        )
        return True

    def _terminal_home_continuation_ready(
        self,
        *,
        home_control: HomeBattleControl,
    ) -> bool:
        """Validate one exact claim against fresh New Battle Home evidence."""

        claim = getattr(self, "_terminal_home_continuation", None)
        if not isinstance(claim, Mapping):
            return False
        phase = str(claim.get("phase") or "armed")
        if phase not in {"armed", "retry_ready"}:
            return False
        if int(claim.get("dispatch_count") or 0) >= 2:
            self._clear_terminal_home_continuation(
                "the bounded Home launch budget was exhausted"
            )
            return False
        if AUTOMATION.mode is not ExecMode.NEXT_BATTLE:
            self._clear_terminal_home_continuation(
                "the terminal policy changed before Home dispatch"
            )
            return False
        if str(
            getattr(getattr(self, "_supervisor", None), "control_state", "")
        ).upper() != "RUNNING":
            self._clear_terminal_home_continuation(
                "the action-authority request changed before Home dispatch"
            )
            return False
        identity = self._current_control_request_identity()
        if (
            str(identity.get("state_request_id") or "")
            != str(claim.get("state_request_id") or "")
            or str(identity.get("mode_request_id") or "")
            != str(claim.get("mode_request_id") or "")
        ):
            self._clear_terminal_home_continuation(
                "operator state or policy request identity changed"
            )
            return False
        if home_control is not HomeBattleControl.NEW_BATTLE:
            if home_control is HomeBattleControl.RESUME_BATTLE:
                self._clear_terminal_home_continuation(
                    "Home now offers Resume Battle instead of New Battle"
                )
            return False
        current = self._current_control_workflow_evidence()
        if not isinstance(current, Mapping):
            return False
        if current.get("game_state") != "home_new_battle":
            return False
        binding = (
            claim.get("launch_binding")
            if phase == "retry_ready"
            else claim.get("binding")
        )
        binding_fields = (
            "runtime_id",
            "pid",
            "adb_target",
            "target_generation",
            "activity_scope_run_id",
        )
        if not (
            isinstance(binding, Mapping)
            and all(
                binding.get(field) == current.get(field)
                for field in binding_fields
            )
        ):
            self._clear_terminal_home_continuation(
                "runtime, target, or battle scope changed"
            )
            return False
        return True

    @staticmethod
    def _terminal_home_continuation_recovery_key(
        claim: Mapping[str, Any],
    ) -> str:
        return "terminal:" + str(
            claim.get("terminal_observation_id")
            or claim.get("operation_id")
            or "unknown"
        )

    def _mark_terminal_home_continuation_dispatched(self) -> bool:
        """Retain a Home launch until a fresh battle boundary proves success."""

        claim = getattr(self, "_terminal_home_continuation", None)
        if not isinstance(claim, dict):
            return False
        phase = str(claim.get("phase") or "armed")
        if phase not in {"armed", "retry_ready"}:
            return False
        current = self._current_control_workflow_evidence()
        if not isinstance(current, Mapping):
            return False
        binding_fields = (
            "runtime_id",
            "pid",
            "adb_target",
            "target_generation",
        )
        if any(current.get(field) in {None, ""} for field in binding_fields):
            return False
        launch_scope_id = self._current_run_scope_id()
        if not launch_scope_id:
            return False
        claim["phase"] = "action_dispatched"
        claim["dispatch_count"] = int(claim.get("dispatch_count") or 0) + 1
        claim["dispatched_at"] = datetime.now(
            timezone.utc
        ).astimezone().isoformat(timespec="seconds")
        claim["dispatched_monotonic"] = time.monotonic()
        claim["dispatch_observation_id"] = current.get("observation_id")
        claim["modal_recovery_completed"] = False
        claim["launch_binding"] = {
            **{field: copy.deepcopy(current.get(field)) for field in binding_fields},
            "activity_scope_run_id": launch_scope_id,
        }
        log(
            "[HOME] Retained terminal-bound continuation after verified New "
            f"Battle dispatch {claim['dispatch_count']}/2; fresh RUNNING "
            "evidence is still required",
            "INFO",
        )
        return True

    def _terminalize_uncertain_terminal_home_action(self, reason: str) -> None:
        """Clear one local continuation after a possibly delivered input."""

        claim = getattr(self, "_terminal_home_continuation", None)
        recovery_key = (
            self._terminal_home_continuation_recovery_key(claim)
            if isinstance(claim, Mapping)
            else "terminal:unknown"
        )
        self._clear_terminal_home_continuation(reason)
        uncertain = getattr(self, "_uncertain_lifecycle_actions", None)
        if uncertain is None:
            uncertain = set()
            self._uncertain_lifecycle_actions = uncertain
        uncertain.add(recovery_key)
        self._runtime_uncertain_mutation_result(
            reason + "; automation is Paused and the action will not be replayed"
        )

    def _terminal_home_continuation_owner_current(self) -> bool:
        """Revalidate an in-flight process-local launch without sending input."""

        claim = getattr(self, "_terminal_home_continuation", None)
        if not isinstance(claim, Mapping):
            return False
        if AUTOMATION.mode is not ExecMode.NEXT_BATTLE:
            return False
        if str(
            getattr(getattr(self, "_supervisor", None), "control_state", "")
        ).upper() != "RUNNING":
            return False
        identity = self._current_control_request_identity()
        if (
            str(identity.get("state_request_id") or "")
            != str(claim.get("state_request_id") or "")
            or str(identity.get("mode_request_id") or "")
            != str(claim.get("mode_request_id") or "")
        ):
            return False
        workflow = getattr(self._supervisor, "battle_workflow", None)
        manual = getattr(self._supervisor, "manual_control", None)
        if (
            isinstance(workflow, Mapping)
            and workflow.get("status") not in BATTLE_WORKFLOW_TERMINAL_STATUSES
        ) or (
            isinstance(manual, Mapping)
            and manual.get("status") not in MANUAL_CONTROL_TERMINAL_STATUSES
        ):
            return False
        current = self._current_control_workflow_evidence()
        binding = claim.get("launch_binding")
        if not isinstance(current, Mapping) or not isinstance(binding, Mapping):
            return False
        for field in ("runtime_id", "pid", "adb_target", "target_generation"):
            if binding.get(field) != current.get(field):
                return False
        return str(binding.get("activity_scope_run_id") or "") == str(
            self._current_run_scope_id() or ""
        )

    def _mark_terminal_home_continuation_modal_cleared(self) -> bool:
        claim = getattr(self, "_terminal_home_continuation", None)
        if not (
            isinstance(claim, dict)
            and str(claim.get("phase") or "") == "action_dispatched"
        ):
            return False
        claim["modal_recovery_completed"] = True
        claim.pop("retry_home_candidate_observation_id", None)
        claim["modal_recovered_at"] = datetime.now(
            timezone.utc
        ).astimezone().isoformat(timespec="seconds")
        return True

    def _yield_tournament_from_ordinary_launch(self, reason: str) -> bool:
        """Pause rather than adopting manual Tournament activity as ordinary."""

        self._clear_terminal_home_continuation(reason)
        evidence = self._current_control_workflow_evidence()
        yielded = None
        if isinstance(evidence, Mapping):
            yielded = self._supervisor.yield_to_unexpected_manual_activity(
                evidence
            )
        if yielded is None and not getattr(
            self._supervisor,
            "unexpected_manual_yield_emergency",
            False,
        ):
            self._supervisor.pause_for_catastrophic_failure(
                RuntimeFailureKind.CONTROL_AUTHORITY_LOST,
                reason=(
                    reason
                    + "; exact manual-control ownership could not be persisted"
                ),
            )
        self._update_action_authority()
        self._publish_action_authority()
        log(
            f"[HOME] {reason}; automation yielded without adopting the battle",
            "WARN",
            console=True,
        )
        return True

    def _reconcile_terminal_home_continuation(
        self,
        detection: Mapping[str, Any],
    ) -> bool:
        """Advance or revoke the retained launch from one fresh observation."""

        claim = getattr(self, "_terminal_home_continuation", None)
        if not isinstance(claim, dict):
            return False
        phase = str(claim.get("phase") or "armed")
        if phase == "armed":
            if AUTOMATION.mode is not ExecMode.NEXT_BATTLE or str(
                getattr(self._supervisor, "control_state", "")
            ).upper() != "RUNNING":
                return self._clear_terminal_home_continuation(
                    "control changed before Home launch"
                )
            return False
        if phase == "retry_ready":
            if not self._terminal_home_continuation_owner_current():
                return self._clear_terminal_home_continuation(
                    "the retained retry owner changed"
                )
            state = str(detection.get("state") or "UNKNOWN").upper()
            current = self._current_control_workflow_evidence()
            game_state = str((current or {}).get("game_state") or "unknown")
            if self._tournament_battle_guard(detection):
                return self._yield_tournament_from_ordinary_launch(
                    "a Tournament battle appeared before the retained ordinary "
                    "Battle retry"
                )
            if game_state in {"active_battle", "home_new_battle"}:
                return False
            if state == "UNKNOWN":
                return False
            return self._clear_terminal_home_continuation(
                f"the retained retry reached unexpected {state}"
            )
        if phase != "action_dispatched":
            return self._clear_terminal_home_continuation(
                f"unsupported retained launch phase {phase}"
            )
        if not self._terminal_home_continuation_owner_current():
            return self._clear_terminal_home_continuation(
                "runtime, target, scope, or control request changed after dispatch"
            )

        state = str(detection.get("state") or "UNKNOWN").upper()
        if state == "FREE_TICKET":
            claim.pop("retry_home_candidate_observation_id", None)
            return False
        current = self._current_control_workflow_evidence()
        game_state = str((current or {}).get("game_state") or "unknown")
        if self._tournament_battle_guard(detection):
            return self._yield_tournament_from_ordinary_launch(
                "a Tournament battle appeared while an ordinary Battle launch "
                "receipt was pending"
            )
        if game_state == "active_battle":
            return False
        if (
            claim.get("modal_recovery_completed") is True
            and game_state == "home_new_battle"
        ):
            observation_id = str((current or {}).get("observation_id") or "")
            candidate = str(
                claim.get("retry_home_candidate_observation_id") or ""
            )
            if not candidate:
                claim["retry_home_candidate_observation_id"] = observation_id
                return False
            if candidate == observation_id:
                return False
            if int(claim.get("dispatch_count") or 0) < 2:
                claim["phase"] = "retry_ready"
                claim["modal_recovery_completed"] = False
                claim.pop("retry_home_candidate_observation_id", None)
                log(
                    "[HOME] Free Ticket cleared; two stable Home observations "
                    "authorize one verified New Battle retry",
                    "INFO",
                )
            else:
                return self._clear_terminal_home_continuation(
                    "the modal cleared after the final launch attempt"
                )
            return False
        claim.pop("retry_home_candidate_observation_id", None)
        if game_state in {
            "home_resume_battle",
            "game_over",
            "tournament_results",
        } or state not in {"UNKNOWN", "HOME_SCREEN", "RUNNING"}:
            return self._clear_terminal_home_continuation(
                f"the dispatched launch reached unexpected {state}"
            )
        elapsed = time.monotonic() - float(
            claim.get("dispatched_monotonic") or time.monotonic()
        )
        if elapsed >= BATTLE_ACTION_DISPATCH_TIMEOUT_SECONDS:
            recovery_key = self._terminal_home_continuation_recovery_key(claim)
            reason = (
                "the accepted Home action had no authoritative battle or known "
                "blocker outcome within "
                f"{int(BATTLE_ACTION_DISPATCH_TIMEOUT_SECONDS)} seconds"
            )
            self._clear_terminal_home_continuation(reason)
            uncertain = getattr(self, "_uncertain_lifecycle_actions", None)
            if uncertain is None:
                uncertain = set()
                self._uncertain_lifecycle_actions = uncertain
            uncertain.add(recovery_key)
            self._runtime_uncertain_mutation_result(reason)
            return True
        return False

    def _complete_terminal_home_continuation(
        self,
        battle_started: bool,
        detection: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        claim = getattr(self, "_terminal_home_continuation", None)
        if not (
            battle_started is True
            and not self._tournament_battle_guard(detection or {})
            and isinstance(claim, Mapping)
            and str(claim.get("phase") or "")
            in {"action_dispatched", "retry_ready"}
        ):
            return False
        return self._clear_terminal_home_continuation(
            "fresh RUNNING evidence completed the retained launch postcondition"
        )

    def _terminal_home_continuation_status(self) -> Dict[str, object]:
        """Publish non-authoritative operator visibility for a pending claim."""

        claim = getattr(self, "_terminal_home_continuation", None)
        if not isinstance(claim, Mapping):
            return {"pending": False}
        return {
            "pending": True,
            "source": claim.get("source"),
            "phase": claim.get("phase", "armed"),
            "dispatch_count": int(claim.get("dispatch_count") or 0),
            "created_at": claim.get("created_at"),
            "terminal_observation_id": claim.get(
                "terminal_observation_id"
            ),
        }

    def _free_ticket_recovery_attempt_map(self) -> Dict[str, int]:
        attempts = getattr(self, "_free_ticket_recovery_attempts", None)
        if attempts is None:
            attempts = {}
            self._free_ticket_recovery_attempts = attempts
        return attempts

    def _free_ticket_recovery_cleared_set(self) -> Set[str]:
        cleared = getattr(self, "_free_ticket_recovery_cleared", None)
        if cleared is None:
            cleared = set()
            self._free_ticket_recovery_cleared = cleared
        return cleared

    def _free_ticket_recovery_warning_set(self) -> Set[str]:
        warned = getattr(self, "_free_ticket_recovery_warnings", None)
        if warned is None:
            warned = set()
            self._free_ticket_recovery_warnings = warned
        return warned

    @staticmethod
    def _blocking_primary_authority_hold() -> AuthorityHoldState:
        return AuthorityHoldState(
            AuthorityHold.BLOCKING_MODAL_RECOVERY,
            "a recognized blocking modal suppresses every background action",
        )

    def _observe_blocking_primary_boundary(
        self,
        detection: Mapping[str, Any],
    ) -> None:
        """Retain a blocker across captures until fresh known absence."""

        state = str(detection.get("state") or "UNKNOWN").upper()
        if state in {"FREE_TICKET", "GAME_RESTARTED"}:
            self._blocking_primary_hold_active = True
            self._blocking_primary_state = state
        elif state != "UNKNOWN":
            self._blocking_primary_hold_active = False
            self._blocking_primary_state = None

    def _pre_capture_authority_holds(self) -> tuple[AuthorityHoldState, ...]:
        """Keep the last blocking boundary and current control owner passive."""

        holds = list(
            self._refreshed_operator_authority_holds(release_stale=False)
        )
        if bool(getattr(self, "_blocking_primary_hold_active", False)):
            blocker = self._blocking_primary_authority_hold()
            if not any(hold.hold is blocker.hold for hold in holds):
                holds.append(blocker)
        return tuple(holds)

    def _blocking_recovery_handoff(
        self,
        owner: Optional[Tuple[str, str]],
    ) -> tuple[tuple[AuthorityHoldState, ...], bool]:
        """Replace a healthy launch hold, but preserve every stronger owner."""

        blocker = self._blocking_primary_authority_hold()
        competing = self._operator_workflow_authority_hold()
        capture = getattr(self._supervisor, "setup_capture", None)
        setup_active = bool(
            isinstance(capture, Mapping)
            and capture.get("status") not in SETUP_CAPTURE_TERMINAL_STATUSES
        )
        if owner is not None and owner[0] == "emulator_maintenance":
            healthy_maintenance = bool(
                getattr(self, "_emulator_maintenance_hold_active", False)
                and getattr(self._supervisor, "input_authority_error", None)
                is None
                and not setup_active
                and (
                    competing is None
                    or competing.hold is AuthorityHold.OPERATOR_WORKFLOW
                )
                and self._free_ticket_recovery_owner_current(*owner)
            )
            maintenance_hold = AuthorityHoldState(
                AuthorityHold.EMULATOR_MAINTENANCE,
                "BlueStacks maintenance owns its Free Ticket recovery",
            )
            return ((maintenance_hold,), True) if healthy_maintenance else (
                tuple(
                    item
                    for item in (competing, blocker)
                    if item is not None
                ),
                False,
            )
        if owner is not None and owner[0] in {
            "exclusive_validation",
            "exclusive_validation_launch",
        }:
            attempts = self._free_ticket_recovery_attempt_map()
            workflow = getattr(self._supervisor, "battle_workflow", None)
            continuation = getattr(self, "_terminal_home_continuation", None)
            passive_initial_intent = bool(
                int(attempts.get(owner[1], 0)) >= 1
                and competing is not None
                and competing.hold is AuthorityHold.OPERATOR_WORKFLOW
                and self._awaiting_initial_battle_intent()
                and (
                    not isinstance(workflow, Mapping)
                    or workflow.get("status")
                    in BATTLE_WORKFLOW_TERMINAL_STATUSES
                )
                and getattr(
                    self._supervisor,
                    "battle_workflow_error",
                    False,
                )
                is not True
                and not (
                    isinstance(continuation, Mapping)
                    and str(continuation.get("phase") or "armed")
                    in {"action_dispatched", "retry_ready"}
                )
            )
            healthy_validation = bool(
                (competing is None or passive_initial_intent)
                and not setup_active
                and self._free_ticket_recovery_owner_current(*owner)
            )
            validation_hold = AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "exclusive validation owns its Free Ticket recovery",
            )
            return ((validation_hold,), True) if healthy_validation else (
                tuple(
                    item
                    for item in (competing, blocker)
                    if item is not None
                ),
                False,
            )
        if owner is not None and owner[0] == "terminal_continuation":
            healthy_terminal = bool(
                competing is not None
                and competing.hold is AuthorityHold.OPERATOR_WORKFLOW
                and not setup_active
                and self._free_ticket_recovery_owner_current(*owner)
            )
            return ((competing,), True) if healthy_terminal else (
                tuple(
                    item
                    for item in (competing, blocker)
                    if item is not None
                ),
                False,
            )
        healthy = bool(
            owner is not None
            and owner[0] == "battle_workflow"
            and competing is not None
            and competing.hold is AuthorityHold.OPERATOR_WORKFLOW
            and getattr(self._supervisor, "battle_workflow_error", False)
            is not True
            and getattr(self._supervisor, "setup_capture_error", False)
            is not True
            and not setup_active
            and self._free_ticket_recovery_owner_current(*owner)
        )
        if healthy:
            return (competing,), True
        holds = tuple(
            item
            for item in (competing, blocker)
            if item is not None
        )
        return holds, False

    @staticmethod
    def _battle_workflow_recovery_key(workflow: Mapping[str, Any]) -> str:
        return "battle:" + str(workflow.get("request_id") or "unknown")

    def _workflow_dispatch_receipt_mismatch(
        self,
        workflow: Mapping[str, Any],
        current: Mapping[str, Any],
    ) -> Optional[str]:
        """Validate one accepted lifecycle input against its exact owner."""

        raw_receipt = workflow.get("acknowledgement")
        receipt = validate_workflow_evidence(raw_receipt)
        requested = validate_workflow_evidence(workflow.get("evidence"))
        current_evidence = validate_workflow_evidence(current)
        if receipt is None:
            return "dispatch receipt is missing or malformed"
        if requested is None or current_evidence is None:
            return "workflow owner or current runtime evidence is unavailable"
        required = (
            "runtime_id",
            "pid",
            "adb_target",
            "target_generation",
            "activity_scope_run_id",
            "observation_id",
        )
        if any(receipt.get(field) in {None, ""} for field in required):
            return "dispatch receipt is incomplete"
        for field in ("runtime_id", "pid", "adb_target", "target_generation"):
            if receipt.get(field) != requested.get(field):
                return f"dispatch owner changed at {field}"
            if receipt.get(field) != current_evidence.get(field):
                return f"runtime evidence changed at {field}"
        if receipt.get("activity_scope_run_id") != current_evidence.get(
            "activity_scope_run_id"
        ):
            return "battle activity scope changed"
        identity = self._current_control_request_identity()
        if not isinstance(raw_receipt, Mapping):
            return "dispatch control identity is unavailable"
        for field in ("state_request_id", "mode_request_id"):
            retained = str(raw_receipt.get(field) or "")
            current_id = str(identity.get(field) or "")
            if not retained or retained != current_id:
                return f"control request identity changed at {field}"
        return None

    def _free_ticket_recovery_owner(self) -> Optional[Tuple[str, str]]:
        """Return the exact launch transition allowed to claim the modal."""

        maintenance = getattr(self._supervisor, "emulator_maintenance", None)
        home_dispatch = getattr(
            self,
            "_emulator_recovery_home_dispatch",
            None,
        )
        if (
            bool(getattr(self, "_emulator_maintenance_hold_active", False))
            and bool(getattr(self, "_emulator_recovery_force_new_battle", False))
            and isinstance(maintenance, Mapping)
            and maintenance.get("state") == "host_restarted"
            and isinstance(home_dispatch, Mapping)
            and str(home_dispatch.get("request_id") or "")
            == str(maintenance.get("request_id") or "")
            and str(home_dispatch.get("control") or "")
            == HomeBattleControl.NEW_BATTLE.value
        ):
            return (
                "emulator_maintenance",
                "emulator:" + str(maintenance.get("request_id") or "unknown"),
            )
        workflow = getattr(self._supervisor, "battle_workflow", None)
        manual = getattr(self._supervisor, "manual_control", None)
        validation_request_id = str(
            getattr(
                self,
                "_exclusive_validation_battle_dispatch_hold",
                None,
            )
            or ""
        )
        workflow_recovery_key = (
            self._battle_workflow_recovery_key(workflow)
            if isinstance(workflow, Mapping)
            else ""
        )
        composite_persistence_retry = bool(
            validation_request_id
            and workflow_recovery_key
            and int(
                self._free_ticket_recovery_attempt_map().get(
                    workflow_recovery_key,
                    0,
                )
            )
            >= 1
        )
        if (
            isinstance(manual, Mapping)
            and manual.get("status") not in MANUAL_CONTROL_TERMINAL_STATUSES
        ):
            return None
        if (
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "start_battle"
            and workflow.get("status") == "action_dispatched"
            and (
                str(workflow.get("request_id") or "")
                not in getattr(self, "_uncertain_lifecycle_actions", set())
                or composite_persistence_retry
            )
        ):
            return "battle_workflow", workflow_recovery_key
        if validation_request_id:
            owner = (
                "exclusive_validation",
                f"exclusive-validation:{validation_request_id}",
            )
            if self._free_ticket_recovery_owner_current(*owner):
                return owner
        launch_request_id = str(
            getattr(
                self,
                "_exclusive_validation_launch_dispatch_hold",
                None,
            )
            or ""
        )
        if launch_request_id:
            owner = (
                "exclusive_validation_launch",
                f"exclusive-validation-launch:{launch_request_id}",
            )
            if self._free_ticket_recovery_owner_current(*owner):
                return owner
        claim = getattr(self, "_terminal_home_continuation", None)
        if (
            isinstance(claim, Mapping)
            and str(claim.get("phase") or "") == "action_dispatched"
            and self._terminal_home_continuation_owner_current()
        ):
            return (
                "terminal_continuation",
                self._terminal_home_continuation_recovery_key(claim),
            )
        return None

    def _free_ticket_recovery_owner_current(
        self,
        owner_kind: str,
        recovery_key: str,
    ) -> bool:
        if owner_kind == "emulator_maintenance":
            maintenance = getattr(
                self._supervisor,
                "emulator_maintenance",
                None,
            )
            home_dispatch = getattr(
                self,
                "_emulator_recovery_home_dispatch",
                None,
            )
            return bool(
                isinstance(maintenance, Mapping)
                and maintenance.get("state") == "host_restarted"
                and recovery_key
                == "emulator:"
                + str(maintenance.get("request_id") or "unknown")
                and getattr(self, "_emulator_maintenance_hold_active", False)
                and getattr(self, "_emulator_recovery_force_new_battle", False)
                and isinstance(home_dispatch, Mapping)
                and str(home_dispatch.get("request_id") or "")
                == str(maintenance.get("request_id") or "")
                and str(home_dispatch.get("control") or "")
                == HomeBattleControl.NEW_BATTLE.value
            )
        if owner_kind == "terminal_continuation":
            claim = getattr(self, "_terminal_home_continuation", None)
            return bool(
                isinstance(claim, Mapping)
                and str(claim.get("phase") or "") == "action_dispatched"
                and self._terminal_home_continuation_recovery_key(claim)
                == recovery_key
                and self._terminal_home_continuation_owner_current()
            )
        if owner_kind == "exclusive_validation":
            request_id = str(
                getattr(
                    self,
                    "_exclusive_validation_battle_dispatch_hold",
                    None,
                )
                or ""
            )
            receipt = (
                self._supervisor.exclusive_validation_receipt(
                    request_id=request_id
                )
                if request_id
                else None
            )
            return bool(
                request_id
                and recovery_key == f"exclusive-validation:{request_id}"
                and isinstance(receipt, Mapping)
                and str(receipt.get("status") or "") == "claimed"
                and request_id
                == str(
                    getattr(
                        self,
                        "_active_exclusive_validation_request_id",
                        None,
                    )
                    or ""
                )
                and self._supervisor.owns_exclusive_validation(
                    request_id,
                    statuses=("claimed",),
                )
            )
        if owner_kind == "exclusive_validation_launch":
            request_id = str(
                getattr(
                    self,
                    "_exclusive_validation_launch_dispatch_hold",
                    None,
                )
                or ""
            )
            receipt = (
                self._supervisor.exclusive_validation_receipt(
                    request_id=request_id
                )
                if request_id
                else None
            )
            launch = receipt.get("launch") if receipt else None
            return bool(
                request_id
                and recovery_key
                == f"exclusive-validation-launch:{request_id}"
                and isinstance(launch, Mapping)
                and str(launch.get("status") or "") == "claimed"
                and request_id
                == str(
                    getattr(
                        self,
                        "_active_exclusive_validation_launch_request_id",
                        None,
                    )
                    or ""
                )
                and self._supervisor.owns_exclusive_validation_launch(
                    request_id
                )
            )
        if owner_kind != "battle_workflow":
            return False
        workflow = getattr(self._supervisor, "battle_workflow", None)
        manual = getattr(self._supervisor, "manual_control", None)
        if not (
            isinstance(workflow, Mapping)
            and self._battle_workflow_recovery_key(workflow) == recovery_key
            and workflow.get("intent") == "start_battle"
            and workflow.get("status") == "action_dispatched"
            and not (
                isinstance(manual, Mapping)
                and manual.get("status") not in MANUAL_CONTROL_TERMINAL_STATUSES
            )
        ):
            return False
        validation_request_id = str(
            getattr(
                self,
                "_exclusive_validation_battle_dispatch_hold",
                None,
            )
            or ""
        )
        if (
            validation_request_id
            and int(
                self._free_ticket_recovery_attempt_map().get(
                    recovery_key,
                    0,
                )
            )
            >= 1
        ):
            # The one physical Claim budget is already consumed. A safety
            # Pause necessarily changes the control request identity, so the
            # original workflow acknowledgement can no longer authorize new
            # input. It can still identify this reporting-only retry when the
            # linked validation receipt remains the exact claimed owner.
            return self._free_ticket_recovery_owner_current(
                "exclusive_validation",
                f"exclusive-validation:{validation_request_id}",
            )
        current = self._current_control_workflow_evidence()
        if not isinstance(current, Mapping):
            return False
        if self._workflow_dispatch_receipt_mismatch(workflow, current) is not None:
            return False
        if validation_request_id:
            return self._free_ticket_recovery_owner_current(
                "exclusive_validation",
                f"exclusive-validation:{validation_request_id}",
            )
        return True

    @staticmethod
    def _free_ticket_recovery_action_owner(
        owner_kind: str,
    ) -> AuthorityHold:
        if owner_kind == "emulator_maintenance":
            return AuthorityHold.EMULATOR_MAINTENANCE
        if owner_kind in {
            "exclusive_validation",
            "exclusive_validation_launch",
        }:
            return AuthorityHold.EXCLUSIVE_VALIDATION
        if owner_kind == "battle_workflow":
            return AuthorityHold.OPERATOR_WORKFLOW
        if owner_kind == "terminal_continuation":
            return AuthorityHold.OPERATOR_WORKFLOW
        return AuthorityHold.BLOCKING_MODAL_RECOVERY

    def _fail_free_ticket_recovery(
        self,
        owner_kind: str,
        recovery_key: str,
        reason: str,
    ) -> None:
        if owner_kind == "emulator_maintenance":
            self._supervisor.pause_for_catastrophic_failure(
                RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                reason=(
                    "the replacement-battle launch reached a Free Ticket "
                    f"blocker that could not be restored: {reason}"
                ),
            )
            return
        if owner_kind == "terminal_continuation":
            self._clear_terminal_home_continuation(reason)
            return
        if owner_kind == "exclusive_validation":
            request_id = recovery_key.removeprefix(
                "exclusive-validation:"
            )
            receipt = self._supervisor.exclusive_validation_receipt(
                request_id=request_id
            )
            if (
                isinstance(receipt, Mapping)
                and str(receipt.get("status") or "") == "claimed"
            ):
                result = self._finish_exclusive_validation_without_cleanup(
                    receipt,
                    reason,
                )
                if result is not None:
                    self._retain_exclusive_validation_passive_battle(
                        request_id
                    )
            return
        if owner_kind == "exclusive_validation_launch":
            request_id = recovery_key.removeprefix(
                "exclusive-validation-launch:"
            )
            result = self._supervisor.finish_exclusive_validation_launch(
                request_id,
                outcome="failed",
                reason=reason,
            )
            if result is not None:
                self._active_exclusive_validation_launch_request_id = None
                self._exclusive_validation_launch_dispatch_hold = None
                self._clear_exclusive_validation_launch_start_proof(
                    request_id
                )
                self._announce_exclusive_validation_launch(result)
                self._retain_exclusive_validation_passive_battle(request_id)
            return
        workflow = getattr(self._supervisor, "battle_workflow", None)
        if not (
            isinstance(workflow, Mapping)
            and self._battle_workflow_recovery_key(workflow) == recovery_key
            and workflow.get("status") == "action_dispatched"
        ):
            return
        validation_request_id = str(
            getattr(
                self,
                "_exclusive_validation_battle_dispatch_hold",
                None,
            )
            or ""
        )
        validation_receipt = None
        if validation_request_id:
            validation_key = (
                f"exclusive-validation:{validation_request_id}"
            )
            # An explicit Start workflow and its validation receipt authorize
            # one physical Battle/Claim sequence, not two retry budgets. Mark
            # the receipt alias consumed before either durable terminal write;
            # if one write is transiently unavailable, a later heartbeat may
            # retry persistence but can never replay Claim under the alias.
            attempts = self._free_ticket_recovery_attempt_map()
            attempts[validation_key] = max(
                1,
                int(attempts.get(validation_key, 0)),
            )
            validation_receipt = self._supervisor.exclusive_validation_receipt(
                request_id=validation_request_id
            )
        request_id = str(workflow.get("request_id") or "")
        self._mission_mgr.revoke_initial_battle_intent(
            str(workflow.get("intent") or ""),
            request_id=request_id,
        )
        transitioned = self._supervisor.transition_battle_workflow(
            request_id,
            "failed",
            reason=reason,
            acknowledgement=(self._current_control_workflow_evidence() or {}),
        )
        if transitioned is None:
            # Keep the still-claimed validation receipt intact so the same
            # composite owner can retry only this durable workflow write on a
            # later heartbeat. Its shared attempt budget already forbids a
            # second Claim input.
            return
        if (
            isinstance(validation_receipt, Mapping)
            and str(validation_receipt.get("status") or "") == "claimed"
        ):
            result = self._finish_exclusive_validation_without_cleanup(
                validation_receipt,
                reason,
            )
            if result is not None:
                self._retain_exclusive_validation_passive_battle(
                    validation_request_id
                )

    def _advance_blocking_primary_recovery(
        self,
        screenshot: Frame,
        detection: Mapping[str, Any],
    ) -> bool:
        """Suppress a known blocker and recover only its exact launch owner."""

        if str(detection.get("state") or "").upper() != "FREE_TICKET":
            return False
        self._blocking_primary_hold_active = True
        owner = self._free_ticket_recovery_owner()
        holds, handoff_ready = self._blocking_recovery_handoff(owner)
        self._update_action_authority(detection=detection, holds=holds)
        self._publish_action_authority()
        if stop_blind_gem_tapper():
            self._blind_tapper_suspended = True

        if owner is None or not handoff_ready:
            warning_key = "unowned_free_ticket"
            if warning_key not in self._free_ticket_recovery_warning_set():
                self._free_ticket_recovery_warning_set().add(warning_key)
                log(
                    "[FREE_TICKET_RECOVERY] Blocking modal recognized, but no "
                    "exact dispatched battle transition owns recovery; all "
                    "background input remains suppressed",
                    "WARN",
                    console=True,
                )
            return True
        owner_kind, recovery_key = owner
        recovery_action_owner = self._free_ticket_recovery_action_owner(
            owner_kind
        )
        attempts = self._free_ticket_recovery_attempt_map()
        if int(attempts.get(recovery_key, 0)) >= 1:
            reason = (
                "the Free Ticket modal recurred after its single bounded "
                "recovery transaction"
            )
            self._fail_free_ticket_recovery(owner_kind, recovery_key, reason)
            if recovery_key not in self._free_ticket_recovery_warning_set():
                self._free_ticket_recovery_warning_set().add(recovery_key)
                log(f"[FREE_TICKET_RECOVERY] {reason}", "WARN", console=True)
            return True
        if getattr(self._supervisor, "is_paused", False):
            return True

        def action_allowed() -> bool:
            if not self._runtime_action_guard(
                action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                owner=recovery_action_owner,
            ):
                return False
            refreshed_owner = self._free_ticket_recovery_owner()
            refreshed_holds, refreshed_ready = (
                self._blocking_recovery_handoff(refreshed_owner)
            )
            self._update_action_authority(holds=refreshed_holds)
            self._publish_action_authority()
            return bool(
                refreshed_ready
                and refreshed_owner == (owner_kind, recovery_key)
            )

        result = handle_free_ticket_modal(
            screenshot,
            action_guard_fn=action_allowed,
            capture_fn=self._capture_frame,
            operation_id=f"{recovery_key}:free-ticket-recovery",
        )
        if result.input_dispatched:
            attempts[recovery_key] = int(attempts.get(recovery_key, 0)) + 1
        if result.status is FreeTicketRecoveryStatus.DISMISSED:
            self._free_ticket_recovery_cleared_set().add(recovery_key)
            if owner_kind == "terminal_continuation":
                self._mark_terminal_home_continuation_modal_cleared()
            elif owner_kind == "emulator_maintenance":
                home_dispatch = getattr(
                    self,
                    "_emulator_recovery_home_dispatch",
                    None,
                )
                if isinstance(home_dispatch, dict):
                    home_dispatch["modal_recovery_completed"] = True
                    home_dispatch.pop(
                        "retry_home_candidate_observation_id",
                        None,
                    )
        elif result.status is FreeTicketRecoveryStatus.ALREADY_RESOLVED:
            if (
                result.final_state == "RUNNING"
                and owner_kind == "emulator_maintenance"
            ):
                self._emulator_recovery_home_dispatch = None
            elif result.final_state != "RUNNING":
                self._fail_free_ticket_recovery(
                    owner_kind,
                    recovery_key,
                    "the Free Ticket modal changed without an owned recovery "
                    "input; no launch retry was inferred",
                )
        elif result.status is FreeTicketRecoveryStatus.DEFERRED:
            # No input was attempted. Preserve the exact launch owner and let
            # a later complete frame retry the same bounded transaction.
            pass
        elif result.status is FreeTicketRecoveryStatus.UNCERTAIN:
            self._fail_free_ticket_recovery(
                owner_kind,
                recovery_key,
                "the Free Ticket Claim input had no authoritative outcome; "
                "the same action is non-replayable",
            )
            uncertain = getattr(self, "_uncertain_lifecycle_actions", None)
            if uncertain is None:
                uncertain = set()
                self._uncertain_lifecycle_actions = uncertain
            uncertain.add(recovery_key)
            if owner_kind == "battle_workflow":
                workflow = getattr(self._supervisor, "battle_workflow", None)
                if isinstance(workflow, Mapping):
                    uncertain.add(str(workflow.get("request_id") or ""))
            self._runtime_uncertain_mutation_result(
                "the Free Ticket Claim input may have reached the device, but "
                "no authoritative result was observed; automation is Paused "
                "and the action will not be replayed"
            )
        elif result.status is FreeTicketRecoveryStatus.FAILED or (
            result.status is FreeTicketRecoveryStatus.INTERRUPTED
            and result.input_dispatched
        ):
            self._fail_free_ticket_recovery(
                owner_kind,
                recovery_key,
                "Free Ticket recovery did not prove that the blocking modal cleared",
            )
        # The supplied frame predates every recovery observation and possible
        # input. Always recapture through the main loop before any other work.
        return True

    def _workflow_evidence_matches_runtime(
        self,
        requested: Mapping[str, Any],
        current: Mapping[str, Any],
        *,
        intent: str,
        allow_new_run_scope: bool = False,
        allowed_activity_scope_transition: Optional[Tuple[str, str]] = None,
    ) -> tuple[bool, str]:
        """Revalidate target/session/battle evidence without changing intent."""

        for field in ("runtime_id", "pid", "adb_target"):
            if requested.get(field) != current.get(field):
                return False, f"runtime evidence changed at {field}"
        requested_generation = requested.get("target_generation")
        current_generation = current.get("target_generation")
        if (
            requested_generation is not None
            and current_generation != requested_generation
        ):
            return False, "ADB target generation changed"
        scope_changed = requested.get("activity_scope_run_id") != current.get(
            "activity_scope_run_id"
        )
        allowed_source_scope = ""
        allowed_target_scope = ""
        if (
            isinstance(allowed_activity_scope_transition, tuple)
            and len(allowed_activity_scope_transition) == 2
        ):
            allowed_source_scope = str(
                allowed_activity_scope_transition[0] or ""
            ).strip()
            allowed_target_scope = str(
                allowed_activity_scope_transition[1] or ""
            ).strip()
        attachment_scope_transition = bool(
            intent == "attach_battle"
            and current.get("game_state") == "active_battle"
            and allowed_source_scope
            and allowed_target_scope
            and requested.get("activity_scope_run_id") == allowed_source_scope
            and current.get("activity_scope_run_id") == allowed_target_scope
        )
        if scope_changed and not (
            (
                allow_new_run_scope
                and intent == "start_battle"
                and current.get("game_state") == "home_new_battle"
            )
            or attachment_scope_transition
        ):
            return False, "battle activity scope changed"
        if not intent_matches_evidence(intent, current):
            return (
                False,
                f"{intent} no longer matches {current.get('game_state')}",
            )
        return True, "fresh runtime evidence still matches the explicit intent"

    @staticmethod
    def _repair_authority_matches_runtime(
        authorized: object,
        current: object,
    ) -> bool:
        """Match a one-shot repair grant to the same active battle owner."""

        if not isinstance(authorized, Mapping) or not isinstance(
            current,
            Mapping,
        ):
            return False
        return bool(
            authorized.get("game_state") == "active_battle"
            and current.get("game_state") == "active_battle"
            and all(
                authorized.get(field) == current.get(field)
                for field in (
                    "runtime_id",
                    "pid",
                    "adb_target",
                    "target_generation",
                    "activity_scope_run_id",
                )
            )
        )

    def _log_operator_workflow_result(
        self,
        operation_id: str,
        *,
        purpose: str,
        reason: str,
        result: str,
    ) -> None:
        """Emit exactly one ACTION/RESULT pair for a workflow acknowledgement."""

        completed = getattr(self, "_completed_operator_workflow_logs", None)
        if completed is None:
            completed = set()
            self._completed_operator_workflow_logs = completed
        if operation_id in completed:
            return
        self._log_operator_workflow_intent(
            operation_id,
            purpose=purpose,
            reason=reason,
        )
        log_result(
            result,
            operation_id=operation_id,
            console=True,
        )
        completed.add(operation_id)

    def _log_operator_workflow_intent(
        self,
        operation_id: str,
        *,
        purpose: str,
        reason: str,
    ) -> None:
        """Emit one correlated ACTION before a workflow can dispatch input."""

        logged = getattr(self, "_logged_operator_workflows", None)
        if logged is None:
            logged = set()
            self._logged_operator_workflows = logged
        if operation_id in logged:
            return
        log_action_intent(
            purpose,
            reason=reason,
            operation_id=operation_id,
        )
        logged.add(operation_id)

    def _terminalize_uncertain_battle_workflow(
        self,
        workflow: Mapping[str, Any],
        current: Mapping[str, Any],
        *,
        reason: str,
    ) -> None:
        """Make an accepted unresolved lifecycle input non-replayable."""

        request_id = str(workflow.get("request_id") or "")
        intent = str(workflow.get("intent") or "")
        if request_id:
            uncertain = getattr(self, "_uncertain_lifecycle_actions", None)
            if uncertain is None:
                uncertain = set()
                self._uncertain_lifecycle_actions = uncertain
            uncertain.add(request_id)
        self._mission_mgr.revoke_initial_battle_intent(
            intent,
            request_id=request_id,
        )
        self._supervisor.transition_battle_workflow(
            request_id,
            "failed",
            reason=reason,
            acknowledgement=current,
        )
        self._runtime_uncertain_mutation_result(
            reason + "; automation is Paused and the action will not be replayed"
        )
        log(
            f"[BATTLE_WORKFLOW] {reason}; accepted input is non-replayable",
            "ERROR",
            console=True,
        )

    def _reconcile_dispatched_battle_workflow(
        self,
        workflow: Mapping[str, Any],
        current: Mapping[str, Any],
        *,
        detection: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Resolve a dispatched Home action without competing for input."""

        request_id = str(workflow.get("request_id") or "")
        intent = str(workflow.get("intent") or "")
        mismatch = self._workflow_dispatch_receipt_mismatch(
            workflow,
            current,
        )
        game_state = str(current.get("game_state") or "unknown")
        unexpected_tournament = bool(
            game_state == "active_battle"
            and self._tournament_battle_guard(detection or {})
        )
        if (
            mismatch is None
            and unexpected_tournament
        ):
            mismatch = (
                "the ordinary battle launch reached a Tournament battle; "
                "manual Tournament activity cannot satisfy this workflow"
            )
        if mismatch is None and game_state == "active_battle":
            return
        if (
            mismatch is None
            and intent == "start_battle"
            and str((detection or {}).get("state") or "").upper()
            == "FREE_TICKET"
        ):
            # A known blocker has its own exact-owner recovery path. Its time
            # on screen cannot consume the ordinary launch postcondition
            # timeout before that path gets the current frame.
            return
        expected_home = (
            "home_new_battle"
            if intent == "start_battle"
            else "home_resume_battle"
        )
        recovery_key = self._battle_workflow_recovery_key(workflow)
        if (
            mismatch is None
            and intent == "start_battle"
            and game_state == expected_home
            and recovery_key in self._free_ticket_recovery_cleared_set()
        ):
            observation_id = str(current.get("observation_id") or "")
            candidates = getattr(
                self,
                "_free_ticket_retry_home_candidates",
                None,
            )
            if candidates is None:
                candidates = {}
                self._free_ticket_retry_home_candidates = candidates
            prior_observation_id = candidates.get(recovery_key)
            if not prior_observation_id:
                candidates[recovery_key] = observation_id
                return
            if prior_observation_id == observation_id:
                return
            transitioned = self._supervisor.transition_battle_workflow(
                request_id,
                "ready",
                reason=(
                    "the recognized Free Ticket modal cleared; one verified "
                    "New Battle retry is ready on fresh Home evidence"
                ),
                acknowledgement=current,
            )
            if transitioned is None:
                candidates.pop(recovery_key, None)
            return
        if game_state != expected_home:
            getattr(
                self,
                "_free_ticket_retry_home_candidates",
                {},
            ).pop(recovery_key, None)
        if (
            mismatch is None
            and game_state not in {expected_home, "unknown"}
        ):
            mismatch = (
                f"dispatched {intent} reached unexpected {game_state} "
                "before battle adoption"
            )

        terminal_status = "interrupted"
        reason = mismatch
        if reason is None:
            try:
                dispatched_at = datetime.fromisoformat(
                    str(workflow.get("updated_at") or "")
                )
                observed_at = datetime.fromisoformat(
                    str(current.get("observed_at") or "")
                )
                elapsed = max(
                    0.0,
                    (observed_at - dispatched_at).total_seconds(),
                )
            except (TypeError, ValueError):
                elapsed = 0.0
            if elapsed < BATTLE_ACTION_DISPATCH_TIMEOUT_SECONDS:
                return
            reason = (
                f"dispatched {intent} had no authoritative outcome within "
                f"{int(BATTLE_ACTION_DISPATCH_TIMEOUT_SECONDS)} seconds"
            )

            self._terminalize_uncertain_battle_workflow(
                workflow,
                current,
                reason=reason,
            )
            return

        self._mission_mgr.revoke_initial_battle_intent(
            intent,
            request_id=request_id,
        )
        self._supervisor.transition_battle_workflow(
            request_id,
            terminal_status,
            reason=reason,
            acknowledgement=current,
        )
        log(
            f"[BATTLE_WORKFLOW] {reason}; automated input remains yielded",
            "WARN",
            console=True,
        )
        if unexpected_tournament:
            yielded = self._supervisor.yield_to_unexpected_manual_activity(
                current
            )
            if yielded is None and not getattr(
                self._supervisor,
                "unexpected_manual_yield_emergency",
                False,
            ):
                self._supervisor.pause_for_catastrophic_failure(
                    RuntimeFailureKind.CONTROL_AUTHORITY_LOST,
                    reason=(
                        "Tournament activity superseded an ordinary Battle "
                        "workflow, but manual ownership could not be persisted"
                    ),
                )
            self._update_action_authority()
            self._publish_action_authority()

    def _reconcile_dispatched_workflow_behind_blocker(
        self,
        detection: Mapping[str, Any],
    ) -> None:
        """Revoke a stale receipt before a blocker can retain its hold."""

        workflow = getattr(self._supervisor, "battle_workflow", None)
        if not (
            isinstance(workflow, Mapping)
            and workflow.get("status") == "action_dispatched"
        ):
            return
        current = self._current_control_workflow_evidence()
        if not isinstance(current, Mapping):
            return
        self._reconcile_dispatched_battle_workflow(
            workflow,
            current,
            detection=detection,
        )

    def _sync_operator_control_workflows(
        self,
        detection: Mapping[str, Any],
        frame: Optional[Frame] = None,
    ) -> None:
        """Acknowledge and revalidate Better Control Model directives."""

        malformed_authority = next(
            (
                label
                for label, present in (
                    (
                        "interactive-development-lease",
                        getattr(
                            self._supervisor,
                            "interactive_development_lease_error",
                            False,
                        )
                        is True,
                    ),
                    (
                        "manual-control",
                        self._supervisor.manual_control_error is True,
                    ),
                    (
                        "battle-workflow",
                        self._supervisor.battle_workflow_error is True,
                    ),
                    (
                        "setup-capture",
                        self._supervisor.setup_capture_error is True,
                    ),
                )
                if present
            ),
            None,
        )
        if malformed_authority is not None:
            if self._supervisor.control_state not in {"PAUSED", "STOPPED"}:
                self._supervisor.pause_for_catastrophic_failure(
                    RuntimeFailureKind.CONTROL_AUTHORITY_LOST,
                    reason=(
                        f"malformed {malformed_authority} directive made "
                        "device-input ownership unknowable"
                    ),
                )
            return
        if self._sync_setup_capture(frame):
            return
        current = self._current_control_workflow_evidence()
        manual = self._supervisor.manual_control
        if manual is not None and manual.get("status") not in (
            MANUAL_CONTROL_TERMINAL_STATUSES
        ):
            manual_id = str(manual.get("manual_control_id") or "")
            return_claim = self._pending_return_reconciliation_claims().get(
                manual_id
            )
            if (
                isinstance(return_claim, Mapping)
                and return_claim.get("semantic_completion_applied") is True
            ):
                self._retry_pending_return_completion_report(
                    manual,
                    return_claim,
                )
                return
            terminal_claim = self._manual_terminal_claims().get(manual_id)
            if (
                isinstance(terminal_claim, Mapping)
                and terminal_claim.get("semantic_completion_applied") is True
                and isinstance(
                    terminal_claim.get("pending_completion"),
                    Mapping,
                )
            ):
                self._retry_pending_manual_terminal_completion(
                    manual,
                    current,
                )
                return
            retried_terminal = self._retry_pending_manual_terminal_completion(
                manual,
                current,
            )
            if retried_terminal is not None:
                return
            terminal_evidence = manual.get("terminal_evidence")
            initial_terminal_observation = not isinstance(
                terminal_evidence,
                Mapping,
            )
            retry_terminal_observation = bool(
                isinstance(terminal_evidence, Mapping)
                and terminal_evidence.get("status") == "unavailable"
                and manual.get("status") == "return_requested"
                and time.monotonic()
                >= float(
                    getattr(self, "_manual_terminal_retry_at", 0.0) or 0.0
                )
            )
            if (
                current is not None
                and current.get("game_state") == "game_over"
                and (
                    initial_terminal_observation
                    or retry_terminal_observation
                )
            ):
                if retry_terminal_observation:
                    self._manual_terminal_retry_at = time.monotonic() + 5.0
                recorded = self._observe_manual_terminal(
                    manual,
                    current,
                )
                if recorded is not None:
                    manual = recorded
            status = str(manual.get("status") or "")
            starting = manual.get("starting_evidence")
            if current is not None and isinstance(starting, Mapping):
                owner_change = None
                for field in ("runtime_id", "pid", "adb_target"):
                    if starting.get(field) != current.get(field):
                        owner_change = f"runtime evidence changed at {field}"
                        break
                if (
                    owner_change is None
                    and starting.get("target_generation") is not None
                    and starting.get("target_generation")
                    != current.get("target_generation")
                ):
                    owner_change = "ADB target generation changed"
                if owner_change is not None:
                    failure_kind = (
                        RuntimeFailureKind.TARGET_OWNERSHIP_LOST
                        if owner_change in {
                            "runtime evidence changed at adb_target",
                            "ADB target generation changed",
                        }
                        else RuntimeFailureKind.CONTROL_AUTHORITY_LOST
                    )
                    if (
                        not self._supervisor.is_paused
                        and not self._supervisor.pause_for_catastrophic_failure(
                            failure_kind,
                            reason=owner_change,
                        )
                    ):
                        return
                    self._supervisor.transition_manual_control(
                        manual_id,
                        "interrupted",
                        detail=(
                            "manual-control handoff cannot cross a runtime or "
                            f"target boundary: {owner_change}"
                        ),
                        refresh_status="owner_boundary_changed",
                    )
                    log(
                        "[MANUAL_CONTROL] Interrupted stale handoff after "
                        f"{owner_change}; Automation remains Paused",
                        "WARN",
                        console=True,
                    )
                    return
            if status == "pause_requested" and self._supervisor.is_paused:
                acknowledgement = current or {
                    "status": "observation_unavailable",
                }
                transitioned = self._supervisor.transition_manual_control(
                    manual_id,
                    "active",
                    detail="runtime acknowledged the indefinite Pause",
                    refresh_status="observation_continues_while_paused",
                    pause_acknowledgement=acknowledgement,
                )
                if transitioned is not None:
                    self._log_operator_workflow_result(
                        manual_id,
                        purpose="Taking manual control",
                        reason="obtain an acknowledged indefinite Pause before yielding input",
                        result="Manual control active — automated device input is blocked",
                    )
                manual = transitioned or manual
                status = str(manual.get("status") or "")
            if status == "return_requested" and self._supervisor.is_paused:
                if current is None:
                    return
                terminal_evidence = manual.get("terminal_evidence")
                terminal_evidence_unavailable = bool(
                    isinstance(terminal_evidence, Mapping)
                    and terminal_evidence.get("status") == "unavailable"
                )
                if (
                    current.get("game_state") == "game_over"
                    and (
                        not isinstance(terminal_evidence, Mapping)
                        or terminal_evidence_unavailable
                    )
                ):
                    return
                if (
                    terminal_evidence_unavailable
                    and current.get("game_state") != "home_new_battle"
                ):
                    return
                configuration = {
                    "schema_version": 1,
                    "starting_game_state": (
                        starting.get("game_state")
                        if isinstance(starting, Mapping)
                        else "unknown"
                    ),
                    "observed_game_state": current.get("game_state"),
                    "battle_scope_preserved": bool(
                        isinstance(starting, Mapping)
                        and starting.get("activity_scope_run_id")
                        == current.get("activity_scope_run_id")
                    ),
                }
                self._supervisor.transition_manual_control(
                    manual_id,
                    "awaiting_enable",
                    detail=(
                        "fresh passive observation recorded; explicit Enable "
                        "is required before reconciliation"
                    ),
                    refresh_status="save_validation_pending",
                    configuration=configuration,
                )
                return
            if status == "awaiting_enable" and not self._supervisor.is_paused:
                if current is None:
                    log(
                        "[MANUAL_CONTROL] Return Control is waiting for a fresh "
                        "runtime observation before save refresh; all ordinary "
                        "input remains held",
                        "WARN",
                    )
                    return
                terminal_evidence = manual.get("terminal_evidence")
                if (
                    isinstance(terminal_evidence, Mapping)
                    and terminal_evidence.get("status") == "unavailable"
                    and current.get("game_state") != "home_new_battle"
                ):
                    log(
                        "[MANUAL_CONTROL] Return Control retained its hold "
                        "because unavailable terminal evidence may resume "
                        "only from a fresh exact Home New Battle boundary",
                        "WARN",
                    )
                    return
                transitioned = self._supervisor.transition_manual_control(
                    manual_id,
                    "reconciling",
                    detail=(
                        "Enable acknowledged; fresh save and configuration "
                        "reconciliation owns the next action boundary"
                    ),
                    refresh_status="save_refresh_pending",
                )
                if transitioned is not None:
                    scope_id = str(
                        current.get("activity_scope_run_id") or ""
                    )
                    continuity = getattr(self, "_activity_continuity", None)
                    if continuity is not None and scope_id:
                        continuity.request_running_reconciliation(scope_id)
                    self._log_operator_workflow_result(
                        manual_id,
                        purpose="Returning automation control",
                        reason="refresh observation and save before ordinary input resumes",
                        result="Return Control reconciliation started",
                    )
            if (
                status
                in {"awaiting_configuration", "awaiting_manual_correction"}
                and not self._supervisor.is_paused
                and manual.get("refresh_status")
                == "configuration_retry_after_enable"
            ):
                # A retry crosses another authority boundary, so the previous
                # typed bundle is discarded and a fresh serialization is
                # required.  The explicit Enable also authorizes read-only
                # validation of a previously reported trusted mismatch.
                self._pending_return_reconciliation_claims().pop(
                    manual_id,
                    None,
                )
                self._mission_mgr.finish_manual_return_reconciliation()
                self._manual_return_configuration_authorized_id = manual_id
                transitioned = self._supervisor.transition_manual_control(
                    manual_id,
                    "reconciling",
                    detail=(
                        "configuration retry explicitly enabled; a new save "
                        "serialization is required before any UI fallback"
                    ),
                    refresh_status="save_refresh_pending",
                )
                if transitioned is not None:
                    scope_id = str(
                        current.get("activity_scope_run_id") or ""
                    ) if current is not None else ""
                    continuity = getattr(self, "_activity_continuity", None)
                    if (
                        continuity is not None
                        and scope_id
                        and current is not None
                        and current.get("game_state") == "active_battle"
                    ):
                        continuity.request_running_reconciliation(scope_id)
                return
            if (
                status == "reconciling"
                and current is not None
                and manual_id
                in self._pending_return_reconciliation_claims()
            ):
                self._retry_pending_running_return(manual, current)
                return

        workflow = self._supervisor.battle_workflow
        if workflow is None:
            return
        if workflow.get("status") in {
            "rejected",
            "interrupted",
            "failed",
            "cancelled",
        }:
            intent = str(workflow.get("intent") or "")
            if intent:
                self._mission_mgr.revoke_initial_battle_intent(
                    intent,
                    request_id=str(workflow.get("request_id") or ""),
                )
            return
        if workflow.get("status") == "completed":
            return
        request_id = str(workflow.get("request_id") or "")
        intent = str(workflow.get("intent") or "")
        status = str(workflow.get("status") or "")
        if status == "action_dispatched":
            if current is not None:
                self._reconcile_dispatched_battle_workflow(
                    workflow,
                    current,
                    detection=detection,
                )
            return
        if status == "ready" and intent == "attach_battle":
            retained_claim = self._running_reconciliation_claims().get(
                request_id
            )
            if (
                isinstance(retained_claim, Mapping)
                and retained_claim.get("semantic_completion_applied") is True
            ):
                # Local adoption already released action authority. Retrying
                # the exact durable report must run before terminal/current
                # evidence can invalidate the active-battle reconciliation
                # inputs that originally authorized that adoption.
                self._retry_attachment_completion_report(
                    workflow,
                    retained_claim,
                )
                return
            if current is None:
                return
            claim = self._matching_running_reconciliation_claim(
                workflow,
                current,
            )
            if claim is None:
                self._interrupt_unbacked_ready_attachment(workflow, current)
                return
            requested = workflow.get("evidence")
            if isinstance(requested, Mapping):
                temporal = claim.get("temporal_binding")
                allowed_scope_transition = (
                    (
                        temporal.source_activity_scope_id,
                        temporal.activity_scope_id,
                    )
                    if isinstance(temporal, RunningAttachmentTemporalBinding)
                    and temporal.activity_scope_id is not None
                    else None
                )
                matches, reason = self._workflow_evidence_matches_runtime(
                    requested,
                    current,
                    intent=intent,
                    allowed_activity_scope_transition=(
                        allowed_scope_transition
                    ),
                )
                if matches:
                    if self._mission_mgr.active_battle_observed():
                        return
                    raw_decision = claim.get("attachment_strategy")
                    decision = (
                        dict(raw_decision)
                        if isinstance(raw_decision, Mapping)
                        else self._attachment_strategy_decision(
                            workflow,
                            acquisition=claim.get("acquisition"),
                        )
                    )
                    decision = self._resolve_ready_attachment_strategy(
                        decision,
                        detection,
                        frame,
                    )
                    retained_claim = self._running_reconciliation_claims().get(
                        request_id
                    )
                    if isinstance(retained_claim, dict):
                        retained_claim["attachment_strategy"] = decision
                    selected_strategy = decision.get("_strategy")
                    observation_only = bool(
                        decision.get("attachment_mode") != "strategy"
                        or selected_strategy is None
                    )
                    authorized = self._mission_mgr.authorize_initial_battle_intent(
                        intent,
                        request_id=request_id,
                        observation_only=observation_only,
                        strategy=(
                            selected_strategy
                            if not observation_only
                            else None
                        ),
                    )
                    if not authorized:
                        self._supervisor.transition_battle_workflow(
                            request_id,
                            "interrupted",
                            reason=(
                                "a different initial workflow already owns "
                                "this process"
                            ),
                            acknowledgement=current,
                        )
                    elif observation_only:
                        self._begin_no_strategy_observation_boundary(
                            request_id,
                            observations=decision.get("_observations"),
                        )
                    else:
                        self._end_no_strategy_observation_boundary()
                else:
                    self._mission_mgr.revoke_initial_battle_intent(
                        intent,
                        request_id=request_id,
                    )
                    self._supervisor.transition_battle_workflow(
                        request_id,
                        "interrupted",
                        reason=reason,
                        acknowledgement=current,
                    )
            else:
                self._mission_mgr.revoke_initial_battle_intent(
                    intent,
                    request_id=request_id,
                )
                self._supervisor.transition_battle_workflow(
                    request_id,
                    "interrupted",
                    reason="attachment request evidence is unavailable",
                    acknowledgement=current,
                )
            return
        if status not in {"requested", "awaiting_enable", "acknowledged"}:
            return
        requested = workflow.get("evidence")
        if current is None or not isinstance(requested, Mapping):
            return
        matches, reason = self._workflow_evidence_matches_runtime(
            requested,
            current,
            intent=intent,
            allow_new_run_scope=(
                status == "acknowledged" and intent == "start_battle"
            ),
        )
        if not matches:
            self._mission_mgr.revoke_initial_battle_intent(
                intent,
                request_id=request_id,
            )
            rejected = self._supervisor.transition_battle_workflow(
                request_id,
                "rejected" if status == "requested" else "interrupted",
                reason=reason,
                acknowledgement=current,
            )
            if rejected is not None:
                self._log_operator_workflow_result(
                    request_id,
                    purpose=(
                        "Starting a new battle"
                        if intent == "start_battle"
                        else "Attaching automation to a battle"
                    ),
                    reason="honor only an exact matching operator intent",
                    result=f"Workflow rejected — {reason}",
                )
            return
        if self._supervisor.is_paused:
            if status in {"requested", "acknowledged"}:
                acknowledged = self._supervisor.transition_battle_workflow(
                    request_id,
                    "awaiting_enable",
                    reason="intent matched; Automation remains Paused",
                    acknowledgement=current,
                )
            return
        if status == "acknowledged":
            return
        if intent == "attach_battle":
            validating = self._supervisor.transition_battle_workflow(
                request_id,
                "validating_save",
                reason=(
                    "exact attachment intent acknowledged; fresh-save "
                    "validation must complete before battle adoption"
                ),
                acknowledgement=current,
            )
            if validating is not None:
                self._log_operator_workflow_result(
                    request_id,
                    purpose="Attaching automation to a battle",
                    reason=(
                        "bind the exact operator intent before any "
                        "configuration fallback"
                    ),
                    result=(
                        "Attachment accepted — save validation remains pending"
                    ),
                )
            return
        if not self._mission_mgr.authorize_initial_battle_intent(
            intent,
            request_id=request_id,
        ):
            self._supervisor.transition_battle_workflow(
                request_id,
                "rejected",
                reason="a different initial workflow already owns this process",
                acknowledgement=current,
            )
            return
        self._supervisor.transition_battle_workflow(
            request_id,
            "acknowledged",
            reason=reason,
            acknowledgement=current,
        )

    @staticmethod
    def _setup_capture_workflow_binding(
        acquisition: PlayerSaveAcquisitionBundle,
        evidence: Mapping[str, Any],
    ) -> Tuple[
        Optional[dict[str, Any]],
        Optional[str],
        Optional[str],
    ]:
        """Bind a capture or classify why fresh save evidence cannot bind it."""

        snapshot = acquisition.snapshot
        if getattr(snapshot, "mapping_resolution", None) == (
            "semantic_forward_revision"
        ):
            return (
                None,
                "unavailable",
                "the forced-save capability grants metric observation only; "
                "no setup-capture workflow or UI fallback was opened",
            )
        runtime = getattr(snapshot, "runtime_save", None)
        game_state = str(evidence.get("game_state") or "")
        if runtime is None:
            resolution = str(
                getattr(snapshot, "mapping_resolution", None) or "unsupported"
            ).strip()
            mapping_id = str(getattr(snapshot, "mapping_id", None) or "").strip()
            if resolution == "incompatible_revision" or (
                mapping_id and getattr(snapshot, "shape_valid", None) is not True
            ):
                reason = (
                    "the forced-save evidence is structurally incompatible with "
                    "the available setup-capture mapping"
                )
            elif not mapping_id or resolution == "unsupported":
                reason = (
                    "the forced-save version is unsupported for setup capture"
                )
            else:
                reason = (
                    "the forced-save evidence has no usable runtime projection for "
                    "setup capture"
                )
            return None, "unavailable", f"{reason}; no UI fallback was opened"
        active_identity = getattr(runtime, "active_round_identity", None)
        round_active = getattr(runtime, "round_active", None)
        if game_state in {"active_battle", "home_resume_battle"}:
            fingerprint = str(
                getattr(active_identity, "fingerprint", None) or ""
            ).strip()
            if round_active is False:
                return (
                    None,
                    "failed",
                    "save round identity contradicts the requested active or "
                    "resumable battle boundary",
                )
            if round_active is not True or not fingerprint:
                return (
                    None,
                    "unavailable",
                    "the forced-save evidence did not prove an active battle identity; "
                    "no UI fallback was opened",
                )
        elif game_state == "home_new_battle":
            if round_active is True:
                return (
                    None,
                    "failed",
                    "save round identity contradicts the requested new-run Home "
                    "boundary",
                )
            if round_active is not False:
                return (
                    None,
                    "unavailable",
                    "the forced-save evidence did not prove an inactive round at the "
                    "new-run Home boundary; no UI fallback was opened",
                )
            fingerprint = None
        else:
            return (
                None,
                "unavailable",
                "the requested setup-capture game boundary is unavailable",
            )
        scope_id = str(evidence.get("activity_scope_run_id") or "").strip()
        if not scope_id or acquisition.binding is None:
            return (
                None,
                "unavailable",
                "the exact runtime scope or target binding is unavailable",
            )
        return (
            {
                "schema_version": 1,
                "game_state": game_state,
                "runtime_session_fingerprint": hashlib.sha256(
                    (
                        "thetower-setup-capture-runtime-v1\0"
                        f"{evidence.get('runtime_id')}"
                    ).encode("utf-8")
                ).hexdigest(),
                "activity_scope_fingerprint": hashlib.sha256(
                    f"thetower-setup-capture-scope-v1\0{scope_id}".encode("utf-8")
                ).hexdigest(),
                "target_generation_fingerprint": acquisition.binding.fingerprint,
                "active_round_identity_fingerprint": fingerprint,
            },
            None,
            None,
        )

    def _setup_capture_context_matches(
        self,
        expected: Mapping[str, Any],
        request_id: str,
    ) -> bool:
        capture = self._supervisor.setup_capture
        current = self._current_control_workflow_evidence()
        return bool(
            isinstance(capture, Mapping)
            and capture.get("request_id") == request_id
            and capture.get("status") == "capturing"
            and isinstance(current, Mapping)
            and all(
                current.get(field) == expected.get(field)
                for field in (
                    "runtime_id",
                    "pid",
                    "adb_target",
                    "target_generation",
                    "activity_scope_run_id",
                    "game_state",
                )
            )
        )

    def _retained_return_setup_capture_claim(
        self,
        capture: Mapping[str, Any],
        current: Mapping[str, Any],
    ) -> Optional[Dict[str, object]]:
        """Return the exact in-process forced save retained by Return Control."""

        if capture.get("acquisition_source") != (
            "retained_return_control_refresh"
        ):
            return None
        manual_id = str(
            capture.get("source_manual_control_id") or ""
        ).strip()
        manual = self._supervisor.manual_control
        if not (
            manual_id
            and isinstance(manual, Mapping)
            and manual.get("manual_control_id") == manual_id
            and manual.get("status") == "awaiting_configuration"
            and manual.get("refresh_status") == "trusted_mismatch_paused"
        ):
            return None
        claim = self._matching_pending_running_return_claim(manual, current)
        if not isinstance(claim, Mapping):
            return None
        receipt = claim.get("receipt")
        acquisition = claim.get("acquisition")
        if not (
            isinstance(receipt, Mapping)
            and receipt == manual.get("save_receipt")
            and isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.complete
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.FORCED_SERIALIZATION
        ):
            return None
        return dict(claim)

    def _setup_capture_source_matches(
        self,
        expected: Mapping[str, Any],
        initial_frame: Optional[Frame],
        *,
        stable: bool,
    ) -> bool:
        attempts = 2 if stable else 1
        frame = initial_frame
        expected_state = str(expected.get("game_state") or "")
        for attempt in range(attempts):
            if frame is None or attempt > 0:
                frame = self._capture_frame()
            if frame is None:
                return False
            try:
                detection = detect_state_and_overlays(
                    frame,
                    log_matches=self._match_trace,
                )
                primary = str(detection.get("state") or "").upper()
                if primary in {"HOME", "HOME_SCREEN"}:
                    home_control = detect_home_battle_control(frame).control
                    observed = (
                        "home_new_battle"
                        if home_control is HomeBattleControl.NEW_BATTLE
                        else "home_resume_battle"
                        if home_control is HomeBattleControl.RESUME_BATTLE
                        else "unknown"
                    )
                elif primary == "RUNNING":
                    observed = "active_battle"
                else:
                    observed = "unknown"
            except Exception:
                return False
            if observed != expected_state:
                return False
            if stable and attempt == 0:
                time.sleep(0.2)
        return True

    def _sync_setup_capture(self, frame: Optional[Frame]) -> bool:
        """Run one exact runtime-owned forced save before publishing preview."""

        capture = self._supervisor.setup_capture
        if not isinstance(capture, Mapping):
            return False
        status = str(capture.get("status") or "")
        request_id = str(capture.get("request_id") or "")
        if status in SETUP_CAPTURE_TERMINAL_STATUSES or status == "ready":
            self._pending_setup_capture_claims().pop(request_id, None)
            return False
        requested = capture.get("evidence")
        current = self._current_control_workflow_evidence()

        workflow_log_reason = (
            "reuse the exact in-process forced save already requested by "
            "Return Control before projecting existing Strategy and Module "
            "authoring values"
            if capture.get("acquisition_source")
            == "retained_return_control_refresh"
            else "force an exact-target save before projecting existing "
            "Strategy and Module authoring values"
        )

        def existing_authority_outcome() -> str:
            return (
                "unchanged_paused"
                if self._supervisor.is_paused
                else "preserved"
            )

        def finish(
            final_status: str,
            reason: str,
            *,
            authority_outcome: Optional[str] = None,
        ) -> None:
            transitioned = self._supervisor.transition_setup_capture(
                request_id,
                final_status,
                reason=reason,
                acknowledgement=(current or {}),
                authority_outcome=(
                    authority_outcome or existing_authority_outcome()
                ),
            )
            if transitioned is None:
                claims = self._pending_setup_capture_claims()
                claims.clear()
                claims[request_id] = {
                    "terminal_status": final_status,
                    "reason": reason,
                    "authority_outcome": (
                        authority_outcome or existing_authority_outcome()
                    ),
                }
            else:
                self._pending_setup_capture_claims().pop(request_id, None)
            self._log_operator_workflow_result(
                request_id,
                purpose="Capturing the current setup",
                reason=workflow_log_reason,
                result=f"Setup capture {final_status} — {reason}",
            )

        if not isinstance(requested, Mapping) or not isinstance(current, Mapping):
            finish("unavailable", "fresh runtime workflow evidence is unavailable")
            return True
        mismatch = next(
            (
                field
                for field in (
                    "runtime_id",
                    "pid",
                    "adb_target",
                    "target_generation",
                    "activity_scope_run_id",
                    "game_state",
                )
                if requested.get(field) != current.get(field)
            ),
            None,
        )
        if mismatch is not None:
            finish(
                "interrupted",
                f"capture evidence changed at {mismatch} before serialization",
            )
            return True
        ready_claim = self._pending_setup_capture_claims().get(request_id)
        if isinstance(ready_claim, Mapping) and ready_claim.get(
            "terminal_status"
        ):
            final_status = str(ready_claim.get("terminal_status") or "failed")
            reason = str(
                ready_claim.get("reason") or "setup capture ended"
            )
            terminal = self._supervisor.transition_setup_capture(
                request_id,
                final_status,
                reason=reason,
                acknowledgement=current,
                authority_outcome=str(
                    ready_claim.get("authority_outcome") or "preserved"
                ),
            )
            if terminal is None:
                return True
            self._pending_setup_capture_claims().pop(request_id, None)
            self._log_operator_workflow_result(
                request_id,
                purpose="Capturing the current setup",
                reason=workflow_log_reason,
                result=f"Setup capture {final_status} — {reason}",
            )
            return True
        if isinstance(ready_claim, Mapping):
            ready = self._supervisor.transition_setup_capture(
                request_id,
                "ready",
                reason=str(ready_claim.get("reason") or "capture ready"),
                acknowledgement=current,
                preview=ready_claim.get("preview"),
                authority_outcome=existing_authority_outcome(),
            )
            if ready is None:
                # The exact typed result remains process-local.  Retry only
                # its atomic ledger receipt; never serialize a
                # second time to recover a partial write.  The capture hold is
                # sufficient ownership; do not overwrite Enabled merely
                # because its reporting receipt needs another atomic attempt.
                return True
            self._pending_setup_capture_claims().pop(request_id, None)
            self._log_operator_workflow_result(
                request_id,
                purpose="Capturing the current setup",
                reason=workflow_log_reason,
                result=(
                    "Setup capture ready for review — no Strategy or preset "
                    "was activated"
                ),
            )
            return True
        retained_return_source = capture.get("acquisition_source") == (
            "retained_return_control_refresh"
        )
        retained_return_claim = (
            self._retained_return_setup_capture_claim(capture, current)
            if retained_return_source
            else None
        )
        if retained_return_source and retained_return_claim is None:
            finish(
                "interrupted",
                "the exact in-process Return Control forced save is no longer available; no cached save was used",
            )
            return True
        if status == "capturing":
            # ``capturing`` is a process-owned critical section.  Reaching it
            # without the in-process ready claim means that owner was lost;
            # repeating Android lifecycle input would be ambiguous.
            self._supervisor.pause_for_catastrophic_failure(
                RuntimeFailureKind.INPUT_RESULT_UNCERTAIN,
                reason=(
                    "setup capture lost its process-local owner after entering "
                    "the serialization critical section"
                ),
            )
            finish(
                "interrupted",
                "runtime capture ownership ended before its ready receipt; no second serialization was attempted and Automation remains Paused",
                authority_outcome="paused_for_safety",
            )
            return True
        if self._supervisor.is_paused and not retained_return_source:
            finish(
                "unavailable",
                "Automation Paused blocks the Android lifecycle refresh; no cached save was used",
            )
            return True
        if status == "requested":
            capture = self._supervisor.transition_setup_capture(
                request_id,
                "acknowledged",
                reason="exact workflow evidence acknowledged",
                acknowledgement=current,
            )
            if capture is None:
                return True
            status = str(capture.get("status") or "")
        if status == "acknowledged":
            capture = self._supervisor.transition_setup_capture(
                request_id,
                "capturing",
                reason="runtime owns the forced serialization boundary",
                acknowledgement=current,
            )
            if capture is None:
                return True
        self._log_operator_workflow_intent(
            request_id,
            purpose="Capturing the current setup",
            reason=workflow_log_reason,
        )

        if retained_return_source:
            acquisition = retained_return_claim.get("acquisition")
        else:
            session = self._adb_target_session
            acquirer = self._player_save_acquirer
            if session is None or acquirer is None:
                finish(
                    "unavailable",
                    "exact-target player-save acquisition is unavailable",
                )
                return True
            serializer = GuardedPlayerSaveSerializer(
                acquirer=acquirer,
                context_guard_fn=lambda: self._setup_capture_context_matches(
                    requested,
                    request_id,
                ),
                action_guard_fn=lambda: self._runtime_action_guard(
                    action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                    owner=AuthorityHold.SETUP_CAPTURE,
                ),
                source_guard_fn=lambda source_frame, stable: (
                    self._setup_capture_source_matches(
                        requested,
                        source_frame,
                        stable=stable,
                    )
                ),
                log_prefix="SETUP_CAPTURE",
            )
            serialized = serializer.acquire(
                expected_target=str(requested.get("adb_target") or ""),
                expected_generation=int(
                    requested.get("target_generation") or 0
                ),
                target_generation_detail=hashlib.sha256(
                    (
                        f"{requested.get('adb_target')}\0"
                        f"{requested.get('target_generation')}"
                    ).encode("utf-8")
                ).hexdigest()[:16],
                source_label=str(
                    requested.get("game_state") or "current setup"
                ),
                initial_frame=frame,
            )
            lifecycle_input_attempted = bool(
                getattr(
                    serialized,
                    "lifecycle_input_attempted",
                    serialized.background_dispatched,
                )
            )
            source_restored = bool(
                getattr(
                    serialized,
                    "source_restored",
                    serialized.status is GuardedSerializationStatus.COMPLETE,
                )
            )
            if lifecycle_input_attempted:
                self._setup_capture_source_refreshed = True
            if serialized.status is GuardedSerializationStatus.BLOCKED:
                if lifecycle_input_attempted and not source_restored:
                    self._supervisor.pause_for_catastrophic_failure(
                        RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                        reason=serialized.reason,
                    )
                    finish(
                        "failed",
                        "source restoration or exact workflow authority was lost after backgrounding; Automation remains Paused",
                        authority_outcome="paused_for_safety",
                    )
                else:
                    finish(
                        "unavailable",
                        f"forced save refresh was blocked: {serialized.reason}",
                    )
                return True
            acquisition = serialized.acquisition
        if not (
            isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.complete
        ):
            finish(
                "unavailable",
                "the source was restored, but no stable current save was acquired; "
                "the capture did not change automation authority",
                authority_outcome=(
                    "unchanged_paused"
                    if retained_return_source
                    else existing_authority_outcome()
                ),
            )
            return True
        workflow_binding, binding_status, binding_reason = (
            self._setup_capture_workflow_binding(acquisition, requested)
        )
        if workflow_binding is None:
            authority_outcome = (
                "unchanged_paused"
                if retained_return_source
                else existing_authority_outcome()
            )
            if (
                binding_status == "failed"
                and not retained_return_source
                and requested.get("game_state")
                in {"active_battle", "home_resume_battle"}
            ):
                reason = str(
                    binding_reason
                    or "setup-capture battle identity is contradictory"
                )
                authority = self._get_action_authority()
                prior_gate = authority.strategy_gate
                if prior_gate is not None and prior_gate.source == "setup_capture":
                    authority.clear_strategy_gate(
                        event=StrategyGateExitEvent.SUCCESSFUL_VALIDATION,
                        reason=(
                            "recoverable setup-capture evidence cannot halt "
                            "ordinary automation"
                        ),
                    )
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.EVIDENCE_UNAVAILABLE,
                    reason,
                )
                authority_outcome = "preserved"
                binding_reason = (
                    f"{reason}; the capture failed and Automation continues "
                    "without granting authority from that evidence"
                )
            elif binding_status == "failed" and not retained_return_source:
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.EVIDENCE_UNAVAILABLE,
                    str(
                        binding_reason
                        or "setup-capture Home boundary contradiction"
                    ),
                )
                authority_outcome = "preserved"
                binding_reason = (
                    str(binding_reason or "setup-capture boundary contradiction")
                    + "; capture failed, but Automation remains Enabled because "
                    "no unsafe source transition remains"
                )
            finish(
                binding_status or "unavailable",
                str(binding_reason or "setup-capture save binding is unavailable")
                + (
                    "; the capture did not change automation authority"
                    if authority_outcome
                    in {"preserved", "unchanged_paused"}
                    else ""
                ),
                authority_outcome=authority_outcome,
            )
            return True
        existing_gate = self._get_action_authority().strategy_gate
        if existing_gate is not None and existing_gate.source == "setup_capture":
            self._get_action_authority().clear_strategy_gate(
                event=StrategyGateExitEvent.SUCCESSFUL_VALIDATION,
                reason=(
                    "a later exact setup capture proved a coherent battle boundary"
                ),
            )
        try:
            preview = project_forced_save_setup(acquisition)
        except (SetupCaptureError, TypeError, ValueError) as exc:
            finish(
                "unavailable",
                f"save-backed setup projection is unavailable: {exc}; "
                "the capture did not change automation authority",
                authority_outcome=(
                    "unchanged_paused"
                    if retained_return_source
                    else existing_authority_outcome()
                ),
            )
            return True
        preview["workflow_binding"] = workflow_binding
        source_manual_id = str(
            capture.get("source_manual_control_id") or ""
        ).strip()
        preview["capture_origin"] = {
            "schema_version": 1,
            "acquisition_source": str(
                capture.get("acquisition_source")
                or "new_setup_capture_refresh"
            ),
            "source_manual_control_fingerprint": (
                hashlib.sha256(
                    (
                        "thetower-setup-capture-manual-origin-v1\0"
                        f"{source_manual_id}"
                    ).encode("utf-8")
                ).hexdigest()
                if source_manual_id
                else None
            ),
        }
        ready_reason = (
            "exact retained Return Control forced save projected for review "
            "without new device input; saving will not resolve Return Control, "
            "select, publish, queue, or apply anything"
            if retained_return_source
            else "fresh forced save projected for review; saving will not "
            "select, publish, queue, or apply it"
        )
        claims = self._pending_setup_capture_claims()
        claims.clear()
        claims[request_id] = {
            "preview": preview,
            "reason": ready_reason,
        }
        ready = self._supervisor.transition_setup_capture(
            request_id,
            "ready",
            reason=ready_reason,
            acknowledgement=current,
            preview=preview,
            authority_outcome=existing_authority_outcome(),
        )
        if ready is None:
            return True
        self._pending_setup_capture_claims().pop(request_id, None)
        self._log_operator_workflow_result(
            request_id,
            purpose="Capturing the current setup",
            reason=workflow_log_reason,
            result=(
                "Setup capture ready for review — no Strategy or preset was activated"
            ),
        )
        return True

    @staticmethod
    def _persist_minimal_surrender_record(
        context: Mapping[str, object],
        acquisition: PlayerSaveAcquisitionBundle,
        *,
        initiator: str,
        disposition_provenance: Optional[Mapping[str, object]] = None,
        disposition_reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist one non-representative record from the bound natural save."""

        report = context.get("terminal_save_report")
        if not (
            isinstance(report, Mapping)
            and terminal_save_report_complete(report)
            and acquisition.complete
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.NATURAL_BOUNDARY
        ):
            raise ValueError(
                "minimal surrender record requires a complete natural save bundle"
            )
        record_context = dict(context)
        record_context.pop("terminal_save_report", None)
        record = build_minimal_battle_record_from_player_save(
            report,
            captured_at=acquisition.captured_at,
            strategy_name=(
                str(context.get("strategy"))
                if context.get("strategy")
                else None
            ),
            run_configuration=context.get("run_configuration"),
            runtime_context=record_context,
            initiator=initiator,
            disposition_provenance=disposition_provenance,
            disposition_reason=disposition_reason,
        )
        persist_battle_record(record)
        return record

    @staticmethod
    def _terminal_record_killed_by(record: object) -> str:
        """Return the normalized terminal cause discovered by supported UI."""

        if not isinstance(record, Mapping):
            return ""
        game_stats = record.get("game_stats")
        fields = (
            game_stats.get("fields")
            if isinstance(game_stats, Mapping)
            else None
        )
        killed_by = (
            fields.get("killed_by")
            if isinstance(fields, Mapping)
            else None
        )
        if isinstance(killed_by, Mapping):
            value = str(
                killed_by.get("value") or killed_by.get("raw") or ""
            ).strip()
            if value:
                return value
        more_stats = record.get("more_stats")
        sections = (
            more_stats.get("sections")
            if isinstance(more_stats, Mapping)
            else None
        )
        if not isinstance(sections, list):
            return ""
        for section in sections:
            if not (
                isinstance(section, Mapping)
                and section.get("key") == "battle_report"
                and isinstance(section.get("rows"), list)
            ):
                continue
            for row in section["rows"]:
                if isinstance(row, Mapping) and row.get("key") == "killed_by":
                    return str(row.get("value") or row.get("raw") or "").strip()
        return ""

    def _observe_manual_terminal(
        self,
        manual: Mapping[str, Any],
        current: Mapping[str, Any],
    ) -> Optional[Dict[str, object]]:
        """Classify a manual Game Over from save before any stats UI input."""

        manual_id = str(manual.get("manual_control_id") or "")
        scope_id = str(current.get("activity_scope_run_id") or "")
        observation_id = str(current.get("observation_id") or "")
        if not manual_id or not scope_id or not observation_id:
            return None
        prior_claim = self._manual_terminal_claims().get(manual_id)
        pending_evidence = (
            prior_claim.get("pending_terminal_evidence")
            if isinstance(prior_claim, Mapping)
            else None
        )
        prior_binding = (
            prior_claim.get("evidence")
            if isinstance(prior_claim, Mapping)
            else None
        )
        if isinstance(pending_evidence, Mapping) and isinstance(
            prior_binding,
            Mapping,
        ):
            same_binding = bool(
                current.get("game_state") == "game_over"
                and all(
                    prior_binding.get(field) == current.get(field)
                    for field in (
                        "runtime_id",
                        "pid",
                        "adb_target",
                        "target_generation",
                        "activity_scope_run_id",
                    )
                )
            )
            if same_binding:
                recorded = self._supervisor.record_manual_terminal_evidence(
                    manual_id,
                    pending_evidence,
                )
                recorded_terminal = (
                    recorded.get("terminal_evidence")
                    if isinstance(recorded, Mapping)
                    else None
                )
                if (
                    isinstance(recorded_terminal, Mapping)
                    and recorded_terminal.get("receipt")
                    == pending_evidence.get("receipt")
                ):
                    retained = dict(prior_claim)
                    retained.pop("pending_terminal_evidence", None)
                    self._manual_terminal_claims()[manual_id] = retained
                else:
                    self._flag_recoverable_runtime_failure(
                        RuntimeFailureKind.REPORTING_FAILURE,
                        "manual terminal evidence receipt did not persist",
                    )
                return recorded
            self._manual_terminal_claims().pop(manual_id, None)
        context, acquisition, _mapping_observer = self._terminal_battle_bundle(
            "GAME_OVER"
        )
        report = context.get("terminal_save_report")
        status = "unavailable"
        reason = (
            str(report.get("reason") or "terminal_save_report_unavailable")
            if isinstance(report, Mapping)
            else "terminal_save_report_unavailable"
        )
        receipt = None
        battle_id = None
        killed_by = ""
        semantic_report_complete = bool(
            isinstance(report, Mapping)
            and terminal_save_report_complete(report)
        )
        structural_report_complete = bool(
            isinstance(report, Mapping)
            and terminal_save_report_structural_complete(report)
        )
        if (
            isinstance(report, Mapping)
            and (semantic_report_complete or structural_report_complete)
            and isinstance(acquisition, PlayerSaveAcquisitionBundle)
        ):
            if semantic_report_complete:
                completed = report.get("completed_entry")
                identity = (
                    completed.get("identity")
                    if isinstance(completed, Mapping)
                    else None
                )
                killed_by = str(
                    identity.get("killed_by")
                    if isinstance(identity, Mapping)
                    else ""
                ).strip()
            else:
                snapshot = acquisition.snapshot
                checks = getattr(snapshot, "checks", None)
                cause = (
                    checks.get("battle_history_killed_by")
                    if isinstance(checks, Mapping)
                    else None
                )
                if (
                    getattr(cause, "status", None) == "observed"
                    and getattr(cause, "complete", None) is True
                ):
                    killed_by = str(getattr(cause, "value", "") or "").strip()
            if killed_by:
                collection = str(
                    manual.get("surrender_collection") or "minimal"
                ).lower()
                try:
                    expected_binding = PlayerSaveTargetBinding(
                        str(current.get("adb_target") or ""),
                        int(current.get("target_generation")),
                    )
                    receipt = build_terminal_return_reconciliation_receipt(
                        workflow_id=manual_id,
                        observation_id=observation_id,
                        activity_scope_id=scope_id,
                        acquisition=acquisition,
                        runtime_session_id=str(
                            getattr(
                                self,
                                "_player_save_runtime_session_id",
                                "",
                            )
                            or ""
                        ),
                        expected_binding=expected_binding,
                        killed_by=killed_by,
                        collection=collection,
                    )
                except (TypeError, ValueError) as exc:
                    reason = f"terminal receipt rejected: {exc}"
                else:
                    structural_minimal_surrender = bool(
                        not semantic_report_complete
                        and killed_by.lower() == "surrender"
                        and collection == "minimal"
                    )
                    if not semantic_report_complete and not structural_minimal_surrender:
                        receipt = None
                        reason = (
                            "terminal History continuity is complete, but the "
                            "semantic report requires the existing UI fallback"
                        )
                    else:
                        status = (
                            "confirmed_surrender"
                            if killed_by.lower() == "surrender"
                            else "confirmed_other"
                        )
                        reason = (
                            "causally bound structural terminal save classified "
                            "minimal Surrender; semantic record publication is "
                            "unavailable"
                            if structural_minimal_surrender
                            else "causally bound terminal save classified the outcome"
                        )
                    if status == "confirmed_surrender" and collection == "minimal":
                        if structural_minimal_surrender:
                            log(
                                "[MANUAL_CONTROL] Exact structural History "
                                "continuity confirmed minimal Surrender; the "
                                "malformed semantic report was not published "
                                "and no terminal UI input is required",
                                "INFO",
                            )
                        else:
                            operation_id = f"{manual_id}:minimal-surrender-record"
                            log_action_intent(
                                "Recording a manual Surrender without terminal UI",
                                reason=(
                                    "the exact-run natural save confirmed Surrender "
                                    "and minimal collection was selected"
                                ),
                                operation_id=operation_id,
                            )
                            try:
                                record = self._persist_minimal_surrender_record(
                                    context,
                                    acquisition,
                                    initiator="operator_manual_control",
                                    disposition_provenance={
                                        "terminal_receipt": receipt,
                                    },
                                )
                            except (OSError, TypeError, ValueError) as exc:
                                status = "unavailable"
                                receipt = None
                                reason = f"minimal surrender record failed: {exc}"
                                log_result(
                                    "Manual Surrender record failed; Automation "
                                    "remains Paused and no stats UI was opened",
                                    detail=f"[MANUAL_CONTROL] reason={reason}",
                                    operation_id=operation_id,
                                )
                            else:
                                battle_id = str(record.get("battle_id") or "")
                                log_result(
                                    "Manual Surrender recorded from save; stats UI "
                                    "and optional enrichment were skipped",
                                    detail=(
                                        "[MANUAL_CONTROL] disposition=minimal "
                                        f"battle_id={battle_id} analytics=excluded"
                                    ),
                                    operation_id=operation_id,
                                )
        evidence: Dict[str, object] = {
            "schema_version": 1,
            "status": status,
            "observation_id": observation_id,
            "activity_scope_fingerprint": (
                receipt["terminal"]["activity_scope_fingerprint"]
                if isinstance(receipt, Mapping)
                else hashlib.sha256(
                    (
                        "thetower-control-workflow-scope-v1\0" + scope_id
                    ).encode("utf-8")
                ).hexdigest()
            ),
            "reason": reason,
        }
        if receipt is not None:
            evidence["receipt"] = receipt
        if battle_id:
            evidence["battle_id"] = battle_id
        if receipt is not None:
            self._manual_terminal_claims()[manual_id] = {
                "receipt": copy.deepcopy(receipt),
                "acquisition": acquisition,
                "context": context,
                "evidence": dict(current),
                "pending_terminal_evidence": copy.deepcopy(evidence),
            }
        recorded = self._supervisor.record_manual_terminal_evidence(
            manual_id,
            evidence,
        )
        recorded_terminal = (
            recorded.get("terminal_evidence")
            if isinstance(recorded, Mapping)
            else None
        )
        if (
            receipt is not None
            and isinstance(recorded_terminal, Mapping)
            and recorded_terminal.get("receipt") == receipt
        ):
            retained = self._manual_terminal_claims().get(manual_id)
            if isinstance(retained, Mapping):
                retained = dict(retained)
                retained.pop("pending_terminal_evidence", None)
                self._manual_terminal_claims()[manual_id] = retained
        elif receipt is None:
            self._manual_terminal_claims().pop(manual_id, None)
        if status == "unavailable":
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.EVIDENCE_UNAVAILABLE,
                reason,
            )
            log(
                "[MANUAL_CONTROL] Manual terminal evidence is ambiguous; "
                "the operator-owned manual Pause remains in effect and no "
                "terminal UI input is authorized",
                "WARN",
                console=True,
            )
        return recorded

    def _manual_terminal_claims(self) -> Dict[str, Dict[str, object]]:
        claims = getattr(self, "_manual_terminal_save_claims", None)
        if not isinstance(claims, dict):
            claims = {}
            self._manual_terminal_save_claims = claims
        return claims

    def _matching_manual_terminal_claim(
        self,
        manual: Mapping[str, object],
        current: Mapping[str, object],
    ) -> Optional[Dict[str, object]]:
        """Return the one natural bundle bound to this manual terminal route."""

        manual_id = str(manual.get("manual_control_id") or "")
        claim = self._manual_terminal_claims().get(manual_id)
        terminal_evidence = manual.get("terminal_evidence")
        if not (
            isinstance(claim, Mapping)
            and isinstance(terminal_evidence, Mapping)
            and current.get("game_state") == "game_over"
            and claim.get("receipt") == terminal_evidence.get("receipt")
            and isinstance(claim.get("evidence"), Mapping)
            and all(
                claim["evidence"].get(field) == current.get(field)
                for field in (
                    "runtime_id",
                    "pid",
                    "adb_target",
                    "target_generation",
                    "activity_scope_run_id",
                )
            )
        ):
            self._manual_terminal_claims().pop(manual_id, None)
            return None
        acquisition = claim.get("acquisition")
        context = claim.get("context")
        boundary = (
            acquisition.boundary
            if isinstance(acquisition, PlayerSaveAcquisitionBundle)
            else None
        )
        try:
            expected_binding = PlayerSaveTargetBinding(
                str(current.get("adb_target") or ""),
                int(current.get("target_generation")),
            )
        except (TypeError, ValueError):
            return None
        if not (
            isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.complete
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.NATURAL_BOUNDARY
            and acquisition.binding == expected_binding
            and isinstance(boundary, PlayerSaveNaturalBoundary)
            and boundary.kind is PlayerSaveBoundaryKind.GAME_OVER
            and boundary.activity_scope_id
            == str(current.get("activity_scope_run_id") or "")
            and boundary.runtime_session_id
            == str(getattr(self, "_player_save_runtime_session_id", "") or "")
            and isinstance(context, Mapping)
        ):
            self._manual_terminal_claims().pop(manual_id, None)
            return None
        return dict(claim)

    def _retry_pending_manual_terminal_completion(
        self,
        manual: Mapping[str, object],
        current: Optional[Mapping[str, object]],
    ) -> Optional[Dict[str, object]]:
        """Retry only the ledger write after terminal UI work already ran."""

        manual_id = str(manual.get("manual_control_id") or "")
        claim = self._manual_terminal_claims().get(manual_id)
        pending = (
            claim.get("pending_completion")
            if isinstance(claim, Mapping)
            else None
        )
        if not isinstance(pending, Mapping):
            return None
        semantic_completion_applied = bool(
            claim.get("semantic_completion_applied") is True
        )
        acquisition = claim.get("acquisition")
        evidence = claim.get("evidence")
        ui_fallback = claim.get("ui_fallback") is True
        same_runtime_binding = bool(
            isinstance(current, Mapping)
            and isinstance(evidence, Mapping)
            and all(
                evidence.get(field) == current.get(field)
                for field in (
                    "runtime_id",
                    "pid",
                    "adb_target",
                    "target_generation",
                )
            )
        )
        pending_receipt = pending.get("save_receipt")
        valid_ui_claim = bool(
            ui_fallback
            and isinstance(pending_receipt, Mapping)
            and ui_reconciliation_receipt_matches_evidence(
                pending_receipt,
                evidence,
            )
        )
        valid_save_claim = bool(
            not ui_fallback
            and isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.complete
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.NATURAL_BOUNDARY
        )
        if (
            not semantic_completion_applied
            and (
                not same_runtime_binding
                or not (valid_ui_claim or valid_save_claim)
            )
        ):
            self._manual_terminal_claims().pop(manual_id, None)
            self._supervisor.transition_manual_control(
                manual_id,
                "interrupted",
                detail=(
                    "terminal completion evidence expired; automation continued "
                    "from the fresh observed boundary"
                ),
                refresh_status="terminal_completion_evidence_expired",
            )
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.WORKFLOW_EVIDENCE_EXPIRED,
                "manual terminal completion claim no longer matches the runtime",
            )
            return None
        completed = self._supervisor.transition_manual_control(
            manual_id,
            "completed",
            detail=str(pending.get("detail") or "terminal reconciliation complete"),
            refresh_status=str(
                pending.get("refresh_status")
                or "terminal_reconciliation_complete"
            ),
            save_receipt=(
                dict(pending["save_receipt"])
                if isinstance(pending.get("save_receipt"), Mapping)
                else None
            ),
            configuration=(
                dict(pending["configuration"])
                if isinstance(pending.get("configuration"), Mapping)
                else None
            ),
        )
        if completed is None or completed.get("status") != "completed":
            mutable_claim = self._manual_terminal_claims().get(manual_id)
            if (
                isinstance(mutable_claim, dict)
                and mutable_claim.get("reporting_failure_flagged") is not True
            ):
                mutable_claim["reporting_failure_flagged"] = True
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.REPORTING_FAILURE,
                    "manual terminal completion receipt could not be persisted; retry retained",
                )
            return None
        self._manual_terminal_claims().pop(manual_id, None)
        return dict(completed)

    def _running_reconciliation_claims(self) -> Dict[str, Dict[str, object]]:
        claims = getattr(self, "_operator_save_reconciliation_claims", None)
        if not isinstance(claims, dict):
            claims = {}
            self._operator_save_reconciliation_claims = claims
        return claims

    def _pending_return_reconciliation_claims(
        self,
    ) -> Dict[str, Dict[str, object]]:
        claims = getattr(self, "_manual_return_reconciliation_claims", None)
        if not isinstance(claims, dict):
            claims = {}
            self._manual_return_reconciliation_claims = claims
        return claims

    def _retry_pending_return_completion_report(
        self,
        manual: Mapping[str, object],
        claim: Mapping[str, object],
    ) -> bool:
        """Retry only a Return receipt after local reconciliation released."""

        workflow_id = str(manual.get("manual_control_id") or "")
        pending = claim.get("pending_completion")
        if not workflow_id or not isinstance(pending, Mapping):
            return False
        completed = self._supervisor.transition_manual_control(
            workflow_id,
            "completed",
            detail=str(pending.get("detail") or "Return Control completed"),
            refresh_status=str(
                pending.get("refresh_status")
                or "return_reconciliation_complete"
            ),
            save_receipt=(
                dict(pending["save_receipt"])
                if isinstance(pending.get("save_receipt"), Mapping)
                else None
            ),
            configuration=(
                dict(pending["configuration"])
                if isinstance(pending.get("configuration"), Mapping)
                else None
            ),
        )
        if completed is None or completed.get("status") != "completed":
            mutable_claim = self._pending_return_reconciliation_claims().get(
                workflow_id
            )
            if (
                isinstance(mutable_claim, dict)
                and mutable_claim.get("reporting_failure_flagged") is not True
            ):
                mutable_claim["reporting_failure_flagged"] = True
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.REPORTING_FAILURE,
                    "Return Control completion receipt could not be persisted; "
                    "automation continued and reporting retry was retained",
                )
            return False
        self._pending_return_reconciliation_claims().pop(workflow_id, None)
        return True

    def _stage_return_semantic_completion(
        self,
        manual: Mapping[str, object],
        completion_payload: Mapping[str, object],
        *,
        completion_kind: str,
    ) -> bool:
        """Release local Return authority before retryable ledger reporting."""

        workflow_id = str(manual.get("manual_control_id") or "")
        if not workflow_id:
            return False
        claims = self._pending_return_reconciliation_claims()
        mutable_claim = claims.get(workflow_id)
        if not isinstance(mutable_claim, dict):
            mutable_claim = {}
            claims[workflow_id] = mutable_claim
        mutable_claim["semantic_completion_applied"] = True
        mutable_claim["completion_kind"] = str(completion_kind)
        mutable_claim["pending_completion"] = copy.deepcopy(
            dict(completion_payload)
        )
        self._mission_mgr.finish_manual_return_reconciliation()
        if (
            getattr(
                self,
                "_manual_return_configuration_authorized_id",
                None,
            )
            == workflow_id
        ):
            self._manual_return_configuration_authorized_id = None
        # A failed durable receipt is recoverable reporting loss.  The exact
        # process-local claim remains only for retry and must not retain the
        # device-input hold after reconciliation is semantically complete.
        self._retry_pending_return_completion_report(manual, mutable_claim)
        return True

    def _pending_setup_capture_claims(
        self,
    ) -> Dict[str, Dict[str, object]]:
        """Retain a completed capture only until its ready receipt is durable."""

        claims = getattr(self, "_setup_capture_ready_claims", None)
        if not isinstance(claims, dict):
            claims = {}
            self._setup_capture_ready_claims = claims
        return claims

    def _retry_pending_running_return(
        self,
        manual: Mapping[str, object],
        current: Mapping[str, object],
    ) -> bool:
        """Advance Return using only retained typed, exact-bound evidence."""

        workflow_id = str(manual.get("manual_control_id") or "")
        claim = self._matching_pending_running_return_claim(
            manual,
            current,
        )
        if claim is None:
            return False
        receipt = claim.get("receipt")
        check_sets = claim.get("check_sets")
        if not isinstance(receipt, Mapping) or not isinstance(
            check_sets,
            Mapping,
        ):
            return False
        ui_fallback = isinstance(receipt.get("ui_fallback"), Mapping)

        mismatched = tuple(check_sets.get("mismatched", ()) or ())
        ui_required = tuple(check_sets.get("ui_required", ()) or ())
        unresolved = tuple(sorted({*mismatched, *ui_required}))
        if not unresolved:
            return self._complete_running_return_claim(
                manual,
                current,
                claim,
            )

        configuration = self._return_configuration_report(
            receipt,
            check_sets,
            stage="configuration_validation_pending",
        )
        transitioned = self._supervisor.transition_manual_control(
            workflow_id,
            "awaiting_configuration",
            detail=(
                "save evidence was unusable; supported UI checks require "
                "reconciliation before Strategy input resumes"
                if ui_fallback
                else "fresh forced save found configuration checks that require "
                "operator-visible reconciliation before Strategy input resumes"
            ),
            refresh_status=(
                "configuration_validation_pending"
            ),
            save_receipt=dict(receipt),
            configuration=configuration,
        )
        if transitioned is None or transitioned.get("status") != (
            "awaiting_configuration"
        ):
            return False

        if mismatched:
            # Save-authoritative differences cannot be repaired inside the
            # active battle.  Report them, release Return Control, and let the
            # selected Strategy continue in degraded mode.
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.CONFIGURATION_MISMATCH,
                "Return Control found: " + ", ".join(mismatched),
            )
            self._mission_mgr.mark_running_configuration_degraded(
                source="return_control",
                reason="Return Control found: " + ", ".join(mismatched),
                failed_checks=mismatched,
            )
            return self._complete_running_return_claim(
                transitioned,
                current,
                claim,
                degraded=True,
            )

        started = self._mission_mgr.begin_manual_return_reconciliation()
        mutable_claim = self._pending_return_reconciliation_claims().get(
            workflow_id
        )
        if isinstance(mutable_claim, dict):
            mutable_claim["validation_started"] = bool(started)
        if not started:
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                "the active Strategy did not expose a running Return check",
            )
            if unresolved:
                self._mission_mgr.mark_running_configuration_degraded(
                    source="return_control",
                    reason=(
                        "Return Control could not validate: "
                        + ", ".join(unresolved)
                    ),
                    failed_checks=unresolved,
                )
            return self._complete_running_return_claim(
                transitioned,
                current,
                claim,
                degraded=bool(unresolved),
            )
        return True

    def _matching_pending_running_return_claim(
        self,
        manual: Mapping[str, object],
        current: Mapping[str, object],
    ) -> Optional[Dict[str, object]]:
        """Revalidate the private Return claim against the live battle."""

        workflow_id = str(manual.get("manual_control_id") or "")
        claim = self._pending_return_reconciliation_claims().get(workflow_id)
        if not isinstance(claim, Mapping):
            return None
        acquisition = claim.get("acquisition")
        temporal = claim.get("temporal_binding")
        context = claim.get("context")
        evidence = claim.get("evidence")
        receipt = claim.get("receipt")
        if claim.get("ui_fallback") is True:
            if (
                isinstance(evidence, Mapping)
                and isinstance(receipt, Mapping)
                and ui_reconciliation_receipt_matches_evidence(receipt, current)
                and self._repair_authority_matches_runtime(evidence, current)
                and current.get("game_state") == "active_battle"
            ):
                return dict(claim)
            self._pending_return_reconciliation_claims().pop(
                workflow_id,
                None,
            )
            self._mission_mgr.finish_manual_return_reconciliation()
            self._supervisor.transition_manual_control(
                workflow_id,
                "interrupted",
                detail=(
                    "Return Control UI evidence expired; automation continued "
                    "from the fresh active-battle observation"
                ),
                refresh_status="workflow_evidence_expired",
            )
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.WORKFLOW_EVIDENCE_EXPIRED,
                "running Return UI claim no longer matches the live battle",
            )
            return None
        try:
            live_context = self._current_player_save_attachment_context()
        except Exception:
            live_context = None
        if not (
            isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.complete
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.FORCED_SERIALIZATION
            and isinstance(temporal, RunningAttachmentTemporalBinding)
            and isinstance(context, PlayerSaveAttachmentContext)
            and isinstance(evidence, Mapping)
            and isinstance(receipt, Mapping)
            and temporal.matches_context(context)
            and live_context == context
            and acquisition.binding == temporal.target_binding
            and self._repair_authority_matches_runtime(evidence, current)
        ):
            self._pending_return_reconciliation_claims().pop(
                workflow_id,
                None,
            )
            self._mission_mgr.finish_manual_return_reconciliation()
            self._supervisor.transition_manual_control(
                workflow_id,
                "interrupted",
                detail=(
                    "Return Control save evidence expired; automation continued "
                    "from the fresh active-battle observation"
                ),
                refresh_status="workflow_evidence_expired",
            )
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.WORKFLOW_EVIDENCE_EXPIRED,
                "running Return save claim no longer matches the live battle",
            )
            return None
        return dict(claim)

    def _complete_running_return_claim(
        self,
        manual: Mapping[str, object],
        current: Mapping[str, object],
        claim: Mapping[str, object],
        *,
        after_configuration: bool = False,
        degraded: bool = False,
    ) -> bool:
        """Persist the final Return acknowledgement, retaining write retries."""

        workflow_id = str(manual.get("manual_control_id") or "")
        receipt = claim.get("receipt")
        check_sets = claim.get("check_sets")
        acquisition = claim.get("acquisition")
        temporal = claim.get("temporal_binding")
        ui_fallback = claim.get("ui_fallback") is True
        if not isinstance(receipt, Mapping) or not isinstance(
            check_sets,
            Mapping,
        ):
            return False
        if not ui_fallback and not (
            isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and isinstance(temporal, RunningAttachmentTemporalBinding)
        ):
            return False
        final_receipt = dict(receipt)
        if after_configuration:
            all_checks = tuple(
                sorted(
                    {
                        *check_sets.get("accepted", ()),
                        *check_sets.get("mismatched", ()),
                        *check_sets.get("ui_required", ()),
                    }
                )
            )
            failure_checks_fn = getattr(
                self._mission_mgr,
                "session_preflight_failure_checks",
                None,
            )
            raw_failed_checks = (
                failure_checks_fn() if callable(failure_checks_fn) else []
            )
            failed_checks = {
                str(check).strip()
                for check in (
                    raw_failed_checks
                    if isinstance(raw_failed_checks, (list, tuple, set))
                    else []
                )
                if str(check).strip()
            }
            degraded_fn = getattr(
                self._mission_mgr,
                "session_preflight_degraded",
                None,
            )
            validation_degraded = bool(
                callable(degraded_fn) and degraded_fn() is True
            )
            unresolved_after_validation = set(failed_checks)
            if validation_degraded and not unresolved_after_validation:
                unresolved_after_validation.update(
                    {
                        *check_sets.get("mismatched", ()),
                        *check_sets.get("ui_required", ()),
                    }
                )
            resolved_after_validation = set(all_checks).difference(
                unresolved_after_validation
            )
            degraded = bool(degraded or validation_degraded)
            try:
                disposition = str(
                    receipt.get("continuity", {}).get("disposition")
                    if isinstance(receipt.get("continuity"), Mapping)
                    else "attachment_baseline"
                )
                if ui_fallback:
                    fallback = receipt.get("ui_fallback")
                    final_receipt = build_running_ui_reconciliation_receipt(
                        kind="return_control_reconciliation",
                        workflow_id=workflow_id,
                        observation_id=str(
                            current.get("observation_id") or ""
                        ),
                        evidence=current,
                        disposition=disposition,
                        reason=str(
                            fallback.get("reason")
                            if isinstance(fallback, Mapping)
                            else "save_evidence_unavailable"
                        ),
                        fallback_complete=bool(
                            isinstance(fallback, Mapping)
                            and fallback.get("status") == "complete"
                        ),
                        resolved_check_ids=resolved_after_validation,
                        unresolved_check_ids=unresolved_after_validation,
                    )
                else:
                    final_receipt = build_running_save_reconciliation_receipt(
                        kind="return_control_reconciliation",
                        workflow_id=workflow_id,
                        observation_id=str(
                            current.get("observation_id") or ""
                        ),
                        acquisition=acquisition,
                        temporal_binding=temporal,
                        disposition=disposition,
                        resolved_check_ids=resolved_after_validation,
                        unresolved_check_ids=unresolved_after_validation,
                    )
            except (TypeError, ValueError) as exc:
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.REPORTING_FAILURE,
                    f"Return Control final receipt could not be rebuilt: {exc}",
                )
                degraded = True
                final_receipt = dict(receipt)
        completion_payload = {
            "detail": (
                "Return Control released automation with configuration problems "
                "flagged for the next safe Home repair"
                if degraded
                else "supported UI discovery reconciled battle continuity and "
                "active Strategy configuration after manual control"
                if ui_fallback
                else "fresh forced save confirmed battle identity and active "
                "Strategy configuration after manual control"
            ),
            "refresh_status": (
                "reconciliation_complete_degraded"
                if degraded
                else "ui_fallback_reconciliation_complete"
                if ui_fallback
                else "save_reconciliation_complete"
            ),
            "save_receipt": final_receipt,
            "configuration": self._return_configuration_report(
                final_receipt,
                check_sets,
                stage="complete_degraded" if degraded else "complete",
            ),
        }
        return self._stage_return_semantic_completion(
            manual,
            completion_payload,
            completion_kind="active_battle",
        )

    def _advance_running_return_configuration(
        self,
        img: Frame,
        detection: Dict[str, Any],
    ) -> bool:
        """Run only Return-owned validation before releasing the manual hold."""

        manual = self._supervisor.manual_control
        current = self._current_control_workflow_evidence()
        if not (
            isinstance(manual, Mapping)
            and manual.get("status") == "awaiting_configuration"
            and not self._supervisor.is_paused
            and isinstance(current, Mapping)
            and current.get("game_state") == "active_battle"
        ):
            return False
        claim = self._matching_pending_running_return_claim(manual, current)
        if claim is None:
            return False
        workflow_id = str(manual.get("manual_control_id") or "")
        if claim.get("validation_started") is not True:
            if not self._mission_mgr.begin_manual_return_reconciliation():
                return self._complete_running_return_claim(
                    manual,
                    current,
                    claim,
                    after_configuration=True,
                )
            mutable_claim = self._pending_return_reconciliation_claims().get(
                workflow_id
            )
            if isinstance(mutable_claim, dict):
                mutable_claim["validation_started"] = True

        if self._mission_mgr.session_preflight_terminally_blocked():
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                "legacy Return validation reported a terminal block",
            )
            return self._complete_running_return_claim(
                manual,
                current,
                claim,
                after_configuration=True,
                degraded=True,
            )
        if self._mission_mgr.session_preflight_pending():
            self._run_owned_strategy_tick(
                AuthorityHold.MANUAL_CONTROL_RETURN,
                img,
                detection,
                strategy_only=True,
            )
            if self._mission_mgr.session_preflight_terminally_blocked():
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                    "Return validation could not complete after its bounded check",
                )
                return self._complete_running_return_claim(
                    manual,
                    current,
                    claim,
                    after_configuration=True,
                    degraded=True,
                )
            if self._mission_mgr.session_preflight_pending():
                return True

        strategy = self._mission_mgr.strategy
        if (
            strategy is not None
            and strategy.requires_session_preflight()
            and strategy.is_session_preflight_complete(
                self._mission_mgr.ctx
            )
        ):
            return self._complete_running_return_claim(
                manual,
                current,
                claim,
                after_configuration=True,
            )

        self._flag_recoverable_runtime_failure(
            RuntimeFailureKind.VALIDATION_UNAVAILABLE,
            "Return validation ended without a complete Strategy result",
        )
        return self._complete_running_return_claim(
            manual,
            current,
            claim,
            after_configuration=True,
            degraded=True,
        )

    def _retain_running_reconciliation_claim(
        self,
        workflow_id: str,
        *,
        receipt: Mapping[str, object],
        acquisition: PlayerSaveAcquisitionBundle,
        temporal_binding: RunningAttachmentTemporalBinding,
        context: PlayerSaveAttachmentContext,
        evidence: Mapping[str, object],
        strategy_decision: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Retain typed process-local proof; the durable receipt is report-only."""

        configuration = receipt.get("configuration")
        continuity = receipt.get("continuity")
        if not isinstance(configuration, Mapping) or not isinstance(
            continuity,
            Mapping,
        ):
            raise ValueError("running reconciliation receipt is incomplete")
        rebuilt = build_running_save_reconciliation_receipt(
            kind=str(receipt.get("kind") or ""),
            workflow_id=str(workflow_id),
            observation_id=str(receipt.get("observation_id") or ""),
            acquisition=acquisition,
            temporal_binding=temporal_binding,
            disposition=str(continuity.get("disposition") or ""),
            resolved_check_ids=configuration.get("resolved_check_ids", ()),
            unresolved_check_ids=configuration.get(
                "unresolved_check_ids",
                (),
            ),
        )
        if rebuilt != dict(receipt):
            raise ValueError(
                "durable receipt does not match the retained typed acquisition"
            )
        claim: Dict[str, object] = {
            "receipt": copy.deepcopy(dict(receipt)),
            "acquisition": acquisition,
            "temporal_binding": temporal_binding,
            "context": context,
            "evidence": dict(evidence),
        }
        if isinstance(strategy_decision, Mapping):
            claim["attachment_strategy"] = dict(strategy_decision)
        self._running_reconciliation_claims()[str(workflow_id)] = claim

    def _matching_running_reconciliation_claim(
        self,
        workflow: Mapping[str, object],
        current: Mapping[str, object],
    ) -> Optional[Dict[str, object]]:
        """Return typed proof only while its exact process/battle binding holds."""

        workflow_id = str(workflow.get("request_id") or "")
        claim = self._running_reconciliation_claims().get(workflow_id)
        if not isinstance(claim, Mapping):
            return None
        acquisition = claim.get("acquisition")
        temporal = claim.get("temporal_binding")
        context = claim.get("context")
        evidence = claim.get("evidence")
        receipt = claim.get("receipt")
        reporting_unavailable = claim.get("reporting_unavailable") is True
        if claim.get("ui_fallback") is True:
            ui_receipt_bound = bool(
                (
                    reporting_unavailable
                    and (
                        receipt is None
                        or (
                            isinstance(receipt, Mapping)
                            and ui_reconciliation_receipt_matches_evidence(
                                receipt,
                                current,
                            )
                        )
                    )
                )
                or (
                    isinstance(receipt, Mapping)
                    and receipt == workflow.get("save_receipt")
                    and ui_reconciliation_receipt_matches_evidence(
                        receipt,
                        current,
                    )
                )
            )
            if not (
                isinstance(evidence, Mapping)
                and ui_receipt_bound
                and all(
                    evidence.get(field) == current.get(field)
                    for field in (
                        "runtime_id",
                        "pid",
                        "adb_target",
                        "target_generation",
                        "activity_scope_run_id",
                        "game_state",
                    )
                )
            ):
                self._running_reconciliation_claims().pop(workflow_id, None)
                return None
            return dict(claim)
        receipt_bound = bool(
            reporting_unavailable
            or (
                isinstance(receipt, Mapping)
                and receipt == workflow.get("save_receipt")
            )
        )
        if not (
            isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.complete
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.FORCED_SERIALIZATION
            and isinstance(temporal, RunningAttachmentTemporalBinding)
            and isinstance(context, PlayerSaveAttachmentContext)
            and isinstance(evidence, Mapping)
            and evidence.get("game_state") == "active_battle"
            and temporal.matches_context(context)
            and acquisition.binding == temporal.target_binding
            and receipt_bound
            and self._workflow_evidence_matches_runtime(
                evidence,
                current,
                intent="attach_battle",
                allowed_activity_scope_transition=(
                    temporal.source_activity_scope_id,
                    temporal.activity_scope_id or "",
                ),
            )[0]
        ):
            self._running_reconciliation_claims().pop(workflow_id, None)
            return None
        try:
            live_context = self._current_player_save_attachment_context(
                pending_adoption_workflow_id=workflow_id,
            )
        except Exception:
            return None
        if live_context != context or not temporal.matches_context(live_context):
            self._running_reconciliation_claims().pop(workflow_id, None)
            return None
        return dict(claim)

    def _interrupt_unbacked_ready_attachment(
        self,
        workflow: Mapping[str, object],
        current: Mapping[str, object],
    ) -> None:
        """End Attach when only a persisted/reporting receipt survives."""

        self._mission_mgr.revoke_initial_battle_intent(
            "attach_battle",
            request_id=str(workflow.get("request_id") or ""),
        )
        self._supervisor.transition_battle_workflow(
            str(workflow.get("request_id") or ""),
            "interrupted",
            reason=(
                "process-local reconciliation evidence is unavailable; the "
                "redacted receipt cannot grant attachment authority"
            ),
            acknowledgement=current,
        )
        self._flag_recoverable_runtime_failure(
            RuntimeFailureKind.WORKFLOW_EVIDENCE_EXPIRED,
            "Attach lost its process-local reconciliation claim",
        )

    def _complete_ready_attachment_after_adoption(self) -> bool:
        """Complete Attach only after lifecycle adoption of the validated battle."""

        workflow = self._supervisor.battle_workflow
        if (
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "attach_battle"
            and workflow.get("status")
            in {"ready", "validating_save", "action_dispatched"}
        ):
            retained = self._running_reconciliation_claims().get(
                str(workflow.get("request_id") or "")
            )
            if (
                isinstance(retained, Mapping)
                and retained.get("semantic_completion_applied") is True
            ):
                return self._retry_attachment_completion_report(
                    workflow,
                    retained,
                )
        current = self._current_control_workflow_evidence()
        if not (
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "attach_battle"
            and workflow.get("status")
            in {"ready", "validating_save", "action_dispatched"}
            and isinstance(current, Mapping)
            and current.get("game_state") == "active_battle"
            and self._mission_mgr.active_battle_observed()
        ):
            return False
        claim = self._matching_running_reconciliation_claim(workflow, current)
        if claim is None:
            self._interrupt_unbacked_ready_attachment(workflow, current)
            return False
        reporting_unavailable = claim.get("reporting_unavailable") is True
        if workflow.get("status") != "ready" and not reporting_unavailable:
            return False
        ui_fallback = claim.get("ui_fallback") is True
        raw_decision = claim.get("attachment_strategy")
        decision = (
            dict(raw_decision)
            if isinstance(raw_decision, Mapping)
            else self._attachment_strategy_decision(workflow)
        )
        attachment_mode = str(
            decision.get("attachment_mode") or "observation_only"
        )
        selected_strategy = str(
            decision.get("selected_strategy") or ""
        ).strip().lower()
        degraded = decision.get("degraded") is True
        receipt = workflow.get("save_receipt")
        configuration = (
            self._attachment_reporting_unavailable_configuration(decision)
            if reporting_unavailable
            else self._attachment_configuration_report(
                receipt,
                decision,
                stage="completed",
            )
            if isinstance(receipt, Mapping)
            else None
        )
        completed = self._supervisor.transition_battle_workflow(
            str(workflow.get("request_id") or ""),
            "completed",
            reason=(
                "the active battle was adopted for degraded observation; its "
                "durable reconciliation report was unavailable"
                if reporting_unavailable
                else "the snapshotted selected Strategy was compatible and adopted "
                "without repairing the active battle"
                if attachment_mode == "strategy"
                else "the active battle was adopted for intentional observation"
                if decision.get("applicability") == "intentional_observation"
                else "the active battle was adopted for degraded observation "
                "because Strategy applicability was incompatible or unverifiable"
            ),
            acknowledgement=current,
            configuration=configuration,
        )
        reporting_pending = bool(
            completed is None or completed.get("status") != "completed"
        )
        if reporting_pending:
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.REPORTING_FAILURE,
                "Attach adoption completed, but its terminal workflow report "
                "could not be persisted",
            )
            self._mission_mgr.mark_running_configuration_degraded(
                source="attachment_reporting",
                reason=(
                    "Attach adoption completed, but its terminal workflow "
                    "report is still pending"
                ),
                failed_checks=("workflow_reporting",),
                details={"reporting_status": "retrying"},
            )
        if degraded and not reporting_unavailable:
            self._mission_mgr.mark_running_configuration_degraded(
                source=(
                    "attachment_configuration"
                    if attachment_mode == "strategy"
                    else "attachment_applicability"
                ),
                reason=str(
                    decision.get("reason")
                    or "attached battle is running in degraded mode"
                ),
                failed_checks=decision.get("failed_checks", ()),
                details={
                    key: decision.get(key)
                    for key in (
                        "selected_strategy",
                        "strategy_request_id",
                        "attachment_mode",
                        "applicability",
                        "expected_family",
                        "expected_tier",
                        "observed_tier",
                        "observed_kind",
                    )
                    if decision.get(key) is not None
                },
            )
            log(
                "[ATTACH] Battle is running degraded without active-battle "
                f"repair: {decision.get('reason')}",
                "WARN",
                console=True,
            )

        snapshot_request_id = str(
            decision.get("strategy_request_id") or ""
        ).strip()
        current_strategy_request = self._supervisor.strategy_request
        current_matches_snapshot = bool(
            selected_strategy
            and isinstance(current_strategy_request, tuple)
            and len(current_strategy_request) in {2, 3}
            and str(current_strategy_request[0] or "").strip().lower()
            == selected_strategy
            and (
                not snapshot_request_id
                or str(current_strategy_request[1] or "").strip()
                == snapshot_request_id
            )
        )
        pending_strategy = getattr(self, "_pending_strategy_request", None)
        pending_matches_snapshot = bool(
            isinstance(pending_strategy, tuple)
            and len(pending_strategy) == 3
            and str(pending_strategy[0] or "").strip().lower()
            == selected_strategy
            and (
                not snapshot_request_id
                or str(pending_strategy[1] or "").strip()
                == snapshot_request_id
            )
        )
        if (
            isinstance(pending_strategy, tuple)
            and len(pending_strategy) == 3
            and pending_strategy[2] == "active_battle"
            and not pending_matches_snapshot
        ):
            self._defer_active_strategy_request_to_boundary(
                pending_strategy,
                reason=(
                    "a later request cannot change the immutable Attach "
                    "Strategy snapshot"
                ),
            )
            pending_strategy = self._pending_strategy_request
        if attachment_mode == "strategy" and (
            current_matches_snapshot and pending_matches_snapshot
        ):
            self._complete_strategy_application(
                selected_strategy,
                request_id=current_strategy_request[1],
            )
        elif (
            attachment_mode != "strategy"
            and selected_strategy
            and selected_strategy != "none"
            and current_matches_snapshot
            and (
                pending_strategy is None or pending_matches_snapshot
            )
        ):
            self._pending_strategy_request = (
                selected_strategy,
                current_strategy_request[1],
                "next_boundary",
            )
        elif (
            decision.get("applicability") == "intentional_observation"
            and selected_strategy == "none"
            and current_matches_snapshot
            and pending_matches_snapshot
        ):
            self._complete_strategy_application(
                "none",
                request_id=current_strategy_request[1],
            )

        if attachment_mode == "strategy":
            result = (
                f"Battle attached with {selected_strategy}; Automation remains "
                "Enabled and attachment validation will report mismatches "
                "without repairing this battle"
            )
            purpose = "Completing strategy-aware battle attachment"
        elif decision.get("applicability") == "intentional_observation":
            result = (
                "Battle attached for intentional No Strategy observation; "
                "Automation remains Enabled"
            )
            purpose = "Completing intentional observation attachment"
        else:
            result = (
                "Battle attached for degraded observation; Automation remains "
                "Enabled and the selected Strategy is reserved for the next "
                "safe boundary"
            )
            purpose = "Completing degraded observation attachment"
        self._log_operator_workflow_result(
            str(workflow.get("request_id") or "") + ":completed",
            purpose=purpose,
            reason=(
                "continue with supported UI monitoring after fallback continuity"
                if ui_fallback
                else "adopt the exact save-backed active-battle identity"
            ),
            result=result,
        )
        workflow_id = str(workflow.get("request_id") or "")
        if reporting_pending:
            retained = self._running_reconciliation_claims().get(workflow_id)
            if isinstance(retained, dict):
                retained["semantic_completion_applied"] = True
                retained["completion_acknowledgement"] = dict(current)
        else:
            self._running_reconciliation_claims().pop(workflow_id, None)
        return True

    def _retry_attachment_completion_report(
        self,
        workflow: Mapping[str, object],
        claim: Mapping[str, object],
    ) -> bool:
        """Retry only the durable terminal report after local adoption finished."""

        raw_decision = claim.get("attachment_strategy")
        decision = (
            dict(raw_decision)
            if isinstance(raw_decision, Mapping)
            else self._attachment_strategy_decision(workflow)
        )
        reporting_unavailable = claim.get("reporting_unavailable") is True
        receipt = workflow.get("save_receipt")
        configuration = (
            self._attachment_reporting_unavailable_configuration(decision)
            if reporting_unavailable
            else self._attachment_configuration_report(
                receipt,
                decision,
                stage="completed",
            )
            if isinstance(receipt, Mapping)
            else None
        )
        acknowledgement = claim.get("completion_acknowledgement")
        if not isinstance(acknowledgement, Mapping):
            acknowledgement = claim.get("evidence")
        if not isinstance(acknowledgement, Mapping):
            return False
        attachment_mode = str(
            decision.get("attachment_mode") or "observation_only"
        )
        completed = self._supervisor.transition_battle_workflow(
            str(workflow.get("request_id") or ""),
            "completed",
            reason=(
                "the active battle was adopted for degraded observation; its "
                "durable reconciliation report was unavailable"
                if reporting_unavailable
                else "the snapshotted selected Strategy was compatible and adopted "
                "without repairing the active battle"
                if attachment_mode == "strategy"
                else "the active battle was adopted for intentional observation"
                if decision.get("applicability") == "intentional_observation"
                else "the active battle was adopted for degraded observation "
                "because Strategy applicability was incompatible or unverifiable"
            ),
            acknowledgement=acknowledgement,
            configuration=configuration,
        )
        if completed is None or completed.get("status") != "completed":
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.REPORTING_FAILURE,
                "Attach terminal workflow reporting remains pending; local "
                "adoption and automation continue",
            )
            return False
        if not reporting_unavailable:
            self._mission_mgr.resolve_running_configuration_degradation(
                source="attachment_reporting",
                failed_checks=("workflow_reporting",),
                detail_keys=("reporting_status",),
            )
        self._running_reconciliation_claims().pop(
            str(workflow.get("request_id") or ""),
            None,
        )
        return True

    def _mark_operator_battle_action_dispatched(self, launched: bool) -> bool:
        """Persist the boundary between an explicit Home tap and adoption."""

        workflow = self._supervisor.battle_workflow
        if not (
            launched is True
            and isinstance(workflow, Mapping)
            and (
                (
                    workflow.get("intent") == "start_battle"
                    and workflow.get("status") in {"acknowledged", "ready"}
                )
                or (
                    workflow.get("intent") == "attach_battle"
                    and workflow.get("status") == "validating_save"
                )
            )
        ):
            return False
        request_id = str(workflow.get("request_id") or "")
        if request_id in getattr(self, "_uncertain_lifecycle_actions", set()):
            return False
        current = self._current_control_workflow_evidence()
        launch_scope_id = self._current_run_scope_id()
        identity = self._current_control_request_identity()
        requested = validate_workflow_evidence(workflow.get("evidence"))
        if not (
            isinstance(current, Mapping)
            and launch_scope_id
            and isinstance(requested, Mapping)
            and all(
                current.get(field) not in {None, ""}
                for field in (
                    "runtime_id",
                    "pid",
                    "adb_target",
                    "target_generation",
                    "observation_id",
                )
            )
            and all(
                str(identity.get(field) or "")
                for field in ("state_request_id", "mode_request_id")
            )
            and all(
                current.get(field) == requested.get(field)
                for field in (
                    "runtime_id",
                    "pid",
                    "adb_target",
                    "target_generation",
                )
            )
        ):
            return False
        acknowledgement = {
            **dict(current),
            "activity_scope_run_id": str(launch_scope_id),
            "state_request_id": str(identity["state_request_id"]),
            "mode_request_id": str(identity["mode_request_id"]),
        }
        if validate_workflow_evidence(acknowledgement) is None:
            return False
        was_modal_retry = bool(
            workflow.get("intent") == "start_battle"
            and workflow.get("status") == "ready"
            and self._battle_workflow_recovery_key(workflow)
            in self._free_ticket_recovery_cleared_set()
        )
        transitioned = self._supervisor.transition_battle_workflow(
            request_id,
            "action_dispatched",
            reason=(
                "the exact verified Home battle control was dispatched; "
                "battle lifecycle adoption is pending"
            ),
            acknowledgement=acknowledgement,
        )
        marked = bool(
            transitioned is not None
            and transitioned.get("status") == "action_dispatched"
        )
        if marked and was_modal_retry:
            recovery_key = self._battle_workflow_recovery_key(workflow)
            self._free_ticket_recovery_cleared_set().discard(recovery_key)
            getattr(
                self,
                "_free_ticket_retry_home_candidates",
                {},
            ).pop(recovery_key, None)
        return marked

    def _home_launch_authority_matches(
        self,
        *,
        source: str,
        request_id: str,
        home_control: HomeBattleControl,
    ) -> bool:
        """Revalidate the exact Home launch owner at the input boundary."""

        runtime_authorized = self._runtime_action_guard(
            action_class=RuntimeActionClass.LIFECYCLE_ACTION,
            owner=(
                AuthorityHold.EMULATOR_MAINTENANCE
                if source == "emulator_recovery"
                else None
            ),
        )
        workflow = self._supervisor.battle_workflow
        workflow_active = bool(
            isinstance(workflow, Mapping)
            and workflow.get("status")
            not in BATTLE_WORKFLOW_TERMINAL_STATUSES
        )
        manual = self._supervisor.manual_control
        manual_active = bool(
            isinstance(manual, Mapping)
            and manual.get("status")
            not in MANUAL_CONTROL_TERMINAL_STATUSES
        )
        if request_id and request_id in getattr(
            self,
            "_uncertain_lifecycle_actions",
            set(),
        ):
            return False

        if source == "terminal_continuation":
            if workflow_active or manual_active:
                self._clear_terminal_home_continuation(
                    "an explicit operator workflow superseded it before dispatch"
                )
                return False
            continuation_ready = self._terminal_home_continuation_ready(
                home_control=home_control
            )
            return bool(runtime_authorized and continuation_ready)
        if not runtime_authorized:
            return False
        if source == "start_battle":
            return bool(
                request_id
                and not manual_active
                and workflow_active
                and str(workflow.get("request_id") or "") == request_id
                and workflow.get("intent") == "start_battle"
                and workflow.get("status") in {"acknowledged", "ready"}
                and not self._awaiting_initial_battle_intent()
                and home_control is HomeBattleControl.NEW_BATTLE
            )
        if source == "attach_battle":
            return bool(
                request_id
                and not manual_active
                and workflow_active
                and str(workflow.get("request_id") or "") == request_id
                and workflow.get("intent") == "attach_battle"
                and workflow.get("status") == "validating_save"
                and home_control is HomeBattleControl.RESUME_BATTLE
            )
        if source == "manual_return":
            return bool(
                request_id
                and not workflow_active
                and manual_active
                and str(manual.get("manual_control_id") or "") == request_id
                and manual.get("status") == "reconciling"
                and home_control is HomeBattleControl.RESUME_BATTLE
            )
        if source == "emulator_recovery":
            maintenance = self._supervisor.emulator_maintenance
            pending_home_dispatch = getattr(
                self,
                "_emulator_recovery_home_dispatch",
                None,
            )
            return bool(
                runtime_authorized
                and request_id
                and not workflow_active
                and not manual_active
                and self._emulator_maintenance_hold_active
                and isinstance(maintenance, Mapping)
                and maintenance.get("state") == "host_restarted"
                and str(maintenance.get("request_id") or "") == request_id
                and not isinstance(pending_home_dispatch, Mapping)
                and int(
                    getattr(self, "_emulator_recovery_home_attempts", 0)
                )
                < 2
                and home_control
                in {
                    HomeBattleControl.NEW_BATTLE,
                    HomeBattleControl.RESUME_BATTLE,
                }
                and (
                    home_control is HomeBattleControl.RESUME_BATTLE
                    or self._emulator_recovery_force_new_battle
                )
            )
        if source == "legacy_auto_start":
            return bool(
                not workflow_active
                and not manual_active
                and not getattr(
                    self,
                    "_operator_battle_intent_required",
                    False,
                )
                and not self._awaiting_initial_battle_intent()
                and self._auto_start_enabled
                and AUTOMATION.mode is ExecMode.NEXT_BATTLE
                and self._legacy_home_launch_epoch(home_control)
                not in getattr(
                    self,
                    "_uncertain_legacy_home_launch_epochs",
                    set(),
                )
            )
        return False

    def _legacy_home_launch_epoch(
        self,
        home_control: HomeBattleControl,
    ) -> tuple[object, ...]:
        """Bind an unmanaged Home launch to material runtime UI identity."""

        try:
            current = self._current_control_workflow_evidence()
        except Exception:
            current = getattr(self, "_control_observation", None)
        if not isinstance(current, Mapping):
            current = {}
        return (
            current.get("runtime_id"),
            current.get("pid"),
            current.get("adb_target"),
            current.get("target_generation"),
            current.get("activity_scope_run_id"),
        )

    def _same_owner_free_ticket_retry_ready(
        self,
        *,
        source: Optional[str],
        request_id: str,
    ) -> bool:
        """Retain carried save evidence only across the exact modal retry."""

        claim = getattr(self, "_terminal_home_continuation", None)
        if isinstance(claim, Mapping):
            phase = str(claim.get("phase") or "")
            if (
                phase in {"action_dispatched", "retry_ready"}
                and (
                    phase == "retry_ready"
                    or claim.get("modal_recovery_completed") is True
                )
                and self._terminal_home_continuation_owner_current()
            ):
                return True
        workflow = getattr(self._supervisor, "battle_workflow", None)
        if not (
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "start_battle"
            and workflow.get("status") in {"action_dispatched", "ready"}
            and (
                not request_id
                or str(workflow.get("request_id") or "") == request_id
            )
            and self._battle_workflow_recovery_key(workflow)
            in self._free_ticket_recovery_cleared_set()
        ):
            return False
        if workflow.get("status") == "ready":
            return True
        current = self._current_control_workflow_evidence()
        return bool(
            isinstance(current, Mapping)
            and self._workflow_dispatch_receipt_mismatch(workflow, current)
            is None
        )

    def _complete_started_battle_workflow(
        self,
        battle_started: bool,
        detection: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        """Complete Start after its launch becomes a run, retrying reporting."""

        workflow = self._supervisor.battle_workflow
        retained = getattr(
            self,
            "_started_battle_workflow_completion",
            None,
        )
        if (
            retained is None
            and battle_started is True
            and not self._tournament_battle_guard(detection or {})
            and isinstance(workflow, Mapping)
            and workflow.get("intent") == "start_battle"
            and workflow.get("status") == "action_dispatched"
        ):
            retained = {
                "request_id": str(workflow.get("request_id") or ""),
                "acknowledgement": (
                    self._current_control_workflow_evidence() or {}
                ),
                "semantic_completion_applied": True,
            }
            self._started_battle_workflow_completion = retained
        if not isinstance(retained, Mapping):
            return False
        request_id = str(retained.get("request_id") or "")
        if not (
            request_id
            and isinstance(workflow, Mapping)
            and str(workflow.get("request_id") or "") == request_id
        ):
            self._started_battle_workflow_completion = None
            return False
        if workflow.get("status") == "completed":
            self._started_battle_workflow_completion = None
            return True
        if workflow.get("status") != "action_dispatched":
            self._started_battle_workflow_completion = None
            return False
        completed = self._supervisor.transition_battle_workflow(
            request_id,
            "completed",
            reason=(
                "verified new-run Home launch crossed the normal battle "
                "lifecycle boundary"
            ),
            acknowledgement=dict(retained.get("acknowledgement") or {}),
        )
        if completed is None or completed.get("status") != "completed":
            return False
        self._started_battle_workflow_completion = None
        return True

    def _bind_started_battle_player_save_preflight(
        self,
        *,
        battle_started: bool,
        stable_running: bool,
    ) -> bool:
        """Bind carried save facts across an exact observed battle transition."""

        coordinator = getattr(
            self,
            "_player_save_preflight_coordinator",
            None,
        )
        if coordinator is None:
            return False
        carry = coordinator.carry
        if carry is None:
            return False
        if AUTOMATION.state is RunState.STOPPED:
            coordinator.discard_carry("automation_stopped_before_running_bind")
            return False
        if AUTOMATION.state is not RunState.RUNNING:
            coordinator.suspend_carry("pause_requires_fresh_running_evidence")
            return False
        if self._operator_workflow_authority_hold() is not None:
            coordinator.discard_carry("competing_workflow_at_running_boundary")
            return False

        # Binding is observation, not input authority. In particular, WAIT is
        # only the policy for the next terminal screen, and initialization or
        # session-preflight holds are expected consumers of this evidence.
        bound = coordinator.bind_running(
            battle_started=battle_started,
            stable_running=stable_running,
            continuity_verified=True,
        )
        if bound:
            self._mission_mgr.ctx.data[
                "player_save_preflight_coordinator"
            ] = coordinator
        return bool(bound)

    def _stage_direct_retry_player_save_preflight(
        self,
        acquisition: Optional[PlayerSaveAcquisitionBundle],
        *,
        source_activity_scope_id: str,
        retry_scope: Mapping[str, Any],
    ) -> bool:
        """Bind one natural terminal save to its verified Retry successor."""

        coordinator = getattr(
            self,
            "_player_save_preflight_coordinator",
            None,
        )
        successor_scope_id = str(retry_scope.get("run_id") or "").strip()
        if (
            coordinator is None
            or not successor_scope_id
            or str(retry_scope.get("reason") or "") != "game_over_retry"
        ):
            if coordinator is not None:
                coordinator.discard_carry(
                    "direct_retry_successor_scope_unverified"
                )
            self._mission_mgr.ctx.data.pop(
                "player_save_preflight_coordinator",
                None,
            )
            return False
        self._player_save_preflight_session_id = new_operation_id()
        try:
            strategy = self._mission_mgr.strategy
            requirements = (
                strategy.session_preflight_requirements()
                if strategy is not None
                else {}
            )
            if not isinstance(requirements, Mapping):
                requirements = {}
            result = coordinator.stage_direct_retry(
                acquisition,
                requirements,
                source_activity_scope_id=source_activity_scope_id,
                mode=self._runtime_policy().get(
                    "player_save_preflight",
                    "save_first",
                ),
            )
        except Exception:
            coordinator.discard_carry("direct_retry_save_staging_failed")
            self._mission_mgr.ctx.data.pop(
                "player_save_preflight_coordinator",
                None,
            )
            log(
                "[PLAYER_SAVE_PREFLIGHT] Direct-Retry evidence staging failed; "
                "the verified Retry remains complete and configuration checks "
                "will use their guarded UI fallbacks",
                "ERROR",
            )
            return False
        self._player_save_preflight_result = result
        self._player_save_preflight_activity_scope_id = successor_scope_id
        if result.carry is None:
            self._mission_mgr.ctx.data.pop(
                "player_save_preflight_coordinator",
                None,
            )
            return False
        return True

    def _operator_workflow_authority_hold(
        self,
    ) -> Optional[AuthorityHoldState]:
        """Return the exclusive hold imposed by an unfinished operator handoff."""

        if self._supervisor.manual_control_error is True:
            return AuthorityHoldState(
                AuthorityHold.MANUAL_CONTROL_RETURN,
                "malformed manual-control authority is blocking all input",
            )
        if getattr(
            self._supervisor,
            "interactive_development_lease_error",
            False,
        ) is True:
            return AuthorityHoldState(
                AuthorityHold.EXTERNAL_DEVELOPMENT,
                "malformed interactive-development authority is blocking all input",
            )
        if self._supervisor.battle_workflow_error is True:
            return AuthorityHoldState(
                AuthorityHold.OPERATOR_WORKFLOW,
                "malformed battle-workflow authority is blocking all input",
            )
        if self._supervisor.setup_capture_error is True:
            return AuthorityHoldState(
                AuthorityHold.SETUP_CAPTURE,
                "malformed setup-capture authority is blocking all input",
            )
        capture = self._supervisor.setup_capture
        if isinstance(capture, Mapping) and capture.get("status") in {
            "requested",
            "acknowledged",
            "capturing",
        }:
            return AuthorityHoldState(
                AuthorityHold.SETUP_CAPTURE,
                "save-backed setup capture owns the Android lifecycle boundary",
            )
        manual = self._supervisor.manual_control
        if (
            isinstance(manual, Mapping)
            and manual.get("status") not in MANUAL_CONTROL_TERMINAL_STATUSES
        ):
            manual_id = str(manual.get("manual_control_id") or "")
            return_claim = self._pending_return_reconciliation_claims().get(
                manual_id
            )
            terminal_claim = self._manual_terminal_claims().get(manual_id)
            if any(
                isinstance(claim, Mapping)
                and claim.get("semantic_completion_applied") is True
                for claim in (return_claim, terminal_claim)
            ):
                # Reconciliation/terminal UI work is already complete.  A
                # failed durable receipt remains retryable reporting only and
                # cannot retain global device-input authority.
                return None
            manual_status = str(manual.get("status") or "unknown")
            return AuthorityHoldState(
                AuthorityHold.MANUAL_CONTROL_RETURN,
                (
                    "Return Control owns fresh save and configuration reconciliation"
                    if manual_status == "reconciling"
                    else "manual-control handoff blocks automated device input "
                    f"while status is {manual_status}"
                ),
            )
        continuation = getattr(self, "_terminal_home_continuation", None)
        if (
            isinstance(continuation, Mapping)
            and str(continuation.get("phase") or "armed")
            in {"action_dispatched", "retry_ready"}
        ):
            return AuthorityHoldState(
                AuthorityHold.OPERATOR_WORKFLOW,
                "a retained terminal launch is awaiting fresh battle evidence",
            )
        workflow = self._supervisor.battle_workflow
        started_completion = getattr(
            self,
            "_started_battle_workflow_completion",
            None,
        )
        if (
            isinstance(started_completion, Mapping)
            and started_completion.get("semantic_completion_applied") is True
            and isinstance(workflow, Mapping)
            and workflow.get("intent") == "start_battle"
            and workflow.get("status") == "action_dispatched"
            and str(workflow.get("request_id") or "")
            == str(started_completion.get("request_id") or "")
        ):
            # The physical new-run boundary is already consumed. A transient
            # completed-receipt write is reporting-only and cannot retain the
            # operator input hold over the newly active battle.
            return None
        if self._awaiting_initial_battle_intent():
            workflow_active = bool(
                isinstance(workflow, Mapping)
                and workflow.get("status")
                in {
                    "requested",
                    "awaiting_enable",
                    "acknowledged",
                    "validating_save",
                    "awaiting_configuration",
                    "ready",
                    "action_dispatched",
                }
            )
            return AuthorityHoldState(
                AuthorityHold.OPERATOR_WORKFLOW,
                "runtime is waiting for explicit Start Battle or Attach to Battle intent",
                allowed_auxiliary_collectors=(
                    ()
                    if workflow_active
                    else (AuxiliaryCollector.HOME_AD_GEM,)
                ),
            )
        attachment_claim = (
            self._running_reconciliation_claims().get(
                str(workflow.get("request_id") or "")
            )
            if isinstance(workflow, Mapping)
            else None
        )
        if (
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "attach_battle"
            and workflow.get("status")
            in {"validating_save", "action_dispatched", "ready"}
            and isinstance(attachment_claim, Mapping)
            and (
                self._mission_mgr.active_battle_observed()
                or attachment_claim.get("semantic_completion_applied") is True
            )
        ):
            # Exact process-local evidence already authorized lifecycle
            # adoption. A durable status write may keep retrying, but reporting
            # is recoverable and cannot retain the global input hold.
            return None
        if isinstance(workflow, Mapping) and workflow.get("status") in {
            "requested",
            "awaiting_enable",
            "acknowledged",
            "validating_save",
            "awaiting_configuration",
            "ready",
            "action_dispatched",
        }:
            return AuthorityHoldState(
                AuthorityHold.OPERATOR_WORKFLOW,
                (
                    "explicit attachment owns validation and configuration "
                    "until battle adoption is authorized"
                    if workflow.get("intent") == "attach_battle"
                    else "explicit battle intent is awaiting runtime acknowledgement"
                ),
            )
        return None

    @staticmethod
    def _heartbeat_action_authority_holds(
        *,
        operator_workflow_hold: Optional[AuthorityHoldState],
        continuity_pending: bool,
        exclusive_validation_terminal_finalization_pending: bool,
        exclusive_validation_passive_battle_hold: bool,
        exclusive_validation_ownership_hold: bool,
        exclusive_validation_in_progress: bool,
        exclusive_validation_launch_in_progress: bool,
        initialization_pending: bool,
        session_preflight_pending: bool,
    ) -> tuple[AuthorityHoldState, ...]:
        """Select the heartbeat's single typed owner in priority order."""

        if exclusive_validation_terminal_finalization_pending:
            return (
                AuthorityHoldState(
                    AuthorityHold.EXCLUSIVE_VALIDATION,
                    "exclusive validation is finalizing its exact receipt",
                ),
            )
        if exclusive_validation_passive_battle_hold:
            return (
                AuthorityHoldState(
                    AuthorityHold.EXCLUSIVE_OWNERSHIP,
                    "an inconclusive validation battle remains passive",
                ),
            )
        if operator_workflow_hold is not None:
            return (operator_workflow_hold,)
        if continuity_pending:
            return (
                AuthorityHoldState(
                    AuthorityHold.ACTIVITY_CONTINUITY,
                    "activity continuity owns its verification route",
                ),
            )
        if exclusive_validation_ownership_hold:
            return (
                AuthorityHoldState(
                    AuthorityHold.EXCLUSIVE_OWNERSHIP,
                    "exclusive validation ownership is unresolved",
                ),
            )
        if (
            exclusive_validation_in_progress
            or exclusive_validation_launch_in_progress
        ):
            return (
                AuthorityHoldState(
                    AuthorityHold.EXCLUSIVE_VALIDATION,
                    (
                        "operator-confirmed Tournament launch owns the screen"
                        if exclusive_validation_launch_in_progress
                        else "exclusive strategy validation owns the screen"
                    ),
                ),
            )
        if initialization_pending:
            return (
                AuthorityHoldState(
                    AuthorityHold.RUN_INITIALIZATION,
                    "run initialization owns strategy input",
                ),
            )
        if session_preflight_pending:
            return (
                AuthorityHoldState(
                    AuthorityHold.SESSION_PREFLIGHT,
                    "session preflight owns strategy validation input",
                ),
            )
        return ()

    def _awaiting_initial_battle_intent(self) -> bool:
        manager = getattr(self, "_mission_mgr", None)
        method = getattr(manager, "awaiting_initial_battle_intent", None)
        if not callable(method):
            return False
        try:
            value = method()
        except Exception:
            return False
        return value is True

    def _preserved_game_over_recovery_allowed(
        self,
        state: str,
        *,
        owner: Optional[AuthorityHold],
    ) -> bool:
        """Allow only the documented WAIT-bound terminal replacement route."""

        if (
            str(state or "").upper() != "GAME_OVER"
            or owner is not AuthorityHold.OPERATOR_WORKFLOW
            or AUTOMATION.mode is not ExecMode.WAIT
            or not self._awaiting_initial_battle_intent()
        ):
            return False
        supervisor = getattr(self, "_supervisor", None)
        workflow = getattr(supervisor, "battle_workflow", None)
        if (
            isinstance(workflow, Mapping)
            and workflow.get("status")
            not in BATTLE_WORKFLOW_TERMINAL_STATUSES
        ):
            return False
        manual = getattr(supervisor, "manual_control", None)
        if (
            isinstance(manual, Mapping)
            and manual.get("status") not in MANUAL_CONTROL_TERMINAL_STATUSES
        ):
            return False
        evidence = self._current_control_workflow_evidence()
        return bool(
            isinstance(evidence, Mapping)
            and evidence.get("game_state") == "game_over"
            and str(evidence.get("runtime_id") or "").strip()
            and type(evidence.get("pid")) is int
            and int(evidence["pid"]) > 0
            and str(evidence.get("adb_target") or "").strip()
            and type(evidence.get("target_generation")) is int
            and int(evidence["target_generation"]) > 0
            and str(evidence.get("activity_scope_run_id") or "").strip()
        )

    @staticmethod
    def _interactive_development_timestamp(
        now: Optional[float] = None,
    ) -> str:
        value = time.time() if now is None else float(now)
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().isoformat(
            timespec="seconds"
        )

    @staticmethod
    def _interactive_development_timestamp_value(value: object) -> float:
        parsed = datetime.fromisoformat(str(value or ""))
        if parsed.tzinfo is None:
            raise ValueError("interactive development timestamp has no timezone")
        return parsed.timestamp()

    def _interactive_development_control_state(self) -> str:
        supervisor = getattr(self, "_supervisor", None)
        state = getattr(supervisor, "control_state", None)
        if isinstance(state, str):
            return state.strip().upper()
        runtime_state = getattr(AUTOMATION, "state", None)
        return str(getattr(runtime_state, "value", runtime_state)).strip().upper()

    def _interactive_development_runtime_owner(self) -> Dict[str, object]:
        supervisor = getattr(self, "_supervisor", None)
        owner_fn = getattr(supervisor, "current_exclusive_validation_owner", None)
        if not callable(owner_fn):
            return {"runtime_id": "", "pid": 0, "adb_target": "unknown"}
        return dict(owner_fn())

    def _interactive_development_binding_error(
        self,
        lease: Mapping[str, object],
    ) -> Optional[str]:
        expected = lease.get("runtime")
        if not isinstance(expected, Mapping):
            return "the request has no valid production runtime binding"
        actual = self._interactive_development_runtime_owner()
        if str(expected.get("runtime_id") or "") != str(
            actual.get("runtime_id") or ""
        ):
            return "the production runtime/session changed"
        if expected.get("pid") != actual.get("pid"):
            return "the production runtime PID changed"
        if str(expected.get("adb_target") or "") != str(
            actual.get("adb_target") or ""
        ):
            return "the production ADB target changed"
        return None

    def _interactive_development_expired(
        self,
        lease: Mapping[str, object],
        *,
        now: Optional[float] = None,
    ) -> bool:
        current = time.time() if now is None else float(now)
        try:
            return current >= self._interactive_development_timestamp_value(
                lease.get("expires_at")
            )
        except (TypeError, ValueError):
            return True

    def _set_interactive_development_ack(
        self,
        lease: Mapping[str, object],
        *,
        state: str,
        now: Optional[float] = None,
        reason: Optional[str] = None,
        starting_evidence: Optional[Mapping[str, object]] = None,
        owned_battle_evidence: Optional[Mapping[str, object]] = None,
        terminal_evidence: Optional[Mapping[str, object]] = None,
    ) -> Dict[str, Any]:
        lease_id = str(lease.get("lease_id") or "")
        existing = getattr(self, "_interactive_development_ack", None)
        if not isinstance(existing, Mapping) or existing.get("lease_id") != lease_id:
            existing = {}
        timestamp = self._interactive_development_timestamp(now)
        acknowledgement: Dict[str, Any] = {
            "schema_version": 1,
            "lease_id": lease_id,
            "owner_label": str(lease.get("owner_label") or ""),
            "state": state,
            "requested_at": lease.get("requested_at"),
            "heartbeat_at": lease.get("heartbeat_at"),
            "expires_at": lease.get("expires_at"),
            "runtime": self._interactive_development_runtime_owner(),
            "updated_at": timestamp,
        }
        if lease.get("owned_battle_start") is True:
            acknowledgement["owned_battle_start"] = True
        for name in (
            "hold_installed_at",
            "acknowledged_at",
            "activated_at",
            "starting_evidence",
            "owned_battle_evidence",
        ):
            if existing.get(name) is not None:
                acknowledgement[name] = existing[name]
        if state in {
            "pending",
            "release_pending",
            "release_blocked",
            "expiry_pending",
            "termination_blocked",
        }:
            acknowledgement.setdefault("hold_installed_at", timestamp)
        if starting_evidence is not None:
            acknowledgement["starting_evidence"] = dict(starting_evidence)
        if owned_battle_evidence is not None:
            acknowledgement["owned_battle_evidence"] = dict(
                owned_battle_evidence
            )
        if state == "active":
            acknowledgement.setdefault("hold_installed_at", timestamp)
            acknowledgement.setdefault("acknowledged_at", timestamp)
            acknowledgement.setdefault("activated_at", timestamp)
        if lease.get("release_requested_at") is not None:
            acknowledgement["release_requested_at"] = lease.get(
                "release_requested_at"
            )
        if reason:
            acknowledgement["reason"] = " ".join(str(reason).split())[:256]
        if state == "terminal":
            for name in (
                "terminal_at",
                "terminal_disposition",
                "terminal_reason",
            ):
                if lease.get(name) is not None:
                    acknowledgement[name] = lease[name]
            if terminal_evidence is not None:
                acknowledgement["terminal_evidence"] = dict(terminal_evidence)
        self._interactive_development_ack = acknowledgement
        return acknowledgement

    def _install_external_development_hold(
        self,
        lease: Mapping[str, object],
        *,
        now: Optional[float] = None,
    ) -> None:
        # A watchdog recovery that passed its final authority check owns this
        # boundary until the mutation finishes.  Waiting here ensures the hold
        # and its pending acknowledgement never claim premature quiescence.
        with self._get_watchdog_mutation_guard().quiescence_boundary():
            previous = getattr(self, "_interactive_development_ack", None)
            newly_observed = not (
                isinstance(previous, Mapping)
                and previous.get("lease_id") == lease.get("lease_id")
            )
            self._external_development_hold_active = True
            self._set_interactive_development_ack(
                lease,
                state="pending",
                now=now,
                reason=(
                    "the suppressive production hold is installed; a fresh "
                    "observation and background-input quiescence are still required"
                ),
            )
            self._update_action_authority()
        stop_blind_gem_tapper()
        if newly_observed:
            log(
                "[INTERACTIVE_DEVELOPMENT] Lease request observed at a safe "
                f"runtime boundary: lease={lease.get('lease_id')} "
                f"owner={lease.get('owner_label')}",
                "INFO",
                console=True,
            )

    def _remove_external_development_hold(self) -> None:
        with self._get_watchdog_mutation_guard().quiescence_boundary():
            self._external_development_hold_active = False
            stop_blind_gem_tapper()
            self._update_action_authority()

    def _terminate_interactive_development_lease(
        self,
        lease: Mapping[str, object],
        *,
        disposition: str,
        reason: str,
        now: Optional[float] = None,
        terminal_evidence: Optional[Mapping[str, object]] = None,
        abnormal: bool = False,
        force_local_terminal: bool = False,
    ) -> bool:
        lease_id = str(lease.get("lease_id") or "")
        existing = getattr(self, "_interactive_development_ack", None)
        if (
            isinstance(existing, Mapping)
            and existing.get("lease_id") == lease_id
            and existing.get("state") == "terminal"
        ):
            return True
        supervisor = getattr(self, "_supervisor", None)
        finish = getattr(
            supervisor,
            "finish_interactive_development_lease",
            None,
        )
        persisted = (
            finish(
                lease_id,
                disposition=disposition,
                reason=reason,
                now=now,
            )
            if callable(finish)
            else None
        )
        if not isinstance(persisted, Mapping) and force_local_terminal:
            persisted = {
                **dict(lease),
                "request_state": "terminal",
                "terminal_at": self._interactive_development_timestamp(now),
                "terminal_disposition": disposition,
                "terminal_reason": reason,
            }
            log(
                "[INTERACTIVE_DEVELOPMENT] Operator control terminated the "
                "lease locally after its terminal record could not be persisted",
                "WARN",
                console=True,
            )
        elif not isinstance(persisted, Mapping):
            if getattr(self, "_external_development_hold_active", False):
                self._set_interactive_development_ack(
                    lease,
                    state="termination_blocked",
                    now=now,
                    reason=(
                        "the terminal lease state could not be persisted; "
                        "production input remains suppressed"
                    ),
                )
            return False
        self._set_interactive_development_ack(
            persisted,
            state="terminal",
            now=now,
            terminal_evidence=terminal_evidence,
        )
        self._remove_external_development_hold()
        if abnormal:
            log(
                "[INTERACTIVE_DEVELOPMENT] Lease ended abnormally: "
                f"lease={lease_id}; {reason}",
                "WARN",
                console=True,
            )
        log_result(
            "Interactive development lease ended — "
            f"{disposition.replace('_', ' ')}",
            detail=(
                f"[INTERACTIVE_DEVELOPMENT] lease_id={lease_id} "
                f"disposition={disposition} reason={reason}"
            ),
            console=True,
        )
        return True

    def _sync_interactive_development_control_boundary(
        self,
        *,
        now: Optional[float] = None,
    ) -> None:
        """Install or revoke the hold only between runtime input workflows."""

        supervisor = getattr(self, "_supervisor", None)
        lease = getattr(supervisor, "interactive_development_lease", None)
        if not isinstance(lease, Mapping):
            if getattr(self, "_external_development_hold_active", False):
                acknowledgement = getattr(
                    self,
                    "_interactive_development_ack",
                    {},
                )
                if isinstance(acknowledgement, Mapping):
                    control_state = self._interactive_development_control_state()
                    if control_state in {"PAUSED", "STOPPED"}:
                        self._terminate_interactive_development_lease(
                            acknowledgement,
                            disposition="revoked",
                            reason=f"operator control changed to {control_state}",
                            now=now,
                            force_local_terminal=True,
                        )
                        self._publish_action_authority()
                        return
                    self._set_interactive_development_ack(
                        acknowledgement,
                        state="termination_blocked",
                        now=now,
                        reason=(
                            "the lease directive is missing or malformed; "
                            "production input remains suppressed"
                        ),
                    )
                    self._publish_action_authority()
            return
        lease_id = str(lease.get("lease_id") or "")
        acknowledgement = getattr(self, "_interactive_development_ack", None)
        if (
            isinstance(acknowledgement, Mapping)
            and acknowledgement.get("lease_id") == lease_id
            and acknowledgement.get("state") == "terminal"
        ):
            return
        request_state = str(lease.get("request_state") or "")
        if request_state == "terminal":
            # A terminal directive is historical state, not authority owned by
            # this process. Preserve its recorded disposition before comparing
            # the request's former runtime binding with the replacement runtime.
            self._set_interactive_development_ack(
                lease,
                state="terminal",
                now=now,
            )
            self._remove_external_development_hold()
            self._publish_action_authority()
            return
        control_state = self._interactive_development_control_state()
        if control_state in {"PAUSED", "STOPPED"}:
            self._terminate_interactive_development_lease(
                lease,
                disposition="revoked",
                reason=f"operator control changed to {control_state}",
                now=now,
                force_local_terminal=True,
            )
            self._publish_action_authority()
            return
        binding_error = self._interactive_development_binding_error(lease)
        if binding_error:
            self._terminate_interactive_development_lease(
                lease,
                disposition="abnormal",
                reason=binding_error,
                now=now,
                abnormal=True,
            )
            self._publish_action_authority()
            return
        if not getattr(self, "_external_development_hold_active", False):
            self._install_external_development_hold(lease, now=now)
        if self._interactive_development_expired(lease, now=now):
            self._set_interactive_development_ack(
                lease,
                state="expiry_pending",
                now=now,
                reason=(
                    "the heartbeat expired; a fresh observation is required "
                    "before production input resumes"
                ),
            )
        elif request_state == "release_requested":
            previous_state = (
                acknowledgement.get("state")
                if isinstance(acknowledgement, Mapping)
                else None
            )
            self._set_interactive_development_ack(
                lease,
                state="release_pending",
                now=now,
                reason=(
                    "release was requested; production remains held until a "
                    "fresh post-release observation"
                ),
            )
            if previous_state != "release_pending":
                log(
                    "[INTERACTIVE_DEVELOPMENT] Release request observed; "
                    f"lease={lease_id} remains suppressive pending a fresh screen",
                    "INFO",
                    console=True,
                )
        else:
            current = getattr(self, "_interactive_development_ack", None)
            if isinstance(current, Mapping) and current.get("state") == "active":
                self._set_interactive_development_ack(
                    lease,
                    state="active",
                    now=now,
                )
        stop_blind_gem_tapper()
        initial_workflow_hold = self._operator_workflow_authority_hold()
        self._update_action_authority(
            holds=(initial_workflow_hold,) if initial_workflow_hold else (),
        )
        self._publish_action_authority()

    def _set_emulator_recovery_ack(
        self,
        maintenance: Mapping[str, object],
        *,
        state: str,
        reason: str,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Publish the runtime-owned half of one host maintenance request."""

        runtime = self._runtime_status_owner()
        control_identity = self._supervisor.control_request_identity
        runtime["state_request_id"] = control_identity.get("state_request_id")
        replay = getattr(self, "_emulator_replay_window", None)
        request_id = str(maintenance.get("request_id") or "")
        if not (
            isinstance(replay, RestartReplayWindow)
            and replay.request_id == request_id
        ):
            replay = None
        acknowledgement: Dict[str, Any] = {
            "schema_version": 1,
            "request_id": request_id,
            "state": str(state),
            "runtime": runtime,
            "battle_scope": (
                replay.battle_scope
                if isinstance(replay, RestartReplayWindow)
                else maintenance.get("battle_scope")
            ),
            "high_water_wave": (
                replay.high_water_wave
                if isinstance(replay, RestartReplayWindow)
                else self._last_wave_value
            ),
            "intro_sprint_active": bool(
                replay.intro_sprint_active
                if isinstance(replay, RestartReplayWindow)
                else self._activation_tracker().intro_sprint_active
            ),
            "replay_active": bool(
                replay.active if isinstance(replay, RestartReplayWindow) else False
            ),
            "exclude_from_degradation": bool(
                isinstance(replay, RestartReplayWindow)
            ),
            "reason": " ".join(str(reason or "").split())[:256],
            "observed_at": self._interactive_development_timestamp(now),
        }
        if isinstance(replay, RestartReplayWindow):
            acknowledgement.update(
                {
                    "expected_rollback_waves": replay.expected_rollback_waves,
                    "expected_floor": replay.expected_floor,
                    "lowest_observed_wave": replay.lowest_observed_wave,
                }
            )
        self._emulator_recovery_ack = acknowledgement
        return acknowledgement

    def _finish_emulator_recovery(
        self,
        maintenance: Mapping[str, object],
        *,
        disposition: str,
        reason: str,
        now: Optional[float] = None,
    ) -> bool:
        """Release restored input ownership and persist only its receipt."""

        request_id = str(maintenance.get("request_id") or "")
        self._emulator_recovery_request_id = request_id
        self._emulator_recovery_terminal_pending = {
            "request_id": request_id,
            "disposition": disposition,
            "reason": reason,
        }
        # The caller reaches this boundary only from fresh proof that the game
        # source is safe again.  Receipt persistence is reporting, not input
        # authority: it must never retain or reacquire the recovery hold.
        self._set_emulator_recovery_ack(
            maintenance,
            state="terminal",
            reason=(
                f"{reason}; durable terminal receipt is pending"
            ),
            now=now,
        )
        self._emulator_maintenance_hold_active = False
        self._emulator_recovery_force_new_battle = False
        self._emulator_recovery_home_dispatch = None
        self._emulator_recovery_next_action_at = 0.0
        self._update_action_authority()
        self._publish_action_authority()
        persisted = self._supervisor.finish_emulator_maintenance(
            request_id,
            disposition=disposition,
            reason=reason,
            now=now,
        )
        if isinstance(persisted, Mapping):
            self._emulator_recovery_terminal_pending = None
            self._set_emulator_recovery_ack(
                persisted,
                state="terminal",
                reason=reason,
                now=now,
            )
        else:
            report_failure = getattr(
                self,
                "_flag_recoverable_runtime_failure",
                None,
            )
            if callable(report_failure):
                report_failure(
                    RuntimeFailureKind.REPORTING_FAILURE,
                    "the BlueStacks recovery source was restored, but its "
                    "terminal receipt could not yet be persisted",
                )
        if getattr(self, "_emulator_recovery_action_logged", False):
            log_result(
                "BlueStacks recovery completed — "
                f"{disposition.replace('_', ' ')}",
                detail=(
                    f"[EMULATOR_RECOVERY] request_id={request_id} "
                    f"disposition={disposition} reason={reason}"
                ),
                operation_id=request_id,
                console=True,
            )
        self._emulator_recovery_action_logged = False
        return True

    def _sync_emulator_maintenance_control_boundary(
        self,
        *,
        now: Optional[float] = None,
    ) -> None:
        """Install the restart hold before capture or any ordinary handler."""

        maintenance = self._supervisor.emulator_maintenance
        if not isinstance(maintenance, Mapping):
            return
        request_id = str(maintenance.get("request_id") or "")
        state = str(maintenance.get("state") or "")
        pending_terminal = getattr(
            self,
            "_emulator_recovery_terminal_pending",
            None,
        )
        if (
            isinstance(pending_terminal, Mapping)
            and pending_terminal.get("request_id") == request_id
            and state != "terminal"
        ):
            persisted = self._supervisor.finish_emulator_maintenance(
                request_id,
                disposition=str(pending_terminal.get("disposition") or "restored"),
                reason=str(pending_terminal.get("reason") or "source restored"),
                now=now,
            )
            if isinstance(persisted, Mapping):
                self._emulator_recovery_terminal_pending = None
                self._set_emulator_recovery_ack(
                    persisted,
                    state="terminal",
                    reason=str(pending_terminal.get("reason") or "source restored"),
                    now=now,
                )
            else:
                self._set_emulator_recovery_ack(
                    maintenance,
                    state="terminal",
                    reason=(
                        "the game source is restored; only the durable terminal "
                        "receipt remains pending"
                    ),
                    now=now,
                )
            # Semantic completion is process-local and non-suppressive even
            # while the durable reporting retry remains outstanding.
            self._emulator_maintenance_hold_active = False
            return
        if state == "terminal":
            if (
                isinstance(pending_terminal, Mapping)
                and pending_terminal.get("request_id") == request_id
            ):
                self._emulator_recovery_terminal_pending = None
            self._emulator_recovery_durable_state = state
            if (
                self._emulator_recovery_request_id == request_id
                and self._emulator_maintenance_hold_active
            ):
                self._set_emulator_recovery_ack(
                    maintenance,
                    state="terminal",
                    reason=str(
                        maintenance.get("terminal_reason")
                        or "host maintenance ended"
                    ),
                    now=now,
                )
                self._emulator_maintenance_hold_active = False
                self._emulator_recovery_force_new_battle = False
                self._update_action_authority()
                self._publish_action_authority()
                if getattr(self, "_emulator_recovery_action_logged", False):
                    disposition = str(
                        maintenance.get("terminal_disposition") or "terminal"
                    )
                    reason = str(
                        maintenance.get("terminal_reason")
                        or "host maintenance ended"
                    )
                    log_result(
                        "BlueStacks recovery ended — "
                        f"{disposition.replace('_', ' ')}",
                        detail=(
                            f"[EMULATOR_RECOVERY] request_id={request_id} "
                            f"disposition={disposition} reason={reason}"
                        ),
                        operation_id=request_id,
                        console=True,
                    )
                    self._emulator_recovery_action_logged = False
            return
        if (
            state == "requested"
            and self._exclusive_validation_blocks_target_handoff()
        ):
            self._finish_emulator_recovery(
                maintenance,
                disposition="validation_authority_conflict",
                reason=(
                    "exclusive Tournament validation or its confirmed launch "
                    "acquired the exact runtime after this unacknowledged "
                    "maintenance request; no host mutation was authorized"
                ),
                now=now,
            )
            return
        runtime = maintenance.get("runtime")
        current_runtime = self._runtime_status_owner()
        current_runtime["state_request_id"] = (
            self._supervisor.control_request_identity.get("state_request_id")
        )
        owner_fields = ("runtime_id", "pid", "adb_target", "target_generation")
        owner_matches = bool(
            isinstance(runtime, Mapping)
            and all(runtime.get(field) == current_runtime.get(field) for field in owner_fields)
        )
        state_id_matches = bool(
            isinstance(runtime, Mapping)
            and runtime.get("state_request_id")
            == current_runtime.get("state_request_id")
        )
        if not owner_matches or (state == "requested" and not state_id_matches):
            if state == "requested":
                self._supervisor.finish_emulator_maintenance(
                    request_id,
                    disposition="runtime_replaced",
                    reason=(
                        "the exact runtime, target generation, or authorizing "
                        "Enable request changed before host acknowledgement"
                    ),
                    now=now,
                )
            else:
                target_changed = bool(
                    not isinstance(runtime, Mapping)
                    or runtime.get("adb_target") != current_runtime.get("adb_target")
                    or runtime.get("target_generation")
                    != current_runtime.get("target_generation")
                )
                self._supervisor.pause_for_catastrophic_failure(
                    RuntimeFailureKind.TARGET_OWNERSHIP_LOST
                    if target_changed
                    else RuntimeFailureKind.CONTROL_AUTHORITY_LOST,
                    reason=(
                        "the BlueStacks host was acknowledged, but its exact "
                        "runtime or target owner changed before game source "
                        "restoration"
                    ),
                )
                self._emulator_maintenance_hold_active = True
            return
        if state == "requested":
            requested_scope = str(
                maintenance.get("battle_scope") or ""
            ).strip()
            current_scope = self._current_run_scope_id()
            if not requested_scope or current_scope != requested_scope:
                self._finish_emulator_recovery(
                    maintenance,
                    disposition="battle_scope_replaced",
                    reason=(
                        "the active battle scope changed before Windows "
                        "acknowledged any host mutation"
                    ),
                    now=now,
                )
                return
            try:
                requested_at = datetime.fromisoformat(
                    str(maintenance.get("requested_at") or "")
                )
                if requested_at.tzinfo is None:
                    requested_at = requested_at.replace(tzinfo=timezone.utc)
                current_time = time.time() if now is None else float(now)
                request_age = current_time - requested_at.timestamp()
            except (TypeError, ValueError):
                request_age = 0.0
            if request_age >= EMULATOR_HOST_ACK_TIMEOUT_SECONDS:
                self._finish_emulator_recovery(
                    maintenance,
                    disposition="host_ack_timeout",
                    reason=(
                        "Windows did not acknowledge an exact BlueStacks "
                        "process before the three-minute pre-mutation timeout"
                    ),
                    now=now,
                )
                return
        if self._emulator_recovery_request_id != request_id:
            self._emulator_recovery_request_id = request_id
            self._emulator_recovery_durable_state = None
            self._emulator_replay_window = RestartReplayWindow(
                request_id=request_id,
                high_water_wave=self._last_wave_value,
                request_initiator=str(
                    maintenance.get("initiator") or "automatic_detector"
                ),
                battle_scope=str(maintenance.get("battle_scope") or "") or None,
                intro_sprint_active=(
                    self._activation_tracker().intro_sprint_active
                ),
            )
            self._emulator_recovery_resume_attempts = 0
            self._emulator_recovery_end_run_attempts = 0
            self._emulator_recovery_launch_attempts = 0
            self._emulator_recovery_launch_dispatched_at = None
            self._emulator_recovery_home_attempts = 0
            self._emulator_recovery_home_dispatch = None
            self._emulator_recovery_next_action_at = 0.0
            self._emulator_recovery_force_new_battle = False
            self._emulator_recovery_action_logged = False
        if not self._emulator_maintenance_hold_active:
            with self._get_watchdog_mutation_guard().quiescence_boundary():
                self._emulator_maintenance_hold_active = True
                stop_blind_gem_tapper()
        if getattr(self, "_emulator_recovery_durable_state", None) != state:
            phase = (
                "awaiting_adb"
                if state == "host_restarted"
                else "awaiting_host_restart"
                if state == "host_acknowledged"
                else "pending"
            )
            self._set_emulator_recovery_ack(
                maintenance,
                state=phase,
                reason=(
                    "replacement host process is ready; exact-target Android "
                    "recovery is pending"
                    if state == "host_restarted"
                    else "Windows owns the exact-instance restart"
                    if state == "host_acknowledged"
                    else "a fresh running frame is required before host mutation"
                ),
                now=now,
            )
            self._emulator_recovery_durable_state = state
        self._update_action_authority()
        self._publish_action_authority()

    def _advance_emulator_recovery(
        self,
        detection: Mapping[str, Any],
        img: Optional[Frame],
        *,
        now: Optional[float] = None,
    ) -> bool:
        """Advance one restart from fresh UI evidence before passive observers."""

        self._sync_emulator_maintenance_control_boundary(now=now)
        if not bool(
            getattr(self, "_emulator_maintenance_hold_active", False)
        ):
            return False
        maintenance = self._supervisor.emulator_maintenance
        if not isinstance(maintenance, Mapping):
            return True
        request_id = str(maintenance.get("request_id") or "")
        maintenance_state = str(maintenance.get("state") or "")
        state = str(detection.get("state") or "UNKNOWN").upper()
        self._update_action_authority(detection=detection)
        self._publish_action_authority()
        if self._supervisor.control_state != "RUNNING":
            self._set_emulator_recovery_ack(
                maintenance,
                state="pending",
                reason=(
                    f"operator {self._supervisor.control_state} takes precedence"
                ),
                now=now,
            )
            self._publish_action_authority()
            return True
        if maintenance_state == "requested":
            if state != "RUNNING":
                self._set_emulator_recovery_ack(
                    maintenance,
                    state="pending",
                    reason="a fresh RUNNING frame is required before restart",
                    now=now,
                )
                self._publish_action_authority()
                return True
            lifecycle_authorized = self._runtime_action_guard(
                action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                owner=AuthorityHold.EMULATOR_MAINTENANCE,
            )
            strategy_authorized = self._runtime_action_guard(
                action_class=RuntimeActionClass.STRATEGY_ACTION,
                owner=AuthorityHold.EMULATOR_MAINTENANCE,
            )
            if not lifecycle_authorized or not strategy_authorized:
                self._set_emulator_recovery_ack(
                    maintenance,
                    state="pending",
                    reason=(
                        "another runtime owner or operator control state blocks "
                        "the BlueStacks restart boundary"
                    ),
                    now=now,
                )
                self._publish_action_authority()
                return True
            if not self._emulator_recovery_action_logged:
                log_action_intent(
                    "Restarting the coordinated BlueStacks instance",
                    reason=str(
                        maintenance.get("reason")
                        or (
                            "operator requested BlueStacks restart"
                            if maintenance.get("initiator") == "operator"
                            else "degradation detected"
                        )
                    ),
                    detail=(
                        f"[EMULATOR_RECOVERY] request_id={request_id} "
                        f"initiator={maintenance.get('initiator') or 'automatic_detector'} "
                        f"battle_scope={self._current_run_scope_id() or 'unknown'} "
                        f"high_water_wave={self._last_wave_value}"
                    ),
                    operation_id=request_id,
                )
                self._emulator_recovery_action_logged = True
            self._set_emulator_recovery_ack(
                maintenance,
                state="host_restart_authorized",
                reason=(
                    "the suppressive runtime hold is installed on fresh "
                    "RUNNING evidence"
                ),
                now=now,
            )
            self._publish_action_authority()
            return True
        if maintenance_state == "host_acknowledged":
            self._set_emulator_recovery_ack(
                maintenance,
                state="awaiting_host_restart",
                reason="Windows acknowledged the exact old process identity",
                now=now,
            )
            self._publish_action_authority()
            return True
        if maintenance_state != "host_restarted":
            return True

        monotonic_now = time.monotonic()
        recovery_epoch = (
            request_id,
            *((maintenance.get("runtime") or {}).get(field) for field in (
                "runtime_id",
                "pid",
                "adb_target",
                "target_generation",
            )),
            maintenance.get("battle_scope"),
        )
        breaker = self._get_action_circuit_breaker()
        replay = self._emulator_replay_window
        if not isinstance(replay, RestartReplayWindow):
            replay = RestartReplayWindow(
                request_id,
                self._last_wave_value,
                request_initiator=str(
                    maintenance.get("initiator") or "automatic_detector"
                ),
                battle_scope=str(maintenance.get("battle_scope") or "") or None,
            )
            self._emulator_replay_window = replay

        home_dispatch = getattr(
            self,
            "_emulator_recovery_home_dispatch",
            None,
        )
        if isinstance(home_dispatch, dict):
            if str(home_dispatch.get("request_id") or "") != request_id:
                self._supervisor.pause_for_catastrophic_failure(
                    RuntimeFailureKind.TARGET_OWNERSHIP_LOST,
                    reason=(
                        "the retained Home recovery input belongs to a different "
                        "BlueStacks maintenance request"
                    ),
                )
                self._publish_action_authority()
                return True
            if state in {"RUNNING", "GAME_RESTARTED", "GAME_OVER"}:
                # These are authoritative post-input sources. Their normal
                # recovery branches below decide the next action.
                self._emulator_recovery_home_dispatch = None
            elif state == "HOME_SCREEN" and home_dispatch.get(
                "modal_recovery_completed"
            ):
                current = getattr(self, "_control_observation", None)
                observation_id = str(
                    (current or {}).get("observation_id")
                    if isinstance(current, Mapping)
                    else ""
                )
                candidate = str(
                    home_dispatch.get(
                        "retry_home_candidate_observation_id"
                    )
                    or ""
                )
                if not observation_id or not candidate:
                    if observation_id:
                        home_dispatch[
                            "retry_home_candidate_observation_id"
                        ] = observation_id
                    self._set_emulator_recovery_ack(
                        maintenance,
                        state="fallback_starting_battle",
                        reason=(
                            "Free Ticket cleared; a second stable Home "
                            "observation is required before one bounded retry"
                        ),
                        now=now,
                    )
                    self._publish_action_authority()
                    return True
                if candidate == observation_id:
                    return True
                if int(
                    getattr(self, "_emulator_recovery_home_attempts", 0)
                ) >= 2:
                    self._supervisor.pause_for_catastrophic_failure(
                        RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                        reason=(
                            "Free Ticket cleared after the final bounded Home "
                            "launch; no further replacement-battle input is safe"
                        ),
                    )
                    self._publish_action_authority()
                    return True
                self._emulator_recovery_home_dispatch = None
                self._set_emulator_recovery_ack(
                    maintenance,
                    state="fallback_starting_battle",
                    reason=(
                        "Free Ticket cleared and two stable Home observations "
                        "authorized the final verified launch retry"
                    ),
                    now=now,
                )
                self._publish_action_authority()
                return False
            else:
                elapsed = monotonic_now - float(
                    home_dispatch.get("dispatched_monotonic") or monotonic_now
                )
                control = str(home_dispatch.get("control") or "Home Battle")
                reason = (
                    f"accepted Home {control} input is awaiting fresh battle "
                    "or known-blocker evidence"
                )
                self._set_emulator_recovery_ack(
                    maintenance,
                    state=(
                        "resume_dispatched"
                        if control == HomeBattleControl.RESUME_BATTLE.value
                        else "fallback_starting_battle"
                    ),
                    reason=reason,
                    now=now,
                )
                if elapsed >= EMULATOR_HOME_POSTCONDITION_TIMEOUT_SECONDS:
                    uncertain = getattr(
                        self,
                        "_uncertain_lifecycle_actions",
                        None,
                    )
                    if uncertain is None:
                        uncertain = set()
                        self._uncertain_lifecycle_actions = uncertain
                    uncertain.add(request_id)
                    self._runtime_uncertain_mutation_result(
                        reason
                        + "; its outcome remained unknown for "
                        + str(EMULATOR_HOME_POSTCONDITION_TIMEOUT_SECONDS)
                        + " seconds, so the input will not be replayed"
                    )
                self._publish_action_authority()
                return True

        if state != "UNKNOWN":
            self._emulator_recovery_launch_dispatched_at = None

        if state == "RUNNING":
            if self._emulator_recovery_force_new_battle:
                return not self._finish_emulator_recovery(
                    maintenance,
                    disposition="fallback_new_battle",
                    reason=(
                        "the prior battle was not resumable and the configured "
                        "replacement battle reached fresh RUNNING evidence"
                    ),
                    now=now,
                )
            if not replay.resume_dispatched:
                replay.mark_resume_dispatched()
            try:
                observed_wave, observed_conf = detect_wave_number_from_image(img)
            except Exception:
                observed_wave, observed_conf = None, -1.0
            observation = replay.observe(observed_wave)
            if observation.caught_up:
                if observed_wave is not None:
                    self._last_wave_value = observed_wave
                    self._last_wave_conf = observed_conf
                    self._last_wave_ts = time.time()
                return not self._finish_emulator_recovery(
                    maintenance,
                    disposition="resumed",
                    reason=(
                        "the resumed battle reached its pre-restart wave "
                        "high-water"
                    ),
                    now=now,
                )
            self._set_emulator_recovery_ack(
                maintenance,
                state="replaying",
                reason=(
                    "The Tower is replaying its non-earning restart rollback "
                    "while independent collectors remain available and "
                    "run-progression observers remain suppressed"
                ),
                now=now,
            )
            # Keep replayed wave, Coins/min, perk-timeline, activation, and
            # strategy observations outside their normal owners.  The typed
            # independent-collector lane is safe on a fresh resumed RUNNING
            # frame and still rechecks Pause, battle scope, and route ownership
            # at every material input boundary.
            self._update_action_authority(detection=detection)
            self._publish_action_authority()
            self._handle_emulator_replay_auxiliary_actions(
                detection,
                img,
            )
            return True

        if state == "GAME_RESTARTED":
            action_guard = lambda: self._runtime_action_guard(
                action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                owner=AuthorityHold.EMULATOR_MAINTENANCE,
            )
            resume_action = "emulator_recovery_resume"
            if breaker.allows(resume_action, epoch=recovery_epoch):
                dispatch_outcome = handle_game_restarted(
                    img,
                    action=GameRestartedAction.RESUME,
                    action_guard_fn=action_guard,
                    capture_fn=self._capture_frame,
                    max_attempts=2,
                )
                self._emulator_recovery_resume_attempts += int(
                    dispatch_outcome.attempts
                )
                if dispatch_outcome.uncertain:
                    breaker.record_failure(
                        resume_action,
                        epoch=recovery_epoch,
                        reason=dispatch_outcome.reason,
                    )
                    self._runtime_uncertain_mutation_result(
                        "Welcome Back Resume input result was uncertain"
                    )
                elif dispatch_outcome.status is RecoveryUiDispatchStatus.RESOLVED:
                    breaker.record_success(resume_action, epoch=recovery_epoch)
                    replay.mark_resume_dispatched()
                    self._set_emulator_recovery_ack(
                        maintenance,
                        state="resume_dispatched",
                        reason=(
                            "Welcome Back Resume cleared the modal on fresh "
                            "post-input evidence"
                        ),
                        now=now,
                    )
                elif dispatch_outcome.status is RecoveryUiDispatchStatus.FAILED:
                    breaker.record_failure(
                        resume_action,
                        epoch=recovery_epoch,
                        reason=dispatch_outcome.reason,
                    )
                self._publish_action_authority()
                return True
            end_run_action = "emulator_recovery_end_run"
            if not breaker.allows(end_run_action, epoch=recovery_epoch):
                return True
            dispatch_outcome = handle_game_restarted(
                img,
                action=GameRestartedAction.END_RUN,
                action_guard_fn=action_guard,
                capture_fn=self._capture_frame,
                max_attempts=2,
            )
            self._emulator_recovery_end_run_attempts = int(
                getattr(self, "_emulator_recovery_end_run_attempts", 0)
            ) + int(dispatch_outcome.attempts)
            if dispatch_outcome.uncertain:
                breaker.record_failure(
                    end_run_action,
                    epoch=recovery_epoch,
                    reason=dispatch_outcome.reason,
                )
                self._runtime_uncertain_mutation_result(
                    "Welcome Back End run input result was uncertain"
                )
            elif dispatch_outcome.status is RecoveryUiDispatchStatus.RESOLVED:
                breaker.record_success(end_run_action, epoch=recovery_epoch)
                self._emulator_recovery_force_new_battle = True
                self._set_emulator_recovery_ack(
                    maintenance,
                    state="fallback_ending_run",
                    reason=(
                        "Welcome Back remained after bounded Resume attempts; "
                        "End run was dispatched for the configured fallback"
                    ),
                    now=now,
                )
            elif dispatch_outcome.status is RecoveryUiDispatchStatus.FAILED:
                breaker.record_failure(
                    end_run_action,
                    epoch=recovery_epoch,
                    reason=dispatch_outcome.reason,
                )
                self._supervisor.pause_for_catastrophic_failure(
                    RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                    reason=(
                        "Welcome Back remained after bounded Resume and End "
                        "run transactions; no further modal input is safe"
                    ),
                )
            self._publish_action_authority()
            return True

        home_control = HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        )
        if state == "HOME_SCREEN" and home_control in {
            HomeBattleControl.RESUME_BATTLE,
            HomeBattleControl.NEW_BATTLE,
        }:
            self._emulator_recovery_force_new_battle = bool(
                home_control is HomeBattleControl.NEW_BATTLE
            )
            return False
        if state == "GAME_OVER":
            self._emulator_recovery_force_new_battle = True
            return False

        launched_at = self._emulator_recovery_launch_dispatched_at
        if launched_at is not None:
            if monotonic_now - launched_at >= 45.0:
                self._supervisor.pause_for_catastrophic_failure(
                    RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                    reason=(
                        "The Tower launcher input was accepted, but no known "
                        "game source appeared within 45 seconds"
                    ),
                )
            self._set_emulator_recovery_ack(
                maintenance,
                state="awaiting_welcome_back",
                reason=(
                    "The Tower launch was accepted; fresh game UI is pending"
                ),
                now=now,
            )
            self._publish_action_authority()
            return True

        launch_action = "emulator_recovery_launch_game"
        if (
            monotonic_now >= self._emulator_recovery_next_action_at
            and breaker.allows(launch_action, epoch=recovery_epoch)
        ):
            action_guard = lambda: self._runtime_action_guard(
                action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                owner=AuthorityHold.EMULATOR_MAINTENANCE,
            )
            launch_outcome = bring_to_foreground(
                input_reason=f"emulator_recovery request_id={request_id}",
                action_guard_fn=action_guard,
                return_dispatch_outcome=True,
                defer_uncertain_reporting=True,
            )
            attempted = bool(getattr(launch_outcome, "attempted", False))
            accepted = bool(getattr(launch_outcome, "accepted", False))
            uncertain = bool(getattr(launch_outcome, "uncertain", False))
            if attempted:
                self._emulator_recovery_launch_attempts += 1
            if uncertain:
                breaker.record_failure(
                    launch_action,
                    epoch=recovery_epoch,
                    reason="The Tower launcher dispatch result was uncertain",
                )
                self._runtime_uncertain_mutation_result(
                    "The Tower launcher input result was uncertain after the "
                    "BlueStacks restart"
                )
            elif accepted:
                breaker.record_success(launch_action, epoch=recovery_epoch)
                self._emulator_recovery_launch_dispatched_at = monotonic_now
            elif self._emulator_recovery_launch_attempts >= 3:
                breaker.record_failure(
                    launch_action,
                    epoch=recovery_epoch,
                    reason="the bounded Android launcher transaction exhausted",
                )
                self._supervisor.pause_for_catastrophic_failure(
                    RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                    reason=(
                        "The Tower could not be launched after three bounded "
                        "exact-target attempts following the BlueStacks restart"
                    ),
                )
            self._set_emulator_recovery_ack(
                maintenance,
                state="awaiting_welcome_back" if accepted else "launching_game",
                reason=(
                    "The Tower launch was accepted; fresh game UI is pending"
                    if accepted
                    else "The Tower launch transaction is awaiting a safe retry"
                ),
                now=now,
            )
            self._emulator_recovery_next_action_at = monotonic_now + 15.0
            self._publish_action_authority()
        return True

    def _is_emulator_recovery_landscape_launcher_capture(
        self,
        result: object,
    ) -> bool:
        """Return whether one native frame belongs to the held launch phase."""

        if (
            result is None
            or getattr(result, "failure", None)
            is not ScreenshotFailure.UNSUPPORTED_GEOMETRY
            or getattr(result, "native_width", None) is None
            or getattr(result, "native_height", None) is None
        ):
            return False
        native_size = (int(result.native_width), int(result.native_height))
        if (native_size[1], native_size[0]) not in SUPPORTED_DEVICE_SCREEN_SIZES:
            return False
        if not bool(
            getattr(self, "_emulator_maintenance_hold_active", False)
        ):
            return False
        supervisor = getattr(self, "_supervisor", None)
        maintenance = getattr(supervisor, "emulator_maintenance", None)
        if not (
            isinstance(maintenance, Mapping)
            and str(maintenance.get("state") or "") == "host_restarted"
        ):
            return False
        runtime = maintenance.get("runtime")
        return bool(
            isinstance(runtime, Mapping)
            and str(result.adb_target or "")
            == str(runtime.get("adb_target") or "")
        )

    def _advance_emulator_recovery_from_landscape_launcher(self) -> bool:
        """Launch The Tower when BlueStacks Home cannot supply portrait UI."""

        result = getattr(self, "_last_screenshot_capture_result", None)
        if not self._is_emulator_recovery_landscape_launcher_capture(result):
            return False
        return self._advance_emulator_recovery(
            {"state": "UNKNOWN"},
            None,
        )

    @staticmethod
    def _interactive_development_evidence(
        detection: Mapping[str, object],
        *,
        battle_active: bool,
        battle_scope: Optional[str],
        observed_at: str,
        target_generation: Optional[int] = None,
    ) -> Dict[str, object]:
        evidence: Dict[str, object] = {
            "screen_state": str(detection.get("state") or "UNKNOWN").upper(),
            "battle_active": bool(battle_active),
            "battle_scope": battle_scope,
            "observed_at": observed_at,
        }
        home_control = str(
            detection.get("home_battle_control") or ""
        ).strip().upper()
        if home_control:
            evidence["home_battle_control"] = home_control
        if type(target_generation) is int and target_generation > 0:
            evidence["target_generation"] = target_generation
        return evidence

    @staticmethod
    def _interactive_development_owned_battle_evidence(
        lease: Mapping[str, object],
        acknowledgement: object,
        starting: Mapping[str, object],
        current: Mapping[str, object],
    ) -> Optional[Dict[str, object]]:
        """Retain only one explicitly preclaimed exact Home-to-run owner."""

        if lease.get("owned_battle_start") is not True:
            return None
        existing = (
            acknowledgement.get("owned_battle_evidence")
            if isinstance(acknowledgement, Mapping)
            else None
        )
        current_state = str(
            current.get("screen_state") or "UNKNOWN"
        ).upper()
        if current_state in {"UNKNOWN", "GAME_OVER", "TOURNAMENT_RESULTS"}:
            return None
        expected = existing if isinstance(existing, Mapping) else starting
        same_binding = bool(
            str(expected.get("battle_scope") or "")
            and expected.get("battle_scope") == current.get("battle_scope")
            and type(expected.get("target_generation")) is int
            and expected.get("target_generation")
            == current.get("target_generation")
        )
        if isinstance(existing, Mapping):
            if current.get("battle_active") is True and same_binding:
                return dict(existing)
            return None
        if not (
            str(starting.get("screen_state") or "").upper()
            == "HOME_SCREEN"
            and str(starting.get("home_battle_control") or "").upper()
            == "NEW_BATTLE"
            and starting.get("battle_active") is False
            and current_state == "RUNNING"
            and current.get("battle_active") is True
            and same_binding
        ):
            return None
        return dict(current)

    @staticmethod
    def _interactive_development_boundary_reason(
        starting: Mapping[str, object],
        current: Mapping[str, object],
    ) -> Optional[str]:
        current_state = str(current.get("screen_state") or "UNKNOWN").upper()
        if current_state in {"GAME_OVER", "TOURNAMENT_RESULTS"}:
            return f"authoritative terminal boundary {current_state} was observed"
        if bool(starting.get("battle_active")) != bool(
            current.get("battle_active")
        ):
            return "the authoritative running-battle boundary changed"
        starting_scope = str(starting.get("battle_scope") or "")
        current_scope = str(current.get("battle_scope") or "")
        if starting_scope and current_scope and starting_scope != current_scope:
            return "the authoritative battle/session identity changed"
        starting_generation = starting.get("target_generation")
        current_generation = current.get("target_generation")
        if (
            starting_generation is not None
            and starting_generation != current_generation
        ):
            return "the authoritative ADB target generation changed"
        return None

    def _matching_interactive_development_owned_terminal_claim(
        self,
        current: object,
    ) -> Optional[Dict[str, object]]:
        """Return one same-process preclaimed development terminal owner."""

        acknowledgement = getattr(
            self,
            "_interactive_development_ack",
            None,
        )
        if not (
            isinstance(current, Mapping)
            and current.get("game_state") == "game_over"
            and isinstance(acknowledgement, Mapping)
            and acknowledgement.get("state") == "terminal"
            and acknowledgement.get("owned_battle_start") is True
            and acknowledgement.get("terminal_disposition")
            == "natural_game_over"
            and self._interactive_development_control_state() == "RUNNING"
        ):
            return None
        runtime = acknowledgement.get("runtime")
        owned = acknowledgement.get("owned_battle_evidence")
        terminal = acknowledgement.get("terminal_evidence")
        if not (
            isinstance(runtime, Mapping)
            and isinstance(owned, Mapping)
            and isinstance(terminal, Mapping)
            and terminal.get("screen_state") == "GAME_OVER"
        ):
            return None
        if not all(
            runtime.get(source) == current.get(target)
            for source, target in (
                ("runtime_id", "runtime_id"),
                ("pid", "pid"),
                ("adb_target", "adb_target"),
            )
        ):
            return None
        scope_id = str(owned.get("battle_scope") or "")
        target_generation = owned.get("target_generation")
        if not (
            scope_id
            and type(target_generation) is int
            and target_generation > 0
            and scope_id == terminal.get("battle_scope")
            and scope_id == current.get("activity_scope_run_id")
            and target_generation == terminal.get("target_generation")
            and target_generation == current.get("target_generation")
        ):
            return None
        return {
            "schema_version": 1,
            "lease_id": str(acknowledgement.get("lease_id") or ""),
            "activity_scope_run_id": scope_id,
            "target_generation": target_generation,
        }

    def _sync_interactive_development_observation(
        self,
        detection: Mapping[str, object],
        *,
        now: Optional[float] = None,
    ) -> None:
        """Acknowledge, release, or terminate from one fresh detected frame."""

        self._sync_interactive_development_control_boundary(now=now)
        if not getattr(self, "_external_development_hold_active", False):
            return
        supervisor = getattr(self, "_supervisor", None)
        lease = getattr(supervisor, "interactive_development_lease", None)
        if not isinstance(lease, Mapping):
            return
        self._update_action_authority(detection=detection)
        observed_at = self._interactive_development_timestamp(now)
        battle_active = bool(getattr(self, "_authority_battle_active", False))
        battle_scope = self._current_run_scope_id()
        current_workflow = self._current_control_workflow_evidence()
        target_generation = (
            current_workflow.get("target_generation")
            if isinstance(current_workflow, Mapping)
            else None
        )
        evidence = self._interactive_development_evidence(
            detection,
            battle_active=battle_active,
            battle_scope=battle_scope,
            observed_at=observed_at,
            target_generation=target_generation,
        )
        acknowledgement = getattr(self, "_interactive_development_ack", None)
        starting = (
            acknowledgement.get("starting_evidence")
            if isinstance(acknowledgement, Mapping)
            else None
        )
        if not isinstance(starting, Mapping):
            candidate = lease.get("starting_evidence")
            starting = candidate if isinstance(candidate, Mapping) else {}
        owned_battle_evidence = (
            self._interactive_development_owned_battle_evidence(
                lease,
                acknowledgement,
                starting,
                evidence,
            )
        )
        boundary_reason = self._interactive_development_boundary_reason(
            starting,
            evidence,
        )
        if owned_battle_evidence is not None:
            boundary_reason = None
        if boundary_reason:
            disposition = (
                "natural_game_over"
                if evidence["screen_state"] == "GAME_OVER"
                else "battle_boundary"
            )
            self._terminate_interactive_development_lease(
                lease,
                disposition=disposition,
                reason=boundary_reason,
                now=now,
                terminal_evidence=evidence,
                abnormal=disposition != "natural_game_over",
            )
            self._publish_action_authority()
            return
        screen_state = str(evidence["screen_state"])
        if self._interactive_development_expired(lease, now=now):
            if screen_state == "UNKNOWN":
                self._set_interactive_development_ack(
                    lease,
                    state="termination_blocked",
                    now=now,
                    reason=(
                        "the heartbeat expired on an ambiguous screen; "
                        "production input remains suppressed"
                    ),
                )
            else:
                self._terminate_interactive_development_lease(
                    lease,
                    disposition="expired",
                    reason="the cooperative heartbeat deadline expired",
                    now=now,
                    terminal_evidence=evidence,
                )
            self._publish_action_authority()
            return
        if lease.get("request_state") == "release_requested":
            if screen_state == "UNKNOWN":
                previous_state = (
                    acknowledgement.get("state")
                    if isinstance(acknowledgement, Mapping)
                    else None
                )
                self._set_interactive_development_ack(
                    lease,
                    state="release_blocked",
                    now=now,
                    reason=(
                        "the post-release screen is ambiguous; production "
                        "input remains suppressed"
                    ),
                )
                if previous_state != "release_blocked":
                    log(
                        "[INTERACTIVE_DEVELOPMENT] Release remains held because "
                        "the fresh screen is ambiguous",
                        "WARN",
                        console=True,
                    )
            else:
                self._terminate_interactive_development_lease(
                    lease,
                    disposition="released",
                    reason="a fresh post-release observation was obtained",
                    now=now,
                    terminal_evidence=evidence,
                )
            self._publish_action_authority()
            return
        if screen_state == "UNKNOWN":
            self._set_interactive_development_ack(
                lease,
                state="pending",
                now=now,
                reason="a known starting screen is required before acknowledgement",
            )
            self._publish_action_authority()
            return
        stop_blind_gem_tapper()
        if is_blind_gem_tapper_active():
            self._set_interactive_development_ack(
                lease,
                state="pending",
                now=now,
                reason=(
                    "the background floating-gem input is stopping before "
                    "acknowledgement"
                ),
            )
            self._publish_action_authority()
            return
        previous_state = (
            acknowledgement.get("state")
            if isinstance(acknowledgement, Mapping)
            else None
        )
        if owned_battle_evidence is not None:
            self._observed_active_battle_scope_id = str(
                owned_battle_evidence.get("battle_scope") or ""
            ) or None
            self._last_unbound_terminal_signature = None
        self._set_interactive_development_ack(
            lease,
            state="active",
            now=now,
            starting_evidence=evidence,
            owned_battle_evidence=owned_battle_evidence,
        )
        if previous_state != "active":
            log(
                "[INTERACTIVE_DEVELOPMENT] Production acknowledged lease "
                f"{lease.get('lease_id')} after the suppressive hold was installed",
                "INFO",
                console=True,
            )
            log(
                "[INTERACTIVE_DEVELOPMENT] Lease activated: "
                f"owner={lease.get('owner_label')} screen={screen_state} "
                f"battle_scope={battle_scope or 'unavailable'}",
                "INFO",
                console=True,
            )
        self._publish_action_authority()

    def _action_decision(
        self,
        action_class: RuntimeActionClass,
        *,
        owner: Optional[AuthorityHold | str] = None,
        collector: Optional[AuxiliaryCollector] = None,
        route: Optional[AuxiliaryRouteLease] = None,
    ) -> ActionAuthorityDecision:
        owner_value = owner.value if isinstance(owner, AuthorityHold) else owner
        return self._get_action_authority().decision(
            action_class,
            owner=owner_value,
            collector=collector,
            route_id=route.route_id if route is not None else None,
        )

    def _run_owned_strategy_tick(
        self,
        owner: AuthorityHold,
        img: Frame,
        detection: Dict[str, Any],
        *,
        strategy_only: bool,
    ) -> None:
        """Run one exclusive gate tick with only that hold's input authority."""

        previous = getattr(self, "_active_action_authority_owner", None)
        self._active_action_authority_owner = owner
        try:
            self._mission_mgr.tick(
                img,
                detection,
                strategy_only=strategy_only,
            )
        finally:
            self._active_action_authority_owner = previous

    def _advance_owned_exclusive_validation(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Advance receipt lifecycle under its exact typed owner."""

        if not self._action_decision(
            RuntimeActionClass.LIFECYCLE_ACTION,
            owner=AuthorityHold.EXCLUSIVE_VALIDATION,
        ).allowed:
            return False
        previous = getattr(self, "_active_action_authority_owner", None)
        self._active_action_authority_owner = AuthorityHold.EXCLUSIVE_VALIDATION
        try:
            return self._advance_exclusive_validation(detection)
        finally:
            self._active_action_authority_owner = previous

    def _advance_owned_exclusive_validation_launch(
        self,
        screenshot: Frame,
        detection: Mapping[str, object],
        *,
        battle_started: bool,
    ) -> bool:
        """Advance confirmed Tournament launch under its typed owner."""

        if not self._action_decision(
            RuntimeActionClass.LIFECYCLE_ACTION,
            owner=AuthorityHold.EXCLUSIVE_VALIDATION,
        ).allowed:
            return False
        if self._exclusive_validation_terminal_finalization_pending(
            detection
        ):
            return False
        previous = getattr(self, "_active_action_authority_owner", None)
        self._active_action_authority_owner = AuthorityHold.EXCLUSIVE_VALIDATION
        try:
            return self._advance_exclusive_validation_launch(
                screenshot,
                detection,
                battle_started=battle_started,
            )
        finally:
            self._active_action_authority_owner = previous

    def _perk_timeline_enabled(self) -> bool:
        """Track runs whose declared configuration enables automatic Perks."""

        strategy = self._mission_mgr.strategy
        if strategy is None:
            return False
        configuration = strategy.run_configuration()
        if not isinstance(configuration, Mapping):
            return False
        settings = configuration.get("settings")
        return bool(
            isinstance(settings, Mapping)
            and settings.get("auto_pick_perks") is True
        )

    def _retain_activation_evidence(
        self,
        capture: Mapping[str, Any],
    ) -> Optional[str]:
        """Save a confirmed activation's first transition frame."""

        frame = capture.get("frame")
        if not isinstance(frame, np.ndarray) or frame.ndim < 2:
            log("[BATTLE_EVENT] Activation evidence frame was invalid", "WARN")
            return None

        ability = "".join(
            character
            for character in str(capture.get("ability") or "unknown")
            if character.isalnum() or character in {"_", "-"}
        )
        sequence = max(0, int(capture.get("sequence") or 0))
        detected_at = str(capture.get("detected_at") or "")
        try:
            detected = datetime.fromisoformat(detected_at)
        except ValueError:
            detected = datetime.now().astimezone()
        stamp = detected.strftime("%Y%m%dT%H%M%S%z")

        repository = Path(__file__).resolve().parent.parent
        evidence_dir = repository / "screenshots" / "matches"
        try:
            evidence_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log(
                f"[BATTLE_EVENT] Could not create activation evidence directory: {exc}",
                "WARN",
            )
            return None

        stem = f"SurvivalActivation{stamp}_{ability}_{sequence:02d}_first_absent"
        path = evidence_dir / f"{stem}.png"
        collision = 2
        while path.exists():
            path = evidence_dir / f"{stem}_{collision}.png"
            collision += 1
        try:
            saved = bool(cv2.imwrite(str(path), frame))
        except Exception as exc:
            log(
                f"[BATTLE_EVENT] Could not save activation evidence: {exc}",
                "WARN",
            )
            return None
        if not saved:
            log(
                f"[BATTLE_EVENT] Could not save activation evidence to {path}",
                "WARN",
            )
            return None
        return path.relative_to(repository).as_posix()

    def _prune_generated_artifacts(self, *, force: bool = False) -> None:
        """Apply the bounded retention policy without interrupting automation."""

        retention = getattr(self, "_artifact_retention", None)
        if retention is None:
            return
        try:
            result = retention.maybe_prune(force=force)
        except Exception as exc:
            log(f"[STORAGE] Artifact retention sweep failed: {exc}", "WARN")
            return
        if result is None:
            return
        if result.files_removed:
            removed_mib = result.bytes_removed / (1024 * 1024)
            log(
                "[STORAGE] Removed "
                f"{result.files_removed} expired/old generated artifacts "
                f"({removed_mib:.1f} MiB) under the "
                f"{retention.max_age_days}-day / "
                f"{retention.max_bytes / (1024 * 1024):.0f}-MiB-per-directory "
                "retention policy",
                "INFO",
                console=True,
            )
        if result.errors:
            log(
                "[STORAGE] Artifact retention encountered "
                f"{len(result.errors)} errors; first error: {result.errors[0]}",
                "WARN",
            )

    def _runtime_policy(self) -> Dict[str, Any]:
        strategy = self._mission_mgr.strategy
        if not strategy:
            return {}
        try:
            policy = strategy.runtime_policy()
        except Exception:
            return {}
        return dict(policy) if isinstance(policy, Mapping) else {}

    def _current_player_save_preflight_context(
        self,
    ) -> PlayerSavePreflightContext:
        """Return the exact private identity used by acquisition and carry."""

        session = self._adb_target_session
        if session is None:
            raise RuntimeError(
                "player-save preflight requires an ADB target session"
            )
        target = session.snapshot()
        if not target.owned:
            raise RuntimeError("player-save preflight target is not owned")
        strategy = self._mission_mgr.strategy
        scope = get_activity_scope()
        scope_id = str(scope.get("run_id") or "") if scope else ""
        if not scope_id:
            raise RuntimeError(
                "player-save preflight activity scope is unavailable"
            )
        preflight_id = str(self._player_save_preflight_session_id or "")
        if not preflight_id:
            raise RuntimeError("player-save preflight session is not armed")
        return PlayerSavePreflightContext(
            runtime_session_id=self._player_save_runtime_session_id,
            preflight_session_id=preflight_id,
            activity_scope_id=scope_id,
            strategy_name=(
                str(strategy.name or "") if strategy is not None else "none"
            ),
            configuration_fingerprint=(
                str(strategy.session_preflight_fingerprint() or "")
                if strategy is not None
                else "history-baseline-only"
            ),
            target=target.target,
            target_generation=target.generation,
        )

    def _running_save_reconciliation_owner(
        self,
    ) -> Optional[AuthorityHold]:
        """Return the exact unfinished workflow allowed to serialize RUNNING."""

        current = self._current_control_workflow_evidence()
        if (
            not isinstance(current, Mapping)
            or current.get("game_state") != "active_battle"
            or not current.get("activity_scope_run_id")
            or type(current.get("target_generation")) is not int
            or int(current["target_generation"]) < 1
        ):
            return None
        workflow = self._supervisor.battle_workflow
        if (
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "attach_battle"
            and workflow.get("status") in {"validating_save", "action_dispatched"}
        ):
            requested = workflow.get("evidence")
            if isinstance(requested, Mapping):
                matches, _ = self._workflow_evidence_matches_runtime(
                    requested,
                    current,
                    intent="attach_battle",
                )
                if matches:
                    return AuthorityHold.OPERATOR_WORKFLOW
        manual = self._supervisor.manual_control
        if (
            isinstance(manual, Mapping)
            and manual.get("status") == "reconciling"
        ):
            returned = manual.get("return_evidence")
            if isinstance(returned, Mapping):
                fields_match = all(
                    returned.get(field) == current.get(field)
                    for field in (
                        "runtime_id",
                        "pid",
                        "adb_target",
                        "target_generation",
                        "activity_scope_run_id",
                    )
                )
                if fields_match:
                    return AuthorityHold.MANUAL_CONTROL_RETURN
        return None

    def _current_player_save_attachment_context(
        self,
        *,
        transition_source_activity_scope_id: Optional[str] = None,
        pending_adoption_workflow_id: Optional[str] = None,
    ) -> PlayerSaveAttachmentContext:
        """Return exact process/scope authority for a RUNNING attachment.

        A continuity comparison can durably replace the activity scope before
        the next captured frame publishes that new scope. During that single
        recapture boundary, accept only the source scope carried by the same
        forced-save claim. A retained ready claim may also be revalidated just
        before observation-only lifecycle adoption; that exception is scoped
        to the exact process-local workflow and cannot authorize another save
        read. Every other owner, target, generation, or scope mismatch still
        fails closed.
        """

        session = self._adb_target_session
        if session is None:
            raise RuntimeError(
                "player-save attachment requires an ADB target session"
            )
        target = session.snapshot()
        if not target.owned:
            raise RuntimeError("player-save attachment target is not owned")
        scope = get_activity_scope()
        scope_id = str(scope.get("run_id") or "") if scope else ""
        if not scope_id:
            raise RuntimeError(
                "player-save attachment activity scope is unavailable"
            )
        current = self._current_control_workflow_evidence()
        reconciliation_owner = self._running_save_reconciliation_owner()
        active_battle = self._mission_mgr.active_battle_observed()
        pending_workflow_id = str(
            pending_adoption_workflow_id or ""
        ).strip()
        supervisor = getattr(self, "_supervisor", None)
        workflow = (
            supervisor.battle_workflow
            if pending_workflow_id and supervisor is not None
            else None
        )
        pending_adoption_owner = bool(
            pending_workflow_id
            and isinstance(workflow, Mapping)
            and workflow.get("request_id") == pending_workflow_id
            and workflow.get("intent") == "attach_battle"
            and workflow.get("status") == "ready"
            and isinstance(
                self._running_reconciliation_claims().get(
                    pending_workflow_id
                ),
                Mapping,
            )
            and isinstance(current, Mapping)
            and current.get("game_state") == "active_battle"
        )
        if not (
            active_battle is True
            or (
                reconciliation_owner is not None
                and isinstance(current, Mapping)
                and current.get("game_state") == "active_battle"
            )
            or pending_adoption_owner
        ):
            raise RuntimeError(
                "player-save attachment battle identity is not active"
            )
        if reconciliation_owner is not None or pending_adoption_owner:
            observed_scope_id = (
                str(current.get("activity_scope_run_id") or "")
                if isinstance(current, Mapping)
                else ""
            )
            source_scope_id = str(
                transition_source_activity_scope_id or ""
            ).strip()
            scope_matches = observed_scope_id == scope_id
            expected_transition = bool(
                source_scope_id
                and source_scope_id != scope_id
                and observed_scope_id == source_scope_id
            )
            if (
                not (scope_matches or expected_transition)
                or not isinstance(current, Mapping)
                or current.get("target_generation") != target.generation
            ):
                raise RuntimeError(
                    "player-save attachment workflow binding changed"
                )
        runtime_session_id = str(
            self._player_save_runtime_session_id or ""
        )
        if not runtime_session_id:
            raise RuntimeError(
                "player-save attachment runtime session is unavailable"
            )
        return PlayerSaveAttachmentContext(
            runtime_session_id=runtime_session_id,
            activity_scope_id=scope_id,
            target=target.target,
            target_generation=target.generation,
            active_battle_observed=True,
        )

    def _acquire_player_save_home_preflight(
        self,
        requirements: Mapping[str, Any],
        *,
        screenshot,
        mode_override: Optional[str] = None,
    ):
        coordinator = getattr(
            self,
            "_player_save_preflight_coordinator",
            None,
        )
        if coordinator is None:
            return None
        self._player_save_preflight_session_id = new_operation_id()
        mode = mode_override or self._runtime_policy().get(
            "player_save_preflight",
            "save_first",
        )
        result = coordinator.acquire(
            requirements,
            mode=mode,
            initial_frame=screenshot,
        )
        self._player_save_preflight_result = result
        scope = get_activity_scope()
        self._player_save_preflight_activity_scope_id = (
            getattr(result, "history_scope_id", None)
            or (str(scope.get("run_id") or "") if scope else None)
        )
        activity_continuity = getattr(self, "_activity_continuity", None)
        self._player_save_history_baseline_outcome = (
            activity_continuity.accept_home_save_baseline(
                getattr(result, "history_tail", {}),
                expected_scope_id=getattr(result, "history_scope_id", None),
                player_save_mode=str(mode),
            )
            if (
                activity_continuity is not None
                and result.ready
                and str(mode) != "force_ui"
            )
            else None
        )
        return result

    def _handle_home_return_reconciliation(
        self,
        *,
        screenshot: Optional[Frame],
    ) -> bool:
        """Run exactly one Home/New Return refresh and terminalize failures."""

        manual = self._supervisor.manual_control
        current = self._current_control_workflow_evidence()
        if not (
            isinstance(manual, Mapping)
            and manual.get("status") == "reconciling"
            and isinstance(current, Mapping)
            and current.get("game_state") == "home_new_battle"
        ):
            return False
        result = self._acquire_player_save_home_preflight(
            self._active_strategy_session_requirements(),
            screenshot=screenshot,
            mode_override="save_first",
        )
        if result is not None and self._complete_home_return_reconciliation(
            result,
            screenshot=screenshot,
        ):
            return True

        # A trusted mismatch or genuine allowlisted UI fallback may have
        # advanced the workflow to awaiting_configuration.  Only a workflow
        # still stranded in reconciling is a failed refresh boundary.
        after = self._supervisor.manual_control
        if (
            isinstance(after, Mapping)
            and after.get("manual_control_id")
            == manual.get("manual_control_id")
            and after.get("status") == "reconciling"
        ):
            self._terminate_home_return_reconciliation(result)
        return True

    def _terminate_home_return_reconciliation(self, result: object) -> bool:
        """End a Home Return refresh, pausing only if its source is unsafe."""

        manual = self._supervisor.manual_control
        if not (
            isinstance(manual, Mapping)
            and manual.get("status") == "reconciling"
        ):
            return False
        manual_id = str(manual.get("manual_control_id") or "")
        provenance = getattr(result, "provenance", {})
        provenance = provenance if isinstance(provenance, Mapping) else {}
        serialization = str(provenance.get("serialization") or "")
        background_dispatched = bool(
            provenance.get("background_dispatched") is True
            or serialization
            in {"background_dispatched", "verified_android_home_boundary"}
        )
        result_reason = str(
            getattr(result, "reason", None)
            or "Home save refresh did not return an exact bound acquisition"
        )
        result_status = getattr(result, "status", None)
        status_value = str(getattr(result_status, "value", result_status) or "")
        lifecycle_input_attempted = bool(
            provenance.get("lifecycle_input_attempted") is True
            or background_dispatched
        )
        source_restored = bool(
            provenance.get("source_restored") is True
            or serialization == "verified_android_home_boundary"
        )
        catastrophic = bool(
            lifecycle_input_attempted
            and not source_restored
            and status_value == "blocked"
        )
        final_status = "interrupted" if catastrophic else "failed"
        refresh_status = (
            "home_save_restoration_interrupted"
            if catastrophic
            else "home_save_refresh_failed_continued"
        )
        if catastrophic:
            self._supervisor.pause_for_catastrophic_failure(
                RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                reason=result_reason,
            )
        else:
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                result_reason,
            )
        self._pending_return_reconciliation_claims().pop(manual_id, None)
        mission_manager = getattr(self, "_mission_mgr", None)
        if mission_manager is not None:
            mission_manager.finish_manual_return_reconciliation()
        transitioned = self._supervisor.transition_manual_control(
            manual_id,
            final_status,
            detail=(
                "Home save-backed Return Control stopped after an unsafe source "
                f"transition: {result_reason}; Automation remains Paused"
                if catastrophic
                else "Home save refresh was unavailable: "
                f"{result_reason}; Return Control ended and Automation continues "
                "in degraded mode"
            ),
            refresh_status=refresh_status,
        )
        return transitioned is not None

    def _complete_home_return_reconciliation(
        self,
        result: object,
        *,
        screenshot: Optional[Frame] = None,
    ) -> bool:
        """Complete Return at verified Home/New Battle from one forced save."""

        manual = self._supervisor.manual_control
        current = self._current_control_workflow_evidence()
        acquisition = getattr(result, "acquisition", None)
        result_context = getattr(result, "context", None)
        try:
            live_context = self._current_player_save_preflight_context()
            expected_binding = PlayerSaveTargetBinding(
                str(current.get("adb_target") or "")
                if isinstance(current, Mapping)
                else "",
                int(current.get("target_generation"))
                if isinstance(current, Mapping)
                else 0,
            )
        except (RuntimeError, TypeError, ValueError):
            live_context = None
            expected_binding = None
        base_bound = bool(
            isinstance(manual, Mapping)
            and manual.get("status") == "reconciling"
            and isinstance(current, Mapping)
            and current.get("game_state") == "home_new_battle"
            and getattr(result, "ready", False) is True
            and result_context == live_context
            and isinstance(expected_binding, PlayerSaveTargetBinding)
        )
        save_backed = bool(
            base_bound
            and isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.complete
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.FORCED_SERIALIZATION
            and acquisition.binding == expected_binding
        )
        ui_backed = bool(
            base_bound
            and not save_backed
            and getattr(result, "safe_ui_fallback", False) is True
        )
        if not base_bound or not (save_backed or ui_backed):
            return False
        workflow_id = str(manual.get("manual_control_id") or "")
        decisions = getattr(result, "decisions", {})
        reconciliation = {
            "checks": decisions if isinstance(decisions, Mapping) else {}
        }
        check_sets = self._save_reconciliation_check_sets(reconciliation)
        resolved = check_sets["accepted"]
        unresolved = tuple(
            sorted(
                {
                    *check_sets["mismatched"],
                    *check_sets["ui_required"],
                }
            )
        )
        degraded = False
        degraded_reason = ""
        repair_failure: Dict[str, object] = {}
        try:
            if ui_backed:
                receipt = build_home_ui_reconciliation_receipt(
                    workflow_id=workflow_id,
                    observation_id=str(current.get("observation_id") or ""),
                    evidence=current,
                    reason=str(
                        getattr(result, "reason", "")
                        or "save_evidence_unavailable"
                    ),
                    resolved_check_ids=resolved,
                    unresolved_check_ids=unresolved,
                )
            else:
                receipt = build_home_return_reconciliation_receipt(
                    workflow_id=workflow_id,
                    observation_id=str(current.get("observation_id") or ""),
                    activity_scope_id=str(
                        current.get("activity_scope_run_id") or ""
                    ),
                    acquisition=acquisition,
                    expected_binding=expected_binding,
                    resolved_check_ids=resolved,
                    unresolved_check_ids=unresolved,
                )
        except (TypeError, ValueError) as exc:
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.REPORTING_FAILURE,
                f"Home Return receipt could not be built: {exc}",
            )
            self._supervisor.transition_manual_control(
                workflow_id,
                "failed",
                detail=(
                    f"Home Return reporting failed: {exc}; Automation continues "
                    "from the verified Home boundary"
                ),
                refresh_status="save_reconciliation_failed_continued",
            )
            return True
        if unresolved:
            awaiting = self._supervisor.transition_manual_control(
                workflow_id,
                "awaiting_configuration",
                detail=(
                    "save evidence was unusable, so supported Home UI checks "
                    "will be repaired at the current safe Home boundary"
                    if ui_backed
                    else "fresh Home save found configuration checks that will be "
                    "repaired at the current safe Home boundary"
                ),
                refresh_status="configuration_repair_in_progress",
                save_receipt=receipt,
                configuration=self._return_configuration_report(
                    receipt,
                    check_sets,
                    stage="configuration_repair_in_progress",
                ),
            )
            if awaiting is None:
                return False
            requirements = self._active_strategy_session_requirements()
            if screenshot is None or not requirements:
                degraded = True
                degraded_reason = (
                    "Home configuration evidence is unavailable for repair"
                )
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                    degraded_reason,
                )
            else:
                repair_decision = decide_runtime_failure(
                    RuntimeFailureKind.CONFIGURATION_MISMATCH,
                    repair_available=True,
                )
                if repair_decision.disposition is not (
                    RuntimeFailureDisposition.REPAIR_NOW
                ):
                    raise AssertionError("safe Home mismatch must select repair")
                waivers = merge_profile_skip_waivers(
                    requirements,
                    getattr(self, "_startup_gate_waivers", {}),
                )
                setup = self._run_home_setup_attempts(
                    requirements,
                    screenshot=screenshot,
                    waivers=waivers,
                    save_preflight=result,
                )
                setup_evidence = dict(getattr(setup, "evidence", {}) or {})
                setup_evidence["player_save_preflight"] = result.as_dict()
                if not setup.complete:
                    if setup.interrupted:
                        return True
                    degraded = True
                    failed_check = str(setup.failed_check or "startup_setup")
                    degraded_reason = (
                        f"Home repair stopped at {failed_check}: {setup.reason}"
                    )
                    repair_failure = {
                        "failed_check": failed_check,
                        "failure_reason": str(setup.reason),
                        "retryable_from_home": bool(
                            setup.retryable_from_home
                        ),
                    }
                    self._flag_recoverable_runtime_failure(
                        RuntimeFailureKind.REPAIR_EXHAUSTED,
                        degraded_reason,
                    )
                    self._mission_mgr.mark_no_battle_setup_degraded(
                        setup_evidence,
                        failed_check=failed_check,
                        reason=str(setup.reason),
                        waivers=waivers,
                    )
                else:
                    self._mission_mgr.mark_no_battle_setup_complete(
                        setup_evidence,
                        waivers=waivers,
                    )
                    all_checks = tuple(
                        sorted(
                            {
                                *check_sets["accepted"],
                                *check_sets["mismatched"],
                                *check_sets["ui_required"],
                            }
                        )
                    )
                    if ui_backed:
                        receipt = build_home_ui_reconciliation_receipt(
                            workflow_id=workflow_id,
                            observation_id=str(
                                current.get("observation_id") or ""
                            ),
                            evidence=current,
                            reason=str(
                                getattr(result, "reason", "")
                                or "save_evidence_unavailable"
                            ),
                            resolved_check_ids=all_checks,
                        )
                    else:
                        receipt = build_home_return_reconciliation_receipt(
                            workflow_id=workflow_id,
                            observation_id=str(
                                current.get("observation_id") or ""
                            ),
                            activity_scope_id=str(
                                current.get("activity_scope_run_id") or ""
                            ),
                            acquisition=acquisition,
                            expected_binding=expected_binding,
                            resolved_check_ids=all_checks,
                        )

        completion_payload = {
            "detail": (
                f"Home Return released automation with a flagged problem: {degraded_reason}"
                if degraded
                else "verified Home UI discovery replaced unusable save evidence "
                "and reconciled current configuration"
                if ui_backed
                else "fresh Home save confirmed there is no active battle and "
                "reconciled current configuration evidence"
            ),
            "refresh_status": (
                "home_reconciliation_complete_degraded"
                if degraded
                else "home_ui_fallback_reconciliation_complete"
                if ui_backed
                else "home_save_reconciliation_complete"
            ),
            "save_receipt": receipt,
            "configuration": {
                **self._return_configuration_report(
                    receipt,
                    check_sets,
                    stage="complete_degraded" if degraded else "complete",
                ),
                **repair_failure,
            },
        }
        return self._stage_return_semantic_completion(
            manual,
            completion_payload,
            completion_kind="home",
        )

    def _active_strategy_session_requirements(self) -> Dict[str, Any]:
        """Return only the active battle Strategy's current requirements.

        A queued next-boundary Strategy is deliberately excluded.  Return
        Control reconciles the policy that would regain device authority for
        the battle currently on screen.
        """

        strategy = self._mission_mgr.strategy
        effective = self._strategy_session_requirements(strategy)
        if not effective:
            return {}
        runtime_waivers_fn = getattr(
            self._mission_mgr,
            "session_preflight_waivers",
            None,
        )
        runtime_waivers = (
            runtime_waivers_fn() if callable(runtime_waivers_fn) else None
        )
        waivers = merge_profile_skip_waivers(
            effective,
            runtime_waivers if isinstance(runtime_waivers, Mapping) else None,
        )
        if waivers:
            for check_id in waivers:
                effective.pop(str(check_id), None)
            effective["_gate_waivers"] = waivers
        return effective

    @staticmethod
    def _strategy_session_requirements(strategy: object) -> Dict[str, Any]:
        """Return one resolved Strategy's requirements without runtime state."""

        requirement_fn = getattr(
            strategy,
            "session_preflight_requirements",
            None,
        )
        if not callable(requirement_fn):
            return {}
        requirements = requirement_fn()
        if not isinstance(requirements, Mapping):
            return {}
        return dict(requirements)

    def _attachment_strategy_decision(
        self,
        workflow: Mapping[str, object],
        *,
        acquisition: object = None,
        observations: object = None,
        ui_fallback_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Classify the immutable Attach Strategy before lifecycle adoption."""

        raw_strategy = workflow.get("strategy")
        strategy_name = str(raw_strategy or "").strip().lower()
        strategy_request_id = str(
            workflow.get("strategy_request_id") or ""
        ).strip()
        strategy_definition_fingerprint = str(
            workflow.get("strategy_definition_fingerprint") or ""
        ).strip()
        decision: Dict[str, Any] = {
            "schema_version": 1,
            "selected_strategy": strategy_name or None,
            "strategy_request_id": strategy_request_id or None,
            "strategy_definition_fingerprint": (
                strategy_definition_fingerprint or None
            ),
            "attachment_mode": "observation_only",
            "applicability": "unverifiable",
            "degraded": True,
            "reason": "Attach has no immutable selected Strategy snapshot",
            "failed_checks": ["strategy_applicability"],
            "trusted_mismatch_check_ids": [],
            "ui_required_check_ids": [],
            "_strategy": None,
            "_requirements": {},
            "_observations": (
                observations
                if isinstance(observations, RunningAttachmentSaveObservations)
                else None
            ),
            "_check_sets": {
                "accepted": (),
                "mismatched": (),
                "ui_required": (),
            },
        }
        if not strategy_name:
            return decision
        if strategy_name == "none":
            decision.update(
                {
                    "attachment_mode": "observation_only",
                    "applicability": "intentional_observation",
                    "degraded": False,
                    "reason": "No Strategy was selected for this attachment",
                    "failed_checks": [],
                }
            )
            return decision
        if not strategy_request_id or len(strategy_definition_fingerprint) != 64:
            decision["reason"] = (
                "Attach has no immutable accepted Strategy definition identity"
            )
            return decision
        try:
            strategy = get_strategy(strategy_name)
        except Exception:
            decision["reason"] = (
                f"selected Strategy {strategy_name} could not be resolved"
            )
            return decision
        if strategy is None:
            decision["reason"] = (
                f"selected Strategy {strategy_name} resolved to observation-only"
            )
            return decision
        actual_definition_fingerprint = str(
            strategy.definition_fingerprint()
        ).strip()
        if actual_definition_fingerprint != strategy_definition_fingerprint:
            decision["reason"] = (
                f"selected Strategy {strategy_name} changed after Attach was "
                "requested"
            )
            return decision

        run_configuration_fn = getattr(strategy, "run_configuration", None)
        run_configuration = (
            run_configuration_fn()
            if callable(run_configuration_fn)
            else {}
        )
        if not isinstance(run_configuration, Mapping):
            run_configuration = {}
        strategy_config = getattr(strategy, "config", None)
        meta = (
            strategy_config.get("meta")
            if isinstance(strategy_config, Mapping)
            else None
        )
        meta = meta if isinstance(meta, Mapping) else {}
        family = str(
            meta.get("family") or run_configuration.get("profile") or ""
        ).strip().lower()
        raw_tier = run_configuration.get("tier", meta.get("tier"))
        expected_tier = (
            raw_tier
            if type(raw_tier) is int and raw_tier >= 0
            else None
        )
        decision.update(
            {
                "expected_family": family or None,
                "expected_tier": expected_tier,
                "_strategy": strategy,
            }
        )
        if family not in {"farm", "tournament"}:
            decision["reason"] = (
                f"selected Strategy {strategy_name} has no supported battle "
                "applicability declaration"
            )
            return decision
        if ui_fallback_reason is not None and family == "farm":
            decision["reason"] = (
                "the UI continuity fallback cannot prove the active farming Tier"
            )
            decision["ui_fallback_reason"] = str(ui_fallback_reason)
            return decision

        observed_tier: Optional[int] = None
        if isinstance(acquisition, PlayerSaveAcquisitionBundle):
            snapshot = acquisition.snapshot
            runtime = getattr(snapshot, "runtime_save", None)
            active_identity = getattr(runtime, "active_round_identity", None)
            candidate_tier = getattr(active_identity, "current_tier", None)
            if type(candidate_tier) is int and candidate_tier >= 0:
                observed_tier = candidate_tier
        decision["observed_tier"] = observed_tier
        if family == "farm":
            if expected_tier is None or observed_tier is None:
                decision["reason"] = (
                    "the active battle Tier or selected farming Tier is unavailable"
                )
                return decision
            if expected_tier != observed_tier:
                decision.update(
                    {
                        "applicability": "incompatible",
                        "reason": (
                            f"selected Strategy expects Tier {expected_tier}, "
                            f"but the active battle is Tier {observed_tier}"
                        ),
                        "failed_checks": ["battle_tier"],
                    }
                )
                return decision

        requirements = self._strategy_session_requirements(strategy)
        profile_waivers = merge_profile_skip_waivers(requirements)
        if profile_waivers:
            # Applicability must compare the same effective Strategy contract
            # used by ordinary runtime validation.  A profile-owned permanent
            # skip is unmanaged configuration, not an attached-battle
            # mismatch merely because a placeholder expectation exists in the
            # raw source requirements.
            for check_id in profile_waivers:
                requirements.pop(str(check_id), None)
            requirements["_gate_waivers"] = profile_waivers
        check_sets: Dict[str, tuple[str, ...]] = {
            "accepted": (),
            "mismatched": (),
            "ui_required": (),
        }
        requested_check_ids = set(
            requested_player_save_check_ids(requirements)
        )
        if requirements and isinstance(
            acquisition,
            PlayerSaveAcquisitionBundle,
        ):
            try:
                reconciliation = reconcile_acquired_requirements(
                    acquisition,
                    requirements,
                )
                if isinstance(
                    observations,
                    RunningAttachmentSaveObservations,
                ):
                    check_sets = self._save_reconciliation_check_sets(
                        reconciliation,
                        observations=observations,
                    )
                    check_sets = {
                        category: tuple(
                            check_id
                            for check_id in check_ids
                            if check_id in requested_check_ids
                        )
                        for category, check_ids in check_sets.items()
                    }
                else:
                    check_sets = {
                        "accepted": (),
                        "mismatched": (),
                        "ui_required": tuple(
                            sorted(
                                requested_check_ids
                            )
                        ),
                    }
            except (TypeError, ValueError):
                decision["reason"] = (
                    "selected Strategy configuration could not be reconciled "
                    "against the attached save"
                )
                return decision
        elif requirements:
            check_sets = {
                "accepted": (),
                "mismatched": (),
                "ui_required": tuple(
                    sorted(requested_check_ids)
                ),
            }

        mismatched = list(check_sets["mismatched"])
        ui_required = list(check_sets["ui_required"])
        decision.update(
            {
                "attachment_mode": "strategy",
                "applicability": "pending_battle_kind",
                "degraded": bool(mismatched),
                "reason": (
                    "battle applicability matched; saved configuration "
                    "mismatches were flagged without repair"
                    if mismatched
                    else "battle applicability matched; selected Strategy "
                    "configuration validation is non-mutating"
                ),
                "failed_checks": mismatched,
                "trusted_mismatch_check_ids": mismatched,
                "ui_required_check_ids": ui_required,
                "_requirements": requirements,
                "_check_sets": check_sets,
            }
        )
        return decision

    @staticmethod
    def _attachment_configuration_report(
        receipt: Mapping[str, object],
        decision: Mapping[str, object],
        *,
        stage: str,
    ) -> Dict[str, Any]:
        """Publish the Attach outcome without process-local strategy objects."""

        configuration = receipt.get("configuration")
        report = dict(configuration) if isinstance(configuration, Mapping) else {}
        for key in (
            "selected_strategy",
            "strategy_request_id",
            "strategy_definition_fingerprint",
            "attachment_mode",
            "applicability",
            "degraded",
            "reason",
            "failed_checks",
            "expected_family",
            "expected_tier",
            "observed_tier",
            "observed_kind",
            "trusted_mismatch_check_ids",
            "ui_required_check_ids",
            "ui_fallback_reason",
        ):
            if key in decision:
                report[key] = copy.deepcopy(decision[key])
        report.update({"schema_version": 1, "stage": str(stage)})
        return report

    def _attachment_reporting_failure_decision(
        self,
        workflow: Mapping[str, object],
        *,
        reason: str,
        observations: object = None,
    ) -> Dict[str, Any]:
        """Fall back to a marked observer when only durable reporting failed."""

        decision = self._attachment_strategy_decision(
            workflow,
            observations=observations,
        )
        failed_checks = {
            str(check_id).strip()
            for check_id in decision.get("failed_checks", ())
            if str(check_id).strip()
        }
        failed_checks.add("workflow_reporting")
        decision.update(
            {
                "attachment_mode": "observation_only",
                "applicability": "unverifiable",
                "degraded": True,
                "reason": str(reason or "Attach reporting is unavailable"),
                "failed_checks": sorted(failed_checks),
                "_strategy": None,
            }
        )
        return decision

    @staticmethod
    def _attachment_reporting_unavailable_configuration(
        decision: Mapping[str, object],
    ) -> Dict[str, Any]:
        """Return a terminal flag that cannot be mistaken for a replay receipt."""

        configuration: Dict[str, Any] = {
            "schema_version": 1,
            "stage": "completed",
            "reporting_status": "unavailable",
            "selected_strategy": decision.get("selected_strategy"),
            "strategy_request_id": decision.get("strategy_request_id"),
            "attachment_mode": "observation_only",
            "applicability": "unverifiable",
            "degraded": True,
            "reason": str(
                decision.get("reason") or "Attach reporting is unavailable"
            ),
            "failed_checks": list(decision.get("failed_checks", ())),
        }
        for key in (
            "expected_family",
            "expected_tier",
            "observed_tier",
            "observed_kind",
        ):
            if decision.get(key) is not None:
                configuration[key] = decision.get(key)
        return configuration

    def _authorize_reporting_degraded_attachment(
        self,
        workflow: Mapping[str, object],
        decision: Mapping[str, object],
    ) -> bool:
        """Adopt a proven battle as observer when its report cannot persist."""

        request_id = str(workflow.get("request_id") or "")
        authorized = self._mission_mgr.authorize_initial_battle_intent(
            "attach_battle",
            request_id=request_id,
            observation_only=True,
        )
        if not authorized:
            return False
        self._begin_no_strategy_observation_boundary(
            request_id,
            observations=decision.get("_observations"),
        )
        self._mission_mgr.mark_running_configuration_degraded(
            source="attachment_reporting",
            reason=str(decision.get("reason") or "Attach reporting failed"),
            failed_checks=decision.get("failed_checks", ()),
            details={
                key: decision.get(key)
                for key in (
                    "selected_strategy",
                    "strategy_request_id",
                    "attachment_mode",
                    "applicability",
                )
                if decision.get(key) is not None
            },
        )
        self._flag_recoverable_runtime_failure(
            RuntimeFailureKind.REPORTING_FAILURE,
            str(decision.get("reason") or "Attach reporting failed"),
        )
        return True

    def _resolve_ready_attachment_strategy(
        self,
        decision: Mapping[str, object],
        detection: Mapping[str, object],
        frame: Optional[Frame],
    ) -> Dict[str, Any]:
        """Finish battle-kind applicability from the fresh ready frame."""

        resolved = dict(decision)
        if resolved.get("attachment_mode") != "strategy":
            return resolved
        tournament = self._tournament_battle_guard(detection)
        if tournament:
            observed_kind = "tournament"
        else:
            x, y, width, height = DISSONANCE_BADGE_REGION
            if not (
                isinstance(frame, np.ndarray)
                and frame.ndim == 3
                and y + height <= frame.shape[0]
                and x + width <= frame.shape[1]
            ):
                resolved.update(
                    {
                        "attachment_mode": "observation_only",
                        "applicability": "unverifiable",
                        "degraded": True,
                        "reason": (
                            "a complete fresh battle frame was unavailable "
                            "for battle-kind classification"
                        ),
                        "failed_checks": ["battle_kind"],
                        "_strategy": None,
                    }
                )
                return resolved
            try:
                badge = detect_dissonance_badge(frame)
            except Exception:
                resolved.update(
                    {
                        "attachment_mode": "observation_only",
                        "applicability": "unverifiable",
                        "degraded": True,
                        "reason": (
                            "battle-kind classification failed on the fresh "
                            "attachment frame"
                        ),
                        "failed_checks": ["battle_kind"],
                        "_strategy": None,
                    }
                )
                return resolved
            if badge.get("observed") is True:
                observed_kind = "dissonance"
            elif str(detection.get("state") or "").upper() == "RUNNING":
                observed_kind = "ordinary"
            else:
                resolved.update(
                    {
                        "attachment_mode": "observation_only",
                        "applicability": "unverifiable",
                        "degraded": True,
                        "reason": (
                            "the fresh attachment frame did not show an "
                            "unobstructed running-battle HUD"
                        ),
                        "failed_checks": ["battle_kind"],
                        "_strategy": None,
                    }
                )
                return resolved
        resolved["observed_kind"] = observed_kind
        family = str(resolved.get("expected_family") or "").strip().lower()
        expected_kind = "tournament" if family == "tournament" else "ordinary"
        if observed_kind != expected_kind:
            resolved.update(
                {
                    "attachment_mode": "observation_only",
                    "applicability": "incompatible",
                    "degraded": True,
                    "reason": (
                        f"selected Strategy expects an {expected_kind} battle, "
                        f"but the active battle is {observed_kind}"
                    ),
                    "failed_checks": ["battle_kind"],
                    "_strategy": None,
                }
            )
            return resolved
        resolved["applicability"] = "compatible"
        return resolved

    @staticmethod
    def _save_reconciliation_check_sets(
        reconciliation: Mapping[str, Any],
        *,
        observations: Optional[RunningAttachmentSaveObservations] = None,
    ) -> Dict[str, tuple[str, ...]]:
        """Separate accepted, mismatched, and genuinely unresolved checks."""

        raw_checks = reconciliation.get("checks")
        checks = raw_checks if isinstance(raw_checks, Mapping) else {}
        accepted: list[str] = []
        mismatched: list[str] = []
        ui_required: list[str] = []
        for raw_check_id, raw_decision in checks.items():
            check_id = str(raw_check_id or "").strip()
            if not check_id or not isinstance(raw_decision, Mapping):
                continue
            disposition = str(
                raw_decision.get("disposition") or "ui_required"
            ).strip().lower()
            fact = observations.fact(check_id) if observations is not None else None
            consumable_running_fact = bool(
                observations is None
                or (
                    fact is not None
                    and fact.temporal_class
                    is PlayerSaveTemporalClass.ROUND_INVARIANT
                )
            )
            if disposition in {"save_match", "save_observation"} and (
                consumable_running_fact
            ):
                accepted.append(check_id)
            elif disposition == "save_mismatch" and consumable_running_fact:
                mismatched.append(check_id)
            else:
                # This includes unmapped/incomplete evidence and any value
                # that cannot be safely consumed at a running boundary.
                ui_required.append(check_id)
        return {
            "accepted": tuple(sorted(set(accepted))),
            "mismatched": tuple(sorted(set(mismatched))),
            "ui_required": tuple(sorted(set(ui_required))),
        }

    @staticmethod
    def _return_configuration_report(
        receipt: Mapping[str, Any],
        check_sets: Mapping[str, Sequence[str]],
        *,
        stage: str,
    ) -> Dict[str, Any]:
        """Build a compact, durable operator report without replay authority."""

        configuration = receipt.get("configuration")
        report = dict(configuration) if isinstance(configuration, Mapping) else {}
        report.update(
            {
                "schema_version": 1,
                "stage": str(stage),
                "trusted_mismatch_check_ids": list(
                    check_sets.get("mismatched", ())
                ),
                "ui_required_check_ids": list(
                    check_sets.get("ui_required", ())
                ),
                "ui_fallback_restricted": not isinstance(
                    receipt.get("ui_fallback"),
                    Mapping,
                ),
            }
        )
        return report

    def _current_strategy_name(self) -> str:
        strategy = self._mission_mgr.strategy
        return str(strategy.name if strategy else "none").strip().lower()

    def _current_strategy_home_tier(self) -> Optional[int]:
        """Return an exact strategy-declared Home tier, if one is applicable."""

        strategy = self._mission_mgr.strategy
        if strategy is None:
            return None
        run_configuration_fn = getattr(strategy, "run_configuration", None)
        run_configuration = (
            run_configuration_fn()
            if callable(run_configuration_fn)
            else {}
        )
        if not isinstance(run_configuration, Mapping):
            return None
        if "tier" not in run_configuration:
            return None
        tier = run_configuration.get("tier")
        if type(tier) is not int or not 1 <= tier <= 100:
            raise ValueError(
                "selected Strategy declares an invalid Home tier; expected an "
                "integer between 1 and 100"
            )
        return tier

    def _begin_no_strategy_observation_boundary(
        self,
        boundary_id: str,
        *,
        observations: object = None,
    ) -> None:
        """Start one clean observer boundary and apply its exact save facts."""

        if getattr(self, "_pending_no_strategy_record", None) is not None:
            return
        normalized_id = str(boundary_id or "no-strategy").strip()
        observer = getattr(self, "_no_strategy_observer", None)
        if observer is None:
            return
        if (
            getattr(self, "_no_strategy_attachment_boundary_id", None)
            != normalized_id
        ):
            observer.reset()
            self._no_strategy_attachment_boundary_id = normalized_id
        self._no_strategy_observation_active = True
        self._no_strategy_inventory_complete = False
        self._no_strategy_inventory_retry_at = 0.0
        if isinstance(observations, RunningAttachmentSaveObservations):
            try:
                applied = observer.record_player_save_observations(observations)
            except (TypeError, ValueError) as exc:
                log(
                    "[NO_STRATEGY] Guarded attachment save observations "
                    f"were rejected: {exc}",
                    "WARN",
                )
            else:
                if applied:
                    log(
                        "[NO_STRATEGY] Applied guarded attachment save "
                        f"observations for {len(applied)} fields: "
                        + ", ".join(applied),
                        "INFO",
                        console=True,
                    )

    def _end_no_strategy_observation_boundary(self) -> None:
        """Discard passive state when a managed Strategy takes ownership."""

        if getattr(self, "_pending_no_strategy_record", None) is not None:
            return
        self._no_strategy_observation_active = False
        self._no_strategy_attachment_boundary_id = None
        self._no_strategy_inventory_complete = False
        self._no_strategy_inventory_retry_at = 0.0
        observer = getattr(self, "_no_strategy_observer", None)
        if observer is not None:
            observer.reset()

    def _current_strategy_definition_matches(self, requested_name: str) -> bool:
        """Return whether the latest named definition is already loaded."""

        current = self._mission_mgr.strategy
        try:
            requested = get_strategy(requested_name)
        except Exception:
            # Resolution is retried by the guarded boundary application.  A
            # failed comparison must never acknowledge an unproved reload.
            return False
        if current is None or requested is None:
            return current is requested
        current_config = getattr(current, "config", None)
        requested_config = getattr(requested, "config", None)
        return bool(
            type(current) is type(requested)
            and isinstance(current_config, Mapping)
            and isinstance(requested_config, Mapping)
            and current_config == requested_config
        )

    def _exclusive_validation_definition(
        self,
    ) -> Optional[ExclusiveValidationDefinition]:
        try:
            return exclusive_validation_definition(self._mission_mgr.strategy)
        except (TypeError, ValueError) as exc:
            log(f"[VALIDATION] Invalid strategy validation policy: {exc}", "ERROR")
            return None

    def _exclusive_validation_receipt(self) -> Optional[Dict[str, object]]:
        request_ids = (
            getattr(self, "_exclusive_validation_terminal_hold", None),
            getattr(
                self,
                "_exclusive_validation_battle_dispatch_hold",
                None,
            ),
            getattr(
                self,
                "_exclusive_validation_launch_dispatch_hold",
                None,
            ),
            getattr(
                self,
                "_exclusive_validation_claimed_start_hold",
                None,
            ),
            getattr(
                self,
                "_exclusive_validation_launch_start_hold",
                None,
            ),
            getattr(
                self,
                "_active_exclusive_validation_request_id",
                None,
            ),
            getattr(
                self,
                "_active_exclusive_validation_launch_request_id",
                None,
            ),
        )
        checked_ids: set[str] = set()
        for candidate_id in request_ids:
            active_request_id = str(candidate_id or "").strip()
            if not active_request_id or active_request_id in checked_ids:
                continue
            checked_ids.add(active_request_id)
            receipt = self._supervisor.exclusive_validation_receipt(
                request_id=active_request_id
            )
            if receipt is not None:
                return receipt
        strategy_request = self._supervisor.strategy_request
        if (
            not isinstance(strategy_request, tuple)
            or len(strategy_request) not in {2, 3}
            or str(strategy_request[0] or "").strip().lower()
            != self._current_strategy_name()
        ):
            return None
        return self._supervisor.exclusive_validation_receipt(
            strategy_request_id=strategy_request[1]
        )

    def _reconcile_exclusive_validation(
        self,
    ) -> Optional[Dict[str, object]]:
        ledger = self._supervisor.exclusive_validation
        receipts = ledger.get("receipts")
        if isinstance(receipts, Mapping):
            for candidate in list(receipts.values()):
                if (
                    not isinstance(candidate, Mapping)
                    or str(candidate.get("status") or "")
                    not in {"claimed", "running", "cleanup"}
                ):
                    continue
                candidate_id = str(candidate.get("request_id") or "")
                if self._supervisor.owns_exclusive_validation(
                    candidate_id,
                    statuses=(str(candidate.get("status") or ""),),
                ):
                    self._active_exclusive_validation_request_id = candidate_id
                    self._exclusive_validation_ownership_hold = False
                    return self._supervisor.exclusive_validation_receipt(
                        request_id=candidate_id
                    )
                failed = self._supervisor.fail_orphaned_exclusive_validation(
                    candidate_id,
                    reason=(
                        "validation ownership belongs to a prior runtime or ADB "
                        "target; no Surrender or battle action was taken"
                    ),
                )
                if failed:
                    self._exclusive_validation_ownership_hold = False
                    self._flag_recoverable_runtime_failure(
                        RuntimeFailureKind.WORKFLOW_EVIDENCE_EXPIRED,
                        "orphaned Tournament validation ownership was retired",
                    )
                    self._announce_exclusive_validation_result(failed)
                    continue
                refreshed = self._supervisor.exclusive_validation_receipt(
                    request_id=candidate_id
                )
                if (
                    refreshed is None
                    or str(refreshed.get("status") or "")
                    not in {"claimed", "running", "cleanup"}
                ):
                    self._exclusive_validation_ownership_hold = False
                    continue
                # A failed exact-owner reread and an inconclusive orphan
                # transition are not proof that this process may release its
                # cached work. Retain a suppressive owner until a fresh read
                # either restores exact ownership or durably retires it.
                self._active_exclusive_validation_request_id = candidate_id
                self._exclusive_validation_ownership_hold = True
                return refreshed

        receipt = self._exclusive_validation_receipt()
        if receipt is None:
            return None
        request_id = str(receipt.get("request_id") or "")
        status = str(receipt.get("status") or "")
        if status in {"claimed", "running", "cleanup"}:
            if self._supervisor.owns_exclusive_validation(
                request_id,
                statuses=(status,),
            ):
                self._active_exclusive_validation_request_id = request_id
                self._exclusive_validation_ownership_hold = False
                return self._supervisor.exclusive_validation_receipt(
                    request_id=request_id
                )
            failed = self._supervisor.fail_orphaned_exclusive_validation(
                request_id,
                reason=(
                    "validation ownership belongs to a prior runtime or ADB "
                    "target; no Surrender or battle action was taken"
                ),
            )
            if failed:
                self._active_exclusive_validation_request_id = None
                self._exclusive_validation_ownership_hold = False
                self._announce_exclusive_validation_result(failed)
                return failed
            refreshed = self._supervisor.exclusive_validation_receipt(
                request_id=request_id
            )
            if (
                refreshed is not None
                and str(refreshed.get("status") or "")
                in {"claimed", "running", "cleanup"}
            ):
                self._active_exclusive_validation_request_id = request_id
                self._exclusive_validation_ownership_hold = True
                return refreshed
            self._exclusive_validation_ownership_hold = False
            return refreshed
        self._exclusive_validation_ownership_hold = False
        return receipt

    def _finish_exclusive_validation_without_cleanup(
        self,
        receipt: Mapping[str, object],
        reason: str,
    ) -> Optional[Dict[str, object]]:
        request_id = str(receipt.get("request_id") or "")
        status = str(receipt.get("status") or "")
        result = self._supervisor.finish_exclusive_validation(
            request_id,
            outcome="failed",
            reason=reason,
            allowed_statuses=(status,),
        )
        if result:
            self._active_exclusive_validation_request_id = None
            if (
                getattr(
                    self,
                    "_exclusive_validation_battle_dispatch_hold",
                    None,
                )
                == request_id
            ):
                self._exclusive_validation_battle_dispatch_hold = None
            if (
                getattr(
                    self,
                    "_exclusive_validation_claimed_start_hold",
                    None,
                )
                == request_id
            ):
                self._exclusive_validation_claimed_start_hold = None
            self._announce_exclusive_validation_result(result)
        return result

    def _cancel_pending_tournament_validation_after_boundary(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Retire pre-Tournament work once the Tournament has started or ended."""

        state = str(detection.get("state") or "").upper()
        if self._tournament_battle_guard(detection):
            reason = (
                "the Tournament was already running when automation observed it; "
                "validation applies only before a Tournament begins"
            )
        elif state == "TOURNAMENT_RESULTS":
            reason = (
                "Tournament Results prove that the Tournament already completed; "
                "validation applies only before a Tournament begins"
            )
        else:
            return False

        receipt = self._reconcile_exclusive_validation()
        if (
            not isinstance(receipt, Mapping)
            or str(receipt.get("status") or "") != "pending"
        ):
            return False
        result = self._supervisor.finish_exclusive_validation(
            str(receipt.get("request_id") or ""),
            outcome="cancelled",
            reason=reason,
            allowed_statuses=("pending",),
        )
        if result is None:
            return False
        self._announce_exclusive_validation_result(result)
        return True

    def _prepare_exclusive_validation_home_request(
        self,
        definition: ExclusiveValidationDefinition,
    ) -> bool:
        """Re-arm Home checks once for the current valid pending request."""

        receipt = self._reconcile_exclusive_validation()
        if receipt is None or str(receipt.get("status") or "") != "pending":
            return False
        if (
            str(receipt.get("configuration_fingerprint") or "")
            != definition.configuration_fingerprint
        ):
            self._finish_exclusive_validation_without_cleanup(
                receipt,
                "strategy configuration changed after the explicit request; "
                "select Tournament again to authorize the new configuration",
            )
            return False
        request_id = str(receipt.get("request_id") or "")
        self._mission_mgr.prepare_exclusive_validation_request(request_id)
        self._startup_gate_waivers = {}
        return True

    def _maybe_start_exclusive_validation(
        self,
        *,
        home_control: HomeBattleControl,
    ) -> bool:
        """Claim and start exactly one verified ordinary validation battle."""

        if getattr(self, "_external_development_hold_active", False):
            return False

        definition = self._exclusive_validation_definition()
        if definition is None:
            return False
        receipt = self._reconcile_exclusive_validation()
        if receipt is None:
            return False
        if str(receipt.get("status") or "") != "pending":
            return False
        if (
            str(receipt.get("configuration_fingerprint") or "")
            != definition.configuration_fingerprint
        ):
            self._finish_exclusive_validation_without_cleanup(
                receipt,
                "strategy configuration changed after the explicit request; "
                "select Tournament again to authorize the new configuration",
            )
            return False
        if self._mission_mgr.prepare_exclusive_validation_request(
            str(receipt.get("request_id") or "")
        ):
            # Home handling normally prepares before it evaluates requirements.
            # A direct caller must recapture and complete that re-armed pass.
            return False
        if (
            home_control is not HomeBattleControl.NEW_BATTLE
            or self._mission_mgr.no_battle_setup_requirements()
            or self._supervisor.is_paused
        ):
            return False
        strategy_request_id = str(receipt.get("strategy_request_id") or "")
        claimed = self._supervisor.claim_exclusive_validation(
            strategy_request_id=strategy_request_id,
            configuration_fingerprint=definition.configuration_fingerprint,
            timeout_seconds=definition.timeout_seconds,
        )
        if claimed is None:
            return False
        request_id = str(claimed["request_id"])
        self._active_exclusive_validation_request_id = request_id
        current_holds = self._refreshed_operator_authority_holds(
            release_stale=False
        )
        active_owner = getattr(
            self,
            "_active_action_authority_owner",
            None,
        )
        workflow = self._supervisor.battle_workflow
        explicit_start_owner = bool(
            len(current_holds) == 1
            and current_holds[0].hold is AuthorityHold.OPERATOR_WORKFLOW
            and isinstance(workflow, Mapping)
            and workflow.get("intent") == "start_battle"
            and workflow.get("status") in {"acknowledged", "ready"}
        )
        source_owner = (
            AuthorityHold.OPERATOR_WORKFLOW
            if (
                active_owner is AuthorityHold.OPERATOR_WORKFLOW
                or explicit_start_owner
            )
            else AuthorityHold.EXCLUSIVE_VALIDATION
        )
        if not current_holds:
            current_holds += (
                AuthorityHoldState(
                    source_owner,
                    "exclusive validation owns its verified New Battle launch",
                ),
            )
        self._update_action_authority(holds=current_holds)
        self._publish_action_authority()
        log_action_intent(
            "Starting the one-shot Tournament validation battle",
            reason=(
                "use the verified ordinary New Battle control to inspect "
                "battle-only Tournament settings, then Surrender only this "
                "automation-owned battle"
            ),
            detail=f"[TOURNAMENT_VALIDATION] request_id={request_id}",
        )
        raw_launch = tap_verified_new_battle(
            action_guard_fn=lambda: bool(
                self._supervisor.owns_exclusive_validation(
                    request_id,
                    statuses=("claimed",),
                )
                and self._runtime_action_guard(
                    action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                    owner=source_owner,
                )
            ),
            return_dispatch_outcome=True,
        )
        launch_outcome = (
            raw_launch
            if isinstance(raw_launch, TapDispatchOutcome)
            else TapDispatchOutcome(
                TapDispatchStatus.DISPATCHED
                if raw_launch
                else TapDispatchStatus.NOT_DISPATCHED
            )
        )
        if launch_outcome.dispatched or launch_outcome.uncertain:
            # A claimed receipt alone proves authorization, not that the
            # Battle input reached its final mutation boundary. Retain this
            # process-local fact so a following blocking modal can borrow only
            # the exact launch that could have exposed it.
            self._exclusive_validation_battle_dispatch_hold = request_id
        workflow = self._supervisor.battle_workflow
        if launch_outcome.dispatched:
            operator_launch = bool(
                isinstance(workflow, Mapping)
                and workflow.get("intent") == "start_battle"
                and workflow.get("status") in {"acknowledged", "ready"}
            )
            marked = bool(
                not operator_launch
                or self._mark_operator_battle_action_dispatched(True)
            )
            if not marked:
                reason = (
                    "the owned validation Battle input was accepted, but its "
                    "durable action receipt could not be retained"
                )
                if isinstance(workflow, Mapping):
                    self._terminalize_uncertain_battle_workflow(
                        workflow,
                        self._current_control_workflow_evidence() or {},
                        reason=reason,
                    )
                else:
                    self._runtime_uncertain_mutation_result(reason)
                self._finish_exclusive_validation_without_cleanup(
                    claimed,
                    reason + "; no Surrender or replay was attempted",
                )
                return True
            log(
                "[TOURNAMENT_VALIDATION] Ordinary NEW_BATTLE dispatched after "
                f"durable ownership claim {request_id}",
                "DEBUG",
            )
            return True
        if launch_outcome.uncertain:
            reason = (
                "the owned validation Battle input may have reached the device, "
                "but its dispatch result was uncertain"
            )
            if isinstance(workflow, Mapping):
                self._terminalize_uncertain_battle_workflow(
                    workflow,
                    self._current_control_workflow_evidence() or {},
                    reason=reason,
                )
            else:
                self._runtime_uncertain_mutation_result(reason)
            # Keep the claimed receipt exact while Pause is durable. A later
            # fresh RUNNING/terminal/no-battle frame may resolve it, but the
            # Battle input itself is never replayed.
            return True
        if (
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "start_battle"
            and workflow.get("status") == "acknowledged"
        ):
            self._mission_mgr.revoke_initial_battle_intent(
                "start_battle",
                request_id=str(workflow.get("request_id") or ""),
            )
            self._supervisor.transition_battle_workflow(
                str(workflow.get("request_id") or ""),
                "failed",
                reason=(
                    "the verified ordinary New Battle validation control "
                    "could not be dispatched"
                ),
                acknowledgement=(
                    self._current_control_workflow_evidence() or {}
                ),
            )
        self._finish_exclusive_validation_without_cleanup(
            claimed,
            "the verified ordinary NEW_BATTLE control could not be tapped; "
            "no validation battle ownership was assumed",
        )
        return True

    @staticmethod
    def _ordinary_validation_battle_guard(
        detection: Mapping[str, object],
    ) -> bool:
        return bool(
            str(detection.get("state") or "").upper() == "RUNNING"
            and "TOURNAMENT"
            not in {
                str(value).upper()
                for value in detection.get("secondary_states", []) or []
            }
        )

    def _observe_exclusive_validation_battle_start(
        self,
        detection: Mapping[str, object],
        *,
        battle_started: bool,
    ) -> None:
        receipt = self._reconcile_exclusive_validation()
        if receipt is None:
            return
        status = str(receipt.get("status") or "")
        request_id = str(receipt.get("request_id") or "")
        if status == "running":
            if (
                getattr(
                    self,
                    "_exclusive_validation_battle_dispatch_hold",
                    None,
                )
                == request_id
            ):
                self._exclusive_validation_battle_dispatch_hold = None
            if (
                getattr(
                    self,
                    "_exclusive_validation_claimed_start_hold",
                    None,
                )
                == request_id
            ):
                self._exclusive_validation_claimed_start_hold = None
            if self._supervisor.owns_exclusive_validation(
                request_id,
                statuses=("running",),
            ):
                self._mission_mgr.set_exclusive_validation_battle(True)
            return
        if status != "claimed":
            if (
                getattr(
                    self,
                    "_exclusive_validation_battle_dispatch_hold",
                    None,
                )
                == request_id
            ):
                self._exclusive_validation_battle_dispatch_hold = None
            if (
                getattr(
                    self,
                    "_exclusive_validation_claimed_start_hold",
                    None,
                )
                == request_id
            ):
                self._exclusive_validation_claimed_start_hold = None
            return
        state = str(detection.get("state") or "").upper()
        retained_start = bool(
            request_id
            and getattr(
                self,
                "_exclusive_validation_claimed_start_hold",
                None,
            )
            == request_id
        )
        if state != "RUNNING":
            if retained_start:
                running = self._supervisor.mark_exclusive_validation_running(
                    request_id
                )
                if running is None:
                    return
                self._exclusive_validation_claimed_start_hold = None
                self._exclusive_validation_battle_dispatch_hold = None
                self._active_exclusive_validation_request_id = request_id
                self._mission_mgr.set_exclusive_validation_battle(True)
                if (
                    state in {"HOME", "HOME_SCREEN"}
                    and HomeBattleControl.parse(
                        detection.get("home_battle_control", "UNKNOWN")
                    )
                    is HomeBattleControl.RESUME_BATTLE
                ):
                    self._stage_exclusive_validation_release_without_cleanup(
                        running,
                        reason=(
                            "the owned validation battle returned to resumable "
                            "Home after its fresh start; refusing Resume, "
                            "Surrender, or navigation because the transition "
                            "is operator-controlled"
                        ),
                    )
                return
            try:
                deadline_at = float(receipt.get("deadline_at") or 0.0)
            except (TypeError, ValueError):
                deadline_at = 0.0
            if deadline_at and time.time() >= deadline_at:
                result = self._finish_exclusive_validation_without_cleanup(
                    receipt,
                    "the claimed ordinary NEW_BATTLE did not reach fresh "
                    "RUNNING evidence before its timeout; ownership is "
                    "ambiguous and no Surrender was attempted",
                )
                if (
                    result is not None
                    and self._exclusive_validation_requires_passive_battle_hold(
                        detection
                    )
                ):
                    self._retain_exclusive_validation_passive_battle(
                        request_id
                    )
            return
        ordinary_running = self._ordinary_validation_battle_guard(detection)
        if battle_started and ordinary_running:
            # MissionManager consumes a fresh start boundary exactly once. If
            # the durable claimed->running write is transiently unavailable,
            # retain that exact proof so the next stable RUNNING frame retries
            # the write instead of reinterpreting the owned battle as stale.
            self._exclusive_validation_claimed_start_hold = request_id
            retained_start = True
        if retained_start and not ordinary_running:
            running = self._supervisor.mark_exclusive_validation_running(
                request_id
            )
            if running is None:
                return
            self._exclusive_validation_claimed_start_hold = None
            self._exclusive_validation_battle_dispatch_hold = None
            self._active_exclusive_validation_request_id = request_id
            self._mission_mgr.set_exclusive_validation_battle(True)
            self._stage_exclusive_validation_release_without_cleanup(
                running,
                reason=(
                    "Tournament identity appeared after the claimed ordinary "
                    "battle start; refusing Surrender because the battle is "
                    "ambiguous"
                ),
            )
            return
        if not retained_start:
            result = self._finish_exclusive_validation_without_cleanup(
                receipt,
                "the claimed Home transition did not establish a new ordinary "
                "battle; ownership is ambiguous and no Surrender was attempted",
            )
            if (
                result is not None
                and self._exclusive_validation_requires_passive_battle_hold(
                    detection
                )
            ):
                self._retain_exclusive_validation_passive_battle(request_id)
            return
        running = self._supervisor.mark_exclusive_validation_running(request_id)
        if running is None:
            return
        self._exclusive_validation_claimed_start_hold = None
        self._exclusive_validation_battle_dispatch_hold = None
        self._active_exclusive_validation_request_id = request_id
        self._mission_mgr.set_exclusive_validation_battle(True)
        log(
            "[TOURNAMENT_VALIDATION] Owned ordinary battle reached RUNNING; "
            "Damage Slider and Ultimate Weapon validation is active",
            "DEBUG",
        )

    def _exclusive_validation_disposition(
        self,
        receipt: Mapping[str, object],
    ) -> Optional[Tuple[str, str]]:
        if getattr(self, "_pending_strategy_request", None) is not None:
            return (
                "failed",
                "a newer Strategy request was queued while exclusive "
                "validation owned the disposable battle",
            )
        receipt_strategy = str(receipt.get("strategy") or "").strip().lower()
        if receipt_strategy != self._current_strategy_name():
            return (
                "failed",
                "the active strategy changed after validation started",
            )
        mission_vars = self._mission_mgr.ctx.data.setdefault("mission_vars", {})
        if (
            mission_vars.get("damage_slider_checked")
            and mission_vars.get("gc_session_preflight_completed")
        ):
            return (
                "ready",
                "Damage Slider is 100% and all configured Ultimate Weapons, "
                "including Spotlight Missiles, are enabled",
            )
        if mission_vars.get("gc_session_preflight_attempted"):
            reason = str(
                mission_vars.get("gc_session_preflight_last_reason")
                or "Ultimate Weapon validation reported a mismatch"
            )
            return "failed", reason
        try:
            deadline_at = float(receipt.get("deadline_at") or 0.0)
        except (TypeError, ValueError):
            deadline_at = 0.0
        if deadline_at and time.time() >= deadline_at:
            damage = mission_vars.get("damage_slider_observation")
            damage_reason = (
                str(damage.get("reason") or "")
                if isinstance(damage, Mapping)
                else ""
            )
            detail = damage_reason or str(
                mission_vars.get("gc_session_preflight_last_reason") or ""
            )
            return (
                "failed",
                "validation timed out before battle-only checks completed"
                + (f": {detail}" if detail else ""),
            )
        return None

    def _exclusive_validation_in_progress(self) -> bool:
        receipt = self._exclusive_validation_receipt()
        if (
            receipt
            and str(receipt.get("status") or "") == "result"
            and getattr(self, "_exclusive_validation_terminal_hold", None)
            == str(receipt.get("request_id") or "")
        ):
            # The durable write completed after verified Home cleanup, but the
            # process-local mission boundary has not been released yet.
            return True
        request_id = str(receipt.get("request_id") or "") if receipt else ""
        return bool(
            receipt
            and str(receipt.get("status") or "")
            in {"claimed", "running", "cleanup"}
            and request_id
            == str(
                getattr(
                    self,
                    "_active_exclusive_validation_request_id",
                    None,
                )
                or ""
            )
            and not getattr(
                self,
                "_exclusive_validation_ownership_hold",
                False,
            )
        )

    def _exclusive_validation_cleanup_in_progress(self) -> bool:
        """Return whether exact cleanup or its local finalization is pending."""

        receipt = self._exclusive_validation_receipt()
        if (
            receipt
            and str(receipt.get("status") or "") == "result"
            and getattr(self, "_exclusive_validation_terminal_hold", None)
            == str(receipt.get("request_id") or "")
        ):
            return True
        request_id = str(receipt.get("request_id") or "") if receipt else ""
        return bool(
            receipt
            and str(receipt.get("status") or "") == "cleanup"
            and request_id
            == str(
                getattr(
                    self,
                    "_active_exclusive_validation_request_id",
                    None,
                )
                or ""
            )
            and not getattr(
                self,
                "_exclusive_validation_ownership_hold",
                False,
            )
        )

    def _exclusive_validation_blocks_strategy_application(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Keep Strategy replacement behind every exact validation boundary."""

        return bool(
            getattr(
                self,
                "_exclusive_validation_passive_battle_hold",
                None,
            )
            or getattr(
                self,
                "_exclusive_validation_battle_dispatch_hold",
                None,
            )
            or getattr(
                self,
                "_exclusive_validation_launch_dispatch_hold",
                None,
            )
            or getattr(self, "_exclusive_validation_ownership_hold", False)
            or getattr(
                self,
                "_exclusive_validation_claimed_start_hold",
                None,
            )
            or getattr(
                self,
                "_exclusive_validation_launch_start_hold",
                None,
            )
            or self._exclusive_validation_terminal_finalization_pending(
                detection
            )
            or self._exclusive_validation_in_progress()
        )

    def _observe_exclusive_validation_passive_battle_boundary(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Release a no-input battle hold only at authoritative boundaries."""

        request_id = str(
            getattr(
                self,
                "_exclusive_validation_passive_battle_hold",
                None,
            )
            or ""
        )
        if not request_id:
            return False
        if not self._exclusive_validation_passive_release_proven(detection):
            return True
        manager = getattr(self, "_mission_mgr", None)
        observe_release = getattr(
            manager,
            "observe_exclusive_validation_passive_release",
            None,
        )
        if callable(observe_release):
            observe_release(detection)
        self._exclusive_validation_passive_battle_hold = None
        self._exclusive_validation_passive_battle_scope_id = None
        log(
            "[TOURNAMENT_VALIDATION] Authoritative terminal/no-battle "
            "evidence released the passive validation battle hold",
            "INFO",
        )
        return False

    @staticmethod
    def _exclusive_validation_passive_release_proven(
        detection: Mapping[str, object],
    ) -> bool:
        """Recognize exact evidence that no dispatched battle remains active."""

        state = str(detection.get("state") or "").upper()
        verified_home_new_battle = bool(
            state in {"HOME", "HOME_SCREEN"}
            and HomeBattleControl.parse(
                detection.get("home_battle_control", "UNKNOWN")
            )
            is HomeBattleControl.NEW_BATTLE
        )
        return bool(
            state
            in {
                "GAME_OVER",
                "TOURNAMENT_RESULTS",
                "WORKSHOP",
                "TOURNAMENT_SCREEN",
            }
            or verified_home_new_battle
        )

    @staticmethod
    def _exclusive_validation_requires_passive_battle_hold(
        detection: Mapping[str, object],
    ) -> bool:
        """Fail closed after a dispatched start until no-battle proof exists."""

        return not App._exclusive_validation_passive_release_proven(
            detection
        )

    def _retain_exclusive_validation_passive_battle(
        self,
        request_id: str,
    ) -> None:
        """Suppress all automation input after an ambiguous owned launch."""

        normalized = str(request_id or "").strip()
        if not normalized:
            return
        self._exclusive_validation_passive_battle_hold = normalized
        self._exclusive_validation_passive_battle_scope_id = (
            self._current_run_scope_id()
        )
        self._mission_mgr.release_exclusive_validation_battle_without_boundary()

    @staticmethod
    def _exclusive_validation_home_cleanup_verified(
        detection: Mapping[str, object],
    ) -> bool:
        """Recognize fresh Home evidence that the owned cleanup completed."""

        return bool(
            str(detection.get("state") or "").upper()
            in {"HOME", "HOME_SCREEN"}
            and HomeBattleControl.parse(
                detection.get("home_battle_control", "UNKNOWN")
            )
            is HomeBattleControl.NEW_BATTLE
        )

    def _exclusive_validation_terminal_proof_kind(
        self,
        detection: Mapping[str, object],
    ) -> Optional[str]:
        """Classify fresh no-battle proof for one owned running receipt."""

        state = str(detection.get("state") or "").upper()
        if state == "GAME_OVER":
            return "game_over"
        if self._exclusive_validation_home_cleanup_verified(detection):
            return "verified_home"
        if state == "TOURNAMENT_RESULTS":
            return "tournament_results"
        if state == "WORKSHOP":
            return "workshop_no_battle"
        if state == "TOURNAMENT_SCREEN":
            return "tournament_entry_no_battle"
        return None

    def _exclusive_validation_terminal_finalization_pending(
        self,
        detection: Optional[Mapping[str, object]] = None,
    ) -> bool:
        """Return whether exact terminal work must precede new work."""

        receipt = self._exclusive_validation_receipt()
        if receipt is None:
            return False
        request_id = str(receipt.get("request_id") or "")
        status = str(receipt.get("status") or "")
        if (
            request_id
            and getattr(self, "_exclusive_validation_terminal_hold", None)
            == request_id
            and status in {"cleanup", "result"}
        ):
            return True
        if (
            status in {"claimed", "running"}
            and detection is not None
            and request_id
            == str(
                getattr(
                    self,
                    "_active_exclusive_validation_request_id",
                    None,
                )
                or ""
            )
            and (
                status == "running"
                or getattr(
                    self,
                    "_exclusive_validation_claimed_start_hold",
                    None,
                )
                == request_id
            )
            and (
                self._exclusive_validation_terminal_proof_kind(detection)
                is not None
            )
        ):
            # A natural terminal can arrive before the late validation advance.
            # Quarantine it before MissionManager consumes the boundary, then
            # atomically promote the exact running receipt to cleanup.
            return True
        if (
            status in {"claimed", "running"}
            and request_id
            and getattr(self, "_exclusive_validation_terminal_hold", None)
            == request_id
            and getattr(
                self,
                "_exclusive_validation_terminal_mode",
                None,
            )
            == "running_terminal_observed"
        ):
            return True
        return bool(
            status == "cleanup"
            and detection is not None
            and self._exclusive_validation_home_cleanup_verified(detection)
        )

    def _exclusive_validation_cleanup_dispatch_ready(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Allow only Game Over cleanup or proof-backed result persistence."""

        if str(detection.get("state") or "").upper() == "GAME_OVER":
            return True
        receipt = self._exclusive_validation_receipt()
        if receipt is None:
            return False
        request_id = str(receipt.get("request_id") or "")
        terminal_hold = getattr(
            self,
            "_exclusive_validation_terminal_hold",
            None,
        )
        terminal_mode = str(
            getattr(self, "_exclusive_validation_terminal_mode", None)
            or "home_cleanup"
        )
        if (
            str(receipt.get("status") or "") == "result"
            and terminal_hold == request_id
        ):
            # A retained terminal claim is exact process-local proof that the
            # physical cleanup already completed (or that no further cleanup
            # input is allowed).  Dispatch it before observing a possibly
            # operator-started successor, regardless of the successor's screen.
            return True
        if str(receipt.get("status") or "") != "cleanup":
            return False
        return bool(
            request_id
            and (
                (
                    terminal_hold == request_id
                    and (
                        terminal_mode
                        in {
                            "home_cleanup",
                            "game_over_release",
                            "release_without_cleanup",
                        }
                    )
                )
                or self._exclusive_validation_home_cleanup_verified(detection)
            )
        )

    def _quarantine_exclusive_validation_terminal_finalization(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Finalize an exact old receipt before observing successor lifecycle.

        Pause may coexist with a manually started successor.  Detection remains
        live, but MissionManager and continuity must not adopt that successor
        until the validation receipt has durably completed and released its
        process-local boundary.
        """

        if not self._exclusive_validation_terminal_finalization_pending(
            detection
        ):
            return False
        self._retain_exclusive_validation_running_terminal_proof(detection)
        self._retire_exclusive_validation_cleanup_after_later_running(
            detection
        )
        self._update_action_authority(
            detection=detection,
            holds=(
                AuthorityHoldState(
                    AuthorityHold.EXCLUSIVE_VALIDATION,
                    "exclusive validation is finalizing its exact receipt",
                ),
            ),
        )
        self._publish_action_authority()
        if (
            self._action_decision(
                RuntimeActionClass.LIFECYCLE_ACTION,
                owner=AuthorityHold.EXCLUSIVE_VALIDATION,
            ).allowed
        ):
            previous_owner = getattr(
                self,
                "_active_action_authority_owner",
                None,
            )
            self._active_action_authority_owner = (
                AuthorityHold.EXCLUSIVE_VALIDATION
            )
            try:
                self._promote_exclusive_validation_running_terminal(
                    detection
                )
                if self._exclusive_validation_cleanup_dispatch_ready(
                    detection
                ):
                    self._dispatch_exclusive_validation_game_over(detection)
            finally:
                self._active_action_authority_owner = previous_owner
        return True

    def _retain_exclusive_validation_running_terminal_proof(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Remember fresh terminal evidence even while Pause blocks writes."""

        proof_kind = self._exclusive_validation_terminal_proof_kind(detection)
        if proof_kind is None:
            return False
        receipt = self._exclusive_validation_receipt()
        if receipt is None or str(receipt.get("status") or "") not in {
            "claimed",
            "running",
        }:
            return False
        request_id = str(receipt.get("request_id") or "")
        if request_id != str(
            getattr(
                self,
                "_active_exclusive_validation_request_id",
                None,
            )
            or ""
        ):
            return False
        if (
            str(receipt.get("status") or "") == "claimed"
            and getattr(
                self,
                "_exclusive_validation_claimed_start_hold",
                None,
            )
            != request_id
        ):
            # A bare claim proves tap ownership, not that a battle actually
            # started. Only MissionManager's retained fresh-start boundary may
            # bind later terminal evidence to that claim.
            return False
        disposition = (
            self._exclusive_validation_disposition(receipt)
            if proof_kind in {"game_over", "verified_home"}
            else None
        )
        if disposition is None:
            outcome = "failed"
            reason = (
                "the owned validation battle ended before its battle-only "
                "checks completed"
            )
        else:
            outcome, reason = disposition
        proof = {
            "game_over": "fresh Game Over",
            "verified_home": "fresh verified Home NEW_BATTLE",
            "tournament_results": "fresh Tournament Results",
            "workshop_no_battle": "fresh Workshop no-battle evidence",
            "tournament_entry_no_battle": (
                "fresh Tournament-entry no-battle evidence"
            ),
        }[proof_kind]
        reason += (
            f"; {proof} proved the owned battle ended before guarded "
            "Surrender, so the terminal boundary was retained without "
            "another battle action"
        )
        self._stage_exclusive_validation_terminal_result(
            receipt,
            mode="running_terminal_observed",
            outcome=outcome,
            reason=reason,
        )
        self._exclusive_validation_terminal_proof_kind_value = proof_kind
        return True

    def _promote_exclusive_validation_running_terminal(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Consume natural terminal proof before ordinary lifecycle observes it."""

        proof_kind = self._exclusive_validation_terminal_proof_kind(detection)
        receipt = self._reconcile_exclusive_validation()
        if receipt is None:
            return False
        if str(receipt.get("status") or "") in {"cleanup", "result"}:
            return True
        request_id = str(receipt.get("request_id") or "")
        if (
            str(receipt.get("status") or "") == "claimed"
            and getattr(
                self,
                "_exclusive_validation_claimed_start_hold",
                None,
            )
            == request_id
        ):
            # The fresh ordinary start was already consumed by MissionManager.
            # Retry only the durable claim transition before consuming its
            # retained terminal proof; never require a second fresh start.
            running = self._supervisor.mark_exclusive_validation_running(
                request_id
            )
            if running is None:
                return False
            receipt = running
            self._exclusive_validation_claimed_start_hold = None
            self._exclusive_validation_battle_dispatch_hold = None
            self._active_exclusive_validation_request_id = request_id
            self._mission_mgr.set_exclusive_validation_battle(True)
        if (
            str(receipt.get("status") or "") != "running"
            or getattr(
                self,
                "_exclusive_validation_ownership_hold",
                False,
            )
        ):
            return False
        retained_exact = bool(
            getattr(self, "_exclusive_validation_terminal_hold", None)
            == str(receipt.get("request_id") or "")
            and getattr(
                self,
                "_exclusive_validation_terminal_mode",
                None,
            )
            == "running_terminal_observed"
        )
        if proof_kind is None and not retained_exact:
            return False
        if proof_kind is None:
            proof_kind = str(
                getattr(
                    self,
                    "_exclusive_validation_terminal_proof_kind_value",
                    None,
                )
                or "game_over"
            )
        if retained_exact:
            outcome = str(
                getattr(
                    self,
                    "_exclusive_validation_terminal_outcome",
                    None,
                )
                or "failed"
            )
            reason = str(
                getattr(
                    self,
                    "_exclusive_validation_terminal_reason",
                    None,
                )
                or "the owned validation battle ended"
            )
        else:
            self._retain_exclusive_validation_running_terminal_proof(detection)
            outcome = str(
                getattr(self, "_exclusive_validation_terminal_outcome", None)
                or "failed"
            )
            reason = str(
                getattr(self, "_exclusive_validation_terminal_reason", None)
                or "the owned validation battle ended"
            )
        cleanup = self._supervisor.begin_exclusive_validation_cleanup(
            request_id,
            outcome=outcome,
            reason=reason,
        )
        if cleanup is None:
            return False
        self._stage_exclusive_validation_terminal_result(
            cleanup,
            mode=(
                "game_over_observed"
                if proof_kind in {"game_over", "verified_home"}
                else "game_over_release"
            ),
            outcome=outcome,
            reason=reason,
        )
        self._exclusive_validation_terminal_proof_kind_value = proof_kind
        if proof_kind not in {"game_over", "verified_home"}:
            self._retry_exclusive_validation_terminal_result(cleanup)
        log(
            "[TOURNAMENT_VALIDATION] Natural terminal proof closed the "
            "owned validation battle without Surrender input",
            "WARN",
            console=True,
        )
        return True

    def _retire_exclusive_validation_cleanup_after_later_running(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Close proven Game Over cleanup before observing a later battle."""

        state = str(detection.get("state") or "").upper()
        later_running = state == "RUNNING"
        later_resumable_home = bool(
            state in {"HOME", "HOME_SCREEN"}
            and HomeBattleControl.parse(
                detection.get("home_battle_control", "UNKNOWN")
            )
            is HomeBattleControl.RESUME_BATTLE
        )
        later_tournament_results = state == "TOURNAMENT_RESULTS"
        later_workshop = state == "WORKSHOP"
        later_tournament_entry = state == "TOURNAMENT_SCREEN"
        if not (
            later_running
            or later_resumable_home
            or later_tournament_results
            or later_workshop
            or later_tournament_entry
        ):
            return False
        receipt = self._exclusive_validation_receipt()
        if receipt is None:
            return False
        request_id = str(receipt.get("request_id") or "")
        if (
            str(receipt.get("status") or "") != "cleanup"
            or getattr(self, "_exclusive_validation_terminal_hold", None)
            != request_id
            or getattr(self, "_exclusive_validation_terminal_mode", None)
            != "game_over_observed"
        ):
            return False
        prior_reason = str(
            getattr(self, "_exclusive_validation_terminal_reason", None)
            or receipt.get("pending_reason")
            or "validation cleanup was pending"
        )
        later_evidence = (
            "a later RUNNING screen"
            if later_running
            else "a later resumable Home screen"
            if later_resumable_home
            else "later Tournament Results"
            if later_tournament_results
            else "later Workshop no-battle evidence"
            if later_workshop
            else "later Tournament-entry no-battle evidence"
        )
        retained_outcome = str(
            getattr(self, "_exclusive_validation_terminal_outcome", None)
            or receipt.get("pending_outcome")
            or "failed"
        )
        outcome = (
            "failed"
            if later_running
            or later_resumable_home
            or later_tournament_results
            else retained_outcome
        )
        reason = (
            prior_reason
            + "; after the owned validation battle conclusively reached Game "
            f"Over, {later_evidence} appeared before verified Home "
            "cleanup; the old cleanup was closed without further battle input"
        )
        self._stage_exclusive_validation_terminal_result(
            receipt,
            mode="game_over_release",
            outcome=outcome,
            reason=reason,
        )
        log(
            "[TOURNAMENT_VALIDATION] Later active-battle evidence followed "
            "the owned Game Over boundary; closing the old cleanup without "
            "input",
            "WARN",
            console=True,
        )
        return True

    def _exclusive_validation_blocks_target_handoff(self) -> bool:
        """Keep a durable validation route bound to its current ADB target."""

        if any(
            str(getattr(self, field, None) or "").strip()
            for field in (
                "_exclusive_validation_terminal_hold",
                "_exclusive_validation_battle_dispatch_hold",
                "_exclusive_validation_launch_dispatch_hold",
                "_exclusive_validation_claimed_start_hold",
                "_exclusive_validation_launch_start_hold",
                "_exclusive_validation_passive_battle_hold",
                "_active_exclusive_validation_request_id",
                "_active_exclusive_validation_launch_request_id",
            )
        ) or getattr(
            self,
            "_exclusive_validation_ownership_hold",
            False,
        ):
            return True
        if getattr(self, "_supervisor", None) is None or getattr(
            self,
            "_mission_mgr",
            None,
        ) is None:
            return False
        receipt = self._exclusive_validation_receipt()
        launch = receipt.get("launch") if receipt else None
        if isinstance(launch, Mapping) and str(
            launch.get("status") or ""
        ) in {"awaiting_operator", "requested", "claimed"}:
            # A ready prompt is bound to the ADB target on which validation
            # completed. Do not let a paused handoff silently turn that proof
            # into launch authority on a different device.
            return True
        return bool(
            self._exclusive_validation_in_progress()
            or self._exclusive_validation_launch_in_progress()
        )

    def _clear_exclusive_validation_terminal_claim(self) -> None:
        self._exclusive_validation_terminal_hold = None
        self._exclusive_validation_terminal_mode = None
        self._exclusive_validation_terminal_outcome = None
        self._exclusive_validation_terminal_reason = None
        self._exclusive_validation_terminal_announced = None
        self._exclusive_validation_terminal_proof_kind_value = None

    def _stage_exclusive_validation_terminal_result(
        self,
        receipt: Mapping[str, object],
        *,
        mode: str,
        outcome: str,
        reason: str,
    ) -> None:
        """Retain exact proof until its result is durably observable."""

        request_id = str(receipt.get("request_id") or "")
        self._active_exclusive_validation_request_id = request_id
        if (
            getattr(
                self,
                "_exclusive_validation_battle_dispatch_hold",
                None,
            )
            == request_id
        ):
            self._exclusive_validation_battle_dispatch_hold = None
        self._exclusive_validation_terminal_hold = request_id
        self._exclusive_validation_terminal_mode = mode
        self._exclusive_validation_terminal_outcome = outcome
        self._exclusive_validation_terminal_reason = reason

    def _retry_exclusive_validation_terminal_result(
        self,
        receipt: Mapping[str, object],
    ) -> Optional[Dict[str, object]]:
        """Retry only the exact terminal receipt write; never send input."""

        request_id = str(receipt.get("request_id") or "")
        if (
            not request_id
            or getattr(self, "_exclusive_validation_terminal_hold", None)
            != request_id
            or str(receipt.get("status") or "") != "cleanup"
        ):
            return None
        outcome = str(
            getattr(self, "_exclusive_validation_terminal_outcome", None)
            or receipt.get("pending_outcome")
            or "failed"
        )
        reason = str(
            getattr(self, "_exclusive_validation_terminal_reason", None)
            or receipt.get("pending_reason")
            or "validation completed"
        )
        result = self._supervisor.finish_exclusive_validation(
            request_id,
            outcome=outcome,
            reason=reason,
            allowed_statuses=("cleanup",),
        )
        if (
            result is not None
            and getattr(
                self,
                "_exclusive_validation_terminal_announced",
                None,
            )
            != request_id
        ):
            self._announce_exclusive_validation_result(result)
            self._exclusive_validation_terminal_announced = request_id
        return result

    def _stage_exclusive_validation_release_without_cleanup(
        self,
        receipt: Mapping[str, object],
        *,
        reason: str,
    ) -> bool:
        """Retire ambiguous active work without claiming terminal input."""

        cleanup = self._supervisor.begin_exclusive_validation_cleanup(
            str(receipt.get("request_id") or ""),
            outcome="failed",
            reason=reason,
        )
        if cleanup is None:
            return False
        self._stage_exclusive_validation_terminal_result(
            cleanup,
            mode="release_without_cleanup",
            outcome="failed",
            reason=reason,
        )
        self._retry_exclusive_validation_terminal_result(cleanup)
        return True

    def _advance_exclusive_validation(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Conclude validation and Surrender only its still-owned battle."""

        if getattr(self, "_external_development_hold_active", False):
            return False

        receipt = self._reconcile_exclusive_validation()
        if receipt is None or str(receipt.get("status") or "") != "running":
            return False
        request_id = str(receipt.get("request_id") or "")
        if not self._ordinary_validation_battle_guard(detection):
            state = str(detection.get("state") or "").upper()
            if state == "RUNNING":
                reason = (
                    "Tournament identity appeared in the owned validation "
                    "window; refusing Surrender because the battle is ambiguous"
                )
                return self._stage_exclusive_validation_release_without_cleanup(
                    receipt,
                    reason=reason,
                )
            if (
                state in {"HOME", "HOME_SCREEN"}
                and HomeBattleControl.parse(
                    detection.get("home_battle_control", "UNKNOWN")
                )
                is HomeBattleControl.RESUME_BATTLE
            ):
                return self._stage_exclusive_validation_release_without_cleanup(
                    receipt,
                    reason=(
                        "the owned validation battle unexpectedly returned to "
                        "resumable Home before its checks completed; refusing "
                        "Surrender or navigation because the transition is "
                        "operator-controlled"
                    ),
                )
            return False
        disposition = self._exclusive_validation_disposition(receipt)
        if disposition is None or self._supervisor.is_paused:
            return False
        outcome, reason = disposition
        cleanup = self._supervisor.begin_exclusive_validation_cleanup(
            request_id,
            outcome=outcome,
            reason=reason,
        )
        if cleanup is None:
            return False
        log(
            "[TOURNAMENT_VALIDATION] Cleaning up the owned ordinary battle "
            f"after validation reached {outcome}",
            "DEBUG",
        )
        surrendered = surrender_run(
            timeout_s=12.0,
            action_guard=lambda: bool(
                self._supervisor.owns_exclusive_validation(
                    request_id,
                    statuses=("cleanup",),
                )
                and self._runtime_action_guard(
                    action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                    owner=AuthorityHold.EXCLUSIVE_VALIDATION,
                )
            ),
            running_guard=self._ordinary_validation_battle_guard,
        )
        if surrendered:
            self._stage_exclusive_validation_terminal_result(
                cleanup,
                mode="game_over_observed",
                outcome=outcome,
                reason=reason,
            )
            log(
                "[TOURNAMENT_VALIDATION] Owned validation battle reached Game Over",
                "DEBUG",
            )
            return True
        terminal_reason = (
            reason
            + "; guarded Surrender did not conclusively reach Game Over, so "
            "no retry or further battle action was attempted"
        )
        self._stage_exclusive_validation_terminal_result(
            cleanup,
            mode="release_without_cleanup",
            outcome="failed",
            reason=terminal_reason,
        )
        self._retry_exclusive_validation_terminal_result(cleanup)
        return True

    def _handle_exclusive_validation_game_over(
        self,
        *,
        home_cleanup_verified: bool = False,
    ) -> bool:
        """Complete exact receipt cleanup without repeating a proven Home tap."""

        receipt = self._reconcile_exclusive_validation()
        if receipt is None or str(receipt.get("status") or "") != "cleanup":
            return False
        request_id = str(receipt.get("request_id") or "")
        if not self._supervisor.owns_exclusive_validation(
            request_id,
            statuses=("cleanup",),
        ):
            return False
        terminal_hold = getattr(
            self,
            "_exclusive_validation_terminal_hold",
            None,
        )
        terminal_mode = str(
            getattr(self, "_exclusive_validation_terminal_mode", None)
            or ""
        )
        if terminal_hold not in {None, request_id}:
            # A process-local proof can authorize persistence only for the
            # exact receipt that produced it.
            self._clear_exclusive_validation_terminal_claim()
            terminal_hold = None
        cleanup_complete = bool(
            (
                terminal_hold == request_id
                and terminal_mode == "home_cleanup"
            )
            or home_cleanup_verified
        )
        if not cleanup_complete:
            cleanup_complete = return_home_from_game_over(
                timeout_s=8.0,
                action_guard=lambda: bool(
                    self._supervisor.owns_exclusive_validation(
                        request_id,
                        statuses=("cleanup",),
                    )
                    and self._runtime_action_guard(
                        action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                        owner=AuthorityHold.EXCLUSIVE_VALIDATION,
                    )
                ),
            )
        outcome = str(receipt.get("pending_outcome") or "failed")
        reason = str(receipt.get("pending_reason") or "validation completed")
        if not cleanup_complete:
            reason += "; the owned battle ended, but verified NEW_BATTLE Home was not reached"
            # The source is still a known Game Over boundary and the cleanup
            # lease remains exact.  Failure to tap Home is retryable—not loss
            # of input authority.  Retain cleanup and try again on a later
            # fresh frame without globally Pausing automation.
            if terminal_mode == "game_over_observed":
                self._exclusive_validation_terminal_reason = reason
            else:
                self._clear_exclusive_validation_terminal_claim()
            if not self._supervisor.is_paused:
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                    reason,
                )
            return True
        # Keep exact process-local proof across a failed/uncertain result write.
        # The next heartbeat may be Home rather than Game Over, so it must
        # retry only persistence and must never repeat the terminal tap.
        self._stage_exclusive_validation_terminal_result(
            receipt,
            mode="home_cleanup",
            outcome=outcome,
            reason=reason,
        )
        self._retry_exclusive_validation_terminal_result(receipt)
        return True

    def _finalize_exclusive_validation_cleanup_boundary(
        self,
        receipt: Mapping[str, object],
    ) -> None:
        """Release process-local state only after the result is durable."""

        request_id = str(receipt.get("request_id") or "")
        mode = str(
            getattr(self, "_exclusive_validation_terminal_mode", None)
            or "home_cleanup"
        )
        game_over_boundary = mode in {
            "game_over_release",
            "home_cleanup",
        }
        if (
            getattr(
                self,
                "_exclusive_validation_terminal_announced",
                None,
            )
            != request_id
        ):
            self._announce_exclusive_validation_result(receipt)
            self._exclusive_validation_terminal_announced = request_id
        if game_over_boundary:
            self._mission_mgr.finalize_exclusive_validation_game_over_boundary()
            self._mission_mgr.set_exclusive_validation_battle(False)
        else:
            if mode == "release_without_cleanup":
                self._retain_exclusive_validation_passive_battle(
                    request_id
                )
            else:
                self._mission_mgr.release_exclusive_validation_battle_without_boundary()
        if game_over_boundary:
            self._status_reporter.reset_coin_rate_samples()
            self._strategy_boundary_confirmed = True
        self._active_exclusive_validation_request_id = None
        self._clear_exclusive_validation_terminal_claim()
        if game_over_boundary:
            self._apply_pending_strategy()

    def _dispatch_exclusive_validation_game_over(
        self,
        detection: Optional[Mapping[str, object]] = None,
    ) -> bool:
        """Run only the receipt-owned Game Over cleanup lifecycle."""

        receipt = self._reconcile_exclusive_validation()
        if receipt is None:
            return False
        request_id = str(receipt.get("request_id") or "")
        status = str(receipt.get("status") or "")
        if status == "result":
            if (
                getattr(self, "_exclusive_validation_terminal_hold", None)
                != request_id
            ):
                return False
            self._finalize_exclusive_validation_cleanup_boundary(receipt)
            return True
        if status != "cleanup":
            return False
        terminal_mode = str(
            getattr(self, "_exclusive_validation_terminal_mode", None)
            or "home_cleanup"
        )
        if (
            getattr(self, "_exclusive_validation_terminal_hold", None)
            == request_id
            and terminal_mode
            in {"game_over_release", "release_without_cleanup"}
        ):
            self._retry_exclusive_validation_terminal_result(receipt)
        elif not self._handle_exclusive_validation_game_over(
            home_cleanup_verified=(
                detection is not None
                and self._exclusive_validation_home_cleanup_verified(detection)
            )
        ):
            return False
        receipt = self._reconcile_exclusive_validation()
        if (
            receipt is not None
            and str(receipt.get("status") or "") == "cleanup"
        ):
            # Home was not reached or the result write did not persist. The
            # exact receipt and any process-local Home proof retain this owner
            # for a later fresh frame without repeating a proven terminal tap.
            return True
        if receipt is None or str(receipt.get("status") or "") != "result":
            return False
        self._finalize_exclusive_validation_cleanup_boundary(receipt)
        return True

    def _announce_exclusive_validation_result(
        self,
        receipt: Mapping[str, object],
    ) -> None:
        definition = self._exclusive_validation_definition()
        outcome = str(receipt.get("outcome") or "")
        reason = str(receipt.get("reason") or "reason unavailable")
        if outcome == "ready" and definition is not None:
            log_result(
                f"Tournament validation complete — {definition.ready_message}",
                detail=(
                    f"[TOURNAMENT_VALIDATION] result=ready "
                    f"request_id={receipt.get('request_id')} reason={reason}"
                ),
            )
            return
        if outcome == "cancelled":
            log_result(
                "No Tournament validation is planned — " + reason,
                detail=(
                    f"[TOURNAMENT_VALIDATION] result=cancelled "
                    f"request_id={receipt.get('request_id')} reason={reason}"
                ),
            )
            return
        prefix = (
            definition.failure_prefix
            if definition is not None
            else "Tournament validation failed"
        )
        log(
            f"[TOURNAMENT_VALIDATION_FAILED] {prefix}: {reason}",
            "ERROR",
            console=True,
        )
        log_result(
            f"{prefix} — {reason}",
            detail=(
                f"[TOURNAMENT_VALIDATION] result=failed "
                f"request_id={receipt.get('request_id')} reason={reason}"
            ),
        )

    def _reconcile_exclusive_validation_launch(
        self,
    ) -> Optional[Dict[str, object]]:
        """Recover only same-owner launch work; fail prior ownership closed."""

        ledger = self._supervisor.exclusive_validation
        receipts = ledger.get("receipts")
        if isinstance(receipts, Mapping):
            for candidate in list(receipts.values()):
                if not isinstance(candidate, Mapping):
                    continue
                launch = candidate.get("launch")
                if (
                    not isinstance(launch, Mapping)
                    or launch.get("status") != "claimed"
                ):
                    continue
                candidate_id = str(candidate.get("request_id") or "")
                if self._supervisor.owns_exclusive_validation_launch(
                    candidate_id
                ):
                    self._active_exclusive_validation_launch_request_id = (
                        candidate_id
                    )
                    self._exclusive_validation_ownership_hold = False
                    return self._supervisor.exclusive_validation_receipt(
                        request_id=candidate_id
                    )
                failed = (
                    self._supervisor.fail_orphaned_exclusive_validation_launch(
                        candidate_id,
                        reason=(
                            "Tournament launch ownership belongs to a prior "
                            "runtime or ADB target; no navigation or battle "
                            "input was attempted"
                        ),
                    )
                )
                if failed:
                    self._active_exclusive_validation_launch_request_id = None
                    self._clear_exclusive_validation_launch_dispatch_proof(
                        candidate_id
                    )
                    self._clear_exclusive_validation_launch_start_proof(
                        candidate_id
                    )
                    self._exclusive_validation_ownership_hold = False
                    self._announce_exclusive_validation_launch(failed)
                    continue
                refreshed = self._supervisor.exclusive_validation_receipt(
                    request_id=candidate_id
                )
                refreshed_launch = (
                    refreshed.get("launch") if refreshed else None
                )
                if not (
                    isinstance(refreshed_launch, Mapping)
                    and refreshed_launch.get("status") == "claimed"
                ):
                    self._clear_exclusive_validation_launch_dispatch_proof(
                        candidate_id
                    )
                    self._clear_exclusive_validation_launch_start_proof(
                        candidate_id
                    )
                    self._exclusive_validation_ownership_hold = False
                    continue
                self._active_exclusive_validation_launch_request_id = (
                    candidate_id
                )
                self._exclusive_validation_ownership_hold = True
                return refreshed

        receipt = self._exclusive_validation_receipt()
        if receipt is None:
            self._active_exclusive_validation_launch_request_id = None
            return None
        request_id = str(receipt.get("request_id") or "")
        launch = receipt.get("launch")
        if not isinstance(launch, Mapping):
            self._clear_exclusive_validation_launch_dispatch_proof(
                request_id
            )
            self._clear_exclusive_validation_launch_start_proof(request_id)
            if request_id == str(
                getattr(
                    self,
                    "_active_exclusive_validation_launch_request_id",
                    None,
                )
                or ""
            ):
                self._active_exclusive_validation_launch_request_id = None
                self._exclusive_validation_ownership_hold = False
            return None
        launch_status = str(launch.get("status") or "")
        validation_owner = receipt.get("owner")
        current_owner = self._supervisor.current_exclusive_validation_owner()
        validated_target = (
            str(validation_owner.get("adb_target") or "").strip()
            if isinstance(validation_owner, Mapping)
            else ""
        )
        current_target = str(
            current_owner.get("adb_target") or ""
        ).strip()
        if (
            launch_status in {"awaiting_operator", "requested"}
            and validated_target
            and current_target
            and validated_target != current_target
        ):
            failed = (
                self._supervisor.fail_unclaimed_exclusive_validation_launch(
                    request_id,
                    reason=(
                        "Tournament launch prompt belongs to validated ADB "
                        f"target {validated_target}, not current target "
                        f"{current_target}; no navigation or battle input was "
                        "attempted"
                    ),
                )
            )
            if failed is None:
                # Keep the prompt non-actionable. The control-store claim also
                # requires target equality, so a failed report cannot grant
                # launch input on this target.
                return receipt
            self._clear_exclusive_validation_launch_start_proof(request_id)
            self._clear_exclusive_validation_launch_dispatch_proof(request_id)
            self._active_exclusive_validation_launch_request_id = None
            self._announce_exclusive_validation_launch(failed)
            return failed
        if launch_status not in {
            "awaiting_operator",
            "requested",
            "claimed",
        }:
            self._clear_exclusive_validation_launch_dispatch_proof(request_id)
            self._clear_exclusive_validation_launch_start_proof(request_id)
        if launch_status != "claimed":
            if (
                request_id
                != str(
                    getattr(
                        self,
                        "_exclusive_validation_launch_start_hold",
                        None,
                    )
                    or ""
                )
                and request_id
                == str(
                    getattr(
                        self,
                        "_active_exclusive_validation_launch_request_id",
                        None,
                    )
                    or ""
                )
            ):
                self._active_exclusive_validation_launch_request_id = None
            self._exclusive_validation_ownership_hold = False
        return receipt

    def _exclusive_validation_launch_in_progress(self) -> bool:
        """Return whether a confirmed launch request owns the next input."""

        receipt = self._exclusive_validation_receipt()
        launch = receipt.get("launch") if receipt else None
        if not isinstance(launch, Mapping):
            return False
        status = str(launch.get("status") or "")
        request_id = str(receipt.get("request_id") or "")
        if (
            request_id
            and getattr(
                self,
                "_exclusive_validation_launch_start_hold",
                None,
            )
            == request_id
            and status in {"awaiting_operator", "requested", "claimed"}
        ):
            # MissionManager consumes the fresh battle boundary once. Keep the
            # launch as this heartbeat's sole owner until its exact started
            # receipt can be persisted, even when Pause delayed that write.
            return not getattr(
                self,
                "_exclusive_validation_ownership_hold",
                False,
            )
        if status == "requested":
            return True
        return bool(
            status == "claimed"
            and request_id
            == str(
                getattr(
                    self,
                    "_active_exclusive_validation_launch_request_id",
                    None,
                )
                or ""
            )
            and not getattr(
                self,
                "_exclusive_validation_ownership_hold",
                False,
            )
        )

    @staticmethod
    def _tournament_battle_guard(
        detection: Mapping[str, object],
    ) -> bool:
        return bool(
            str(detection.get("state") or "").upper() == "RUNNING"
            and "TOURNAMENT"
            in {
                str(value).upper()
                for value in detection.get("secondary_states", []) or []
            }
        )

    def _retain_exclusive_validation_launch_start_proof(
        self,
        detection: Mapping[str, object],
        *,
        battle_started: bool,
    ) -> bool:
        """Retain one fresh Tournament start until its receipt is durable."""

        if not battle_started or not self._tournament_battle_guard(detection):
            return False
        receipt = self._exclusive_validation_receipt()
        launch = receipt.get("launch") if receipt else None
        if not isinstance(launch, Mapping) or str(
            launch.get("status") or ""
        ) not in {"awaiting_operator", "requested", "claimed"}:
            return False
        request_id = str(receipt.get("request_id") or "")
        if not request_id:
            return False
        self._exclusive_validation_launch_start_hold = request_id
        self._clear_exclusive_validation_launch_dispatch_proof(request_id)
        if str(launch.get("status") or "") == "claimed":
            self._active_exclusive_validation_launch_request_id = request_id
        return True

    def _clear_exclusive_validation_launch_start_proof(
        self,
        request_id: str,
    ) -> None:
        if (
            getattr(
                self,
                "_exclusive_validation_launch_start_hold",
                None,
            )
            == str(request_id or "")
        ):
            self._exclusive_validation_launch_start_hold = None

    def _clear_exclusive_validation_launch_dispatch_proof(
        self,
        request_id: str,
    ) -> None:
        if (
            getattr(
                self,
                "_exclusive_validation_launch_dispatch_hold",
                None,
            )
            == str(request_id or "")
        ):
            self._exclusive_validation_launch_dispatch_hold = None

    def _announce_exclusive_validation_launch(
        self,
        receipt: Mapping[str, object],
    ) -> None:
        launch = receipt.get("launch")
        if not isinstance(launch, Mapping):
            return
        status = str(launch.get("status") or "")
        reason = str(launch.get("reason") or "reason unavailable")
        if status == "started":
            log_result(
                "Tournament launch complete — battle started",
                detail=(
                    f"[TOURNAMENT_LAUNCH] result=started "
                    f"request_id={receipt.get('request_id')} reason={reason}"
                ),
            )
        elif status in {"failed", "cancelled"}:
            log(
                f"[TOURNAMENT_LAUNCH_{status.upper()}] {reason}",
                "WARN" if status == "cancelled" else "ERROR",
                console=True,
            )
            log_result(
                f"Tournament launch {status} — {reason}",
                detail=(
                    f"[TOURNAMENT_LAUNCH] result={status} "
                    f"request_id={receipt.get('request_id')} reason={reason}"
                ),
            )

    def _advance_exclusive_validation_launch(
        self,
        screenshot: Frame,
        detection: Mapping[str, object],
        *,
        battle_started: bool,
    ) -> bool:
        """Handle one confirmed Tournament launch without rerunning validation."""

        if getattr(self, "_external_development_hold_active", False):
            return False

        receipt = self._reconcile_exclusive_validation_launch()
        if receipt is None:
            return False
        request_id = str(receipt.get("request_id") or "")
        launch = receipt.get("launch")
        if not isinstance(launch, Mapping):
            return False
        launch_status = str(launch.get("status") or "")
        self._retain_exclusive_validation_launch_start_proof(
            detection,
            battle_started=battle_started,
        )
        retained_start = bool(
            request_id
            and getattr(
                self,
                "_exclusive_validation_launch_start_hold",
                None,
            )
            == request_id
        )
        if (
            launch_status in {"awaiting_operator", "requested"}
            and retained_start
        ):
            started = (
                self._supervisor.record_manual_exclusive_validation_launch(
                    request_id,
                    reason=(
                        "Fresh Tournament battle start was observed; the "
                        "operator started it manually"
                    ),
                )
            )
            if started is None:
                # The fresh boundary will not be emitted again. Keep the exact
                # proof and typed owner until this receipt-only write succeeds.
                return True
            self._clear_exclusive_validation_launch_start_proof(request_id)
            self._clear_exclusive_validation_launch_dispatch_proof(request_id)
            if (
                getattr(
                    self,
                    "_active_exclusive_validation_launch_request_id",
                    None,
                )
                == request_id
            ):
                self._active_exclusive_validation_launch_request_id = None
            self._announce_exclusive_validation_launch(started)
            return False
        if launch_status not in {"requested", "claimed"}:
            return False
        if not self._action_decision(
            RuntimeActionClass.LIFECYCLE_ACTION,
            owner=AuthorityHold.EXCLUSIVE_VALIDATION,
        ).allowed:
            return False
        if launch_status == "claimed" and retained_start:
            if not self._supervisor.owns_exclusive_validation_launch(
                request_id
            ):
                return True
            started = self._supervisor.finish_exclusive_validation_launch(
                request_id,
                outcome="started",
                reason=(
                    "Tournament battle started from the operator-confirmed "
                    "launch; EHLS/EALS initialization is active"
                ),
            )
            if started is None:
                return True
            self._clear_exclusive_validation_launch_start_proof(request_id)
            self._clear_exclusive_validation_launch_dispatch_proof(request_id)
            self._active_exclusive_validation_launch_request_id = None
            self._announce_exclusive_validation_launch(started)
            return False

        definition = self._exclusive_validation_definition()
        launch_definition = (
            definition.operator_launch if definition is not None else None
        )
        fingerprint_matches = bool(
            definition is not None
            and receipt.get("configuration_fingerprint")
            == definition.configuration_fingerprint
        )
        launch_kind_matches = bool(
            launch_definition is not None
            and launch_definition.kind == "tournament_battle"
        )
        if launch_status == "requested":
            if self._supervisor.is_paused:
                return True
            if not fingerprint_matches or not launch_kind_matches:
                failed = (
                    self._supervisor.fail_unclaimed_exclusive_validation_launch(
                        request_id,
                        reason=(
                            "Tournament launch request no longer matches the "
                            "validated strategy configuration"
                        ),
                    )
                )
                if failed:
                    self._announce_exclusive_validation_launch(failed)
                return False
            claimed = self._supervisor.claim_exclusive_validation_launch(
                request_id,
                configuration_fingerprint=definition.configuration_fingerprint,
            )
            if claimed is None:
                return False
            self._active_exclusive_validation_launch_request_id = request_id
            log_action_intent(
                "Starting the operator-confirmed Tournament",
                reason=(
                    "use fresh Home or Tournament-entry evidence to enter and "
                    "start exactly one Tournament battle; validation is not "
                    "being repeated"
                ),
                detail=f"[TOURNAMENT_LAUNCH] request_id={request_id}",
            )
            dispatched = dispatch_tournament_launch(
                screenshot,
                action_guard=lambda: bool(
                    self._supervisor.exclusive_validation_launch_action_allowed(
                        request_id
                    )
                    and self._runtime_action_guard(
                        action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                        owner=AuthorityHold.EXCLUSIVE_VALIDATION,
                    )
                ),
            )
            if dispatched.dispatched or dispatched.uncertain:
                self._exclusive_validation_launch_dispatch_hold = request_id
            if dispatched.dispatched:
                log(
                    "[TOURNAMENT_LAUNCH] Verified Tournament BATTLE dispatched "
                    f"under durable launch claim {request_id}",
                    "DEBUG",
                )
                return True
            if dispatched.uncertain:
                self._runtime_uncertain_mutation_result(
                    dispatched.reason
                    + "; the claimed Tournament launch remains exact and the "
                    "OPEN/BATTLE input will not be replayed"
                )
                return True
            failed = self._supervisor.finish_exclusive_validation_launch(
                request_id,
                outcome="failed",
                reason=dispatched.reason,
            )
            if failed:
                self._active_exclusive_validation_launch_request_id = None
                self._clear_exclusive_validation_launch_dispatch_proof(
                    request_id
                )
                self._announce_exclusive_validation_launch(failed)
            return True

        if not self._supervisor.owns_exclusive_validation_launch(request_id):
            return False
        current_request_id = str(
            self._supervisor.exclusive_validation.get("current_request_id")
            or ""
        )
        if (
            current_request_id != request_id
            or not fingerprint_matches
            or not launch_kind_matches
        ):
            failed = self._supervisor.finish_exclusive_validation_launch(
                request_id,
                outcome="failed",
                reason=(
                    "Tournament launch was superseded or its validated "
                    "configuration changed; no further input was attempted"
                ),
            )
            if failed:
                self._active_exclusive_validation_launch_request_id = None
                self._clear_exclusive_validation_launch_dispatch_proof(
                    request_id
                )
                self._announce_exclusive_validation_launch(failed)
                if self._exclusive_validation_requires_passive_battle_hold(
                    detection
                ):
                    self._retain_exclusive_validation_passive_battle(
                        request_id
                    )
            return True
        if str(detection.get("state") or "").upper() == "RUNNING":
            if battle_started and self._tournament_battle_guard(detection):
                started = self._supervisor.finish_exclusive_validation_launch(
                    request_id,
                    outcome="started",
                    reason=(
                        "Tournament battle started from the operator-confirmed "
                        "launch; EHLS/EALS initialization is active"
                    ),
                )
                if started:
                    self._active_exclusive_validation_launch_request_id = None
                    self._clear_exclusive_validation_launch_dispatch_proof(
                        request_id
                    )
                    self._announce_exclusive_validation_launch(started)
                    return False
                return True
            failed = self._supervisor.finish_exclusive_validation_launch(
                request_id,
                outcome="failed",
                reason=(
                    "Launch reached RUNNING without a fresh Tournament battle "
                    "boundary; no further input was attempted"
                ),
            )
            if failed:
                self._active_exclusive_validation_launch_request_id = None
                self._clear_exclusive_validation_launch_dispatch_proof(
                    request_id
                )
                self._announce_exclusive_validation_launch(failed)
                self._retain_exclusive_validation_passive_battle(request_id)
            return True
        try:
            deadline_at = float(launch.get("deadline_at") or 0.0)
        except (TypeError, ValueError):
            deadline_at = 0.0
        if deadline_at and time.time() >= deadline_at:
            failed = self._supervisor.finish_exclusive_validation_launch(
                request_id,
                outcome="failed",
                reason=(
                    "Tournament BATTLE did not reach fresh Tournament RUNNING "
                    "evidence before the bounded launch timeout"
                ),
            )
            if failed:
                self._active_exclusive_validation_launch_request_id = None
                self._clear_exclusive_validation_launch_dispatch_proof(
                    request_id
                )
                self._announce_exclusive_validation_launch(failed)
                if self._exclusive_validation_requires_passive_battle_hold(
                    detection
                ):
                    self._retain_exclusive_validation_passive_battle(
                        request_id
                    )
            return True
        return True

    def _report_home_policy(
        self,
        *,
        home_control: HomeBattleControl,
        home_handler_enabled: bool,
        home_preflight_enabled: bool,
        requirements_pending: bool,
    ) -> None:
        """Report a changed Home disposition without a per-frame INFO heartbeat."""

        mission_vars: Mapping[str, Any] = {}
        ctx = getattr(self._mission_mgr, "ctx", None)
        data = getattr(ctx, "data", None)
        if isinstance(data, Mapping):
            candidate = data.get("mission_vars")
            if isinstance(candidate, Mapping):
                mission_vars = candidate
        session_valid = bool(
            mission_vars.get("gc_session_preflight_completed")
        )
        validation_receipt = self._exclusive_validation_receipt()
        validation_status = (
            str(validation_receipt.get("status") or "")
            if validation_receipt
            else ""
        )
        validation_outcome = (
            str(validation_receipt.get("outcome") or "")
            if validation_receipt
            else ""
        )
        validation_reason = (
            str(validation_receipt.get("reason") or "")
            if validation_receipt
            else ""
        )
        validation_launch_status = ""
        if validation_receipt:
            launch = validation_receipt.get("launch")
            if isinstance(launch, Mapping):
                validation_launch_status = str(launch.get("status") or "")
        terminal_mode = AUTOMATION.mode if home_handler_enabled else None
        signature: Tuple[object, ...] = (
            self._current_strategy_name(),
            home_control,
            home_handler_enabled,
            home_preflight_enabled,
            requirements_pending,
            terminal_mode,
            session_valid,
            validation_status,
            validation_outcome,
            validation_reason,
            validation_launch_status,
        )
        if signature == getattr(self, "_last_home_policy_signature", None):
            return
        self._last_home_policy_signature = signature

        definition = self._exclusive_validation_definition()
        exclusive_validation_home = bool(
            definition is not None
            and home_preflight_enabled
            and not home_handler_enabled
            and home_control is HomeBattleControl.NEW_BATTLE
        )
        if not exclusive_validation_home:
            if terminal_mode is ExecMode.HOME:
                log(
                    "[HOME] Stay Home active — holding Home without starting "
                    "or resuming a battle",
                    "INFO",
                )
            elif terminal_mode is ExecMode.WAIT:
                log(
                    "[HOME] Wait active — holding Home until the disposition "
                    "changes",
                    "INFO",
                )
            else:
                log("Detected HOME_SCREEN. Evaluating Home policy.", "INFO")
            return
        if validation_status == "result" and validation_outcome == "ready":
            if validation_launch_status == "awaiting_operator":
                log(
                    f"[TOURNAMENT_READY] {definition.ready_message}",
                    "INFO",
                    console=True,
                )
            elif validation_launch_status == "requested":
                log(
                    "[TOURNAMENT_LAUNCH] Operator Start is waiting for the "
                    "runtime to claim it",
                    "INFO",
                    console=True,
                )
        elif validation_status == "result":
            if validation_outcome == "cancelled":
                log(
                    "[TOURNAMENT_VALIDATION] No validation is planned — "
                    f"{validation_reason or 'the request was cancelled'}",
                    "INFO",
                    console=True,
                )
            else:
                log(
                    "[TOURNAMENT_VALIDATION_FAILED] "
                    f"{definition.failure_prefix}: "
                    f"{validation_reason or 'reason unavailable'}",
                    "ERROR",
                    console=True,
                )
        elif requirements_pending:
            log(
                "[TOURNAMENT_VALIDATION] Home preflight is pending; the ordinary "
                "validation battle has not started",
                "INFO",
                console=True,
            )
        elif validation_status in {"claimed", "running", "cleanup"}:
            log(
                "[TOURNAMENT_VALIDATION] The one-shot ordinary validation "
                f"battle is {validation_status}",
                "INFO",
                console=True,
            )
        elif validation_status == "pending" and self._supervisor.is_paused:
            log(
                "[TOURNAMENT_VALIDATION] Home preflight is complete; Resume "
                "automation to start the authorized ordinary validation battle",
                "INFO",
                console=True,
            )
        elif validation_status == "pending":
            log(
                "[TOURNAMENT_VALIDATION] Home preflight is complete; the "
                "authorized ordinary validation battle is pending",
                "INFO",
                console=True,
            )
        else:
            log(
                "[TOURNAMENT_VALIDATION_FAILED] Tournament validation failed: "
                "no explicit request receipt exists; select Tournament again",
                "ERROR",
                console=True,
            )

    def _gate_decision_directive(self) -> Optional[Dict[str, Any]]:
        directive = self._supervisor.gate_decision
        if not isinstance(directive, Mapping):
            return None
        request_id = str(directive.get("request_id") or "").strip()
        status = str(directive.get("status") or "").strip().lower()
        if not request_id or status not in {"pending", "resolved", "consumed"}:
            return None
        return dict(directive)

    def _publish_gate_decision(
        self,
        *,
        phase: str,
        check_id: str,
        reason: str,
        expected: object,
        blocking: bool = True,
        allow_waive: bool = True,
    ) -> Optional[Dict[str, Any]]:
        options = build_gate_decision_options(
            check_id,
            (
                self._mission_mgr.gate_fallbacks(check_id)
                if allow_waive
                else ()
            ),
            advisory=not blocking,
            allow_waive=allow_waive,
        )
        directive = self._supervisor.publish_gate_decision(
            strategy=self._current_strategy_name(),
            phase=phase,
            check_id=check_id,
            reason=reason,
            expected=expected,
            options=options,
            blocking=blocking,
            repair_authority=None,
        )
        if not isinstance(directive, Mapping):
            return None
        if directive and directive.get("status") == "pending":
            log(
                (
                    f"[GATE_DECISION] Waiting for {check_id}: {reason}"
                    if blocking
                    else f"[RUNTIME_ADVISORY] {check_id}: {reason}; "
                    "Automation continues"
                ),
                "WARN",
                console=True,
            )
        return dict(directive)

    def _prompt_for_gate_decision(
        self,
        directive: Mapping[str, Any],
    ) -> Optional[Dict[str, Any]]:
        prompt = getattr(self, "_gate_decision_prompt", None)
        request_id = str(directive.get("request_id") or "")
        if (
            prompt is None
            or not request_id
            or request_id == getattr(self, "_gate_prompted_request_id", None)
        ):
            return dict(directive)
        self._gate_prompted_request_id = request_id
        decision_id = prompt(directive)
        if not decision_id:
            return dict(directive)
        resolved = self._supervisor.resolve_gate_decision(
            request_id,
            decision_id,
            source="runtime-cli",
        )
        return dict(resolved) if resolved else dict(directive)

    def _matching_gate_decision(
        self,
        phase: str,
        *,
        prompt_pending: bool = True,
    ) -> Optional[Dict[str, Any]]:
        directive = self._gate_decision_directive()
        if not directive or directive.get("status") == "consumed":
            return None
        if (
            str(directive.get("strategy") or "").lower()
            != self._current_strategy_name()
            or str(directive.get("phase") or "").lower() != phase
        ):
            return None
        if directive.get("status") == "pending" and prompt_pending:
            directive = self._prompt_for_gate_decision(directive) or directive
        return directive

    def _apply_gate_decision(
        self,
        directive: Mapping[str, Any],
        *,
        phase: str,
    ) -> bool:
        """Apply a resolved choice; return whether its gate may retry."""

        if str(directive.get("status") or "").lower() != "resolved":
            return False
        request_id = str(directive.get("request_id") or "")
        check_id = str(directive.get("check_id") or "startup_setup")
        selected = directive.get("selected_option")
        if not isinstance(selected, Mapping):
            return False
        action = str(selected.get("action") or "").lower()
        if action == "waive":
            if (
                phase == "session_preflight"
                and check_id not in STARTUP_GATE_CHECK_LABELS
            ):
                return False
            waiver = {
                "request_id": request_id,
                "decision_id": str(directive.get("decision_id") or ""),
                "label": str(selected.get("label") or ""),
                "kind": str(selected.get("kind") or "standard"),
                "value": str(selected.get("value") or ""),
                "reason": str(directive.get("reason") or ""),
            }
            if phase == "home_setup":
                waivers = getattr(self, "_startup_gate_waivers", {})
                waivers[check_id] = waiver
                self._startup_gate_waivers = waivers
            else:
                self._mission_mgr.waive_session_preflight_check(check_id, waiver)
            completion_reason = f"waived {check_id} for this run"
        elif action == "retry":
            if phase == "session_preflight":
                self._mission_mgr.retry_session_preflight()
            completion_reason = f"retrying {check_id} without a waiver"
        elif action in {"pause", "repair_restart"}:
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.WORKFLOW_EVIDENCE_EXPIRED,
                f"retired legacy {action} decision for {check_id}",
            )
            completion_reason = (
                f"retired legacy {action} decision for {check_id}; "
                "automation continues degraded"
            )
        else:
            return False
        if phase == "session_preflight" and action in {"waive", "retry"}:
            self._get_action_authority().clear_strategy_gate(
                event={
                    "waive": StrategyGateExitEvent.RUN_SCOPED_WAIVER,
                    "retry": StrategyGateExitEvent.ACCEPTED_RETRY,
                }[action],
                reason=completion_reason,
            )
        elif phase == "session_preflight" and action in {
            "pause",
            "repair_restart",
        }:
            self._get_action_authority().clear_strategy_gate(
                event=StrategyGateExitEvent.SUCCESSFUL_VALIDATION,
                reason=completion_reason,
            )
        self._supervisor.consume_gate_decision(
            request_id,
            completion_reason=completion_reason,
        )
        log(
            f"[GATE_DECISION] {completion_reason}",
            "WARN" if action in {"waive", "pause", "repair_restart"} else "INFO",
            console=True,
        )
        return True

    def _session_preflight_gate_context(
        self,
    ) -> Tuple[list[str], str]:
        """Return scoped failed checks and the best retained operator reason."""

        mission_vars = self._mission_mgr.ctx.data.get("mission_vars", {})
        if not isinstance(mission_vars, Mapping):
            mission_vars = {}
        raw_evidence = mission_vars.get("gc_session_preflight_evidence")
        evidence: Mapping[str, Any] = (
            raw_evidence if isinstance(raw_evidence, Mapping) else {}
        )

        def scoped_checks(raw: object) -> list[str]:
            if not isinstance(raw, (list, tuple)):
                return []
            checks: list[str] = []
            for value in raw:
                check_id = str(value or "").strip()
                if (
                    check_id in STARTUP_GATE_CHECK_LABELS
                    and check_id not in checks
                ):
                    checks.append(check_id)
            return checks

        evidence_checks = scoped_checks(evidence.get("failed_checks"))
        manager_checks = scoped_checks(
            self._mission_mgr.session_preflight_failure_checks()
        )
        checks = evidence_checks or manager_checks

        stored_reason = str(
            mission_vars.get("gc_session_preflight_last_reason") or ""
        ).strip()
        if (
            not stored_reason
            or stored_reason.lower() in _GENERIC_SESSION_PREFLIGHT_REASONS
        ):
            if checks:
                summary_evidence = dict(evidence)
                summary_evidence["failed_checks"] = checks
                reason = summarize_gc_preflight_mismatch(summary_evidence)
            else:
                reason = (
                    "Session preflight failed without a scoped requirement; "
                    "retry to collect fresh evidence"
                )
        else:
            reason = stored_reason
        return checks, reason[:1000]

    @staticmethod
    def _unscoped_session_gate_is_safe(
        directive: Mapping[str, Any],
    ) -> bool:
        options = directive.get("options")
        return bool(
            isinstance(options, (list, tuple))
            and options
            and all(
                isinstance(option, Mapping)
                and str(option.get("action") or "").lower() in {"retry", "pause"}
                for option in options
            )
        )

    def _retire_successful_session_preflight_decision(self) -> None:
        """Consume only this Strategy's stale running-session decision."""

        mission_vars = self._mission_mgr.ctx.data.get("mission_vars", {})
        if not isinstance(mission_vars, Mapping) or not (
            mission_vars.get("gc_session_preflight_completed") is True
            and mission_vars.get("gc_session_preflight_last_status") == "complete"
        ):
            return
        degraded_fn = getattr(
            self._mission_mgr,
            "session_preflight_degraded",
            None,
        )
        if callable(degraded_fn) and degraded_fn() is True:
            return
        directive = self._gate_decision_directive()
        if not directive or directive.get("status") not in {
            "pending",
            "resolved",
        }:
            return
        if (
            str(directive.get("strategy") or "").lower()
            != self._current_strategy_name()
            or str(directive.get("phase") or "").lower()
            != "session_preflight"
        ):
            return
        request_id = str(directive.get("request_id") or "")
        if request_id:
            retired = self._supervisor.consume_gate_decision(
                request_id,
                completion_reason=(
                    "session preflight subsequently completed successfully"
                ),
            )
            if retired and directive.get("blocking") is False:
                log(
                    "[RUNTIME_ADVISORY] Session configuration recovered; "
                    "the persistent advisory is cleared",
                    "INFO",
                    console=True,
                )

    def _handle_terminal_session_gate_decision(self) -> None:
        checks, reason = self._session_preflight_gate_context()
        check_id = checks[0] if checks else "session_preflight"
        directive = self._matching_gate_decision(
            "session_preflight",
            prompt_pending=False,
        )
        if directive is not None:
            directive_check = str(directive.get("check_id") or "").strip()
            refresh_required = bool(
                directive_check != check_id
                or str(directive.get("reason") or "").strip() != reason
                or directive.get("blocking") is not False
                or (
                    not checks
                    and not self._unscoped_session_gate_is_safe(directive)
                )
            )
            if refresh_required:
                retired = self._supervisor.consume_gate_decision(
                    str(directive.get("request_id") or ""),
                    completion_reason=(
                        "superseded by refreshed session preflight evidence"
                    ),
                )
                if not retired:
                    return
                directive = None
        if directive is None:
            requirements = self._mission_mgr.strategy.session_preflight_requirements()
            directive = self._publish_gate_decision(
                phase="session_preflight",
                check_id=check_id,
                reason=reason,
                expected=(requirements.get(check_id) if checks else None),
                blocking=False,
                allow_waive=bool(checks),
            )
        if directive and directive.get("status") == "pending":
            directive = self._prompt_for_gate_decision(directive) or directive
        if directive:
            self._apply_gate_decision(directive, phase="session_preflight")

    def _claim_proactive_gate_waivers(
        self,
        *,
        for_home_setup: bool,
        requirements: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Apply staged one-run skips supported by the active strategy."""

        strategy = self._mission_mgr.strategy
        if not strategy:
            return {}
        if requirements is None:
            requirement_fn = getattr(
                strategy,
                "session_preflight_requirements",
                None,
            )
            if not callable(requirement_fn):
                return {}
            requirements = requirement_fn()
        if not isinstance(requirements, Mapping):
            return {}
        check_ids = [
            entry["id"] for entry in startup_gate_check_catalog(requirements)
        ]
        claimed = self._supervisor.claim_startup_gate_waivers(
            check_ids,
            strategy=self._current_strategy_name(),
        )
        if not isinstance(claimed, Mapping):
            return {}
        applied: Dict[str, Dict[str, Any]] = {}
        for check_id, directive in claimed.items():
            waiver = {
                "request_id": str(directive.get("request_id") or ""),
                "decision_id": "proactive_skip",
                "label": str(directive.get("label") or check_id),
                "kind": "proactive",
                "value": "",
                "reason": "configured before the run",
            }
            if for_home_setup:
                self._startup_gate_waivers[check_id] = waiver
            else:
                self._mission_mgr.waive_session_preflight_check(check_id, waiver)
            applied[check_id] = waiver
            pending = self._gate_decision_directive()
            if (
                pending
                and pending.get("status") in {"pending", "resolved"}
                and pending.get("check_id") == check_id
            ):
                self._supervisor.consume_gate_decision(
                    str(pending["request_id"]),
                    completion_reason=(
                        f"superseded by proactive {check_id} skip"
                    ),
                )
            log(
                f"[GATE_WAIVER] Skipping {check_id} for this run by "
                "advance operator configuration",
                "WARN",
                console=True,
            )
        return applied

    def _observe_strategy_request(self) -> None:
        request = self._supervisor.strategy_request
        if not isinstance(request, tuple) or len(request) not in {2, 3}:
            return
        apply_mode = (
            str(request[2] or "next_boundary").strip().lower()
            if len(request) == 3
            else "next_boundary"
        )
        if apply_mode not in {"next_boundary", "active_battle"}:
            apply_mode = "next_boundary"
        normalized_request = (request[0], request[1], apply_mode)
        if normalized_request == getattr(self, "_last_strategy_request", None):
            return
        self._last_strategy_request = normalized_request
        requested_name = normalized_request[0]
        current_name = self._current_strategy_name()
        same_name_reload = requested_name == current_name
        if (
            same_name_reload
            and self._current_strategy_definition_matches(requested_name)
        ):
            self._pending_strategy_request = None
            self._supervisor.acknowledge_strategy(
                requested_name,
                normalized_request[1],
            )
            log(
                f"[CTRL] Strategy set to {requested_name} via control file",
                "INFO",
                console=True,
            )
            return
        self._pending_strategy_request = normalized_request
        if apply_mode == "active_battle":
            message = (
                f"[CTRL] Strategy {requested_name} requested for the active battle; "
                "waiting for fresh active-battle evidence"
            )
        else:
            message = (
                f"[CTRL] Strategy {requested_name} queued for the next run boundary"
            )
        log(message, "INFO", console=True)

    def _observe_strategy_gate_boundary(
        self,
        detection: Mapping[str, Any],
    ) -> None:
        """Clear a running-battle gate only on authoritative boundary evidence."""

        authority = self._get_action_authority()
        gate = authority.strategy_gate
        if gate is None and authority.auxiliary_route is None:
            return
        state = str(detection.get("state") or "UNKNOWN").upper()
        control = HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        )
        boundary_event: Optional[StrategyGateExitEvent] = None
        boundary_reason: Optional[str] = None
        if state in {"GAME_OVER", "TOURNAMENT_RESULTS"}:
            boundary_event = StrategyGateExitEvent.NATURAL_BATTLE_BOUNDARY
            boundary_reason = f"natural {state} was observed"
        elif state in {"HOME", "HOME_SCREEN"} and (
            control is HomeBattleControl.NEW_BATTLE
        ):
            boundary_event = StrategyGateExitEvent.NATURAL_BATTLE_BOUNDARY
            boundary_reason = "Home authoritatively offers New Battle"
        elif state == "WORKSHOP":
            boundary_event = StrategyGateExitEvent.NATURAL_BATTLE_BOUNDARY
            boundary_reason = "Workshop proves that no resumable battle is active"
        else:
            current_scope = self._current_run_scope_id()
            route_state = authority.auxiliary_route
            authoritative_scope = (
                gate.battle_scope
                if gate is not None and gate.battle_scope is not None
                else (
                    route_state.lease.battle_scope
                    if route_state is not None
                    else None
                )
            )
            if (
                authoritative_scope is not None
                and current_scope is not None
                and authoritative_scope != current_scope
            ):
                boundary_event = StrategyGateExitEvent.BATTLE_IDENTITY_CHANGE
                boundary_reason = (
                    "the authoritative current-run identity changed from "
                    f"{authoritative_scope} to {current_scope}"
                )
        if boundary_event is None or boundary_reason is None:
            if gate is not None:
                authority.scope_gate_if_missing(self._current_run_scope_id())
            return
        authority.abandon_auxiliary_route(reason=boundary_reason)
        self._pending_auxiliary_cleanup = None
        authority.clear_strategy_gate(
            event=boundary_event,
            reason=boundary_reason,
        )

    def _sync_strategy_action_gate(
        self,
        *,
        terminally_blocked: bool,
        detection: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Retire legacy preflight gates; mismatches are advisory only."""

        authority = self._get_action_authority()
        if terminally_blocked:
            existing_gate = authority.strategy_gate
            if existing_gate is not None and existing_gate.source == (
                "session_preflight"
            ):
                authority.clear_strategy_gate(
                    event=StrategyGateExitEvent.SUCCESSFUL_VALIDATION,
                    reason=(
                        "configuration mismatches are advisory under the global "
                        "runtime failure policy"
                    ),
                )
            return

        strategy = self._mission_mgr.strategy
        session_preflight_complete = bool(
            strategy is not None
            and strategy.requires_session_preflight()
            and strategy.is_session_preflight_complete(self._mission_mgr.ctx)
        )
        if session_preflight_complete:
            self._retire_successful_session_preflight_decision()

        gate = authority.strategy_gate
        if gate is None:
            return
        if (
            gate.source == "session_preflight"
            and session_preflight_complete
        ):
            authority.clear_strategy_gate(
                event=StrategyGateExitEvent.SUCCESSFUL_VALIDATION,
                reason="the running-battle strategy checks completed successfully",
            )

    def _attach_workflow_in_progress(self) -> bool:
        """Return whether the immutable Attach snapshot still owns selection."""

        workflow = self._supervisor.battle_workflow
        return bool(
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "attach_battle"
            and workflow.get("status") not in BATTLE_WORKFLOW_TERMINAL_STATUSES
        )

    def _defer_active_strategy_request_to_boundary(
        self,
        request: Tuple[str, object, str],
        *,
        reason: str,
    ) -> bool:
        """Keep one exact active-battle request out of the current battle."""

        requested_name, request_id, _apply_mode = request
        deferred = self._supervisor.defer_strategy_request_to_next_boundary(
            requested_name,
            request_id,
            source="runtime-attachment-strategy-deferral",
        )
        self._pending_strategy_request = (
            requested_name,
            request_id,
            "next_boundary",
        )
        if not deferred:
            self._flag_recoverable_runtime_failure(
                RuntimeFailureKind.REPORTING_FAILURE,
                "the active-battle Strategy request was kept locally for the "
                "next boundary, but its durable apply mode could not be updated",
            )
        log(
            "[ATTACH] Active-battle Strategy request queued for the next safe "
            f"boundary: {reason}",
            "WARN",
            console=True,
        )
        return deferred

    def _attached_observer_requires_strategy_boundary(self) -> bool:
        """Keep incompatible or unverifiable Attach observers read-only."""

        if self._mission_mgr.strategy is not None:
            return False
        degradation = self._mission_mgr.running_configuration_degradation()
        if not isinstance(degradation, Mapping):
            return False
        raw_sources = degradation.get("sources", ())
        if not isinstance(raw_sources, (list, tuple, set, frozenset)):
            raw_sources = ()
        sources = {
            str(source or "").strip()
            for source in raw_sources
            if str(source or "").strip()
        }
        return bool(
            sources.intersection(
                {"attachment_applicability", "attachment_reporting"}
            )
        )

    def _process_strategy_boundary(self, detection: Mapping[str, Any]) -> None:
        state = str(detection.get("state") or "UNKNOWN").upper()
        control = HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        )
        if state in {"GAME_OVER", "TOURNAMENT_RESULTS"}:
            self._strategy_boundary_confirmed = True
            self._exclusive_validation_ownership_hold = False
            return
        if state in {"HOME", "HOME_SCREEN"}:
            if control is HomeBattleControl.NEW_BATTLE:
                self._strategy_boundary_confirmed = True
                self._exclusive_validation_ownership_hold = False
            elif control is HomeBattleControl.RESUME_BATTLE:
                self._strategy_boundary_confirmed = False
        elif state in {"WORKSHOP", "TOURNAMENT_SCREEN"}:
            # Neither screen is an active or resumable battle. This is
            # authoritative no-battle evidence even when dropped terminal
            # frames or manual navigation made it the first visible boundary.
            self._strategy_boundary_confirmed = True
            self._exclusive_validation_ownership_hold = False

        if self._exclusive_validation_blocks_strategy_application(detection):
            # A newly accepted Strategy may queue while validation owns or
            # persists any exact boundary. It cannot replace the manager and
            # erase a single-frame start/terminal proof before that receipt is
            # durable and its old mission boundary has been released.
            if state == "RUNNING":
                self._strategy_boundary_confirmed = False
            return

        pending = getattr(self, "_pending_strategy_request", None)
        if (
            pending is not None
            and pending[2] == "active_battle"
            and (
                state == "RUNNING"
                or (
                    state in {"HOME", "HOME_SCREEN"}
                    and control is HomeBattleControl.RESUME_BATTLE
                )
            )
        ):
            self._apply_pending_strategy_to_active_battle()
            self._strategy_boundary_confirmed = False
            return

        if getattr(self, "_strategy_boundary_confirmed", False):
            self._apply_pending_strategy()
        if state == "RUNNING":
            self._strategy_boundary_confirmed = False

    def _apply_pending_strategy(self) -> bool:
        request = getattr(self, "_pending_strategy_request", None)
        if request is None or not getattr(
            self,
            "_strategy_boundary_confirmed",
            False,
        ):
            return False
        requested_name = request[0]
        try:
            strategy = get_strategy(requested_name)
            self._mission_mgr.replace_strategy_at_boundary(strategy)
        except Exception as exc:
            log(
                f"[CTRL] Failed to apply strategy {requested_name}: {exc}",
                "ERROR",
                console=True,
            )
            return False
        self._complete_strategy_application(
            requested_name,
            request_id=request[1],
        )
        log(
            f"[CTRL] Strategy set to {requested_name} via control file",
            "INFO",
            console=True,
        )
        return True

    def _apply_pending_strategy_to_active_battle(self) -> bool:
        request = getattr(self, "_pending_strategy_request", None)
        if request is None or request[2] != "active_battle":
            return False
        if self._attach_workflow_in_progress():
            return False
        if self._attached_observer_requires_strategy_boundary():
            self._defer_active_strategy_request_to_boundary(
                request,
                reason=(
                    "the attached battle is an incompatible or unverifiable "
                    "observer and cannot be converted into a Strategy-run battle"
                ),
            )
            return False
        requested_name = request[0]
        try:
            strategy = get_strategy(requested_name)
            self._mission_mgr.adopt_strategy_for_active_battle(strategy)
        except Exception as exc:
            log(
                f"[CTRL] Failed to adopt strategy {requested_name} for active battle: "
                f"{exc}",
                "ERROR",
                console=True,
            )
            return False
        self._complete_strategy_application(
            requested_name,
            request_id=request[1],
        )
        log(
            f"[CTRL] Adopted strategy {requested_name} for active battle; "
            "startup gates deferred until the next run boundary",
            "INFO",
            console=True,
        )
        # Keep the established exact acknowledgement entry for status clients.
        log(
            f"[CTRL] Strategy set to {requested_name} via control file",
            "INFO",
            console=True,
        )
        return True

    def _complete_strategy_application(
        self,
        requested_name: str,
        *,
        request_id: object = None,
    ) -> None:
        self._get_action_authority().clear_strategy_gate(
            event=StrategyGateExitEvent.ACTIVE_STRATEGY_CHANGE,
            reason=(
                f"strategy policy changed explicitly to {requested_name}"
            ),
        )
        self._get_action_authority().abandon_auxiliary_route(
            reason="the active strategy changed"
        )
        self._pending_auxiliary_cleanup = None
        self._config.strategy_name = requested_name
        self._supervisor.acknowledge_strategy(
            requested_name,
            request_id,
        )
        self._pending_strategy_request = None
        self._run_initialization_gate_logged = False
        self._session_preflight_gate_logged = False
        self._session_preflight_terminal_blocked_logged = False
        self._session_preflight_repair_denial_logged = False
        self._steady_run_entry_pending = False
        self._startup_gate_waivers = {}
        self._no_strategy_inventory_complete = False
        self._no_strategy_inventory_retry_at = 0.0
        if str(requested_name or "").strip().lower() == "none":
            self._begin_no_strategy_observation_boundary(
                f"strategy:{str(request_id or requested_name)}"
            )
        else:
            self._end_no_strategy_observation_boundary()
        self._active_exclusive_validation_request_id = None
        self._active_exclusive_validation_launch_request_id = None
        self._exclusive_validation_battle_dispatch_hold = None
        self._exclusive_validation_launch_dispatch_hold = None
        self._exclusive_validation_claimed_start_hold = None
        self._exclusive_validation_launch_start_hold = None
        self._clear_exclusive_validation_terminal_claim()
        self._exclusive_validation_ownership_hold = False

    def _handler_enabled(self, name: str) -> bool:
        """Honor an optional strategy handler allowlist; legacy plans allow all."""

        handlers = self._runtime_policy().get("handlers")
        if handlers is None:
            return True
        if not isinstance(handlers, (list, tuple, set, frozenset)):
            return False
        return name in {str(handler).strip() for handler in handlers}

    def _log_steady_run_entry(self) -> None:
        """Announce the transition from startup checks to normal execution."""

        log_result(
            "[RUN] All configured checks complete; entering steady run state",
            detail="[RUN] result=steady_state",
            console=True,
        )

    def _maybe_log_steady_run_entry(self, *, actions_blocked: bool) -> bool:
        """Emit the pending steady-state notice at the first actionable frame."""

        if (
            actions_blocked
            or not getattr(self, "_steady_run_entry_pending", False)
        ):
            return False
        self._log_steady_run_entry()
        self._steady_run_entry_pending = False
        return True

    def _apply_activity_continuity_outcome(self, outcome: object) -> None:
        """Apply run identity facts established by attachment continuity."""

        interruption_reason = str(
            getattr(
                outcome,
                "operator_workflow_interruption_reason",
                "",
            )
            or ""
        ).strip()
        if interruption_reason:
            source_restored = getattr(
                outcome,
                "operator_workflow_source_restored",
                None,
            )
            catastrophic = source_restored is False
            if catastrophic:
                self._supervisor.pause_for_catastrophic_failure(
                    RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                    reason=interruption_reason,
                )
            else:
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.EVIDENCE_UNAVAILABLE,
                    interruption_reason,
                )
            current = self._current_control_workflow_evidence() or {}
            final_status = (
                "failed"
                if "projection" in interruption_reason
                or "identity" in interruption_reason
                else "interrupted"
            )
            workflow = self._supervisor.battle_workflow
            if (
                isinstance(workflow, Mapping)
                and workflow.get("intent") == "attach_battle"
                and workflow.get("status")
                in {"validating_save", "action_dispatched"}
            ):
                self._supervisor.transition_battle_workflow(
                    str(workflow.get("request_id") or ""),
                    final_status,
                    reason=(
                        "save-backed attachment stopped after source restoration "
                        f"failed: {interruption_reason}; Automation remains Paused"
                        if catastrophic
                        else "save-backed attachment evidence was rejected: "
                        f"{interruption_reason}; Automation continues without "
                        "granting attachment authority"
                    ),
                    acknowledgement=current,
                )
            manual = self._supervisor.manual_control
            if (
                isinstance(manual, Mapping)
                and manual.get("status") == "reconciling"
            ):
                manual_id = str(manual.get("manual_control_id") or "")
                self._pending_return_reconciliation_claims().pop(
                    manual_id,
                    None,
                )
                self._supervisor.transition_manual_control(
                    manual_id,
                    final_status,
                    detail=(
                        "save-backed Return Control stopped after source restoration "
                        f"failed: {interruption_reason}; Automation remains Paused"
                        if catastrophic
                        else "save-backed Return Control evidence was rejected: "
                        f"{interruption_reason}; Automation continues in degraded mode"
                    ),
                    refresh_status=(
                        "save_restoration_interrupted"
                        if catastrophic
                        else "save_evidence_rejected_continued"
                    ),
                )
            log(
                "[PLAYER_SAVE] Operator workflow ended after guarded "
                f"serialization issue: {interruption_reason}; disposition="
                + ("pause_for_safety" if catastrophic else "continue_degraded"),
                "WARN",
            )
            return

        save_observations = getattr(
            outcome,
            "running_attachment_observations",
            None,
        )
        attachment_temporal_binding = getattr(
            outcome,
            "running_attachment_temporal_binding",
            None,
        )
        attachment_acquisition = getattr(
            outcome,
            "running_attachment_acquisition",
            None,
        )
        attachment_bundle_context = getattr(
            outcome,
            "running_attachment_context",
            None,
        )
        attachment_context = None
        transition_source_scope_id = ""
        if isinstance(
            attachment_temporal_binding,
            RunningAttachmentTemporalBinding,
        ):
            transition_source_scope_id = (
                attachment_temporal_binding.source_activity_scope_id
            )
        elif isinstance(
            save_observations,
            RunningAttachmentSaveObservations,
        ):
            transition_source_scope_id = (
                save_observations.binding.source_activity_scope_id
            )
        if (
            isinstance(save_observations, RunningAttachmentSaveObservations)
            or isinstance(
                attachment_temporal_binding,
                RunningAttachmentTemporalBinding,
            )
            or isinstance(
                attachment_bundle_context,
                PlayerSaveAttachmentContext,
            )
        ):
            try:
                attachment_context = (
                    self._current_player_save_attachment_context()
                )
            except Exception:
                if transition_source_scope_id:
                    try:
                        attachment_context = (
                            self._current_player_save_attachment_context(
                                transition_source_activity_scope_id=(
                                    transition_source_scope_id
                                )
                            )
                        )
                    except Exception:
                        attachment_context = None
        if (
            isinstance(save_observations, RunningAttachmentSaveObservations)
            and not save_observations.matches_context(attachment_context)
        ):
            log(
                "[PLAYER_SAVE] Bound attachment observations were rejected "
                "after the target or activity scope changed",
                "WARN",
            )
            save_observations = None
            attachment_acquisition = None
            attachment_bundle_context = None
            attachment_temporal_binding = None

        if (
            isinstance(
                attachment_temporal_binding,
                RunningAttachmentTemporalBinding,
            )
            and not attachment_temporal_binding.matches_context(
                attachment_context
            )
        ):
            log(
                "[PLAYER_SAVE] Bound attachment round identity was rejected "
                "after the target or activity scope changed",
                "WARN",
            )
            attachment_temporal_binding = None
            attachment_acquisition = None
            attachment_bundle_context = None

        if (
            isinstance(attachment_bundle_context, PlayerSaveAttachmentContext)
            and attachment_bundle_context == attachment_context
        ):
            if (
                not isinstance(attachment_acquisition, PlayerSaveAcquisitionBundle)
                or attachment_acquisition.binding
                != PlayerSaveTargetBinding(
                    attachment_bundle_context.target,
                    attachment_bundle_context.target_generation,
                )
            ):
                attachment_acquisition = None
            else:
                monitor_context = PlayerSaveObservationContext(
                    runtime_session_id=(
                        attachment_bundle_context.runtime_session_id
                    ),
                    activity_scope_id=(
                        attachment_bundle_context.activity_scope_id
                    ),
                    target_binding=attachment_acquisition.binding,
                )
                self._publish_player_save_observation(
                    attachment_acquisition,
                    context=monitor_context,
                    reason_code="forced_running_attachment",
                    bind_new_activity=True,
                )
        else:
            attachment_acquisition = None
            attachment_bundle_context = None
            attachment_temporal_binding = None

        if isinstance(save_observations, RunningAttachmentSaveObservations):
            mapping_observer = None
            if (
                isinstance(attachment_acquisition, PlayerSaveAcquisitionBundle)
                and attachment_acquisition.snapshot is not None
                and save_observations.binding.final
            ):
                binding = save_observations.binding
                mapping_observer = BoundPlayerSaveMappingObserver(
                    snapshot=attachment_acquisition.snapshot,
                    context_guard_fn=lambda: save_observations.matches_context(
                        self._current_player_save_attachment_context()
                    ),
                    workflow_provenance={
                        "capture_request_id": (
                            "capture-" + binding.claim_fingerprint[:32]
                        ),
                        "inspection_request_id": (
                            "running-attachment-ui-fallback"
                        ),
                        "runtime_session_fingerprint": hashlib.sha256(
                            (
                                "runtime-session\0"
                                f"{binding.runtime_session_id}"
                            ).encode("utf-8")
                        ).hexdigest(),
                        "pid": max(1, os.getpid()),
                        "target_generation_fingerprint": hashlib.sha256(
                            (
                                f"{binding.target_binding.target}\0"
                                f"{binding.target_binding.generation}"
                            ).encode("utf-8")
                        ).hexdigest(),
                        "activity_scope_fingerprint": hashlib.sha256(
                            str(binding.activity_scope_id).encode("utf-8")
                        ).hexdigest(),
                        "game_state": "active_battle",
                        "active_round_identity_fingerprint": (
                            binding.active_round_identity_fingerprint
                        ),
                        "boundary_fingerprint": binding.claim_fingerprint,
                    },
                )
            self._mission_mgr.ctx.data[
                "player_save_attachment_evidence"
            ] = BoundRunningAttachmentSaveEvidence(
                save_observations,
                self._current_player_save_attachment_context,
                mapping_observer,
            )
            strategy_name = self._current_strategy_name()
            battle_workflow = getattr(
                getattr(self, "_supervisor", None),
                "battle_workflow",
                None,
            )
            attach_strategy_pending = bool(
                isinstance(battle_workflow, Mapping)
                and battle_workflow.get("intent") == "attach_battle"
                and battle_workflow.get("status")
                not in BATTLE_WORKFLOW_TERMINAL_STATUSES
            )
            if (
                strategy_name == "none"
                and getattr(self, "_no_strategy_observation_active", False)
                and not attach_strategy_pending
            ):
                try:
                    applied = (
                        self._no_strategy_observer.record_player_save_observations(
                            save_observations
                        )
                    )
                except (TypeError, ValueError) as exc:
                    log(
                        "[NO_STRATEGY] Guarded attachment save observations "
                        f"were rejected: {exc}",
                        "WARN",
                    )
                else:
                    if applied:
                        log(
                            "[NO_STRATEGY] Applied guarded attachment save "
                            f"observations for {len(applied)} fields: "
                            + ", ".join(applied),
                            "INFO",
                            console=True,
                        )
            elif (
                strategy_name == "tournament"
                and save_observations.fact("workshop_preset") is not None
            ):
                log(
                    "[TOURNAMENT] Bound the active round's Workshop preset "
                    "from the guarded attachment save",
                    "INFO",
                    console=True,
                )

        confirmed_scope_id = getattr(
            outcome,
            "confirmed_same_battle_scope_id",
            None,
        )
        if confirmed_scope_id:
            self._mission_mgr.reuse_session_preflight_for_confirmed_attachment(
                str(confirmed_scope_id)
            )
            if getattr(self, "_exclusive_validation_ownership_hold", False):
                self._exclusive_validation_ownership_hold = False
                log(
                    "[TOURNAMENT_VALIDATION] Released legacy orphaned "
                    "validation hold after current-battle continuity was "
                    "confirmed",
                    "INFO",
                )

        confirmed_later_scope_id = getattr(
            outcome,
            "confirmed_later_battle_scope_id",
            None,
        )
        if (
            confirmed_later_scope_id
            and getattr(self, "_exclusive_validation_ownership_hold", False)
        ):
            self._exclusive_validation_ownership_hold = False
            log(
                "[TOURNAMENT_VALIDATION] Cleared stale exclusive ownership "
                "hold after Battle History confirmed the attached battle is "
                "a later run",
                "INFO",
            )

        self._complete_save_backed_operator_reconciliation(
            outcome=outcome,
            acquisition=attachment_acquisition,
            temporal_binding=attachment_temporal_binding,
            observations=save_observations,
            context=attachment_bundle_context,
        )

    def _complete_save_backed_operator_reconciliation(
        self,
        *,
        outcome: object,
        acquisition: object,
        temporal_binding: object,
        observations: object,
        context: object,
    ) -> bool:
        """Persist typed Attach/Return evidence after final-scope continuity."""

        save_backed = bool(
            isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and isinstance(temporal_binding, RunningAttachmentTemporalBinding)
            and isinstance(context, PlayerSaveAttachmentContext)
            and temporal_binding.matches_context(context)
            and acquisition.binding == temporal_binding.target_binding
        )
        if not save_backed:
            if getattr(outcome, "ui_monitoring_fallback", False) is True:
                return self._complete_ui_backed_operator_reconciliation(outcome)
            return False
        current = self._current_control_workflow_evidence()
        if not (
            isinstance(current, Mapping)
            and current.get("game_state") == "active_battle"
        ):
            return False
        confirmed_same = getattr(
            outcome,
            "confirmed_same_battle_scope_id",
            None,
        )
        confirmed_later = getattr(
            outcome,
            "confirmed_later_battle_scope_id",
            None,
        )
        disposition = (
            "later_battle"
            if confirmed_later
            else "same_battle"
            if confirmed_same
            else "attachment_baseline"
        )
        workflow = self._supervisor.battle_workflow
        if (
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "attach_battle"
            and workflow.get("status") in {"validating_save", "action_dispatched"}
        ):
            workflow_id = str(workflow.get("request_id") or "")
            strategy_decision = self._attachment_strategy_decision(
                workflow,
                acquisition=acquisition,
                observations=observations,
            )
            check_sets = strategy_decision.get("_check_sets")
            if not isinstance(check_sets, Mapping):
                check_sets = {}
            strategy_selected = (
                strategy_decision.get("attachment_mode") == "strategy"
            )
            resolved_checks = (
                check_sets.get("accepted", ()) if strategy_selected else ()
            )
            unresolved_checks = (
                tuple(
                    sorted(
                        {
                            *check_sets.get("mismatched", ()),
                            *check_sets.get("ui_required", ()),
                        }
                    )
                )
                if strategy_selected
                else ()
            )
            try:
                receipt = build_running_save_reconciliation_receipt(
                    kind="running_attachment_reconciliation",
                    workflow_id=workflow_id,
                    observation_id=str(current.get("observation_id") or ""),
                    acquisition=acquisition,
                    temporal_binding=temporal_binding,
                    disposition=disposition,
                    resolved_check_ids=resolved_checks,
                    unresolved_check_ids=unresolved_checks,
                )
            except (TypeError, ValueError) as exc:
                reporting_decision = (
                    self._attachment_reporting_failure_decision(
                        workflow,
                        reason=(
                            "Attach reconciliation reporting failed; the "
                            f"battle continues in degraded observation: {exc}"
                        ),
                        observations=observations,
                    )
                )
                self._running_reconciliation_claims()[workflow_id] = {
                    "receipt": None,
                    "evidence": dict(current),
                    "ui_fallback": True,
                    "reporting_unavailable": True,
                    "attachment_strategy": reporting_decision,
                }
                return self._authorize_reporting_degraded_attachment(
                    workflow,
                    reporting_decision,
                )
            self._retain_running_reconciliation_claim(
                workflow_id,
                receipt=receipt,
                acquisition=acquisition,
                temporal_binding=temporal_binding,
                context=context,
                evidence=current,
                strategy_decision=strategy_decision,
            )
            ready = self._supervisor.transition_battle_workflow(
                workflow_id,
                "ready",
                reason=(
                    "forced save and active-round identity were bound to the "
                    "final persisted battle scope"
                ),
                acknowledgement=current,
                save_receipt=receipt,
                configuration=self._attachment_configuration_report(
                    receipt,
                    strategy_decision,
                    stage="ready",
                ),
            )
            if ready is None or ready.get("status") != "ready":
                reporting_decision = (
                    self._attachment_reporting_failure_decision(
                        workflow,
                        reason=(
                            "Attach reconciliation was proven, but its durable "
                            "ready report could not be persisted; the battle "
                            "continues in degraded observation"
                        ),
                        observations=observations,
                    )
                )
                retained = self._running_reconciliation_claims().get(workflow_id)
                if isinstance(retained, dict):
                    retained["reporting_unavailable"] = True
                    retained["attachment_strategy"] = reporting_decision
                return self._authorize_reporting_degraded_attachment(
                    workflow,
                    reporting_decision,
                )
            return True

        manual = self._supervisor.manual_control
        if (
            isinstance(manual, Mapping)
            and manual.get("status") == "reconciling"
        ):
            workflow_id = str(manual.get("manual_control_id") or "")
            try:
                requirements = self._active_strategy_session_requirements()
                reconciliation = (
                    reconcile_acquired_requirements(
                        acquisition,
                        requirements,
                    )
                    if requirements
                    else {"checks": {}}
                )
                check_sets = self._save_reconciliation_check_sets(
                    reconciliation,
                    observations=(
                        observations
                        if isinstance(
                            observations,
                            RunningAttachmentSaveObservations,
                        )
                        else None
                    ),
                )
                resolved = check_sets["accepted"]
                unresolved = tuple(
                    sorted(
                        {
                            *check_sets["mismatched"],
                            *check_sets["ui_required"],
                        }
                    )
                )
                receipt = build_running_save_reconciliation_receipt(
                    kind="return_control_reconciliation",
                    workflow_id=workflow_id,
                    observation_id=str(current.get("observation_id") or ""),
                    acquisition=acquisition,
                    temporal_binding=temporal_binding,
                    disposition=disposition,
                    resolved_check_ids=resolved,
                    unresolved_check_ids=unresolved,
                )
            except (TypeError, ValueError) as exc:
                self._flag_recoverable_runtime_failure(
                    RuntimeFailureKind.REPORTING_FAILURE,
                    f"Return reconciliation receipt failed: {exc}",
                )
                self._supervisor.transition_manual_control(
                    workflow_id,
                    "failed",
                    detail=f"typed save reconciliation failed: {exc}",
                    refresh_status="save_reconciliation_failed",
                )
                return False
            self._pending_return_reconciliation_claims()[workflow_id] = {
                "receipt": copy.deepcopy(receipt),
                "acquisition": acquisition,
                "temporal_binding": temporal_binding,
                "context": context,
                "evidence": dict(current),
                "requirements": copy.deepcopy(requirements),
                "reconciliation": copy.deepcopy(reconciliation),
                "check_sets": copy.deepcopy(check_sets),
            }
            return self._retry_pending_running_return(manual, current)
        return False

    def _complete_ui_backed_operator_reconciliation(
        self,
        outcome: object,
    ) -> bool:
        """Release Attach/Return into supported UI discovery after save failure."""

        current = self._current_control_workflow_evidence()
        if not (
            isinstance(current, Mapping)
            and current.get("game_state") == "active_battle"
        ):
            return False
        confirmed_same = getattr(
            outcome,
            "confirmed_same_battle_scope_id",
            None,
        )
        confirmed_later = getattr(
            outcome,
            "confirmed_later_battle_scope_id",
            None,
        )
        disposition = (
            "later_battle"
            if confirmed_later
            else "same_battle"
            if confirmed_same
            else "attachment_baseline"
        )
        reason = str(
            getattr(outcome, "ui_fallback_reason", "")
            or "save_evidence_unavailable"
        )
        fallback_complete = bool(
            getattr(outcome, "ui_fallback_complete", False)
        )
        workflow = self._supervisor.battle_workflow
        if (
            isinstance(workflow, Mapping)
            and workflow.get("intent") == "attach_battle"
            and workflow.get("status")
            in {"validating_save", "action_dispatched"}
        ):
            workflow_id = str(workflow.get("request_id") or "")
            strategy_decision = self._attachment_strategy_decision(
                workflow,
                ui_fallback_reason=reason,
            )
            try:
                receipt = build_running_ui_reconciliation_receipt(
                    kind="running_attachment_reconciliation",
                    workflow_id=workflow_id,
                    observation_id=str(current.get("observation_id") or ""),
                    evidence=current,
                    disposition=disposition,
                    reason=reason,
                    fallback_complete=fallback_complete,
                )
            except (TypeError, ValueError) as exc:
                reporting_decision = (
                    self._attachment_reporting_failure_decision(
                        workflow,
                        reason=(
                            "Attach UI reconciliation reporting failed; the "
                            f"battle continues in degraded observation: {exc}"
                        ),
                    )
                )
                self._running_reconciliation_claims()[workflow_id] = {
                    "receipt": None,
                    "evidence": dict(current),
                    "ui_fallback": True,
                    "reporting_unavailable": True,
                    "attachment_strategy": reporting_decision,
                }
                return self._authorize_reporting_degraded_attachment(
                    workflow,
                    reporting_decision,
                )
            self._running_reconciliation_claims()[workflow_id] = {
                "receipt": copy.deepcopy(receipt),
                "evidence": dict(current),
                "ui_fallback": True,
                "attachment_strategy": strategy_decision,
            }
            ready = self._supervisor.transition_battle_workflow(
                workflow_id,
                "ready",
                reason=(
                    "save evidence was unusable; Battle History/UI continuity "
                    "was bound and supported UI monitoring remains available"
                ),
                acknowledgement=current,
                save_receipt=receipt,
                configuration=self._attachment_configuration_report(
                    receipt,
                    strategy_decision,
                    stage="ready",
                ),
            )
            if ready is None or ready.get("status") != "ready":
                reporting_decision = (
                    self._attachment_reporting_failure_decision(
                        workflow,
                        reason=(
                            "Attach UI continuity was proven, but its durable "
                            "ready report could not be persisted; the battle "
                            "continues in degraded observation"
                        ),
                    )
                )
                retained = self._running_reconciliation_claims().get(workflow_id)
                if isinstance(retained, dict):
                    retained["reporting_unavailable"] = True
                    retained["attachment_strategy"] = reporting_decision
                return self._authorize_reporting_degraded_attachment(
                    workflow,
                    reporting_decision,
                )
            return True

        manual = self._supervisor.manual_control
        if not (
            isinstance(manual, Mapping)
            and manual.get("status") == "reconciling"
        ):
            return False
        workflow_id = str(manual.get("manual_control_id") or "")
        requirements = self._active_strategy_session_requirements()
        ui_required = tuple(
            sorted(requested_player_save_check_ids(requirements))
        )
        check_sets = {
            "accepted": (),
            "mismatched": (),
            "ui_required": ui_required,
        }
        try:
            receipt = build_running_ui_reconciliation_receipt(
                kind="return_control_reconciliation",
                workflow_id=workflow_id,
                observation_id=str(current.get("observation_id") or ""),
                evidence=current,
                disposition=disposition,
                reason=reason,
                fallback_complete=fallback_complete,
                unresolved_check_ids=ui_required,
            )
        except (TypeError, ValueError) as exc:
            log(
                "[PLAYER_SAVE] Could not bind the Return Control UI fallback "
                f"receipt: {exc}",
                "ERROR",
            )
            return False
        self._pending_return_reconciliation_claims()[workflow_id] = {
            "receipt": copy.deepcopy(receipt),
            "evidence": dict(current),
            "requirements": copy.deepcopy(requirements),
            "check_sets": copy.deepcopy(check_sets),
            "ui_fallback": True,
        }
        return self._retry_pending_running_return(manual, current)

    def _annotate_home_battle_control(
        self,
        img: Frame,
        detection: Dict[str, Any],
    ) -> None:
        """Classify Home's battle control and log only semantic transitions."""

        if detection.get("state") != "HOME_SCREEN":
            self._last_logged_home_battle_control = None
            return

        evidence = detect_home_battle_control(img)
        detection["home_battle_control"] = evidence.control.value
        if evidence.control is getattr(
            self,
            "_last_logged_home_battle_control",
            None,
        ):
            return

        self._last_logged_home_battle_control = evidence.control
        log(
            "[BATTLE] Home control="
            f"{evidence.control.value} source={evidence.source} "
            f"confidence={evidence.confidence:.2f}",
            "DEBUG",
        )

    def run(self) -> None:
        log("Starting main heartbeat loop.", level="INFO", console=True)
        self._prune_generated_artifacts(force=True)
        if self._config.wait_on_start:
            try:
                AUTOMATION.mode = ExecMode.WAIT
                log("[CTRL] Startup flag: ExecMode set to WAIT", "INFO", console=True)
            except Exception:
                pass

        if self._supervisor.apply_control():
            self._status_reporter.request_immediate_report()
        self._observe_strategy_request()
        self._sync_interactive_development_control_boundary()
        self._sync_emulator_maintenance_control_boundary()
        if self._supervisor.is_paused:
            if stop_blind_gem_tapper():
                self._blind_tapper_suspended = True
        self._update_action_authority()
        self._publish_action_authority()
        mutation_guard_token = AUTOMATION.install_mutation_guard(
            self._runtime_control_mutation_guard,
            uncertain_result_handler=self._runtime_uncertain_mutation_result,
            guard_failure_handler=self._runtime_mutation_guard_failure,
            dispatch_control_lock_path=(
                self._supervisor.dispatch_control_lock_path
            ),
        )
        try:
            if self._adb_connection_coordinator.ensure_connected():
                time.sleep(2)

            threading.Thread(
                target=watchdog_process_check,
                args=(
                    30,
                    self._adb_connection_coordinator,
                    self._get_watchdog_mutation_guard(),
                ),
                daemon=True,
            ).start()

            while True:
                self._prune_generated_artifacts()
                # Control synchronization must not depend on a working ADB
                # connection. This both acknowledges Pause during an outage
                # and permits a paused live target handoff before capture.
                if self._supervisor.apply_control():
                    self._status_reporter.request_immediate_report()
                self._observe_strategy_request()
                self._sync_interactive_development_control_boundary()
                self._sync_emulator_maintenance_control_boundary()
                is_paused = self._supervisor.is_paused
                if is_paused and stop_blind_gem_tapper():
                    self._blind_tapper_suspended = True
                # Observation never needs input authority. Preserve the prior
                # heartbeat's durable owner and any blocking primary boundary
                # through capture, while merging newly accepted operator work.
                self._update_action_authority(
                    holds=self._pre_capture_authority_holds(),
                )
                self._publish_action_authority()

                img = self._capture_frame()
                if img is None:
                    self._advance_emulator_recovery_from_landscape_launcher()
                    continue

                detection = detect_state_and_overlays(img, log_matches=self._match_trace)
                game_speed_guard = getattr(self, "_game_speed_guard", None)
                if game_speed_guard is not None:
                    game_speed_guard.set_target(
                        self._supervisor.game_speed_target,
                        wave=self._last_wave_value,
                    )
                self._annotate_home_battle_control(img, detection)
                exclusive_validation_passive_battle_hold = (
                    self._observe_exclusive_validation_passive_battle_boundary(
                        detection
                    )
                )
                if self._quarantine_exclusive_validation_terminal_finalization(
                    detection
                ):
                    # The exact old boundary must finish before control workflow
                    # acknowledgement, mission observation, run adoption, or
                    # continuity can interpret a manually started successor.
                    time.sleep(1.0)
                    continue
                self._record_control_observation(detection)
                self._observe_blocking_primary_boundary(detection)
                self._setup_capture_source_refreshed = False
                detected_state = str(
                    detection.get("state") or "UNKNOWN"
                ).upper()
                if detected_state == "GAME_RESTARTED":
                    if getattr(
                        self,
                        "_emulator_maintenance_hold_active",
                        False,
                    ):
                        if self._advance_emulator_recovery(detection, img):
                            continue
                    self._update_action_authority(
                        detection=detection,
                        holds=(self._blocking_primary_authority_hold(),),
                    )
                    self._publish_action_authority()
                    if stop_blind_gem_tapper():
                        self._blind_tapper_suspended = True
                    warning_key = "unowned_game_restarted"
                    if warning_key not in self._free_ticket_recovery_warning_set():
                        self._free_ticket_recovery_warning_set().add(warning_key)
                        log(
                            "[EMULATOR_RECOVERY] Welcome Back is blocking, but "
                            "no exact BlueStacks maintenance request owns its "
                            "buttons; all ordinary input remains suppressed",
                            "WARN",
                            console=True,
                        )
                    continue
                if detected_state == "FREE_TICKET":
                    terminal_interrupted = (
                        self._reconcile_terminal_home_continuation(detection)
                    )
                    self._reconcile_dispatched_workflow_behind_blocker(
                        detection
                    )
                    self._observe_action_circuits(detection)
                    if terminal_interrupted:
                        continue
                    if self._advance_blocking_primary_recovery(img, detection):
                        continue
                if getattr(
                    self,
                    "_emulator_maintenance_hold_active",
                    False,
                ) and self._advance_emulator_recovery(detection, img):
                    # Recovery owns this fresh frame before workflow,
                    # continuity, mission, or monotonic observers see it.
                    continue
                emulator_recovery_routing = bool(
                    getattr(
                        self,
                        "_emulator_maintenance_hold_active",
                        False,
                    )
                )
                if (
                    not emulator_recovery_routing
                    and not exclusive_validation_passive_battle_hold
                ):
                    self._yield_on_unexpected_manual_activity()
                    self._sync_operator_control_workflows(detection, frame=img)
                    if self._reconcile_terminal_home_continuation(detection):
                        continue
                    self._observe_action_circuits(detection)
                if self._setup_capture_source_refreshed:
                    # The frame predates the Android-Home serialization route.
                    # Re-enter capture/detection before any ordinary handler.
                    continue
                operator_workflow_hold = (
                    None
                    if exclusive_validation_passive_battle_hold
                    else self._operator_workflow_authority_hold()
                )
                if emulator_recovery_routing:
                    self._update_action_authority(
                        detection=detection,
                        holds=(),
                    )
                    self._publish_action_authority()
                elif operator_workflow_hold is not None:
                    self._update_action_authority(
                        detection=detection,
                        holds=(operator_workflow_hold,),
                    )
                    self._publish_action_authority()
                if (
                    not emulator_recovery_routing
                    and not exclusive_validation_passive_battle_hold
                    and self._advance_pending_home_setup_recovery(img)
                ):
                    # Recovery may have navigated from a setup sub-screen. A
                    # fresh frame must establish verified Home before setup or
                    # any other handler runs again.
                    continue

                # This passive sidecar sees exact Home NEW_BATTLE before any
                # later setup or Home handler can dispatch an action. It never
                # returns a control decision and remains active during Pause.
                if not emulator_recovery_routing:
                    self._observe_player_save_audit_screen(detection)
                    self._sync_interactive_development_observation(detection)

                    self._mission_mgr.observe_detection(detection)
                    self._observe_no_strategy_frame(img, detection)

                    self._process_strategy_boundary(detection)
                    self._observe_strategy_gate_boundary(detection)

                # Update battle identity independently of screen navigation,
                # then give a genuinely initializing strategy exclusive tap
                # authority. No overlay handler, recovery tap, mission action,
                # or blind tapper may run before this gate clears.
                if emulator_recovery_routing:
                    battle_started = False
                else:
                    battle_started = self._mission_mgr.maybe_run_start(detection)
                    self._retain_exclusive_validation_launch_start_proof(
                        detection,
                        battle_started=battle_started is True,
                    )
                    self._observe_exclusive_validation_battle_start(
                        detection,
                        battle_started=battle_started is True,
                    )
                exclusive_validation_passive_battle_hold = bool(
                    exclusive_validation_passive_battle_hold
                    or getattr(
                        self,
                        "_exclusive_validation_passive_battle_hold",
                        None,
                    )
                )
                exclusive_validation_blocks_continuity = bool(
                    emulator_recovery_routing
                    or exclusive_validation_passive_battle_hold
                    or self._exclusive_validation_in_progress()
                    or self._exclusive_validation_launch_in_progress()
                    or getattr(
                        self,
                        "_exclusive_validation_ownership_hold",
                        False,
                    )
                )
                if battle_started is True:
                    self._complete_terminal_home_continuation(
                        battle_started,
                        detection,
                    )
                self._accept_pending_terminal_history_handoff()
                self._cancel_pending_tournament_validation_after_boundary(
                    detection
                )
                self._complete_started_battle_workflow(
                    battle_started is True,
                    detection,
                )
                if battle_started is True:
                    self._activation_tracker().reset()
                    self._perk_timeline().reset(fresh_battle=True)
                    self._reset_player_save_audit_perk_mapping_evidence()
                    self._steady_run_entry_pending = False
                    if game_speed_guard is not None:
                        game_speed_guard.reset_battle()
                save_coordinator = getattr(
                    self,
                    "_player_save_preflight_coordinator",
                    None,
                )
                save_carry = (
                    save_coordinator.carry
                    if save_coordinator is not None
                    else None
                )
                if (
                    is_paused
                    and save_carry is not None
                    and save_carry.state
                    not in {
                        CarriedEvidenceState.SUSPENDED,
                        CarriedEvidenceState.INVALIDATED,
                        CarriedEvidenceState.CONSUMED,
                    }
                ):
                    save_coordinator.suspend_carry(
                        "pause_requires_fresh_running_evidence"
                    )
                if battle_started is True or (
                    save_carry is not None
                    and save_carry.state is CarriedEvidenceState.LAUNCH_DISPATCHED
                ):
                    self._bind_started_battle_player_save_preflight(
                        battle_started=battle_started is True,
                        stable_running=(
                            str(detection.get("state") or "").upper()
                            == "RUNNING"
                        ),
                    )
                self._complete_ready_attachment_after_adoption()
                continuity_pending = False
                activity_continuity = getattr(
                    self,
                    "_activity_continuity",
                    None,
                )
                if (
                    activity_continuity is not None
                    and not emulator_recovery_routing
                    and not exclusive_validation_blocks_continuity
                ):
                    player_save_mode = str(
                        self._runtime_policy().get(
                            "player_save_preflight",
                            "save_first",
                        )
                    )
                    current_scope = get_activity_scope()
                    current_scope_id = (
                        str(current_scope.get("run_id") or "")
                        if current_scope
                        else ""
                    )
                    home_state = str(
                        detection.get("state") or ""
                    ).upper() in {"HOME", "HOME_SCREEN"}
                    home_new_battle = bool(
                        home_state
                        and HomeBattleControl.parse(
                            detection.get(
                                "home_battle_control",
                                "UNKNOWN",
                            )
                        )
                        is HomeBattleControl.NEW_BATTLE
                    )
                    home_requirements = (
                        self._mission_mgr.no_battle_setup_requirements()
                    )
                    current_preflight_ready = bool(
                        getattr(
                            self,
                            "_player_save_preflight_activity_scope_id",
                            None,
                        )
                        == current_scope_id
                        and getattr(
                            getattr(
                                self,
                                "_player_save_preflight_result",
                                None,
                            ),
                            "ready",
                            False,
                        )
                    )
                    save_history_baseline_blocked = bool(
                        getattr(
                            getattr(
                                self,
                                "_player_save_history_baseline_outcome",
                                None,
                            ),
                            "blocked",
                            False,
                        )
                        and getattr(
                            self,
                            "_player_save_preflight_activity_scope_id",
                            None,
                        )
                        == current_scope_id
                    )
                    forced_home_bundle_needed = bool(
                        (
                            player_save_mode
                            in {"save_first", "comparison_audit"}
                            and bool(home_requirements)
                        )
                        or (
                            player_save_mode == "save_first"
                            and not self._activity_scope_has_history_baseline(
                                current_scope
                            )
                        )
                    )
                    home_save_preflight_pending = bool(
                        home_new_battle
                        and getattr(
                            self,
                            "_player_save_preflight_coordinator",
                            None,
                        )
                        is not None
                        and (
                            (
                                forced_home_bundle_needed
                                and not current_preflight_ready
                            )
                            or save_history_baseline_blocked
                        )
                    )
                    initialization_blocks_history = (
                        self._mission_mgr.run_initialization_pending()
                    )
                    level_skip_priority_pending = (
                        self._level_skip_priority_pending(
                            initialization_pending=initialization_blocks_history
                        )
                    )
                    session_preflight_blocks_history = bool(
                        not initialization_blocks_history
                        and self._mission_mgr.session_preflight_pending()
                    )
                    post_retry_poll_allowed = not (
                        initialization_blocks_history
                        or session_preflight_blocks_history
                    )
                    reconciliation_owner = (
                        self._running_save_reconciliation_owner()
                    )
                    workflow_hold = self._operator_workflow_authority_hold()
                    continuity_needed = bool(
                        (
                            not self._awaiting_initial_battle_intent()
                            or reconciliation_owner is not None
                        )
                        and (
                            workflow_hold is None
                            or reconciliation_owner is not None
                        )
                        and activity_continuity.needs_check(
                            detection,
                            post_retry_poll_allowed=post_retry_poll_allowed,
                            defer_home_baseline=home_save_preflight_pending,
                            defer_running_check=level_skip_priority_pending,
                        )
                    )
                    operator_workflow_hold = workflow_hold
                    continuity_holds = (
                        (operator_workflow_hold,)
                        if operator_workflow_hold is not None
                        else (
                            AuthorityHoldState(
                                AuthorityHold.ACTIVITY_CONTINUITY,
                                "activity continuity owns its verification route",
                            ),
                        )
                        if continuity_needed
                        else ()
                    )
                    self._update_action_authority(
                        detection=detection,
                        holds=continuity_holds,
                    )
                    if (
                        not is_paused
                        and continuity_needed
                        and stop_blind_gem_tapper()
                    ):
                        self._blind_tapper_suspended = True
                    continuity = activity_continuity.handle(
                        detection,
                        actions_allowed=self._action_decision(
                            RuntimeActionClass.STRATEGY_ACTION,
                            owner=(
                                reconciliation_owner
                                or AuthorityHold.ACTIVITY_CONTINUITY
                            ),
                        ).allowed,
                        action_guard_fn=lambda: self._runtime_action_guard(
                            owner=(
                                reconciliation_owner
                                or AuthorityHold.ACTIVITY_CONTINUITY
                            )
                        ),
                        post_retry_poll_allowed=post_retry_poll_allowed,
                        defer_home_baseline=home_save_preflight_pending,
                        defer_running_check=level_skip_priority_pending,
                        player_save_mode=player_save_mode,
                    )
                    continuity_pending = continuity.pending
                    self._apply_activity_continuity_outcome(continuity)
                    if continuity.recapture:
                        self._publish_action_authority()
                        continue
                self._observe_terminal_run_binding(
                    detection,
                    continuity_pending=continuity_pending,
                )
                if not emulator_recovery_routing:
                    # Reconcile a claimed confirmed-launch receipt before owner
                    # selection. A failed fresh ownership reread retains the
                    # suppressive ownership hold; it must never expose ordinary
                    # routes for this heartbeat.
                    self._reconcile_exclusive_validation_launch()
                exclusive_validation_ownership_hold = bool(
                    getattr(
                        self,
                        "_exclusive_validation_ownership_hold",
                        False,
                    )
                )
                if (
                    battle_started is True
                    and not exclusive_validation_ownership_hold
                    and not self._mission_mgr.ctx.data.get(
                        "exclusive_validation_battle"
                    )
                ):
                    self._claim_proactive_gate_waivers(
                        for_home_setup=False
                    )
                initialization_pending = self._mission_mgr.run_initialization_pending()
                session_preflight_pending = (
                    not initialization_pending
                    and self._mission_mgr.session_preflight_pending()
                )
                if emulator_recovery_routing:
                    exclusive_validation_ownership_hold = False
                    initialization_pending = False
                    session_preflight_pending = False
                elif exclusive_validation_ownership_hold:
                    initialization_pending = False
                    session_preflight_pending = False
                if (
                    (initialization_pending or session_preflight_pending)
                    and not emulator_recovery_routing
                    and not self._mission_mgr.ctx.data.get(
                        "exclusive_validation_battle"
                    )
                ):
                    if self._claim_proactive_gate_waivers(for_home_setup=False):
                        initialization_pending = (
                            self._mission_mgr.run_initialization_pending()
                        )
                        session_preflight_pending = (
                            not initialization_pending
                            and self._mission_mgr.session_preflight_pending()
                        )
                legacy_session_preflight_block = bool(
                    session_preflight_pending
                    and self._mission_mgr.session_preflight_terminally_blocked()
                )
                degraded_fn = getattr(
                    self._mission_mgr,
                    "session_preflight_degraded",
                    None,
                )
                session_preflight_degraded = bool(
                    not emulator_recovery_routing
                    and callable(degraded_fn)
                    and degraded_fn() is True
                )
                if (
                    legacy_session_preflight_block
                    or session_preflight_degraded
                ):
                    self._handle_terminal_session_gate_decision()
                    session_preflight_pending = (
                        not initialization_pending
                        and self._mission_mgr.session_preflight_pending()
                    )
                    if legacy_session_preflight_block and session_preflight_pending:
                        self._flag_recoverable_runtime_failure(
                            RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                            "legacy session validation remained blocked after "
                            "migration; releasing its action hold",
                        )
                        session_preflight_pending = False
                self._sync_strategy_action_gate(
                    terminally_blocked=legacy_session_preflight_block,
                    detection=detection,
                )
                operator_workflow_hold = (
                    None
                    if exclusive_validation_passive_battle_hold
                    else self._operator_workflow_authority_hold()
                )
                exclusive_validation_in_progress = (
                    self._exclusive_validation_in_progress()
                )
                exclusive_validation_launch_in_progress = (
                    self._exclusive_validation_launch_in_progress()
                )
                exclusive_validation_terminal_finalization_pending = (
                    self._exclusive_validation_terminal_finalization_pending(
                        detection
                    )
                )
                authority_holds = (
                    ()
                    if emulator_recovery_routing
                    else self._heartbeat_action_authority_holds(
                        operator_workflow_hold=operator_workflow_hold,
                        continuity_pending=continuity_pending,
                        exclusive_validation_terminal_finalization_pending=(
                            exclusive_validation_terminal_finalization_pending
                        ),
                        exclusive_validation_passive_battle_hold=(
                            exclusive_validation_passive_battle_hold
                        ),
                        exclusive_validation_ownership_hold=(
                            exclusive_validation_ownership_hold
                        ),
                        exclusive_validation_in_progress=(
                            exclusive_validation_in_progress
                        ),
                        exclusive_validation_launch_in_progress=(
                            exclusive_validation_launch_in_progress
                        ),
                        initialization_pending=initialization_pending,
                        session_preflight_pending=session_preflight_pending,
                    )
                )
                self._update_action_authority(
                    detection=detection,
                    holds=authority_holds,
                )
                self._publish_action_authority()

                if (
                    not emulator_recovery_routing
                    and exclusive_validation_terminal_finalization_pending
                    and self._exclusive_validation_cleanup_dispatch_ready(
                        detection
                    )
                    and self._action_decision(
                        RuntimeActionClass.LIFECYCLE_ACTION,
                        owner=AuthorityHold.EXCLUSIVE_VALIDATION,
                    ).allowed
                ):
                    previous_owner = getattr(
                        self,
                        "_active_action_authority_owner",
                        None,
                    )
                    self._active_action_authority_owner = (
                        AuthorityHold.EXCLUSIVE_VALIDATION
                    )
                    try:
                        # Receipt-only completion must precede every successor
                        # Start/launch route. It either persists an already-
                        # proven outcome or finalizes the exact old boundary;
                        # it never inherits a new workflow's input authority.
                        self._dispatch_exclusive_validation_game_over(
                            detection
                        )
                    finally:
                        self._active_action_authority_owner = previous_owner
                    continue

                if (
                    not emulator_recovery_routing
                    and not continuity_pending
                    and self._advance_owned_exclusive_validation_launch(
                        img,
                        detection,
                        battle_started=battle_started is True,
                    )
                ):
                    continue

                # Validation Surrender is a lifecycle action owned by the same
                # typed hold as its bounded strategy checks. Install that hold
                # before advancing so Pause/Stop or a stronger workflow owner
                # can interrupt every input in the cleanup route.
                if (
                    not emulator_recovery_routing
                    and not continuity_pending
                    and exclusive_validation_in_progress
                    and self._advance_owned_exclusive_validation(detection)
                ):
                    continue

                strategy_action_allowed = self._action_decision(
                    RuntimeActionClass.STRATEGY_ACTION
                ).allowed
                lifecycle_action_allowed = self._action_decision(
                    RuntimeActionClass.LIFECYCLE_ACTION
                ).allowed
                operator_action_owner = (
                    operator_workflow_hold.hold
                    if operator_workflow_hold is not None
                    and operator_workflow_hold.hold
                    in {
                        AuthorityHold.OPERATOR_WORKFLOW,
                        AuthorityHold.MANUAL_CONTROL_RETURN,
                        AuthorityHold.SETUP_CAPTURE,
                    }
                    else None
                )
                operator_workflow_action_allowed = bool(
                    operator_action_owner is not None
                    and self._action_decision(
                        RuntimeActionClass.STRATEGY_ACTION,
                        owner=operator_action_owner,
                    ).allowed
                    and self._action_decision(
                        RuntimeActionClass.LIFECYCLE_ACTION,
                        owner=operator_action_owner,
                    ).allowed
                )
                repair_terminal_action_allowed = bool(
                    str(detection.get("state") or "").upper() == "GAME_OVER"
                    and self._mission_mgr.session_preflight_repair_in_progress()
                    is True
                    and self._action_decision(
                        RuntimeActionClass.LIFECYCLE_ACTION,
                        owner=AuthorityHold.SESSION_PREFLIGHT,
                    ).allowed
                )
                emulator_recovery_action_allowed = bool(
                    getattr(
                        self,
                        "_emulator_maintenance_hold_active",
                        False,
                    )
                    and self._action_decision(
                        RuntimeActionClass.STRATEGY_ACTION,
                        owner=AuthorityHold.EMULATOR_MAINTENANCE,
                    ).allowed
                    and self._action_decision(
                        RuntimeActionClass.LIFECYCLE_ACTION,
                        owner=AuthorityHold.EMULATOR_MAINTENANCE,
                    ).allowed
                )
                exclusive_validation_cleanup_action_allowed = bool(
                    self._exclusive_validation_cleanup_in_progress()
                    and self._exclusive_validation_cleanup_dispatch_ready(
                        detection
                    )
                    and self._action_decision(
                        RuntimeActionClass.LIFECYCLE_ACTION,
                        owner=AuthorityHold.EXCLUSIVE_VALIDATION,
                    ).allowed
                )
                game_speed_guard = getattr(self, "_game_speed_guard", None)
                if game_speed_guard is not None:
                    game_speed_guard.set_target(
                        self._supervisor.game_speed_target,
                        wave=self._last_wave_value,
                    )
                    expected_game_speed_target = (
                        self._supervisor.game_speed_target
                    )
                    game_speed_allowed = bool(
                        strategy_action_allowed
                        and self._handler_enabled("game_speed")
                        and self._game_speed_priority_ready(
                            initialization_pending=initialization_pending
                        )
                    )
                    if game_speed_guard.handle(
                        img,
                        detection,
                        action_guard_fn=lambda: (
                            game_speed_allowed
                            and self._runtime_action_guard()
                            and self._supervisor.game_speed_target
                            == expected_game_speed_target
                        ),
                    ):
                        # The guard may have captured several newer frames while
                        # walking the speed control. Re-enter through capture
                        # before any other consumer sees the stale frame.
                        continue
                if initialization_pending:
                    if not self._run_initialization_gate_logged:
                        log(
                            "[RUN_INIT] Exclusive startup gate active; normal handlers are blocked",
                            "INFO",
                            console=True,
                        )
                        self._run_initialization_gate_logged = True
                    if stop_blind_gem_tapper():
                        self._blind_tapper_suspended = True
                    if (
                        not continuity_pending
                        and self._action_decision(
                            RuntimeActionClass.STRATEGY_ACTION,
                            owner=AuthorityHold.RUN_INITIALIZATION,
                        ).allowed
                    ):
                        self._run_owned_strategy_tick(
                            AuthorityHold.RUN_INITIALIZATION,
                            img,
                            detection,
                            strategy_only=True,
                        )
                elif self._run_initialization_gate_logged:
                    strategy = self._mission_mgr.strategy
                    if (
                        strategy
                        and strategy.requires_run_initialization()
                        and strategy.is_run_initialization_complete(self._mission_mgr.ctx)
                    ):
                        if session_preflight_pending:
                            log(
                                "[RUN_INIT] Initialization checks complete; "
                                "session preflight is starting",
                                "INFO",
                                console=True,
                            )
                        else:
                            self._steady_run_entry_pending = True
                    self._run_initialization_gate_logged = False

                if session_preflight_pending:
                    if not self._session_preflight_gate_logged:
                        log(
                            "[SESSION_PREFLIGHT] Exclusive validation gate active; "
                            "normal handlers are blocked",
                            "INFO",
                            console=True,
                        )
                        self._session_preflight_gate_logged = True
                    if stop_blind_gem_tapper():
                        self._blind_tapper_suspended = True
                    session_preflight_owner = (
                        AuthorityHold.EXCLUSIVE_VALIDATION
                        if exclusive_validation_in_progress
                        else AuthorityHold.SESSION_PREFLIGHT
                    )
                    if (
                        not continuity_pending
                        and self._action_decision(
                            RuntimeActionClass.STRATEGY_ACTION,
                            owner=session_preflight_owner,
                        ).allowed
                    ):
                        self._run_owned_strategy_tick(
                            session_preflight_owner,
                            img,
                            detection,
                            strategy_only=True,
                        )
                elif self._session_preflight_gate_logged or getattr(
                    self,
                    "_session_preflight_terminal_blocked_logged",
                    False,
                ):
                    strategy = self._mission_mgr.strategy
                    if (
                        strategy
                        and strategy.requires_session_preflight()
                        and strategy.is_session_preflight_complete(
                            self._mission_mgr.ctx
                        )
                    ):
                        mission_vars = self._mission_mgr.ctx.data.get(
                            "mission_vars",
                            {},
                        )
                        if (
                            mission_vars.get("gc_session_preflight_last_status")
                            == "mismatch"
                            and not mission_vars.get(
                                "gc_session_preflight_blocked"
                            )
                        ):
                            message = (
                                "[SESSION_PREFLIGHT] Observation complete with "
                                "mismatches"
                            )
                        else:
                            message = (
                                "[SESSION_PREFLIGHT] Validation complete"
                            )
                        log(message, "INFO", console=True)
                        self._steady_run_entry_pending = True
                    self._session_preflight_gate_logged = False
                    self._session_preflight_terminal_blocked_logged = False
                    self._session_preflight_repair_denial_logged = False

                if (
                    not continuity_pending
                    and exclusive_validation_in_progress
                    and self._advance_owned_exclusive_validation(detection)
                ):
                    continue

                self._maybe_log_steady_run_entry(
                    actions_blocked=not strategy_action_allowed
                )

                if strategy_action_allowed and self._handler_enabled("upgrade_detail"):
                    img, detection, overlay_cleared = self._resolve_upgrade_detail_overlay(
                        img,
                        detection,
                    )
                    if not overlay_cleared:
                        time.sleep(0.3)
                        continue

                if (
                    strategy_action_allowed
                    and self._current_strategy_name() == "none"
                    and self._run_perk_selector.handle(
                        img,
                        detection,
                        action_guard_fn=self._no_strategy_action_guard,
                    )
                ):
                    # Perk selection owns its modal route. Recapture before any
                    # handler consumes the pre-route frame.
                    continue

                if (
                    strategy_action_allowed
                    and self._handle_no_strategy_in_battle_inventory(detection)
                ):
                    # The inventory owns a multi-screen route. Always recapture
                    # before any handler consumes the pre-route frame.
                    continue

                wave_val: Optional[int] = None
                wave_conf: float = -1.0
                if detection.get("state") == "RUNNING":
                    try:
                        wave_val, wave_conf = detect_wave_number_from_image(img)
                    except Exception:
                        wave_val, wave_conf = None, -1.0

                if wave_val is not None:
                    self._last_wave_value = wave_val
                    self._last_wave_conf = wave_conf
                    self._last_wave_ts = time.time()
                else:
                    wave_val = self._last_wave_value
                    wave_conf = self._last_wave_conf

                detection["wave"] = wave_val
                detection["wave_conf"] = wave_conf
                mv = self._mission_mgr.ctx.data.setdefault("mission_vars", {})
                mv["last_wave"] = wave_val
                mv["last_wave_conf"] = wave_conf
                mv["last_wave_ts"] = self._last_wave_ts
                wave_observed_at = (
                    datetime.fromtimestamp(
                        self._last_wave_ts,
                        tz=timezone.utc,
                    ).astimezone()
                    if self._last_wave_ts > 0
                    else None
                )
                if self._perk_timeline_enabled():
                    self._sync_perk_timeline_save_checkpoint()
                    self._perk_timeline().observe_passive(
                        img,
                        detection,
                        wave=wave_val,
                    )
                    self._sync_perk_exhaustion_evidence()
                    self._request_perk_checkpoint_for_passive_boundary()
                    self._observe_player_save_audit_perk_mapping_evidence()
                activation_tracker = self._activation_tracker()
                activation_events = activation_tracker.observe(
                    img,
                    ui_state=str(detection.get("state") or "UNKNOWN"),
                    wave=wave_val,
                    wave_confidence=wave_conf,
                    wave_observed_at=wave_observed_at,
                )
                for capture in activation_tracker.drain_evidence_captures():
                    evidence_path = self._retain_activation_evidence(capture)
                    if evidence_path is None:
                        continue
                    ability = str(capture.get("ability") or "")
                    sequence = int(capture.get("sequence") or 0)
                    activation_tracker.record_evidence_image(
                        ability,
                        sequence,
                        evidence_path,
                    )
                    for event in activation_events:
                        if (
                            event.get("ability") == ability
                            and int(event.get("sequence") or 0) == sequence
                        ):
                            event["evidence_image"] = evidence_path
                    display_name = {
                        "second_wind": "Second Wind",
                        "demon_mode": "Demon Mode",
                        "nuke": "Nuke",
                    }.get(ability, ability or "unknown ability")
                    log(
                        "[BATTLE_EVENT] Preserved first transition frame for "
                        f"{display_name} activation #{sequence}: {evidence_path}",
                        "INFO",
                    )
                self._observe_player_save_audit_visual_events(activation_events)
                for event in activation_events:
                    name = {
                        "second_wind": "Second Wind",
                        "demon_mode": "Demon Mode",
                        "nuke": "Nuke",
                    }.get(
                        str(event.get("ability") or ""),
                        str(event.get("ability") or "Unknown ability"),
                    )
                    wave_text = event.get("approximate_wave")
                    log(
                        f"[BATTLE_EVENT] {name} activation observed at "
                        f"approximately wave {wave_text if wave_text is not None else 'unknown'}",
                        "INFO",
                        console=True,
                    )

                new_state, menu, secondary, overlays = self._normalise_detection(detection)

                if strategy_action_allowed:
                    # Allow missions to react immediately to overlays before general state handling.
                    self._mission_mgr.handle_overlays(detection)

                    self._mission_mgr.on_state(detection)

                self._state_tracker.update(state=new_state, menu=menu, secondary=secondary, overlays=overlays)

                self._status_reporter.maybe_report(
                    img=img,
                    ui_state=new_state,
                    menu=menu,
                    secondary=secondary,
                    overlays=overlays,
                    wave=wave_val,
                    wave_conf=wave_conf,
                    allow_actions=(
                        strategy_action_allowed
                        and self._handler_enabled("coin_display")
                    ),
                )
                if self._handler_enabled("event_mission_warnings"):
                    self._emit_event_mission_warnings()

                if (
                    strategy_action_allowed
                    and self._handler_enabled("auto_return")
                    and self._runtime_policy().get("auto_return", True) is not False
                ):
                    self._supervisor.auto_return_check(img, new_state)

                if new_state == "UNKNOWN":
                    update_unknown_state(True)
                    if (
                        strategy_action_allowed
                        and self._handler_enabled("unknown_recovery")
                    ):
                        trigger_after = self._supervisor.auto_return_secs or 900
                        handle_unknown_state(img, trigger_after_s=trigger_after)
                else:
                    update_unknown_state(False)

                self._sync_floating_gem_tapper(
                    state=new_state,
                    auxiliary_authority=self._action_decision(
                        RuntimeActionClass.AUXILIARY_COLLECTION,
                        collector=AuxiliaryCollector.FLOATING_GEM_SCAN,
                    ),
                )

                if strategy_action_allowed:
                    self._mission_mgr.tick(img, detection)
                if strategy_action_allowed and lifecycle_action_allowed:
                    self._handle_primary_states(new_state, overlays, img)
                elif (
                    emulator_recovery_action_allowed
                    and new_state in {"HOME_SCREEN", "GAME_OVER"}
                ):
                    previous_owner = getattr(
                        self,
                        "_active_action_authority_owner",
                        None,
                    )
                    self._active_action_authority_owner = (
                        AuthorityHold.EMULATOR_MAINTENANCE
                    )
                    try:
                        self._handle_primary_states(
                            new_state,
                            overlays,
                            img,
                        )
                    finally:
                        self._active_action_authority_owner = previous_owner
                elif (
                    operator_workflow_action_allowed
                    and (
                        new_state == "HOME_SCREEN"
                        or (
                            new_state == "GAME_OVER"
                            and (
                                operator_action_owner
                                is AuthorityHold.MANUAL_CONTROL_RETURN
                                or self._preserved_game_over_recovery_allowed(
                                    new_state,
                                    owner=operator_action_owner,
                                )
                            )
                        )
                        or (
                            new_state == "RUNNING"
                            and operator_action_owner
                            is AuthorityHold.MANUAL_CONTROL_RETURN
                            and isinstance(
                                self._supervisor.manual_control,
                                Mapping,
                            )
                            and self._supervisor.manual_control.get("status")
                            == "awaiting_configuration"
                        )
                    )
                ):
                    previous_owner = getattr(
                        self,
                        "_active_action_authority_owner",
                        None,
                    )
                    self._active_action_authority_owner = (
                        operator_action_owner
                    )
                    try:
                        if (
                            new_state == "RUNNING"
                            and operator_action_owner
                            is AuthorityHold.MANUAL_CONTROL_RETURN
                        ):
                            self._advance_running_return_configuration(
                                img,
                                detection,
                            )
                        else:
                            self._handle_primary_states(
                                new_state,
                                overlays,
                                img,
                                operator_workflow_only=True,
                            )
                    finally:
                        self._active_action_authority_owner = previous_owner
                elif repair_terminal_action_allowed:
                    previous_owner = getattr(
                        self,
                        "_active_action_authority_owner",
                        None,
                    )
                    self._active_action_authority_owner = (
                        AuthorityHold.SESSION_PREFLIGHT
                    )
                    try:
                        self._handle_primary_states(
                            new_state,
                            overlays,
                            img,
                        )
                    finally:
                        self._active_action_authority_owner = previous_owner
                elif exclusive_validation_cleanup_action_allowed:
                    previous_owner = getattr(
                        self,
                        "_active_action_authority_owner",
                        None,
                    )
                    self._active_action_authority_owner = (
                        AuthorityHold.EXCLUSIVE_VALIDATION
                    )
                    try:
                        # This owner grants only the durable receipt's verified
                        # Home cleanup. Do not enter the general Game Over
                        # dispatcher, where unrelated recovery, reporting, or
                        # terminal-policy routes could inherit this owner.
                        self._dispatch_exclusive_validation_game_over(
                            detection
                        )
                    finally:
                        self._active_action_authority_owner = previous_owner
                elif (
                    getattr(self, "_pending_auxiliary_cleanup", None)
                    is not None
                    or self._get_action_authority().strategy_gate is not None
                ):
                    self._handle_strategy_gate_auxiliary_actions(
                        new_state,
                        overlays,
                        img,
                    )

                sleep_interval = 1.0 if initialization_pending else 5.0
                try:
                    override = float(mv.get("loop_sleep_override_sec") or 0.0)
                    if override > 0:
                        sleep_interval = max(0.5, override)
                except Exception:
                    sleep_interval = 5.0
                time.sleep(sleep_interval)
        except KeyboardInterrupt:
            log("KeyboardInterrupt — shutting down.", "INFO")
        finally:
            # The daemon watchdog, tap queue, and collector workers can outlive
            # this method briefly.  Latch mutation shutdown before any cleanup
            # and retain the deny-all state after the callback is removed.
            self._runtime_shutting_down = True
            AUTOMATION.shutdown_mutations(mutation_guard_token)
            stop_blind_gem_tapper()
            lease = getattr(
                getattr(self, "_supervisor", None),
                "interactive_development_lease",
                None,
            )
            if (
                isinstance(lease, Mapping)
                and lease.get("request_state") != "terminal"
            ):
                self._terminate_interactive_development_lease(
                    lease,
                    disposition="abnormal",
                    reason="the production runtime shut down",
                    abnormal=True,
                )
            self._update_action_authority(shutting_down=True)
            self._publish_action_authority(runtime_active=False)
            scheduler = getattr(self, "_player_save_passive_scheduler", None)
            if scheduler is not None:
                try:
                    scheduler.close(wait=False)
                except Exception:
                    pass
            collector = getattr(self, "_player_save_audit_collector", None)
            if collector is not None:
                try:
                    collector.close(wait=False)
                except Exception:
                    pass
            AUTOMATION.clear_mutation_guard(mutation_guard_token)
            log("Exited cleanly.", "INFO")

    def _capture_frame(self) -> Optional[Frame]:
        """Capture a new frame from the device, retrying once if ADB reconnects."""
        coordinator = self._adb_connection_coordinator
        self._last_screenshot_capture_result = None
        if (
            not coordinator.capture_allowed()
            and not coordinator.ensure_connected()
        ):
            time.sleep(2)
            return None

        result = capture_and_save_screenshot_result(
            log_capture=False,
            log_empty=False,
            report_adb_errors=False,
        )
        self._last_screenshot_capture_result = result
        if result.frame is not None:
            coordinator.record_capture_success()
            return result.frame
        if result.failure is ScreenshotFailure.UNSUPPORTED_GEOMETRY:
            coordinator.record_capture_success(target=result.adb_target)
            if self._is_emulator_recovery_landscape_launcher_capture(result):
                time.sleep(1)
                return None
            log(
                "Failed to capture screenshot while ADB remained connected "
                f"({result.detail or result.failure.value}).",
                level="FAIL",
            )
            time.sleep(2)
            return None

        if not coordinator.ensure_connected():
            time.sleep(2)
            return None
        if result.failure is ScreenshotFailure.EMPTY:
            log(
                "[ADB] Empty screenshot data while the target remains connected",
                "ERROR",
            )

        time.sleep(1)
        retry = capture_and_save_screenshot_result(
            log_capture=False,
            log_empty=False,
            report_adb_errors=False,
        )
        self._last_screenshot_capture_result = retry
        if retry.frame is not None:
            coordinator.record_capture_success()
            return retry.frame

        if not coordinator.ensure_connected():
            time.sleep(2)
            return None
        if retry.failure is ScreenshotFailure.EMPTY:
            log(
                "[ADB] Empty screenshot data while the target remains connected",
                "ERROR",
            )
        failure_detail = retry.detail or (
            retry.failure.value if retry.failure is not None else "unknown"
        )
        log(
            "Failed to capture screenshot while ADB remained connected "
            f"({failure_detail}).",
            level="FAIL",
        )
        time.sleep(2)
        return None

    def _defer_home_setup_recovery(self) -> None:
        """Retain one yielded Home route only for its exact current owner."""

        owner = getattr(self, "_active_action_authority_owner", None)
        owner_value = owner.value if isinstance(owner, AuthorityHold) else owner
        owner_value = str(owner_value or "").strip() or None
        supervisor = getattr(self, "_supervisor", None)
        workflow_id = None
        if supervisor is not None and owner_value == AuthorityHold.OPERATOR_WORKFLOW.value:
            workflow = supervisor.battle_workflow
            if isinstance(workflow, Mapping):
                workflow_id = str(workflow.get("request_id") or "").strip()
        elif (
            supervisor is not None
            and owner_value == AuthorityHold.MANUAL_CONTROL_RETURN.value
        ):
            manual = supervisor.manual_control
            if isinstance(manual, Mapping):
                workflow_id = str(
                    manual.get("manual_control_id") or ""
                ).strip()
        try:
            evidence = self._current_control_workflow_evidence() or {}
        except Exception:
            evidence = {}
        if not all(
            evidence.get(field) is not None
            for field in (
                "runtime_id",
                "pid",
                "adb_target",
                "target_generation",
                "activity_scope_run_id",
            )
        ):
            self._pending_home_setup_recovery = None
            log(
                "[GC_NO_BATTLE] Yielded Home setup has no exact recovery "
                "binding; later cleanup input is unavailable",
                "WARN",
            )
            return
        self._pending_home_setup_recovery = {
            "operation_id": new_operation_id(),
            "owner": owner_value,
            "workflow_id": workflow_id,
            "runtime_id": evidence.get("runtime_id"),
            "pid": evidence.get("pid"),
            "adb_target": evidence.get("adb_target"),
            "target_generation": evidence.get("target_generation"),
            "activity_scope_run_id": evidence.get("activity_scope_run_id"),
        }

    def _home_setup_recovery_owner_matches(
        self,
        pending: Mapping[str, object],
        current: Mapping[str, object],
    ) -> bool:
        """Reject a yielded cleanup after any workflow or binding change."""

        for field in (
            "runtime_id",
            "pid",
            "adb_target",
            "target_generation",
            "activity_scope_run_id",
        ):
            expected = pending.get(field)
            if expected is not None and current.get(field) != expected:
                return False
        owner_value = str(pending.get("owner") or "")
        workflow_id = str(pending.get("workflow_id") or "")
        if owner_value == AuthorityHold.OPERATOR_WORKFLOW.value:
            workflow = self._supervisor.battle_workflow
            return bool(
                workflow_id
                and isinstance(workflow, Mapping)
                and workflow.get("request_id") == workflow_id
                and workflow.get("status")
                not in BATTLE_WORKFLOW_TERMINAL_STATUSES
            )
        if owner_value == AuthorityHold.MANUAL_CONTROL_RETURN.value:
            manual = self._supervisor.manual_control
            return bool(
                workflow_id
                and isinstance(manual, Mapping)
                and manual.get("manual_control_id") == workflow_id
                and manual.get("status") not in MANUAL_CONTROL_TERMINAL_STATUSES
            )
        if owner_value:
            hold = self._operator_workflow_authority_hold()
            return bool(hold is not None and hold.hold.value == owner_value)
        workflow = self._supervisor.battle_workflow
        manual = self._supervisor.manual_control
        return bool(
            not (
                isinstance(workflow, Mapping)
                and workflow.get("status")
                not in BATTLE_WORKFLOW_TERMINAL_STATUSES
            )
            and not (
                isinstance(manual, Mapping)
                and manual.get("status") not in MANUAL_CONTROL_TERMINAL_STATUSES
            )
        )

    def _advance_pending_home_setup_recovery(
        self,
        screenshot: Frame,
    ) -> bool:
        """Recover a yielded Home route on a later same-owner Enabled frame."""

        pending = getattr(self, "_pending_home_setup_recovery", None)
        if not isinstance(pending, Mapping):
            return False
        current = self._current_control_workflow_evidence()
        owner_matches = bool(
            isinstance(current, Mapping)
            and self._home_setup_recovery_owner_matches(pending, current)
        )
        if not owner_matches:
            self._pending_home_setup_recovery = None
            log(
                "[GC_NO_BATTLE] Discarded yielded Home recovery after its "
                "runtime, scope, or workflow owner changed",
                "INFO",
            )
            return False
        if self._supervisor.control_state != "RUNNING":
            return False
        if current.get("game_state") in {
            "active_battle",
            "home_resume_battle",
            "game_over",
            "tournament_results",
        }:
            self._pending_home_setup_recovery = None
            log(
                "[GC_NO_BATTLE] Discarded yielded Home recovery at an "
                f"incompatible {current.get('game_state')} boundary",
                "WARN",
            )
            return False
        owner_value = str(pending.get("owner") or "")
        try:
            owner = AuthorityHold(owner_value) if owner_value else None
        except ValueError:
            self._pending_home_setup_recovery = None
            return False

        def action_allowed() -> bool:
            return bool(
                self._runtime_action_guard(owner=owner)
                and self._runtime_action_guard(
                    action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                    owner=owner,
                )
                and self._home_setup_recovery_owner_matches(pending, current)
            )

        if not action_allowed():
            return False
        operation_id = str(pending.get("operation_id") or new_operation_id())
        log_action_intent(
            "Restoring verified Home after a yielded setup",
            reason=(
                "the same explicit workflow owner was Enabled on a later "
                "observation heartbeat"
            ),
            detail=(
                "[GC_NO_BATTLE_RECOVERY] owner="
                f"{owner_value or 'automation'} workflow_id="
                f"{pending.get('workflow_id') or 'none'}"
            ),
            operation_id=operation_id,
        )
        recovered = recover_gc_no_battle_setup_home(
            screenshot=screenshot,
            action_guard_fn=action_allowed,
        )
        recovery_failed = bool(
            not recovered
            and self._supervisor.control_state == "RUNNING"
            and self._home_setup_recovery_owner_matches(pending, current)
        )
        result_status = (
            "completed"
            if recovered
            else "failed"
            if recovery_failed
            else "interrupted"
        )
        log_result(
            (
                "Verified no-battle Home restored; setup will restart from "
                "fresh evidence"
                if recovered
                else "Home recovery failed safely; Automation Paused"
                if recovery_failed
                else "Home recovery yielded without granting further input"
            ),
            detail=(
                "[GC_NO_BATTLE_RECOVERY] result="
                f"{result_status}"
            ),
            operation_id=operation_id,
        )
        if recovered:
            self._pending_home_setup_recovery = None
        elif recovery_failed:
            self._pending_home_setup_recovery = None
            failure_reason = (
                "verified Home recovery failed after the same workflow was "
                "explicitly Enabled; no further cleanup input is authorized"
            )
            self._supervisor.pause_for_catastrophic_failure(
                RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                reason=failure_reason,
            )
            workflow_id = str(pending.get("workflow_id") or "")
            if owner is AuthorityHold.OPERATOR_WORKFLOW and workflow_id:
                self._supervisor.transition_battle_workflow(
                    workflow_id,
                    "failed",
                    reason=failure_reason,
                    acknowledgement=current,
                )
            elif owner is AuthorityHold.MANUAL_CONTROL_RETURN and workflow_id:
                self._supervisor.transition_manual_control(
                    workflow_id,
                    "failed",
                    detail=failure_reason,
                    refresh_status="home_recovery_failed",
                )
        return True

    def _run_home_setup_attempts(
        self,
        requirements: Mapping[str, Any],
        *,
        screenshot,
        waivers: Optional[Mapping[str, Any]] = None,
        save_preflight=None,
    ):
        """Retry a recoverable Home setup from fresh evidence before blocking."""

        current = screenshot
        for attempt in range(1, HOME_SETUP_MAX_ATTEMPTS + 1):
            setup_kwargs: Dict[str, Any] = {
                "screenshot": current,
                "action_guard_fn": self._runtime_action_guard,
            }
            if waivers:
                setup_kwargs["waivers"] = dict(waivers)
            coordinator = getattr(
                self,
                "_player_save_preflight_coordinator",
                None,
            )
            carry = coordinator.carry if coordinator is not None else None
            save_still_valid = bool(
                getattr(coordinator, "snapshot_invalidated", False) is not True
                and (
                    carry is None
                    or carry.state is not CarriedEvidenceState.INVALIDATED
                )
            )
            if save_preflight is not None and save_still_valid:
                setup_kwargs["save_decisions"] = dict(
                    save_preflight.decisions
                )
                if coordinator is not None:
                    setup_kwargs["snapshot_invalidation_fn"] = (
                        coordinator.invalidate
                    )
                    record_ui_verification = getattr(
                        coordinator,
                        "record_ui_verification",
                        None,
                    )
                    if callable(record_ui_verification):
                        setup_kwargs["save_ui_verification_fn"] = (
                            record_ui_verification
                        )
                    record_mapping_observation = getattr(
                        coordinator,
                        "record_mapping_observation",
                        None,
                    )
                    if callable(record_mapping_observation):
                        setup_kwargs["save_mapping_observation_fn"] = (
                            record_mapping_observation
                        )
                    close_mapping_candidate_window = getattr(
                        coordinator,
                        "close_mapping_candidate_window",
                        None,
                    )
                    if callable(close_mapping_candidate_window):
                        setup_kwargs["save_mapping_window_close_fn"] = (
                            close_mapping_candidate_window
                        )
            with AUTOMATION.action_guard_scope(
                setup_kwargs["action_guard_fn"]
            ):
                setup = run_gc_no_battle_setup(
                    requirements,
                    **setup_kwargs,
                )
            if setup.interrupted:
                self._defer_home_setup_recovery()
                return setup
            if (
                setup.complete
                or (
                    getattr(setup, "status", None)
                    is not GcNoBattleSetupStatus.FAILED
                )
                or not getattr(setup, "retryable_from_home", True)
                or attempt == HOME_SETUP_MAX_ATTEMPTS
            ):
                return setup

            check_id = setup.failed_check or "startup_setup"
            close_mapping_candidate_window = getattr(
                coordinator,
                "close_mapping_candidate_window",
                None,
            )
            if callable(close_mapping_candidate_window):
                close_mapping_candidate_window(
                    f"home_setup_retry:{check_id}"
                )
            revalidation_required = bool(
                isinstance(getattr(setup, "evidence", None), Mapping)
                and isinstance(
                    setup.evidence.get("save_preflight"),
                    Mapping,
                )
                and setup.evidence["save_preflight"].get(
                    "revalidation_required"
                )
                is True
            )
            log(
                f"[GC_NO_BATTLE] Home setup attempt {attempt}/"
                f"{HOME_SETUP_MAX_ATTEMPTS} failed at {check_id}: "
                f"{setup.reason}; retrying the complete setup from fresh "
                "Home evidence",
                "INFO" if revalidation_required else "WARN",
                console=True,
            )
            current = self._capture_frame()
            if current is None:
                log(
                    "[GC_NO_BATTLE] Fresh Home capture failed; automatic "
                    "setup retry cannot continue",
                    "ERROR",
                    console=True,
                )
                return setup

        raise AssertionError("Home setup retry loop did not return")

    def _handoff_adb_port(self, port: int) -> bool:
        """Move a paused live runtime to another localhost ADB endpoint."""

        session = self._adb_target_session
        if session is None:
            return False
        if self._exclusive_validation_blocks_target_handoff():
            log(
                "[CTRL] Deferring paused ADB target handoff while exclusive "
                "validation still owns or is finalizing its current target",
                "WARN",
                console=True,
            )
            return False
        if stop_blind_gem_tapper():
            self._blind_tapper_suspended = True
        target = f"localhost:{port}"
        log(f"[CTRL] Validating paused ADB target handoff to {target}", "INFO")

        def validate() -> bool:
            if not self._adb_connection_coordinator.ensure_connected(force=True):
                return False
            time.sleep(1)
            result = capture_and_save_screenshot_result(
                log_capture=False,
                log_empty=False,
                report_adb_errors=False,
            )
            if result.frame is None:
                return False
            self._adb_connection_coordinator.record_capture_success()
            return True

        try:
            return session.handoff(target, validate=validate)
        except Exception as exc:
            log(f"[CTRL] Unable to hand off ADB target to {target}: {exc}", "WARN")
            return False

    def _sync_floating_gem_tapper(
        self,
        *,
        state: str,
        auxiliary_authority: ActionAuthorityDecision,
    ) -> None:
        """Cooperatively stop a bounded tapper after authority is lost."""

        if state != "RUNNING" or not auxiliary_authority.allowed:
            stop_blind_gem_tapper()
            self._blind_tapper_suspended = False
            return

    def _handle_emulator_replay_auxiliary_actions(
        self,
        detection: Mapping[str, Any],
        img: Frame,
    ) -> None:
        """Run only reviewed independent collectors during restart replay."""

        if not self._emulator_replay_auxiliary_collectors():
            return
        state, _, _, overlays = self._normalise_detection(detection)
        if state != "RUNNING":
            return
        self._sync_floating_gem_tapper(
            state=state,
            auxiliary_authority=self._action_decision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                collector=AuxiliaryCollector.FLOATING_GEM_SCAN,
            ),
        )
        self._handle_strategy_gate_auxiliary_actions(
            state,
            overlays,
            img,
        )

    def _handle_strategy_gate_auxiliary_actions(
        self,
        state: str,
        overlays: Set[str],
        img: Frame,
    ) -> None:
        """Dispatch only explicitly allowlisted independent collectors."""

        if self._resume_pending_auxiliary_cleanup():
            return
        if (
            state == "RUNNING"
            and getattr(self, "_daily_gem_scheduler", None) is not None
            and self._handler_enabled("daily_gem")
            and self._handle_daily_gem_if_due(state, overlays)
        ):
            return
        if (
            state == "RUNNING"
            and getattr(self, "_mission_reward_scheduler", None) is not None
            and self._handler_enabled("mission_rewards")
            and self._handle_mission_rewards_if_due(state, img, overlays)
        ):
            return
        if (
            state == "RUNNING"
            and "AD_GEMS_AVAILABLE" in overlays
            and self._handler_enabled("ad_gem")
            and self._action_decision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                collector=AuxiliaryCollector.IN_BATTLE_AD_GEM,
            ).allowed
        ):
            handle_ad_gem(
                action_guard_fn=self._auxiliary_action_guard(
                    AuxiliaryCollector.IN_BATTLE_AD_GEM
                ),
                floating_action_guard_fn=self._auxiliary_action_guard(
                    AuxiliaryCollector.FLOATING_GEM_SCAN
                ),
            )

    def _observe_no_strategy_frame(
        self,
        img: Frame,
        detection: Mapping[str, Any],
    ) -> None:
        """Passively accumulate actual values without acquiring tap authority."""

        state = str(detection.get("state") or "UNKNOWN")
        pending = getattr(self, "_pending_no_strategy_record", None) is not None
        if not pending and self._current_strategy_name() != "none":
            return
        if state == "RUNNING":
            self._no_strategy_observation_active = True
        if not self._no_strategy_observation_active and not pending:
            return
        phase = "post_run_home" if pending else "in_battle"
        try:
            self._no_strategy_observer.observe(img, detection, phase=phase)
        except Exception as exc:
            log(f"[NO_STRATEGY] Passive observation failed: {exc}", "WARN")

    def _refreshed_operator_authority_holds(
        self,
        *,
        release_stale: bool,
    ) -> tuple[AuthorityHoldState, ...]:
        """Merge newly accepted operator ownership into the current holds.

        General action routes retain an already-published operator hold until
        the heartbeat reconciles its semantic completion. Home Ad collection
        is the one reviewed exception: it refreshes (and may release) that
        stale hold because its collector permission is derived from the latest
        workflow state.
        """

        operator_holds = {
            AuthorityHold.OPERATOR_WORKFLOW,
            AuthorityHold.MANUAL_CONTROL_RETURN,
            AuthorityHold.SETUP_CAPTURE,
        }
        current = tuple(getattr(self, "_authority_holds", ()))
        if getattr(
            self,
            "_exclusive_validation_passive_battle_hold",
            None,
        ):
            # No receipt-owned input remains, but the inconclusive battle must
            # stay automation-passive until a genuine terminal/no-battle
            # boundary. Synthesize the suppressive hold even for background
            # guards that run between heartbeat owner selections.
            return (
                AuthorityHoldState(
                    AuthorityHold.EXCLUSIVE_OWNERSHIP,
                    "an inconclusive validation battle remains passive",
                ),
            )
        if getattr(self, "_exclusive_validation_terminal_hold", None):
            # A successor Start may be queued while exact Game Over/Home proof
            # is being persisted. It is future work, not authority over the
            # old receipt's remaining cleanup. Pause/Stop still deny globally;
            # defer every newly requested typed workflow until finalization.
            return current
        if release_stale:
            current = tuple(
                hold for hold in current if hold.hold not in operator_holds
            )
        refreshed = self._operator_workflow_authority_hold()
        if refreshed is None or any(
            hold.hold is refreshed.hold for hold in current
        ):
            return current
        return current + (refreshed,)

    def _runtime_control_mutation_guard(self) -> bool:
        """Refresh durable Pause/Stop intent at the final ADB boundary."""

        if getattr(self, "_runtime_shutting_down", False):
            return False
        if self._supervisor.apply_control():
            reporter = getattr(self, "_status_reporter", None)
            if reporter is not None:
                reporter.request_immediate_report()
        if self._uninstalled_durable_action_owner_pending():
            return False
        malformed_authority = getattr(
            self._supervisor,
            "input_authority_error",
            None,
        )
        if malformed_authority is not None:
            if self._supervisor.control_state not in {"PAUSED", "STOPPED"}:
                self._supervisor.pause_for_catastrophic_failure(
                    RuntimeFailureKind.CONTROL_AUTHORITY_LOST,
                    reason=(
                        f"malformed {malformed_authority} directive made "
                        "device-input ownership unknowable"
                    ),
                )
            return False
        lease = self._supervisor.interactive_development_lease
        development_requested = bool(
            isinstance(lease, Mapping)
            and lease.get("request_state") != "terminal"
        )
        return bool(
            self._supervisor.control_state == "RUNNING"
            and not development_requested
            and not getattr(self, "_runtime_shutting_down", False)
        )

    def _uninstalled_durable_action_owner_pending(self) -> bool:
        """Fail closed when a new durable owner arrived between heartbeats."""

        maintenance = self._supervisor.emulator_maintenance
        maintenance_request_id = (
            str(maintenance.get("request_id") or "")
            if isinstance(maintenance, Mapping)
            else ""
        )
        terminal_pending = getattr(
            self,
            "_emulator_recovery_terminal_pending",
            None,
        )
        reporting_only = bool(
            maintenance_request_id
            and isinstance(terminal_pending, Mapping)
            and str(terminal_pending.get("request_id") or "")
            == maintenance_request_id
        )
        if (
            isinstance(maintenance, Mapping)
            and str(maintenance.get("state") or "") != "terminal"
            and not getattr(
                self,
                "_emulator_maintenance_hold_active",
                False,
            )
            and not reporting_only
        ):
            return True

        ledger = self._supervisor.exclusive_validation
        receipts = ledger.get("receipts")
        request_id = str(ledger.get("current_request_id") or "")
        receipt = (
            receipts.get(request_id)
            if isinstance(receipts, Mapping) and request_id
            else None
        )
        launch = receipt.get("launch") if isinstance(receipt, Mapping) else None
        return bool(
            isinstance(launch, Mapping)
            and str(launch.get("status") or "") == "requested"
            and request_id
            != str(
                getattr(
                    self,
                    "_active_exclusive_validation_launch_request_id",
                    None,
                )
                or ""
            )
        )

    def _runtime_uncertain_mutation_result(self, reason: str) -> None:
        """Persist the catastrophic result of a timed-out ADB mutation."""

        self._supervisor.pause_for_catastrophic_failure(
            RuntimeFailureKind.INPUT_RESULT_UNCERTAIN,
            reason=reason,
        )
        self._update_action_authority()
        self._publish_action_authority()
        reporter = getattr(self, "_status_reporter", None)
        if reporter is not None:
            reporter.request_immediate_report()

    def _runtime_mutation_guard_failure(self, reason: str) -> None:
        """Persist loss of the final durable-control authority check."""

        self._supervisor.pause_for_catastrophic_failure(
            RuntimeFailureKind.CONTROL_AUTHORITY_LOST,
            reason=reason,
        )
        self._update_action_authority()
        self._publish_action_authority()
        reporter = getattr(self, "_status_reporter", None)
        if reporter is not None:
            reporter.request_immediate_report()

    def _runtime_action_guard(
        self,
        *,
        action_class: RuntimeActionClass = RuntimeActionClass.STRATEGY_ACTION,
        owner: Optional[AuthorityHold | str] = None,
        collector: Optional[AuxiliaryCollector] = None,
        route: Optional[AuxiliaryRouteLease] = None,
        observed_battle_scope: object = _BATTLE_SCOPE_UNSET,
    ) -> bool:
        """Synchronize control and recheck the central typed authority."""

        if self._supervisor.apply_control():
            self._status_reporter.request_immediate_report()
        if self._uninstalled_durable_action_owner_pending():
            return False
        refreshed_holds = self._refreshed_operator_authority_holds(
            release_stale=bool(
                action_class is RuntimeActionClass.AUXILIARY_COLLECTION
                and collector is AuxiliaryCollector.HOME_AD_GEM
            )
        )
        self._update_action_authority(
            holds=refreshed_holds,
            observed_battle_scope=observed_battle_scope
        )
        effective_owner = owner or getattr(
            self,
            "_active_action_authority_owner",
            None,
        )
        return self._action_decision(
            action_class,
            owner=effective_owner,
            collector=collector,
            route=route,
        ).allowed

    def _auxiliary_action_guard(
        self,
        collector: AuxiliaryCollector,
        *,
        route: Optional[AuxiliaryRouteLease] = None,
    ) -> Callable[[], bool]:
        """Bind one collector/input route to the current gate and run scope."""

        gate = self._get_action_authority().strategy_gate
        bound_gate_id = gate.gate_id if gate is not None else None
        bound_scope = (
            route.battle_scope if route is not None else self._current_run_scope_id()
        )

        def allowed() -> bool:
            current_gate = self._get_action_authority().strategy_gate
            current_gate_id = (
                current_gate.gate_id if current_gate is not None else None
            )
            if current_gate_id != bound_gate_id:
                return False
            current_scope = self._current_run_scope_id()
            if (
                bound_scope is not None
                and current_scope is not None
                and bound_scope != current_scope
            ):
                return False
            return self._runtime_action_guard(
                action_class=RuntimeActionClass.AUXILIARY_COLLECTION,
                collector=collector,
                route=route,
                # The hot floating-gem path has already refreshed this scope.
                # Do not parse the same run ledger twice before one input.
                observed_battle_scope=current_scope,
            )

        return allowed

    def _begin_auxiliary_route(
        self,
        collectors: tuple[AuxiliaryCollector, ...],
        *,
        source_state: str,
    ) -> Optional[AuxiliaryRouteLease]:
        """Claim exclusive route ownership after a fresh source observation."""

        current_scope = self._current_run_scope_id()
        self._update_action_authority(
            observed_battle_scope=current_scope
        )
        lease = self._get_action_authority().begin_auxiliary_route(
            collectors,
            battle_scope=current_scope,
            source_state=source_state,
        )
        if lease is not None:
            self._publish_action_authority()
        return lease

    def _auxiliary_route_state_callback(
        self,
        lease: AuxiliaryRouteLease,
    ) -> Callable[[str, bool, Optional[str]], None]:
        def update(
            expected_state: str,
            cleanup_pending: bool,
            reason: Optional[str],
        ) -> None:
            self._get_action_authority().update_auxiliary_route(
                lease,
                expected_state=expected_state,
                cleanup_pending=cleanup_pending,
                suspended_reason=reason,
            )
            self._publish_action_authority()

        return update

    def _release_auxiliary_route(
        self,
        lease: AuxiliaryRouteLease,
        *,
        reason: str,
    ) -> None:
        self._get_action_authority().release_auxiliary_route(
            lease,
            reason=reason,
        )
        pending = getattr(self, "_pending_auxiliary_cleanup", None)
        if pending is not None and pending[1].route_id == lease.route_id:
            self._pending_auxiliary_cleanup = None
        self._publish_action_authority()

    def _resume_pending_auxiliary_cleanup(self) -> bool:
        """Resume only collector-owned cleanup after fresh authority returns."""

        pending = getattr(self, "_pending_auxiliary_cleanup", None)
        if pending is None:
            return False
        route_kind, old_lease = pending
        authority = self._get_action_authority()
        route_state = authority.auxiliary_route
        if route_state is None or route_state.lease.route_id != old_lease.route_id:
            self._pending_auxiliary_cleanup = None
            return False
        lease = authority.resume_auxiliary_route(old_lease)
        if lease is None:
            return True
        self._pending_auxiliary_cleanup = (route_kind, lease)
        expected_state = route_state.expected_state
        if route_kind == "daily_gem":
            log_action_intent(
                "Restoring the interrupted Daily Gem route",
                reason="collector-owned cleanup retained the verified Store route",
                detail=f"[AUXILIARY_ROUTE] route={lease.route_id} kind=daily_gem",
            )
            result = resume_daily_gem_cleanup(
                lease.source_state,
                action_guard_fn=self._auxiliary_action_guard(
                    AuxiliaryCollector.DAILY_GEM_STORE,
                    route=lease,
                ),
            )
            complete = result is DailyGemCleanupResult.COMPLETE
            abandoned = result is DailyGemCleanupResult.ABANDONED
        elif route_kind == "mission_rewards":
            log_action_intent(
                "Restoring the interrupted mission-reward route",
                reason="collector-owned cleanup retained the verified reward route",
                detail=(
                    f"[AUXILIARY_ROUTE] route={lease.route_id} "
                    "kind=mission_rewards"
                ),
            )
            result = resume_mission_reward_cleanup(
                lease.source_state,
                expected_state,
                action_guard_fn=self._auxiliary_action_guard(
                    lease.collectors[0],
                    route=lease,
                ),
            )
            complete = result is MissionRewardCleanupResult.COMPLETE
            abandoned = result is MissionRewardCleanupResult.ABANDONED
        else:
            authority.abandon_auxiliary_route(
                reason=f"unknown retained route kind {route_kind}"
            )
            self._pending_auxiliary_cleanup = None
            self._publish_action_authority()
            return True
        log_result(
            (
                "Auxiliary route cleanup complete"
                if complete
                else (
                    "Auxiliary route cleanup abandoned at an authoritative boundary"
                    if abandoned
                    else "Auxiliary route cleanup interrupted"
                )
            ),
            detail=(
                f"[AUXILIARY_ROUTE] result={result.value} "
                f"route={lease.route_id} kind={route_kind}"
            ),
        )
        if complete:
            self._release_auxiliary_route(
                lease,
                reason="verified auxiliary cleanup returned to the source UI",
            )
            if route_kind == "daily_gem":
                self._daily_gem_scheduler.mark_failed()
            else:
                self._mission_reward_scheduler.mark_failed(
                    wall_now=datetime.now(timezone.utc)
                )
        elif abandoned:
            authority.abandon_auxiliary_route(
                reason="a boundary or unexpected state superseded collector cleanup"
            )
            self._pending_auxiliary_cleanup = None
            self._publish_action_authority()
        else:
            authority.update_auxiliary_route(
                lease,
                expected_state=expected_state,
                cleanup_pending=True,
                suspended_reason=f"cleanup result={result.value}",
            )
            self._publish_action_authority()
        return True

    def _level_skip_priority_pending(
        self,
        *,
        initialization_pending: bool,
    ) -> bool:
        """Return whether Farm's urgent EHLS/EALS purchase still owns priority."""

        if not initialization_pending:
            return False
        mv = self._mission_mgr.ctx.data.get("mission_vars", {})
        level_skip_keys = {"ehls_completed", "eals_completed"}
        if not level_skip_keys.intersection(mv):
            return False
        return not all(bool(mv.get(key)) for key in level_skip_keys)

    def _game_speed_priority_ready(
        self,
        *,
        initialization_pending: bool,
    ) -> bool:
        """Keep Farm's urgent EHLS/EALS purchases ahead of game speed."""

        return not self._level_skip_priority_pending(
            initialization_pending=initialization_pending
        )

    def _game_speed_control_snapshot(self) -> Dict[str, Any]:
        """Return run-scoped speed-mode experiment metadata."""

        guard = getattr(self, "_game_speed_guard", None)
        return guard.snapshot() if guard is not None else {}

    def _no_strategy_action_guard(self) -> bool:
        """Synchronize control before one inventory action."""

        return self._runtime_action_guard()

    def _handle_no_strategy_in_battle_inventory(
        self,
        detection: Mapping[str, Any],
    ) -> bool:
        """Run the automatic read-only inventory as an exclusive route."""

        if getattr(self, "_pending_no_strategy_record", None) is not None:
            return False
        if self._current_strategy_name() != "none":
            return False
        if getattr(self, "_no_strategy_inventory_complete", False):
            return False
        if not getattr(self, "_no_strategy_observation_active", False):
            return False
        active_battle_observed = getattr(
            getattr(self, "_mission_mgr", None),
            "active_battle_observed",
            None,
        )
        if not callable(active_battle_observed):
            return False
        try:
            active_battle = active_battle_observed()
        except Exception:
            return False
        if active_battle is not True:
            return False
        state = str(detection.get("state") or "UNKNOWN")
        # This dispatcher grants a new synchronous inventory route.  Its
        # internal owner may traverse Cards, Perks, and the other supported
        # panels, but an arbitrary panel observed by the main loop is not
        # proof that automation opened it.  Requiring fresh RUNNING here keeps
        # operator navigation at Home or in a live battle from being mistaken
        # for cleanup authority.
        if state != "RUNNING":
            return False
        if self._supervisor.is_paused:
            return False
        if time.time() < getattr(self, "_no_strategy_inventory_retry_at", 0.0):
            return state != "RUNNING"
        if stop_blind_gem_tapper():
            self._blind_tapper_suspended = True

        operation_id = new_operation_id()
        log_action_intent(
            "Collecting unresolved No Strategy configuration",
            reason=(
                "record actual battle settings while visiting only fields not "
                "already resolved by guarded save or passive evidence"
            ),
            detail="[NO_STRATEGY] phase=in_battle_inventory status=pending",
            operation_id=operation_id,
            console=True,
        )
        result = run_no_strategy_in_battle_inventory(
            self._no_strategy_observer,
            control_sync=self._no_strategy_action_guard,
            actions_allowed=lambda: not self._supervisor.is_paused,
        )
        if result.status is NoStrategyInventoryStatus.COMPLETE:
            self._no_strategy_inventory_complete = True
            self._no_strategy_inventory_retry_at = 0.0
            log_result(
                "No Strategy in-battle inventory complete — " + result.reason,
                detail=(
                    "[NO_STRATEGY] phase=in_battle_inventory "
                    "status=complete"
                ),
                operation_id=operation_id,
                console=True,
            )
        elif result.status is NoStrategyInventoryStatus.PAUSED:
            self._no_strategy_inventory_retry_at = 0.0
            log_result(
                "No Strategy in-battle inventory interrupted by Pause — no "
                "cleanup input was sent",
                detail=(
                    "[NO_STRATEGY] phase=in_battle_inventory status=paused "
                    f"reason={result.reason}"
                ),
                operation_id=operation_id,
                console=True,
            )
        elif result.status is NoStrategyInventoryStatus.BATTLE_ENDED:
            self._no_strategy_inventory_retry_at = 0.0
            log_result(
                "No Strategy in-battle inventory ended at the natural battle "
                "boundary — Home evidence will continue there",
                detail=(
                    "[NO_STRATEGY] phase=in_battle_inventory "
                    f"status=battle_ended reason={result.reason}"
                ),
                operation_id=operation_id,
                console=True,
            )
        else:
            self._no_strategy_inventory_retry_at = time.time() + 60.0
            log_result(
                "No Strategy in-battle inventory failed safely — retry scheduled",
                detail=(
                    "[NO_STRATEGY] phase=in_battle_inventory status=failed "
                    f"reason={result.reason} retry_seconds=60"
                ),
                operation_id=operation_id,
                console=True,
            )
            log(
                f"[NO_STRATEGY] Automatic in-battle inventory failed: "
                f"{result.reason}. It will retry after 60 seconds.",
                "WARN",
                console=True,
            )
        return True

    @staticmethod
    def _no_strategy_fields_resolved(
        snapshot: object,
        fields: Sequence[str],
    ) -> bool:
        if not isinstance(snapshot, Mapping):
            return False
        observations = snapshot.get("fields")
        if not isinstance(observations, Mapping):
            return False
        resolved = {"observed", "evidence_captured", "unavailable"}
        return all(
            isinstance(observations.get(field), Mapping)
            and str(observations[field].get("status") or "") in resolved
            for field in fields
        )

    def _next_no_strategy_post_run_stage(
        self,
        snapshot: Optional[Mapping[str, Any]] = None,
    ) -> str:
        observed = (
            snapshot
            if snapshot is not None
            else self._no_strategy_observer.snapshot()
        )
        if not self._no_strategy_fields_resolved(
            observed,
            ("workshop_preset", "free_upgrade_locks"),
        ):
            return "locks"
        if not self._no_strategy_fields_resolved(
            observed,
            (
                "cards_deck",
                "perk_first_choice",
                "perk_bans",
                "perk_auto_pick_order",
            ),
        ):
            return "perks"
        return "finalize"

    def _persist_pending_no_strategy_record(self, *, finalized: bool) -> None:
        record = self._pending_no_strategy_record
        if record is None:
            return
        snapshot = self._no_strategy_observer.snapshot(finalized=finalized)
        attach_observed_run_configuration(record, snapshot)
        json_path, markdown_path = persist_battle_record(record)
        log(
            f"[NO_STRATEGY] Updated battle observation record: {json_path} "
            f"(view: {markdown_path})",
            "INFO",
            console=True,
        )

    def _finish_no_strategy_post_run(self) -> bool:
        """Persist final evidence, then release or retain the Home boundary."""

        self._persist_pending_no_strategy_record(finalized=True)
        if AUTOMATION.mode is ExecMode.WAIT:
            self._no_strategy_post_run_stage = "complete_wait"
            self._no_strategy_post_run_retry_at = 0.0
            log(
                "[NO_STRATEGY] Post-run inventory complete; WAIT is holding "
                "the verified Home boundary",
                "INFO",
                console=True,
            )
            return True
        self._release_no_strategy_post_run()
        log(
            "[NO_STRATEGY] Post-run inventory complete; the next-battle path "
            "is released",
            "INFO",
            console=True,
        )
        return True

    def _finish_incomplete_no_strategy_post_run(
        self,
        *,
        reason: str,
        new_state: str,
        img: Frame,
    ) -> bool:
        """Persist what exists and release Home after a noncritical failure."""

        persistence_reason = None
        try:
            self._persist_pending_no_strategy_record(finalized=True)
        except Exception as exc:
            persistence_reason = str(exc)
            log(
                "[NO_STRATEGY] Could not persist the incomplete post-run "
                f"observation ({exc}); continuity still takes precedence",
                "ERROR",
                console=True,
            )
        if new_state != "HOME_SCREEN":
            try:
                restore_post_run_home(
                    img,
                    action_guard_fn=self._no_strategy_action_guard,
                )
            except NoStrategyPostRunPaused:
                self._no_strategy_post_run_retry_at = 0.0
                return True
            except Exception as exc:
                self._no_strategy_post_run_retry_at = time.time() + 5.0
                log(
                    "[NO_STRATEGY] Incomplete inventory is waiting only for "
                    f"verified Home restoration ({exc}); Automation remains "
                    "Enabled and recovery will retry",
                    "WARN",
                    console=True,
                )
                return True

        detail = reason
        if persistence_reason:
            detail = f"{detail}; persistence={persistence_reason}"
        if AUTOMATION.mode is ExecMode.WAIT:
            self._no_strategy_post_run_stage = "complete_wait"
            self._no_strategy_post_run_retry_at = 0.0
            log(
                "[NO_STRATEGY] Post-run inventory ended incomplete at verified "
                f"Home ({detail}); explicit WAIT still holds the boundary",
                "WARN",
                console=True,
            )
            return True
        self._release_no_strategy_post_run()
        log(
            "[NO_STRATEGY] Post-run inventory ended incomplete at verified "
            f"Home ({detail}); the next-battle path was released",
            "WARN",
            console=True,
        )
        return True

    def _handle_no_strategy_post_run(
        self,
        new_state: str,
        img: Frame,
    ) -> bool:
        """Hold the no-battle boundary until Home-only evidence is recorded."""

        record = getattr(self, "_pending_no_strategy_record", None)
        if record is None:
            return False
        if time.time() < getattr(self, "_no_strategy_post_run_retry_at", 0.0):
            return True
        stage = getattr(self, "_no_strategy_post_run_stage", None)
        if stage == "complete_wait":
            if AUTOMATION.mode is ExecMode.WAIT:
                return True
            self._release_no_strategy_post_run()
            log(
                "[NO_STRATEGY] WAIT released; the next-battle path is available",
                "INFO",
                console=True,
            )
            return True
        try:
            if stage == "locks":
                if new_state != "HOME_SCREEN":
                    restore_post_run_home(
                        img,
                        action_guard_fn=self._no_strategy_action_guard,
                    )
                    return True
                lock_result = inspect_post_run_free_upgrade_locks(
                    img,
                    action_guard_fn=self._no_strategy_action_guard,
                )
                workshop_detection = detect_state_and_overlays(
                    lock_result.workshop_screenshot
                )
                self._no_strategy_observer.observe(
                    lock_result.workshop_screenshot,
                    workshop_detection,
                    phase="post_run_home",
                )
                self._no_strategy_observer.record_post_run_value(
                    "free_upgrade_locks",
                    lock_result.values,
                    source="home_workshop_lock_details",
                )
                self._persist_pending_no_strategy_record(finalized=False)
                current_snapshot = self._no_strategy_observer.snapshot()
                self._no_strategy_post_run_stage = (
                    "finalize"
                    if self._no_strategy_fields_resolved(
                        current_snapshot,
                        (
                            "cards_deck",
                            "perk_first_choice",
                            "perk_bans",
                            "perk_auto_pick_order",
                        ),
                    )
                    else "perks"
                )
                self._no_strategy_post_run_retry_at = 0.0
                stage = self._no_strategy_post_run_stage
                new_state = "HOME_SCREEN"
                img = lock_result.home_screenshot

            if stage == "finalize":
                if new_state != "HOME_SCREEN":
                    restore_post_run_home(
                        img,
                        action_guard_fn=self._no_strategy_action_guard,
                    )
                    return True
                return self._finish_no_strategy_post_run()

            if stage == "perks":
                if new_state == "HOME_SCREEN":
                    opened = open_perks_configuration_for_post_run_capture(
                        img,
                        action_guard_fn=self._no_strategy_action_guard,
                    )
                    self._no_strategy_observer.observe(
                        opened.cards_screenshot,
                        detect_state_and_overlays(opened.cards_screenshot),
                        phase="post_run_home",
                    )
                    img = opened.perks_screenshot
                elif new_state == "CARDS":
                    self._no_strategy_observer.observe(
                        img,
                        detect_state_and_overlays(img),
                        phase="post_run_home",
                    )
                    img = open_perks_configuration_from_cards(
                        img,
                        action_guard_fn=self._no_strategy_action_guard,
                    )
                elif new_state != "PERKS":
                    restore_post_run_home(
                        img,
                        action_guard_fn=self._no_strategy_action_guard,
                    )
                    return True
                capture = capture_post_run_perk_configuration(
                    img,
                    battle_id=str(record.get("battle_id") or "Battle"),
                    action_guard_fn=self._no_strategy_action_guard,
                )
                for field, value in capture.fields.items():
                    quality = value.get("quality") if isinstance(value, Mapping) else None
                    if isinstance(quality, Mapping) and quality.get("valid") is True:
                        self._no_strategy_observer.record_post_run_value(
                            field,
                            value,
                            source="home_perks_configuration_tabs",
                        )
                    else:
                        self._no_strategy_observer.record_post_run_evidence(
                            field,
                            value,
                            source="home_perks_configuration_tabs",
                        )
                return self._finish_no_strategy_post_run()
        except NoStrategyPostRunPaused:
            self._no_strategy_post_run_retry_at = 0.0
            log(
                "[NO_STRATEGY] Post-run inventory paused; the next-battle "
                "boundary remains held and capture will resume after RUNNING",
                "INFO",
                console=True,
            )
            return True
        except NoStrategyPostRunError as exc:
            return self._finish_incomplete_no_strategy_post_run(
                reason=str(exc),
                new_state=new_state,
                img=img,
            )
        except Exception as exc:
            return self._finish_incomplete_no_strategy_post_run(
                reason=f"unexpected failure: {exc}",
                new_state=new_state,
                img=img,
            )
        return True

    def _recover_no_strategy_post_run(
        self,
        new_state: str,
        img: Frame,
    ) -> None:
        """Recover an unfinished Home inventory after process replacement."""

        mission_mgr = getattr(self, "_mission_mgr", None)
        if (
            getattr(self, "_no_strategy_post_run_recovery_checked", False)
            or getattr(self, "_pending_no_strategy_record", None) is not None
            or mission_mgr is None
            or mission_mgr.strategy is not None
            or new_state != "HOME_SCREEN"
        ):
            return
        home_control = detect_home_battle_control(img)
        if home_control.control is not HomeBattleControl.NEW_BATTLE:
            return
        self._no_strategy_post_run_recovery_checked = True
        record = load_pending_no_strategy_record()
        if record is None:
            return
        observed = record.get("observed_run_configuration")
        try:
            self._no_strategy_observer.restore_snapshot(observed)
        except (TypeError, ValueError) as exc:
            log(
                f"[NO_STRATEGY] Could not restore unfinished post-run "
                f"inventory: {exc}",
                "ERROR",
                console=True,
            )
            return
        self._pending_no_strategy_record = record
        self._no_strategy_post_run_stage = self._next_no_strategy_post_run_stage(
            observed if isinstance(observed, Mapping) else None
        )
        self._no_strategy_post_run_retry_at = 0.0
        self._no_strategy_observation_active = True
        log(
            "[NO_STRATEGY] Recovered unfinished post-run inventory for "
            f"{record.get('battle_id')}",
            "INFO",
            console=True,
        )

    def _release_no_strategy_post_run(self) -> None:
        """Release a completed observation boundary and reset its collector."""

        self._pending_no_strategy_record = None
        self._no_strategy_post_run_stage = None
        self._no_strategy_post_run_retry_at = 0.0
        self._no_strategy_observation_active = False
        self._no_strategy_attachment_boundary_id = None
        self._no_strategy_inventory_complete = False
        self._no_strategy_inventory_retry_at = 0.0
        self._no_strategy_observer.reset()

    def _advance_pending_game_over_route_recovery(
        self,
        new_state: str,
        img: Frame,
    ) -> bool:
        """Recover an optional-collection modal before retrying Home/Retry."""

        pending = getattr(self, "_pending_game_over_route", None)
        if not isinstance(pending, dict):
            return False
        if new_state == "GAME_OVER":
            return False
        if new_state not in {"PERKS", "UNKNOWN"}:
            self._pending_game_over_route = None
            return False
        try:
            current = self._current_control_workflow_evidence()
        except Exception:
            current = None
        expected = pending.get("binding")
        binding_fields = (
            "runtime_id",
            "pid",
            "adb_target",
            "target_generation",
            "activity_scope_run_id",
        )
        if not (
            isinstance(expected, Mapping)
            and isinstance(current, Mapping)
            and all(
                expected.get(field) == current.get(field)
                for field in binding_fields
            )
        ):
            self._pending_game_over_route = None
            log(
                "[GAME_OVER] Discarded pending terminal-screen recovery after "
                "its exact runtime or battle binding changed",
                "WARN",
            )
            return False
        now = time.monotonic()
        if now < float(pending.get("retry_at") or 0.0):
            return True
        operation_id = new_operation_id()
        owner = getattr(self, "_active_action_authority_owner", None)

        def action_allowed() -> bool:
            return self._runtime_action_guard(
                action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                owner=owner,
            )

        log_action_intent(
            "Restoring Game Stats for the pending terminal route",
            reason=(
                "optional data collection left a verified Perks or More Stats "
                "screen in front of Home/Retry"
            ),
            detail=(
                "[GAME_OVER_RECOVERY] route="
                f"{pending.get('desired_route') or 'selected_policy'}"
            ),
            operation_id=operation_id,
        )
        restored = restore_game_stats_for_terminal_route(
            img,
            action_guard_fn=action_allowed,
        )
        if restored:
            pending["retry_at"] = 0.0
            result = "completed"
            message = (
                "Game Stats restored; Home/Retry will use a fresh observation"
            )
        else:
            pending["retry_at"] = now + 5.0
            result = (
                "failed"
                if self._supervisor.control_state == "RUNNING"
                else "interrupted"
            )
            message = (
                "Game Stats recovery remains pending; automation authority and "
                "the selected terminal policy were preserved"
            )
        log_result(
            message,
            detail=(
                f"[GAME_OVER_RECOVERY] result={result} retry="
                f"{'false' if restored else 'true'}"
            ),
            operation_id=operation_id,
        )
        # Whether recovery succeeded or yielded, the supplied frame predates
        # that bounded attempt. Never run another handler against it.
        return True

    def _handle_primary_states(
        self,
        new_state: str,
        overlays: Set[str],
        img: Frame,
        *,
        operator_workflow_only: bool = False,
    ) -> None:
        """Dispatch handlers for top-level UI states and overlay-driven events."""
        selector = getattr(self, "_run_perk_selector", None)
        if selector is not None:
            selector.observe_state(new_state)
        if self._advance_pending_game_over_route_recovery(new_state, img):
            return
        if new_state != "HOME_SCREEN":
            self._last_home_policy_signature = None
        if not operator_workflow_only:
            self._recover_no_strategy_post_run(new_state, img)
            if self._handle_no_strategy_post_run(new_state, img):
                return
        if new_state == "RUNNING":
            self._tournament_results_captured = False
            self._tournament_terminal_continuation_bound = False
            self._tournament_terminal_continuation_claim = None
            self._tournament_degradation_snapshot = None
        if (
            not operator_workflow_only
            and self._handler_enabled("daily_gem")
            and self._handle_daily_gem_if_due(new_state, overlays)
        ):
            # The handler navigates through Store and may return to a different
            # screen. Do not act on the stale pre-handler detection this tick.
            return
        if (
            not operator_workflow_only
            and self._handler_enabled("mission_rewards")
            and self._handle_mission_rewards_if_due(new_state, img, overlays)
        ):
            # The handler traverses several panels and restores RUNNING. Avoid
            # dispatching against the frame captured before that navigation.
            return
        if (
            new_state == "HOME_SCREEN"
            and "HOME_AD_GEMS_AVAILABLE" in overlays
            and self._handler_enabled("ad_gem")
            and self._action_decision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                collector=AuxiliaryCollector.HOME_AD_GEM,
            ).allowed
        ):
            scheduled_epoch = self._home_ad_gem_circuit_epoch()
            if self._home_ad_gem_circuit_allows(epoch=scheduled_epoch):
                # Collect before Home handling can start or resume a battle. The
                # handler revalidates both the visible control and typed
                # authority at the final dispatch boundary, so this overlay is
                # scheduling evidence rather than tap authority.
                outcome = handle_home_ad_gem(
                    action_guard_fn=self._auxiliary_action_guard(
                        AuxiliaryCollector.HOME_AD_GEM
                    )
                )
                self._record_home_ad_gem_outcome(
                    outcome,
                    epoch=scheduled_epoch,
                )
                return

        if (
            new_state == "TOURNAMENT_RESULTS"
            and self._handler_enabled("game_over")
        ):
            terminal_policy = AUTOMATION.mode.value
            if not getattr(
                self,
                "_tournament_terminal_continuation_bound",
                False,
            ):
                degradation_snapshot = None
                if AUTOMATION.mode is ExecMode.NEXT_BATTLE:
                    degradation_fn = getattr(
                        self._mission_mgr,
                        "running_configuration_degradation",
                        None,
                    )
                    candidate = (
                        degradation_fn()
                        if callable(degradation_fn)
                        else None
                    )
                    if self._degradation_requires_home_repair(candidate):
                        degradation_snapshot = dict(candidate)
                        log(
                            "[RUNTIME_POLICY] Continue automatically will "
                            "repair degraded Tournament configuration at Home",
                            "INFO",
                            console=True,
                        )
                self._tournament_degradation_snapshot = degradation_snapshot
                self._tournament_terminal_continuation_claim = (
                    self._build_terminal_home_continuation_claim(
                        source="tournament_results"
                    )
                )
                self._tournament_terminal_continuation_bound = True
            terminal_continuation_claim = getattr(
                self,
                "_tournament_terminal_continuation_claim",
                None,
            )
            if (
                getattr(self, "_tournament_results_captured", False)
                and terminal_policy == ExecMode.WAIT.value
            ):
                return
            operation_id = new_operation_id()
            record = None
            if not getattr(self, "_tournament_results_captured", False):
                log_action_intent(
                    "Capturing the finished Tournament",
                    reason=(
                        "preserve its result before following the selected "
                        "post-terminal policy"
                    ),
                    detail=(
                        "[TOURNAMENT_RESULTS] result=pending "
                        f"terminal_policy={terminal_policy} screen=retained"
                    ),
                    operation_id=operation_id,
                )
                log(
                    "Detected TOURNAMENT_RESULTS. Capturing result before dismissal.",
                    "INFO",
                    console=True,
                )
                tournament_observed_configuration = (
                    self._no_strategy_observer.snapshot(finalized=True)
                    if self._mission_mgr.strategy is None
                    and getattr(
                        self,
                        "_no_strategy_observation_active",
                        False,
                    )
                    else None
                )
                (
                    tournament_context,
                    _tournament_acquisition,
                    tournament_mapping_observer,
                ) = self._terminal_battle_bundle(
                    "TOURNAMENT_RESULTS",
                    observed_run_configuration=(
                        tournament_observed_configuration
                    ),
                )
                record = handle_tournament_results(
                    img,
                    battle_context=tournament_context,
                    mapping_observation_fn=(
                        tournament_mapping_observer.record_mapping_observation
                        if tournament_mapping_observer is not None
                        else None
                    ),
                    action_guard_fn=lambda: self._runtime_action_guard(
                        action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                    ),
                )
                if record is None:
                    log_result(
                        "Tournament result capture failed — Tournament Results "
                        f"remains visible (policy {terminal_policy} preserved) "
                        "and capture will retry",
                        detail=(
                            "[TOURNAMENT_RESULTS] result=failed "
                            f"terminal_policy={terminal_policy} screen=retained "
                            "retry=true"
                        ),
                        operation_id=operation_id,
                    )
                    return
                self._tournament_results_captured = True
                if tournament_observed_configuration is not None:
                    self._end_no_strategy_observation_boundary()
                self._mission_mgr.on_game_over()
                self._status_reporter.reset_coin_rate_samples()
                self._strategy_boundary_confirmed = True
                self._apply_pending_strategy()
                degradation_snapshot = getattr(
                    self,
                    "_tournament_degradation_snapshot",
                    None,
                )
                if (
                    isinstance(degradation_snapshot, Mapping)
                    and self._mission_mgr.strategy is not None
                ):
                    prepared = self._mission_mgr.prepare_degraded_home_repair(
                        degradation_snapshot
                    )
                    if not prepared:
                        self._flag_recoverable_runtime_failure(
                            RuntimeFailureKind.REPORTING_FAILURE,
                            "degraded Tournament Home repair state could not "
                            "be prepared",
                        )
                if terminal_policy == ExecMode.WAIT.value:
                    log_result(
                        "Tournament finished — result saved; Tournament Results "
                        "remains visible under the explicit wait policy",
                        detail=(
                            "[TOURNAMENT_RESULTS] result=completed "
                            f"tournament_id={record.get('tournament_id')} "
                            f"terminal_policy={terminal_policy} screen=retained"
                        ),
                        operation_id=operation_id,
                    )
                    return
            else:
                log_action_intent(
                    "Following the finished Tournament direction",
                    reason=(
                        "the Tournament result is already saved and the selected "
                        f"post-terminal policy is {terminal_policy}"
                    ),
                    detail=(
                        "[TOURNAMENT_RESULTS] result=pending "
                        f"terminal_policy={terminal_policy} screen=retained"
                    ),
                    operation_id=operation_id,
                )

            if terminal_policy == ExecMode.WAIT.value:
                log_result(
                    "Tournament Results retained under the explicit wait policy",
                    detail=(
                        "[TOURNAMENT_RESULTS] result=completed "
                        f"terminal_policy={terminal_policy} screen=retained"
                    ),
                    operation_id=operation_id,
                )
                return

            dismissed = dismiss_tournament_results_to_home(
                action_guard_fn=lambda: self._runtime_action_guard(
                    action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                )
            )
            if not dismissed:
                log_result(
                    "Tournament result was saved, but verified Home was not "
                    "reached; the same terminal route will retry without "
                    "changing Automation authority",
                    detail=(
                        "[TOURNAMENT_RESULTS] result=pending_retry "
                        f"terminal_policy={terminal_policy} screen=retained "
                        f"action_authority={AUTOMATION.state.value} retry=true"
                    ),
                    operation_id=operation_id,
                )
                return
            log_result(
                "Tournament result saved and verified Home reached; the "
                "selected future battle policy remains separate",
                detail=(
                    "[TOURNAMENT_RESULTS] result=completed "
                    f"terminal_policy={terminal_policy} screen=home_new_battle"
                ),
                operation_id=operation_id,
            )
            self._commit_terminal_home_continuation(
                terminal_continuation_claim
            )
            self._tournament_terminal_continuation_claim = None
            self._tournament_terminal_continuation_bound = False
            self._tournament_degradation_snapshot = None
            return

        if new_state == "GAME_OVER" and self._handler_enabled("game_over"):
            emulator_fallback = bool(
                getattr(self, "_emulator_maintenance_hold_active", False)
                and getattr(
                    self,
                    "_emulator_recovery_force_new_battle",
                    False,
                )
            )
            manual = self._supervisor.manual_control
            manual_terminal = (
                manual.get("terminal_evidence")
                if isinstance(manual, Mapping)
                and isinstance(manual.get("terminal_evidence"), Mapping)
                else None
            )
            if (
                isinstance(manual, Mapping)
                and manual.get("status") == "completed"
                and isinstance(manual_terminal, Mapping)
                and isinstance(manual.get("save_receipt"), Mapping)
                and manual_terminal.get("receipt")
                == manual.get("save_receipt")
            ):
                # The terminal handler already ran before this durable Return
                # acknowledgement.  WAIT may intentionally leave Game Over on
                # screen; never replay collection or navigation from it.
                return
            current_manual_evidence = self._current_control_workflow_evidence()
            owned_development_terminal_claim = (
                self._matching_interactive_development_owned_terminal_claim(
                    current_manual_evidence
                )
            )
            manual_terminal_claim = (
                self._matching_manual_terminal_claim(
                    manual,
                    current_manual_evidence,
                )
                if isinstance(manual, Mapping)
                and isinstance(current_manual_evidence, Mapping)
                else None
            )
            save_backed_manual_return = bool(
                operator_workflow_only
                and isinstance(manual, Mapping)
                and manual.get("status") == "reconciling"
                and isinstance(manual_terminal, Mapping)
                and manual_terminal.get("status")
                in {"confirmed_surrender", "confirmed_other"}
                and isinstance(manual_terminal.get("receipt"), Mapping)
                and isinstance(manual_terminal_claim, Mapping)
            )
            ui_backed_manual_return = bool(
                operator_workflow_only
                and isinstance(manual, Mapping)
                and manual.get("status") == "reconciling"
                and isinstance(manual_terminal, Mapping)
                and manual_terminal.get("status") == "unavailable"
                and isinstance(current_manual_evidence, Mapping)
                and current_manual_evidence.get("game_state") == "game_over"
            )
            manual_return = bool(
                save_backed_manual_return or ui_backed_manual_return
            )
            preserved_terminal_recovery = bool(
                operator_workflow_only
                and not manual_return
                and self._preserved_game_over_recovery_allowed(
                    new_state,
                    owner=getattr(
                        self,
                        "_active_action_authority_owner",
                        None,
                    ),
                )
            )
            if (
                operator_workflow_only
                and not manual_return
                and not preserved_terminal_recovery
            ):
                log(
                    "[MANUAL_CONTROL] Return Control reached Game Over without "
                    "a safe save or UI fallback boundary; terminal input remains "
                    "blocked without changing automation authority",
                    "WARN",
                    console=True,
                )
                return
            if preserved_terminal_recovery:
                log(
                    "[GAME_OVER] Recovering the fresh preserved terminal under "
                    "the explicit WAIT policy without attaching stale run state",
                    "INFO",
                    console=True,
                )
            if ui_backed_manual_return:
                log(
                    "[MANUAL_CONTROL] Terminal save evidence is unavailable; "
                    "using the supported Game Stats/Perks/More Stats UI route",
                    "INFO",
                    console=True,
                )
            if owned_development_terminal_claim is not None:
                log(
                    "[INTERACTIVE_DEVELOPMENT] The exact preclaimed owned "
                    "battle reached Game Over; the supported minimal terminal "
                    "route will return Home",
                    "INFO",
                    console=True,
                )
            log("Detected GAME_OVER. Executing handler.", "INFO", console=True)
            strategy = self._mission_mgr.strategy
            no_strategy_run = strategy is None
            observed_run_configuration = (
                self._no_strategy_observer.snapshot()
                if no_strategy_run
                else None
            )
            repair_in_progress = (
                self._mission_mgr.session_preflight_repair_in_progress()
            )
            pending_terminal_route = getattr(
                self,
                "_pending_game_over_route",
                None,
            )
            if isinstance(pending_terminal_route, Mapping):
                expected_binding = pending_terminal_route.get("binding")
                binding_fields = (
                    "runtime_id",
                    "pid",
                    "adb_target",
                    "target_generation",
                    "activity_scope_run_id",
                )
                if not (
                    isinstance(expected_binding, Mapping)
                    and isinstance(current_manual_evidence, Mapping)
                    and all(
                        expected_binding.get(field)
                        == current_manual_evidence.get(field)
                        for field in binding_fields
                    )
                ):
                    self._pending_game_over_route = None
                    pending_terminal_route = None
            degradation_snapshot = None
            if isinstance(pending_terminal_route, Mapping):
                raw_degradation = pending_terminal_route.get(
                    "degraded_home_repair"
                )
                if isinstance(raw_degradation, Mapping):
                    degradation_snapshot = dict(raw_degradation)
            elif AUTOMATION.mode is ExecMode.NEXT_BATTLE:
                degradation_fn = getattr(
                    self._mission_mgr,
                    "running_configuration_degradation",
                    None,
                )
                candidate = (
                    degradation_fn() if callable(degradation_fn) else None
                )
                if self._degradation_requires_home_repair(candidate):
                    degradation_snapshot = dict(candidate)
                    log(
                        "[RUNTIME_POLICY] Continue automatically will return "
                        "Home before the next battle to repair degraded "
                        "configuration",
                        "INFO",
                        console=True,
                    )
            degraded_home_repair = isinstance(
                degradation_snapshot,
                Mapping,
            )
            if isinstance(pending_terminal_route, Mapping):
                raw_terminal_continuation = pending_terminal_route.get(
                    "terminal_home_continuation"
                )
                terminal_continuation_claim = (
                    dict(raw_terminal_continuation)
                    if isinstance(raw_terminal_continuation, Mapping)
                    else None
                )
            elif degraded_home_repair:
                terminal_continuation_claim = (
                    self._build_terminal_home_continuation_claim(
                        source="degraded_battle_repair",
                        evidence=current_manual_evidence,
                    )
                )
            elif not manual_return and (repair_in_progress or no_strategy_run):
                terminal_continuation_claim = (
                    self._build_terminal_home_continuation_claim(
                        source=(
                            "session_preflight_repair"
                            if repair_in_progress
                            else "no_strategy_post_run"
                        ),
                        evidence=current_manual_evidence,
                    )
                )
            else:
                terminal_continuation_claim = None
            boundary_finalized = bool(
                isinstance(pending_terminal_route, Mapping)
                and pending_terminal_route.get("boundary_finalized")
            )

            def finalize_run_boundary() -> None:
                nonlocal boundary_finalized
                if boundary_finalized:
                    return
                self._mission_mgr.on_game_over()
                self._status_reporter.reset_coin_rate_samples()
                self._strategy_boundary_confirmed = True
                self._apply_pending_strategy()
                if (
                    degraded_home_repair
                    and self._mission_mgr.strategy is not None
                ):
                    prepared = self._mission_mgr.prepare_degraded_home_repair(
                        degradation_snapshot
                    )
                    if not prepared:
                        self._flag_recoverable_runtime_failure(
                            RuntimeFailureKind.REPORTING_FAILURE,
                            "degraded Home repair state could not be prepared",
                        )
                boundary_finalized = True

            def sync_terminal_control() -> None:
                self._supervisor.apply_control()
                self._observe_strategy_request()

            def mark_retry_started() -> None:
                retry_scope = start_retry_activity_scope()
                if isinstance(retry_scope, Mapping):
                    self._accept_pending_terminal_history_handoff()
                    run_binding = (
                        terminal_battle_context.get("run_binding")
                        if isinstance(terminal_battle_context, Mapping)
                        else None
                    )
                    source_scope_id = (
                        str(run_binding.get("activity_scope_run_id") or "")
                        if isinstance(run_binding, Mapping)
                        and run_binding.get("status") == "bound"
                        else ""
                    )
                    staged_retry_save = False
                    if not manual_return and source_scope_id:
                        staged_retry_save = (
                            self._stage_direct_retry_player_save_preflight(
                                terminal_acquisition,
                                source_activity_scope_id=source_scope_id,
                                retry_scope=retry_scope,
                            )
                        )
                    if not staged_retry_save:
                        coordinator = getattr(
                            self,
                            "_player_save_preflight_coordinator",
                            None,
                        )
                        if coordinator is not None:
                            coordinator.discard_carry(
                                "direct_retry_source_boundary_unverified"
                            )
                        self._mission_mgr.ctx.data.pop(
                            "player_save_preflight_coordinator",
                            None,
                        )

            terminal_acquisition = None
            terminal_mapping_observer = None
            if save_backed_manual_return:
                terminal_battle_context = dict(
                    manual_terminal_claim["context"]
                )
                terminal_acquisition = manual_terminal_claim.get("acquisition")
            else:
                (
                    terminal_battle_context,
                    terminal_acquisition,
                    terminal_mapping_observer,
                ) = self._terminal_battle_bundle(
                    "GAME_OVER",
                    observed_run_configuration=observed_run_configuration,
                )
            repair_terminal_failure_reason = (
                str(
                    pending_terminal_route.get("repair_failure_reason") or ""
                )
                if isinstance(pending_terminal_route, Mapping)
                else ""
            ) or None
            if repair_in_progress and repair_terminal_failure_reason:
                # The save/record attempt already failed in this process. Keep
                # only the bounded terminal route pending; never repeat data
                # work merely because its Home/Retry receipt is still pending.
                repair_in_progress = False
            if repair_in_progress:
                repair_grant = (
                    self._mission_mgr.session_preflight_repair_grant()
                )
                current_terminal = self._current_control_workflow_evidence()
                boundary = (
                    terminal_acquisition.boundary
                    if isinstance(
                        terminal_acquisition,
                        PlayerSaveAcquisitionBundle,
                    )
                    else None
                )
                repair_binding_valid = bool(
                    isinstance(repair_grant, Mapping)
                    and isinstance(current_terminal, Mapping)
                    and current_terminal.get("game_state") == "game_over"
                    and all(
                        repair_grant.get(field) == current_terminal.get(field)
                        for field in (
                            "runtime_id",
                            "pid",
                            "adb_target",
                            "target_generation",
                            "activity_scope_run_id",
                        )
                    )
                    and isinstance(
                        terminal_acquisition,
                        PlayerSaveAcquisitionBundle,
                    )
                    and terminal_acquisition.complete
                    and terminal_acquisition.binding
                    == PlayerSaveTargetBinding(
                        str(current_terminal.get("adb_target") or ""),
                        int(current_terminal.get("target_generation")),
                    )
                    and isinstance(boundary, PlayerSaveNaturalBoundary)
                    and boundary.kind is PlayerSaveBoundaryKind.GAME_OVER
                    and boundary.activity_scope_id
                    == str(current_terminal.get("activity_scope_run_id") or "")
                    and boundary.runtime_session_id
                    == str(
                        getattr(
                            self,
                            "_player_save_runtime_session_id",
                            "",
                        )
                        or ""
                    )
                )
                try:
                    if not repair_binding_valid:
                        raise ValueError(
                            "repair terminal save does not match the exact grant"
                        )
                    repair_record = self._persist_minimal_surrender_record(
                        terminal_battle_context,
                        terminal_acquisition,
                        initiator="automation_config_repair",
                        disposition_provenance={
                            "acquisition": (
                                terminal_acquisition.redacted_provenance()
                            ),
                            "repair_request_id": str(
                                repair_grant.get("request_id") or ""
                            ),
                            "check_id": str(
                                repair_grant.get("check_id") or ""
                            ),
                            "reason": str(repair_grant.get("reason") or ""),
                        },
                    )
                except (OSError, TypeError, ValueError) as exc:
                    reason = f"repair Surrender record failed: {exc}"
                    repair_terminal_failure_reason = reason
                    repair_in_progress = False
                    log(
                        "[SESSION_PREFLIGHT] Repair data failed, but the "
                        "selected terminal policy remains actionable — "
                        f"{reason}",
                        "ERROR",
                        console=True,
                    )
                else:
                    log(
                        "[SESSION_PREFLIGHT] Repair Surrender was retained as "
                        f"non-representative battle {repair_record.get('battle_id')}; "
                        "verifying the return Home",
                        "INFO",
                        console=True,
                    )
                    finalize_run_boundary()
                    returned_home = return_home_from_game_over(
                        timeout_s=8.0,
                        action_guard=lambda: bool(
                            self._mission_mgr.session_preflight_repair_authorized_for(
                                repair_grant
                            )
                            and self._runtime_action_guard(
                                action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                                owner=AuthorityHold.SESSION_PREFLIGHT,
                            )
                        ),
                    )
                    if not returned_home:
                        binding = (
                            {
                                field: current_terminal.get(field)
                                for field in (
                                    "runtime_id",
                                    "pid",
                                    "adb_target",
                                    "target_generation",
                                    "activity_scope_run_id",
                                )
                            }
                            if isinstance(current_terminal, Mapping)
                            else None
                        )
                        self._pending_game_over_route = {
                            "binding": binding,
                            "desired_route": "home",
                            "record": None,
                            "stats_status": "skipped",
                            "boundary_finalized": boundary_finalized,
                            "terminal_home_continuation": (
                                copy.deepcopy(terminal_continuation_claim)
                            ),
                            "degraded_home_repair": copy.deepcopy(
                                degradation_snapshot
                            ),
                            "retry_at": 0.0,
                        }
                        log(
                            "[SESSION_PREFLIGHT] Verified Home was not reached; "
                            "the repair terminal route will retry without "
                            "changing automation authority",
                            "ERROR",
                            console=True,
                        )
                        return
                    self._pending_game_over_route = None
                    log(
                        "[SESSION_PREFLIGHT] Repair Surrender returned to "
                        "verified Home; normal Home repair and the selected "
                        "future-battle policy remain Enabled",
                        "INFO",
                        console=True,
                    )
                    self._commit_terminal_home_continuation(
                        terminal_continuation_claim
                    )
                    return
            manual_full_disposition = None
            if (
                save_backed_manual_return
                and manual_terminal.get("status") == "confirmed_surrender"
                and str(
                    manual.get("surrender_collection") or "minimal"
                ).lower()
                == "full"
            ):
                manual_full_disposition = {
                    "schema_version": 1,
                    "outcome": "surrendered",
                    "initiator": "operator_manual_control",
                    "collection": "full_terminal_ui",
                    "representative": False,
                    "analytics": "excluded",
                    "history": "excluded_by_default",
                    "reason": (
                        "operator opted into full collection for a "
                        "save-confirmed manual Surrender"
                    ),
                    "provenance": copy.deepcopy(
                        dict(manual_terminal.get("receipt") or {})
                    ),
                }
            emulator_fallback_disposition = (
                {
                    "schema_version": 1,
                    "outcome": "interrupted",
                    "initiator": (
                        "operator_emulator_recovery"
                        if isinstance(
                            self._emulator_replay_window,
                            RestartReplayWindow,
                        )
                        and self._emulator_replay_window.request_initiator
                        == "operator"
                        else "automatic_emulator_recovery"
                    ),
                    "collection": "full_terminal_ui",
                    "representative": False,
                    "analytics": "excluded",
                    "history": "excluded_by_default",
                    "reason": (
                        "Welcome Back could not resume the old run; the "
                        "operator-selected recovery policy requires a new battle"
                    ),
                    "provenance": {
                        "maintenance_request_id": str(
                            getattr(
                                self,
                                "_emulator_recovery_request_id",
                                "",
                            )
                            or ""
                        )
                    },
                }
                if emulator_fallback
                else None
            )
            owned_development_disposition = (
                {
                    "schema_version": 1,
                    "outcome": "interrupted",
                    "initiator": "interactive_development_owned_battle",
                    "collection": "minimal_terminal_save",
                    "representative": False,
                    "analytics": "excluded",
                    "history": "excluded_by_default",
                    "reason": (
                        "an explicitly preclaimed development battle reached "
                        "its authorized terminal cleanup"
                    ),
                    "provenance": {
                        "lease_id": str(
                            owned_development_terminal_claim.get("lease_id")
                            or ""
                        ),
                    },
                }
                if owned_development_terminal_claim is not None
                else None
            )
            terminal_outcome = handle_game_over(
                capture_stats=(
                    not isinstance(pending_terminal_route, Mapping)
                    and repair_terminal_failure_reason is None
                    and not repair_in_progress
                    and owned_development_terminal_claim is None
                    and (
                        (
                            manual_return
                            and not (
                                manual_terminal.get("status")
                                == "confirmed_surrender"
                                and str(
                                    manual.get("surrender_collection")
                                    or "minimal"
                                ).lower()
                                == "minimal"
                            )
                        )
                        or (
                            not manual_return
                            and (
                                not self._fast_game_over
                                or no_strategy_run
                                or emulator_fallback
                            )
                        )
                    )
                ),
                control_sync=sync_terminal_control,
                before_terminal_action=finalize_run_boundary,
                after_retry_started=mark_retry_started,
                on_terminal_failure=lambda _step: True,
                action_guard_fn=lambda: self._runtime_action_guard(
                    action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                    owner=getattr(
                        self,
                        "_active_action_authority_owner",
                        None,
                    ),
                ),
                return_home_after_battle=(
                    repair_in_progress
                    or no_strategy_run
                    or degraded_home_repair
                    or emulator_fallback
                    or owned_development_terminal_claim is not None
                ),
                battle_context=terminal_battle_context,
                report_disposition=(
                    manual_full_disposition
                    or emulator_fallback_disposition
                    or owned_development_disposition
                ),
                captured_at=(
                    terminal_acquisition.captured_at
                    if manual_return
                    and isinstance(
                        terminal_acquisition,
                        PlayerSaveAcquisitionBundle,
                    )
                    else None
                ),
                battle_id=(
                    make_battle_id(terminal_acquisition.captured_at)
                    if manual_return
                    and isinstance(
                        terminal_acquisition,
                        PlayerSaveAcquisitionBundle,
                    )
                    else None
                ),
                mapping_observation_fn=(
                    terminal_mapping_observer.record_mapping_observation
                    if terminal_mapping_observer is not None
                    else None
                ),
            )
            if not isinstance(terminal_outcome, GameOverHandlingOutcome):
                # Keep old in-process test/extension doubles compatible while
                # the runtime itself uses the typed route/data separation.
                terminal_outcome = GameOverHandlingOutcome(
                    True,
                    "legacy",
                    (
                        terminal_outcome
                        if isinstance(terminal_outcome, dict)
                        else None
                    ),
                    (
                        "saved"
                        if isinstance(terminal_outcome, dict)
                        else "unavailable"
                    ),
                )
            completed_record = terminal_outcome.record
            if (
                completed_record is None
                and isinstance(pending_terminal_route, Mapping)
                and isinstance(pending_terminal_route.get("record"), dict)
            ):
                completed_record = pending_terminal_route.get("record")
            if not terminal_outcome.route_completed:
                # Keep the selected terminal policy and existing control
                # authority. A later fresh frame retries the bounded terminal
                # route; optional collection failure must not strand gems or
                # future battles behind a global Pause.
                binding = (
                    {
                        field: current_manual_evidence.get(field)
                        for field in (
                            "runtime_id",
                            "pid",
                            "adb_target",
                            "target_generation",
                            "activity_scope_run_id",
                        )
                    }
                    if isinstance(current_manual_evidence, Mapping)
                    else None
                )
                self._pending_game_over_route = {
                    "binding": binding,
                    "desired_route": (
                        "home"
                        if repair_in_progress
                        or no_strategy_run
                        or degraded_home_repair
                        or emulator_fallback
                        or owned_development_terminal_claim is not None
                        or AUTOMATION.mode is ExecMode.HOME
                        else "retry"
                    ),
                    "record": completed_record,
                    "stats_status": terminal_outcome.stats_status,
                    "boundary_finalized": boundary_finalized,
                    "repair_failure_reason": repair_terminal_failure_reason,
                    "terminal_home_continuation": (
                        copy.deepcopy(terminal_continuation_claim)
                    ),
                    "degraded_home_repair": copy.deepcopy(
                        degradation_snapshot
                    ),
                    "retry_at": 0.0,
                }
                return
            self._pending_game_over_route = None
            if terminal_outcome.route == "home":
                self._commit_terminal_home_continuation(
                    terminal_continuation_claim
                )
                if degraded_home_repair:
                    log(
                        "[RUNTIME_POLICY] Degraded battle returned Home; "
                        "normal profile setup will repair before automatic "
                        "continuation",
                        "INFO",
                        console=True,
                    )
                if owned_development_terminal_claim is not None:
                    log_result(
                        "Owned development battle cleanup reached verified Home",
                        detail=(
                            "[INTERACTIVE_DEVELOPMENT] "
                            "disposition=owned_battle_home_complete "
                            f"lease_id={owned_development_terminal_claim.get('lease_id')}"
                        ),
                    )
            if repair_terminal_failure_reason is not None:
                self._mission_mgr.fail_session_preflight_repair(
                    repair_terminal_failure_reason
                )
                log(
                    "[SESSION_PREFLIGHT] Repair data failed; the terminal "
                    "route completed under the selected policy and Automation "
                    "remains Enabled in degraded strategy mode",
                    "WARN",
                    console=True,
                )
            if manual_return:
                manual_id = str(manual.get("manual_control_id") or "")
                if ui_backed_manual_return:
                    killed_by = self._terminal_record_killed_by(completed_record)
                    if not killed_by:
                        self._supervisor.transition_manual_control(
                            manual_id,
                            "failed",
                            detail=(
                                "the save was unusable and terminal UI discovery "
                                "could not produce a bound outcome; the selected "
                                "terminal route still completed and automation "
                                "authority was preserved"
                            ),
                            refresh_status="terminal_ui_collection_unavailable",
                        )
                    else:
                        try:
                            ui_receipt = (
                                build_terminal_ui_reconciliation_receipt(
                                    workflow_id=manual_id,
                                    observation_id=str(
                                        current_manual_evidence.get(
                                            "observation_id"
                                        )
                                        or ""
                                    ),
                                    evidence=current_manual_evidence,
                                    killed_by=killed_by,
                                    reason=str(
                                        manual_terminal.get("reason")
                                        or "terminal_save_unavailable"
                                    ),
                                )
                            )
                        except (TypeError, ValueError) as exc:
                            self._supervisor.transition_manual_control(
                                manual_id,
                                "failed",
                                detail=(
                                    "terminal UI discovery completed, but its "
                                    f"bound receipt was rejected: {exc}; automation "
                                    "authority was preserved"
                                ),
                                refresh_status="terminal_ui_receipt_rejected",
                            )
                        else:
                            completion_configuration = {
                                "schema_version": 1,
                                "terminal_status": (
                                    "confirmed_surrender"
                                    if ui_receipt["terminal"]["surrendered"]
                                    else "confirmed_other"
                                ),
                                "collection": "full_ui_fallback",
                                "battle_id": (
                                    completed_record.get("battle_id")
                                    if isinstance(completed_record, Mapping)
                                    else None
                                ),
                            }
                            completion_payload = {
                                "detail": (
                                    "terminal save evidence was unusable; the "
                                    "supported UI collector reconciled the outcome"
                                ),
                                "refresh_status": (
                                    "terminal_ui_fallback_reconciliation_complete"
                                ),
                                "save_receipt": ui_receipt,
                                "configuration": completion_configuration,
                            }
                            self._manual_terminal_claims()[manual_id] = {
                                "receipt": copy.deepcopy(ui_receipt),
                                "evidence": dict(current_manual_evidence),
                                "ui_fallback": True,
                                "semantic_completion_applied": True,
                                "pending_completion": copy.deepcopy(
                                    completion_payload
                                ),
                            }
                            completed_manual = (
                                self._supervisor.transition_manual_control(
                                    manual_id,
                                    "completed",
                                    detail=str(completion_payload["detail"]),
                                    refresh_status=str(
                                        completion_payload["refresh_status"]
                                    ),
                                    save_receipt=dict(ui_receipt),
                                    configuration=completion_configuration,
                                )
                            )
                            if completed_manual is None:
                                log(
                                    "[MANUAL_CONTROL] Terminal UI route completed; "
                                    "retrying only its completion receipt without "
                                    "changing action authority",
                                    "WARN",
                                    console=True,
                                )
                            else:
                                self._manual_terminal_claims().pop(
                                    manual_id,
                                    None,
                                )
                else:
                    if (
                        manual_full_disposition is not None
                        and completed_record is None
                    ):
                        self._supervisor.transition_manual_control(
                            manual_id,
                            "failed",
                            detail=(
                                "full manual-Surrender collection was unavailable, "
                                "but the selected terminal route completed and "
                                "automation authority was preserved"
                            ),
                            refresh_status="terminal_collection_unavailable",
                            save_receipt=dict(manual_terminal["receipt"]),
                        )
                        return
                    completion_configuration = {
                        "schema_version": 1,
                        "terminal_status": manual_terminal.get("status"),
                        "collection": manual.get("surrender_collection"),
                        "battle_id": (
                            completed_record.get("battle_id")
                            if isinstance(completed_record, Mapping)
                            else manual_terminal.get("battle_id")
                        ),
                    }
                    completion_payload = {
                        "detail": (
                            "terminal save evidence was reconciled and the explicit "
                            "collection disposition was completed"
                        ),
                        "refresh_status": "terminal_reconciliation_complete",
                        "save_receipt": dict(manual_terminal["receipt"]),
                        "configuration": completion_configuration,
                    }
                    retained_claim = self._manual_terminal_claims().get(manual_id)
                    if isinstance(retained_claim, Mapping):
                        retained_claim = dict(retained_claim)
                    else:
                        retained_claim = {
                            "receipt": copy.deepcopy(
                                completion_payload["save_receipt"]
                            ),
                            "evidence": dict(current_manual_evidence),
                        }
                    retained_claim["semantic_completion_applied"] = True
                    retained_claim["pending_completion"] = copy.deepcopy(
                        completion_payload
                    )
                    self._manual_terminal_claims()[manual_id] = retained_claim
                    completed_manual = (
                        self._supervisor.transition_manual_control(
                            manual_id,
                            "completed",
                            detail=str(completion_payload["detail"]),
                            refresh_status=str(
                                completion_payload["refresh_status"]
                            ),
                            save_receipt=dict(completion_payload["save_receipt"]),
                            configuration=completion_configuration,
                        )
                    )
                    if completed_manual is None:
                        log(
                            "[MANUAL_CONTROL] Terminal route completed; retrying "
                            "only the manual-control completion receipt without "
                            "changing action authority",
                            "WARN",
                            console=True,
                        )
                    else:
                        self._manual_terminal_claims().pop(
                            manual_id,
                            None,
                        )
            finalize_run_boundary()
            if no_strategy_run and completed_record is not None:
                self._pending_no_strategy_record = completed_record
                self._no_strategy_post_run_stage = (
                    self._next_no_strategy_post_run_stage(
                        observed_run_configuration
                        if isinstance(observed_run_configuration, Mapping)
                        else None
                    )
                )
                self._no_strategy_post_run_retry_at = 0.0
                log(
                    "[NO_STRATEGY] Battle record is awaiting its verified Home "
                    f"{self._no_strategy_post_run_stage} stage before another "
                    "battle may start",
                    "INFO",
                    console=True,
                )
            elif no_strategy_run:
                log(
                    "[NO_STRATEGY] Structured Game Over capture failed; post-run "
                    "inventory was not attached",
                    "ERROR",
                    console=True,
                )
                self._no_strategy_observation_active = False
                self._no_strategy_attachment_boundary_id = None
                self._no_strategy_inventory_complete = False
                self._no_strategy_inventory_retry_at = 0.0
                self._no_strategy_observer.reset()
        elif new_state == "HOME_SCREEN":
            manual = self._supervisor.manual_control
            if (
                operator_workflow_only
                and self._handle_home_return_reconciliation(
                    screenshot=img,
                )
            ):
                return
            home_handler_enabled = self._handler_enabled("home")
            home_preflight_enabled = bool(
                self._runtime_policy().get("home_preflight") is True
            )
            exclusive_validation = self._exclusive_validation_definition()
            exclusive_request_pending = bool(
                exclusive_validation is not None
                and self._prepare_exclusive_validation_home_request(
                    exclusive_validation
                )
            )
            awaiting_initial_battle_intent = self._awaiting_initial_battle_intent()
            home_control = detect_home_battle_control(img).control
            emulator_recovery_active = bool(
                getattr(self, "_emulator_maintenance_hold_active", False)
                and isinstance(
                    self._supervisor.emulator_maintenance,
                    Mapping,
                )
            )
            emulator_recovery_new_battle = bool(
                emulator_recovery_active
                and getattr(
                    self,
                    "_emulator_recovery_force_new_battle",
                    False,
                )
                and home_control is HomeBattleControl.NEW_BATTLE
            )
            emulator_recovery_resume = bool(
                emulator_recovery_active
                and not emulator_recovery_new_battle
                and home_control is HomeBattleControl.RESUME_BATTLE
            )
            terminal_mode = AUTOMATION.mode
            workflow = self._supervisor.battle_workflow
            workflow_active = bool(
                isinstance(workflow, Mapping)
                and workflow.get("status")
                not in BATTLE_WORKFLOW_TERMINAL_STATUSES
            )
            explicit_start = bool(
                workflow_active
                and not awaiting_initial_battle_intent
                and workflow.get("intent") == "start_battle"
                and workflow.get("status") in {"acknowledged", "ready"}
            )
            explicit_attach = bool(
                workflow_active
                and workflow.get("intent") == "attach_battle"
                and workflow.get("status") == "validating_save"
            )
            manual = self._supervisor.manual_control
            manual_active = bool(
                isinstance(manual, Mapping)
                and manual.get("status")
                not in MANUAL_CONTROL_TERMINAL_STATUSES
            )
            manual_return_resume = bool(
                manual_active
                and manual.get("status") == "reconciling"
                and home_control is HomeBattleControl.RESUME_BATTLE
            )
            if workflow_active or manual_active:
                self._clear_terminal_home_continuation(
                    "an explicit operator workflow superseded it"
                )
                terminal_continuation_authorized = False
            else:
                terminal_continuation_authorized = (
                    self._terminal_home_continuation_ready(
                        home_control=home_control
                    )
                )
            managed_home_control = bool(
                getattr(
                    self,
                    "_operator_battle_intent_required",
                    False,
                )
            )
            legacy_home_launch_authorized = bool(
                not managed_home_control
                and not awaiting_initial_battle_intent
                and self._auto_start_enabled
                and terminal_mode is ExecMode.NEXT_BATTLE
            )
            legacy_home_preflight_authorized = bool(
                not managed_home_control
                and not awaiting_initial_battle_intent
            )
            workflow_request_id = str(
                (
                    workflow.get("request_id")
                    if isinstance(workflow, Mapping)
                    else ""
                )
                or ""
            )
            manual_control_id = str(
                (
                    manual.get("manual_control_id")
                    if isinstance(manual, Mapping)
                    else ""
                )
                or ""
            )
            home_launch_source = None
            home_launch_request_id = ""
            if explicit_start:
                home_launch_source = "start_battle"
                home_launch_request_id = workflow_request_id
            elif explicit_attach:
                home_launch_source = "attach_battle"
                home_launch_request_id = workflow_request_id
            elif manual_return_resume:
                home_launch_source = "manual_return"
                home_launch_request_id = manual_control_id
            elif terminal_continuation_authorized:
                home_launch_source = "terminal_continuation"
            elif (
                (emulator_recovery_new_battle or emulator_recovery_resume)
                and not workflow_active
                and not manual_active
            ):
                home_launch_source = "emulator_recovery"
                home_launch_request_id = str(
                    getattr(self, "_emulator_recovery_request_id", "") or ""
                )
            elif legacy_home_launch_authorized:
                home_launch_source = "legacy_auto_start"

            def home_preflight_owner_still_current() -> bool:
                if home_launch_source is None:
                    return True
                if self._home_launch_authority_matches(
                    source=home_launch_source,
                    request_id=home_launch_request_id,
                    home_control=home_control,
                ):
                    return True
                coordinator = getattr(
                    self,
                    "_player_save_preflight_coordinator",
                    None,
                )
                carry = (
                    coordinator.carry
                    if coordinator is not None
                    else None
                )
                if (
                    carry is not None
                    and carry.state is CarriedEvidenceState.PENDING_LAUNCH
                ):
                    coordinator.discard_carry(
                        "home_launch_authority_changed_during_preflight"
                    )
                return False

            validation_home_preflight_authorized = bool(
                exclusive_request_pending
                and not awaiting_initial_battle_intent
            )
            home_preflight_authorized = bool(
                explicit_start
                or terminal_continuation_authorized
                or emulator_recovery_new_battle
                or legacy_home_preflight_authorized
                or validation_home_preflight_authorized
            )
            requirements = self._mission_mgr.no_battle_setup_requirements()
            scope = get_activity_scope()
            scope_id = str(scope.get("run_id") or "") if scope else ""
            preflight_mode = str(
                self._runtime_policy().get(
                    "player_save_preflight",
                    "save_first",
                )
            )
            current_preflight_ready = bool(
                getattr(
                    self,
                    "_player_save_preflight_activity_scope_id",
                    None,
                )
                == scope_id
                and getattr(
                    getattr(
                        self,
                        "_player_save_preflight_result",
                        None,
                    ),
                    "ready",
                    False,
                )
            )
            baseline_only_preflight = bool(
                home_preflight_authorized
                and home_control is HomeBattleControl.NEW_BATTLE
                and not requirements
                and preflight_mode == "save_first"
                and getattr(
                    self,
                    "_player_save_preflight_coordinator",
                    None,
                )
                is not None
                and not self._activity_scope_has_history_baseline(scope)
                and not current_preflight_ready
            )
            if baseline_only_preflight:
                prior_history_baseline = getattr(
                    self,
                    "_player_save_history_baseline_outcome",
                    None,
                )
                prior_history_baseline_blocked = bool(
                    bool(getattr(prior_history_baseline, "blocked", False))
                    and getattr(
                        self,
                        "_player_save_preflight_activity_scope_id",
                        None,
                    )
                    == scope_id
                )
                if prior_history_baseline_blocked:
                    self._flag_recoverable_runtime_failure(
                        RuntimeFailureKind.EVIDENCE_UNAVAILABLE,
                        str(
                            getattr(prior_history_baseline, "reason", "")
                            or "Home History baseline remains unavailable"
                        ),
                    )
                    history_baseline = prior_history_baseline
                else:
                    save_preflight = self._acquire_player_save_home_preflight(
                        {},
                        screenshot=img,
                    )
                    if save_preflight is not None and not save_preflight.ready:
                        provenance = getattr(save_preflight, "provenance", {})
                        provenance = (
                            provenance if isinstance(provenance, Mapping) else {}
                        )
                        lifecycle_input_attempted = bool(
                            provenance.get("lifecycle_input_attempted") is True
                            or provenance.get("background_dispatched") is True
                        )
                        if lifecycle_input_attempted and not bool(
                            provenance.get("source_restored") is True
                        ):
                            self._supervisor.pause_for_catastrophic_failure(
                                RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                                reason=str(
                                    getattr(save_preflight, "reason", "")
                                    or "Home baseline refresh did not restore its source"
                                ),
                            )
                            return
                        self._flag_recoverable_runtime_failure(
                            RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                            str(
                                getattr(save_preflight, "reason", "")
                                or "Home History baseline was unavailable"
                            ),
                        )
                    if not home_preflight_owner_still_current():
                        return
                    history_baseline = getattr(
                        self,
                        "_player_save_history_baseline_outcome",
                        None,
                    )
                    if bool(getattr(history_baseline, "blocked", False)):
                        self._flag_recoverable_runtime_failure(
                            RuntimeFailureKind.EVIDENCE_UNAVAILABLE,
                            str(
                                getattr(history_baseline, "reason", "")
                                or "Home History baseline binding was unavailable"
                            ),
                        )
                if bool(getattr(history_baseline, "ui_required", False)):
                    log(
                        "[BATTLE_CONTINUITY] Baseline-only Home serialization "
                        "could not project History; yielding the next action "
                        "boundary to the guarded Battle History UI fallback",
                        "INFO",
                    )
                    return
            if (
                home_preflight_authorized
                and home_control is HomeBattleControl.NEW_BATTLE
                and requirements
                and (
                    exclusive_validation is None
                    or exclusive_request_pending
                )
            ):
                prior_history_baseline = getattr(
                    self,
                    "_player_save_history_baseline_outcome",
                    None,
                )
                prior_history_baseline_blocked = bool(
                    bool(getattr(prior_history_baseline, "blocked", False))
                    and getattr(
                        self,
                        "_player_save_preflight_activity_scope_id",
                        None,
                    )
                    == scope_id
                )
                if prior_history_baseline_blocked:
                    self._flag_recoverable_runtime_failure(
                        RuntimeFailureKind.EVIDENCE_UNAVAILABLE,
                        str(
                            getattr(prior_history_baseline, "reason", "")
                            or "Home History baseline remains unavailable"
                        ),
                    )
                self._claim_proactive_gate_waivers(
                    for_home_setup=True,
                    requirements=requirements,
                )
                if exclusive_validation is None:
                    directive = self._matching_gate_decision("home_setup")
                    if directive and directive.get("blocking", True):
                        self._supervisor.consume_gate_decision(
                            str(directive.get("request_id") or ""),
                            completion_reason=(
                                "superseded by the global continue-degraded policy"
                            ),
                        )
                        directive = None
                    if (
                        directive
                        and directive.get("status") == "resolved"
                        and not self._apply_gate_decision(
                            directive,
                            phase="home_setup",
                        )
                    ):
                        return
                waivers = merge_profile_skip_waivers(
                    requirements,
                    getattr(self, "_startup_gate_waivers", {}),
                )
                save_preflight = None
                if not prior_history_baseline_blocked:
                    save_preflight = self._acquire_player_save_home_preflight(
                        requirements,
                        screenshot=img,
                    )
                if save_preflight is not None and not save_preflight.ready:
                    provenance = getattr(save_preflight, "provenance", {})
                    provenance = (
                        provenance if isinstance(provenance, Mapping) else {}
                    )
                    lifecycle_input_attempted = bool(
                        provenance.get("lifecycle_input_attempted") is True
                        or provenance.get("background_dispatched") is True
                    )
                    source_restored = bool(
                        provenance.get("source_restored") is True
                    )
                    if lifecycle_input_attempted and not source_restored:
                        self._supervisor.pause_for_catastrophic_failure(
                            RuntimeFailureKind.SOURCE_RESTORATION_LOST,
                            reason=str(
                                getattr(save_preflight, "reason", "")
                                or "Home save refresh did not restore its source"
                            ),
                        )
                        return
                    self._flag_recoverable_runtime_failure(
                        RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                        str(
                            getattr(save_preflight, "reason", "")
                            or "Home save preflight was unavailable"
                        ),
                    )
                    save_preflight = None
                if not home_preflight_owner_still_current():
                    return
                history_baseline = getattr(
                    self,
                    "_player_save_history_baseline_outcome",
                    None,
                )
                if bool(getattr(history_baseline, "blocked", False)):
                    self._flag_recoverable_runtime_failure(
                        RuntimeFailureKind.EVIDENCE_UNAVAILABLE,
                        str(
                            getattr(history_baseline, "reason", "")
                            or "Home History baseline was unavailable"
                        ),
                    )
                setup = self._run_home_setup_attempts(
                    requirements,
                    screenshot=img,
                    waivers=waivers,
                    save_preflight=save_preflight,
                )
                if setup.interrupted:
                    return
                if not home_preflight_owner_still_current():
                    return
                if not setup.complete:
                    check_id = setup.failed_check or "startup_setup"
                    if exclusive_validation is not None:
                        receipt = self._reconcile_exclusive_validation()
                        if (
                            receipt is not None
                            and str(receipt.get("status") or "") == "pending"
                        ):
                            self._finish_exclusive_validation_without_cleanup(
                                receipt,
                                f"Home preflight failed at {check_id}: "
                                f"{setup.reason}",
                            )
                        else:
                            log(
                                "[TOURNAMENT_VALIDATION_FAILED] Tournament "
                                f"Home preflight failed at {check_id}: "
                                f"{setup.reason}",
                                "ERROR",
                                console=True,
                            )
                        return
                    directive = self._publish_gate_decision(
                        phase="home_setup",
                        check_id=check_id,
                        reason=setup.reason,
                        expected=requirements.get(check_id),
                        blocking=False,
                    )
                    if directive and directive.get("status") == "pending":
                        directive = (
                            self._prompt_for_gate_decision(directive)
                            or directive
                        )
                    if (
                        directive
                        and directive.get("status") == "resolved"
                        and self._apply_gate_decision(
                            directive,
                            phase="home_setup",
                        )
                    ):
                        waivers = merge_profile_skip_waivers(
                            requirements,
                            getattr(self, "_startup_gate_waivers", {}),
                        )
                        retry_frame = self._capture_frame()
                        if retry_frame is not None:
                            setup = self._run_home_setup_attempts(
                                requirements,
                                screenshot=retry_frame,
                                waivers=waivers,
                                save_preflight=save_preflight,
                            )
                            if setup.interrupted:
                                return
                            if not home_preflight_owner_still_current():
                                return
                    if not setup.complete:
                        self._flag_recoverable_runtime_failure(
                            RuntimeFailureKind.REPAIR_EXHAUSTED,
                            f"Home setup stopped at {check_id}: {setup.reason}",
                        )
                setup_evidence = dict(setup.evidence)
                if save_preflight is not None:
                    setup_evidence["player_save_preflight"] = (
                        save_preflight.as_dict()
                    )
                if not setup.complete:
                    self._mission_mgr.mark_no_battle_setup_degraded(
                        setup_evidence,
                        failed_check=str(
                            setup.failed_check or "startup_setup"
                        ),
                        reason=str(setup.reason),
                        waivers=waivers,
                    )
                elif waivers:
                    self._mission_mgr.mark_no_battle_setup_complete(
                        setup_evidence,
                        waivers=waivers,
                    )
                else:
                    self._mission_mgr.mark_no_battle_setup_complete(
                        setup_evidence
                    )
                self._startup_gate_waivers = {}
                if bool(getattr(history_baseline, "ui_required", False)):
                    log(
                        "[BATTLE_CONTINUITY] Home configuration setup is "
                        "complete; yielding the next action boundary to the "
                        "guarded Battle History UI fallback",
                        "INFO",
                    )
                    return
            if (
                not awaiting_initial_battle_intent
                and self._maybe_start_exclusive_validation(
                    home_control=home_control,
                )
            ):
                return
            self._report_home_policy(
                home_control=home_control,
                home_handler_enabled=home_handler_enabled,
                home_preflight_enabled=home_preflight_enabled,
                requirements_pending=bool(
                    self._mission_mgr.no_battle_setup_requirements()
                    and (
                        exclusive_validation is None
                        or exclusive_request_pending
                    )
                ),
            )
            if home_handler_enabled:
                save_coordinator = getattr(
                    self,
                    "_player_save_preflight_coordinator",
                    None,
                )
                carry = (
                    save_coordinator.carry
                    if save_coordinator is not None
                    else None
                )
                carry_pending = bool(
                    carry is not None
                    and carry.state is CarriedEvidenceState.PENDING_LAUNCH
                )
                launch_authorized = False
                if home_launch_source is not None:
                    launch_authorized = self._home_launch_authority_matches(
                        source=home_launch_source,
                        request_id=home_launch_request_id,
                        home_control=home_control,
                    )
                explicit_start = bool(
                    launch_authorized
                    and home_launch_source == "start_battle"
                )
                explicit_attach = bool(
                    launch_authorized
                    and home_launch_source == "attach_battle"
                )
                manual_return_resume = bool(
                    launch_authorized
                    and home_launch_source == "manual_return"
                )
                terminal_continuation_authorized = bool(
                    launch_authorized
                    and home_launch_source == "terminal_continuation"
                )
                emulator_recovery_launch = bool(
                    launch_authorized
                    and home_launch_source == "emulator_recovery"
                )
                legacy_home_launch_authorized = bool(
                    launch_authorized
                    and home_launch_source == "legacy_auto_start"
                )
                restart_enabled = bool(
                    explicit_start
                    or explicit_attach
                    or manual_return_resume
                    or terminal_continuation_authorized
                    or emulator_recovery_launch
                    or legacy_home_launch_authorized
                )
                tier_specific_new_battle = bool(
                    explicit_start
                    or terminal_continuation_authorized
                    or emulator_recovery_new_battle
                    or carry_pending
                    or (
                        legacy_home_launch_authorized
                        and home_control is HomeBattleControl.NEW_BATTLE
                    )
                )
                required_home_tier = None
                if tier_specific_new_battle:
                    try:
                        required_home_tier = self._current_strategy_home_tier()
                    except ValueError as exc:
                        self._flag_recoverable_runtime_failure(
                            RuntimeFailureKind.VALIDATION_UNAVAILABLE,
                            str(exc),
                        )
                        return
                preserve_dispatched_carry = bool(
                    carry is not None
                    and carry.state is CarriedEvidenceState.LAUNCH_DISPATCHED
                    and self._same_owner_free_ticket_retry_ready(
                        source=home_launch_source,
                        request_id=home_launch_request_id,
                    )
                )
                if (
                    carry is not None
                    and carry.state
                    in {
                        CarriedEvidenceState.LAUNCH_DISPATCHED,
                        CarriedEvidenceState.BOUND_RUNNING,
                    }
                    and not preserve_dispatched_carry
                ):
                    save_coordinator.discard_carry(
                        "unrelated_later_home_launch_boundary"
                    )
                launch_guard_state = {"allowed": launch_authorized}
                launch_action_guard = None
                if restart_enabled:

                    def revalidate_home_launch() -> bool:
                        allowed = self._home_launch_authority_matches(
                            source=str(home_launch_source),
                            request_id=home_launch_request_id,
                            home_control=home_control,
                        )
                        if allowed and required_home_tier is not None:
                            try:
                                allowed = (
                                    self._current_strategy_home_tier()
                                    == required_home_tier
                                )
                            except ValueError:
                                allowed = False
                        launch_guard_state["allowed"] = allowed
                        return allowed

                    launch_action_guard = revalidate_home_launch
                workflow_operation_id = None
                workflow_action_purpose = None
                workflow_action_reason = None
                if explicit_start or explicit_attach:
                    observation = self._current_control_workflow_evidence()
                    observation_id = str(
                        (observation or {}).get("observation_id") or "unknown"
                    )
                    workflow_operation_id = (
                        f"{home_launch_request_id}:{observation_id}:home_dispatch"
                    )
                    if explicit_start:
                        workflow_action_purpose = "Starting a new battle"
                        workflow_action_reason = (
                            "execute the exact verified New Battle intent after "
                            "normal new-run gates"
                        )
                    else:
                        workflow_action_purpose = (
                            "Attaching automation to a resumable battle"
                        )
                        workflow_action_reason = (
                            "execute the validated exact Resume Battle intent"
                        )
                elif manual_return_resume:
                    observation = self._current_control_workflow_evidence()
                    observation_id = str(
                        (observation or {}).get("observation_id") or "unknown"
                    )
                    workflow_operation_id = (
                        f"{home_launch_request_id}:{observation_id}:return-resume"
                    )
                    workflow_action_purpose = (
                        "Refreshing the manually controlled battle"
                    )
                    workflow_action_reason = (
                        "resume the exact observed battle only for Return Control "
                        "save reconciliation"
                    )
                elif terminal_continuation_authorized:
                    continuation = getattr(
                        self,
                        "_terminal_home_continuation",
                        {},
                    )
                    continuation_source = str(
                        continuation.get("source")
                        if isinstance(continuation, Mapping)
                        else "terminal_route"
                    )
                    continuation_operation_id = str(
                        continuation.get("operation_id")
                        if isinstance(continuation, Mapping)
                        else ""
                    ) or new_operation_id()
                    dispatch_number = int(
                        continuation.get("dispatch_count", 0)
                        if isinstance(continuation, Mapping)
                        else 0
                    ) + 1
                    workflow_operation_id = (
                        f"{continuation_operation_id}:home-dispatch-"
                        f"{dispatch_number}"
                    )
                    workflow_action_purpose = (
                        "Continuing after the completed battle"
                    )
                    workflow_action_reason = (
                        "exercise the exact bounded Home continuation from "
                        f"{continuation_source}; retain it until fresh battle "
                        "evidence proves the launch postcondition"
                    )
                elif emulator_recovery_launch:
                    # The maintenance request already owns the single outer
                    # ACTION/RESULT pair. The Home handler contributes only
                    # its verified INPUT to that operation.
                    workflow_operation_id = None
                    workflow_action_purpose = (
                        "Starting a replacement battle"
                        if emulator_recovery_new_battle
                        else "Resuming the recovered battle"
                    )
                    workflow_action_reason = (
                        "the prior run was not resumable after BlueStacks "
                        "maintenance"
                        if emulator_recovery_new_battle
                        else "Home still offers the exact resumable battle"
                    )
                typed_dispatch_kwargs = {
                    "return_dispatch_outcome": True
                }
                tier_dispatch_kwargs = (
                    {"required_tier": required_home_tier}
                    if required_home_tier is not None
                    else {}
                )
                if explicit_attach or manual_return_resume or (
                    emulator_recovery_launch
                    and emulator_recovery_resume
                ):
                    launch_result = handle_home_screen(
                        restart_enabled=restart_enabled,
                        require_resume_battle=True,
                        operation_id=workflow_operation_id,
                        action_purpose=workflow_action_purpose,
                        action_reason=workflow_action_reason,
                        action_guard_fn=launch_action_guard,
                        **typed_dispatch_kwargs,
                    )
                elif (
                    explicit_start
                    or terminal_continuation_authorized
                    or emulator_recovery_launch
                ):
                    launch_result = handle_home_screen(
                        restart_enabled=restart_enabled,
                        require_new_battle=True,
                        operation_id=workflow_operation_id,
                        action_purpose=workflow_action_purpose,
                        action_reason=workflow_action_reason,
                        action_guard_fn=launch_action_guard,
                        **tier_dispatch_kwargs,
                        **typed_dispatch_kwargs,
                    )
                elif carry_pending:
                    launch_result = handle_home_screen(
                        restart_enabled=restart_enabled,
                        require_new_battle=True,
                        action_guard_fn=launch_action_guard,
                        **tier_dispatch_kwargs,
                        **typed_dispatch_kwargs,
                    )
                elif (
                    legacy_home_launch_authorized
                    and home_control is HomeBattleControl.NEW_BATTLE
                    and required_home_tier is not None
                ):
                    launch_result = handle_home_screen(
                        restart_enabled=restart_enabled,
                        require_new_battle=True,
                        action_guard_fn=launch_action_guard,
                        **tier_dispatch_kwargs,
                        **typed_dispatch_kwargs,
                    )
                elif legacy_home_launch_authorized:
                    launch_result = handle_home_screen(
                        restart_enabled=restart_enabled,
                        action_guard_fn=launch_action_guard,
                        **typed_dispatch_kwargs,
                    )
                else:
                    if launch_action_guard is None:
                        launch_result = handle_home_screen(
                            restart_enabled=restart_enabled
                        )
                    else:
                        launch_result = handle_home_screen(
                            restart_enabled=restart_enabled,
                            action_guard_fn=launch_action_guard,
                        )
                launch_outcome = (
                    launch_result
                    if isinstance(launch_result, TapDispatchOutcome)
                    else TapDispatchOutcome(
                        TapDispatchStatus.DISPATCHED
                        if launch_result
                        else TapDispatchStatus.NOT_DISPATCHED
                    )
                )
                launched = launch_outcome.dispatched
                launch_authorized = bool(launch_guard_state["allowed"])
                if emulator_recovery_launch and launch_outcome.attempted:
                    self._emulator_recovery_home_attempts = int(
                        getattr(
                            self,
                            "_emulator_recovery_home_attempts",
                            0,
                        )
                    ) + 1
                if launch_outcome.uncertain:
                    reason = (
                        "a verified Home launch input may have reached the "
                        "device, but its result was uncertain"
                    )
                    if explicit_start or explicit_attach:
                        workflow = self._supervisor.battle_workflow
                        current = self._current_control_workflow_evidence() or {}
                        if isinstance(workflow, Mapping):
                            self._terminalize_uncertain_battle_workflow(
                                workflow,
                                current,
                                reason=reason,
                            )
                    elif terminal_continuation_authorized:
                        self._terminalize_uncertain_terminal_home_action(reason)
                    else:
                        if legacy_home_launch_authorized:
                            epochs = getattr(
                                self,
                                "_uncertain_legacy_home_launch_epochs",
                                None,
                            )
                            if epochs is None:
                                epochs = set()
                                self._uncertain_legacy_home_launch_epochs = epochs
                            epochs.add(
                                self._legacy_home_launch_epoch(home_control)
                            )
                        if home_launch_request_id:
                            uncertain = getattr(
                                self,
                                "_uncertain_lifecycle_actions",
                                None,
                            )
                            if uncertain is None:
                                uncertain = set()
                                self._uncertain_lifecycle_actions = uncertain
                            uncertain.add(home_launch_request_id)
                        self._runtime_uncertain_mutation_result(
                            reason + "; no replay is safe"
                        )
                if explicit_start or explicit_attach:
                    marked = (
                        self._mark_operator_battle_action_dispatched(True)
                        if launched
                        else False
                    )
                    if launched and not marked:
                        workflow = self._supervisor.battle_workflow
                        current = self._current_control_workflow_evidence() or {}
                        if isinstance(workflow, Mapping):
                            self._terminalize_uncertain_battle_workflow(
                                workflow,
                                current,
                                reason=(
                                    "a verified explicit Home launch was accepted, "
                                    "but its durable action_dispatched receipt could "
                                    "not be retained"
                                ),
                            )
                        else:
                            self._runtime_uncertain_mutation_result(
                                "a verified explicit Home launch was accepted, but "
                                "its durable owner disappeared; no replay is safe"
                            )
                    elif launch_outcome.uncertain:
                        marked = False
                if terminal_continuation_authorized and launched:
                    marked = self._mark_terminal_home_continuation_dispatched()
                    if not marked:
                        self._terminalize_uncertain_terminal_home_action(
                            "a verified terminal-bound Home launch was accepted, "
                            "but its process-local postcondition receipt could not "
                            "be retained"
                        )
                if emulator_recovery_launch and launched:
                    maintenance = self._supervisor.emulator_maintenance
                    replay = getattr(self, "_emulator_replay_window", None)
                    self._emulator_recovery_home_dispatch = {
                        "request_id": home_launch_request_id,
                        "control": home_control.value,
                        "dispatched_monotonic": time.monotonic(),
                        "modal_recovery_completed": False,
                    }
                    if emulator_recovery_resume:
                        if isinstance(replay, RestartReplayWindow):
                            replay.mark_resume_dispatched()
                        if isinstance(maintenance, Mapping):
                            self._set_emulator_recovery_ack(
                                maintenance,
                                state="resume_dispatched",
                                reason="Home Resume Battle was dispatched",
                            )
                            self._publish_action_authority()
                    elif isinstance(maintenance, Mapping):
                        self._set_emulator_recovery_ack(
                            maintenance,
                            state="fallback_starting_battle",
                            reason=(
                                "the prior battle was not resumable and a "
                                "verified configured New Battle launch was dispatched; "
                                "fresh RUNNING evidence is pending"
                            ),
                        )
                        self._publish_action_authority()
                if carry_pending:
                    if launch_outcome.uncertain:
                        save_coordinator.suspend_carry(
                            "input_result_uncertain"
                        )
                    elif restart_enabled:
                        save_coordinator.mark_runtime_launch(
                            control=home_control,
                            action_authorized=launch_authorized,
                            dispatched=bool(launched),
                        )
                    elif AUTOMATION.state is RunState.PAUSED:
                        save_coordinator.suspend_carry(
                            "pause_requires_fresh_home_evidence"
                        )
                    elif AUTOMATION.state is RunState.STOPPED:
                        save_coordinator.discard_carry(
                            "automation_stopped_before_home_launch"
                        )
                if not operator_workflow_only:
                    self._mission_mgr.on_home()
            else:
                save_coordinator = getattr(
                    self,
                    "_player_save_preflight_coordinator",
                    None,
                )
                if (
                    save_coordinator is not None
                    and save_coordinator.carry is not None
                    and save_coordinator.carry.state
                    is not CarriedEvidenceState.PENDING_LAUNCH
                ):
                    save_coordinator.discard_carry(
                        "home_handler_disabled_after_launch"
                    )

        if (
            "AD_GEMS_AVAILABLE" in overlays
            and self._handler_enabled("ad_gem")
        ):
            handle_ad_gem(
                action_guard_fn=self._auxiliary_action_guard(
                    AuxiliaryCollector.IN_BATTLE_AD_GEM
                ),
                floating_action_guard_fn=self._auxiliary_action_guard(
                    AuxiliaryCollector.FLOATING_GEM_SCAN
                ),
            )

    def _handle_mission_rewards_if_due(
        self,
        new_state: str,
        img: Frame,
        overlays: Optional[Set[str]] = None,
    ) -> bool:
        """Inspect relevant reward badges from an actionable battle or Home UI."""

        if new_state == "RUNNING":
            current_overlays = set(overlays or ())
            if "MENU_OPEN" in current_overlays:
                alert_visible = measure_menu_reward_badges(img).any
            elif "MENU_CLOSED" in current_overlays:
                alert_visible = menu_reward_alert_visible(img)
            else:
                alert_visible = False
        elif new_state == "HOME_SCREEN":
            alert_visible = measure_home_reward_badges(img).any
        else:
            return False
        if not self._mission_reward_scheduler.should_attempt(
            alert_visible=alert_visible,
        ):
            return False

        log(
            f"[MISSION_REWARDS] Starting reward probe from {new_state}",
            "DEBUG",
        )
        if stop_blind_gem_tapper():
            self._blind_tapper_suspended = True
        wall_now = datetime.now(timezone.utc)
        claim_daily_missions = daily_mission_claims_allowed(wall_now)
        weekly_review_kwargs = {}
        weekly_review_state = getattr(
            self,
            "_weekly_chest_review_state",
            None,
        )
        if weekly_review_state is not None:
            weekly_review_kwargs["weekly_review_state"] = weekly_review_state
        lease = None
        if new_state == "RUNNING":
            lease = self._begin_auxiliary_route(
                (
                    AuxiliaryCollector.DAILY_MISSION_REWARDS,
                    AuxiliaryCollector.WEEKLY_MISSION_REWARDS,
                    AuxiliaryCollector.EVENT_MISSION_REWARDS,
                    AuxiliaryCollector.GUILD_CHEST_REWARDS,
                ),
                source_state=new_state,
            )
            if lease is None:
                return False
            action_guard = self._auxiliary_action_guard(
                AuxiliaryCollector.DAILY_MISSION_REWARDS,
                route=lease,
            )
            result = handle_mission_rewards(
                screenshot=img,
                claim_daily_missions=claim_daily_missions,
                event_inventory_callback=(
                    self._event_mission_tracker.record_inventory
                ),
                action_guard_fn=action_guard,
                route_state_callback=self._auxiliary_route_state_callback(
                    lease
                ),
                **weekly_review_kwargs,
            )
        else:
            result = handle_mission_rewards(
                screenshot=img,
                claim_daily_missions=claim_daily_missions,
                event_inventory_callback=(
                    self._event_mission_tracker.record_inventory
                ),
                **weekly_review_kwargs,
            )
        if result == MissionRewardResult.FAILED:
            self._mission_reward_scheduler.mark_failed(wall_now=wall_now)
        elif result == MissionRewardResult.CLAIMED:
            self._mission_reward_scheduler.mark_claimed(wall_now=wall_now)
        elif result == MissionRewardResult.NOTHING_AVAILABLE:
            self._mission_reward_scheduler.mark_nothing_available(
                wall_now=wall_now
            )
        if lease is not None:
            route_state = self._get_action_authority().auxiliary_route
            cleanup_pending = bool(
                route_state is not None
                and route_state.lease.route_id == lease.route_id
                and route_state.cleanup_pending
            )
            if (
                result != MissionRewardResult.INTERRUPTED
                and not cleanup_pending
                and not action_guard()
            ):
                self._get_action_authority().update_auxiliary_route(
                    lease,
                    expected_state=lease.source_state,
                    cleanup_pending=True,
                    suspended_reason=(
                        "auxiliary authority was lost before final route release"
                    ),
                )
                cleanup_pending = True
            if result == MissionRewardResult.INTERRUPTED or cleanup_pending:
                self._pending_auxiliary_cleanup = (
                    "mission_rewards",
                    lease,
                )
            else:
                self._release_auxiliary_route(
                    lease,
                    reason=f"mission reward route result={result.value}",
                )
        return True

    def _emit_event_mission_warnings(self) -> None:
        """Repeat due persisted Event Mission reminders without UI activity."""

        try:
            warnings = self._event_mission_tracker.due_warnings()
        except Exception as exc:
            log(
                f"[EVENT_MISSIONS] Warning check failed: {exc}",
                "WARN",
                console=True,
            )
            return
        for warning in warnings:
            log(format_warning(warning), "WARN", console=True)

    def _handle_daily_gem_if_due(self, new_state: str, overlays: Set[str]) -> bool:
        """Run the Daily Gem probe from a safe state after UTC rollover."""

        # Home is normally a transitional screen before the next battle. Let
        # the Home handler start/resume that battle, then probe Store from the
        # stable RUNNING route on a later loop.
        if new_state != "RUNNING":
            return False
        badge_visible = "DAILY_GEMS_AVAILABLE" in overlays
        attempted_at = datetime.now(timezone.utc)
        if not self._daily_gem_scheduler.should_attempt(
            badge_visible=badge_visible,
            now=attempted_at,
        ):
            return False

        reason = "badge" if badge_visible else "UTC rollover"
        log(
            f"[DAILY_GEM] Starting {reason} Store probe from state={new_state}",
            "DEBUG",
        )
        if stop_blind_gem_tapper():
            self._blind_tapper_suspended = True
        lease = self._begin_auxiliary_route(
            (AuxiliaryCollector.DAILY_GEM_STORE,),
            source_state=new_state,
        )
        if lease is None:
            return False
        action_guard = self._auxiliary_action_guard(
            AuxiliaryCollector.DAILY_GEM_STORE,
            route=lease,
        )
        result = handle_daily_gem(
            action_guard_fn=action_guard,
            route_state_callback=self._auxiliary_route_state_callback(lease),
        )
        if result in {DailyGemResult.CLAIMED, DailyGemResult.NOT_READY}:
            # Attribute a badge-triggered claim to the game day on which the
            # handler began. If navigation crosses UTC midnight, the new day
            # must remain eligible on the next loop.
            self._daily_gem_scheduler.mark_completed(result.value, now=attempted_at)
        elif result != DailyGemResult.INTERRUPTED:
            self._daily_gem_scheduler.mark_failed()
        route_state = self._get_action_authority().auxiliary_route
        cleanup_pending = bool(
            route_state is not None
            and route_state.lease.route_id == lease.route_id
            and route_state.cleanup_pending
        )
        if (
            result != DailyGemResult.INTERRUPTED
            and not cleanup_pending
            and not action_guard()
        ):
            self._get_action_authority().update_auxiliary_route(
                lease,
                expected_state=lease.source_state,
                cleanup_pending=True,
                suspended_reason=(
                    "auxiliary authority was lost before final route release"
                ),
            )
            cleanup_pending = True
        if result == DailyGemResult.INTERRUPTED or cleanup_pending:
            self._pending_auxiliary_cleanup = ("daily_gem", lease)
        else:
            self._release_auxiliary_route(
                lease,
                reason=f"Daily Gem route result={result.value}",
            )
        return True

    def _normalise_detection(self, detection: Dict[str, Any]) -> tuple[str, Optional[str], Set[str], Set[str]]:
        """Normalise detector output, ensuring deterministic container types."""
        state = detection.get("state") or "UNKNOWN"
        menu = detection.get("menu") or None
        secondary = set(detection.get("secondary_states") or [])
        overlays = set(detection.get("overlays") or [])
        return state, menu, secondary, overlays

    def _resolve_upgrade_detail_overlay(
        self,
        img: Frame,
        detection: Dict[str, Any],
        *,
        max_attempts: int = 3,
    ) -> Tuple[Frame, Dict[str, Any], bool]:
        overlays = set(detection.get("overlays") or [])
        if "UPGRADE_DETAIL" not in overlays:
            return img, detection, True

        for attempt in range(1, max_attempts + 1):
            handled_image = handle_upgrade_detail_popup(screenshot=img)
            if handled_image is not None:
                img = handled_image
            time.sleep(0.2)
            detection = detect_state_and_overlays(img, log_matches=self._match_trace)
            overlays = set(detection.get("overlays") or [])
            if "UPGRADE_DETAIL" not in overlays:
                log(
                    f"[UPGRADE_DETAIL] Overlay cleared after attempt {attempt}",
                    "DEBUG",
                )
                return img, detection, True

        log(
            "[UPGRADE_DETAIL] Overlay persisted after multiple attempts; "
            "will retry next loop",
            "WARN",
        )
        return img, detection, False

    def _load_mission(self, config: AppConfig):
        """Initialise the mission configuration based on CLI options."""
        if config.mission_config_path:
            try:
                mission = YamlMission.from_file(config.mission_config_path)
                log(
                    f"[MISSION] Loaded YAML mission from {config.mission_config_path}",
                    "INFO",
                    console=True,
                )
                return mission
            except Exception as exc:
                log(f"[MISSION] Failed to load YAML mission: {exc}", "ERROR")
        return get_mission(config.mission_name)

    def _load_strategy(self, config: AppConfig):
        """Initialise the strategy configuration (YAML overrides name)."""
        if config.strategy_config_path:
            try:
                from automation.strategies.yaml_strategy import YamlStrategy

                strat = YamlStrategy.from_file(config.strategy_config_path)
                log(
                    f"[STRATEGY] Loaded YAML strategy from {config.strategy_config_path}",
                    "INFO",
                    console=True,
                )
                return strat
            except Exception as exc:
                log(f"[STRATEGY] Failed to load YAML strategy: {exc}", "ERROR")
        strategy = get_strategy(config.strategy_name)
        if strategy:
            log(
                f"[STRATEGY] Loaded strategy profile {strategy.name}",
                "INFO",
                console=True,
            )
        return strategy


__all__ = ["App"]
