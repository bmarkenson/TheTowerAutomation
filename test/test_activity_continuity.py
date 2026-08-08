import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.activity_continuity import ActivityContinuityCoordinator
from core.adb_target_session import AdbTargetSnapshot
from core.battle_history import (
    BattleHistoryReadResult,
    BattleHistoryReadStatus,
    parse_battle_history_report,
)
from core.battle_lifecycle import HomeBattleControl
from core.player_save_history import (
    PlayerSaveHistoryReadResult,
    PlayerSaveHistoryReadStatus,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
)
from utils import logger
from test.player_save_temporal_fixtures import (
    running_attachment_observations,
)


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
    tier=19,
    wave=1899,
    semantic_status="observed",
    battle_date=None,
):
    return {
        "schema_version": 2,
        "source": "player_save",
        "mapping_id": "data-9-game-1073",
        "identity_schema_version": 1,
        "fingerprint": fingerprint,
        "tier": tier,
        "wave": wave,
        "battle_date": battle_date
        or {
            "kind_id": 2,
            "kind": "local",
            "ticks": "639197340971234560",
            "clock_time": "2026-07-15T01:41:37.123456",
            "clock_basis": "local_wall_clock_without_offset",
            "submicrosecond_100ns": 0,
        },
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


def _save_complete(metadata, *, observations=None, acquisition=None):
    return PlayerSaveHistoryReadResult(
        PlayerSaveHistoryReadStatus.COMPLETE,
        "structural_history_tail_observed",
        metadata=metadata,
        running_attachment_observations=observations,
        acquisition=acquisition,
    )


def _forced_bundle(observations):
    captured = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)
    return PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=observations.binding.target_binding,
        acquisition_started_at=captured,
        captured_at=captured,
        acquisition_completed_at=captured,
        transport_stable=True,
        snapshot=object(),
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
    )
    boundary_evidence = boundary.redacted()
    latest = _save_metadata(
        fingerprint="b" * 64,
        entry_count=30,
        capacity=30,
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


def test_paused_home_baseline_follows_manual_start_before_sending_input(
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
    reads = []

    def reader(**kwargs):
        reads.append(kwargs)
        return _complete(identity)

    coordinator = ActivityContinuityCoordinator(history_reader=reader)

    paused_home = coordinator.handle(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        },
        actions_allowed=False,
        action_guard_fn=lambda: False,
    )
    paused_running = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=False,
        action_guard_fn=lambda: False,
    )

    assert paused_home.pending
    assert paused_running.pending
    assert reads == []

    resumed = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
    )

    current = logger.get_activity_scope()
    assert resumed.recapture
    assert not resumed.pending
    assert current is not None
    assert current["run_id"] == scope["run_id"]
    assert (
        current["latest_completed_battle"]["fingerprint"]
        == identity.fingerprint
    )
    assert len(reads) == 1
    assert reads[0]["source_state"] == "RUNNING"
    assert (
        reads[0]["expected_home_control"]
        is HomeBattleControl.UNKNOWN
    )
    contents = (
        tmp_path / "logs" / "actions.log"
    ).read_text(encoding="utf-8")
    assert contents.count(
        "Pending Home continuity source advanced to RUNNING"
    ) == 1


def test_paused_manual_start_records_missing_baseline_from_guarded_save(
    tmp_path,
    monkeypatch,
):
    log_path = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(log_path))
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    metadata = _save_metadata(wave=5140)
    observations = running_attachment_observations(
        {"cards_deck": {"value": "Farm"}},
        source_scope_id=str(scope["run_id"]),
        bind_final=False,
    )
    save_reads = []

    def save_reader(**kwargs):
        save_reads.append(kwargs)
        return _save_complete(metadata, observations=observations)

    coordinator = ActivityContinuityCoordinator(
        save_history_reader=save_reader,
        history_reader=lambda **_kwargs: pytest.fail(
            "a missing attachment baseline must not open Battle History UI"
        ),
    )

    paused_home = coordinator.handle(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        },
        actions_allowed=False,
        action_guard_fn=lambda: False,
        player_save_mode="save_first",
    )
    paused_running = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=False,
        action_guard_fn=lambda: False,
        player_save_mode="save_first",
    )
    resumed = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert paused_home.pending and paused_running.pending
    assert resumed.recapture and not resumed.pending
    assert resumed.running_attachment_observations is not None
    assert resumed.running_attachment_observations.binding.activity_scope_id == (
        scope["run_id"]
    )
    assert current is not None
    assert current["run_id"] == scope["run_id"]
    assert current["latest_completed_battle"] == metadata
    assert len(save_reads) == 1
    assert save_reads[0]["source_state"] == "RUNNING"
    assert save_reads[0]["serialize_active_attachment"] is True
    contents = log_path.read_text(encoding="utf-8")
    assert "channel=stable player save" in contents
    assert "attachment_save_baseline_recorded" in contents
    assert "Battle History UI" not in contents


def test_attachment_observations_bind_to_replacement_scope_after_persistence(
):
    previous = _save_metadata(entry_count=29)
    original = _scope_with_save_baseline(previous)
    latest = _save_metadata(
        fingerprint="b" * 64,
        entry_count=30,
        wave=2100,
    )
    observations = running_attachment_observations(
        {"workshop_preset": "Farm"},
        source_scope_id=str(original["run_id"]),
        bind_final=False,
    )
    acquisition = _forced_bundle(observations)
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: _save_complete(
            latest,
            observations=observations,
            acquisition=acquisition,
        ),
        history_reader=lambda **_kwargs: pytest.fail(
            "a complete attachment save must not open History UI"
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert current is not None
    assert current["run_id"] != original["run_id"]
    assert outcome.running_attachment_observations is not None
    assert (
        outcome.running_attachment_observations.binding.activity_scope_id
        == current["run_id"]
    )
    assert outcome.running_attachment_acquisition is acquisition


def test_attachment_observations_are_not_published_when_scope_write_fails(
):
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    metadata = _save_metadata(wave=5140)
    observations = running_attachment_observations(
        {"workshop_preset": "Farm"},
        source_scope_id=str(scope["run_id"]),
        bind_final=False,
    )
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: _save_complete(
            metadata,
            observations=observations,
        ),
        history_reader=lambda **_kwargs: pytest.fail(
            "a running attachment must not open History UI"
        ),
    )
    coordinator.handle(
        {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"},
        actions_allowed=False,
        action_guard_fn=lambda: False,
        player_save_mode="save_first",
    )

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "core.activity_continuity.record_activity_scope_battle_history",
            lambda **_kwargs: None,
        )
        outcome = coordinator.handle(
            {"state": "RUNNING"},
            actions_allowed=True,
            action_guard_fn=lambda: True,
            player_save_mode="save_first",
        )

    assert outcome.running_attachment_observations is None


def test_missing_attachment_baseline_uses_history_ui_when_save_is_unusable(
    tmp_path,
    monkeypatch,
):
    log_path = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(log_path))
    logger.start_activity_scope(reason="automation_started")
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: PlayerSaveHistoryReadResult(
            PlayerSaveHistoryReadStatus.UI_FALLBACK,
            "save_history_acquisition_failed",
            safe_ui_fallback=True,
        ),
        history_reader=lambda **kwargs: (
            ui_reads.append(kwargs) or _complete(_identity(wave="9333"))
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    assert not outcome.pending
    assert outcome.recapture
    assert len(ui_reads) == 1
    assert logger.get_activity_scope()["latest_completed_battle"]["source"] == (
        "battle_history_ui"
    )
    assert "using the guarded UI route" in log_path.read_text(encoding="utf-8")


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


def test_unchanged_save_tail_preserves_running_attachment_without_history_ui(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    baseline = _save_metadata()
    scope = _scope_with_save_baseline(baseline)
    save_reads = []
    ui_reads = []

    def save_reader(**kwargs):
        save_reads.append(kwargs)
        return _save_complete(baseline)

    coordinator = ActivityContinuityCoordinator(
        save_history_reader=save_reader,
        history_reader=lambda **kwargs: ui_reads.append(kwargs),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.recapture
    assert outcome.confirmed_same_battle_scope_id == scope["run_id"]
    assert current is not None
    assert current["run_id"] == scope["run_id"]
    assert current["latest_completed_battle"] == baseline
    assert ui_reads == []
    assert len(save_reads) == 1
    assert save_reads[0]["serialize_active_attachment"] is True


def test_corroborated_ui_baseline_migrates_to_save_without_fingerprint_compare(
    tmp_path,
    monkeypatch,
):
    log_path = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(log_path))
    ui_identity = _identity(wave="9112")
    original = _scope_with_baseline(ui_identity)
    save_metadata = _save_metadata(
        fingerprint="save-fingerprint-is-source-specific",
        tier=18,
        wave=9112,
    )
    save_reads = []

    def save_reader(**kwargs):
        save_reads.append(kwargs)
        return _save_complete(save_metadata)

    coordinator = ActivityContinuityCoordinator(
        save_history_reader=save_reader,
        history_reader=lambda **_kwargs: pytest.fail(
            "corroborated UI baseline should migrate without History UI"
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
    assert outcome.confirmed_same_battle_scope_id == original["run_id"]
    assert current is not None
    assert current["run_id"] == original["run_id"]
    assert current["latest_completed_battle"] == save_metadata
    assert len(save_reads) == 1
    assert save_reads[0]["serialize_active_attachment"] is True
    contents = log_path.read_text(encoding="utf-8")
    assert "cross_source_scope_preserved" in contents
    assert "fingerprint_compared=False" in contents


@pytest.mark.parametrize(
    "evidence_kind",
    ("tier_wave_mismatch", "date_mismatch", "ambiguous_date"),
)
def test_cross_source_mismatch_or_ambiguity_uses_save_without_history_ui(
    tmp_path,
    monkeypatch,
    evidence_kind,
):
    log_path = tmp_path / evidence_kind / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(log_path))
    ui_identity = _identity(wave="9112")
    original = _scope_with_baseline(ui_identity)
    battle_date = {
        "kind_id": 2,
        "kind": "local",
        "ticks": "639197341571234560",
        "clock_time": "2026-07-15T01:42:37.123456",
        "clock_basis": "local_wall_clock_without_offset",
        "submicrosecond_100ns": 0,
    }
    wave = 9112
    if evidence_kind == "tier_wave_mismatch":
        wave = 9333
        battle_date["clock_time"] = "2026-07-15T01:41:37.123456"
    elif evidence_kind == "ambiguous_date":
        battle_date.update(
            {
                "kind_id": 1,
                "kind": "utc",
                "clock_time": "2026-07-15T01:41:37.123456+00:00",
                "clock_basis": "utc",
            }
        )
    save_metadata = _save_metadata(
        fingerprint="save-fingerprint-is-never-compared",
        tier=18,
        wave=wave,
        battle_date=battle_date,
    )
    save_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **kwargs: (
            save_reads.append(kwargs) or _save_complete(save_metadata)
        ),
        history_reader=lambda **_kwargs: pytest.fail(
            "a running attachment must not open Battle History UI"
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
    assert current["run_id"] != original["run_id"]
    assert current["latest_completed_battle"] == save_metadata
    assert len(save_reads) == 1
    contents = log_path.read_text(encoding="utf-8")
    expected_reason = {
        "tier_wave_mismatch": "cross_source_tier_wave_mismatch",
        "date_mismatch": "cross_source_battle_date_mismatch",
        "ambiguous_date": "cross_source_battle_date_ambiguous",
    }[evidence_kind]
    assert expected_reason in contents
    if evidence_kind == "ambiguous_date":
        assert outcome.confirmed_later_battle_scope_id is None
        assert current["reason"] == "battle_history_unavailable_on_attachment"
        assert "unverified_new_attachment_scope" in contents
    else:
        assert outcome.confirmed_later_battle_scope_id == current["run_id"]
        assert current["reason"] == "battle_history_changed_on_attachment"
        assert "disposition=new_attachment_scope" in contents


def test_insufficient_ui_baseline_starts_conservative_save_scope_without_ui(
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
    metadata = identity.scope_metadata()
    metadata["battle_date"] = "date kind unavailable"
    logger.record_activity_scope_battle_history(
        run_id=str(scope["run_id"]),
        latest_completed_battle=metadata,
    )
    save_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **kwargs: (
            save_reads.append(kwargs)
            or _save_complete(_save_metadata(wave=9112))
        ),
        history_reader=lambda **_kwargs: pytest.fail(
            "an incomparable attachment baseline must not open History UI"
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    assert outcome.recapture
    assert len(save_reads) == 1
    assert save_reads[0]["serialize_active_attachment"] is True
    current = logger.get_activity_scope()
    assert current is not None
    assert current["run_id"] != scope["run_id"]
    assert current["latest_completed_battle"]["source"] == "player_save"
    assert outcome.confirmed_later_battle_scope_id is None


@pytest.mark.parametrize(
    ("previous_count", "latest_count"),
    ((29, 30), (30, 30)),
)
def test_valid_changed_save_tail_starts_later_running_attachment_scope(
    tmp_path,
    monkeypatch,
    previous_count,
    latest_count,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    baseline = _save_metadata(entry_count=previous_count)
    original = _scope_with_save_baseline(baseline)
    latest = _save_metadata(
        fingerprint="b" * 64,
        entry_count=latest_count,
        wave=2100,
    )
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: _save_complete(latest),
        history_reader=lambda **_kwargs: pytest.fail(
            "valid same-source tail advance must suppress History UI"
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
    assert current["run_id"] != original["run_id"]
    assert outcome.confirmed_later_battle_scope_id == current["run_id"]
    assert current["latest_completed_battle"] == latest
    assert current["reason"] == "battle_history_changed_on_attachment"


def test_invalid_attachment_save_transition_starts_later_scope_without_ui(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    _scope_with_save_baseline(_save_metadata(entry_count=28))
    invalid = _save_metadata(
        fingerprint="b" * 64,
        entry_count=30,
        wave=2100,
    )
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: _save_complete(invalid),
        history_reader=lambda **_kwargs: pytest.fail(
            "a valid fresh attachment save must not open History UI"
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    assert outcome.recapture
    current = logger.get_activity_scope()
    assert current is not None
    assert current["latest_completed_battle"] == invalid
    assert outcome.confirmed_later_battle_scope_id == current["run_id"]
    assert "without a valid append/rollover" in (
        tmp_path / "logs" / "actions.log"
    ).read_text(encoding="utf-8")


def test_active_attachment_save_failure_uses_history_ui_fallback(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    _scope_with_save_baseline(_save_metadata())
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **_kwargs: PlayerSaveHistoryReadResult(
            PlayerSaveHistoryReadStatus.UI_FALLBACK,
            "runtime_history_projection_unavailable",
            safe_ui_fallback=True,
        ),
        history_reader=lambda **kwargs: (
            ui_reads.append(kwargs) or _complete(_identity(wave="9333"))
        ),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    assert not outcome.pending
    assert outcome.recapture
    assert len(ui_reads) == 1
    assert ui_reads[0]["source_state"] == "RUNNING"
    assert "using the guarded UI route" in (
        tmp_path / "logs" / "actions.log"
    ).read_text(encoding="utf-8")


def test_blocked_active_attachment_save_never_opens_history_ui(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    _scope_with_save_baseline(_save_metadata())
    save_reads = []
    ui_reads = []

    def save_reader(**kwargs):
        save_reads.append(kwargs)
        return PlayerSaveHistoryReadResult(
            PlayerSaveHistoryReadStatus.BLOCKED,
            "active_attachment_restored_source_boundary_unverified",
            background_dispatched=True,
            operator_workflow_interrupted=True,
        )

    coordinator = ActivityContinuityCoordinator(
        save_history_reader=save_reader,
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
    assert len(save_reads) == 1
    assert save_reads[0]["serialize_active_attachment"] is True
    assert ui_reads == []
    assert outcome.operator_workflow_interruption_reason == (
        "active_attachment_restored_source_boundary_unverified"
    )


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


def test_legacy_v1_ui_scope_with_complete_fields_uses_same_strong_bridge(
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
    save_metadata = _save_metadata(tier=18, wave=9112)
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **kwargs: (
            save_reads.append(kwargs) or _save_complete(save_metadata)
        ),
        history_reader=lambda **kwargs: ui_reads.append(kwargs),
    )

    outcome = coordinator.handle(
        {"state": "RUNNING"},
        actions_allowed=True,
        action_guard_fn=lambda: True,
        player_save_mode="save_first",
    )

    current = logger.get_activity_scope()
    assert outcome.confirmed_same_battle_scope_id == scope["run_id"]
    assert len(save_reads) == 1
    assert ui_reads == []
    assert current is not None
    assert current["latest_completed_battle"]["schema_version"] == 2
    assert current["latest_completed_battle"]["source"] == (
        "player_save"
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


@pytest.mark.parametrize(
    ("destination", "terminal_state"),
    (
        ("home", "GAME_OVER"),
        ("retry", "GAME_OVER"),
        ("home", "TOURNAMENT_RESULTS"),
    ),
)
def test_terminal_handoff_seeds_next_scope_without_save_or_history_read(
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
    source = _scope_with_save_baseline(
        _save_metadata(fingerprint="a" * 64, entry_count=29)
    )
    staged = logger.record_activity_scope_terminal_history_handoff(
        run_id=str(source["run_id"]),
        handoff=_terminal_handoff(
            source_scope_id=str(source["run_id"]),
            target=target,
            terminal_state=terminal_state,
        ),
    )
    assert staged is not None
    if destination == "retry":
        next_scope = logger.start_retry_activity_scope()
    else:
        next_scope = logger.start_activity_scope(
            reason="new_battle_preflight",
            carry_terminal_history_handoff=True,
        )
    assert next_scope is not None
    save_reads = []
    ui_reads = []
    coordinator = ActivityContinuityCoordinator(
        save_history_reader=lambda **kwargs: save_reads.append(kwargs),
        history_reader=lambda **kwargs: ui_reads.append(kwargs),
    )

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
    assert not coordinator.needs_check(
        {
            "state": "RUNNING" if destination == "retry" else "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        }
    )
    assert save_reads == []
    assert ui_reads == []


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
def test_terminal_handoff_fails_closed_after_process_or_target_change(
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
    original_target = AdbTargetSnapshot("private-target", 3, True)
    source = _scope_with_save_baseline(
        _save_metadata(fingerprint="a" * 64, entry_count=29)
    )
    logger.record_activity_scope_terminal_history_handoff(
        run_id=str(source["run_id"]),
        handoff=_terminal_handoff(
            source_scope_id=str(source["run_id"]),
            target=original_target,
        ),
    )
    next_scope = logger.start_activity_scope(
        reason="new_battle_preflight",
        carry_terminal_history_handoff=True,
    )
    assert next_scope is not None
    coordinator = ActivityContinuityCoordinator()

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
    assert coordinator.needs_check(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        }
    )


def test_terminal_handoff_is_not_consumed_for_a_different_destination_scope(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    target = AdbTargetSnapshot("private-target", 3, True)
    source = _scope_with_save_baseline(
        _save_metadata(fingerprint="a" * 64, entry_count=29)
    )
    logger.record_activity_scope_terminal_history_handoff(
        run_id=str(source["run_id"]),
        handoff=_terminal_handoff(
            source_scope_id=str(source["run_id"]),
            target=target,
        ),
    )
    next_scope = logger.start_activity_scope(
        reason="new_battle_preflight",
        carry_terminal_history_handoff=True,
    )
    assert next_scope is not None
    coordinator = ActivityContinuityCoordinator()

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
