"""Typed state for one BlueStacks restart and The Tower replay window.

The host process mutation and the Android/UI recovery have different owners.
This module contains their shared, side-effect-free contracts so neither side
has to infer authority from a missing ADB connection or an unfamiliar frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional


EMULATOR_MAINTENANCE_SCHEMA_VERSION = 1
EMULATOR_MAINTENANCE_ACTION = "restart_bluestacks"
EMULATOR_HOST_ACK_TIMEOUT_SECONDS = 180
EMULATOR_HOME_POSTCONDITION_TIMEOUT_SECONDS = 15
EMULATOR_MAINTENANCE_STATES = frozenset(
    {"requested", "host_acknowledged", "host_restarted", "terminal"}
)
EMULATOR_MAINTENANCE_INITIATORS = frozenset(
    {"automatic_detector", "operator"}
)
EMULATOR_RECOVERY_ACK_STATES = frozenset(
    {
        "pending",
        "host_restart_authorized",
        "awaiting_host_restart",
        "awaiting_adb",
        "launching_game",
        "awaiting_welcome_back",
        "resume_dispatched",
        "replaying",
        "fallback_ending_run",
        "fallback_starting_battle",
        "terminal",
    }
)


class RecoveryUiAction(str, Enum):
    """One bounded input action selected from fresh recovery evidence."""

    NONE = "none"
    LAUNCH_GAME = "launch_game"
    RESUME = "resume"
    END_RUN = "end_run"
    START_NEW_BATTLE = "start_new_battle"


class RecoveryUiDispatchStatus(str, Enum):
    """Typed disposition for one verified Welcome Back transaction."""

    RESOLVED = "resolved"
    ALREADY_RESOLVED = "already_resolved"
    DEFERRED = "deferred"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class RecoveryUiDispatchOutcome:
    status: RecoveryUiDispatchStatus
    input_dispatched: bool = False
    attempts: int = 0
    final_state: str = "UNKNOWN"
    reason: str = ""

    @property
    def dispatched(self) -> bool:
        return self.input_dispatched

    @property
    def uncertain(self) -> bool:
        return self.status is RecoveryUiDispatchStatus.UNCERTAIN

    def __bool__(self) -> bool:
        return self.status is RecoveryUiDispatchStatus.RESOLVED


@dataclass(frozen=True)
class ReplayObservation:
    """Result of observing one wave against a restart high-water mark."""

    active: bool
    caught_up: bool
    regressed: bool
    expected_floor: Optional[int]
    high_water_wave: Optional[int]
    observed_wave: Optional[int]


@dataclass
class RestartReplayWindow:
    """Keep a legitimate app-restart rollback out of monotonic observers."""

    request_id: str
    high_water_wave: Optional[int]
    request_initiator: str = "automatic_detector"
    battle_scope: Optional[str] = None
    intro_sprint_active: bool = False
    resume_dispatched: bool = False
    active: bool = False
    caught_up: bool = False
    lowest_observed_wave: Optional[int] = None

    @property
    def expected_rollback_waves(self) -> int:
        # The Tower v28 resumes five waves back, or fifty while Intro Sprint is
        # active.  Completion is nevertheless based on the captured high-water
        # rather than trusting that implementation detail forever.
        return 50 if self.intro_sprint_active else 5

    @property
    def expected_floor(self) -> Optional[int]:
        if self.high_water_wave is None:
            return None
        return max(1, self.high_water_wave - self.expected_rollback_waves)

    def mark_resume_dispatched(self) -> None:
        self.resume_dispatched = True

    def observe(self, wave: Optional[int]) -> ReplayObservation:
        if wave is not None and (isinstance(wave, bool) or wave < 1):
            wave = None
        high_water = self.high_water_wave
        if wave is not None:
            if self.lowest_observed_wave is None:
                self.lowest_observed_wave = wave
            else:
                self.lowest_observed_wave = min(self.lowest_observed_wave, wave)
        regressed = bool(
            wave is not None and high_water is not None and wave < high_water
        )
        if self.resume_dispatched and wave is not None:
            if high_water is None:
                # With no trusted pre-restart OCR value, one fresh RUNNING wave
                # is the strongest available boundary.  Do not invent a delay.
                self.caught_up = True
            elif wave >= high_water:
                self.caught_up = True
            elif wave < high_water:
                self.active = True
        if self.caught_up:
            self.active = False
        return ReplayObservation(
            active=self.active,
            caught_up=self.caught_up,
            regressed=regressed,
            expected_floor=self.expected_floor,
            high_water_wave=high_water,
            observed_wave=wave,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "request_initiator": self.request_initiator,
            "battle_scope": self.battle_scope,
            "high_water_wave": self.high_water_wave,
            "intro_sprint_active": self.intro_sprint_active,
            "expected_rollback_waves": self.expected_rollback_waves,
            "expected_floor": self.expected_floor,
            "resume_dispatched": self.resume_dispatched,
            "replay_active": self.active,
            "caught_up": self.caught_up,
            "lowest_observed_wave": self.lowest_observed_wave,
            "exclude_from_degradation": True,
        }


def normalize_emulator_maintenance(value: object) -> Optional[dict[str, Any]]:
    """Return one bounded schema-1 maintenance directive or ``None``."""

    if not isinstance(value, Mapping):
        return None
    if value.get("schema_version") != EMULATOR_MAINTENANCE_SCHEMA_VERSION:
        return None
    request_id = str(value.get("request_id") or "").strip().lower()
    state = str(value.get("state") or "").strip().lower()
    action = str(value.get("action") or "").strip().lower()
    reason = " ".join(str(value.get("reason") or "").split())[:256]
    source = " ".join(str(value.get("source") or "").split())[:64]
    initiator = " ".join(
        str(value.get("initiator") or "automatic_detector").split()
    )[:32].lower()
    runtime = _runtime_binding(value.get("runtime"))
    requested_at = _bounded_text(value.get("requested_at"), 64)
    updated_at = _bounded_text(value.get("updated_at"), 64)
    if (
        len(request_id) != 32
        or any(character not in "0123456789abcdef" for character in request_id)
        or action != EMULATOR_MAINTENANCE_ACTION
        or state not in EMULATOR_MAINTENANCE_STATES
        or not reason
        or not source
        or initiator not in EMULATOR_MAINTENANCE_INITIATORS
        or runtime is None
        or not requested_at
        or not updated_at
    ):
        return None
    battle_scope = _bounded_text(value.get("battle_scope"), 128) or None
    if battle_scope is None:
        return None
    result: dict[str, Any] = {
        "schema_version": EMULATOR_MAINTENANCE_SCHEMA_VERSION,
        "request_id": request_id,
        "action": EMULATOR_MAINTENANCE_ACTION,
        "state": state,
        "reason": reason,
        "source": source,
        "initiator": initiator,
        "requested_at": requested_at,
        "updated_at": updated_at,
        "runtime": runtime,
        "battle_scope": battle_scope,
    }
    trigger = value.get("trigger")
    if isinstance(trigger, Mapping):
        result["trigger"] = _bounded_mapping(trigger)
    host_target = _host_identity(value.get("host_target"), include_new=False)
    if "host_target" in value and host_target is None:
        return None
    request_kind = (
        str(trigger.get("request_kind") or "").strip().lower()
        if isinstance(trigger, Mapping)
        else ""
    )
    if initiator == "operator" or request_kind in {
        "operator",
        "automatic_detector",
    }:
        if host_target is None:
            return None
    if host_target is not None:
        result["host_target"] = host_target
    host_ack = _host_identity(value.get("host_ack"), include_new=False)
    if state in {"host_acknowledged", "host_restarted"}:
        if host_ack is None:
            return None
        result["host_ack"] = host_ack
    elif host_ack is not None:
        result["host_ack"] = host_ack
    completion = _host_identity(value.get("host_completion"), include_new=True)
    if state == "host_restarted":
        if completion is None:
            return None
        result["host_completion"] = completion
    elif completion is not None:
        result["host_completion"] = completion
    if state == "terminal":
        terminal_at = _bounded_text(value.get("terminal_at"), 64)
        disposition = _bounded_text(value.get("terminal_disposition"), 48).lower()
        terminal_reason = " ".join(
            str(value.get("terminal_reason") or "").split()
        )[:256]
        if not terminal_at or not disposition or not terminal_reason:
            return None
        result.update(
            {
                "terminal_at": terminal_at,
                "terminal_disposition": disposition,
                "terminal_reason": terminal_reason,
            }
        )
    return result


def normalize_runtime_recovery_ack(value: object) -> Optional[dict[str, Any]]:
    """Validate the runtime-owned half of the maintenance handshake."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    request_id = str(value.get("request_id") or "").strip().lower()
    state = str(value.get("state") or "").strip().lower()
    runtime = _runtime_binding(value.get("runtime"))
    battle_scope = _bounded_text(value.get("battle_scope"), 128) or None
    observed_at = _bounded_text(value.get("observed_at"), 64)
    if (
        len(request_id) != 32
        or any(character not in "0123456789abcdef" for character in request_id)
        or state not in EMULATOR_RECOVERY_ACK_STATES
        or runtime is None
        or battle_scope is None
        or not observed_at
    ):
        return None
    result = {
        "schema_version": 1,
        "request_id": request_id,
        "state": state,
        "runtime": runtime,
        "observed_at": observed_at,
        "battle_scope": battle_scope,
        "high_water_wave": _optional_nonnegative_int(value.get("high_water_wave")),
        "intro_sprint_active": value.get("intro_sprint_active") is True,
        "replay_active": value.get("replay_active") is True,
        "exclude_from_degradation": value.get("exclude_from_degradation") is True,
        "reason": " ".join(str(value.get("reason") or "").split())[:256],
    }
    for name in (
        "expected_rollback_waves",
        "expected_floor",
        "lowest_observed_wave",
    ):
        normalized = _optional_nonnegative_int(value.get(name))
        if normalized is not None:
            result[name] = normalized
    return result


def _runtime_binding(value: object) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    runtime_id = str(value.get("runtime_id") or "").strip()
    target = str(value.get("adb_target") or "").strip()
    try:
        pid = int(value.get("pid"))
        target_generation = int(value.get("target_generation"))
    except (TypeError, ValueError):
        return None
    state_request_id = _bounded_text(value.get("state_request_id"), 96)
    if (
        not runtime_id
        or pid <= 0
        or not target
        or target == "unknown"
        or target_generation < 1
        or not state_request_id
    ):
        return None
    return {
        "runtime_id": runtime_id[:96],
        "pid": pid,
        "adb_target": target[:128],
        "target_generation": target_generation,
        "state_request_id": state_request_id,
    }


def _host_identity(value: object, *, include_new: bool) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    host_id = _bounded_text(value.get("host_id"), 128)
    observed_at = _bounded_text(value.get("observed_at"), 64)
    try:
        adb_port = int(value.get("adb_port"))
        process_id = int(value.get("process_id"))
    except (TypeError, ValueError):
        return None
    process_started_at = _bounded_text(value.get("process_started_at"), 64)
    executable_path = _bounded_text(value.get("executable_path"), 512)
    instance_name = _bounded_text(value.get("instance_name"), 64)
    if (
        not host_id
        or not observed_at
        or not 1 <= adb_port <= 65535
        or process_id <= 0
        or not process_started_at
        or not executable_path
        or not instance_name
    ):
        return None
    result: dict[str, Any] = {
        "host_id": host_id,
        "adb_port": adb_port,
        "process_id": process_id,
        "process_started_at": process_started_at,
        "executable_path": executable_path,
        "instance_name": instance_name,
        "observed_at": observed_at,
    }
    if include_new:
        try:
            previous_process_id = int(value.get("previous_process_id"))
        except (TypeError, ValueError):
            return None
        previous_started_at = _bounded_text(
            value.get("previous_process_started_at"), 64
        )
        if previous_process_id <= 0 or not previous_started_at:
            return None
        result.update(
            {
                "previous_process_id": previous_process_id,
                "previous_process_started_at": previous_started_at,
            }
        )
    return result


def _bounded_mapping(value: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in list(value.items())[:24]:
        name = _bounded_text(key, 64)
        if not name:
            continue
        if item is None or isinstance(item, (bool, int, float)):
            result[name] = item
        elif isinstance(item, str):
            result[name] = item[:256]
        elif isinstance(item, (list, tuple)):
            result[name] = [str(entry)[:128] for entry in item[:12]]
    return result


def _bounded_text(value: object, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _optional_nonnegative_int(value: object) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


__all__ = [
    "EMULATOR_MAINTENANCE_ACTION",
    "EMULATOR_HOST_ACK_TIMEOUT_SECONDS",
    "EMULATOR_MAINTENANCE_INITIATORS",
    "EMULATOR_MAINTENANCE_SCHEMA_VERSION",
    "EMULATOR_MAINTENANCE_STATES",
    "EMULATOR_RECOVERY_ACK_STATES",
    "RecoveryUiAction",
    "RecoveryUiDispatchOutcome",
    "RecoveryUiDispatchStatus",
    "ReplayObservation",
    "RestartReplayWindow",
    "normalize_emulator_maintenance",
    "normalize_runtime_recovery_ack",
]
