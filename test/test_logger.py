from utils import logger


def test_log_honors_primary_path_override(tmp_path, monkeypatch):
    isolated_log = tmp_path / "nested" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))

    logger.log("synthetic test event", "INFO", console=False)

    contents = isolated_log.read_text(encoding="utf-8")
    assert "[INFO " in contents
    assert contents.endswith("] synthetic test event\n")


def test_log_action_pairs_operator_summary_with_diagnostic_detail(
    tmp_path,
    monkeypatch,
):
    isolated_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))

    logger.log_action(
        "Tap requested: buttons.return_to_game",
        detail="TAP_SAFE now=True label=buttons.return_to_game at (10,20) vis=True",
        console=False,
    )

    lines = isolated_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("[ACTION ")
    assert lines[0].endswith("] Tap requested: buttons.return_to_game")
    assert lines[1].startswith("[DEBUG ")
    assert lines[1].endswith(
        "] TAP_SAFE now=True label=buttons.return_to_game at (10,20) vis=True"
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
