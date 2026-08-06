"""Bind one stable terminal save to the battle that just finished.

The exact-version player-save decoder already exposes a complete semantic
projection of the newest Battle History entry.  This module adds the narrower
runtime proof required before that projection may replace terminal More Stats
navigation: the current process must own the terminal run, and the newest save
tail must be one valid append or capped rollover beyond the baseline retained
for that same activity scope.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from core.player_save import PlayerSaveSnapshot
from core.player_save_history import (
    PLAYER_SAVE_HISTORY_SOURCE,
    history_metadata_from_snapshot,
    history_sources_compatible,
    valid_history_tail_advance,
)


TERMINAL_SAVE_REPORT_SCHEMA_VERSION = 1
_SUPPORTED_TERMINALS = {"GAME_OVER", "TOURNAMENT_RESULTS"}


def unavailable_terminal_save_report(
    reason: str,
    *,
    terminal_state: Optional[str] = None,
    captured_at: Optional[str] = None,
    mapping_id: Optional[str] = None,
    save_revision: Optional[int] = None,
) -> dict[str, Any]:
    """Return explicit evidence that keeps the verified More Stats fallback."""

    return {
        "schema_version": TERMINAL_SAVE_REPORT_SCHEMA_VERSION,
        "status": "unavailable",
        "complete": False,
        "reason": _safe_reason(reason),
        "terminal_state": _terminal_state(terminal_state),
        "mapping_id": mapping_id,
        "capture": {
            "captured_at": captured_at,
            "save_revision": save_revision,
        },
        "history_transition": {},
        "completed_entry": None,
        "ui_fallback": {
            "required": True,
            "reason": _safe_reason(reason),
        },
    }


def terminal_save_report_from_snapshot(
    snapshot: PlayerSaveSnapshot,
    *,
    terminal_state: str,
    run_binding: Mapping[str, Any],
    activity_scope: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a completed entry only after exact same-run tail attachment.

    Fingerprints are compared only within the player-save source contract.  A
    UI History baseline, missing scope, unbound terminal, unchanged tail,
    malformed count transition, unknown semantic cause, or terminal-kind
    mismatch leaves the existing terminal UI route authoritative.
    """

    terminal = _terminal_state(terminal_state)

    def unavailable(reason: str) -> dict[str, Any]:
        return unavailable_terminal_save_report(
            reason,
            terminal_state=terminal,
            captured_at=getattr(snapshot, "captured_at", None),
            mapping_id=getattr(snapshot, "mapping_id", None),
            save_revision=getattr(snapshot, "save_revision", None),
        )

    if terminal not in _SUPPORTED_TERMINALS:
        return unavailable("unsupported_terminal_state")
    if not isinstance(run_binding, Mapping) or run_binding.get("status") != "bound":
        return unavailable("terminal_run_unbound")
    if not isinstance(activity_scope, Mapping):
        return unavailable("activity_scope_unavailable")

    expected_scope_id = str(run_binding.get("activity_scope_run_id") or "")
    actual_scope_id = str(activity_scope.get("run_id") or "")
    if not expected_scope_id or actual_scope_id != expected_scope_id:
        return unavailable("terminal_activity_scope_binding_lost")

    baseline = activity_scope.get("latest_completed_battle")
    if not isinstance(baseline, Mapping):
        return unavailable("pre_terminal_history_baseline_unavailable")

    history_result = history_metadata_from_snapshot(
        snapshot,
        acquisition="stable_terminal_player_save",
    )
    if not history_result.complete or not isinstance(history_result.metadata, Mapping):
        return unavailable(
            history_result.reason or "terminal_history_tail_unavailable"
        )
    latest = history_result.metadata
    if latest.get("source") != PLAYER_SAVE_HISTORY_SOURCE:
        return unavailable("terminal_history_source_invalid")
    if not history_sources_compatible(baseline, latest):
        return unavailable("pre_terminal_history_source_incompatible")
    if baseline.get("fingerprint") == latest.get("fingerprint"):
        return unavailable("terminal_history_tail_unchanged")
    if not valid_history_tail_advance(baseline, latest):
        return unavailable("terminal_history_tail_transition_invalid")

    runtime = getattr(snapshot, "runtime_save", None)
    if runtime is None:
        return unavailable("runtime_history_projection_unavailable")
    if getattr(runtime, "round_active", True):
        return unavailable("terminal_save_still_active")
    tail = getattr(runtime, "battle_history_tail", None)
    entry = getattr(tail, "entry", None)
    if (
        tail is None
        or getattr(tail, "completed_entry_status", None) != "observed"
        or entry is None
    ):
        return unavailable(
            getattr(tail, "completed_entry_reason", None)
            or "semantic_completed_entry_unavailable"
        )

    expected_tournament = terminal == "TOURNAMENT_RESULTS"
    if bool(getattr(entry, "is_tournament", False)) is not expected_tournament:
        return unavailable("terminal_history_kind_mismatch")

    try:
        completed_entry = entry.as_dict()
        baseline_count = int(baseline["entry_count"])
        observed_count = int(latest["entry_count"])
        capacity = int(latest["capacity"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return unavailable("semantic_completed_entry_changed_shape")
    if not isinstance(completed_entry, Mapping):
        return unavailable("semantic_completed_entry_changed_shape")

    rollover = baseline_count == capacity and observed_count == capacity
    return {
        "schema_version": TERMINAL_SAVE_REPORT_SCHEMA_VERSION,
        "status": "complete",
        "complete": True,
        "reason": "",
        "terminal_state": terminal,
        "mapping_id": getattr(snapshot, "mapping_id", None),
        "capture": {
            "captured_at": getattr(snapshot, "captured_at", None),
            "save_revision": getattr(snapshot, "save_revision", None),
            "source_fingerprint": getattr(snapshot, "source_sha256", None),
            "acquisition": "stable_terminal_player_save",
        },
        "run_binding": {
            "status": "bound",
            "activity_scope_run_id": actual_scope_id,
        },
        "history_transition": {
            "status": "capacity_rollover" if rollover else "append",
            "baseline_fingerprint": baseline.get("fingerprint"),
            "observed_fingerprint": latest.get("fingerprint"),
            "baseline_entry_count": baseline_count,
            "observed_entry_count": observed_count,
            "capacity": capacity,
        },
        "completed_entry": dict(completed_entry),
        "ui_fallback": {
            "required": False,
            "reason": "",
        },
    }


def terminal_save_report_complete(value: Any) -> bool:
    """Return whether a caller may attempt save-derived record construction."""

    return bool(
        isinstance(value, Mapping)
        and value.get("schema_version") == TERMINAL_SAVE_REPORT_SCHEMA_VERSION
        and value.get("status") == "complete"
        and value.get("complete") is True
        and isinstance(value.get("completed_entry"), Mapping)
        and value.get("ui_fallback", {}).get("required") is False
    )


def _terminal_state(value: Optional[str]) -> str:
    return str(value or "UNKNOWN").strip().upper() or "UNKNOWN"


def _safe_reason(value: Any) -> str:
    normalized = "_".join(str(value or "unknown").strip().lower().split())
    return "".join(
        character
        for character in normalized[:160]
        if character.isalnum() or character in {"_", ":", "-"}
    ) or "unknown"


__all__ = [
    "TERMINAL_SAVE_REPORT_SCHEMA_VERSION",
    "terminal_save_report_complete",
    "terminal_save_report_from_snapshot",
    "unavailable_terminal_save_report",
]
