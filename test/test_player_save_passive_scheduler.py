from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from core.perk_save_monitor import PerkSaveMonitorContext
from core.player_save_acquisition import (
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
)
from core.player_save_passive_scheduler import PlayerSavePassiveScheduler


UTC = timezone.utc


def _context(
    generation: int = 3,
    *,
    activity: str = "scope-1",
) -> PerkSaveMonitorContext:
    return PerkSaveMonitorContext(
        runtime_session_id="runtime-1",
        activity_scope_id=activity,
        target_binding=PlayerSaveTargetBinding("localhost:5555", generation),
    )


def test_one_passive_read_fans_same_immutable_bundle_to_every_consumer():
    pull = Mock(return_value=b"stable-payload")
    snapshot = SimpleNamespace(marker="normalized")
    decode = Mock(return_value=snapshot)
    target = lambda: SimpleNamespace(
        target="localhost:5555",
        generation=3,
        owned=True,
    )
    acquirer = StablePlayerSaveAcquirer(
        target_snapshot_fn=target,
        pull_fn=pull,
        decode_fn=decode,
        now_fn=lambda: datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
    )
    received = []

    def consume(bundle, context, reason):
        received.append((bundle, context, reason))

    scheduler = PlayerSavePassiveScheduler(
        acquirer=acquirer,
        context_fn=_context,
        consumers=(consume, consume),
        start_worker=False,
    )

    assert scheduler.acquire_once("perk_selection_boundary")
    scheduler.close()

    pull.assert_called_once_with(device_id="localhost:5555")
    decode.assert_called_once()
    assert len(received) == 2
    assert received[0][0] is received[1][0]
    assert received[0][0].snapshot is snapshot
    assert received[0][0].acquisition_type is (
        PlayerSaveAcquisitionType.PASSIVE_STABLE_READ
    )
    assert received[0][0].status is PlayerSaveAcquisitionStatus.COMPLETE
    assert received[0][1] == received[1][1] == _context()
    assert received[0][2] == received[1][2] == "perk_selection_boundary"


def test_scheduler_does_not_read_without_an_active_bound_context():
    pull = Mock(return_value=b"unused")
    acquirer = StablePlayerSaveAcquirer(
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=3,
            owned=True,
        ),
        pull_fn=pull,
        decode_fn=Mock(),
    )
    consumer = Mock()
    scheduler = PlayerSavePassiveScheduler(
        acquirer=acquirer,
        context_fn=lambda: None,
        consumers=(consumer,),
        start_worker=False,
    )

    assert scheduler.acquire_once() is False
    scheduler.close()

    pull.assert_not_called()
    consumer.assert_not_called()


def test_target_generation_change_is_delivered_only_as_typed_failure():
    target = SimpleNamespace(
        target="localhost:5555",
        generation=4,
        owned=True,
    )
    pull = Mock(return_value=b"unused")
    received = []
    scheduler = PlayerSavePassiveScheduler(
        acquirer=StablePlayerSaveAcquirer(
            target_snapshot_fn=lambda: target,
            pull_fn=pull,
            decode_fn=Mock(),
        ),
        context_fn=lambda: _context(generation=3),
        consumers=(lambda bundle, *_args: received.append(bundle),),
        start_worker=False,
    )

    assert scheduler.acquire_once()
    scheduler.close()

    pull.assert_not_called()
    assert len(received) == 1
    assert received[0].status is PlayerSaveAcquisitionStatus.BINDING_REJECTED
    assert received[0].snapshot is None
    assert received[0].reason == "exact_target_binding_mismatch"


def test_activity_scope_change_during_read_discards_bundle_before_projection():
    pull = Mock(return_value=b"stable-payload")
    decode = Mock(return_value=SimpleNamespace(marker="normalized"))
    contexts = iter((_context(activity="scope-1"), _context(activity="scope-2")))
    consumer = Mock()
    scheduler = PlayerSavePassiveScheduler(
        acquirer=StablePlayerSaveAcquirer(
            target_snapshot_fn=lambda: SimpleNamespace(
                target="localhost:5555",
                generation=3,
                owned=True,
            ),
            pull_fn=pull,
            decode_fn=decode,
        ),
        context_fn=lambda: next(contexts),
        consumers=(consumer,),
        start_worker=False,
    )

    assert scheduler.acquire_once() is False
    scheduler.close()

    pull.assert_called_once_with(device_id="localhost:5555")
    decode.assert_called_once()
    consumer.assert_not_called()
