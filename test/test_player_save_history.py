from types import SimpleNamespace

import pytest

from core.adb_target_session import AdbTargetSnapshot
from core.battle_lifecycle import HomeBattleControl
from core.player_save import pull_player_save_bytes
from core.player_save_history import (
    CrossSourceHistoryStatus,
    PlayerSaveAttachmentContext,
    PlayerSaveHistoryReadStatus,
    PlayerSaveHistoryReader,
    corroborate_ui_and_save_history,
    history_metadata_from_snapshot,
    history_sources_compatible,
    valid_history_tail_advance,
)


def _save_battle_date(
    *,
    kind: str = "local",
    clock_time: str = "2026-07-15T01:41:37.123456",
):
    kinds = {
        "unspecified": (0, "unspecified", clock_time),
        "utc": (1, "utc", f"{clock_time}+00:00"),
        "local": (2, "local_wall_clock_without_offset", clock_time),
        "local_ambiguous": (
            3,
            "local_wall_clock_without_offset",
            clock_time,
        ),
    }
    kind_id, clock_basis, normalized_time = kinds[kind]
    return {
        "kind_id": kind_id,
        "kind": kind,
        "ticks": "639197340971234560",
        "clock_time": normalized_time,
        "clock_basis": clock_basis,
        "submicrosecond_100ns": 0,
    }


def _snapshot(
    *,
    fingerprint: str = "a" * 64,
    entry_count: int = 29,
    capacity: int = 30,
    semantic_status: str = "observed",
    semantic_reason: str = "",
    active: bool = True,
    battle_date=None,
):
    identity = SimpleNamespace(
        mapping_id="data-9-game-1073",
        fingerprint=fingerprint,
        tier=19,
        wave=1899,
        battle_date=battle_date or _save_battle_date(),
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
        round_active=active,
        active_round_identity=(
            SimpleNamespace(
                game_version=1073,
                current_tier=19,
                rounds_started_this_tier=12,
                round_seed=123456789,
                fingerprint="active-round-fingerprint",
            )
            if active
            else None
        ),
    )
    return SimpleNamespace(
        runtime_save=runtime,
        captured_at="2026-08-04T20:00:00+00:00",
        game_version=1073,
    )


def _attachment_context(**changes):
    values = {
        "runtime_session_id": "runtime-1",
        "activity_scope_id": "scope-1",
        "target": "private-target",
        "target_generation": 3,
        "active_battle_observed": True,
    }
    values.update(changes)
    return PlayerSaveAttachmentContext(**values)


def _reader(
    *,
    target_snapshot_fn=lambda: AdbTargetSnapshot("private-target", 3, True),
    capture_fn=lambda: object(),
    detector=lambda _frame: {"state": "RUNNING"},
    scope_fn=lambda: {"run_id": "scope-1"},
    attachment_context_fn=None,
    background_fn=None,
    foreground_fn=None,
    pull_fn=lambda **_kwargs: b"stable-save",
    decode_fn=lambda _payload, **_kwargs: _snapshot(),
    sleep_fn=lambda _seconds: None,
    input_log_fn=lambda *_args, **_kwargs: None,
    debug_log_fn=lambda *_args, **_kwargs: None,
):
    return PlayerSaveHistoryReader(
        target_snapshot_fn=target_snapshot_fn,
        capture_fn=capture_fn,
        detector=detector,
        home_control_fn=lambda _frame: SimpleNamespace(
            control=HomeBattleControl.NEW_BATTLE
        ),
        scope_fn=scope_fn,
        attachment_context_fn=attachment_context_fn,
        background_fn=background_fn,
        foreground_fn=foreground_fn,
        pull_fn=pull_fn,
        decode_fn=decode_fn,
        sleep_fn=sleep_fn,
        input_log_fn=input_log_fn,
        debug_log_fn=debug_log_fn,
    )


def _read(reader):
    return reader.read(
        source_state="RUNNING",
        expected_home_control=HomeBattleControl.UNKNOWN,
        expected_scope_id="scope-1",
        action_guard_fn=lambda: True,
    )


def _read_active(reader, *, action_guard_fn=lambda: True):
    return reader.read(
        source_state="RUNNING",
        expected_home_control=HomeBattleControl.UNKNOWN,
        expected_scope_id="scope-1",
        action_guard_fn=action_guard_fn,
        serialize_active_attachment=True,
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
    assert result.metadata["battle_date"] == _save_battle_date()
    assert "killed_by_id" not in result.metadata


def _ui_history_metadata(**changes):
    values = {
        "schema_version": 2,
        "source": "battle_history_ui",
        "mapping_id": "battle-history-ui-report-v1",
        "identity_schema_version": 1,
        "fingerprint": "ui-fingerprint-never-compared",
        "battle_date": "Jul 15, 2026 01:41",
        "tier": "19",
        "wave": "1899",
    }
    values.update(changes)
    return values


def _save_history_metadata(**snapshot_changes):
    result = history_metadata_from_snapshot(
        _snapshot(**snapshot_changes),
        acquisition="forced_active_attachment_android_home_serialization",
    )
    assert result.metadata is not None
    return dict(result.metadata)


def test_cross_source_bridge_matches_tier_wave_and_local_date_without_fingerprint():
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(),
        _save_history_metadata(fingerprint="save-fingerprint-differs"),
    )

    assert result.status is CrossSourceHistoryStatus.MATCH
    assert result.reason == "cross_source_tier_wave_battle_date_match"


@pytest.mark.parametrize(
    ("ui_changes", "save_changes", "reason"),
    (
        (
            {"tier": "18"},
            {},
            "cross_source_tier_wave_mismatch",
        ),
        (
            {"wave": "1900"},
            {},
            "cross_source_tier_wave_mismatch",
        ),
        (
            {"battle_date": "Jul 15, 2026 01:42"},
            {},
            "cross_source_battle_date_mismatch",
        ),
    ),
)
def test_cross_source_bridge_reports_shared_identity_mismatch(
    ui_changes,
    save_changes,
    reason,
):
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(**ui_changes),
        _save_history_metadata(**save_changes),
    )

    assert result.status is CrossSourceHistoryStatus.MISMATCH
    assert result.reason == reason


@pytest.mark.parametrize(
    "kind",
    ("utc", "unspecified", "local_ambiguous"),
)
def test_cross_source_bridge_rejects_ambiguous_save_date_kind(kind):
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(),
        _save_history_metadata(battle_date=_save_battle_date(kind=kind)),
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "cross_source_battle_date_ambiguous"


def test_cross_source_bridge_rejects_malformed_ui_date():
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(battle_date="2026-07-15 01:41 unknown-zone"),
        _save_history_metadata(),
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "ui_history_identity_insufficient"


def test_cross_source_bridge_rejects_unknown_ui_identity_schema():
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(identity_schema_version=2),
        _save_history_metadata(),
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "ui_history_identity_insufficient"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tier", 0),
        ("wave", "0"),
        ("tier", False),
        ("wave", -1),
        ("tier", "01"),
        ("wave", " 1899"),
        ("tier", "+19"),
        ("wave", "1899.0"),
    ),
)
def test_cross_source_bridge_rejects_nonpositive_or_noncanonical_ui_values(
    field,
    value,
):
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(**{field: value}),
        _save_history_metadata(),
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "ui_history_identity_insufficient"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tier", 0),
        ("wave", "0"),
        ("tier", True),
        ("wave", -1),
        ("tier", "01"),
        ("wave", " 1899"),
        ("tier", "+19"),
        ("wave", "1899.0"),
    ),
)
def test_cross_source_bridge_rejects_nonpositive_or_noncanonical_save_values(
    field,
    value,
):
    save_metadata = _save_history_metadata()
    save_metadata[field] = value

    result = corroborate_ui_and_save_history(
        _ui_history_metadata(),
        save_metadata,
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "save_history_tier_wave_ambiguous"


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


def test_active_attachment_forces_serialization_and_restores_same_running_source():
    calls = {
        "target": 0,
        "context": 0,
        "capture": 0,
        "background": 0,
        "foreground": 0,
        "pull": 0,
    }
    inputs = []

    def target():
        calls["target"] += 1
        return AdbTargetSnapshot("private-target", 3, True)

    def context():
        calls["context"] += 1
        return _attachment_context()

    def capture():
        calls["capture"] += 1
        return object()

    def pull(**kwargs):
        calls["pull"] += 1
        assert kwargs == {"device_id": "private-target"}
        return b"stable-save"

    result = _read_active(
        _reader(
            target_snapshot_fn=target,
            capture_fn=capture,
            attachment_context_fn=context,
            background_fn=lambda _target: (
                calls.__setitem__("background", calls["background"] + 1)
                or True
            ),
            foreground_fn=lambda _target: (
                calls.__setitem__("foreground", calls["foreground"] + 1)
                or True
            ),
            pull_fn=pull,
            input_log_fn=lambda *args, **kwargs: inputs.append(
                (args, kwargs)
            ),
        )
    )

    assert result.complete
    assert result.metadata is not None
    assert result.metadata["acquisition"] == (
        "forced_active_attachment_android_home_serialization"
    )
    assert result.active_round_identity_fingerprint == (
        "active-round-fingerprint"
    )
    assert calls == {
        "target": 2,
        "context": 5,
        "capture": 4,
        "background": 1,
        "foreground": 1,
        "pull": 1,
    }
    assert len(inputs) == 2


def test_active_attachment_default_pull_uses_two_identical_reads(monkeypatch):
    import core.adb_utils as adb_utils
    import core.player_save as player_save

    reads = []

    def read_device_file(path, **kwargs):
        reads.append((path, kwargs))
        return b"identical-stable-save"

    monkeypatch.setattr(adb_utils, "read_device_file", read_device_file)
    monkeypatch.setattr(player_save.time, "sleep", lambda _seconds: None)

    result = _read_active(
        _reader(
            attachment_context_fn=_attachment_context,
            background_fn=lambda _target: True,
            foreground_fn=lambda _target: True,
            pull_fn=pull_player_save_bytes,
        )
    )

    assert result.complete
    assert len(reads) == 2
    assert all(
        kwargs
        == {
            "device_id": "private-target",
            "report_errors": False,
        }
        for _path, kwargs in reads
    )


@pytest.mark.parametrize(
    "fallback_kind",
    ("acquisition", "unsupported_projection"),
)
def test_active_attachment_allows_ui_fallback_only_after_safe_restoration(
    fallback_kind,
):
    lifecycle = []
    if fallback_kind == "acquisition":
        pull_fn = lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private read failure")
        )
        decode_fn = lambda _payload, **_kwargs: _snapshot()
    else:
        pull_fn = lambda **_kwargs: b"stable-save"
        decode_fn = lambda _payload, **_kwargs: SimpleNamespace(
            runtime_save=None,
            captured_at="2026-08-04T20:00:00+00:00",
            game_version=1073,
        )

    result = _read_active(
        _reader(
            attachment_context_fn=_attachment_context,
            background_fn=lambda _target: lifecycle.append("background") or True,
            foreground_fn=lambda _target: lifecycle.append("foreground") or True,
            pull_fn=pull_fn,
            decode_fn=decode_fn,
        )
    )

    assert result.status is PlayerSaveHistoryReadStatus.UI_FALLBACK
    assert result.safe_ui_fallback
    assert result.metadata is None
    assert lifecycle == ["background", "foreground"]


@pytest.mark.parametrize(
    "failure_kind",
    ("target", "scope", "source", "control"),
)
def test_active_attachment_prebackground_authority_failure_is_action_free(
    failure_kind,
):
    lifecycle = []
    target_snapshot_fn = lambda: AdbTargetSnapshot(
        "private-target",
        3,
        failure_kind != "target",
    )
    context = _attachment_context
    if failure_kind == "scope":
        context = lambda: _attachment_context(
            activity_scope_id="different-scope"
        )
    detector = (
        (lambda _frame: {"state": "HOME_SCREEN"})
        if failure_kind == "source"
        else (lambda _frame: {"state": "RUNNING"})
    )
    action_guard = (
        (lambda: False)
        if failure_kind == "control"
        else (lambda: True)
    )

    result = _read_active(
        _reader(
            target_snapshot_fn=target_snapshot_fn,
            detector=detector,
            attachment_context_fn=context,
            background_fn=lambda _target: lifecycle.append("background") or True,
            foreground_fn=lambda _target: lifecycle.append("foreground") or True,
        ),
        action_guard_fn=action_guard,
    )

    assert result.status is PlayerSaveHistoryReadStatus.BLOCKED
    assert not result.safe_ui_fallback
    assert lifecycle == []


def test_active_attachment_control_loss_while_backgrounded_cannot_restore():
    authority = iter((True, False))
    lifecycle = []
    result = _read_active(
        _reader(
            attachment_context_fn=_attachment_context,
            background_fn=lambda _target: lifecycle.append("background") or True,
            foreground_fn=lambda _target: lifecycle.append("foreground") or True,
        ),
        action_guard_fn=lambda: next(authority),
    )

    assert result.status is PlayerSaveHistoryReadStatus.BLOCKED
    assert not result.safe_ui_fallback
    assert result.reason.endswith(
        "control_authority_interrupted_before_foreground"
    )
    assert lifecycle == ["background"]


def test_active_attachment_rechecks_context_at_background_input_boundary():
    lifecycle = []
    contexts = iter(
        (
            _attachment_context(),
            _attachment_context(),
            _attachment_context(runtime_session_id="runtime-2"),
        )
    )

    result = _read_active(
        _reader(
            attachment_context_fn=lambda: next(contexts),
            background_fn=lambda _target: lifecycle.append("background") or True,
            foreground_fn=lambda _target: lifecycle.append("foreground") or True,
        )
    )

    assert result.status is PlayerSaveHistoryReadStatus.BLOCKED
    assert not result.safe_ui_fallback
    assert result.reason.endswith("initial_source_boundary_unverified")
    assert lifecycle == []


@pytest.mark.parametrize(
    "failure_kind",
    ("foreground", "target_generation", "process", "restored_source"),
)
def test_active_attachment_restoration_ambiguity_blocks_ui_fallback(
    failure_kind,
):
    lifecycle = []
    targets = iter(
        (
            AdbTargetSnapshot("private-target", 3, True),
            AdbTargetSnapshot(
                "private-target",
                4 if failure_kind == "target_generation" else 3,
                True,
            ),
        )
    )
    contexts = iter(
        (
            _attachment_context(),
            _attachment_context(),
            _attachment_context(),
            _attachment_context(
                runtime_session_id=(
                    "runtime-2" if failure_kind == "process" else "runtime-1"
                )
            ),
            _attachment_context(
                runtime_session_id=(
                    "runtime-2" if failure_kind == "process" else "runtime-1"
                )
            ),
        )
    )
    states = iter(
        (
            {"state": "RUNNING"},
            {"state": "RUNNING"},
            {"state": "RUNNING"},
            {
                "state": (
                    "HOME_SCREEN"
                    if failure_kind == "restored_source"
                    else "RUNNING"
                )
            },
        )
    )

    result = _read_active(
        _reader(
            target_snapshot_fn=lambda: next(targets),
            detector=lambda _frame: next(states),
            attachment_context_fn=lambda: next(contexts),
            background_fn=lambda _target: lifecycle.append("background") or True,
            foreground_fn=lambda _target: (
                lifecycle.append("foreground")
                or failure_kind != "foreground"
            ),
        )
    )

    assert result.status is PlayerSaveHistoryReadStatus.BLOCKED
    assert not result.safe_ui_fallback
    assert lifecycle == ["background", "foreground"]


@pytest.mark.parametrize("failure_kind", ("context", "control"))
def test_active_attachment_rechecks_authority_after_stable_restoration(
    failure_kind,
):
    lifecycle = []
    contexts = iter(
        (
            _attachment_context(),
            _attachment_context(),
            _attachment_context(),
            _attachment_context(),
            _attachment_context(
                runtime_session_id=(
                    "runtime-2" if failure_kind == "context" else "runtime-1"
                )
            ),
        )
    )
    authority = iter(
        (
            True,
            True,
            True,
            failure_kind != "control",
        )
    )

    result = _read_active(
        _reader(
            attachment_context_fn=lambda: next(contexts),
            background_fn=lambda _target: lifecycle.append("background") or True,
            foreground_fn=lambda _target: lifecycle.append("foreground") or True,
        ),
        action_guard_fn=lambda: next(authority),
    )

    assert result.status is PlayerSaveHistoryReadStatus.BLOCKED
    assert not result.safe_ui_fallback
    assert result.reason.endswith("restored_source_boundary_unverified")
    assert lifecycle == ["background", "foreground"]


def test_active_attachment_conflicting_active_round_identity_blocks_fallback():
    result = _read_active(
        _reader(
            attachment_context_fn=_attachment_context,
            background_fn=lambda _target: True,
            foreground_fn=lambda _target: True,
            decode_fn=lambda _payload, **_kwargs: _snapshot(active=False),
        )
    )

    assert result.status is PlayerSaveHistoryReadStatus.BLOCKED
    assert not result.safe_ui_fallback
    assert result.reason == "active_round_identity_conflicted_after_restore"


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
