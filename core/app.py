from __future__ import annotations

"""Primary application orchestration loop for the automation runtime."""

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
from core.watchdog import watchdog_process_check
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
    StrategyGateExitEvent,
)
from core.ss_capture import (
    ScreenshotFailure,
    capture_and_save_screenshot,
    capture_and_save_screenshot_result,
)
from core.state_detector import detect_state_and_overlays
from core.automation_supervisor import AutomationSupervisor
from core.daily_gem_scheduler import DailyGemScheduler
from core.event_mission_tracker import EventMissionTracker, format_warning
from core.home_battle import detect_home_battle_control
from core.battle_lifecycle import HomeBattleControl
from core.battle_stats import (
    attach_observed_run_configuration,
    persist_battle_record,
)
from core.battle_activation_tracker import BattleActivationTracker
from core.player_save_audit import PlayerSaveAuditCollector
from core.player_save_preflight import (
    CarriedEvidenceState,
    PlayerSavePreflightContext,
    PlayerSavePreflightCoordinator,
)
from core.perk_timeline import PerkTimelineObserver
from core.no_strategy_inventory import (
    NoStrategyInventoryStatus,
    RECOVERABLE_INVENTORY_STATES,
    run_no_strategy_in_battle_inventory,
)
from core.no_strategy_observer import NoStrategyRunObserver
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
    run_gc_no_battle_setup,
)
from core.game_speed import GameSpeedGuard
from core.gate_decisions import (
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
    daily_mission_claims_allowed,
)
from core.run_state import AUTOMATION, ExecMode, RunState
from core.app_setup import AppConfig
from core.status_report import StateChangeTracker, StatusReporter
from core.recovery import handle_unknown_state, update_unknown_state
from core.run_controls import return_home_from_game_over, surrender_run
from automation.missions.manager import MissionManager
from automation.missions import get_mission
from automation.missions.yaml_mission import YamlMission
from automation.strategies import get_strategy
from handlers.game_over_handler import handle_game_over
from handlers.tournament_result_handler import handle_tournament_results
from handlers.home_screen_handler import handle_home_screen, tap_verified_new_battle
from handlers.tournament_launch_handler import dispatch_tournament_launch
from handlers.ad_gem_handler import (
    handle_ad_gem,
    handle_home_ad_gem,
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
_BATTLE_SCOPE_UNSET = object()


class App:
    """Main automation orchestrator wrapping capture → detect → dispatch."""

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
        self._adb_target_session = adb_target_session
        self._adb_connection_coordinator = (
            adb_connection_coordinator or AdbConnectionCoordinator()
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
        self._exclusive_validation_terminal_hold: Optional[str] = None
        self._exclusive_validation_ownership_hold = False
        self._observe_strategy_request()
        ensure_activity_scope(reason="automation_started")
        self._player_save_runtime_session_id = new_operation_id()
        self._player_save_preflight_session_id = ""
        self._player_save_preflight_result = None
        self._player_save_preflight_coordinator = (
            PlayerSavePreflightCoordinator(
                target_snapshot_fn=adb_target_session.snapshot,
                context_fn=self._current_player_save_preflight_context,
                action_guard_fn=self._runtime_action_guard,
                capture_fn=self._capture_frame,
            )
            if adb_target_session is not None
            else None
        )
        self._activity_continuity = ActivityContinuityCoordinator()
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
                target_snapshot_fn=(
                    adb_target_session.snapshot
                    if adb_target_session is not None
                    else None
                ),
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
        self._blind_tapper_suspended = False
        self._tournament_results_captured = False
        self._no_strategy_observer = NoStrategyRunObserver()
        self._no_strategy_observation_active = False
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
        self._last_home_policy_signature: Optional[Tuple[object, ...]] = None
        rollover_state = Path(config.control_file).parent / "daily_gem_state.json"
        self._daily_gem_scheduler = DailyGemScheduler(rollover_state)
        self._mission_reward_scheduler = MissionRewardScheduler()
        event_mission_state = (
            Path(config.control_file).parent / "event_mission_tracker.json"
        )
        self._event_mission_tracker = EventMissionTracker(event_mission_state)
        self._action_authority = RuntimeActionAuthority()
        self._action_authority_publisher = RuntimeActionAuthorityPublisher(
            Path(config.control_file).with_name("strategy_action_gate.json"),
            owner=self._supervisor.current_exclusive_validation_owner(),
        )
        self._authority_battle_active = False
        self._authority_primary_state = "UNKNOWN"
        self._authority_holds: tuple[AuthorityHoldState, ...] = ()
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
            self._observed_active_battle_scope_id = self._current_run_scope_id()
            self._last_unbound_terminal_signature = None
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

        terminal = str(terminal_state or "UNKNOWN").upper()
        binding = self._terminal_run_binding()
        context: dict[str, Any] = {
            "strategy": None,
            "terminal_state": terminal,
            "run_configuration": {},
            "run_binding": binding,
        }
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
            return context

        self._last_unbound_terminal_signature = None
        strategy = self._mission_mgr.strategy
        context.update(
            {
                "strategy": strategy.name if strategy else None,
                "run_configuration": (
                    strategy.run_configuration() if strategy else {}
                ),
                "last_wave": self._last_wave_value,
                "last_wave_confidence": self._last_wave_conf,
                "coin_rate_samples": self._status_reporter.coin_rate_samples,
                "game_speed_control": self._game_speed_control_snapshot(),
                "survival_ability_activations": (
                    self._activation_tracker().snapshot()
                ),
                "perk_selection_timeline": self._perk_timeline().snapshot(),
                "session_preflight_evidence": dict(
                    self._mission_mgr.ctx.data.get("mission_vars", {}).get(
                        "gc_session_preflight_evidence",
                        {},
                    )
                ),
            }
        )
        if isinstance(observed_run_configuration, Mapping):
            context["observed_run_configuration"] = dict(
                observed_run_configuration
            )
        return context

    def _get_action_authority(self) -> RuntimeActionAuthority:
        """Return the central authority, including for partial test instances."""

        authority = getattr(self, "_action_authority", None)
        if authority is None:
            authority = RuntimeActionAuthority()
            self._action_authority = authority
        return authority

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
            self._authority_holds = tuple(holds)
        current_holds = tuple(getattr(self, "_authority_holds", ()))
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

    def _publish_action_authority(
        self,
        *,
        runtime_active: bool = True,
    ) -> None:
        publisher = getattr(self, "_action_authority_publisher", None)
        if publisher is None:
            return
        supervisor = getattr(self, "_supervisor", None)
        owner = (
            supervisor.current_exclusive_validation_owner()
            if supervisor is not None
            and callable(
                getattr(supervisor, "current_exclusive_validation_owner", None)
            )
            else None
        )
        publisher.publish(
            self._get_action_authority().snapshot(),
            runtime_active=runtime_active,
            owner=owner,
        )

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
        if strategy is None:
            raise RuntimeError(
                "player-save preflight requires a selected strategy"
            )
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
            strategy_name=str(strategy.name or ""),
            configuration_fingerprint=str(
                strategy.session_preflight_fingerprint() or ""
            ),
            target=target.target,
            target_generation=target.generation,
        )

    def _acquire_player_save_home_preflight(
        self,
        requirements: Mapping[str, Any],
        *,
        screenshot,
    ):
        coordinator = getattr(
            self,
            "_player_save_preflight_coordinator",
            None,
        )
        if coordinator is None:
            return None
        self._player_save_preflight_session_id = new_operation_id()
        mode = self._runtime_policy().get(
            "player_save_preflight",
            "save_first",
        )
        result = coordinator.acquire(
            requirements,
            mode=mode,
            initial_frame=screenshot,
        )
        self._player_save_preflight_result = result
        return result

    def _current_strategy_name(self) -> str:
        strategy = self._mission_mgr.strategy
        return str(strategy.name if strategy else "none").strip().lower()

    def _exclusive_validation_definition(
        self,
    ) -> Optional[ExclusiveValidationDefinition]:
        try:
            return exclusive_validation_definition(self._mission_mgr.strategy)
        except (TypeError, ValueError) as exc:
            log(f"[VALIDATION] Invalid strategy validation policy: {exc}", "ERROR")
            return None

    def _exclusive_validation_receipt(self) -> Optional[Dict[str, object]]:
        active_request_id = getattr(
            self,
            "_active_exclusive_validation_request_id",
            None,
        )
        if active_request_id:
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
                    break
                failed = self._supervisor.fail_orphaned_exclusive_validation(
                    candidate_id,
                    reason=(
                        "validation ownership belongs to a prior runtime or ADB "
                        "target; no Surrender or battle action was taken"
                    ),
                )
                if failed:
                    self._exclusive_validation_ownership_hold = True
                    self._announce_exclusive_validation_result(failed)

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
            self._active_exclusive_validation_request_id = None
            if failed:
                self._announce_exclusive_validation_result(failed)
                return failed
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
        self._active_exclusive_validation_request_id = None
        if result:
            self._announce_exclusive_validation_result(result)
        return result

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
        log_action_intent(
            "Starting the one-shot Tournament validation battle",
            reason=(
                "use the verified ordinary New Battle control to inspect "
                "battle-only Tournament settings, then Surrender only this "
                "automation-owned battle"
            ),
            detail=f"[TOURNAMENT_VALIDATION] request_id={request_id}",
        )
        if tap_verified_new_battle():
            log(
                "[TOURNAMENT_VALIDATION] Ordinary NEW_BATTLE dispatched after "
                f"durable ownership claim {request_id}",
                "DEBUG",
            )
            return True
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
            if self._supervisor.owns_exclusive_validation(
                request_id,
                statuses=("running",),
            ):
                self._mission_mgr.set_exclusive_validation_battle(True)
            return
        if status != "claimed" or str(
            detection.get("state") or ""
        ).upper() != "RUNNING":
            if status == "claimed":
                try:
                    deadline_at = float(receipt.get("deadline_at") or 0.0)
                except (TypeError, ValueError):
                    deadline_at = 0.0
                if deadline_at and time.time() >= deadline_at:
                    self._finish_exclusive_validation_without_cleanup(
                        receipt,
                        "the claimed ordinary NEW_BATTLE did not reach fresh "
                        "RUNNING evidence before its timeout; ownership is "
                        "ambiguous and no Surrender was attempted",
                    )
            return
        if not battle_started or not self._ordinary_validation_battle_guard(
            detection
        ):
            self._finish_exclusive_validation_without_cleanup(
                receipt,
                "the claimed Home transition did not establish a new ordinary "
                "battle; ownership is ambiguous and no Surrender was attempted",
            )
            return
        running = self._supervisor.mark_exclusive_validation_running(request_id)
        if running is None:
            return
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
        return bool(
            receipt
            and str(receipt.get("status") or "") in {"claimed", "running", "cleanup"}
            and self._supervisor.owns_exclusive_validation(
                str(receipt.get("request_id") or ""),
                statuses=(str(receipt.get("status") or ""),),
            )
        )

    def _advance_exclusive_validation(
        self,
        detection: Mapping[str, object],
    ) -> bool:
        """Conclude validation and Surrender only its still-owned battle."""

        receipt = self._reconcile_exclusive_validation()
        if receipt is None or str(receipt.get("status") or "") != "running":
            return False
        request_id = str(receipt.get("request_id") or "")
        if not self._ordinary_validation_battle_guard(detection):
            if str(detection.get("state") or "").upper() == "RUNNING":
                self._finish_exclusive_validation_without_cleanup(
                    receipt,
                    "Tournament identity appeared in the owned validation "
                    "window; refusing Surrender because the battle is ambiguous",
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
            action_guard=lambda: self._supervisor.owns_exclusive_validation(
                request_id,
                statuses=("cleanup",),
            ),
            running_guard=self._ordinary_validation_battle_guard,
        )
        if surrendered:
            log(
                "[TOURNAMENT_VALIDATION] Owned validation battle reached Game Over",
                "DEBUG",
            )
            return True
        self._finish_exclusive_validation_without_cleanup(
            cleanup,
            reason
            + "; guarded Surrender did not conclusively reach Game Over, so "
            "no retry or further battle action was attempted",
        )
        return True

    def _handle_exclusive_validation_game_over(self) -> bool:
        if getattr(self, "_exclusive_validation_terminal_hold", None):
            return True
        receipt = self._reconcile_exclusive_validation()
        if receipt is None or str(receipt.get("status") or "") != "cleanup":
            return False
        request_id = str(receipt.get("request_id") or "")
        if not self._supervisor.owns_exclusive_validation(
            request_id,
            statuses=("cleanup",),
        ):
            return False
        returned_home = return_home_from_game_over(
            timeout_s=8.0,
            action_guard=lambda: self._supervisor.owns_exclusive_validation(
                request_id,
                statuses=("cleanup",),
            ),
        )
        outcome = str(receipt.get("pending_outcome") or "failed")
        reason = str(receipt.get("pending_reason") or "validation completed")
        if not returned_home:
            outcome = "failed"
            reason += "; the owned battle ended, but verified NEW_BATTLE Home was not reached"
            self._exclusive_validation_terminal_hold = request_id
        else:
            self._exclusive_validation_terminal_hold = None
        result = self._supervisor.finish_exclusive_validation(
            request_id,
            outcome=outcome,
            reason=reason,
            allowed_statuses=("cleanup",),
        )
        self._active_exclusive_validation_request_id = None
        if result:
            self._announce_exclusive_validation_result(result)
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
                    self._announce_exclusive_validation_launch(failed)

        receipt = self._exclusive_validation_receipt()
        if receipt is None:
            return None
        launch = receipt.get("launch")
        if not isinstance(launch, Mapping):
            return None
        return receipt

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

        receipt = self._reconcile_exclusive_validation_launch()
        if receipt is None:
            return False
        request_id = str(receipt.get("request_id") or "")
        launch = receipt.get("launch")
        if not isinstance(launch, Mapping):
            return False
        launch_status = str(launch.get("status") or "")
        if (
            launch_status in {"awaiting_operator", "requested"}
            and battle_started
            and self._tournament_battle_guard(detection)
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
            if started:
                self._announce_exclusive_validation_launch(started)
            return False
        if launch_status not in {"requested", "claimed"}:
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
                action_guard=lambda: (
                    self._supervisor.exclusive_validation_launch_action_allowed(
                        request_id
                    )
                ),
            )
            if dispatched.dispatched:
                log(
                    "[TOURNAMENT_LAUNCH] Verified Tournament BATTLE dispatched "
                    f"under durable launch claim {request_id}",
                    "DEBUG",
                )
                return True
            failed = self._supervisor.finish_exclusive_validation_launch(
                request_id,
                outcome="failed",
                reason=dispatched.reason,
            )
            self._active_exclusive_validation_launch_request_id = None
            if failed:
                self._announce_exclusive_validation_launch(failed)
            return False

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
            self._active_exclusive_validation_launch_request_id = None
            if failed:
                self._announce_exclusive_validation_launch(failed)
            return False
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
                self._active_exclusive_validation_launch_request_id = None
                if started:
                    self._announce_exclusive_validation_launch(started)
                return False
            failed = self._supervisor.finish_exclusive_validation_launch(
                request_id,
                outcome="failed",
                reason=(
                    "Launch reached RUNNING without a fresh Tournament battle "
                    "boundary; no further input was attempted"
                ),
            )
            self._active_exclusive_validation_launch_request_id = None
            if failed:
                self._announce_exclusive_validation_launch(failed)
            return False
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
            self._active_exclusive_validation_launch_request_id = None
            if failed:
                self._announce_exclusive_validation_launch(failed)
            return False
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
        signature: Tuple[object, ...] = (
            self._current_strategy_name(),
            home_control,
            home_handler_enabled,
            home_preflight_enabled,
            requirements_pending,
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
        allow_repair_restart: bool = False,
    ) -> Optional[Dict[str, Any]]:
        options = build_gate_decision_options(
            check_id,
            self._mission_mgr.gate_fallbacks(check_id),
            advisory=not blocking,
            allow_repair_restart=allow_repair_restart,
        )
        directive = self._supervisor.publish_gate_decision(
            strategy=self._current_strategy_name(),
            phase=phase,
            check_id=check_id,
            reason=reason,
            expected=expected,
            options=options,
            blocking=blocking,
        )
        if not isinstance(directive, Mapping):
            return None
        if directive and directive.get("status") == "pending":
            log(
                f"[GATE_DECISION] Waiting for {check_id}: {reason}",
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
        if directive.get("status") == "pending":
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
        elif action == "pause":
            if not self._supervisor.persist_state("PAUSED"):
                return False
            completion_reason = f"paused for manual {check_id} changes"
        elif action == "repair_restart":
            if not self._mission_mgr.authorize_session_preflight_restart():
                return False
            completion_reason = (
                f"authorized guarded battle restart to repair {check_id}"
            )
        else:
            return False
        if phase == "session_preflight" and action in {
            "waive",
            "retry",
            "repair_restart",
        }:
            self._get_action_authority().clear_strategy_gate(
                event={
                    "waive": StrategyGateExitEvent.RUN_SCOPED_WAIVER,
                    "retry": StrategyGateExitEvent.ACCEPTED_RETRY,
                    "repair_restart": (
                        StrategyGateExitEvent.AUTHORIZED_REPAIR_TRANSITION
                    ),
                }[action],
                reason=completion_reason,
            )
        self._supervisor.consume_gate_decision(
            request_id,
            completion_reason=completion_reason,
        )
        log(
            f"[GATE_DECISION] {completion_reason}",
            "WARN" if action in {"waive", "repair_restart"} else "INFO",
            console=True,
        )
        return True

    def _handle_terminal_session_gate_decision(self) -> None:
        checks = self._mission_mgr.session_preflight_failure_checks()
        check_id = checks[0] if checks else "session_preflight"
        directive = self._matching_gate_decision("session_preflight")
        if directive is None:
            requirements = self._mission_mgr.strategy.session_preflight_requirements()
            reason = str(
                self._mission_mgr.ctx.data.get("mission_vars", {}).get(
                    "gc_session_preflight_last_reason",
                    "session preflight mismatch",
                )
            )
            directive = self._publish_gate_decision(
                phase="session_preflight",
                check_id=check_id,
                reason=reason,
                expected=requirements.get(check_id),
                allow_repair_restart=(
                    getattr(self, "_auto_start_enabled", True)
                    and self._mission_mgr.session_preflight_restart_available()
                ),
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
        if requested_name == self._current_strategy_name():
            self._pending_strategy_request = None
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
    ) -> None:
        """Translate terminal preflight evidence into the typed gate state."""

        authority = self._get_action_authority()
        if terminally_blocked:
            mission_vars = self._mission_mgr.ctx.data.setdefault(
                "mission_vars",
                {},
            )
            checks = self._mission_mgr.session_preflight_failure_checks()
            evidence = mission_vars.get("gc_session_preflight_evidence")
            if not checks and isinstance(evidence, Mapping):
                raw_checks = evidence.get("failed_checks")
                if isinstance(raw_checks, (list, tuple)):
                    checks = [str(value) for value in raw_checks]
            reason = str(
                mission_vars.get("gc_session_preflight_last_reason")
                or "running-battle strategy validation failed"
            )
            authority.activate_strategy_gate(
                strategy=self._current_strategy_name(),
                battle_scope=self._current_run_scope_id(),
                source="session_preflight",
                phase="running_battle",
                failed_check_ids=checks,
                reason=reason,
            )
            return

        gate = authority.strategy_gate
        if gate is None:
            return
        strategy = self._mission_mgr.strategy
        if (
            strategy is not None
            and strategy.requires_session_preflight()
            and strategy.is_session_preflight_complete(self._mission_mgr.ctx)
        ):
            authority.clear_strategy_gate(
                event=StrategyGateExitEvent.SUCCESSFUL_VALIDATION,
                reason="the running-battle strategy checks completed successfully",
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
        elif state == "WORKSHOP":
            # Workshop is not available from an active or resumable battle.
            # This is authoritative no-battle evidence even when the operator
            # navigated here manually while Pause blocks runtime actions.
            self._strategy_boundary_confirmed = True
            self._exclusive_validation_ownership_hold = False

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
        self._complete_strategy_application(requested_name)
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
        self._complete_strategy_application(requested_name)
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

    def _complete_strategy_application(self, requested_name: str) -> None:
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
        self._pending_strategy_request = None
        self._run_initialization_gate_logged = False
        self._session_preflight_gate_logged = False
        self._session_preflight_terminal_blocked_logged = False
        self._session_preflight_repair_denial_logged = False
        self._steady_run_entry_pending = False
        self._startup_gate_waivers = {}
        self._no_strategy_inventory_complete = False
        self._no_strategy_inventory_retry_at = 0.0
        self._active_exclusive_validation_request_id = None
        self._active_exclusive_validation_launch_request_id = None
        self._exclusive_validation_terminal_hold = None
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

        confirmed_scope_id = getattr(
            outcome,
            "confirmed_same_battle_scope_id",
            None,
        )
        if confirmed_scope_id:
            self._mission_mgr.reuse_session_preflight_for_confirmed_attachment(
                str(confirmed_scope_id)
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
        if self._supervisor.is_paused:
            if stop_blind_gem_tapper():
                self._blind_tapper_suspended = True
        self._update_action_authority()
        self._publish_action_authority()
        if self._adb_connection_coordinator.ensure_connected():
            time.sleep(2)

        threading.Thread(
            target=watchdog_process_check,
            args=(30, self._adb_connection_coordinator),
            daemon=True,
        ).start()

        try:
            while True:
                self._prune_generated_artifacts()
                # Control synchronization must not depend on a working ADB
                # connection. This both acknowledges Pause during an outage
                # and permits a paused live target handoff before capture.
                if self._supervisor.apply_control():
                    self._status_reporter.request_immediate_report()
                self._observe_strategy_request()
                is_paused = self._supervisor.is_paused
                if is_paused and stop_blind_gem_tapper():
                    self._blind_tapper_suspended = True
                self._update_action_authority()
                self._publish_action_authority()

                img = self._capture_frame()
                if img is None:
                    continue

                detection = detect_state_and_overlays(img, log_matches=self._match_trace)
                game_speed_guard = getattr(self, "_game_speed_guard", None)
                if game_speed_guard is not None:
                    game_speed_guard.set_target(
                        self._supervisor.game_speed_target,
                        wave=self._last_wave_value,
                    )
                if detection.get("state") == "HOME_SCREEN":
                    home_evidence = detect_home_battle_control(img)
                    detection["home_battle_control"] = home_evidence.control.value
                    log(
                        "[BATTLE] Home control="
                        f"{home_evidence.control.value} source={home_evidence.source} "
                        f"confidence={home_evidence.confidence:.2f}",
                        "DEBUG",
                    )

                # This passive sidecar sees exact Home NEW_BATTLE before any
                # later setup or Home handler can dispatch an action. It never
                # returns a control decision and remains active during Pause.
                self._observe_player_save_audit_screen(detection)

                self._mission_mgr.observe_detection(detection)
                self._observe_no_strategy_frame(img, detection)

                self._process_strategy_boundary(detection)
                self._observe_strategy_gate_boundary(detection)

                # Update battle identity independently of screen navigation,
                # then give a genuinely initializing strategy exclusive tap
                # authority. No overlay handler, recovery tap, mission action,
                # or blind tapper may run before this gate clears.
                battle_started = self._mission_mgr.maybe_run_start(detection)
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
                    if save_coordinator is not None:
                        carry_action_authorized = bool(
                            AUTOMATION.mode is not ExecMode.WAIT
                            and self._runtime_action_guard(
                                action_class=(
                                    RuntimeActionClass.LIFECYCLE_ACTION
                                )
                            )
                        )
                        bound = save_coordinator.bind_running(
                            battle_started=True,
                            stable_running=(
                                str(detection.get("state") or "").upper()
                                == "RUNNING"
                            ),
                            action_authorized=carry_action_authorized,
                        )
                        if bound:
                            self._mission_mgr.ctx.data[
                                "player_save_preflight_coordinator"
                            ] = save_coordinator
                continuity_pending = False
                activity_continuity = getattr(
                    self,
                    "_activity_continuity",
                    None,
                )
                if activity_continuity is not None:
                    initialization_blocks_history = (
                        self._mission_mgr.run_initialization_pending()
                    )
                    session_preflight_blocks_history = bool(
                        not initialization_blocks_history
                        and self._mission_mgr.session_preflight_pending()
                    )
                    post_retry_poll_allowed = not (
                        initialization_blocks_history
                        or session_preflight_blocks_history
                    )
                    continuity_needed = activity_continuity.needs_check(
                        detection,
                        post_retry_poll_allowed=post_retry_poll_allowed,
                    )
                    continuity_holds = (
                        (
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
                            owner=AuthorityHold.ACTIVITY_CONTINUITY,
                        ).allowed,
                        action_guard_fn=lambda: self._runtime_action_guard(
                            owner=AuthorityHold.ACTIVITY_CONTINUITY
                        ),
                        post_retry_poll_allowed=post_retry_poll_allowed,
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
                self._observe_exclusive_validation_battle_start(
                    detection,
                    battle_started=battle_started is True,
                )
                if (
                    not continuity_pending
                    and self._advance_exclusive_validation_launch(
                        img,
                        detection,
                        battle_started=battle_started is True,
                    )
                ):
                    continue
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
                if exclusive_validation_ownership_hold:
                    initialization_pending = False
                    session_preflight_pending = False
                if (
                    initialization_pending or session_preflight_pending
                ) and not self._mission_mgr.ctx.data.get(
                    "exclusive_validation_battle"
                ):
                    if self._claim_proactive_gate_waivers(for_home_setup=False):
                        initialization_pending = (
                            self._mission_mgr.run_initialization_pending()
                        )
                        session_preflight_pending = (
                            not initialization_pending
                            and self._mission_mgr.session_preflight_pending()
                        )
                if (
                    not continuity_pending
                    and self._advance_exclusive_validation(detection)
                ):
                    continue
                session_preflight_terminally_blocked = bool(
                    session_preflight_pending
                    and self._mission_mgr.session_preflight_terminally_blocked()
                )
                if session_preflight_terminally_blocked:
                    self._handle_terminal_session_gate_decision()
                    session_preflight_pending = (
                        not initialization_pending
                        and self._mission_mgr.session_preflight_pending()
                    )
                    session_preflight_terminally_blocked = bool(
                        session_preflight_pending
                        and self._mission_mgr.session_preflight_terminally_blocked()
                    )
                self._sync_strategy_action_gate(
                    terminally_blocked=session_preflight_terminally_blocked
                )
                if continuity_pending:
                    authority_holds = (
                        AuthorityHoldState(
                            AuthorityHold.ACTIVITY_CONTINUITY,
                            "activity continuity owns its verification route",
                        ),
                    )
                elif exclusive_validation_ownership_hold:
                    authority_holds = (
                        AuthorityHoldState(
                            AuthorityHold.EXCLUSIVE_OWNERSHIP,
                            "exclusive validation ownership is unresolved",
                        ),
                    )
                elif self._exclusive_validation_in_progress():
                    authority_holds = (
                        AuthorityHoldState(
                            AuthorityHold.EXCLUSIVE_VALIDATION,
                            "exclusive strategy validation owns the screen",
                        ),
                    )
                elif initialization_pending:
                    authority_holds = (
                        AuthorityHoldState(
                            AuthorityHold.RUN_INITIALIZATION,
                            "run initialization owns strategy input",
                        ),
                    )
                elif (
                    session_preflight_pending
                    and not session_preflight_terminally_blocked
                ):
                    authority_holds = (
                        AuthorityHoldState(
                            AuthorityHold.SESSION_PREFLIGHT,
                            "session preflight owns strategy validation input",
                        ),
                    )
                else:
                    authority_holds = ()
                self._update_action_authority(
                    detection=detection,
                    holds=authority_holds,
                )
                self._publish_action_authority()

                strategy_action_allowed = self._action_decision(
                    RuntimeActionClass.STRATEGY_ACTION
                ).allowed
                lifecycle_action_allowed = self._action_decision(
                    RuntimeActionClass.LIFECYCLE_ACTION
                ).allowed
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
                    if session_preflight_terminally_blocked:
                        if not self._session_preflight_terminal_blocked_logged:
                            log(
                                "[SESSION_PREFLIGHT] Validation is blocked; "
                                "strategy actions remain blocked while safe runtime "
                                "handlers stay available",
                                "WARN",
                                console=True,
                            )
                            self._session_preflight_terminal_blocked_logged = True
                    else:
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
                        if (
                            not continuity_pending
                            and self._action_decision(
                                RuntimeActionClass.STRATEGY_ACTION,
                                owner=AuthorityHold.SESSION_PREFLIGHT,
                            ).allowed
                        ):
                            if self._mission_mgr.session_preflight_repair_required():
                                if self._action_decision(
                                    RuntimeActionClass.LIFECYCLE_ACTION,
                                    owner=AuthorityHold.SESSION_PREFLIGHT,
                                ).allowed:
                                    self._attempt_session_preflight_repair(
                                        detection
                                    )
                            else:
                                self._run_owned_strategy_tick(
                                    AuthorityHold.SESSION_PREFLIGHT,
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
                    and self._advance_exclusive_validation(detection)
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
                perk_timeline_handled = False
                if self._perk_timeline_enabled():
                    perk_timeline_handled = self._perk_timeline().handle(
                        img,
                        detection,
                        wave=wave_val,
                        actions_allowed=strategy_action_allowed,
                        action_guard_fn=self._runtime_action_guard,
                    )
                    self._observe_player_save_audit_perk_mapping_evidence()
                if perk_timeline_handled:
                    # The observer owns its Perks modal route. Re-enter through
                    # capture before any consumer uses the pre-route frame.
                    continue
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

                    if "TOURNAMENT" in secondary:
                        try:
                            if AUTOMATION.mode != ExecMode.WAIT:
                                AUTOMATION.mode = ExecMode.WAIT
                                log("[CTRL] Tournament detected — ExecMode set to WAIT", "INFO")
                        except Exception:
                            pass

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
            self._update_action_authority(shutting_down=True)
            stop_blind_gem_tapper()
            self._publish_action_authority(runtime_active=False)
            collector = getattr(self, "_player_save_audit_collector", None)
            if collector is not None:
                try:
                    collector.close(wait=False)
                except Exception:
                    pass
            log("Exited cleanly.", "INFO")

    def _capture_frame(self) -> Optional[Frame]:
        """Capture a new frame from the device, retrying once if ADB reconnects."""
        coordinator = self._adb_connection_coordinator
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
        if result.frame is not None:
            coordinator.record_capture_success()
            return result.frame

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
                carry is None
                or carry.state is not CarriedEvidenceState.INVALIDATED
            )
            if save_preflight is not None and save_still_valid:
                setup_kwargs["save_decisions"] = dict(
                    save_preflight.decisions
                )
                if coordinator is not None:
                    setup_kwargs["snapshot_invalidation_fn"] = (
                        coordinator.invalidate
                    )
            setup = run_gc_no_battle_setup(requirements, **setup_kwargs)
            if (
                setup.complete
                or setup.interrupted
                or (
                    getattr(setup, "status", None)
                    is not GcNoBattleSetupStatus.FAILED
                )
                or attempt == HOME_SETUP_MAX_ATTEMPTS
            ):
                return setup

            check_id = setup.failed_check or "startup_setup"
            log(
                f"[GC_NO_BATTLE] Home setup attempt {attempt}/"
                f"{HOME_SETUP_MAX_ATTEMPTS} failed at {check_id}: "
                f"{setup.reason}; retrying the complete setup from fresh "
                "Home evidence",
                "WARN",
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

    def _attempt_session_preflight_repair(
        self,
        detection: Dict[str, Any],
    ) -> None:
        """End one GC run so Home-only settings can be corrected safely."""

        if not self._auto_start_enabled:
            if not self._session_preflight_repair_denial_logged:
                log(
                    "[SESSION_PREFLIGHT] Repair requires automatic restart, but "
                    "automatic Battle start is disabled; automation remains blocked",
                    "ERROR",
                )
                self._session_preflight_repair_denial_logged = True
            return
        if detection.get("state") != "RUNNING":
            return
        if not self._mission_mgr.begin_session_preflight_repair():
            return

        attached_authorization = (
            self._mission_mgr.attached_validation_requested()
        )
        log_action_intent(
            "Restarting the battle for Home-only strategy repair",
            reason=(
                "the operator authorized the attached-battle restart after "
                "read-only validation"
                if attached_authorization
                else "repeated session validation established the configured "
                "guarded recovery threshold"
            ),
            detail="[SESSION_PREFLIGHT] repair_transition=surrender_to_game_over",
            console=True,
        )
        if not surrender_run(
            action_guard=self._session_preflight_repair_action_guard
        ):
            reason = "guarded Surrender did not reach Game Over"
            self._mission_mgr.fail_session_preflight_repair(reason)
            log(
                f"[SESSION_PREFLIGHT] {reason}; automation remains blocked",
                "ERROR",
                console=True,
            )
            log_result(
                f"Battle restart for strategy repair failed — {reason}",
                detail="[SESSION_PREFLIGHT] repair_transition=failed",
                console=True,
            )
            return
        log_result(
            "Battle ended for strategy repair — Game Over reached",
            detail=(
                "[SESSION_PREFLIGHT] repair_transition=game_over; "
                "next_step=return_home_and_revalidate"
            ),
            console=True,
        )

    def _session_preflight_repair_action_guard(self) -> bool:
        """Revalidate the separately authorized repair owner's lifecycle lease."""

        return self._runtime_action_guard(
            action_class=RuntimeActionClass.LIFECYCLE_ACTION,
            owner=AuthorityHold.SESSION_PREFLIGHT,
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
        self._update_action_authority(
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

    def _game_speed_priority_ready(
        self,
        *,
        initialization_pending: bool,
    ) -> bool:
        """Keep Farm's urgent EHLS/EALS purchases ahead of game speed."""

        if not initialization_pending:
            return True
        mv = self._mission_mgr.ctx.data.get("mission_vars", {})
        level_skip_keys = {"ehls_completed", "eals_completed"}
        if not level_skip_keys.intersection(mv):
            return True
        return all(bool(mv.get(key)) for key in level_skip_keys)

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
        state = str(detection.get("state") or "UNKNOWN")
        if state not in RECOVERABLE_INVENTORY_STATES:
            return False
        if self._supervisor.is_paused:
            return False
        if time.time() < getattr(self, "_no_strategy_inventory_retry_at", 0.0):
            return state != "RUNNING"
        if stop_blind_gem_tapper():
            self._blind_tapper_suspended = True

        log(
            "[NO_STRATEGY] Starting automatic read-only in-battle inventory",
            "INFO",
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
        elif result.status is NoStrategyInventoryStatus.PAUSED:
            self._no_strategy_inventory_retry_at = 0.0
            log(
                "[NO_STRATEGY] In-battle inventory paused; it will resume "
                "without sending cleanup input",
                "INFO",
                console=True,
            )
        elif result.status is NoStrategyInventoryStatus.BATTLE_ENDED:
            self._no_strategy_inventory_retry_at = 0.0
            log(
                "[NO_STRATEGY] Battle ended during in-battle inventory; "
                "Home-only capture will continue at the natural boundary",
                "INFO",
                console=True,
            )
        else:
            self._no_strategy_inventory_retry_at = time.time() + 60.0
            log(
                f"[NO_STRATEGY] Automatic in-battle inventory failed: "
                f"{result.reason}. It will retry after 60 seconds.",
                "WARN",
                console=True,
            )
        return True

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
                self._no_strategy_post_run_stage = "perks"
                self._no_strategy_post_run_retry_at = 0.0
                stage = "perks"
                new_state = "HOME_SCREEN"
                img = lock_result.home_screenshot

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
                self._persist_pending_no_strategy_record(finalized=True)
                if AUTOMATION.mode is ExecMode.WAIT:
                    self._no_strategy_post_run_stage = "complete_wait"
                    self._no_strategy_post_run_retry_at = 0.0
                    log(
                        "[NO_STRATEGY] Post-run inventory complete; WAIT is "
                        "holding the verified Home boundary",
                        "INFO",
                        console=True,
                    )
                    return True
                self._release_no_strategy_post_run()
                log(
                    "[NO_STRATEGY] Post-run inventory complete; the next-battle "
                    "path is released",
                    "INFO",
                    console=True,
                )
                return True
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
            self._no_strategy_post_run_retry_at = time.time() + 60.0
            log(
                f"[NO_STRATEGY] Post-run inventory is still holding the next "
                f"battle: {exc}. It will retry after 60 seconds.",
                "WARN",
                console=True,
            )
            return True
        except Exception as exc:
            self._no_strategy_post_run_retry_at = time.time() + 60.0
            log(
                f"[NO_STRATEGY] Could not persist post-run inventory: {exc}. "
                "The next battle remains held.",
                "ERROR",
                console=True,
            )
            return True
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
        fields = observed.get("fields") if isinstance(observed, Mapping) else {}
        lock_field = fields.get("free_upgrade_locks", {}) if isinstance(fields, Mapping) else {}
        lock_status = lock_field.get("status") if isinstance(lock_field, Mapping) else None
        self._pending_no_strategy_record = record
        self._no_strategy_post_run_stage = (
            "perks"
            if lock_status in {"observed", "evidence_captured", "unavailable"}
            else "locks"
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
        self._no_strategy_inventory_complete = False
        self._no_strategy_inventory_retry_at = 0.0
        self._no_strategy_observer.reset()

    def _handle_primary_states(
        self,
        new_state: str,
        overlays: Set[str],
        img: Frame,
    ) -> None:
        """Dispatch handlers for top-level UI states and overlay-driven events."""
        selector = getattr(self, "_run_perk_selector", None)
        if selector is not None:
            selector.observe_state(new_state)
        if new_state != "GAME_OVER":
            self._exclusive_validation_terminal_hold = None
        if new_state != "HOME_SCREEN":
            self._last_home_policy_signature = None
        self._recover_no_strategy_post_run(new_state, img)
        if self._handle_no_strategy_post_run(new_state, img):
            return
        if new_state == "RUNNING":
            self._tournament_results_captured = False
        if (
            self._handler_enabled("daily_gem")
            and self._handle_daily_gem_if_due(new_state, overlays)
        ):
            # The handler navigates through Store and may return to a different
            # screen. Do not act on the stale pre-handler detection this tick.
            return
        if (
            self._handler_enabled("mission_rewards")
            and self._handle_mission_rewards_if_due(new_state, img, overlays)
        ):
            # The handler traverses several panels and restores RUNNING. Avoid
            # dispatching against the frame captured before that navigation.
            return
        if (
            new_state == "HOME_SCREEN"
            and "HOME_AD_GEMS_AVAILABLE" in overlays
            and self._handler_enabled("ad_gem")
        ):
            # Collect before Home handling can start or resume a battle.  The
            # handler revalidates the control against a fresh frame, so this
            # overlay is scheduling evidence rather than tap authority.
            handle_home_ad_gem()
            return

        if (
            new_state == "TOURNAMENT_RESULTS"
            and self._handler_enabled("game_over")
        ):
            if getattr(self, "_tournament_results_captured", False):
                return
            operation_id = new_operation_id()
            log_action_intent(
                "Capturing the finished Tournament",
                reason=(
                    "preserve its result before waiting for operator direction"
                ),
                detail="[TOURNAMENT_RESULTS] result=pending next_mode=WAIT",
                operation_id=operation_id,
            )
            log(
                "Detected TOURNAMENT_RESULTS. Capturing result without dismissing it.",
                "INFO",
                console=True,
            )
            if not self._supervisor.persist_mode("WAIT"):
                AUTOMATION.mode = ExecMode.WAIT
                log(
                    "[CTRL] Could not persist Tournament Results WAIT; "
                    "using in-memory WAIT",
                    "WARN",
                )
            record = handle_tournament_results(
                img,
                battle_context=self._terminal_battle_context(
                    "TOURNAMENT_RESULTS"
                ),
            )
            if record is not None:
                self._tournament_results_captured = True
                self._mission_mgr.on_game_over()
                self._status_reporter.reset_coin_rate_samples()
                self._strategy_boundary_confirmed = True
                self._apply_pending_strategy()
                log_result(
                    "Tournament finished — result saved; automation is waiting "
                    "on the Tournament Results screen (mode WAIT)",
                    detail=(
                        "[TOURNAMENT_RESULTS] result=completed "
                        f"tournament_id={record.get('tournament_id')} "
                        "next_mode=WAIT"
                    ),
                    operation_id=operation_id,
                )
            else:
                log_result(
                    "Tournament result capture failed — automation remains on "
                    "the Tournament Results screen in WAIT and will retry",
                    detail=(
                        "[TOURNAMENT_RESULTS] result=failed next_mode=WAIT "
                        "retry=true"
                    ),
                    operation_id=operation_id,
                )
            return

        if (
            new_state == "GAME_OVER"
            and self._handle_exclusive_validation_game_over()
        ):
            self._mission_mgr.on_game_over()
            self._mission_mgr.set_exclusive_validation_battle(False)
            self._status_reporter.reset_coin_rate_samples()
            self._strategy_boundary_confirmed = True
            self._apply_pending_strategy()
            return

        if new_state == "GAME_OVER" and self._handler_enabled("game_over"):
            log("Detected GAME_OVER. Executing handler.", "INFO", console=True)
            strategy = self._mission_mgr.strategy
            no_strategy_run = strategy is None
            observed_run_configuration = (
                self._no_strategy_observer.snapshot()
                if no_strategy_run
                else {}
            )
            if self._runtime_policy().get("game_over_mode") == "wait":
                if not self._supervisor.persist_mode("WAIT"):
                    # Preserve the safe in-memory behavior even if the control
                    # file cannot be updated. A later readable directive may
                    # still give the handler explicit operator direction.
                    AUTOMATION.mode = ExecMode.WAIT
                    log(
                        "[CTRL] Could not persist Tournament Game Over WAIT; "
                        "using in-memory WAIT",
                        "WARN",
                    )
            repair_in_progress = (
                self._mission_mgr.session_preflight_repair_in_progress()
            )
            boundary_finalized = False

            def finalize_run_boundary() -> None:
                nonlocal boundary_finalized
                if boundary_finalized:
                    return
                self._mission_mgr.on_game_over()
                self._status_reporter.reset_coin_rate_samples()
                self._strategy_boundary_confirmed = True
                self._apply_pending_strategy()
                boundary_finalized = True

            def sync_terminal_control() -> None:
                self._supervisor.apply_control()
                self._observe_strategy_request()

            def mark_retry_started() -> None:
                start_retry_activity_scope()

            if repair_in_progress:
                log(
                    "[SESSION_PREFLIGHT] Automation-owned repair Surrender "
                    "reached Game Over; skipping battle capture and returning Home",
                    "INFO",
                    console=True,
                )
            completed_record = handle_game_over(
                capture_stats=(
                    not repair_in_progress
                    and (not self._fast_game_over or no_strategy_run)
                ),
                control_sync=sync_terminal_control,
                before_terminal_action=finalize_run_boundary,
                after_retry_started=mark_retry_started,
                return_home_after_battle=(repair_in_progress or no_strategy_run),
                battle_context=self._terminal_battle_context(
                    "GAME_OVER",
                    observed_run_configuration=observed_run_configuration,
                ),
            )
            finalize_run_boundary()
            if no_strategy_run and completed_record is not None:
                self._pending_no_strategy_record = completed_record
                self._no_strategy_post_run_stage = "locks"
                self._no_strategy_post_run_retry_at = 0.0
                log(
                    "[NO_STRATEGY] Battle record is awaiting read-only Home "
                    "inventory before another battle may start",
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
                self._no_strategy_inventory_complete = False
                self._no_strategy_inventory_retry_at = 0.0
                self._no_strategy_observer.reset()
        elif new_state == "HOME_SCREEN":
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
            home_control = detect_home_battle_control(img).control
            requirements = self._mission_mgr.no_battle_setup_requirements()
            if (
                (self._auto_start_enabled or home_preflight_enabled)
                and home_control is HomeBattleControl.NEW_BATTLE
                and requirements
                and (
                    exclusive_validation is None
                    or exclusive_request_pending
                )
            ):
                self._claim_proactive_gate_waivers(
                    for_home_setup=True,
                    requirements=requirements,
                )
                if exclusive_validation is None:
                    directive = self._matching_gate_decision("home_setup")
                    if directive and not self._apply_gate_decision(
                        directive,
                        phase="home_setup",
                    ):
                        return
                waivers = merge_profile_skip_waivers(
                    requirements,
                    getattr(self, "_startup_gate_waivers", {}),
                )
                save_preflight = self._acquire_player_save_home_preflight(
                    requirements,
                    screenshot=img,
                )
                if save_preflight is not None and not save_preflight.ready:
                    return
                setup = self._run_home_setup_attempts(
                    requirements,
                    screenshot=img,
                    waivers=waivers,
                    save_preflight=save_preflight,
                )
                if setup.interrupted:
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
                    )
                    if directive and directive.get("status") == "pending":
                        directive = (
                            self._prompt_for_gate_decision(directive) or directive
                        )
                    if not directive or not self._apply_gate_decision(
                        directive,
                        phase="home_setup",
                    ):
                        log(
                            f"[GC_NO_BATTLE] Blocking Battle start at "
                            f"{check_id}: {setup.reason}",
                            "ERROR",
                        )
                        return
                    fresh = self._capture_frame()
                    if fresh is None:
                        return
                    waivers = merge_profile_skip_waivers(
                        requirements,
                        getattr(self, "_startup_gate_waivers", {}),
                    )
                    setup = self._run_home_setup_attempts(
                        requirements,
                        screenshot=fresh,
                        waivers=waivers,
                        save_preflight=save_preflight,
                    )
                    if setup.interrupted:
                        return
                    if not setup.complete:
                        next_check = setup.failed_check or "startup_setup"
                        self._publish_gate_decision(
                            phase="home_setup",
                            check_id=next_check,
                            reason=setup.reason,
                            expected=requirements.get(next_check),
                        )
                        log(
                            f"[GC_NO_BATTLE] Blocking Battle start at "
                            f"{next_check}: {setup.reason}",
                            "ERROR",
                        )
                        return
                setup_evidence = dict(setup.evidence)
                if save_preflight is not None:
                    setup_evidence["player_save_preflight"] = (
                        save_preflight.as_dict()
                    )
                if waivers:
                    self._mission_mgr.mark_no_battle_setup_complete(
                        setup_evidence,
                        waivers=waivers,
                    )
                else:
                    self._mission_mgr.mark_no_battle_setup_complete(
                        setup_evidence
                    )
                self._startup_gate_waivers = {}
            if self._maybe_start_exclusive_validation(
                home_control=home_control,
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
                restart_enabled = (
                    self._auto_start_enabled
                    and AUTOMATION.mode is not ExecMode.WAIT
                )
                if self._auto_start_enabled and not restart_enabled:
                    log(
                        "[HOME] WAIT mode — holding Home without starting a battle",
                        "INFO",
                    )
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
                if (
                    carry is not None
                    and carry.state
                    in {
                        CarriedEvidenceState.LAUNCH_DISPATCHED,
                        CarriedEvidenceState.BOUND_RUNNING,
                    }
                ):
                    save_coordinator.invalidate(
                        "unrelated_later_home_launch_boundary"
                    )
                launch_authorized = True
                if carry_pending and restart_enabled:
                    launch_authorized = self._runtime_action_guard(
                        action_class=RuntimeActionClass.LIFECYCLE_ACTION,
                    )
                    if not launch_authorized:
                        restart_enabled = False
                launched = (
                    handle_home_screen(
                        restart_enabled=restart_enabled,
                        require_new_battle=True,
                    )
                    if carry_pending
                    else handle_home_screen(restart_enabled=restart_enabled)
                )
                if carry_pending:
                    if restart_enabled:
                        save_coordinator.mark_runtime_launch(
                            control=home_control,
                            action_authorized=launch_authorized,
                            dispatched=bool(launched),
                        )
                    else:
                        save_coordinator.invalidate(
                            "wait_pause_stop_or_manual_launch_boundary"
                        )
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
                ):
                    save_coordinator.invalidate("home_handler_disabled")

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
            )
        else:
            result = handle_mission_rewards(
                screenshot=img,
                claim_daily_missions=claim_daily_missions,
                event_inventory_callback=(
                    self._event_mission_tracker.record_inventory
                ),
            )
        if result == MissionRewardResult.FAILED:
            self._mission_reward_scheduler.mark_failed(wall_now=wall_now)
        elif result != MissionRewardResult.INTERRUPTED:
            self._mission_reward_scheduler.mark_completed(wall_now=wall_now)
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
