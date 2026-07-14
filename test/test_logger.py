from utils import logger


def test_log_honors_primary_path_override(tmp_path, monkeypatch):
    isolated_log = tmp_path / "nested" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(isolated_log))

    logger.log("synthetic test event", "INFO", console=False)

    contents = isolated_log.read_text(encoding="utf-8")
    assert "[INFO " in contents
    assert contents.endswith("] synthetic test event\n")
