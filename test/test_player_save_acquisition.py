from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from core.adb_target_session import AdbTargetSnapshot
from core.player_save_acquisition import (
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
    quiet_player_save_read,
)


def _times(count=8):
    current = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    values = [current + timedelta(milliseconds=index) for index in range(count)]
    iterator = iter(values)
    return lambda: next(iterator)


def _snapshot(**overrides):
    values = {
        "mapping_id": "data-9-game-1073",
        "shape_valid": True,
        "source_sha256": "a" * 64,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parser_api_runs_once_and_bundle_is_reused_by_many_projectors():
    target = AdbTargetSnapshot("private-serial", 7, True)
    pulled = []
    decoded = []
    snapshot = _snapshot()

    def pull(**kwargs):
        pulled.append(kwargs)
        return b"private-save-payload"

    def decode(payload, **kwargs):
        decoded.append((payload, kwargs))
        return snapshot

    acquirer = StablePlayerSaveAcquirer(
        target_snapshot_fn=lambda: target,
        pull_fn=pull,
        parser=SimpleNamespace(parse_bytes=decode),
        source_name="/private/device/custom-save.dat",
        now_fn=_times(),
    )

    bundle = acquirer.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)

    assert bundle.complete
    assert bundle.status is PlayerSaveAcquisitionStatus.COMPLETE
    assert bundle.snapshot is snapshot
    assert pulled == [{"device_id": "private-serial"}]
    assert decoded[0][0] == b"private-save-payload"
    assert decoded[0][1]["source_name"] == "custom-save.dat"
    assert decoded[0][1]["captured_at"] == bundle.captured_at

    # Any number of pure projectors can consume the same immutable bundle.
    assert bundle.snapshot.mapping_id == "data-9-game-1073"
    assert bundle.snapshot.shape_valid is True
    assert len(pulled) == 1

    provenance = bundle.redacted_provenance()
    serialized = repr(provenance)
    assert provenance["type"] == "passive_stable_read"
    assert provenance["transport_stable"] is True
    assert provenance["binding_fingerprint"] == bundle.binding_fingerprint
    assert "private-serial" not in repr(bundle)
    assert "private-serial" not in serialized
    assert "private-save-payload" not in serialized
    with pytest.raises(FrozenInstanceError):
        bundle.reason = "changed"


def test_parser_and_legacy_decode_adapter_are_mutually_exclusive():
    with pytest.raises(ValueError, match="parser or legacy decode_fn"):
        StablePlayerSaveAcquirer(
            fixed_target="owned-target",
            parser=SimpleNamespace(parse_bytes=lambda *_args, **_kwargs: None),
            decode_fn=lambda *_args, **_kwargs: None,
        )


def test_each_acquisition_parses_again_without_a_latest_snapshot_cache():
    target = AdbTargetSnapshot("private-serial", 7, True)
    parsed = []

    def parse(payload, **_kwargs):
        snapshot = _snapshot(source_sha256=str(len(parsed) + 1) * 64)
        parsed.append((payload, snapshot))
        return snapshot

    acquirer = StablePlayerSaveAcquirer(
        target_snapshot_fn=lambda: target,
        pull_fn=lambda **_kwargs: b"stable-payload",
        parser=SimpleNamespace(parse_bytes=parse),
        now_fn=_times(16),
    )

    first = acquirer.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)
    second = acquirer.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)

    assert first.complete and second.complete
    assert len(parsed) == 2
    assert first.snapshot is parsed[0][1]
    assert second.snapshot is parsed[1][1]
    assert first.snapshot is not second.snapshot


def test_default_transport_is_quiet_and_bounded():
    target = AdbTargetSnapshot("private-serial", 3, True)
    calls = []

    def pull(**kwargs):
        calls.append(kwargs)
        return b"payload"

    # Supplying the real function identity is covered by the integration tests;
    # explicit transport options prove the owner, rather than a consumer, shapes
    # the call for injected transport too.
    acquirer = StablePlayerSaveAcquirer(
        target_snapshot_fn=lambda: target,
        pull_fn=pull,
        decode_fn=lambda *_args, **_kwargs: _snapshot(),
        now_fn=_times(),
        pull_options={
            "attempts": 3,
            "settle_seconds": 0.1,
            "read_fn": quiet_player_save_read,
        },
    )

    assert acquirer.acquire(
        PlayerSaveAcquisitionType.PASSIVE_STABLE_READ
    ).complete
    assert calls == [
        {
            "device_id": "private-serial",
            "attempts": 3,
            "settle_seconds": 0.1,
            "read_fn": quiet_player_save_read,
        }
    ]


def test_target_generation_change_discards_decoded_snapshot():
    snapshots = iter(
        (
            AdbTargetSnapshot("private-serial", 2, True),
            AdbTargetSnapshot("private-serial", 3, True),
        )
    )
    decoded = _snapshot()
    acquirer = StablePlayerSaveAcquirer(
        target_snapshot_fn=lambda: next(snapshots),
        pull_fn=lambda **_kwargs: b"payload",
        decode_fn=lambda *_args, **_kwargs: decoded,
        now_fn=_times(),
    )

    bundle = acquirer.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)

    assert bundle.status is PlayerSaveAcquisitionStatus.BINDING_LOST
    assert bundle.reason == "exact_target_binding_lost"
    assert bundle.snapshot is None
    assert bundle.transport_stable is True


def test_expected_target_mismatch_rejects_without_pull():
    pull_called = False

    def pull(**_kwargs):
        nonlocal pull_called
        pull_called = True
        return b"payload"

    acquirer = StablePlayerSaveAcquirer(
        target_snapshot_fn=lambda: AdbTargetSnapshot("second", 4, True),
        pull_fn=pull,
        decode_fn=lambda *_args, **_kwargs: _snapshot(),
        now_fn=_times(),
    )
    expected = PlayerSaveTargetBinding("first", 4)

    bundle = acquirer.acquire(
        PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        expected_binding=expected,
    )

    assert bundle.status is PlayerSaveAcquisitionStatus.BINDING_REJECTED
    assert bundle.reason == "exact_target_binding_mismatch"
    assert bundle.snapshot is None
    assert pull_called is False


def test_transport_options_cannot_override_exact_target_and_type_is_required():
    calls = []
    acquirer = StablePlayerSaveAcquirer(
        fixed_target="owned-target",
        pull_fn=lambda **kwargs: calls.append(kwargs) or b"payload",
        decode_fn=lambda *_args, **_kwargs: _snapshot(),
        now_fn=_times(),
        pull_options={"device_id": "wrong-target", "attempts": 2},
    )

    with pytest.raises(TypeError):
        acquirer.acquire("passive_stable_read")
    assert acquirer.acquire(
        PlayerSaveAcquisitionType.PASSIVE_STABLE_READ
    ).complete
    assert calls == [{"device_id": "owned-target", "attempts": 2}]


def test_decode_failure_is_sanitized_and_retains_no_snapshot():
    secret = "private-target:/private/path/playerInfo.dat"

    def decode(*_args, **_kwargs):
        raise RuntimeError(secret)

    acquirer = StablePlayerSaveAcquirer(
        fixed_target="private-target",
        pull_fn=lambda **_kwargs: b"private-payload",
        decode_fn=decode,
        now_fn=_times(),
    )

    bundle = acquirer.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)

    assert bundle.status is PlayerSaveAcquisitionStatus.UNAVAILABLE
    assert bundle.reason == "player_save_acquisition_failed"
    assert bundle.snapshot is None
    assert bundle.transport_stable is True
    assert secret not in repr(bundle)
    assert secret not in repr(bundle.redacted_provenance())


def test_natural_boundary_is_lifecycle_issued_and_redacted():
    boundary = PlayerSaveNaturalBoundary(
        kind=PlayerSaveBoundaryKind.GAME_OVER,
        observed_at=datetime(2026, 8, 7, 12, 1, tzinfo=timezone.utc),
        runtime_session_id="private-runtime",
        activity_scope_id="private-scope",
    )
    acquirer = StablePlayerSaveAcquirer(
        fixed_target="private-target",
        pull_fn=lambda **_kwargs: b"payload",
        decode_fn=lambda *_args, **_kwargs: _snapshot(),
        now_fn=_times(),
    )

    missing = acquirer.acquire(PlayerSaveAcquisitionType.NATURAL_BOUNDARY)
    complete = acquirer.acquire(
        PlayerSaveAcquisitionType.NATURAL_BOUNDARY,
        boundary=boundary,
    )

    assert missing.status is PlayerSaveAcquisitionStatus.BINDING_REJECTED
    assert missing.reason == "acquisition_boundary_invalid"
    assert complete.complete
    provenance = complete.redacted_provenance()
    assert provenance["boundary"]["kind"] == "GAME_OVER"
    assert "private-runtime" not in repr(provenance)
    assert "private-scope" not in repr(provenance)


def test_projection_incompatibility_is_not_an_acquisition_failure():
    snapshot = _snapshot(mapping_id=None, shape_valid=False)
    acquirer = StablePlayerSaveAcquirer(
        fixed_target="private-target",
        pull_fn=lambda **_kwargs: b"payload",
        decode_fn=lambda *_args, **_kwargs: snapshot,
        now_fn=_times(),
    )

    bundle = acquirer.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)

    assert bundle.complete
    assert bundle.snapshot is snapshot
    assert bundle.snapshot.mapping_id is None
    assert bundle.snapshot.shape_valid is False


def test_global_lock_serializes_acquisitions_across_owner_instances():
    first_entered = Event()
    release_first = Event()
    second_entered = Event()
    target = AdbTargetSnapshot("private-target", 1, True)

    def first_pull(**_kwargs):
        first_entered.set()
        assert release_first.wait(2.0)
        return b"first"

    def second_pull(**_kwargs):
        second_entered.set()
        return b"second"

    first = StablePlayerSaveAcquirer(
        target_snapshot_fn=lambda: target,
        pull_fn=first_pull,
        decode_fn=lambda *_args, **_kwargs: _snapshot(),
    )
    second = StablePlayerSaveAcquirer(
        target_snapshot_fn=lambda: target,
        pull_fn=second_pull,
        decode_fn=lambda *_args, **_kwargs: _snapshot(),
    )
    outcomes = []
    first_thread = Thread(
        target=lambda: outcomes.append(
            first.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)
        )
    )
    second_thread = Thread(
        target=lambda: outcomes.append(
            second.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)
        )
    )

    first_thread.start()
    assert first_entered.wait(1.0)
    second_thread.start()
    assert not second_entered.wait(0.05)
    release_first.set()
    first_thread.join(2.0)
    second_thread.join(2.0)

    assert second_entered.is_set()
    assert len(outcomes) == 2
    assert all(outcome.complete for outcome in outcomes)


def test_complete_stable_acquisition_notifies_advisory_observer_once():
    observed = []
    snapshot = _snapshot()
    acquirer = StablePlayerSaveAcquirer(
        fixed_target="private-target",
        pull_fn=lambda **_kwargs: b"payload",
        decode_fn=lambda *_args, **_kwargs: snapshot,
        now_fn=_times(),
        completion_observer=lambda bundle, _start_evidence: observed.append(bundle),
    )

    bundle = acquirer.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)

    assert bundle.complete
    assert observed == [bundle]
    assert observed[0].snapshot is snapshot


def test_advisory_observer_failure_cannot_degrade_stable_acquisition():
    def fail(_bundle, _start_evidence):
        raise OSError("receipt unavailable")

    acquirer = StablePlayerSaveAcquirer(
        fixed_target="private-target",
        pull_fn=lambda **_kwargs: b"payload",
        decode_fn=lambda *_args, **_kwargs: _snapshot(),
        now_fn=_times(),
        completion_observer=fail,
    )

    bundle = acquirer.acquire(PlayerSaveAcquisitionType.PASSIVE_STABLE_READ)

    assert bundle.complete
    assert bundle.reason == "save_acquired"


def test_completion_observer_waits_for_outer_mutation_and_target_boundaries():
    observed = []
    acquirer = StablePlayerSaveAcquirer(
        fixed_target="private-target",
        pull_fn=lambda **_kwargs: b"payload",
        decode_fn=lambda *_args, **_kwargs: _snapshot(),
        now_fn=_times(),
        completion_observer=lambda bundle, _start: observed.append(bundle),
    )

    with acquirer.deferred_completion_observers():
        with acquirer.locked_operation():
            bundle = acquirer.acquire(
                PlayerSaveAcquisitionType.FORCED_SERIALIZATION
            )
            assert observed == []
        assert observed == []

    assert observed == [bundle]
