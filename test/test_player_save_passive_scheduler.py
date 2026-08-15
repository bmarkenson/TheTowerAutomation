from __future__ import annotations

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace
from unittest.mock import Mock, call

from core.app import App
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


def test_worker_accepts_only_explicit_perk_checkpoint_requests():
    parameters = inspect.signature(PlayerSavePassiveScheduler).parameters
    assert "interval_seconds" not in parameters
    assert "monotonic_fn" not in parameters

    pull = Mock(return_value=b"stable-payload")
    consumer = Mock()
    scheduler = PlayerSavePassiveScheduler(
        acquirer=StablePlayerSaveAcquirer(
            target_snapshot_fn=lambda: SimpleNamespace(
                target="localhost:5555",
                generation=3,
                owned=True,
            ),
            pull_fn=pull,
            decode_fn=Mock(return_value=SimpleNamespace(marker="normalized")),
        ),
        context_fn=_context,
        consumers=(consumer,),
    )

    assert scheduler.request_observation("periodic_interval") is False
    assert scheduler.request_observation("mapping_followup") is False
    assert scheduler.request_observation("perk_selection_boundary") is True
    assert scheduler.wait_until_idle()
    scheduler.close(wait=True)

    pull.assert_called_once_with(device_id="localhost:5555")
    assert consumer.call_count == 1
    assert consumer.call_args.args[2] == "perk_selection_boundary"


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


def test_target_generation_change_after_read_discards_bundle_before_projection():
    pull = Mock(return_value=b"stable-payload")
    decode = Mock(return_value=SimpleNamespace(marker="normalized"))
    contexts = iter((_context(generation=3), _context(generation=4)))
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


def test_app_applies_worker_checkpoint_only_on_matching_current_context():
    app = App.__new__(App)
    context = _context()
    checkpoint = {"schema_version": 1, "picked_count": 2}
    observer = Mock()
    observer.observe_saved_checkpoint.return_value = "initial_saved_prefix"
    app._perk_timeline_observer = observer
    app._pending_perk_timeline_save_checkpoint = (context, checkpoint)
    app._current_perk_save_monitor_context = lambda: context

    assert app._sync_perk_timeline_save_checkpoint() == "initial_saved_prefix"
    observer.observe_saved_checkpoint.assert_called_once_with(checkpoint)
    assert app._pending_perk_timeline_save_checkpoint is None


def test_app_discards_worker_checkpoint_after_activity_scope_changes():
    app = App.__new__(App)
    observer = Mock()
    app._perk_timeline_observer = observer
    app._pending_perk_timeline_save_checkpoint = (
        _context(activity="scope-1"),
        {"schema_version": 1},
    )
    app._current_perk_save_monitor_context = lambda: _context(
        activity="scope-2"
    )

    assert app._sync_perk_timeline_save_checkpoint() is None
    observer.observe_saved_checkpoint.assert_not_called()
    assert app._pending_perk_timeline_save_checkpoint is None


def test_app_fans_one_passive_bundle_to_perks_metrics_and_optional_audit():
    app = App.__new__(App)
    context = _context()
    acquisition = Mock()
    app._perk_save_monitor = Mock()
    app._perk_save_monitor.bound_checkpoint_evidence.return_value = None
    app._active_run_metric_monitor = Mock()
    app._active_run_metric_monitor.observe_bundle.return_value = (
        "accepted_checkpoint"
    )
    app._active_run_metric_monitor.latest_summary.return_value = None
    app._player_save_audit_collector = Mock()

    app._consume_passive_player_save_bundle(
        acquisition,
        context,
        "perk_selection_boundary",
    )

    app._perk_save_monitor.observe_bundle.assert_called_once_with(
        acquisition,
        context=context,
    )
    app._active_run_metric_monitor.observe_bundle.assert_called_once_with(
        acquisition,
        context=context,
    )
    app._player_save_audit_collector.observe_acquisition.assert_called_once_with(
        acquisition,
        reason_code="perk_selection_boundary",
    )


def test_app_requests_only_changed_perk_selection_or_exhaustion_boundaries():
    app = App.__new__(App)
    observer = Mock()
    scheduler = Mock()
    scheduler.request_observation.return_value = True
    app._perk_timeline_observer = observer
    app._player_save_passive_scheduler = scheduler
    app._last_requested_perk_checkpoint_signature = None

    observer.snapshot.return_value = {
        "passive_top_bar": {
            "selection_boundaries": [{"scheduled_wave": 200}],
            "exhaustion": None,
        }
    }
    app._request_perk_checkpoint_for_passive_boundary()
    app._request_perk_checkpoint_for_passive_boundary()

    observer.snapshot.return_value = {
        "passive_top_bar": {
            "selection_boundaries": [{"scheduled_wave": 200}],
            "exhaustion": {"event_id": "exhaustion-1"},
        }
    }
    app._request_perk_checkpoint_for_passive_boundary()

    assert scheduler.request_observation.call_args_list == [
        call("perk_selection_boundary"),
        call("perk_exhaustion_boundary"),
    ]
