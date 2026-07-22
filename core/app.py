from __future__ import annotations

"""Primary application orchestration loop for the automation runtime."""

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Dict, Any, Mapping, Set, Tuple

import numpy as np
from numpy.typing import NDArray

from utils.logger import log, set_mission_log_path
from core.watchdog import watchdog_process_check, ensure_adb_connected
from core.adb_target_session import AdbTargetSession
from core.ss_capture import capture_and_save_screenshot
from core.state_detector import detect_state_and_overlays
from core.automation_supervisor import AutomationSupervisor
from core.daily_gem_scheduler import DailyGemScheduler
from core.event_mission_tracker import EventMissionTracker, format_warning
from core.home_battle import detect_home_battle_control
from core.battle_lifecycle import HomeBattleControl
from core.gc_no_battle_setup import run_gc_no_battle_setup
from core.gate_decisions import (
    build_gate_decision_options,
    startup_gate_check_catalog,
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
from core.run_state import AUTOMATION, ExecMode
from core.app_setup import AppConfig
from core.status_report import StateChangeTracker, StatusReporter
from core.recovery import handle_unknown_state, update_unknown_state
from core.run_controls import surrender_run
from automation.missions.manager import MissionManager
from automation.missions import get_mission
from automation.missions.yaml_mission import YamlMission
from automation.strategies import get_strategy
from handlers.game_over_handler import handle_game_over
from handlers.tournament_result_handler import handle_tournament_results
from handlers.home_screen_handler import handle_home_screen
from handlers.ad_gem_handler import (
    handle_ad_gem,
    handle_home_ad_gem,
    start_blind_gem_tapper,
    stop_blind_gem_tapper,
)
from handlers.daily_gem_handler import DailyGemResult, handle_daily_gem
from handlers.dismiss_uw_detail import handle_upgrade_detail_popup
from handlers.mission_reward_handler import MissionRewardResult, handle_mission_rewards
from utils.wave_detector import detect_wave_number_from_image


Frame = NDArray[np.uint8]


class App:
    """Main automation orchestrator wrapping capture → detect → dispatch."""

    def __init__(
        self,
        config: AppConfig,
        *,
        adb_target_session: Optional[AdbTargetSession] = None,
        gate_decision_prompt: Optional[
            Callable[[Mapping[str, Any]], Optional[str]]
        ] = None,
    ) -> None:
        self._config = config
        self._adb_target_session = adb_target_session
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
                config.startup_gate_policy == "next_run"
            ),
        )
        self._mission_mgr.start()
        self._gate_decision_prompt = gate_decision_prompt
        self._gate_prompted_request_id: Optional[str] = None
        self._startup_gate_waivers: Dict[str, Dict[str, Any]] = {}
        self._last_strategy_request: Optional[Tuple[str, object, str]] = None
        self._pending_strategy_request: Optional[Tuple[str, object, str]] = None
        self._strategy_boundary_confirmed = False
        self._observe_strategy_request()
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

        self._match_trace = config.match_trace
        self._auto_start_enabled = config.auto_start_enabled

        # Structured battle records are the default for every run, including
        # mission-driven runs. Only the explicit fast flag may skip capture;
        # the legacy full flag remains an override when both are supplied.
        self._fast_game_over = config.fast_game_over and not config.full_game_over

        self._last_wave_value: Optional[int] = None
        self._last_wave_conf: float = -1.0
        self._last_wave_ts: float = 0.0
        self._blind_tapper_suspended = False
        self._tournament_results_captured = False
        self._run_initialization_gate_logged = False
        self._session_preflight_gate_logged = False
        self._session_preflight_terminal_blocked_logged = False
        self._session_preflight_repair_denial_logged = False
        rollover_state = Path(config.control_file).parent / "daily_gem_state.json"
        self._daily_gem_scheduler = DailyGemScheduler(rollover_state)
        self._mission_reward_scheduler = MissionRewardScheduler()
        event_mission_state = (
            Path(config.control_file).parent / "event_mission_tracker.json"
        )
        self._event_mission_tracker = EventMissionTracker(event_mission_state)

    def _runtime_policy(self) -> Dict[str, Any]:
        strategy = self._mission_mgr.strategy
        if not strategy:
            return {}
        try:
            policy = strategy.runtime_policy()
        except Exception:
            return {}
        return dict(policy) if isinstance(policy, Mapping) else {}

    def _current_strategy_name(self) -> str:
        strategy = self._mission_mgr.strategy
        return str(strategy.name if strategy else "none").strip().lower()

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
    ) -> Optional[Dict[str, Any]]:
        options = build_gate_decision_options(
            check_id,
            self._mission_mgr.gate_fallbacks(check_id),
            advisory=not blocking,
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
        else:
            return False
        self._supervisor.consume_gate_decision(
            request_id,
            completion_reason=completion_reason,
        )
        log(
            f"[GATE_DECISION] {completion_reason}",
            "WARN" if action == "waive" else "INFO",
            console=True,
        )
        return True

    def _handle_session_preflight_advisory(self) -> None:
        """Publish a non-blocking decision for an observer-only mismatch."""

        checks = self._mission_mgr.session_preflight_failure_checks()
        check_id = checks[0] if checks else "session_preflight"
        directive = self._matching_gate_decision("session_preflight")
        if directive is None:
            requirements = self._mission_mgr.strategy.session_preflight_requirements()
            reason = str(
                self._mission_mgr.ctx.data.get("mission_vars", {}).get(
                    "gc_session_preflight_last_reason",
                    "read-only session preflight mismatch",
                )
            )
            directive = self._publish_gate_decision(
                phase="session_preflight",
                check_id=check_id,
                reason=(
                    f"{reason}. Tournament result capture remains active; "
                    "this warning does not block observation."
                ),
                expected=requirements.get(check_id),
                blocking=False,
            )
            if directive and directive.get("status") == "pending":
                directive = self._prompt_for_gate_decision(directive) or directive
        if directive:
            self._apply_gate_decision(directive, phase="session_preflight")

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

    def _process_strategy_boundary(self, detection: Mapping[str, Any]) -> None:
        state = str(detection.get("state") or "UNKNOWN").upper()
        control = HomeBattleControl.parse(
            detection.get("home_battle_control", "UNKNOWN")
        )
        if state in {"GAME_OVER", "TOURNAMENT_RESULTS"}:
            self._strategy_boundary_confirmed = True
            return
        if state in {"HOME", "HOME_SCREEN"}:
            if control is HomeBattleControl.NEW_BATTLE:
                self._strategy_boundary_confirmed = True
            elif control is HomeBattleControl.RESUME_BATTLE:
                self._strategy_boundary_confirmed = False
        elif state == "WORKSHOP":
            # Workshop is not available from an active or resumable battle.
            # This is authoritative no-battle evidence even when the operator
            # navigated here manually while Pause blocks runtime actions.
            self._strategy_boundary_confirmed = True

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
        self._config.strategy_name = requested_name
        self._pending_strategy_request = None
        self._run_initialization_gate_logged = False
        self._session_preflight_gate_logged = False
        self._session_preflight_terminal_blocked_logged = False
        self._session_preflight_repair_denial_logged = False
        self._startup_gate_waivers = {}

    def _handler_enabled(self, name: str) -> bool:
        """Honor an optional strategy handler allowlist; legacy plans allow all."""

        handlers = self._runtime_policy().get("handlers")
        if handlers is None:
            return True
        if not isinstance(handlers, (list, tuple, set, frozenset)):
            return False
        return name in {str(handler).strip() for handler in handlers}

    def run(self) -> None:
        log("Starting main heartbeat loop.", level="INFO", console=True)
        if self._config.wait_on_start:
            try:
                AUTOMATION.mode = ExecMode.WAIT
                log("[CTRL] Startup flag: ExecMode set to WAIT", "INFO", console=True)
            except Exception:
                pass

        self._supervisor.apply_control()
        self._observe_strategy_request()
        if self._supervisor.is_paused:
            if stop_blind_gem_tapper():
                self._blind_tapper_suspended = True
        if ensure_adb_connected():
            time.sleep(2)

        threading.Thread(target=watchdog_process_check, daemon=True).start()

        try:
            while True:
                # Control synchronization must not depend on a working ADB
                # connection. This both acknowledges Pause during an outage
                # and permits a paused live target handoff before capture.
                self._supervisor.apply_control()
                self._observe_strategy_request()
                is_paused = self._supervisor.is_paused
                if is_paused and stop_blind_gem_tapper():
                    self._blind_tapper_suspended = True

                img = self._capture_frame()
                if img is None:
                    continue

                detection = detect_state_and_overlays(img, log_matches=self._match_trace)
                if detection.get("state") == "HOME_SCREEN":
                    home_evidence = detect_home_battle_control(img)
                    detection["home_battle_control"] = home_evidence.control.value
                    log(
                        "[BATTLE] Home control="
                        f"{home_evidence.control.value} source={home_evidence.source} "
                        f"confidence={home_evidence.confidence:.2f}",
                        "DEBUG",
                    )

                self._process_strategy_boundary(detection)

                # Update battle identity independently of screen navigation,
                # then give a genuinely initializing strategy exclusive tap
                # authority. No overlay handler, recovery tap, mission action,
                # or blind tapper may run before this gate clears.
                battle_started = self._mission_mgr.maybe_run_start(detection)
                if battle_started is True:
                    self._claim_proactive_gate_waivers(
                        for_home_setup=False
                    )
                initialization_pending = self._mission_mgr.run_initialization_pending()
                session_preflight_pending = (
                    not initialization_pending
                    and self._mission_mgr.session_preflight_pending()
                )
                if initialization_pending or session_preflight_pending:
                    if self._claim_proactive_gate_waivers(for_home_setup=False):
                        initialization_pending = (
                            self._mission_mgr.run_initialization_pending()
                        )
                        session_preflight_pending = (
                            not initialization_pending
                            and self._mission_mgr.session_preflight_pending()
                        )
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
                advisory_pending = bool(
                    not session_preflight_pending
                    and not self._supervisor.is_paused
                    and self._runtime_policy().get("preflight_mismatch") == "notify"
                    and self._mission_mgr.session_preflight_advisory_pending()
                )
                if advisory_pending:
                    self._handle_session_preflight_advisory()
                    is_paused = self._supervisor.is_paused
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
                    if not is_paused:
                        self._mission_mgr.tick(img, detection, strategy_only=True)
                elif self._run_initialization_gate_logged:
                    strategy = self._mission_mgr.strategy
                    if (
                        strategy
                        and strategy.requires_run_initialization()
                        and strategy.is_run_initialization_complete(self._mission_mgr.ctx)
                    ):
                        log(
                            "[RUN_INIT] Startup gate complete; normal handlers may resume",
                            "INFO",
                            console=True,
                        )
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
                        if not is_paused:
                            if self._mission_mgr.session_preflight_repair_required():
                                self._attempt_session_preflight_repair(detection)
                            else:
                                self._mission_mgr.tick(img, detection, strategy_only=True)
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
                        log(
                            "[SESSION_PREFLIGHT] Validation complete; normal "
                            "handlers may resume",
                            "INFO",
                            console=True,
                        )
                    self._session_preflight_gate_logged = False
                    self._session_preflight_terminal_blocked_logged = False
                    self._session_preflight_repair_denial_logged = False

                actions_blocked = (
                    is_paused
                    or initialization_pending
                    or session_preflight_pending
                )
                safe_runtime_actions_blocked = (
                    is_paused
                    or initialization_pending
                    or (
                        session_preflight_pending
                        and not session_preflight_terminally_blocked
                    )
                )

                if not actions_blocked and self._handler_enabled("upgrade_detail"):
                    img, detection, overlay_cleared = self._resolve_upgrade_detail_overlay(
                        img,
                        detection,
                    )
                    if not overlay_cleared:
                        time.sleep(0.3)
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

                new_state, menu, secondary, overlays = self._normalise_detection(detection)

                if not actions_blocked:
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
                        not actions_blocked
                        and self._handler_enabled("coin_display")
                    ),
                )
                if self._handler_enabled("event_mission_warnings"):
                    self._emit_event_mission_warnings()

                if (
                    not actions_blocked
                    and self._handler_enabled("auto_return")
                    and self._runtime_policy().get("auto_return", True) is not False
                ):
                    self._supervisor.auto_return_check(img, new_state)

                if new_state == "UNKNOWN":
                    update_unknown_state(True)
                    if (
                        not actions_blocked
                        and self._handler_enabled("unknown_recovery")
                    ):
                        trigger_after = self._supervisor.auto_return_secs or 900
                        handle_unknown_state(img, trigger_after_s=trigger_after)
                else:
                    update_unknown_state(False)

                self._sync_floating_gem_tapper(
                    state=new_state,
                    actions_blocked=safe_runtime_actions_blocked,
                )

                if not actions_blocked:
                    self._mission_mgr.tick(img, detection)
                    self._handle_primary_states(new_state, overlays, img)
                elif not safe_runtime_actions_blocked:
                    self._handle_terminal_preflight_safe_actions(
                        new_state,
                        overlays,
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
            stop_blind_gem_tapper()
            log("Exited cleanly.", "INFO")

    def _capture_frame(self) -> Optional[Frame]:
        """Capture a new frame from the device, retrying once if ADB reconnects."""
        img = capture_and_save_screenshot(log_capture=False)
        if img is None:
            if ensure_adb_connected():
                time.sleep(1)
                img = capture_and_save_screenshot(log_capture=False)
            if img is None:
                log("Failed to capture screenshot.", level="FAIL")
                time.sleep(2)
                return None
        return img

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
            if not ensure_adb_connected():
                return False
            time.sleep(1)
            return capture_and_save_screenshot(log_capture=False) is not None

        try:
            return session.handoff(target, validate=validate)
        except Exception as exc:
            log(f"[CTRL] Unable to hand off ADB target to {target}: {exc}", "WARN")
            return False

    def _sync_floating_gem_tapper(
        self,
        *,
        state: str,
        actions_blocked: bool,
    ) -> None:
        """Suspend and resume only an ad-gem-triggered bounded tapper."""

        if state != "RUNNING" or actions_blocked:
            if stop_blind_gem_tapper():
                self._blind_tapper_suspended = True
            return
        if self._blind_tapper_suspended:
            start_blind_gem_tapper(duration=10, interval=1, blocking=False)
            self._blind_tapper_suspended = False

    def _handle_terminal_preflight_safe_actions(
        self,
        state: str,
        overlays: Set[str],
    ) -> None:
        """Allow bounded operational actions after a terminal preflight failure."""

        if (
            state == "RUNNING"
            and "AD_GEMS_AVAILABLE" in overlays
            and self._handler_enabled("ad_gem")
        ):
            handle_ad_gem()

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

        log(
            "[SESSION_PREFLIGHT] Ending the active GC run to repair Home-only settings",
            "WARN",
            console=True,
        )
        if not surrender_run():
            reason = "guarded Surrender did not reach Game Over"
            self._mission_mgr.fail_session_preflight_repair(reason)
            log(
                f"[SESSION_PREFLIGHT] {reason}; automation remains blocked",
                "ERROR",
                console=True,
            )
            return
        log(
            "[SESSION_PREFLIGHT] GC run ended; Game Over will return Home for repair",
            "INFO",
            console=True,
        )

    def _handle_primary_states(
        self,
        new_state: str,
        overlays: Set[str],
        img: Frame,
    ) -> None:
        """Dispatch handlers for top-level UI states and overlay-driven events."""
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
            strategy = self._mission_mgr.strategy
            record = handle_tournament_results(
                img,
                battle_context={
                    "strategy": strategy.name if strategy else None,
                    "terminal_state": "TOURNAMENT_RESULTS",
                    "run_configuration": (
                        strategy.run_configuration() if strategy else {}
                    ),
                    "last_wave": self._last_wave_value,
                    "last_wave_confidence": self._last_wave_conf,
                    "coin_rate_samples": self._status_reporter.coin_rate_samples,
                    "session_preflight_evidence": dict(
                        self._mission_mgr.ctx.data.get("mission_vars", {}).get(
                            "gc_session_preflight_evidence",
                            {},
                        )
                    ),
                },
            )
            if record is not None:
                self._tournament_results_captured = True
                self._mission_mgr.on_game_over()
                self._status_reporter.reset_coin_rate_samples()
                self._strategy_boundary_confirmed = True
                self._apply_pending_strategy()
            return

        if new_state == "GAME_OVER" and self._handler_enabled("game_over"):
            log("Detected GAME_OVER. Executing handler.", "INFO", console=True)
            strategy = self._mission_mgr.strategy
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

            handle_game_over(
                capture_stats=(not self._fast_game_over),
                control_sync=sync_terminal_control,
                before_terminal_action=finalize_run_boundary,
                return_home_after_battle=repair_in_progress,
                battle_context={
                    "strategy": strategy.name if strategy else None,
                    "terminal_state": "GAME_OVER",
                    "run_configuration": (
                        strategy.run_configuration() if strategy else {}
                    ),
                    "last_wave": self._last_wave_value,
                    "last_wave_confidence": self._last_wave_conf,
                    "coin_rate_samples": self._status_reporter.coin_rate_samples,
                    "session_preflight_evidence": dict(
                        self._mission_mgr.ctx.data.get("mission_vars", {}).get(
                            "gc_session_preflight_evidence",
                            {},
                        )
                    ),
                },
            )
            finalize_run_boundary()
        elif new_state == "HOME_SCREEN":
            home_handler_enabled = self._handler_enabled("home")
            home_preflight_enabled = bool(
                self._runtime_policy().get("home_preflight") is True
            )
            log("Detected HOME_SCREEN. Evaluating Home policy.", "INFO")
            home_control = detect_home_battle_control(img).control
            requirements = self._mission_mgr.no_battle_setup_requirements()
            if (
                (self._auto_start_enabled or home_preflight_enabled)
                and home_control is HomeBattleControl.NEW_BATTLE
                and requirements
            ):
                self._claim_proactive_gate_waivers(
                    for_home_setup=True,
                    requirements=requirements,
                )
                directive = self._matching_gate_decision("home_setup")
                if directive and not self._apply_gate_decision(
                    directive,
                    phase="home_setup",
                ):
                    return
                waivers = dict(getattr(self, "_startup_gate_waivers", {}))
                setup_kwargs: Dict[str, Any] = {"screenshot": img}
                if waivers:
                    setup_kwargs["waivers"] = waivers
                setup = run_gc_no_battle_setup(requirements, **setup_kwargs)
                if not setup.complete:
                    check_id = setup.failed_check or "startup_setup"
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
                    waivers = dict(
                        getattr(self, "_startup_gate_waivers", {})
                    )
                    retry_kwargs: Dict[str, Any] = {"screenshot": fresh}
                    if waivers:
                        retry_kwargs["waivers"] = waivers
                    setup = run_gc_no_battle_setup(requirements, **retry_kwargs)
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
                if waivers:
                    self._mission_mgr.mark_no_battle_setup_complete(
                        setup.evidence,
                        waivers=waivers,
                    )
                else:
                    self._mission_mgr.mark_no_battle_setup_complete(setup.evidence)
                self._startup_gate_waivers = {}
            if home_handler_enabled:
                handle_home_screen(restart_enabled=self._auto_start_enabled)
                self._mission_mgr.on_home()

        if (
            "AD_GEMS_AVAILABLE" in overlays
            and self._handler_enabled("ad_gem")
        ):
            handle_ad_gem()

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
            "INFO",
        )
        if stop_blind_gem_tapper():
            self._blind_tapper_suspended = True
        wall_now = datetime.now(timezone.utc)
        claim_daily_missions = daily_mission_claims_allowed(wall_now)
        result = handle_mission_rewards(
            screenshot=img,
            claim_daily_missions=claim_daily_missions,
            event_inventory_callback=self._event_mission_tracker.record_inventory,
        )
        if result == MissionRewardResult.FAILED:
            self._mission_reward_scheduler.mark_failed(wall_now=wall_now)
        else:
            self._mission_reward_scheduler.mark_completed(wall_now=wall_now)
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
            "INFO",
        )
        if stop_blind_gem_tapper():
            self._blind_tapper_suspended = True
        result = handle_daily_gem()
        if result in {DailyGemResult.CLAIMED, DailyGemResult.NOT_READY}:
            # Attribute a badge-triggered claim to the game day on which the
            # handler began. If navigation crosses UTC midnight, the new day
            # must remain eligible on the next loop.
            self._daily_gem_scheduler.mark_completed(result.value, now=attempted_at)
        else:
            self._daily_gem_scheduler.mark_failed()
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
                f"[STRATEGY] Loaded bundled strategy profile {strategy.name}",
                "INFO",
                console=True,
            )
        return strategy


__all__ = ["App"]
