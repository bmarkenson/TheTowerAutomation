"""Bind one stable terminal save to the battle that just finished.

The exact-version player-save decoder already exposes a complete semantic
projection of the newest Battle History entry.  This module adds the narrower
runtime proof required before that projection may replace terminal More Stats
navigation: the current process must own the terminal run, and the newest save
tail must be one valid append or capped rollover beyond the baseline retained
for that same activity scope.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Mapping, Optional

from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveTargetBinding,
)
from core.player_save_history import (
    PLAYER_SAVE_HISTORY_SOURCE,
    history_metadata_from_acquisition,
    history_sources_compatible,
    valid_history_tail_advance,
)


TERMINAL_SAVE_REPORT_SCHEMA_VERSION = 1
TERMINAL_HISTORY_TRANSITION_SCHEMA_VERSION = 1
TERMINAL_HISTORY_HANDOFF_SCHEMA_VERSION = 1
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


def terminal_save_report_from_acquisition(
    acquisition: PlayerSaveAcquisitionBundle,
    *,
    terminal_state: str,
    run_binding: Mapping[str, Any],
    activity_scope: Optional[Mapping[str, Any]],
    history_transition: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Return a completed entry only after exact same-run tail attachment.

    Fingerprints are compared only within the player-save source contract.  A
    UI History baseline, missing scope, unbound terminal, unchanged tail,
    malformed count transition, unknown semantic cause, or terminal-kind
    mismatch leaves the existing terminal UI route authoritative.
    """

    if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
        raise TypeError("terminal report requires a typed acquisition")
    snapshot = acquisition.snapshot
    terminal = _terminal_state(terminal_state)

    def unavailable(reason: str) -> dict[str, Any]:
        return unavailable_terminal_save_report(
            reason,
            terminal_state=terminal,
            captured_at=getattr(snapshot, "captured_at", None),
            mapping_id=getattr(snapshot, "mapping_id", None),
            save_revision=getattr(snapshot, "save_revision", None),
        )

    structural = (
        terminal_history_transition_from_acquisition(
            acquisition,
            terminal_state=terminal,
            run_binding=run_binding,
            activity_scope=activity_scope,
        )
        if history_transition is None
        else dict(history_transition)
    )
    if not _terminal_history_transition_complete(structural):
        return unavailable(
            structural.get("reason")
            if isinstance(structural, Mapping)
            else "terminal_history_transition_unavailable"
        )
    if not acquisition.complete or snapshot is None:
        return unavailable(acquisition.reason)
    if structural.get("terminal_state") != terminal:
        return unavailable("terminal_history_transition_kind_mismatch")
    if structural.get("mapping_id") != getattr(snapshot, "mapping_id", None):
        return unavailable("terminal_history_transition_mapping_mismatch")
    structural_handoff = structural.get("handoff")
    structural_source = (
        structural_handoff.get("source")
        if isinstance(structural_handoff, Mapping)
        else None
    )
    structural_binding = structural.get("run_binding")
    expected_scope_id = (
        str(run_binding.get("activity_scope_run_id") or "")
        if isinstance(run_binding, Mapping)
        else ""
    )
    actual_scope_id = (
        str(activity_scope.get("run_id") or "")
        if isinstance(activity_scope, Mapping)
        else ""
    )
    if not (
        isinstance(structural_source, Mapping)
        and isinstance(structural_binding, Mapping)
        and expected_scope_id
        and actual_scope_id == expected_scope_id
        and structural_binding.get("activity_scope_run_id")
        == actual_scope_id
        and terminal_history_handoff_matches_source_scope(
            structural_handoff, actual_scope_id
        )
        and structural_source.get("mapping_id")
        == getattr(snapshot, "mapping_id", None)
        and structural_source.get("source_fingerprint")
        == getattr(snapshot, "source_sha256", None)
        and structural_source.get("target_generation_fingerprint")
        == acquisition.binding_fingerprint
        and structural_source.get("acquisition")
        == acquisition.redacted_provenance()
    ):
        return unavailable("terminal_history_transition_provenance_mismatch")

    runtime = getattr(snapshot, "runtime_save", None)
    if runtime is None:
        return _semantic_unavailable(
            unavailable("runtime_history_projection_unavailable"), structural
        )
    tail = getattr(runtime, "battle_history_tail", None)
    entry = getattr(tail, "entry", None)
    if (
        tail is None
        or getattr(tail, "completed_entry_status", None) != "observed"
        or entry is None
    ):
        return _semantic_unavailable(
            unavailable(
                getattr(tail, "completed_entry_reason", None)
                or "semantic_completed_entry_unavailable"
            ),
            structural,
        )

    expected_tournament = terminal == "TOURNAMENT_RESULTS"
    if bool(getattr(entry, "is_tournament", False)) is not expected_tournament:
        return _semantic_unavailable(
            unavailable("terminal_history_kind_mismatch"), structural
        )

    try:
        completed_entry = entry.as_dict()
    except (AttributeError, KeyError, TypeError, ValueError):
        return _semantic_unavailable(
            unavailable("semantic_completed_entry_changed_shape"), structural
        )
    if not isinstance(completed_entry, Mapping):
        return _semantic_unavailable(
            unavailable("semantic_completed_entry_changed_shape"), structural
        )

    return {
        "schema_version": TERMINAL_SAVE_REPORT_SCHEMA_VERSION,
        "status": "complete",
        "complete": True,
        "reason": "",
        "terminal_state": terminal,
        "mapping_id": getattr(snapshot, "mapping_id", None),
        "capture": dict(structural.get("capture") or {}),
        "run_binding": dict(structural.get("run_binding") or {}),
        "history_transition": dict(
            structural.get("history_transition") or {}
        ),
        "completed_entry": dict(completed_entry),
        "ui_fallback": {
            "required": False,
            "reason": "",
        },
    }


def terminal_history_transition_from_acquisition(
    acquisition: PlayerSaveAcquisitionBundle,
    *,
    terminal_state: str,
    run_binding: Mapping[str, Any],
    activity_scope: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project one terminal structural tail independently of report semantics."""

    if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
        raise TypeError("terminal History transition requires a typed acquisition")
    snapshot = acquisition.snapshot
    terminal = _terminal_state(terminal_state)

    def unavailable(reason: str) -> dict[str, Any]:
        return {
            "schema_version": TERMINAL_HISTORY_TRANSITION_SCHEMA_VERSION,
            "status": "unavailable",
            "complete": False,
            "reason": _safe_reason(reason),
            "terminal_state": terminal,
            "mapping_id": getattr(snapshot, "mapping_id", None),
            "capture": {
                "captured_at": getattr(snapshot, "captured_at", None),
                "save_revision": getattr(snapshot, "save_revision", None),
            },
            "run_binding": {},
            "latest_completed_battle": None,
            "history_transition": {},
            "handoff": None,
        }

    if not acquisition.complete or snapshot is None:
        return unavailable(acquisition.reason)
    if terminal not in _SUPPORTED_TERMINALS:
        return unavailable("unsupported_terminal_state")
    if (
        acquisition.acquisition_type
        is not PlayerSaveAcquisitionType.NATURAL_BOUNDARY
        or acquisition.boundary is None
    ):
        return unavailable("terminal_acquisition_type_invalid")
    expected_boundary_kind = PlayerSaveBoundaryKind(terminal)
    if acquisition.boundary.kind is not expected_boundary_kind:
        return unavailable("terminal_natural_boundary_kind_mismatch")
    if not isinstance(run_binding, Mapping) or run_binding.get("status") != "bound":
        return unavailable("terminal_run_unbound")
    if not isinstance(activity_scope, Mapping):
        return unavailable("activity_scope_unavailable")

    expected_scope_id = str(run_binding.get("activity_scope_run_id") or "")
    actual_scope_id = str(activity_scope.get("run_id") or "")
    if not expected_scope_id or actual_scope_id != expected_scope_id:
        return unavailable("terminal_activity_scope_binding_lost")
    if acquisition.boundary.activity_scope_id != actual_scope_id:
        return unavailable("terminal_natural_boundary_scope_mismatch")

    baseline = activity_scope.get("latest_completed_battle")
    if not isinstance(baseline, Mapping):
        return unavailable("pre_terminal_history_baseline_unavailable")

    history_result = history_metadata_from_acquisition(acquisition)
    if not history_result.complete or not isinstance(history_result.metadata, Mapping):
        return unavailable(
            history_result.reason or "terminal_history_tail_unavailable"
        )
    latest = dict(history_result.metadata)
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
    source_fingerprint = str(
        getattr(snapshot, "source_sha256", None) or ""
    ).strip().lower()
    if not _sha256_fingerprint(source_fingerprint):
        return unavailable("terminal_source_fingerprint_unavailable")
    try:
        baseline_count = int(baseline["entry_count"])
        observed_count = int(latest["entry_count"])
        capacity = int(latest["capacity"])
    except (KeyError, TypeError, ValueError):
        return unavailable("terminal_history_tail_transition_invalid")
    rollover = baseline_count == capacity and observed_count == capacity
    transition = {
        "status": "capacity_rollover" if rollover else "append",
        "baseline_fingerprint": baseline.get("fingerprint"),
        "observed_fingerprint": latest.get("fingerprint"),
        "baseline_entry_count": baseline_count,
        "observed_entry_count": observed_count,
        "capacity": capacity,
    }
    capture = {
        "captured_at": getattr(snapshot, "captured_at", None),
        "save_revision": getattr(snapshot, "save_revision", None),
        "source_fingerprint": source_fingerprint,
        "acquisition": acquisition.redacted_provenance(),
    }
    handoff = {
        "schema_version": TERMINAL_HISTORY_HANDOFF_SCHEMA_VERSION,
        "status": "ready",
        "terminal_state": terminal,
        "latest_completed_battle": latest,
        "history_transition": transition,
        "source": {
            "mapping_id": getattr(snapshot, "mapping_id", None),
            "source_fingerprint": source_fingerprint,
            "runtime_session_fingerprint": _redacted_identity(
                "runtime", acquisition.boundary.runtime_session_id
            ),
            "activity_scope_fingerprint": _redacted_identity(
                "scope", actual_scope_id
            ),
            "target_generation_fingerprint": acquisition.binding_fingerprint,
            "boundary": acquisition.boundary.redacted(),
            "acquisition": acquisition.redacted_provenance(),
        },
    }
    return {
        "schema_version": TERMINAL_HISTORY_TRANSITION_SCHEMA_VERSION,
        "status": "complete",
        "complete": True,
        "reason": "",
        "terminal_state": terminal,
        "mapping_id": getattr(snapshot, "mapping_id", None),
        "capture": capture,
        "run_binding": {
            "status": "bound",
            "activity_scope_run_id": actual_scope_id,
        },
        "latest_completed_battle": latest,
        "history_transition": transition,
        "handoff": handoff,
    }


def validate_terminal_history_handoff(
    value: Any,
    *,
    runtime_session_id: str,
    target_binding: Optional[PlayerSaveTargetBinding],
    destination_reason: str,
) -> tuple[Optional[dict[str, Any]], str]:
    """Validate one persisted handoff for exactly one new activity scope."""

    if not isinstance(value, Mapping):
        return None, "terminal_history_handoff_unavailable"
    if (
        value.get("schema_version") != TERMINAL_HISTORY_HANDOFF_SCHEMA_VERSION
        or value.get("status") != "ready"
    ):
        return None, "terminal_history_handoff_schema_invalid"
    terminal = _terminal_state(value.get("terminal_state"))
    normalized_destination = str(destination_reason or "").strip().lower()
    if normalized_destination == "game_over_retry":
        if terminal != "GAME_OVER":
            return None, "terminal_history_handoff_route_invalid"
    elif normalized_destination == "new_battle_preflight":
        if terminal not in _SUPPORTED_TERMINALS:
            return None, "terminal_history_handoff_route_invalid"
    else:
        return None, "terminal_history_handoff_destination_invalid"

    source = value.get("source")
    latest = value.get("latest_completed_battle")
    transition = value.get("history_transition")
    if not all(isinstance(item, Mapping) for item in (source, latest, transition)):
        return None, "terminal_history_handoff_shape_invalid"
    assert isinstance(source, Mapping)
    assert isinstance(latest, Mapping)
    assert isinstance(transition, Mapping)
    runtime_id = str(runtime_session_id or "").strip()
    if not runtime_id or source.get("runtime_session_fingerprint") != (
        _redacted_identity("runtime", runtime_id)
    ):
        return None, "terminal_history_handoff_process_changed"
    if (
        target_binding is None
        or source.get("target_generation_fingerprint")
        != target_binding.fingerprint
    ):
        return None, "terminal_history_handoff_target_changed"

    mapping_id = str(source.get("mapping_id") or "").strip()
    fingerprint = str(latest.get("fingerprint") or "").strip()
    if not (
        latest.get("schema_version") == 2
        and latest.get("source") == PLAYER_SAVE_HISTORY_SOURCE
        and latest.get("identity_schema_version") == 1
        and mapping_id
        and latest.get("mapping_id") == mapping_id
        and fingerprint
    ):
        return None, "terminal_history_handoff_identity_invalid"
    try:
        baseline_count = int(transition["baseline_entry_count"])
        observed_count = int(transition["observed_entry_count"])
        capacity = int(transition["capacity"])
        latest_count = int(latest.get("entry_count"))
        latest_capacity = int(latest.get("capacity"))
    except (KeyError, TypeError, ValueError):
        return None, "terminal_history_handoff_transition_invalid"
    transition_status = transition.get("status")
    valid_counts = bool(
        capacity > 0
        and observed_count > 0
        and (
            (
                transition_status == "append"
                and observed_count == baseline_count + 1
                and observed_count <= capacity
            )
            or (
                transition_status == "capacity_rollover"
                and baseline_count == observed_count == capacity
            )
        )
    )
    if (
        not valid_counts
        or transition.get("observed_fingerprint") != fingerprint
        or latest_count != observed_count
        or latest_capacity != capacity
    ):
        return None, "terminal_history_handoff_transition_invalid"

    boundary = source.get("boundary")
    acquisition = source.get("acquisition")
    latest_acquisition = latest.get("acquisition")
    if not isinstance(boundary, Mapping) or not isinstance(acquisition, Mapping):
        return None, "terminal_history_handoff_provenance_invalid"
    if not (
        boundary.get("kind") == terminal
        and boundary.get("runtime_session")
        == source.get("runtime_session_fingerprint")
        and boundary.get("activity_scope")
        == source.get("activity_scope_fingerprint")
        and acquisition.get("type")
        == PlayerSaveAcquisitionType.NATURAL_BOUNDARY.value
        and acquisition.get("status") == "complete"
        and acquisition.get("binding_fingerprint")
        == target_binding.fingerprint
        and acquisition.get("transport_stable") is True
        and acquisition.get("boundary") == boundary
        and isinstance(latest_acquisition, Mapping)
        and latest_acquisition == acquisition
    ):
        return None, "terminal_history_handoff_provenance_invalid"
    try:
        boundary_at = datetime.fromisoformat(
            str(boundary.get("observed_at") or "")
        )
        timing = acquisition.get("timing")
        if not isinstance(timing, Mapping):
            raise ValueError
        started_at = datetime.fromisoformat(str(timing.get("started_at") or ""))
        captured_at = datetime.fromisoformat(str(timing.get("captured_at") or ""))
        completed_at = datetime.fromisoformat(
            str(timing.get("completed_at") or "")
        )
        if not (
            boundary_at.tzinfo is not None
            and started_at.tzinfo is not None
            and captured_at.tzinfo is not None
            and completed_at.tzinfo is not None
            and boundary_at <= completed_at
            and started_at <= captured_at <= completed_at
            and str(latest.get("captured_at") or "")
            == str(timing.get("captured_at") or "")
        ):
            raise ValueError
    except (TypeError, ValueError):
        return None, "terminal_history_handoff_time_invalid"
    return dict(latest), "terminal_history_handoff_accepted"


def terminal_history_handoff_matches_source_scope(
    value: Any,
    scope_id: str,
) -> bool:
    """Return whether redacted handoff provenance names this exact source scope."""

    if not isinstance(value, Mapping):
        return False
    source = value.get("source")
    boundary = source.get("boundary") if isinstance(source, Mapping) else None
    expected = str(scope_id or "").strip()
    return bool(
        expected
        and isinstance(boundary, Mapping)
        and source.get("activity_scope_fingerprint")
        == _redacted_identity("scope", expected)
        and boundary.get("activity_scope")
        == source.get("activity_scope_fingerprint")
    )


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


def _terminal_history_transition_complete(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("schema_version")
        == TERMINAL_HISTORY_TRANSITION_SCHEMA_VERSION
        and value.get("status") == "complete"
        and value.get("complete") is True
        and isinstance(value.get("latest_completed_battle"), Mapping)
        and isinstance(value.get("history_transition"), Mapping)
        and isinstance(value.get("handoff"), Mapping)
    )


def _semantic_unavailable(
    report: dict[str, Any],
    structural: Mapping[str, Any],
) -> dict[str, Any]:
    """Retain structural success while leaving the report UI authoritative."""

    report["history_transition"] = dict(
        structural.get("history_transition") or {}
    )
    report["structural_history"] = {
        "status": "complete",
        "reason": "",
    }
    return report


def _terminal_state(value: Optional[str]) -> str:
    return str(value or "UNKNOWN").strip().upper() or "UNKNOWN"


def _safe_reason(value: Any) -> str:
    normalized = "_".join(str(value or "unknown").strip().lower().split())
    return "".join(
        character
        for character in normalized[:160]
        if character.isalnum() or character in {"_", ":", "-"}
    ) or "unknown"


def _redacted_identity(label: str, value: str) -> str:
    return hashlib.sha256(
        f"thetower-player-save-{label}-v1\0{value}".encode("utf-8")
    ).hexdigest()


def _sha256_fingerprint(value: str) -> bool:
    return bool(
        len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "TERMINAL_HISTORY_HANDOFF_SCHEMA_VERSION",
    "TERMINAL_HISTORY_TRANSITION_SCHEMA_VERSION",
    "TERMINAL_SAVE_REPORT_SCHEMA_VERSION",
    "terminal_history_transition_from_acquisition",
    "terminal_history_handoff_matches_source_scope",
    "terminal_save_report_complete",
    "terminal_save_report_from_acquisition",
    "unavailable_terminal_save_report",
    "validate_terminal_history_handoff",
]
