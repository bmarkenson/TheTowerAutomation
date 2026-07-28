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

import math
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray

from core.app_setup import CONFIGURABLE_STRATEGIES
from core.control_directives import ControlDirectiveError, ControlDirectiveStore
from utils.logger import log, log_action_intent, log_result
from core.run_state import AUTOMATION
from core.input import tap_if_visible
from core.label_tapper import is_visible
from core.matcher import get_match as _get_match
from core.ss_capture import capture_and_save_screenshot
from utils.coin_detector import detect_coins_from_image, format_compact_decimal


Frame = NDArray[np.uint8]


_ALLOWED_STATES = {"RUNNING", "PAUSED", "STOPPED"}
_ALLOWED_MODES = {"RETRY", "WAIT", "HOME"}


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
    ) -> None:
        self.control_file = Path(control_file)
        self._control_store = ControlDirectiveStore(self.control_file)
        initial_directives = self._load_control_directive()
        self._strategy_request = self._parse_strategy_request(initial_directives)
        self._gate_decision = self._parse_gate_decision(initial_directives)
        self._exclusive_validation = self._parse_exclusive_validation(
            initial_directives
        )
        self._startup_gate_waivers = self._parse_startup_gate_waivers(
            initial_directives
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

        # Internal state
        self._last_applied_state: Optional[str] = None
        self._last_state_directive_revision: object = None
        self._last_applied_mode: Optional[str] = None
        self._pause_resume_at: Optional[float] = None
        self._last_invalid_resume_at: object = None
        self._last_applied_adb_request: Optional[Tuple[int, object]] = None
        self._last_deferred_adb_request: Optional[Tuple[int, object]] = None
        self._next_adb_handoff_attempt_at = 0.0

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

    def apply_control(self) -> bool:
        """Apply directives and report whether state intent changed on disk."""

        directives = self._load_control_directive()
        state_directive_changed = False
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
            self._last_state_directive_revision = state_revision
            self._strategy_request = self._parse_strategy_request(directives)
            self._gate_decision = self._parse_gate_decision(directives)
            self._exclusive_validation = self._parse_exclusive_validation(
                directives
            )
            self._startup_gate_waivers = self._parse_startup_gate_waivers(
                directives
            )
            self._apply_state(
                directives.get("state"),
                acknowledge_unchanged=state_directive_changed,
            )
            self._apply_mode(directives.get("mode"))
            self._sync_pause_deadline(directives)
            self._apply_adb_port(
                directives.get("adb_port"),
                directives.get("adb_port_updated_at"),
            )

        self._auto_resume_if_needed()
        return state_directive_changed

    @staticmethod
    def _parse_strategy_request(
        directives: Dict[str, object],
    ) -> Optional[Tuple[str, object, str]]:
        strategy = str(directives.get("strategy") or "").strip().lower()
        if strategy not in CONFIGURABLE_STRATEGIES:
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

    def persist_state(self, state: str) -> bool:
        """Persist and immediately apply a runtime-owned state transition."""

        normalized = str(state).strip().upper()
        if normalized not in _ALLOWED_STATES:
            raise ValueError(
                f"Unsupported automation state {state!r}; "
                f"expected one of {sorted(_ALLOWED_STATES)}"
            )
        try:
            self._control_store.set_state(normalized, source="runtime")
        except ControlDirectiveError as exc:
            log(f"[CTRL] Failed writing control file: {exc}", "WARN")
            return False
        self._last_applied_state = None
        self._apply_state(normalized)
        return True

    def persist_mode(self, mode: str) -> bool:
        """Persist and apply a runtime-owned terminal mode transition."""

        normalized = str(mode).strip().upper()
        if normalized not in _ALLOWED_MODES:
            raise ValueError(
                f"Unsupported automation mode {mode!r}; "
                f"expected one of {sorted(_ALLOWED_MODES)}"
            )
        try:
            self._control_store.set_mode(normalized, source="runtime")
        except ControlDirectiveError as exc:
            log(f"[CTRL] Failed writing control file: {exc}", "WARN")
            return False
        self._last_applied_mode = None
        self._apply_mode(normalized)
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
            "WARN",
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
            return self._control_store.read()
        except ControlDirectiveError as exc:
            log(f"[CTRL] Failed reading control file: {exc}", "WARN")
            return {}

    def _apply_state(
        self,
        state: object,
        *,
        acknowledge_unchanged: bool = False,
    ) -> None:
        if not isinstance(state, str) or not state:
            return
        normalized = state.upper()
        if normalized not in _ALLOWED_STATES:
            return
        if normalized == self._last_applied_state:
            if acknowledge_unchanged:
                log(
                    f"[CTRL] State set to {normalized} via control file",
                    "INFO",
                    console=True,
                )
            return
        try:
            AUTOMATION.state = normalized
            log(
                f"[CTRL] State set to {normalized} via control file",
                "INFO",
                console=True,
            )
            self._last_applied_state = normalized
        except Exception as exc:
            log(f"[CTRL] Failed to set state={normalized}: {exc}", "WARN")

    def _apply_mode(self, mode: object) -> None:
        if not isinstance(mode, str) or not mode:
            return
        normalized = mode.upper()
        if normalized not in _ALLOWED_MODES or normalized == self._last_applied_mode:
            return
        try:
            AUTOMATION.mode = normalized
            log(
                f"[CTRL] Mode set to {normalized} via control file",
                "INFO",
                console=True,
            )
            self._last_applied_mode = normalized
        except Exception as exc:
            log(f"[CTRL] Failed to set mode={normalized}: {exc}", "WARN")

    def _apply_adb_port(self, port: object, updated_at: object) -> None:
        if isinstance(port, bool) or not isinstance(port, int):
            return
        if not 1 <= port <= 65535 or self._adb_port_handoff is None:
            return

        request = (port, updated_at)
        if request == self._last_applied_adb_request:
            return
        target = f"localhost:{port}"
        if os.getenv("ADB_DEVICE") == target:
            self._last_applied_adb_request = request
            log(
                f"[CTRL] ADB target set to {target} via control file",
                "INFO",
                console=True,
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
        if self._adb_port_handoff(port):
            self._last_applied_adb_request = request
            self._next_adb_handoff_attempt_at = 0.0
            log(
                f"[CTRL] ADB target set to {target} via control file",
                "INFO",
                console=True,
            )
            return

        self._next_adb_handoff_attempt_at = now + 10.0
        log(
            f"[CTRL] ADB target handoff to localhost:{port} failed; "
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
        if not self.is_paused or deadline is None or time.time() < deadline:
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

        self._apply_state("RUNNING")
        self._pause_resume_at = None
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
