import json
from datetime import datetime

import pytest

from utils import logger


def test_log_honors_primary_path_override(tmp_path, monkeypatch):
    isolated_log = tmp_path / "nested" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))

    logger.log("synthetic test event", "INFO", console=False)

    contents = isolated_log.read_text(encoding="utf-8")
    assert "[INFO " in contents
    assert contents.endswith("] synthetic test event\n")


def test_log_action_pairs_workflow_summary_with_diagnostic_detail(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))

    logger.log_action(
        "Reviewing mission rewards — reward badges may indicate claimable rewards",
        detail="source=RUNNING daily=True event=False guild=False",
        console=False,
    )

    lines = isolated_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("[ACTION ")
    assert lines[0].endswith(
        "] Reviewing mission rewards — reward badges may indicate "
        "claimable rewards"
    )
    assert lines[1].startswith("[DEBUG ")
    assert lines[1].endswith(
        "] source=RUNNING daily=True event=False guild=False"
    )
    summary_timestamp = lines[0].split("]", 1)[0].removeprefix("[ACTION ")
    detail_timestamp = lines[1].split("]", 1)[0].removeprefix("[DEBUG ")
    assert summary_timestamp == detail_timestamp


def test_log_action_intent_writes_one_human_readable_header(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))

    logger.log_action_intent(
        "  Reviewing   mission rewards ",
        reason=" visible badges may identify claimable rewards ",
        console=False,
    )

    lines = isolated_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("[ACTION ")
    assert lines[0].endswith(
        "] Reviewing mission rewards — visible badges may identify "
        "claimable rewards"
    )


def test_correlated_action_and_result_keep_individual_summary_lines(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    operation_id = logger.new_operation_id()

    logger.log_action_intent(
        "Recording the perk selection timeline",
        reason="record the selection scheduled for wave 100",
        detail="[PERK_TIMELINE] mode=full",
        operation_id=operation_id,
        console=False,
    )
    logger.log_result(
        "Perk timeline selection recorded — Bounce Shot +2",
        detail="[PERK_TIMELINE] result=recorded",
        operation_id=operation_id,
        console=False,
    )

    lines = isolated_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert lines[0].endswith(
        "] Recording the perk selection timeline — "
        "record the selection scheduled for wave 100"
    )
    assert lines[1].endswith(
        f"] [PERK_TIMELINE] mode=full [OPERATION] id={operation_id}"
    )
    assert lines[2].endswith(
        "] Perk timeline selection recorded — Bounce Shot +2"
    )
    assert lines[3].endswith(
        f"] [PERK_TIMELINE] result=recorded [OPERATION] id={operation_id}"
    )


def test_start_activity_scope_records_exact_log_boundary(tmp_path, monkeypatch):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    logger.log("older activity", "INFO", console=False)

    scope = logger.start_activity_scope(reason="new battle preflight")

    assert scope is not None
    assert scope["reason"] == "new_battle_preflight"
    assert scope["start_offset"] > 0
    saved = json.loads(
        (tmp_path / "logs" / "activity_scope.json").read_text(encoding="utf-8")
    )
    assert saved == scope
    contents = isolated_log.read_text(encoding="utf-8")
    assert contents[int(scope["start_offset"]) :].startswith("[INFO ")
    assert "[RUN_SCOPE] Current run activity started" in contents


def test_ensure_activity_scope_preserves_existing_boundary(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    original = logger.start_activity_scope(reason="new_battle_preflight")
    logger.log("activity from the current battle", "INFO", console=False)

    attached = logger.ensure_activity_scope(reason="automation_started")

    assert attached == original
    saved = json.loads(
        (tmp_path / "logs" / "activity_scope.json").read_text(encoding="utf-8")
    )
    assert saved == original
    contents = isolated_log.read_text(encoding="utf-8")
    assert contents.count("[RUN_SCOPE] Current run activity started") == 1
    assert "activity from the current battle" in contents


def test_ensure_activity_scope_creates_missing_boundary(tmp_path, monkeypatch):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))

    scope = logger.ensure_activity_scope(reason="automation started")

    assert scope is not None
    assert scope["reason"] == "automation_started"
    assert json.loads(
        (tmp_path / "logs" / "activity_scope.json").read_text(encoding="utf-8")
    ) == scope


def test_start_activity_scope_can_reuse_an_earlier_log_boundary(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    logger.log("activity before continuity check", "INFO", console=False)
    boundary = logger.capture_activity_boundary()
    logger.log_action_intent(
        "Checking battle continuity",
        reason="determine whether the attached battle changed",
        console=False,
    )

    scope = logger.start_activity_scope(
        reason="battle_history_changed_on_attachment",
        boundary=boundary,
    )

    assert scope is not None
    assert boundary is not None
    assert scope["started_at"] == boundary["started_at"]
    assert scope["source_file_id"] == boundary["source_file_id"]
    assert scope["start_offset"] == boundary["start_offset"]
    contents = isolated_log.read_text(encoding="utf-8")
    assert contents[int(scope["start_offset"]) :].startswith("[ACTION ")


def test_battle_history_identity_updates_only_the_matching_scope(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    identity = {
        "fingerprint": "abc123",
        "battle_date": "Jul 15, 2026 01:41",
        "tier": "18",
        "wave": "9112",
    }

    rejected = logger.record_activity_scope_battle_history(
        run_id="different-run",
        latest_completed_battle=identity,
    )
    updated = logger.record_activity_scope_battle_history(
        run_id=str(scope["run_id"]),
        latest_completed_battle=identity,
    )

    assert rejected is None
    assert updated is not None
    assert updated["latest_completed_battle"] == identity
    assert logger.get_activity_scope() == updated


def test_session_preflight_receipt_updates_only_the_matching_scope(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    evidence = {
        "valid": True,
        "failed_checks": [],
        "modules": {"names": ["Being Annihilator"]},
    }

    rejected = logger.record_activity_scope_session_preflight(
        run_id="different-run",
        strategy="farm_t19",
        configuration_fingerprint="abc123",
        evidence=evidence,
    )
    updated = logger.record_activity_scope_session_preflight(
        run_id=str(scope["run_id"]),
        strategy="farm_t19",
        configuration_fingerprint="abc123",
        evidence=evidence,
    )
    evidence["modules"]["names"].append("mutated later")

    assert rejected is None
    assert updated is not None
    receipt = updated["session_preflight"]
    assert receipt["schema_version"] == 2
    assert receipt["status"] == "completed"
    assert receipt["activity_scope_run_id"] == scope["run_id"]
    assert receipt["strategy"] == "farm_t19"
    assert receipt["configuration_fingerprint"] == "abc123"
    assert datetime.fromisoformat(str(receipt["completed_at"]))
    assert receipt["evidence"] == {
        "schema_version": 1,
        "status": "available",
        "activity_scope_run_id": scope["run_id"],
        "strategy": "farm_t19",
        "configuration_fingerprint": "abc123",
        "payload": {
            "valid": True,
            "failed_checks": [],
            "modules": {"names": ["Being Annihilator"]},
        },
    }
    assert logger.get_activity_scope() == updated


@pytest.mark.parametrize(
    "evidence",
    [
        {"valid": True, "failed_checks": [], "score": float("nan")},
        {"valid": True, "failed_checks": [], "raw": object()},
        {
            "valid": True,
            "failed_checks": [],
            "raw": "x" * logger.SESSION_PREFLIGHT_REPORT_EVIDENCE_MAX_BYTES,
        },
    ],
)
def test_session_preflight_receipt_rejects_unsafe_report_evidence(
    tmp_path,
    monkeypatch,
    evidence,
):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None

    with pytest.raises(ValueError):
        logger.record_activity_scope_session_preflight(
            run_id=str(scope["run_id"]),
            strategy="farm_t19",
            configuration_fingerprint="abc123",
            evidence=evidence,
        )

    assert "session_preflight" not in logger.get_activity_scope()


def test_session_preflight_receipt_rejects_cyclic_report_evidence(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    assert scope is not None
    evidence = {"valid": True, "failed_checks": []}
    evidence["cycle"] = evidence

    with pytest.raises(ValueError, match="acyclic JSON values"):
        logger.record_activity_scope_session_preflight(
            run_id=str(scope["run_id"]),
            strategy="farm_t19",
            configuration_fingerprint="abc123",
            evidence=evidence,
        )

    assert "session_preflight" not in logger.get_activity_scope()


def test_retry_scope_waits_for_a_new_history_identity(tmp_path, monkeypatch):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    original = logger.start_activity_scope(reason="new_battle_preflight")
    assert original is not None
    previous_identity = {
        "fingerprint": "previous-battle",
        "battle_date": "Jul 31, 2026 07:22",
        "tier": "19",
        "wave": "4903",
    }
    original = logger.record_activity_scope_battle_history(
        run_id=str(original["run_id"]),
        latest_completed_battle=previous_identity,
    )
    assert original is not None

    retry_scope = logger.start_retry_activity_scope()

    assert retry_scope is not None
    assert retry_scope["run_id"] != original["run_id"]
    assert retry_scope["reason"] == "game_over_retry"
    assert "latest_completed_battle" not in retry_scope
    assert retry_scope["pending_latest_completed_battle"] == {
        "schema_version": 1,
        "previous_completed_battle": previous_identity,
    }

    latest_identity = {
        **previous_identity,
        "fingerprint": "new-battle",
        "wave": "5100",
    }
    completed = logger.record_activity_scope_battle_history(
        run_id=str(retry_scope["run_id"]),
        latest_completed_battle=latest_identity,
    )

    assert completed is not None
    assert completed["latest_completed_battle"] == latest_identity
    assert "pending_latest_completed_battle" not in completed


def test_terminal_history_handoff_moves_once_to_the_next_home_scope(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    source = logger.start_activity_scope(reason="new_battle_preflight")
    assert source is not None
    handoff = {
        "schema_version": 1,
        "status": "ready",
        "terminal_state": "GAME_OVER",
    }
    staged = logger.record_activity_scope_terminal_history_handoff(
        run_id=str(source["run_id"]),
        handoff=handoff,
    )
    assert staged is not None

    destination = logger.start_activity_scope(
        reason="new_battle_preflight",
        carry_terminal_history_handoff=True,
    )
    assert destination is not None
    assert "terminal_history_handoff" not in destination
    assert destination["pending_terminal_history_handoff"] == {
        "schema_version": 1,
        "destination_run_id": destination["run_id"],
        "handoff": handoff,
    }

    consumed = logger.take_activity_scope_terminal_history_handoff(
        run_id=str(destination["run_id"])
    )
    repeated = logger.take_activity_scope_terminal_history_handoff(
        run_id=str(destination["run_id"])
    )

    assert consumed == destination["pending_terminal_history_handoff"]
    assert repeated is None
    assert "pending_terminal_history_handoff" not in logger.get_activity_scope()


def test_retry_scope_carries_terminal_handoff_with_its_previous_tail(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    source = logger.start_activity_scope(reason="new_battle_preflight")
    assert source is not None
    previous = {"fingerprint": "previous-tail"}
    source = logger.record_activity_scope_battle_history(
        run_id=str(source["run_id"]),
        latest_completed_battle=previous,
    )
    assert source is not None
    staged = logger.record_activity_scope_terminal_history_handoff(
        run_id=str(source["run_id"]),
        handoff={"schema_version": 1, "status": "ready"},
    )
    assert staged is not None

    retry = logger.start_retry_activity_scope()

    assert retry is not None
    assert retry["pending_latest_completed_battle"] == {
        "schema_version": 1,
        "previous_completed_battle": previous,
    }
    assert retry["pending_terminal_history_handoff"]["handoff"] == {
        "schema_version": 1,
        "status": "ready",
    }


def test_log_result_pairs_terminal_summary_with_diagnostic_detail(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))

    logger.log_result(
        "Mission reward review complete — no rewards available",
        detail="daily=0 weekly=0 event=0 guild=0 disposition=noop",
        console=False,
    )

    lines = isolated_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("[RESULT ")
    assert lines[0].endswith(
        "] Mission reward review complete — no rewards available"
    )
    assert lines[1].startswith("[DEBUG ")
    assert lines[1].endswith(
        "] daily=0 weekly=0 event=0 guild=0 disposition=noop"
    )
    result_timestamp = lines[0].split("]", 1)[0].removeprefix("[RESULT ")
    detail_timestamp = lines[1].split("]", 1)[0].removeprefix("[DEBUG ")
    assert result_timestamp == detail_timestamp


def test_log_input_pairs_human_summary_with_dispatch_detail(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))

    logger.log_input(
        "Swipe requested: Weekly Mission chests",
        detail="SWIPE_NOW: weekly_mission_chests (900,390)→(650,390) in 250ms",
        console=False,
    )

    lines = isolated_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("[INPUT ")
    assert lines[0].endswith("] Swipe requested: Weekly Mission chests")
    assert lines[1].startswith("[DEBUG ")
    assert lines[1].endswith(
        "] SWIPE_NOW: weekly_mission_chests "
        "(900,390)→(650,390) in 250ms"
    )
    input_timestamp = lines[0].split("]", 1)[0].removeprefix("[INPUT ")
    detail_timestamp = lines[1].split("]", 1)[0].removeprefix("[DEBUG ")
    assert input_timestamp == detail_timestamp


def test_explicit_primary_path_does_not_also_write_environment_log(
    tmp_path,
    monkeypatch,
):
    environment_log = tmp_path / "environment" / "actions.log"
    selected_log = tmp_path / "selected" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(environment_log))

    logger.log_action_intent(
        "Development ADB tap",
        reason="verify one bounded input",
        detail="lease_id=0123456789abcdef0123456789abcdef",
        operation_id="development-input-test",
        primary_path=str(selected_log),
        console=False,
    )

    lines = selected_log.read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[0] for line in lines] == ["[ACTION", "[DEBUG"]
    assert "[OPERATION] id=development-input-test" in lines[1]
    assert not environment_log.exists()


def test_action_log_rotation_bounds_oversized_history_and_keeps_action_pair(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "actions.log"
    isolated_log.write_text(
        "".join(
            f"[INFO 2026-07-26 10:00:{index:02d}] historical event {index}\n"
            for index in range(20)
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))
    monkeypatch.setenv("TOWER_ACTION_LOG_MAX_BYTES", "240")
    monkeypatch.setenv("TOWER_ACTION_LOG_BACKUP_COUNT", "2")

    logger.log_action(
        "Discard requested",
        detail="battle_id=Battle20260726T100000-0700",
        console=False,
    )

    backup = tmp_path / "actions.log.1"
    assert backup.is_file()
    assert backup.stat().st_size <= 240
    current_lines = isolated_log.read_text(encoding="utf-8").splitlines()
    assert len(current_lines) == 2
    assert current_lines[0].endswith("] Discard requested")
    assert current_lines[1].endswith(
        "] battle_id=Battle20260726T100000-0700"
    )
