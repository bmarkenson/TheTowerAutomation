"""Source-bound Battle History continuity from stable player-save reads.

This module is deliberately observation-only.  It never backgrounds the game,
navigates the UI, consumes collector receipts, or authorizes lifecycle input.
The caller may use ``UI_FALLBACK`` only after the exact target, activity scope,
control authority, and source screen were all shown to remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Optional

from core.adb_target_session import ADB_TARGET_OPERATION_LOCK, AdbTargetSnapshot
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.player_save import (
    PlayerSaveSnapshot,
    decode_player_save_bytes,
    pull_player_save_bytes,
)
from core.state_detector import detect_state_and_overlays
from utils.logger import get_activity_scope


PLAYER_SAVE_HISTORY_SOURCE = "player_save"
PLAYER_SAVE_HISTORY_IDENTITY_SCHEMA_VERSION = 1
ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION = 2


class PlayerSaveHistoryReadStatus(str, Enum):
    COMPLETE = "complete"
    UI_FALLBACK = "ui_fallback"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PlayerSaveHistoryReadResult:
    status: PlayerSaveHistoryReadStatus
    reason: str
    metadata: Optional[Mapping[str, Any]] = None
    safe_ui_fallback: bool = False

    @property
    def complete(self) -> bool:
        return bool(
            self.status is PlayerSaveHistoryReadStatus.COMPLETE
            and self.metadata is not None
        )


def history_metadata_from_snapshot(
    snapshot: PlayerSaveSnapshot,
    *,
    acquisition: str,
) -> PlayerSaveHistoryReadResult:
    """Project only the structural newest-tail identity needed by continuity."""

    runtime = snapshot.runtime_save
    if runtime is None:
        return _ui_fallback("runtime_history_projection_unavailable")
    tail = runtime.battle_history_tail
    identity = tail.identity
    if tail.structural_status != "observed" or identity is None:
        return _ui_fallback(
            tail.structural_reason or "history_tail_identity_unavailable"
        )
    if (
        tail.capacity <= 0
        or tail.entry_count <= 0
        or tail.entry_count > tail.capacity
        or not identity.fingerprint
        or identity.mapping_id != runtime.mapping_id
    ):
        return _ui_fallback("history_tail_structure_invalid")

    return PlayerSaveHistoryReadResult(
        PlayerSaveHistoryReadStatus.COMPLETE,
        "structural_history_tail_observed",
        metadata={
            "schema_version": ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION,
            "source": PLAYER_SAVE_HISTORY_SOURCE,
            "mapping_id": identity.mapping_id,
            "identity_schema_version": (
                PLAYER_SAVE_HISTORY_IDENTITY_SCHEMA_VERSION
            ),
            "fingerprint": identity.fingerprint,
            "tier": identity.tier,
            "wave": identity.wave,
            "entry_count": tail.entry_count,
            "capacity": tail.capacity,
            "semantic_status": tail.completed_entry_status,
            "semantic_reason": _safe_reason(tail.completed_entry_reason),
            "captured_at": snapshot.captured_at,
            "acquisition": str(acquisition),
        },
    )


class PlayerSaveHistoryReader:
    """Acquire one stable save while preserving exact runtime source binding."""

    def __init__(
        self,
        *,
        target_snapshot_fn: Callable[[], AdbTargetSnapshot],
        capture_fn: Callable[[], Any],
        detector: Callable[[Any], Mapping[str, Any]] = (
            detect_state_and_overlays
        ),
        home_control_fn: Callable[[Any], Any] = detect_home_battle_control,
        scope_fn: Callable[[], Optional[Mapping[str, Any]]] = get_activity_scope,
        pull_fn: Callable[..., bytes] = pull_player_save_bytes,
        decode_fn: Callable[..., PlayerSaveSnapshot] = decode_player_save_bytes,
    ) -> None:
        self._target_snapshot_fn = target_snapshot_fn
        self._capture_fn = capture_fn
        self._detector = detector
        self._home_control_fn = home_control_fn
        self._scope_fn = scope_fn
        self._pull_fn = pull_fn
        self._decode_fn = decode_fn

    def read(
        self,
        *,
        source_state: str,
        expected_home_control: HomeBattleControl,
        expected_scope_id: str,
        action_guard_fn: Callable[[], bool],
    ) -> PlayerSaveHistoryReadResult:
        normalized_source = str(source_state or "").upper()
        if normalized_source not in {"RUNNING", "HOME_SCREEN"}:
            return _blocked("save_history_source_unsupported")

        with ADB_TARGET_OPERATION_LOCK:
            try:
                target_before = self._target_snapshot_fn()
            except Exception:
                return _blocked("exact_target_ownership_unverified")
            if (
                not target_before.owned
                or not target_before.target
                or not _scope_matches(self._scope_fn, expected_scope_id)
                or not _action_allowed(action_guard_fn)
                or not self._source_matches(
                    normalized_source,
                    expected_home_control,
                )
            ):
                return _blocked("history_source_binding_unverified")

            try:
                pull_kwargs: dict[str, Any] = {
                    "device_id": target_before.target
                }
                if self._pull_fn is pull_player_save_bytes:
                    pull_kwargs["read_fn"] = _quiet_player_save_read
                payload = self._pull_fn(**pull_kwargs)
                snapshot = self._decode_fn(
                    payload,
                    source_name="playerInfo.dat",
                )
                del payload
                observed = history_metadata_from_snapshot(
                    snapshot,
                    acquisition="stable_two_identical_read_exact_target",
                )
            except Exception:
                observed = _ui_fallback("save_history_acquisition_failed")

            try:
                target_after = self._target_snapshot_fn()
            except Exception:
                return _blocked("exact_target_ownership_lost")
            if (
                not _same_target(target_before, target_after)
                or not _scope_matches(self._scope_fn, expected_scope_id)
                or not _action_allowed(action_guard_fn)
                or not self._source_matches(
                    normalized_source,
                    expected_home_control,
                )
            ):
                return _blocked("history_source_binding_lost")
            return observed

    def _source_matches(
        self,
        source_state: str,
        expected_home_control: HomeBattleControl,
    ) -> bool:
        try:
            frame = self._capture_fn()
            if frame is None:
                return False
            detection = self._detector(frame)
            state = str(detection.get("state") or "").upper()
            if source_state == "RUNNING":
                return state == "RUNNING"
            if state not in {"HOME", "HOME_SCREEN"}:
                return False
            return (
                self._home_control_fn(frame).control
                is expected_home_control
            )
        except Exception:
            return False


def history_sources_compatible(
    first: Optional[Mapping[str, Any]],
    second: Optional[Mapping[str, Any]],
) -> bool:
    """Return whether two fingerprints share one proven source contract."""

    if first is None or second is None:
        return False
    return bool(
        first.get("schema_version") == ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION
        and second.get("schema_version")
        == ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION
        and str(first.get("source") or "")
        == str(second.get("source") or "")
        and str(first.get("mapping_id") or "")
        == str(second.get("mapping_id") or "")
        and first.get("identity_schema_version")
        == second.get("identity_schema_version")
        and str(first.get("fingerprint") or "")
        and str(second.get("fingerprint") or "")
    )


def valid_history_tail_advance(
    previous: Mapping[str, Any],
    latest: Mapping[str, Any],
) -> bool:
    """Validate one append or a capacity-preserving 30-entry rollover."""

    if not history_sources_compatible(previous, latest):
        return False
    try:
        previous_count = int(previous["entry_count"])
        latest_count = int(latest["entry_count"])
        previous_capacity = int(previous["capacity"])
        latest_capacity = int(latest["capacity"])
    except (KeyError, TypeError, ValueError):
        return False
    if (
        previous_capacity <= 0
        or latest_capacity != previous_capacity
        or not 0 < previous_count <= previous_capacity
        or not 0 < latest_count <= latest_capacity
        or previous.get("fingerprint") == latest.get("fingerprint")
    ):
        return False
    if previous_count < previous_capacity:
        return latest_count == previous_count + 1
    return latest_count == previous_capacity


def _scope_matches(
    scope_fn: Callable[[], Optional[Mapping[str, Any]]],
    expected_scope_id: str,
) -> bool:
    try:
        scope = scope_fn()
    except Exception:
        return False
    return bool(
        isinstance(scope, Mapping)
        and str(scope.get("run_id") or "") == str(expected_scope_id or "")
    )


def _action_allowed(action_guard_fn: Callable[[], bool]) -> bool:
    try:
        return action_guard_fn() is True
    except Exception:
        return False


def _same_target(
    before: AdbTargetSnapshot,
    after: AdbTargetSnapshot,
) -> bool:
    return bool(
        before.owned
        and after.owned
        and before.target == after.target
        and before.generation == after.generation
    )


def _quiet_player_save_read(
    path: str,
    *,
    device_id: Optional[str] = None,
) -> Optional[bytes]:
    from core.adb_utils import read_device_file

    return read_device_file(
        path,
        device_id=device_id,
        report_errors=False,
    )


def _safe_reason(value: Any) -> str:
    normalized = "_".join(str(value or "").strip().lower().split())
    return "".join(
        character
        for character in normalized[:160]
        if character.isalnum() or character in {"_", ":", "-"}
    )


def _ui_fallback(reason: str) -> PlayerSaveHistoryReadResult:
    return PlayerSaveHistoryReadResult(
        PlayerSaveHistoryReadStatus.UI_FALLBACK,
        _safe_reason(reason),
        safe_ui_fallback=True,
    )


def _blocked(reason: str) -> PlayerSaveHistoryReadResult:
    return PlayerSaveHistoryReadResult(
        PlayerSaveHistoryReadStatus.BLOCKED,
        _safe_reason(reason),
        safe_ui_fallback=False,
    )


__all__ = [
    "ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION",
    "PLAYER_SAVE_HISTORY_SOURCE",
    "PlayerSaveHistoryReadResult",
    "PlayerSaveHistoryReadStatus",
    "PlayerSaveHistoryReader",
    "history_metadata_from_snapshot",
    "history_sources_compatible",
    "valid_history_tail_advance",
]
