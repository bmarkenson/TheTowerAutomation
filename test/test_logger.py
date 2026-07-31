import json

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
