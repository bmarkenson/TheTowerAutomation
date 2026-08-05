from types import SimpleNamespace

from core.adb_target_session import AdbTargetSnapshot
from core.battle_lifecycle import HomeBattleControl
from core.player_save import pull_player_save_bytes
from core.player_save_history import (
    PlayerSaveHistoryReadStatus,
    PlayerSaveHistoryReader,
    history_metadata_from_snapshot,
    history_sources_compatible,
    valid_history_tail_advance,
)


def _snapshot(
    *,
    fingerprint: str = "a" * 64,
    entry_count: int = 29,
    capacity: int = 30,
    semantic_status: str = "observed",
    semantic_reason: str = "",
):
    identity = SimpleNamespace(
        mapping_id="data-9-game-1073",
        fingerprint=fingerprint,
        tier=19,
        wave=1899,
    )
    tail = SimpleNamespace(
        structural_status="observed",
        structural_reason="",
        identity=identity,
        entry_count=entry_count,
        capacity=capacity,
        completed_entry_status=semantic_status,
        completed_entry_reason=semantic_reason,
    )
    runtime = SimpleNamespace(
        mapping_id="data-9-game-1073",
        battle_history_tail=tail,
    )
    return SimpleNamespace(
        runtime_save=runtime,
        captured_at="2026-08-04T20:00:00+00:00",
    )


def _reader(
    *,
    target_snapshot_fn=lambda: AdbTargetSnapshot("private-target", 3, True),
    capture_fn=lambda: object(),
    detector=lambda _frame: {"state": "RUNNING"},
    scope_fn=lambda: {"run_id": "scope-1"},
    pull_fn=lambda **_kwargs: b"stable-save",
    decode_fn=lambda _payload, **_kwargs: _snapshot(),
):
    return PlayerSaveHistoryReader(
        target_snapshot_fn=target_snapshot_fn,
        capture_fn=capture_fn,
        detector=detector,
        home_control_fn=lambda _frame: SimpleNamespace(
            control=HomeBattleControl.NEW_BATTLE
        ),
        scope_fn=scope_fn,
        pull_fn=pull_fn,
        decode_fn=decode_fn,
    )


def _read(reader):
    return reader.read(
        source_state="RUNNING",
        expected_home_control=HomeBattleControl.UNKNOWN,
        expected_scope_id="scope-1",
        action_guard_fn=lambda: True,
    )


def test_structural_tail_remains_authoritative_when_semantics_are_unavailable():
    result = history_metadata_from_snapshot(
        _snapshot(
            semantic_status="unavailable",
            semantic_reason="unmapped_killed_by_id:999",
        ),
        acquisition="authoritative_home_preflight_snapshot",
    )

    assert result.complete
    assert result.metadata is not None
    assert result.metadata["source"] == "player_save"
    assert result.metadata["mapping_id"] == "data-9-game-1073"
    assert result.metadata["semantic_status"] == "unavailable"
    assert result.metadata["semantic_reason"] == "unmapped_killed_by_id:999"
    assert "killed_by_id" not in result.metadata


def test_reader_binds_stable_read_to_exact_target_scope_control_and_source():
    calls = {"target": 0, "capture": 0, "pull": 0}

    def target():
        calls["target"] += 1
        return AdbTargetSnapshot("private-target", 3, True)

    def capture():
        calls["capture"] += 1
        return object()

    def pull(**kwargs):
        calls["pull"] += 1
        assert kwargs == {"device_id": "private-target"}
        return b"stable-save"

    result = _read(
        _reader(
            target_snapshot_fn=target,
            capture_fn=capture,
            pull_fn=pull,
        )
    )

    assert result.complete
    assert result.metadata is not None
    assert result.metadata["acquisition"] == (
        "stable_two_identical_read_exact_target"
    )
    assert calls == {"target": 2, "capture": 2, "pull": 1}


def test_default_reader_transport_requires_two_identical_exact_target_reads(
    monkeypatch,
):
    import core.adb_utils as adb_utils
    import core.player_save as player_save

    reads = []

    def read_device_file(path, **kwargs):
        reads.append((path, kwargs))
        return b"identical-stable-save"

    monkeypatch.setattr(adb_utils, "read_device_file", read_device_file)
    monkeypatch.setattr(player_save.time, "sleep", lambda _seconds: None)
    reader = _reader(pull_fn=pull_player_save_bytes)

    result = _read(reader)

    assert result.complete
    assert len(reads) == 2
    assert all(
        kwargs == {
            "device_id": "private-target",
            "report_errors": False,
        }
        for _path, kwargs in reads
    )


def test_acquisition_failure_allows_ui_only_after_source_binding_is_restored():
    result = _read(
        _reader(
            pull_fn=lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError("private decode details")
            )
        )
    )

    assert result.status is PlayerSaveHistoryReadStatus.UI_FALLBACK
    assert result.safe_ui_fallback
    assert result.metadata is None
    assert "private decode details" not in result.reason


def test_target_generation_change_blocks_ui_fallback():
    targets = iter(
        (
            AdbTargetSnapshot("private-target", 3, True),
            AdbTargetSnapshot("private-target", 4, True),
        )
    )

    result = _read(_reader(target_snapshot_fn=lambda: next(targets)))

    assert result.status is PlayerSaveHistoryReadStatus.BLOCKED
    assert not result.safe_ui_fallback


def test_boundary_or_control_loss_blocks_ui_fallback():
    states = iter(({"state": "RUNNING"}, {"state": "HOME_SCREEN"}))

    result = _read(_reader(detector=lambda _frame: next(states)))

    assert result.status is PlayerSaveHistoryReadStatus.BLOCKED
    assert result.reason == "history_source_binding_lost"


def test_source_compatibility_and_capacity_rollover_are_explicit():
    previous = history_metadata_from_snapshot(
        _snapshot(fingerprint="a" * 64, entry_count=30),
        acquisition="home",
    ).metadata
    rollover = history_metadata_from_snapshot(
        _snapshot(fingerprint="b" * 64, entry_count=30),
        acquisition="retry",
    ).metadata
    append = history_metadata_from_snapshot(
        _snapshot(fingerprint="c" * 64, entry_count=30),
        acquisition="retry",
    ).metadata
    assert previous is not None and rollover is not None and append is not None

    assert history_sources_compatible(previous, rollover)
    assert valid_history_tail_advance(previous, rollover)

    wrong_source = {**rollover, "source": "battle_history_ui"}
    assert not history_sources_compatible(previous, wrong_source)
    assert not valid_history_tail_advance(previous, wrong_source)

    before_append = {**previous, "entry_count": 29}
    assert valid_history_tail_advance(before_append, append)
    assert not valid_history_tail_advance(
        before_append,
        {**append, "entry_count": 29},
    )
