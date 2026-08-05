import json
from pathlib import Path

import pytest

from core.activity_continuity import ActivityContinuityCoordinator
from core.battle_history import (
    BattleHistoryReadResult,
    BattleHistoryReadStatus,
    parse_battle_history_report,
)
from core.player_save_history import (
    PlayerSaveHistoryReadResult,
    PlayerSaveHistoryReadStatus,
)
from utils import logger


FIXTURES = Path(__file__).parent / "fixtures"
REPORT = (FIXTURES / "battle_history_report_clipboard.txt").read_text(
    encoding="utf-8"
)


def _identity(*, wave: str):
    return parse_battle_history_report(
        REPORT.replace("Wave\t9112", f"Wave\t{wave}")
    )


def _complete(identity):
    return BattleHistoryReadResult(
        BattleHistoryReadStatus.COMPLETE,
        "copied",
        identity=identity,
        source_restored=True,
    )


def _scope_with_baseline(identity):
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    updated = logger.record_activity_scope_battle_history(
        run_id=str(scope["run_id"]),
        latest_completed_battle=identity.scope_metadata(),
    )
    assert updated is not None
    return updated


def _save_metadata(
    *,
    fingerprint="a" * 64,
    entry_count=29,
    capacity=30,
    wave=1899,
    semantic_status="observed",
):
    return {
        "schema_version": 2,
        "source": "player_save",
        "mapping_id": "data-9-game-1073",
        "identity_schema_version": 1,
        "fingerprint": fingerprint,
        "tier": 19,
        "wave": wave,
        "entry_count": entry_count,
        "capacity": capacity,
        "semantic_status": semantic_status,
        "semantic_reason": (
            "unmapped_killed_by_id:999"
            if semantic_status == "unavailable"
            else ""
        ),
        "captured_at": "2026-08-04T20:00:00+00:00",
        "acquisition": "stable_two_identical_read_exact_target",
    }


def _save_complete(metadata):
    return PlayerSaveHistoryReadResult(
        PlayerSaveHistoryReadStatus.COMPLETE,
        "structural_history_tail_observed",
        metadata=metadata,
    )


def _scope_with_save_baseline(metadata):
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    updated = logger.record_activity_scope_battle_history(
        run_id=str(scope["run_id"]),
        latest_completed_battle=metadata,
    )
    assert updated is not None
    return updated


def test_unchanged_history_preserves_scope_on_attachment(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    identity = _identity(wave="9112")
    original = _scope_with_baseline(identity)
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **_kwargs: _complete(identity)
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert not outcome.pending
    assert outcome.confirmed_same_battle_scope_id == original["run_id"]
    assert current is not None
    assert current["run_id"] == original["run_id"]
    contents = (
        tmp_path / "logs" / "actions.log"
    ).read_text(encoding="utf-8")
    assert "Attached battle continuity confirmed" in contents


def test_interrupted_history_route_is_resumed_as_attachment_check(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    identity = _identity(wave="9112")
    original = _scope_with_baseline(identity)
    observed_sources = []

    def reader(**kwargs):
        observed_sources.append(kwargs["source_state"])
        return _complete(identity)

    coordinator = ActivityContinuityCoordinator(history_reader=reader)

    assert coordinator.needs_check({"state": "BATTLE_HISTORY"})
    outcome = coordinator.handle(
        {"state": "BATTLE_HISTORY"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert outcome.confirmed_same_battle_scope_id == original["run_id"]
    assert current is not None
    assert current["run_id"] == original["run_id"]
    assert observed_sources == ["BATTLE_HISTORY"]


def test_advanced_history_starts_scope_at_continuity_action(
    tmp_path,
    monkeypatch,
):
    log_path = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(log_path))
    original_identity = _identity(wave="9112")
    latest_identity = _identity(wave="9333")
    original = _scope_with_baseline(original_identity)
    logger.log("activity from prior battle", "INFO", console=False)
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **_kwargs: _complete(latest_identity)
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert outcome.confirmed_same_battle_scope_id is None
    assert current is not None
    assert outcome.confirmed_later_battle_scope_id == current["run_id"]
    assert current["run_id"] != original["run_id"]
    assert current["reason"] == "battle_history_changed_on_attachment"
    assert (
        current["latest_completed_battle"]["fingerprint"]
        == latest_identity.fingerprint
    )
    scoped_text = log_path.read_text(encoding="utf-8")[
        int(current["start_offset"]) :
    ]
    assert scoped_text.startswith("[ACTION ")
    assert "Checking attached battle continuity" in scoped_text
    assert "Attached battle identified as a later run" in scoped_text


def test_home_new_battle_records_baseline_without_replacing_scope(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    identity = _identity(wave="9112")
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **_kwargs: _complete(identity)
    )

    outcome = coordinator.handle(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        },
        actions_allowed=True,
        action_guard_fn=lambda: True,
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert current is not None
    assert current["run_id"] == scope["run_id"]
    assert (
        current["latest_completed_battle"]["fingerprint"]
        == identity.fingerprint
    )


def test_post_retry_history_poll_waits_for_startup_gates(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    previous = _identity(wave="9112")
    _scope_with_baseline(previous)
    retry_scope = logger.start_retry_activity_scope()
    assert retry_scope is not None
    reads = []
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **kwargs: reads.append(kwargs)
    )

    assert not coordinator.needs_check(
        {"state": "RUNNING"},
        post_retry_poll_allowed=False,
    )
    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        post_retry_poll_allowed=False,
    )

    assert not outcome.pending
    assert not outcome.recapture
    assert reads == []
    assert logger.get_activity_scope() == retry_scope


def test_post_retry_history_poll_rejects_stale_latest_then_records_new_entry(
    tmp_path,
    monkeypatch,
):
    log_path = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(log_path))
    previous = _identity(wave="9112")
    latest = _identity(wave="9333")
    _scope_with_baseline(previous)
    retry_scope = logger.start_retry_activity_scope()
    assert retry_scope is not None
    now = [100.0]
    identities = [previous, latest]
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **_kwargs: _complete(identities.pop(0)),
        clock=lambda: now[0],
    )

    stale = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
    )

    current = logger.get_activity_scope()
    assert stale.recapture
    assert not stale.pending
    assert current is not None
    assert current["run_id"] == retry_scope["run_id"]
    assert "latest_completed_battle" not in current
    assert "pending_latest_completed_battle" in current

    now[0] = 114.9
    assert not coordinator.needs_check({"state": "RUNNING"})
    waiting = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
    )
    assert not waiting.pending
    assert not waiting.recapture
    assert len(identities) == 1

    now[0] = 115.0
    assert coordinator.needs_check({"state": "RUNNING"})
    recorded = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
    )

    current = logger.get_activity_scope()
    assert recorded.recapture
    assert not recorded.pending
    assert current is not None
    assert current["run_id"] == retry_scope["run_id"]
    assert (
        current["latest_completed_battle"]["fingerprint"]
        == latest.fingerprint
    )
    assert "pending_latest_completed_battle" not in current
    contents = log_path.read_text(encoding="utf-8")
    assert contents.count("Polling the post-Retry Battle History baseline") == 2
    assert contents.count("passive polling will continue") == 1
    assert "Post-Retry Battle History baseline recorded" in contents


def test_unverified_attachment_uses_conservative_new_scope(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    identity = _identity(wave="9112")
    original = _scope_with_baseline(identity)
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **_kwargs: BattleHistoryReadResult(
            BattleHistoryReadStatus.FAILED,
            "clipboard unreadable",
            source_restored=True,
        )
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert current is not None
    assert current["run_id"] != original["run_id"]
    assert current["reason"] == "battle_history_unavailable_on_attachment"


def test_scope_metadata_remains_valid_json_after_identity_update(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    identity = _identity(wave="9112")
    updated = _scope_with_baseline(identity)

    saved = json.loads(
        (tmp_path / "logs" / "activity_scope.json").read_text(
            encoding="utf-8"
        )
    )

    assert saved == updated


def test_home_new_battle_accepts_save_baseline_without_history_ui(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **kwargs: ui_reads.append(kwargs)
    )
    metadata = _save_metadata(semantic_status="unavailable")

    outcome = coordinator.accept_home_save_baseline(
        {
            "disposition": "save_match",
            "reason": "structural_history_tail_observed",
            "mapping_id": "data-9-game-1073",
            "metadata": metadata,
            "safe_ui_fallback": True,
        },
        expected_scope_id=str(scope["run_id"]),
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.accepted
    assert not outcome.ui_required
    assert ui_reads == []
    assert current is not None
    assert current["latest_completed_battle"] == metadata
    assert coordinator.needs_check(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        }
    ) is False


def test_home_save_preflight_defer_prevents_early_history_navigation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    logger.start_activity_scope(reason="new_battle_preflight")
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **kwargs: ui_reads.append(kwargs)
    )
    detection = {
        "state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
    }

    assert not coordinator.needs_check(
        detection,
        defer_home_baseline=True,
    )
    outcome = coordinator.handle(
        detection,
        actions_allowed=True,
        action_guard_fn=lambda: True,
        defer_home_baseline=True,
        player_save_mode="save_first",
    )

    assert not outcome.pending
    assert not outcome.recapture
    assert ui_reads == []


def test_home_baseline_scope_write_loss_blocks_without_history_ui(
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
        "core.activity_continuity.record_activity_scope_battle_history",
        lambda **_kwargs: None,
    )
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **kwargs: ui_reads.append(kwargs)
    )

    outcome = coordinator.accept_home_save_baseline(
        {
            "disposition": "save_match",
            "reason": "structural_history_tail_observed",
            "mapping_id": "data-9-game-1073",
            "metadata": _save_metadata(),
            "safe_ui_fallback": True,
        },
        expected_scope_id=str(scope["run_id"]),
        player_save_mode="save_first",
    )

    assert outcome.blocked
    assert not outcome.ui_required
    assert outcome.reason == "save_history_baseline_write_failed"
    assert ui_reads == []


@pytest.mark.parametrize(
    ("previous_count", "latest_count"),
    ((29, 30), (30, 30)),
)
def test_direct_retry_accepts_save_tail_append_or_capacity_rollover(
    tmp_path,
    monkeypatch,
    previous_count,
    latest_count,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    previous = _save_metadata(entry_count=previous_count)
    _scope_with_save_baseline(previous)
    retry_scope = logger.start_retry_activity_scope()
    assert retry_scope is not None
    latest = _save_metadata(
        fingerprint="b" * 64,
        entry_count=latest_count,
        wave=2100,
    )
    save_reads = []

    def save_reader(**kwargs):
        save_reads.append(kwargs)
        return _save_complete(latest)

    coordinator = ActivityContinuityCoordinator(
        save_history_reader=save_reader,
        history_reader=lambda **_kwargs: pytest.fail(
            "valid advancing save tail must suppress History UI"
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert current is not None
    assert current["run_id"] == retry_scope["run_id"]
    assert current["latest_completed_battle"] == latest
    assert "pending_latest_completed_battle" not in current
    assert len(save_reads) == 1
    assert save_reads[0]["expected_scope_id"] == retry_scope["run_id"]


def test_unchanged_retry_save_tail_polls_passively_without_history_ui(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    previous = _save_metadata()
    _scope_with_save_baseline(previous)
    retry_scope = logger.start_retry_activity_scope()
    assert retry_scope is not None
    now = [100.0]
    save_reads = []

    def save_reader(**kwargs):
        save_reads.append(kwargs)
        return _save_complete(previous)

    coordinator = ActivityContinuityCoordinator(
        save_history_reader=save_reader,
        history_reader=lambda **_kwargs: pytest.fail(
            "unchanged save tail must not navigate History"
        ),
        clock=lambda: now[0],
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert not outcome.pending
    assert current is not None
    assert current["run_id"] == retry_scope["run_id"]
    assert "latest_completed_battle" not in current
    assert "pending_latest_completed_battle" in current
    now[0] = 114.9
    assert not coordinator.needs_check({"state": "RUNNING"})
    now[0] = 115.0
    assert coordinator.needs_check({"state": "RUNNING"})
    repeated = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )
    assert repeated.recapture
    assert len(save_reads) == 2


def test_save_acquisition_failure_restores_guarded_history_ui_fallback(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    previous = _save_metadata()
    _scope_with_save_baseline(previous)
    retry_scope = logger.start_retry_activity_scope()
    assert retry_scope is not None
    ui_identity = _identity(wave="9333")
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: PlayerSaveHistoryReadResult(
            PlayerSaveHistoryReadStatus.UI_FALLBACK,
            "save_history_acquisition_failed",
            safe_ui_fallback=True,
        ),
        history_reader=lambda **kwargs: (
            ui_reads.append(kwargs) or _complete(ui_identity)
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert len(ui_reads) == 1
    assert current is not None
    assert current["run_id"] == retry_scope["run_id"]
    assert current["latest_completed_battle"]["source"] == (
        "battle_history_ui"
    )
    assert "pending_latest_completed_battle" not in current


def test_target_control_or_boundary_loss_never_runs_history_ui(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    _scope_with_save_baseline(_save_metadata())
    logger.start_retry_activity_scope()
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: PlayerSaveHistoryReadResult(
            PlayerSaveHistoryReadStatus.BLOCKED,
            "history_source_binding_lost",
        ),
        history_reader=lambda **kwargs: ui_reads.append(kwargs),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    assert outcome.pending
    assert outcome.recapture
    assert ui_reads == []


def test_pause_or_stop_prohibition_runs_neither_save_nor_history_ui(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    _scope_with_save_baseline(_save_metadata())
    logger.start_retry_activity_scope()
    save_reads = []
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **kwargs: save_reads.append(kwargs),
        history_reader=lambda **kwargs: ui_reads.append(kwargs),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=False,
        action_guard_fn=lambda: False,
        player_save_mode="save_first",
    )

    assert outcome.pending
    assert not outcome.recapture
    assert save_reads == []
    assert ui_reads == []


def test_invalid_save_tail_transition_restores_guarded_history_ui(
    tmp_path,
    monkeypatch,
):
    log_path = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(log_path))
    previous = _save_metadata(entry_count=29)
    _scope_with_save_baseline(previous)
    logger.start_retry_activity_scope()
    invalid = _save_metadata(
        fingerprint="b" * 64,
        entry_count=29,
        wave=2100,
    )
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: _save_complete(invalid),
        history_reader=lambda **kwargs: (
            ui_reads.append(kwargs) or _complete(_identity(wave="2100"))
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert len(ui_reads) == 1
    assert current is not None
    assert current["latest_completed_battle"]["source"] == (
        "battle_history_ui"
    )
    assert "history_tail_transition_invalid" in log_path.read_text(
        encoding="utf-8"
    )


def test_changed_save_mapping_restores_ui_without_comparing_fingerprints(
    tmp_path,
    monkeypatch,
):
    log_path = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(log_path))
    previous = _save_metadata()
    _scope_with_save_baseline(previous)
    logger.start_retry_activity_scope()
    changed_mapping = {
        **_save_metadata(fingerprint="b" * 64, entry_count=30, wave=2100),
        "mapping_id": "data-9-game-1074",
    }
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: _save_complete(changed_mapping),
        history_reader=lambda **kwargs: (
            ui_reads.append(kwargs) or _complete(_identity(wave="2100"))
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert len(ui_reads) == 1
    assert current is not None
    assert current["latest_completed_battle"]["source"] == (
        "battle_history_ui"
    )
    contents = log_path.read_text(encoding="utf-8")
    assert "history_source_mapping_changed" in contents
    assert "post_retry_source_migrated_without_fingerprint_comparison" in contents


@pytest.mark.parametrize("mode", ("force_ui", "comparison_audit"))
def test_force_ui_and_comparison_audit_suppress_no_history_ui(
    tmp_path,
    monkeypatch,
    mode,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / mode / "logs" / "actions.log"),
    )
    save_baseline = _save_metadata()
    _scope_with_save_baseline(save_baseline)
    ui_identity = _identity(wave="9112")
    save_reads = []
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **kwargs: save_reads.append(kwargs),
        history_reader=lambda **kwargs: (
            ui_reads.append(kwargs) or _complete(ui_identity)
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode=mode,
    )

    assert outcome.recapture
    assert save_reads == []
    assert len(ui_reads) == 1


def test_legacy_v1_ui_scope_is_migrated_only_through_ui_source_contract(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    identity = _identity(wave="9112")
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    legacy = {
        "schema_version": 1,
        "fingerprint": identity.fingerprint,
        "battle_date": identity.battle_date,
        "tier": identity.tier,
        "wave": identity.wave,
    }
    logger.record_activity_scope_battle_history(
        run_id=str(scope["run_id"]),
        latest_completed_battle=legacy,
    )
    save_reads = []
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **kwargs: save_reads.append(kwargs),
        history_reader=lambda **kwargs: (
            ui_reads.append(kwargs) or _complete(identity)
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.confirmed_same_battle_scope_id == scope["run_id"]
    assert save_reads == []
    assert len(ui_reads) == 1
    assert current is not None
    assert current["latest_completed_battle"]["schema_version"] == 2
    assert current["latest_completed_battle"]["source"] == (
        "battle_history_ui"
    )


def test_unknown_activity_metadata_schema_is_not_treated_as_legacy_ui(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    logger.record_activity_scope_battle_history(
        run_id=str(scope["run_id"]),
        latest_completed_battle={
            "schema_version": 3,
            "fingerprint": "a" * 64,
            "tier": 19,
            "wave": 1899,
        },
    )
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        history_reader=lambda **kwargs: (
            ui_reads.append(kwargs) or _complete(_identity(wave="1899"))
        )
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert len(ui_reads) == 1
    assert current is not None
    assert current["latest_completed_battle"]["source"] == (
        "battle_history_ui"
    )
