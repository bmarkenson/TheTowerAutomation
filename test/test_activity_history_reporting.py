import json
from datetime import datetime, timezone

import pytest

from core.activity_history_reporting import ActivityHistoryReporter
from core.adb_target_session import AdbTargetSnapshot
from core.player_save_acquisition import (
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
)
from utils import logger


def _save_metadata(
    *,
    fingerprint="a" * 64,
    entry_count=29,
    capacity=30,
    tier=19,
    wave=1899,
    is_tournament=False,
):
    return {
        "schema_version": 2,
        "source": "player_save",
        "mapping_id": "data-9-game-1073",
        "effective_mapping_fingerprint": "9" * 64,
        "identity_schema_version": 2,
        "fingerprint": fingerprint,
        "tier": tier,
        "wave": wave,
        "is_tournament": is_tournament,
        "battle_date": {
            "kind_id": 2,
            "kind": "local",
            "ticks": "639197340971234560",
            "clock_time": "2026-07-15T01:41:37.123456",
            "clock_basis": "local_wall_clock_without_offset",
            "submicrosecond_100ns": 0,
        },
        "entry_count": entry_count,
        "capacity": capacity,
        "semantic_status": "observed",
        "semantic_reason": "",
        "captured_at": "2026-08-04T20:00:00+00:00",
        "acquisition": "stable_two_identical_read_exact_target",
    }


def _scope_with_save_baseline(metadata):
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    updated = logger.record_activity_scope_battle_history(
        run_id=str(scope["run_id"]),
        latest_completed_battle=metadata,
    )
    assert updated is not None
    return updated


def _terminal_handoff(
    *,
    source_scope_id: str,
    target: AdbTargetSnapshot,
    runtime_session_id: str = "runtime-1",
    terminal_state: str = "GAME_OVER",
):
    binding = PlayerSaveTargetBinding.from_snapshot(target)
    assert binding is not None
    boundary = PlayerSaveNaturalBoundary(
        kind=PlayerSaveBoundaryKind(terminal_state),
        observed_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        runtime_session_id=runtime_session_id,
        activity_scope_id=source_scope_id,
        active_round_identity_fingerprint="d" * 64,
    )
    boundary_evidence = boundary.redacted()
    latest = _save_metadata(
        fingerprint="b" * 64,
        entry_count=30,
        is_tournament=terminal_state == "TOURNAMENT_RESULTS",
    )
    latest["captured_at"] = "2026-08-07T00:00:01+00:00"
    acquisition = {
        "schema_version": 1,
        "type": PlayerSaveAcquisitionType.NATURAL_BOUNDARY.value,
        "status": "complete",
        "reason": "save_acquired",
        "binding_fingerprint": binding.fingerprint,
        "transport_stable": True,
        "timing": {
            "started_at": "2026-08-07T00:00:00+00:00",
            "captured_at": "2026-08-07T00:00:01+00:00",
            "completed_at": "2026-08-07T00:00:02+00:00",
        },
        "boundary": boundary_evidence,
    }
    latest["acquisition"] = acquisition
    return {
        "schema_version": 1,
        "status": "ready",
        "terminal_state": terminal_state,
        "latest_completed_battle": latest,
        "history_transition": {
            "status": "append",
            "baseline_fingerprint": "a" * 64,
            "observed_fingerprint": "b" * 64,
            "baseline_entry_count": 29,
            "observed_entry_count": 30,
            "capacity": 30,
        },
        "source": {
            "mapping_id": latest["mapping_id"],
            "effective_mapping_fingerprint": latest[
                "effective_mapping_fingerprint"
            ],
            "source_fingerprint": "c" * 64,
            "runtime_session_fingerprint": boundary_evidence[
                "runtime_session"
            ],
            "activity_scope_fingerprint": boundary_evidence[
                "activity_scope"
            ],
            "target_generation_fingerprint": binding.fingerprint,
            "boundary": boundary_evidence,
            "acquisition": acquisition,
        },
    }


def _stage_terminal_handoff(*, target, terminal_state="GAME_OVER"):
    source = _scope_with_save_baseline(
        _save_metadata(fingerprint="a" * 64, entry_count=29)
    )
    handoff = _terminal_handoff(
        source_scope_id=str(source["run_id"]),
        target=target,
        terminal_state=terminal_state,
    )
    coordinator = ActivityHistoryReporter()
    assert coordinator.publish_terminal_history_handoff(
        {
            "status": "complete",
            "complete": True,
            "handoff": handoff,
            "run_binding": {
                "activity_scope_run_id": str(source["run_id"]),
            },
        }
    )
    return coordinator


def test_reporter_has_no_lifecycle_or_acquisition_entrypoints():
    coordinator = ActivityHistoryReporter()

    assert not hasattr(coordinator, "needs_check")
    assert not hasattr(coordinator, "handle")
    assert not hasattr(coordinator, "request_running_reconciliation")


def test_home_forced_save_baseline_is_best_effort_report_metadata(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    metadata = _save_metadata()

    outcome = ActivityHistoryReporter().accept_home_save_baseline(
        {
            "disposition": "save_match",
            "mapping_id": metadata["mapping_id"],
            "metadata": metadata,
        },
        expected_scope_id=str(scope["run_id"]),
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.accepted
    assert not outcome.blocked
    assert not outcome.ui_required
    assert current is not None
    assert current["latest_completed_battle"] == metadata
    saved = json.loads(
        (tmp_path / "logs" / "activity_scope.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved == current


@pytest.mark.parametrize(
    ("tail", "expected_scope", "reason"),
    (
        ({"disposition": "unavailable"}, "scope", "save_unavailable"),
        (
            {
                "disposition": "save_match",
                "mapping_id": "wrong-mapping",
                "metadata": _save_metadata(),
            },
            "scope",
            "save_history_report_metadata_invalid",
        ),
        (
            {
                "disposition": "save_match",
                "mapping_id": "data-9-game-1073",
                "metadata": _save_metadata(),
            },
            "different-scope",
            "home_history_report_scope_unavailable",
        ),
    ),
)
def test_missing_home_report_metadata_never_blocks_or_routes_ui(
    tmp_path,
    monkeypatch,
    tail,
    expected_scope,
    reason,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    if expected_scope == "scope":
        expected_scope = str(scope["run_id"])
    tail = dict(tail)
    tail.setdefault("reason", "save_unavailable")

    outcome = ActivityHistoryReporter().accept_home_save_baseline(
        tail,
        expected_scope_id=expected_scope,
        player_save_mode="save_first",
    )

    assert not outcome.accepted
    assert not outcome.blocked
    assert not outcome.ui_required
    assert outcome.reason == reason


def test_home_report_write_loss_never_blocks_or_routes_ui(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    monkeypatch.setattr(
        "core.activity_history_reporting.record_activity_scope_battle_history",
        lambda **_kwargs: None,
    )
    metadata = _save_metadata()

    outcome = ActivityHistoryReporter().accept_home_save_baseline(
        {
            "disposition": "save_match",
            "mapping_id": metadata["mapping_id"],
            "metadata": metadata,
        },
        expected_scope_id=str(scope["run_id"]),
        player_save_mode="save_first",
    )

    assert not outcome.accepted
    assert not outcome.blocked
    assert not outcome.ui_required
    assert outcome.reason == "save_history_baseline_report_write_failed"


@pytest.mark.parametrize(
    ("destination", "terminal_state"),
    (
        ("home", "GAME_OVER"),
        ("retry", "GAME_OVER"),
        ("home", "TOURNAMENT_RESULTS"),
    ),
)
def test_terminal_handoff_is_report_only_and_needs_no_save_or_ui_read(
    tmp_path,
    monkeypatch,
    destination,
    terminal_state,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    target = AdbTargetSnapshot("private-target", 3, True)
    coordinator = _stage_terminal_handoff(
        target=target,
        terminal_state=terminal_state,
    )
    if destination == "retry":
        next_scope = logger.start_retry_activity_scope()
    else:
        next_scope = logger.start_activity_scope(
            reason="new_battle_preflight",
            carry_terminal_history_handoff=True,
        )
    assert next_scope is not None

    outcome = coordinator.accept_pending_terminal_history_handoff(
        expected_scope_id=str(next_scope["run_id"]),
        runtime_session_id="runtime-1",
        target_snapshot=target,
    )

    current = logger.get_activity_scope()
    assert outcome.accepted
    assert current is not None
    assert current["latest_completed_battle"]["fingerprint"] == "b" * 64
    assert "pending_terminal_history_handoff" not in current
    assert "pending_latest_completed_battle" not in current


@pytest.mark.parametrize(
    ("runtime_session_id", "target", "reason"),
    (
        (
            "replacement-runtime",
            AdbTargetSnapshot("private-target", 3, True),
            "terminal_history_handoff_process_changed",
        ),
        (
            "runtime-1",
            AdbTargetSnapshot("private-target", 4, True),
            "terminal_history_handoff_target_changed",
        ),
    ),
)
def test_terminal_handoff_rejects_changed_process_or_target_as_report_data(
    tmp_path,
    monkeypatch,
    runtime_session_id,
    target,
    reason,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    coordinator = _stage_terminal_handoff(
        target=AdbTargetSnapshot("private-target", 3, True)
    )
    next_scope = logger.start_activity_scope(
        reason="new_battle_preflight",
        carry_terminal_history_handoff=True,
    )
    assert next_scope is not None

    outcome = coordinator.accept_pending_terminal_history_handoff(
        expected_scope_id=str(next_scope["run_id"]),
        runtime_session_id=runtime_session_id,
        target_snapshot=target,
    )

    current = logger.get_activity_scope()
    assert not outcome.accepted
    assert outcome.reason == reason
    assert current is not None
    assert "latest_completed_battle" not in current
    assert "pending_terminal_history_handoff" not in current


def test_terminal_handoff_is_not_consumed_for_another_report_segment(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    target = AdbTargetSnapshot("private-target", 3, True)
    coordinator = _stage_terminal_handoff(target=target)
    next_scope = logger.start_activity_scope(
        reason="new_battle_preflight",
        carry_terminal_history_handoff=True,
    )
    assert next_scope is not None

    wrong_scope = coordinator.accept_pending_terminal_history_handoff(
        expected_scope_id="different-scope",
        runtime_session_id="runtime-1",
        target_snapshot=target,
    )
    accepted = coordinator.accept_pending_terminal_history_handoff(
        expected_scope_id=str(next_scope["run_id"]),
        runtime_session_id="runtime-1",
        target_snapshot=target,
    )

    assert not wrong_scope.accepted
    assert accepted.accepted
