"""
core/automation_supervisor.py

Encapsulates runtime automation control & small recoveries so `main.py` stays
focused on capture → detect → dispatch.

Features:
- Persistent control-file polling for explicit pause/resume/mode directives
- Coins/min display recovery and plausibility (jump) gate
- Auto "Return to Game" after sustained visibility, with logs and fallback match

Public usage (simplified):
    sup = AutomationSupervisor(...)
    sup.apply_control()        # updates AUTOMATION.state/mode from persistent directives
    paused = sup.is_paused
    coins_val, coins_conf, has_min, coins_eff = sup.process_coins(img, coins_val, coins_conf, has_min, debug_out)
    sup.auto_return_check(img, ui_state)
    state_label = sup.format_state(new_state)
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import os
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray

from core.control_directives import (
    ControlDirectiveError,
    ControlDirectiveStore,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_STRATEGY,
    MAXIMUM_GAME_SPEED_TARGET,
    normalize_automation_mode,
    normalize_emulator_location,
    normalize_emulator_maintenance,
    normalize_game_speed_target,
    normalize_interactive_development_lease,
    normalize_terminal_idle_timeout,
)
from core.control_model import (
    validate_battle_workflow,
    validate_manual_control,
    validate_process_restart_handoff,
    validate_setup_capture,
)
from core.strategy_profiles import is_configurable_strategy
from utils.logger import log as _write_log, log_action_intent, log_result
from core.run_state import AUTOMATION
from core.runtime_failure_policy import (
    RuntimeFailureDisposition,
    RuntimeFailureKind,
    decide_runtime_failure,
)
from core.input import tap_if_visible
from core.label_tapper import is_visible
from core.matcher import get_match as _get_match
from core.ss_capture import capture_and_save_screenshot
from utils.coin_detector import detect_coins_from_image, format_compact_decimal


Frame = NDArray[np.uint8]


_ALLOWED_STATES = {"RUNNING", "PAUSED", "STOPPED"}


def log(*args, **kwargs):
    """Keep control authority independent of recoverable log I/O failure."""

    try:
        return _write_log(*args, **kwargs)
    except Exception:
        return None


class AutomationSupervisor:
    def __init__(
        self,
        *,
        control_file: str,
        auto_return_secs: int = 0,
        auto_return_enabled: bool = True,
        auto_return_conf_threshold: float = 0.85,
        coins_toggle_cooldown: float = 15.0,
        coins_conf_floor: float = 60.0,
        coins_max_jump_factor: float = 8.0,
        coins_jump_conf_floor: float = 90.0,
        adb_port_handoff: Optional[Callable[[int], bool]] = None,
        emulator_location_handoff: Optional[
            Callable[[int, Mapping[str, object]], bool]
        ] = None,
    ) -> None:
        self.control_file = Path(control_file)
        self._control_store = ControlDirectiveStore(self.control_file)
        self._control_apply_lock = threading.RLock()
        self._control_read_failed = False
        self._control_read_failure_logged = False
        self._catastrophic_pause_latched = False
        self._catastrophic_pause_state_revision: object = None
        self._catastrophic_pause_reason: Optional[str] = None
        try:
            added_request_ids = (
                self._control_store.ensure_request_identities()
            )
        except ControlDirectiveError as exc:
            added_request_ids = {}
            log(
                "[CTRL] Could not add exact identities to legacy control "
                f"directives: {exc}",
                "WARN",
            )
        if added_request_ids:
            log(
                "[CTRL] Added exact request identities to legacy fields: "
                + ", ".join(sorted(added_request_ids)),
                "INFO",
            )
        initial_directives = self._load_control_directive()
        initial_state = initial_directives.get("state")
        if (
            isinstance(initial_state, str)
            and initial_state.strip().upper() in _ALLOWED_STATES
        ):
            # Close the startup window before App wiring, ADB connection, or
            # acknowledgement publication. Exact receipts are still recorded
            # only when apply_control() consumes the directive normally.
            AUTOMATION.state = initial_state.strip().upper()
        self._strategy_request = self._parse_strategy_request(initial_directives)
        self._strategy_active_battle_identity = (
            self._parse_strategy_active_battle_identity(initial_directives)
        )
        self._game_speed_target = self._parse_game_speed_target(
            initial_directives.get("game_speed_target")
        )
        self._gate_decision = self._parse_gate_decision(initial_directives)
        self._exclusive_validation = self._parse_exclusive_validation(
            initial_directives
        )
        self._startup_gate_waivers = self._parse_startup_gate_waivers(
            initial_directives
        )
        self._interactive_development_lease = (
            normalize_interactive_development_lease(
                initial_directives.get("interactive_development_lease")
            )
        )
        self._interactive_development_lease_error = bool(
            initial_directives.get("interactive_development_lease") is not None
            and self._interactive_development_lease is None
        )
        self._emulator_maintenance = normalize_emulator_maintenance(
            initial_directives.get("emulator_maintenance")
        )
        self._emulator_maintenance_error = bool(
            initial_directives.get("emulator_maintenance") is not None
            and self._emulator_maintenance is None
        )
        self._battle_workflow = validate_battle_workflow(
            initial_directives.get("battle_workflow")
        )
        self._battle_workflow_error = bool(
            initial_directives.get("battle_workflow") is not None
            and self._battle_workflow is None
        )
        self._process_restart_handoff = validate_process_restart_handoff(
            initial_directives.get("process_restart_handoff")
        )
        self._process_restart_handoff_error = bool(
            initial_directives.get("process_restart_handoff") is not None
            and self._process_restart_handoff is None
        )
        self._manual_control = validate_manual_control(
            initial_directives.get("manual_control")
        )
        self._manual_control_error = bool(
            initial_directives.get("manual_control") is not None
            and self._manual_control is None
        )
        self._setup_capture = validate_setup_capture(
            initial_directives.get("setup_capture")
        )
        self._setup_capture_error = bool(
            initial_directives.get("setup_capture") is not None
            and self._setup_capture is None
        )
        self._terminal_idle_timeout = normalize_terminal_idle_timeout(
            initial_directives.get("terminal_idle_timeout")
        )
        self._runtime_id = uuid4().hex
        self.auto_return_secs = max(0, int(auto_return_secs))
        self.auto_return_enabled = bool(auto_return_enabled)
        self.auto_return_conf_threshold = float(auto_return_conf_threshold)

        self.coins_toggle_cooldown = float(coins_toggle_cooldown)
        self.coins_conf_floor = float(coins_conf_floor)
        self.coins_max_jump_factor = Decimal(str(coins_max_jump_factor))
        self.coins_jump_conf_floor = float(coins_jump_conf_floor)
        self._adb_port_handoff = adb_port_handoff
        self._emulator_location_handoff = emulator_location_handoff
        self._applied_emulator_location: Optional[Dict[str, object]] = None

        # Internal state
        self._last_applied_state: Optional[str] = None
        self._last_state_directive_revision: object = None
        self._last_applied_mode: Optional[str] = None
        self._last_mode_directive_revision: object = None
        self._last_applied_game_speed_target: Optional[float] = None
        self._last_game_speed_target_revision: object = None
        self._pause_resume_at: Optional[float] = None
        self._timed_pause_expiry_pending: Optional[str] = None
        self._last_invalid_resume_at: object = None
        self._last_applied_adb_request: Optional[Tuple[int, object]] = None
        self._last_deferred_adb_request: Optional[Tuple[int, object]] = None
        self._last_invalid_emulator_location_request: object = None
        self._next_adb_handoff_attempt_at = 0.0
        self._unexpected_manual_yield_emergency = False
        self._control_acknowledgements: Dict[
            str, Optional[Dict[str, object]]
        ] = {
            "state": None,
            "mode": None,
            "game_speed_target": None,
            "adb_target": None,
            "strategy": None,
        }

        self._last_coins_toggle_ts: float = 0.0
        self._coins_has_min_miss: int = 0
        self._last_coins_val: Optional[Decimal] = None
        self._coins_pending_plausibility_val: Optional[Decimal] = None
        self._coins_ignore_plausibility_once: bool = False

        self._rtg_visible_since_ts: Optional[float] = None

    # ------------------------- control / pause -------------------------------
    @property
    def is_paused(self) -> bool:
        st = getattr(AUTOMATION, "state", None)
        return str(st) == "RunState.PAUSED" or st == "PAUSED"

    @property
    def strategy_request(self) -> Optional[Tuple[str, object, str]]:
        """Return the latest validated strategy directive and its identity."""

        return self._strategy_request

    @property
    def strategy_active_battle_identity(self) -> Optional[str]:
        """Return the canonical target of an active-battle Strategy request."""

        return self._strategy_active_battle_identity

    @property
    def game_speed_target(self) -> float:
        """Return the persistent exact or maximum-available speed target."""

        return self._game_speed_target

    @property
    def gate_decision(self) -> Optional[Dict[str, object]]:
        """Return the latest validated startup-gate decision directive."""

        return dict(self._gate_decision) if self._gate_decision else None

    @property
    def exclusive_validation(self) -> Dict[str, object]:
        """Return the durable one-shot strategy-validation ledger."""

        return {
            "schema_version": 1,
            "current_request_id": self._exclusive_validation.get(
                "current_request_id"
            ),
            "receipts": {
                request_id: dict(receipt)
                for request_id, receipt in self._exclusive_validation.get(
                    "receipts", {}
                ).items()
            },
        }

    @property
    def startup_gate_waivers(self) -> Dict[str, Dict[str, object]]:
        """Return proactive check waivers still waiting for an applicable run."""

        return {
            check_id: dict(waiver)
            for check_id, waiver in self._startup_gate_waivers.items()
        }

    @property
    def interactive_development_lease(self) -> Optional[Dict[str, object]]:
        """Return the latest validated cooperative development directive."""

        return (
            deepcopy(self._interactive_development_lease)
            if self._interactive_development_lease is not None
            else None
        )

    @property
    def interactive_development_lease_error(self) -> bool:
        """Return whether external-development input authority is malformed."""

        return bool(self._interactive_development_lease_error)

    @property
    def input_authority_error(self) -> Optional[str]:
        """Return the malformed directive that makes input ownership unknown."""

        for label, present in (
            (
                "interactive-development-lease",
                self._interactive_development_lease_error,
            ),
            ("manual-control", self._manual_control_error),
            ("battle-workflow", self._battle_workflow_error),
            ("process-restart-handoff", self._process_restart_handoff_error),
            ("setup-capture", self._setup_capture_error),
            ("emulator-maintenance", self._emulator_maintenance_error),
        ):
            if present:
                return label
        return None

    @property
    def emulator_maintenance(self) -> Optional[Dict[str, object]]:
        """Return the latest validated BlueStacks maintenance directive."""

        return (
            deepcopy(self._emulator_maintenance)
            if self._emulator_maintenance is not None
            else None
        )

    @property
    def battle_workflow(self) -> Optional[Dict[str, object]]:
        """Return the latest validated explicit battle workflow directive."""

        return deepcopy(self._battle_workflow) if self._battle_workflow else None

    @property
    def terminal_idle_timeout(self) -> Optional[Dict[str, object]]:
        """Return the exact terminal/Home hold currently in force."""

        return (
            deepcopy(self._terminal_idle_timeout)
            if self._terminal_idle_timeout is not None
            else None
        )

    @property
    def timed_pause_expiry_pending(self) -> Optional[str]:
        """Return the resumed State request awaiting screen disposition."""

        return self._timed_pause_expiry_pending

    def consume_timed_pause_expiry(self, request_id: str) -> bool:
        """Consume only the current process-local timed-Pause expiry."""

        if self._timed_pause_expiry_pending != str(request_id or "").strip():
            return False
        self._timed_pause_expiry_pending = None
        return True

    @property
    def process_restart_handoff(self) -> Optional[Dict[str, object]]:
        """Return the latest active-battle process-restart handoff."""

        return (
            deepcopy(self._process_restart_handoff)
            if self._process_restart_handoff
            else None
        )

    @property
    def process_restart_handoff_error(self) -> bool:
        """Return whether the active-battle restart handoff is malformed."""

        return bool(self._process_restart_handoff_error)

    @property
    def manual_control(self) -> Optional[Dict[str, object]]:
        """Return the latest validated Take/Return Control directive."""

        return deepcopy(self._manual_control) if self._manual_control else None

    @property
    def setup_capture(self) -> Optional[Dict[str, object]]:
        """Return the latest validated save-backed setup capture directive."""

        return deepcopy(self._setup_capture) if self._setup_capture else None

    @property
    def battle_workflow_error(self) -> bool:
        """Return whether a raw battle workflow exists but is malformed."""

        return bool(self._battle_workflow_error)

    @property
    def manual_control_error(self) -> bool:
        """Return whether a raw manual-control handoff exists but is malformed."""

        return bool(self._manual_control_error)

    @property
    def setup_capture_error(self) -> bool:
        """Return whether a raw setup capture exists but is malformed."""

        return bool(self._setup_capture_error)

    @property
    def control_state(self) -> str:
        """Return the currently applied operator run-state value."""

        state = getattr(AUTOMATION, "state", None)
        value = getattr(state, "value", state)
        return str(value or "UNKNOWN").strip().upper()

    @property
    def runtime_id(self) -> str:
        """Return this supervisor's process-lifetime runtime identity."""

        return self._runtime_id

    @property
    def dispatch_control_lock_path(self) -> str:
        """Return the shared ordering boundary for control and device input."""

        return str(self._control_store.dispatch_lock_path)

    @property
    def control_acknowledgements(self) -> Dict[str, object]:
        """Return exact runtime-applied directive receipts for publication."""

        return {
            "schema_version": 1,
            **deepcopy(self._control_acknowledgements),
        }

    @property
    def emulator_location(self) -> Optional[Dict[str, object]]:
        """Return the exact Windows emulator location applied by this runtime."""

        return deepcopy(self._applied_emulator_location)

    def acknowledge_strategy(
        self,
        strategy: str,
        request_id: object,
    ) -> bool:
        """Record Strategy application only for the exact current request."""

        normalized_strategy = str(strategy or "").strip().lower()
        normalized_request_id = str(request_id or "").strip()
        current = self._strategy_request
        if (
            current is None
            or normalized_strategy != str(current[0]).strip().lower()
            or normalized_request_id != str(current[1] or "").strip()
        ):
            return False
        return self._record_control_acknowledgement(
            "strategy",
            normalized_strategy,
            normalized_request_id,
        )

    def defer_strategy_request_to_next_boundary(
        self,
        strategy: str,
        request_id: object,
        *,
        source: str = "runtime-strategy-deferral",
    ) -> bool:
        """Persistently downshift one exact active-battle request."""

        normalized_strategy = str(strategy or "").strip().lower()
        normalized_request_id = str(request_id or "").strip()
        try:
            directives = self._control_store.defer_strategy_request_to_next_boundary(
                normalized_strategy,
                normalized_request_id,
                source=source,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                "[CTRL] Could not defer active-battle Strategy request to "
                f"the next boundary: {exc}",
                "WARN",
            )
            return False
        if directives is None:
            return False
        parsed = self._parse_strategy_request(directives)
        if parsed != (
            normalized_strategy,
            normalized_request_id,
            "next_boundary",
        ):
            return False
        self._strategy_request = parsed
        self._strategy_active_battle_identity = None
        return True

    @property
    def control_request_identity(self) -> Dict[str, object]:
        """Return the exact state and terminal-policy directives in force."""

        return {
            "state_request_id": deepcopy(
                self._last_state_directive_revision
            ),
            "mode_request_id": deepcopy(
                self._last_mode_directive_revision
            ),
        }

    @property
    def unexpected_manual_yield_emergency(self) -> bool:
        """Return whether a failed durable yield is enforcing local Pause."""

        return bool(self._unexpected_manual_yield_emergency)

    @property
    def catastrophic_pause_hold(self) -> Dict[str, object]:
        """Describe the local hold that requires one newer Enable request."""

        return {
            "active": bool(self._catastrophic_pause_latched),
            "reason": self._catastrophic_pause_reason,
        }

    def apply_control(self) -> bool:
        """Apply directives and report whether tracked control intent changed."""

        with AUTOMATION.quiescence_boundary():
            with self._control_apply_lock:
                return self._apply_control_locked()

    def _apply_control_locked(self) -> bool:
        """Apply one serialized persistent-control snapshot."""

        directives = self._load_control_directive()
        if self._control_read_failed:
            # Durable control is the sole operator authority.  If it cannot be
            # read, fail closed locally before any later device mutation; do
            # not manufacture an acknowledgement for unknown intent.  Keep a
            # process-local catastrophic hold so recovery of an older RUNNING
            # directive cannot silently resume input; a fresh Enable request
            # is required after control authority was unavailable.
            if self.control_state == "STOPPED":
                return False
            changed = self.control_state != "PAUSED"
            self._latch_catastrophic_pause(
                "durable control authority became unreadable"
            )
            self._last_applied_state = None
            self._apply_state("PAUSED")
            return changed
        control_directive_changed = False
        if directives:
            state_revision = (
                directives.get("state_request_id")
                or directives.get("state_updated_at")
                or directives.get("updated_at")
            )
            state_directive_changed = (
                state_revision is not None
                and state_revision != self._last_state_directive_revision
            )
            if (
                state_directive_changed
                and self._timed_pause_expiry_pending is not None
                and state_revision != self._timed_pause_expiry_pending
            ):
                self._timed_pause_expiry_pending = None
            requested_state = str(directives.get("state") or "").upper()
            if self._catastrophic_pause_latched and requested_state == "RUNNING":
                if self._catastrophic_pause_state_revision is None:
                    # The first readable RUNNING snapshot after authority was
                    # lost is the stale baseline, not proof of a new Enable.
                    self._catastrophic_pause_state_revision = state_revision
                elif (
                    state_revision is not None
                    and state_revision
                    != self._catastrophic_pause_state_revision
                ):
                    self._catastrophic_pause_latched = False
                    self._catastrophic_pause_state_revision = None
                    self._catastrophic_pause_reason = None
                    log(
                        "[RUNTIME_POLICY] Fresh Enable request released the "
                        "catastrophic control hold",
                        "INFO",
                        console=True,
                    )
            elif (
                self._catastrophic_pause_latched
                and requested_state == "PAUSED"
                and state_revision is not None
            ):
                # A successfully persisted catastrophic Pause becomes the
                # baseline that the later explicit Enable must replace.
                self._catastrophic_pause_state_revision = state_revision
            self._last_state_directive_revision = state_revision
            mode_revision = (
                directives.get("mode_request_id")
                or directives.get("mode_updated_at")
                or (
                    directives.get("updated_at")
                    if "mode" in directives
                    else None
                )
            )
            mode_directive_changed = bool(
                mode_revision is not None
                and mode_revision != self._last_mode_directive_revision
            )
            self._last_mode_directive_revision = mode_revision
            game_speed_target_revision = (
                directives.get("game_speed_target_request_id")
                or directives.get("game_speed_target_updated_at")
                or (
                    directives.get("updated_at")
                    if "game_speed_target" in directives
                    else None
                )
            )
            game_speed_target_changed = (
                game_speed_target_revision is not None
                and game_speed_target_revision
                != self._last_game_speed_target_revision
            )
            self._last_game_speed_target_revision = game_speed_target_revision
            control_directive_changed = (
                state_directive_changed
                or mode_directive_changed
                or game_speed_target_changed
            )
            self._strategy_request = self._parse_strategy_request(directives)
            self._strategy_active_battle_identity = (
                self._parse_strategy_active_battle_identity(directives)
            )
            self._gate_decision = self._parse_gate_decision(directives)
            self._exclusive_validation = self._parse_exclusive_validation(
                directives
            )
            self._startup_gate_waivers = self._parse_startup_gate_waivers(
                directives
            )
            self._interactive_development_lease = (
                normalize_interactive_development_lease(
                    directives.get("interactive_development_lease")
                )
            )
            self._interactive_development_lease_error = bool(
                directives.get("interactive_development_lease") is not None
                and self._interactive_development_lease is None
            )
            self._emulator_maintenance = normalize_emulator_maintenance(
                directives.get("emulator_maintenance")
            )
            self._emulator_maintenance_error = bool(
                directives.get("emulator_maintenance") is not None
                and self._emulator_maintenance is None
            )
            self._battle_workflow = validate_battle_workflow(
                directives.get("battle_workflow")
            )
            self._battle_workflow_error = bool(
                directives.get("battle_workflow") is not None
                and self._battle_workflow is None
            )
            self._process_restart_handoff = validate_process_restart_handoff(
                directives.get("process_restart_handoff")
            )
            self._process_restart_handoff_error = bool(
                directives.get("process_restart_handoff") is not None
                and self._process_restart_handoff is None
            )
            self._manual_control = validate_manual_control(
                directives.get("manual_control")
            )
            self._manual_control_error = bool(
                directives.get("manual_control") is not None
                and self._manual_control is None
            )
            self._setup_capture = validate_setup_capture(
                directives.get("setup_capture")
            )
            self._setup_capture_error = bool(
                directives.get("setup_capture") is not None
                and self._setup_capture is None
            )
            self._terminal_idle_timeout = normalize_terminal_idle_timeout(
                directives.get("terminal_idle_timeout")
            )
            if self._unexpected_manual_yield_emergency and self._manual_control:
                self._unexpected_manual_yield_emergency = False
            held_running = bool(
                self._catastrophic_pause_latched
                and requested_state == "RUNNING"
            )
            self._apply_state(
                "PAUSED" if held_running else directives.get("state"),
                acknowledge_unchanged=(
                    state_directive_changed and not held_running
                ),
                request_id=(
                    None
                    if held_running
                    else directives.get("state_request_id")
                ),
            )
            self._apply_mode(
                directives.get("mode"),
                acknowledge_unchanged=mode_directive_changed,
                request_id=directives.get("mode_request_id"),
            )
            self._apply_game_speed_target(
                directives.get("game_speed_target"),
                acknowledge_unchanged=game_speed_target_changed,
                request_id=directives.get(
                    "game_speed_target_request_id"
                ),
            )
            self._sync_pause_deadline(directives)
            self._apply_adb_port(
                directives.get("adb_port"),
                directives.get("adb_port_updated_at"),
                request_id=directives.get("adb_port_request_id"),
                emulator_location=directives.get("emulator_location"),
            )

        if self._unexpected_manual_yield_emergency:
            self._apply_state("PAUSED")

        self._auto_resume_if_needed()
        return control_directive_changed

    @staticmethod
    def _parse_strategy_request(
        directives: Dict[str, object],
    ) -> Optional[Tuple[str, object, str]]:
        strategy = str(directives.get("strategy") or "").strip().lower()
        if not is_configurable_strategy(strategy, allow_legacy_aliases=False):
            return None
        identity = directives.get("strategy_request_id") or directives.get(
            "strategy_updated_at"
        )
        apply_mode = str(
            directives.get("strategy_apply_mode") or "next_boundary"
        ).strip().lower()
        if apply_mode not in {"next_boundary", "active_battle"}:
            apply_mode = "next_boundary"
        return strategy, identity, apply_mode

    @staticmethod
    def _parse_strategy_active_battle_identity(
        directives: Mapping[str, object],
    ) -> Optional[str]:
        if str(
            directives.get("strategy_apply_mode") or "next_boundary"
        ).strip().lower() != "active_battle":
            return None
        identity = str(
            directives.get("strategy_active_battle_identity") or ""
        ).strip().lower()
        if len(identity) != 64 or any(
            character not in "0123456789abcdef" for character in identity
        ):
            return None
        return identity

    @staticmethod
    def _parse_game_speed_target(value: object) -> float:
        try:
            return normalize_game_speed_target(value)
        except ValueError:
            return MAXIMUM_GAME_SPEED_TARGET

    @staticmethod
    def _parse_gate_decision(
        directives: Mapping[str, object],
    ) -> Optional[Dict[str, object]]:
        value = directives.get("gate_decision")
        if not isinstance(value, Mapping):
            return None
        request_id = str(value.get("request_id") or "").strip()
        status = str(value.get("status") or "").strip().lower()
        options = value.get("options")
        if (
            not request_id
            or status not in {"pending", "resolved", "consumed"}
            or not isinstance(options, list)
        ):
            return None
        parsed = dict(value)
        parsed["request_id"] = request_id
        parsed["status"] = status
        return parsed

    @staticmethod
    def _parse_exclusive_validation(
        directives: Mapping[str, object],
    ) -> Dict[str, object]:
        value = directives.get("exclusive_validation")
        if not isinstance(value, Mapping):
            return {
                "schema_version": 1,
                "current_request_id": None,
                "receipts": {},
            }
        raw_receipts = value.get("receipts")
        receipts: Dict[str, Dict[str, object]] = {}
        if isinstance(raw_receipts, Mapping):
            for raw_id, raw_receipt in raw_receipts.items():
                request_id = str(raw_id or "").strip()
                if not request_id or not isinstance(raw_receipt, Mapping):
                    continue
                if str(raw_receipt.get("request_id") or "") != request_id:
                    continue
                receipts[request_id] = dict(raw_receipt)
        current_request_id = str(
            value.get("current_request_id") or ""
        ).strip()
        if current_request_id not in receipts:
            current_request_id = ""
        return {
            "schema_version": 1,
            "current_request_id": current_request_id or None,
            "receipts": receipts,
        }

    @staticmethod
    def _parse_startup_gate_waivers(
        directives: Mapping[str, object],
    ) -> Dict[str, Dict[str, object]]:
        raw = directives.get("startup_gate_waivers")
        if not isinstance(raw, Mapping):
            return {}
        parsed: Dict[str, Dict[str, object]] = {}
        for raw_check, value in raw.items():
            check_id = str(raw_check or "").strip().lower()
            if not check_id or not isinstance(value, Mapping):
                continue
            if str(value.get("status") or "").strip().lower() != "pending":
                continue
            request_id = str(value.get("request_id") or "").strip()
            if not request_id:
                continue
            parsed[check_id] = dict(value)
        return parsed

    def publish_gate_decision(
        self,
        *,
        strategy: str,
        phase: str,
        check_id: str,
        reason: str,
        expected: object,
        options,
        blocking: bool = True,
        repair_authority: Optional[Mapping[str, object]] = None,
    ) -> Optional[Dict[str, object]]:
        try:
            directive = self._control_store.publish_gate_decision(
                strategy=strategy,
                phase=phase,
                check_id=check_id,
                reason=reason,
                expected=expected,
                options=options,
                blocking=blocking,
                repair_authority=repair_authority,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[GATE_DECISION] Failed publishing request: {exc}", "WARN")
            return None
        self._gate_decision = dict(directive)
        return dict(directive)

    def resolve_gate_decision(
        self,
        request_id: str,
        decision_id: str,
        *,
        source: str,
    ) -> Optional[Dict[str, object]]:
        try:
            directive = self._control_store.resolve_gate_decision(
                request_id,
                decision_id,
                source=source,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[GATE_DECISION] Failed resolving request: {exc}", "WARN")
            return None
        self._gate_decision = dict(directive) if directive else None
        return dict(directive) if directive else None

    def consume_gate_decision(
        self,
        request_id: str,
        *,
        completion_reason: str,
    ) -> Optional[Dict[str, object]]:
        try:
            directive = self._control_store.consume_gate_decision(
                request_id,
                completion_reason=completion_reason,
            )
        except ControlDirectiveError as exc:
            log(f"[GATE_DECISION] Failed consuming request: {exc}", "WARN")
            return None
        self._gate_decision = dict(directive) if directive else None
        return dict(directive) if directive else None

    def claim_startup_gate_waivers(
        self,
        check_ids,
        *,
        strategy: str,
    ) -> Dict[str, Dict[str, object]]:
        """Claim proactive waivers that the active strategy actually declares."""

        try:
            claimed = self._control_store.claim_startup_gate_waivers(
                check_ids,
                strategy=strategy,
            )
        except ControlDirectiveError as exc:
            log(f"[GATE_WAIVER] Failed claiming staged checks: {exc}", "WARN")
            return {}
        if claimed:
            claimed_ids = set(claimed)
            self._startup_gate_waivers = {
                check_id: waiver
                for check_id, waiver in self._startup_gate_waivers.items()
                if check_id not in claimed_ids
            }
        return {
            check_id: dict(waiver)
            for check_id, waiver in claimed.items()
        }

    def current_exclusive_validation_owner(self) -> Dict[str, object]:
        """Return this process identity on its currently selected ADB target."""

        return {
            "runtime_id": self._runtime_id,
            "pid": os.getpid(),
            "adb_target": os.getenv("ADB_DEVICE") or "unknown",
        }

    def finish_interactive_development_lease(
        self,
        lease_id: str,
        *,
        disposition: str,
        reason: str,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, object]]:
        """Persist a terminal lease result owned by the runtime boundary."""

        try:
            lease = self._control_store.finish_interactive_development_lease(
                lease_id,
                disposition=disposition,
                reason=reason,
                now=now,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                "[INTERACTIVE_DEVELOPMENT] Failed recording terminal lease "
                f"state: {exc}",
                "WARN",
            )
            return None
        self._interactive_development_lease = deepcopy(lease)
        return deepcopy(lease)

    def finish_emulator_maintenance(
        self,
        request_id: str,
        *,
        disposition: str,
        reason: str,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, object]]:
        """Persist one terminal result owned by this recovery runtime."""

        try:
            maintenance = self._control_store.finish_emulator_maintenance(
                request_id,
                disposition=disposition,
                reason=reason,
                source="runtime-emulator-recovery",
                now=now,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                "[EMULATOR_RECOVERY] Failed recording terminal maintenance "
                f"state: {exc}",
                "WARN",
            )
            return None
        self._emulator_maintenance = deepcopy(maintenance)
        return deepcopy(maintenance)

    def exclusive_validation_receipt(
        self,
        *,
        request_id: Optional[str] = None,
        strategy_request_id: Optional[object] = None,
    ) -> Optional[Dict[str, object]]:
        """Find one locally cached receipt by validation or strategy request."""

        receipts = self._exclusive_validation.get("receipts")
        if not isinstance(receipts, Mapping):
            return None
        if request_id:
            receipt = receipts.get(str(request_id))
            return dict(receipt) if isinstance(receipt, Mapping) else None
        identity = str(strategy_request_id or "").strip()
        if identity:
            for receipt in receipts.values():
                if (
                    isinstance(receipt, Mapping)
                    and str(receipt.get("strategy_request_id") or "") == identity
                ):
                    return dict(receipt)
            return None
        current_request_id = str(
            self._exclusive_validation.get("current_request_id") or ""
        )
        receipt = receipts.get(current_request_id)
        return dict(receipt) if isinstance(receipt, Mapping) else None

    def claim_exclusive_validation(
        self,
        *,
        strategy_request_id: object,
        configuration_fingerprint: str,
        timeout_seconds: float,
    ) -> Optional[Dict[str, object]]:
        """Claim a pending validation under this exact runtime owner."""

        try:
            receipt = self._control_store.claim_exclusive_validation(
                strategy_request_id=str(strategy_request_id or ""),
                configuration_fingerprint=configuration_fingerprint,
                owner=self.current_exclusive_validation_owner(),
                timeout_seconds=timeout_seconds,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[VALIDATION] Failed claiming exclusive request: {exc}", "WARN")
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def mark_exclusive_validation_running(
        self,
        request_id: str,
    ) -> Optional[Dict[str, object]]:
        try:
            receipt = self._control_store.mark_exclusive_validation_running(
                request_id,
                owner=self.current_exclusive_validation_owner(),
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[VALIDATION] Failed recording owned battle: {exc}", "WARN")
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def begin_exclusive_validation_cleanup(
        self,
        request_id: str,
        *,
        outcome: str,
        reason: str,
    ) -> Optional[Dict[str, object]]:
        try:
            receipt = self._control_store.begin_exclusive_validation_cleanup(
                request_id,
                owner=self.current_exclusive_validation_owner(),
                outcome=outcome,
                reason=reason,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[VALIDATION] Failed claiming cleanup: {exc}", "WARN")
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def finish_exclusive_validation(
        self,
        request_id: str,
        *,
        outcome: str,
        reason: str,
        allowed_statuses=("cleanup",),
    ) -> Optional[Dict[str, object]]:
        try:
            receipt = self._control_store.finish_exclusive_validation(
                request_id,
                outcome=outcome,
                reason=reason,
                owner=(
                    None
                    if set(allowed_statuses) == {"pending"}
                    else self.current_exclusive_validation_owner()
                ),
                allowed_statuses=allowed_statuses,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[VALIDATION] Failed persisting result: {exc}", "WARN")
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def fail_orphaned_exclusive_validation(
        self,
        request_id: str,
        *,
        reason: str,
    ) -> Optional[Dict[str, object]]:
        try:
            receipt = self._control_store.fail_orphaned_exclusive_validation(
                request_id,
                current_owner=self.current_exclusive_validation_owner(),
                reason=reason,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[VALIDATION] Failed recording lost ownership: {exc}", "WARN")
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def claim_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        configuration_fingerprint: str,
    ) -> Optional[Dict[str, object]]:
        """Claim one operator Start decision before its first device input."""

        try:
            receipt = self._control_store.claim_exclusive_validation_launch(
                request_id,
                configuration_fingerprint=configuration_fingerprint,
                owner=self.current_exclusive_validation_owner(),
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[VALIDATION_LAUNCH] Failed claiming request: {exc}", "WARN")
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def finish_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        outcome: str,
        reason: str,
    ) -> Optional[Dict[str, object]]:
        """Finish the Tournament launch owned by this runtime."""

        try:
            receipt = self._control_store.finish_exclusive_validation_launch(
                request_id,
                owner=self.current_exclusive_validation_owner(),
                outcome=outcome,
                reason=reason,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[VALIDATION_LAUNCH] Failed recording result: {exc}", "WARN")
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def record_manual_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        reason: str,
    ) -> Optional[Dict[str, object]]:
        """Consume an unclaimed launch prompt after a fresh manual start."""

        try:
            receipt = (
                self._control_store.record_manual_exclusive_validation_launch(
                    request_id,
                    observer=self.current_exclusive_validation_owner(),
                    reason=reason,
                )
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                f"[VALIDATION_LAUNCH] Failed recording manual start: {exc}",
                "WARN",
            )
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def fail_orphaned_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        reason: str,
    ) -> Optional[Dict[str, object]]:
        """Fail a launch claimed by a prior runtime without device input."""

        try:
            receipt = (
                self._control_store.fail_orphaned_exclusive_validation_launch(
                    request_id,
                    current_owner=self.current_exclusive_validation_owner(),
                    reason=reason,
                )
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                f"[VALIDATION_LAUNCH] Failed recording lost ownership: {exc}",
                "WARN",
            )
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def fail_unclaimed_exclusive_validation_launch(
        self,
        request_id: str,
        *,
        reason: str,
    ) -> Optional[Dict[str, object]]:
        """Fail a Start request before this runtime claims tap authority."""

        try:
            receipt = (
                self._control_store.fail_unclaimed_exclusive_validation_launch(
                    request_id,
                    reason=reason,
                )
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                f"[VALIDATION_LAUNCH] Failed rejecting request: {exc}",
                "WARN",
            )
            return None
        self._refresh_exclusive_validation()
        return dict(receipt) if receipt else None

    def owns_exclusive_validation_launch(self, request_id: str) -> bool:
        """Re-read durable launch ownership immediately before an action."""

        try:
            ledger = self._control_store.status().get("exclusive_validation")
        except ControlDirectiveError as exc:
            log(
                f"[VALIDATION_LAUNCH] Ownership recheck failed: {exc}",
                "WARN",
            )
            return False
        self._exclusive_validation = dict(ledger or {})
        receipt = self.exclusive_validation_receipt(request_id=request_id)
        launch = receipt.get("launch") if receipt else None
        return bool(
            isinstance(launch, Mapping)
            and launch.get("status") == "claimed"
            and launch.get("owner")
            == self.current_exclusive_validation_owner()
        )

    def exclusive_validation_launch_action_allowed(
        self,
        request_id: str,
    ) -> bool:
        """Require fresh RUNNING intent plus this runtime's launch ownership."""

        try:
            control = self._control_store.status()
        except ControlDirectiveError as exc:
            log(
                f"[VALIDATION_LAUNCH] Action guard failed: {exc}",
                "WARN",
            )
            return False
        self._exclusive_validation = dict(
            control.get("exclusive_validation") or {}
        )
        receipt = self.exclusive_validation_receipt(request_id=request_id)
        launch = receipt.get("launch") if receipt else None
        strategy_request = self._parse_strategy_request(control)
        return bool(
            control.get("state") == "RUNNING"
            and self._exclusive_validation.get("current_request_id")
            == request_id
            and strategy_request is not None
            and receipt is not None
            and receipt.get("strategy") == strategy_request[0]
            and receipt.get("strategy_request_id")
            == str(strategy_request[1] or "")
            and isinstance(launch, Mapping)
            and launch.get("status") == "claimed"
            and launch.get("owner")
            == self.current_exclusive_validation_owner()
        )

    def owns_exclusive_validation(
        self,
        request_id: str,
        *,
        statuses=("claimed", "running", "cleanup"),
    ) -> bool:
        """Re-read durable ownership immediately before a guarded action."""

        try:
            ledger = self._control_store.status().get("exclusive_validation")
        except ControlDirectiveError as exc:
            log(f"[VALIDATION] Ownership recheck failed: {exc}", "WARN")
            return False
        self._exclusive_validation = dict(ledger or {})
        receipt = self.exclusive_validation_receipt(request_id=request_id)
        return bool(
            receipt
            and str(receipt.get("status") or "") in set(statuses)
            and receipt.get("owner") == self.current_exclusive_validation_owner()
        )

    def _refresh_exclusive_validation(self) -> None:
        try:
            ledger = self._control_store.status().get("exclusive_validation")
        except ControlDirectiveError:
            return
        self._exclusive_validation = dict(ledger or {})

    def _persist_runtime_state(self, state: str, *, source: str) -> bool:
        """Persist one already-authorized runtime state transition."""

        normalized = str(state).strip().upper()
        if normalized not in _ALLOWED_STATES:
            raise ValueError(
                f"Unsupported automation state {state!r}; "
                f"expected one of {sorted(_ALLOWED_STATES)}"
            )
        try:
            saved = self._control_store.set_state(
                normalized,
                source=source,
            )
        except ControlDirectiveError as exc:
            log(f"[CTRL] Failed writing control file: {exc}", "WARN")
            return False
        self._last_applied_state = None
        request_id = saved.get("state_request_id")
        self._last_state_directive_revision = request_id
        self._apply_state(normalized, request_id=request_id)
        return True

    def pause_for_operator_authority(self, reason: str) -> bool:
        """Persist a Pause explicitly selected by, or yielded to, the operator."""

        log(
            "[RUNTIME_POLICY] Operator authority requested Pause: "
            f"{str(reason or 'operator request').strip()}",
            "INFO",
        )
        return self._persist_runtime_state(
            "PAUSED",
            source="runtime-operator-authority",
        )

    def pause_for_catastrophic_failure(
        self,
        kind: RuntimeFailureKind,
        *,
        reason: str,
    ) -> bool:
        """Persist the only automatic Pause permitted by global policy."""

        decision = decide_runtime_failure(kind)
        if decision.disposition is not RuntimeFailureDisposition.PAUSE_FOR_SAFETY:
            raise ValueError(
                f"{kind.value} is recoverable and cannot globally Pause automation"
            )
        # The local safety latch must precede every fallible diagnostic or
        # persistence operation.  Otherwise a logger failure could let the
        # next guard reapply stale durable RUNNING authority.
        self._latch_catastrophic_pause(reason)
        try:
            saved = self._control_store.set_paused_unless_stopped(
                source="runtime-catastrophic-failure"
            )
        except ControlDirectiveError as exc:
            try:
                log(f"[CTRL] Failed writing control file: {exc}", "WARN")
            except Exception:
                pass
            return False

        saved_state = str(saved.get("state") or "").strip().upper()
        request_id = saved.get("state_request_id")
        self._last_state_directive_revision = request_id
        self._last_applied_state = None
        if saved_state == "STOPPED":
            # Explicit Stop always outranks automatic Pause, including when a
            # command reports uncertainty after Stop was persisted.
            self._catastrophic_pause_latched = False
            self._catastrophic_pause_state_revision = None
            self._catastrophic_pause_reason = None
            self._apply_state("STOPPED", request_id=request_id)
            try:
                log(
                    "[RUNTIME_POLICY] Catastrophic result arrived after "
                    "explicit Stop; STOPPED authority was preserved",
                    "ERROR",
                )
            except Exception:
                pass
            return True

        self._apply_state("PAUSED", request_id=request_id)
        self._catastrophic_pause_state_revision = request_id
        try:
            log(
                "[RUNTIME_POLICY] Catastrophic failure Paused automation: "
                f"kind={kind.value} "
                f"reason={str(reason or 'unavailable').strip()}",
                "ERROR",
            )
        except Exception:
            pass
        return True

    def _latch_catastrophic_pause(self, reason: str) -> None:
        """Require a newer explicit RUNNING request after an unsafe gap."""

        if self.control_state == "STOPPED":
            # An explicit Stop is stricter than a catastrophic Pause and must
            # never be weakened when durable authority or reporting fails.
            return
        if not self._catastrophic_pause_latched:
            self._catastrophic_pause_state_revision = getattr(
                self,
                "_last_state_directive_revision",
                None,
            )
        self._catastrophic_pause_latched = True
        self._catastrophic_pause_reason = str(reason or "catastrophic failure")
        # This is deliberately independent of the control-file write.  A
        # failed persistence attempt must still stop this process immediately.
        AUTOMATION.state = "PAUSED"

    def transition_battle_workflow(
        self,
        request_id: str,
        status: str,
        **details: object,
    ) -> Optional[Dict[str, object]]:
        """Persist a runtime-owned explicit battle-workflow result."""

        try:
            workflow = self._control_store.transition_battle_workflow(
                request_id,
                status,
                **details,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[BATTLE_WORKFLOW] Failed recording transition: {exc}", "WARN")
            return None
        self._battle_workflow = dict(workflow) if workflow else None
        return dict(workflow) if workflow else None

    def activate_terminal_idle_timeout(
        self,
        evidence: Mapping[str, object],
    ) -> Optional[Dict[str, object]]:
        """Arm the configured one-shot Wait/Home timeout."""

        previous_id = str(
            (self._terminal_idle_timeout or {}).get("request_id") or ""
        )
        try:
            hold = self._control_store.activate_terminal_idle_timeout(
                evidence=evidence,
                timeout_seconds=DEFAULT_IDLE_TIMEOUT_SECONDS,
                strategy=DEFAULT_IDLE_TIMEOUT_STRATEGY,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[IDLE_TIMEOUT] Could not arm terminal hold: {exc}", "WARN")
            return None
        self._terminal_idle_timeout = dict(hold) if hold else None
        if hold is not None and hold["request_id"] != previous_id:
            log(
                "[IDLE_TIMEOUT] Holding the requested terminal/Home boundary "
                f"for {DEFAULT_IDLE_TIMEOUT_SECONDS // 60} minutes; then "
                f"starting {DEFAULT_IDLE_TIMEOUT_STRATEGY}",
                "INFO",
                console=True,
            )
        return dict(hold) if hold else None

    def advance_terminal_idle_timeout_if_expired(self) -> bool:
        """Move one expired exact terminal hold toward Home."""

        hold = self._terminal_idle_timeout
        if (
            not isinstance(hold, Mapping)
            or hold.get("status") == "returning_home"
            or float(hold.get("expires_at") or 0.0) > time.time()
        ):
            return False
        try:
            advanced = (
                self._control_store.advance_expired_terminal_idle_timeout_to_home(
                    str(hold.get("request_id") or ""),
                    now=time.time(),
                )
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[IDLE_TIMEOUT] Could not release terminal hold: {exc}", "WARN")
            return False
        if advanced is None or advanced.get("status") != "returning_home":
            return False
        self._terminal_idle_timeout = dict(advanced)
        self._last_applied_mode = None
        self.apply_control()
        log(
            "[IDLE_TIMEOUT] Terminal hold expired; returning Home before "
            f"starting {advanced.get('strategy')}",
            "INFO",
            console=True,
        )
        return True

    def request_idle_timeout_start(
        self,
        *,
        evidence: Mapping[str, object],
        terminal_timeout_request_id: Optional[str] = None,
        timed_pause_state_request_id: Optional[str] = None,
    ) -> Optional[Dict[str, object]]:
        """Consume one timeout into the ordinary exact-evidence Start workflow."""

        hold = self._terminal_idle_timeout
        strategy = (
            str(hold.get("strategy") or "").strip().lower()
            if isinstance(hold, Mapping)
            and terminal_timeout_request_id is not None
            else DEFAULT_IDLE_TIMEOUT_STRATEGY
        )
        try:
            workflow = self._control_store.request_battle_workflow(
                "start_battle",
                evidence=evidence,
                strategy=strategy,
                terminal_idle_timeout_request_id=terminal_timeout_request_id,
                timed_pause_expiry_state_request_id=(
                    timed_pause_state_request_id
                ),
                source="runtime-idle-timeout",
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[IDLE_TIMEOUT] Could not request fallback battle: {exc}", "WARN")
            return None
        self._terminal_idle_timeout = None
        if timed_pause_state_request_id is not None:
            self.consume_timed_pause_expiry(timed_pause_state_request_id)
        self._last_applied_mode = None
        self.apply_control()
        self._battle_workflow = dict(workflow)
        log_action_intent(
            f"Starting timeout fallback Strategy {strategy}",
            reason="the bounded idle hold expired without newer operator intent",
            detail=(
                "[IDLE_TIMEOUT] result=requested "
                f"strategy={strategy} workflow={workflow.get('request_id')}"
            ),
            operation_id=str(workflow.get("request_id") or ""),
        )
        return dict(workflow)

    def request_process_restart_reattachment(
        self,
        handoff_id: str,
        *,
        evidence: Mapping[str, object],
        strategy: Optional[str] = None,
    ) -> Optional[Dict[str, object]]:
        """Create the fresh Attach workflow for one exact restart handoff."""

        try:
            workflow = self._control_store.request_battle_workflow(
                "attach_battle",
                evidence=evidence,
                strategy=strategy,
                process_restart_handoff_id=handoff_id,
                source="runtime-process-restart",
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                "[PROCESS_RESTART] Failed creating active-battle Attach "
                f"workflow: {exc}",
                "WARN",
            )
            return None
        self._battle_workflow = dict(workflow)
        handoff = deepcopy(self._process_restart_handoff)
        if (
            handoff is not None
            and handoff.get("handoff_id") == handoff_id
            and handoff.get("status") == "pending"
        ):
            handoff["workflow_id"] = workflow["request_id"]
            self._process_restart_handoff = handoff
        return dict(workflow)

    def request_unexpected_restart_reattachment(
        self,
        *,
        evidence: Mapping[str, object],
        strategy: Optional[str] = None,
    ) -> Optional[Dict[str, object]]:
        """Create a normal Attach after Welcome Back proves an active battle."""

        try:
            workflow = self._control_store.request_battle_workflow(
                "attach_battle",
                evidence=evidence,
                strategy=strategy,
                source="runtime-welcome-back",
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                "[BATTLE_IDENTITY] Failed creating Welcome Back Attach "
                f"workflow: {exc}",
                "WARN",
            )
            return None
        self._battle_workflow = dict(workflow)
        return dict(workflow)

    def finish_process_restart_handoff(
        self,
        handoff_id: str,
        status: str,
        **details: object,
    ) -> Optional[Dict[str, object]]:
        """Persist a terminal result for one active-battle restart handoff."""

        try:
            handoff = self._control_store.finish_process_restart_handoff(
                handoff_id,
                status,
                **details,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                "[PROCESS_RESTART] Failed recording restart handoff result: "
                f"{exc}",
                "WARN",
            )
            return None
        self._process_restart_handoff = (
            dict(handoff) if handoff else None
        )
        return dict(handoff) if handoff else None

    def transition_manual_control(
        self,
        manual_control_id: str,
        status: str,
        **details: object,
    ) -> Optional[Dict[str, object]]:
        """Persist a runtime-owned Take/Return Control result."""

        try:
            manual = self._control_store.transition_manual_control(
                manual_control_id,
                status,
                **details,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[MANUAL_CONTROL] Failed recording transition: {exc}", "WARN")
            return None
        self._manual_control = dict(manual) if manual else None
        return dict(manual) if manual else None

    def transition_setup_capture(
        self,
        request_id: str,
        status: str,
        **details: object,
    ) -> Optional[Dict[str, object]]:
        """Persist one runtime-owned setup-capture transition."""

        try:
            capture = self._control_store.transition_setup_capture(
                request_id,
                status,
                **details,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(f"[SETUP_CAPTURE] Failed recording transition: {exc}", "WARN")
            return None
        self._setup_capture = dict(capture) if capture else None
        return dict(capture) if capture else None

    def record_manual_terminal_evidence(
        self,
        manual_control_id: str,
        evidence: Mapping[str, object],
    ) -> Optional[Dict[str, object]]:
        """Persist passive exact-run terminal evidence for Manual Control."""

        try:
            manual = self._control_store.record_manual_terminal_evidence(
                manual_control_id,
                evidence,
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                f"[MANUAL_CONTROL] Failed recording terminal evidence: {exc}",
                "WARN",
            )
            return None
        self._manual_control = dict(manual) if manual else None
        return dict(manual) if manual else None

    def yield_to_unexpected_manual_activity(
        self,
        evidence: Mapping[str, object],
    ) -> Optional[Dict[str, object]]:
        """Atomically Pause when passive evidence shows unexpected manual input."""

        try:
            manual = self._control_store.request_manual_control(
                evidence=evidence,
                reason="unexpected_manual_activity",
                source="runtime-manual-yield",
            )
        except (ControlDirectiveError, ValueError) as exc:
            log(
                f"[MANUAL_CONTROL] Could not yield after manual activity: {exc}",
                "WARN",
            )
            self._unexpected_manual_yield_emergency = True
            self._last_applied_state = None
            self._apply_state("PAUSED")
            return None
        self._manual_control = dict(manual)
        self._unexpected_manual_yield_emergency = False
        self._last_applied_state = None
        self._apply_state("PAUSED")
        return dict(manual)

    def persist_mode(self, mode: str) -> bool:
        """Persist and apply a runtime-owned terminal mode transition."""

        normalized = normalize_automation_mode(mode)
        try:
            saved = self._control_store.set_mode(normalized, source="runtime")
        except ControlDirectiveError as exc:
            log(f"[CTRL] Failed writing control file: {exc}", "WARN")
            return False
        self._last_applied_mode = None
        request_id = saved.get("mode_request_id")
        self._last_mode_directive_revision = request_id
        self._apply_mode(normalized, request_id=request_id)
        return True

    def format_state(self, ui_state: str) -> str:
        return f"{ui_state}/PAUSED" if self.is_paused else ui_state

    # --------------------- coins: toggle + plausibility ----------------------
    def _recover_missing_coin_magnitude(
        self,
        coins_val: Optional[Decimal],
        coins_conf: float,
        has_min: bool,
    ) -> Optional[Decimal]:
        """Recover a magnitude suffix omitted from an otherwise valid rate OCR."""

        reference = self._last_coins_val
        if (
            not has_min
            or coins_val is None
            or coins_val <= 0
            or coins_val >= 1000
            or reference is None
            or reference < 1000
        ):
            return coins_val

        reference_exponent = max(3, (reference.adjusted() // 3) * 3)
        candidates = []
        for exponent in {
            max(0, reference_exponent - 3),
            reference_exponent,
            reference_exponent + 3,
        }:
            candidate = coins_val * (Decimal(10) ** exponent)
            if candidate <= 0:
                continue
            factor = max(candidate / reference, reference / candidate)
            candidates.append((factor, candidate))

        factor, recovered = min(candidates, key=lambda item: item[0])
        if factor > self.coins_max_jump_factor or recovered == coins_val:
            return coins_val

        log(
            "[COINS] Recovered missing magnitude suffix "
            f"{format_compact_decimal(coins_val)} → "
            f"{format_compact_decimal(recovered)} using prior "
            f"{format_compact_decimal(reference)} "
            f"(factor={factor:.2f}, conf={coins_conf:.1f})",
            "DEBUG",
        )
        return recovered

    def _apply_plausibility(
        self,
        coins_val: Optional[Decimal],
        coins_conf: float,
        has_min: bool,
    ) -> Optional[Decimal]:
        coins_eff = coins_val
        try:
            if not has_min:
                self._coins_pending_plausibility_val = None
                return coins_eff
            if self._coins_ignore_plausibility_once:
                self._coins_ignore_plausibility_once = False
                self._coins_pending_plausibility_val = None
                return coins_eff
            if (
                self._last_coins_val is not None
                and coins_val is not None
                and self._last_coins_val > 0
                and coins_val > 0
            ):
                ratio = coins_val / self._last_coins_val
                change_kind = None
                factor = None
                if ratio > self.coins_max_jump_factor and coins_conf < self.coins_jump_conf_floor:
                    change_kind = "jump"
                    factor = ratio
                else:
                    drop_factor = self._last_coins_val / coins_val
                    if drop_factor > self.coins_max_jump_factor:
                        change_kind = "drop"
                        factor = drop_factor

                if change_kind is not None and factor is not None:
                    pending = self._coins_pending_plausibility_val
                    if pending is not None and pending > 0:
                        confirmation_factor = max(
                            coins_val / pending,
                            pending / coins_val,
                        )
                        if confirmation_factor <= self.coins_max_jump_factor:
                            log(
                                "[COINS] Accepted sustained rate change "
                                f"{format_compact_decimal(self._last_coins_val)} → "
                                f"{format_compact_decimal(coins_val)} after "
                                f"consecutive {format_compact_decimal(pending)} "
                                f"and {format_compact_decimal(coins_val)} readings "
                                f"(factor={confirmation_factor:.2f}, "
                                f"conf={coins_conf:.1f})",
                                "WARN",
                            )
                            self._coins_pending_plausibility_val = None
                            return coins_eff

                    self._coins_pending_plausibility_val = coins_val
                    if change_kind == "jump":
                        log(
                            "[COINS] Holding implausible jump "
                            f"{format_compact_decimal(self._last_coins_val)} → "
                            f"{format_compact_decimal(coins_val)} pending "
                            f"confirmation (×{factor:.2f}, "
                            f"conf={coins_conf:.1f})",
                            "WARN",
                        )
                    else:
                        log(
                            "[COINS] Holding implausible drop "
                            f"{format_compact_decimal(self._last_coins_val)} → "
                            f"{format_compact_decimal(coins_val)} pending "
                            f"confirmation (÷{factor:.2f}, "
                            f"conf={coins_conf:.1f})",
                            "WARN",
                        )
                    coins_eff = self._last_coins_val
                else:
                    self._coins_pending_plausibility_val = None
            else:
                self._coins_pending_plausibility_val = None
        except Exception:
            pass
        return coins_eff

    def process_coins(
        self,
        img: Frame,
        coins_val: Optional[Decimal],
        coins_conf: float,
        has_min: bool,
        *,
        debug_out: Optional[str] = None,
        allow_actions: bool = True,
    ) -> Tuple[Optional[Decimal], float, bool, Optional[Decimal]]:
        """
        Apply plausibility and optionally debounce the coin-display toggle.

        ``allow_actions=False`` keeps status sampling read-only even when the
        supervisor itself is not paused, such as during an exclusive startup
        gate. Returns updated ``(val, conf, has_min, eff)``.
        """
        # Restore a dropped OCR suffix before comparing the reading with the
        # last trusted rate. The recovery is limited to a parsed /min display
        # and must itself fall within the normal plausibility window.
        coins_val = self._recover_missing_coin_magnitude(
            coins_val,
            coins_conf,
            has_min,
        )

        # Plausibility first
        coins_eff = self._apply_plausibility(coins_val, coins_conf, has_min)

        if not has_min:
            # When '/min' is missing, stick with the last trusted coins/min value (if any)
            if self._last_coins_val is not None:
                coins_eff = self._last_coins_val
            else:
                coins_eff = None

        if allow_actions and not self.is_paused:
            now_ts = time.time()
            if has_min:
                self._coins_has_min_miss = 0
            else:
                ratio = None
                if (
                    coins_val is not None
                    and self._last_coins_val is not None
                    and self._last_coins_val > 0
                ):
                    try:
                        ratio = (coins_val / self._last_coins_val) if coins_val > 0 else None
                    except Exception:
                        ratio = None

                if coins_val is not None or coins_conf >= self.coins_conf_floor:
                    self._coins_has_min_miss += 1

                should_toggle = False
                toggle_reason = ""
                if self._coins_has_min_miss >= 2:
                    should_toggle = True
                    if ratio is not None and ratio >= self.coins_max_jump_factor:
                        toggle_reason = (
                            f"ratio={ratio:.2f}, "
                            f"miss_count={self._coins_has_min_miss}"
                        )
                    else:
                        toggle_reason = f"miss_count={self._coins_has_min_miss}"

                if should_toggle and (now_ts - self._last_coins_toggle_ts) >= self.coins_toggle_cooldown:
                    log(f"[COINS] Auto-toggle coin display ({toggle_reason or 'missing /min'})", "INFO")
                    if tap_if_visible("buttons.coin_toggle", retries=1):
                        self._last_coins_toggle_ts = now_ts
                        self._coins_has_min_miss = 0
                        self._coins_ignore_plausibility_once = True
                        time.sleep(0.6)
                        img2 = capture_and_save_screenshot(log_capture=False)
                        if img2 is not None:
                            try:
                                coins_val, coins_conf, has_min = detect_coins_from_image(img2, debug_out=debug_out)
                                coins_val = self._recover_missing_coin_magnitude(
                                    coins_val,
                                    coins_conf,
                                    has_min,
                                )
                                coins_eff = self._apply_plausibility(
                                    coins_val,
                                    coins_conf,
                                    has_min,
                                )
                                if not has_min:
                                    coins_eff = self._last_coins_val
                            except Exception:
                                pass

        # Update accepted last value
        try:
            if coins_eff is not None and has_min:
                self._last_coins_val = coins_eff
        except Exception:
            pass

        return coins_val, coins_conf, has_min, coins_eff

    # ------------------------- auto return-to-game ---------------------------
    def auto_return_check(self, img: Frame, ui_state: str) -> None:
        if not self.auto_return_enabled or self.is_paused or ui_state == "RUNNING":
            # If timer was running but conditions no longer hold, cancel
            if self._rtg_visible_since_ts is not None:
                try:
                    elapsed = int(time.time() - self._rtg_visible_since_ts)
                except Exception:
                    elapsed = 0
                reason = (
                    "state RUNNING" if ui_state == "RUNNING" else "paused/disabled"
                )
                log(
                    f"[AUTO] Return-to-Game timer cancelled due to {reason} (after {elapsed}s)",
                    "INFO",
                    console=True,
                )
                self._rtg_visible_since_ts = None
            return

        try:
            visible = is_visible("buttons.return_to_game", screenshot=img)
            if not visible:
                try:
                    pt, conf = _get_match("buttons.return_to_game", screenshot=img)
                    visible = bool(pt) and (conf >= self.auto_return_conf_threshold)
                    if visible:
                        log(f"[AUTO] Return-to-Game matched via fallback (conf={conf:.2f})", "DEBUG")
                except Exception:
                    visible = False

            if visible:
                if self._rtg_visible_since_ts is None:
                    self._rtg_visible_since_ts = time.time()
                    mins = (self.auto_return_secs // 60) if self.auto_return_secs > 0 else 0
                    log(
                        f"[AUTO] Return-to-Game detected; starting timer ({mins}m)",
                        "INFO",
                        console=True,
                    )
                elif (time.time() - self._rtg_visible_since_ts) >= self.auto_return_secs > 0:
                    elapsed = int(time.time() - self._rtg_visible_since_ts)
                    log_action_intent(
                        "Returning to the active battle",
                        reason=(
                            "the Return to Game control remained visible for "
                            f"{elapsed}s"
                        ),
                        detail=(
                            f"[AUTO_RETURN] elapsed_s={elapsed} "
                            f"threshold_s={self.auto_return_secs}"
                        ),
                    )
                    try:
                        returned = tap_if_visible(
                            "buttons.return_to_game",
                            retries=1,
                        )
                    except Exception as exc:
                        returned = False
                        log(
                            f"Automatic Return to Game input failed: {exc!r}",
                            "ERROR",
                        )
                    log_result(
                        (
                            "Automatic Return to Game complete — battle resumed"
                            if returned
                            else (
                                "Automatic Return to Game failed — the verified "
                                "control could not be tapped"
                            )
                        ),
                        detail=(
                            f"[AUTO_RETURN] result={'completed' if returned else 'failed'} "
                            f"elapsed_s={elapsed}"
                        ),
                    )
                    self._rtg_visible_since_ts = None
            else:
                if self._rtg_visible_since_ts is not None:
                    elapsed = int(time.time() - self._rtg_visible_since_ts)
                    log(
                        f"[AUTO] Return-to-Game disappeared before threshold — cancelling timer (after {elapsed}s)",
                        "INFO",
                        console=True,
                    )
                    self._rtg_visible_since_ts = None
        except Exception:
            self._rtg_visible_since_ts = None

    # ------------------------------ helpers ---------------------------------
    def _load_control_directive(self) -> Dict[str, object]:
        try:
            directives = self._control_store.read()
        except ControlDirectiveError as exc:
            self._control_read_failed = True
            self._latch_catastrophic_pause(
                "durable control authority became unreadable"
            )
            if not self._control_read_failure_logged:
                log(
                    "[CTRL] Failed reading control file; device mutation is "
                    f"blocked until authority recovers: {exc}",
                    "WARN",
                )
                self._control_read_failure_logged = True
            return {}
        authority_error = self._core_state_authority_error(directives)
        if authority_error is not None:
            self._control_read_failed = True
            raw_state = directives.get("state")
            if (
                isinstance(raw_state, str)
                and raw_state.strip().upper() == "STOPPED"
            ):
                # A malformed identity cannot authorize action, but the
                # stricter durable STOPPED value is still safe to honor. Do
                # not acknowledge the malformed request envelope.
                AUTOMATION.state = "STOPPED"
            else:
                self._latch_catastrophic_pause(authority_error)
            if not self._control_read_failure_logged:
                log(
                    "[CTRL] Durable state authority is missing or malformed; "
                    f"device mutation is blocked: {authority_error}",
                    "WARN",
                )
                self._control_read_failure_logged = True
            return {}
        if self._control_read_failure_logged:
            log(
                "[CTRL] Control-file authority recovered; applying the "
                "current durable directive",
                "INFO",
            )
        self._control_read_failed = False
        self._control_read_failure_logged = False
        return directives

    @staticmethod
    def _core_state_authority_error(
        directives: Mapping[str, object],
    ) -> Optional[str]:
        """Validate the exact durable Pause/Stop authority identity."""

        state = directives.get("state")
        normalized_state = (
            state.strip().upper() if isinstance(state, str) else ""
        )
        if normalized_state not in _ALLOWED_STATES:
            return "durable control state is missing or unsupported"
        raw_request_id = directives.get("state_request_id")
        request_id = (
            raw_request_id.strip() if isinstance(raw_request_id, str) else ""
        )
        if (
            not request_id
            or len(request_id) > 128
            or any(
                not character.isascii()
                or not (character.isalnum() or character in "._:-")
                for character in request_id
            )
        ):
            return "durable control state request identity is missing or malformed"
        return None

    def _record_control_acknowledgement(
        self,
        field: str,
        value: str,
        request_id: object,
    ) -> bool:
        """Replace one receipt only after the exact directive was applied."""

        normalized_request_id = str(request_id or "").strip()
        if (
            field not in self._control_acknowledgements
            or not normalized_request_id
            or len(normalized_request_id) > 128
            or any(
                not character.isascii()
                or not (character.isalnum() or character in "._:-")
                for character in normalized_request_id
            )
        ):
            return False
        self._control_acknowledgements[field] = {
            "value": str(value),
            "request_id": normalized_request_id,
            "acknowledged_at": datetime.now(timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds"),
        }
        return True

    def _apply_state(
        self,
        state: object,
        *,
        acknowledge_unchanged: bool = False,
        request_id: object = None,
    ) -> None:
        if not isinstance(state, str) or not state:
            return
        normalized = state.upper()
        if normalized not in _ALLOWED_STATES:
            return
        request_suffix = (
            f" request_id={str(request_id).strip()}"
            if str(request_id or "").strip()
            else ""
        )
        actual_matches = self.control_state == normalized
        if (
            normalized == self._last_applied_state
            and actual_matches
            and not acknowledge_unchanged
        ):
            return
        try:
            if not actual_matches:
                AUTOMATION.state = normalized
            log(
                f"[CTRL] State set to {normalized} via control file"
                f"{request_suffix}",
                "INFO",
                console=True,
            )
            self._last_applied_state = normalized
            self._record_control_acknowledgement(
                "state",
                normalized,
                request_id,
            )
        except Exception as exc:
            try:
                log(f"[CTRL] Failed to set state={normalized}: {exc}", "WARN")
            except Exception:
                pass

    def _apply_mode(
        self,
        mode: object,
        *,
        acknowledge_unchanged: bool = False,
        request_id: object = None,
    ) -> None:
        if not isinstance(mode, str) or not mode:
            return
        try:
            normalized = normalize_automation_mode(mode)
        except ValueError:
            return
        request_suffix = (
            f" request_id={str(request_id).strip()}"
            if str(request_id or "").strip()
            else ""
        )
        if normalized == self._last_applied_mode:
            if acknowledge_unchanged:
                log(
                    f"[CTRL] Mode set to {normalized} via control file"
                    f"{request_suffix}",
                    "INFO",
                    console=True,
                )
                self._record_control_acknowledgement(
                    "mode",
                    normalized,
                    request_id,
                )
            return
        try:
            AUTOMATION.mode = normalized
            log(
                f"[CTRL] Mode set to {normalized} via control file"
                f"{request_suffix}",
                "INFO",
                console=True,
            )
            self._last_applied_mode = normalized
            self._record_control_acknowledgement(
                "mode",
                normalized,
                request_id,
            )
        except Exception as exc:
            log(f"[CTRL] Failed to set mode={normalized}: {exc}", "WARN")

    def _apply_game_speed_target(
        self,
        target: object,
        *,
        acknowledge_unchanged: bool = False,
        request_id: object = None,
    ) -> None:
        normalized = self._parse_game_speed_target(target)
        self._game_speed_target = normalized
        if normalized == self._last_applied_game_speed_target:
            if acknowledge_unchanged:
                log(
                    f"[CTRL] Game speed target set to x{normalized:.1f} "
                    "via control file",
                    "INFO",
                    console=True,
                )
                self._record_control_acknowledgement(
                    "game_speed_target",
                    f"x{normalized:.1f}",
                    request_id,
                )
            return
        self._last_applied_game_speed_target = normalized
        log(
            f"[CTRL] Game speed target set to x{normalized:.1f} "
            "via control file",
            "INFO",
            console=True,
        )
        self._record_control_acknowledgement(
            "game_speed_target",
            f"x{normalized:.1f}",
            request_id,
        )

    def _apply_adb_port(
        self,
        port: object,
        updated_at: object,
        *,
        request_id: object = None,
        emulator_location: object = None,
    ) -> None:
        if isinstance(port, bool) or not isinstance(port, int):
            return
        if not 1 <= port <= 65535:
            return

        location = normalize_emulator_location(emulator_location)
        request_identity = request_id or updated_at
        if emulator_location is not None and (
            location is None
            or location.get("linux_adb_port") != port
            or str(location.get("request_id") or "")
            != str(request_id or "")
        ):
            if request_identity != self._last_invalid_emulator_location_request:
                log(
                    "[CTRL] Emulator location request is malformed or does "
                    "not match its ADB-port request; retaining the current "
                    "target",
                    "WARN",
                    console=True,
                )
                self._last_invalid_emulator_location_request = request_identity
            return
        callback_available = (
            self._emulator_location_handoff is not None
            if location is not None
            else self._adb_port_handoff is not None
        )
        if not callback_available:
            return

        request = (port, request_identity)
        if request == self._last_applied_adb_request:
            return
        target = f"localhost:{port}"
        if os.getenv("ADB_DEVICE") == target and location is None:
            self._last_applied_adb_request = request
            log(
                f"[CTRL] ADB target set to {target} via control file",
                "INFO",
                console=True,
            )
            self._record_control_acknowledgement(
                "adb_target",
                target,
                request_id,
            )
            return
        if not self.is_paused:
            if request != self._last_deferred_adb_request:
                log(
                    f"[CTRL] Deferring ADB target {target}; "
                    "runtime must be PAUSED",
                    "WARN",
                    console=True,
                )
                self._last_deferred_adb_request = request
            return

        now = time.monotonic()
        if (
            request == self._last_deferred_adb_request
            and now < self._next_adb_handoff_attempt_at
        ):
            return
        self._last_deferred_adb_request = request
        if location is not None:
            assert self._emulator_location_handoff is not None
            applied = self._emulator_location_handoff(port, location)
        else:
            assert self._adb_port_handoff is not None
            applied = self._adb_port_handoff(port)
        if applied:
            self._last_applied_adb_request = request
            self._next_adb_handoff_attempt_at = 0.0
            self._applied_emulator_location = (
                deepcopy(location) if location is not None else None
            )
            log(
                (
                    f"[CTRL] Emulator location set to "
                    f"{location['host_name']} at {target} via control file"
                    if location is not None
                    else f"[CTRL] ADB target set to {target} via control file"
                ),
                "INFO",
                console=True,
            )
            self._record_control_acknowledgement(
                "adb_target",
                target,
                request_id,
            )
            return

        self._next_adb_handoff_attempt_at = now + 10.0
        log(
            f"[CTRL] ADB target handoff to {target} failed; "
            "remaining PAUSED and retaining the previous target",
            "WARN",
            console=True,
        )

    @staticmethod
    def _parse_resume_at(value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    def _sync_pause_deadline(self, directives: Dict[str, object]) -> None:
        state = directives.get("state")
        if not isinstance(state, str) or state.upper() != "PAUSED":
            self._pause_resume_at = None
            self._last_invalid_resume_at = None
            return

        raw_resume_at = directives.get("resume_at")
        self._pause_resume_at = self._parse_resume_at(raw_resume_at)
        if raw_resume_at is None or self._pause_resume_at is not None:
            self._last_invalid_resume_at = None
            return

        if raw_resume_at != self._last_invalid_resume_at:
            log(
                f"[CTRL] Ignoring invalid pause resume_at={raw_resume_at!r}",
                "WARN",
            )
            self._last_invalid_resume_at = raw_resume_at

    def _write_control_directive(self, directives: Dict[str, object]) -> bool:
        try:
            self._control_store.replace(directives)
            return True
        except ControlDirectiveError as exc:
            log(f"[CTRL] Failed writing control file: {exc}", "WARN")
            return False

    def _auto_resume_if_needed(self) -> None:
        deadline = self._pause_resume_at
        if (
            self._catastrophic_pause_latched
            or not self.is_paused
            or deadline is None
            or time.time() < deadline
        ):
            return

        try:
            resumed = self._control_store.resume_expired_pause(
                expected_resume_at=deadline,
                now=time.time(),
            )
        except ControlDirectiveError as exc:
            log(f"[CTRL] Failed writing control file: {exc}", "WARN")
            return
        if resumed is None:
            directives = self._load_control_directive()
            self._sync_pause_deadline(directives)
            return

        request_id = resumed.get("state_request_id")
        self._last_state_directive_revision = request_id
        self._apply_state("RUNNING", request_id=request_id)
        self._pause_resume_at = None
        self._timed_pause_expiry_pending = str(request_id or "").strip() or None
        log(
            "[CTRL] Timed pause expired; persisted State=RUNNING",
            "INFO",
            console=True,
        )

# Re-exports for convenience
try:
    from core.run_state import RunState, ExecMode
    __all__ = [
        "AutomationSupervisor",
        "AUTOMATION",
        "RunState",
        "ExecMode",
    ]
except Exception:
    __all__ = ["AutomationSupervisor", "AUTOMATION"]
