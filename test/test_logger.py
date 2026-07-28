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
