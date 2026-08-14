from __future__ import annotations

import json
from types import SimpleNamespace

from core.player_save_acquisition import PlayerSaveAcquisitionType
from tools import import_player_save


class _Snapshot:
    def as_dict(self):
        return {"schema_version": 7, "mapping": {"supported": True}}


def test_local_import_uses_global_parser_file_api(monkeypatch, tmp_path, capsys):
    source = tmp_path / "renamed-save.dat"
    source.write_bytes(b"opaque")
    calls = []

    class Parser:
        def parse_file(self, path):
            calls.append(path)
            return _Snapshot()

    monkeypatch.setattr(import_player_save, "PlayerSaveParser", Parser)

    assert import_player_save.main(["--file", str(source), "--compact"]) == 0
    assert calls == [source]
    assert json.loads(capsys.readouterr().out)["snapshot"]["schema_version"] == 7


def test_adb_import_uses_one_acquirer_and_custom_source_name(
    monkeypatch,
    capsys,
):
    parser = object()
    constructed = []
    acquired = []

    monkeypatch.setattr(import_player_save, "PlayerSaveParser", lambda: parser)

    class Acquirer:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        def acquire(self, acquisition_type):
            acquired.append(acquisition_type)
            return SimpleNamespace(complete=True, snapshot=_Snapshot())

    monkeypatch.setattr(
        import_player_save,
        "StablePlayerSaveAcquirer",
        Acquirer,
    )

    assert import_player_save.main(
        [
            "--adb-target",
            "private-target",
            "--device-path",
            "/private/device/custom-save.dat",
            "--compact",
        ]
    ) == 0

    assert constructed == [
        {
            "fixed_target": "private-target",
            "parser": parser,
            "source_name": "custom-save.dat",
            "pull_options": {
                "device_path": "/private/device/custom-save.dat"
            },
        }
    ]
    assert acquired == [PlayerSaveAcquisitionType.PASSIVE_STABLE_READ]
    assert json.loads(capsys.readouterr().out)["snapshot"]["schema_version"] == 7


def test_adb_import_reports_shared_acquisition_failure(monkeypatch, capsys):
    monkeypatch.setattr(import_player_save, "PlayerSaveParser", object)

    class Acquirer:
        def __init__(self, **_kwargs):
            pass

        def acquire(self, _acquisition_type):
            return SimpleNamespace(
                complete=False,
                snapshot=None,
                reason="stable_read_unavailable",
            )

    monkeypatch.setattr(
        import_player_save,
        "StablePlayerSaveAcquirer",
        Acquirer,
    )

    assert import_player_save.main(["--adb-target", "private-target"]) == 1
    assert "stable_read_unavailable" in capsys.readouterr().err
