import json
from pathlib import Path

from core.activity_continuity import ActivityContinuityCoordinator
from core.battle_history import (
    BattleHistoryReadResult,
    BattleHistoryReadStatus,
    parse_battle_history_report,
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
    assert contents.count("Polling the post-Retry Battle History baseline") == 1
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
