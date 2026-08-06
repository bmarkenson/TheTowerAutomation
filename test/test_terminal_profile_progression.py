from types import SimpleNamespace
from unittest.mock import patch

from core.adb_target_session import AdbTargetSnapshot
from core.app import App
from core.player_save_serialization import quiet_player_save_read


class _StableSession:
    def __init__(self, *snapshots):
        self._snapshots = list(snapshots)

    def snapshot(self):
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        return self._snapshots[0]


def _normalized_progression():
    return {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "identity": {
            "data_version": 9,
            "game_version": 1073,
            "save_revision": 47316,
            "mapping_id": "data-9-game-1073",
            "audit_matrix_id": "data-9-game-1073-profile-progression-v1",
        },
        "source": {
            "captured_at": "2026-08-06T04:00:00+00:00",
            "sha256": "source-fingerprint",
        },
        "fingerprint": "profile-fingerprint",
        "components": {},
        "warnings": [],
    }


def test_terminal_progression_uses_stable_exact_target_and_marks_acquisition():
    target = AdbTargetSnapshot("localhost:5555", 4, True)
    app = App.__new__(App)
    app._adb_target_session = _StableSession(target, target)
    decoded = SimpleNamespace(
        profile_progression=_normalized_progression(),
        mapping_id="data-9-game-1073",
        save_revision=47316,
    )

    with (
        patch("core.app.pull_player_save_bytes", return_value=b"save") as pull,
        patch("core.app.decode_player_save_bytes", return_value=decoded) as decode,
    ):
        result = app._capture_terminal_profile_progression()

    assert result["status"] == "complete"
    assert result["source"]["acquisition"] == "stable_terminal_player_save"
    pull.assert_called_once_with(
        device_id="localhost:5555",
        attempts=3,
        settle_seconds=0.1,
        read_fn=quiet_player_save_read,
    )
    assert decode.call_args.args == (b"save",)
    assert decode.call_args.kwargs["source_name"] == "playerInfo.dat"


def test_terminal_progression_discards_snapshot_across_target_generation_change():
    app = App.__new__(App)
    app._adb_target_session = _StableSession(
        AdbTargetSnapshot("localhost:5555", 4, True),
        AdbTargetSnapshot("localhost:5555", 5, True),
    )
    decoded = SimpleNamespace(
        profile_progression=_normalized_progression(),
        mapping_id="data-9-game-1073",
        save_revision=47316,
    )

    with (
        patch("core.app.pull_player_save_bytes", return_value=b"save"),
        patch("core.app.decode_player_save_bytes", return_value=decoded),
    ):
        result = app._capture_terminal_profile_progression()

    assert result["status"] == "unavailable"
    assert result["reason"] == "adb_target_changed_during_terminal_capture"
    assert result["identity"]["mapping_id"] == "data-9-game-1073"


def test_terminal_progression_is_nonblocking_without_an_owned_target():
    app = App.__new__(App)

    result = app._capture_terminal_profile_progression()

    assert result["status"] == "unavailable"
    assert result["reason"] == "adb_target_session_unavailable"


def test_terminal_save_capture_reuses_one_snapshot_for_progression_and_report():
    target = AdbTargetSnapshot("localhost:5555", 4, True)
    app = App.__new__(App)
    app._adb_target_session = _StableSession(target, target)
    decoded = SimpleNamespace(
        profile_progression=_normalized_progression(),
        mapping_id="data-9-game-1073",
        save_revision=47316,
        checks={},
    )
    report = {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "completed_entry": {"schema_version": 1},
        "ui_fallback": {"required": False},
    }
    binding = {
        "schema_version": 1,
        "status": "bound",
        "activity_scope_run_id": "scope-1",
    }
    scope = {"schema_version": 1, "run_id": "scope-1"}

    with (
        patch("core.app.pull_player_save_bytes", return_value=b"save") as pull,
        patch("core.app.decode_player_save_bytes", return_value=decoded),
        patch("core.app.get_activity_scope", return_value=scope),
        patch(
            "core.app.terminal_save_report_from_snapshot",
            return_value=report,
        ) as report_from_snapshot,
    ):
        result = app._capture_terminal_player_save(
            "GAME_OVER",
            run_binding=binding,
        )

    assert result["profile_progression"]["status"] == "complete"
    assert result["terminal_save_report"] is report
    pull.assert_called_once()
    report_from_snapshot.assert_called_once_with(
        decoded,
        terminal_state="GAME_OVER",
        run_binding=binding,
        activity_scope=scope,
    )
