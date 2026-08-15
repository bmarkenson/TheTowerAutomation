from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from automation.missions.manager import MissionManager
from core.app import App
from core.battle_identity import (
    ActiveBattleIdentityCoordinator,
    BattleIdentityCheckContext,
    BattleIdentityCheckStatus,
    BattleIdentityRelation,
    BattleIdentityStore,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
)
from core.player_save_serialization import (
    GuardedSerializationResult,
    GuardedSerializationStatus,
)
from core.player_save_preflight import (
    PlayerSavePreflightResult,
    PlayerSavePreflightStatus,
)
from core.runtime_save import ActiveRoundIdentity


def _identity(*, seed: int = 12345, counter: int = 9) -> ActiveRoundIdentity:
    values = {
        "game_version": 1102,
        "current_tier": 19,
        "rounds_started_this_tier": counter,
        "round_seed": seed,
    }
    rendered = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return ActiveRoundIdentity(
        **values,
        fingerprint=hashlib.sha256(rendered).hexdigest(),
    )


def _acquisition(
    identity: ActiveRoundIdentity | None,
) -> PlayerSaveAcquisitionBundle:
    now = datetime.now(timezone.utc)
    snapshot = SimpleNamespace(
        runtime_save=SimpleNamespace(
            round_active=identity is not None,
            active_round_identity=identity,
        )
    )
    return PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="stable_player_save_decoded",
        binding=PlayerSaveTargetBinding("localhost:5555", 7),
        acquisition_started_at=now,
        captured_at=now,
        acquisition_completed_at=now,
        transport_stable=True,
        snapshot=snapshot,
    )


def _coordinator(
    tmp_path,
    context: BattleIdentityCheckContext,
) -> ActiveBattleIdentityCoordinator:
    acquirer = StablePlayerSaveAcquirer(
        fixed_target="localhost:5555",
        pull_fn=lambda *_args, **_kwargs: b"unused",
        decode_fn=lambda *_args, **_kwargs: SimpleNamespace(),
    )
    return ActiveBattleIdentityCoordinator(
        acquirer=acquirer,
        store=BattleIdentityStore(tmp_path / "battle_identity.json"),
        context_fn=lambda: context,
        source_guard_fn=lambda _frame, _stable: True,
    )


def test_store_uses_active_round_identity_for_same_and_later_battles(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    first = _identity()
    acquisition = _acquisition(first)

    record, relation = store.bind(
        first,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=acquisition,
    )

    assert relation is BattleIdentityRelation.FIRST_OBSERVATION
    assert record.fingerprint == first.fingerprint
    assert store.record_session_preflight(
        identity_fingerprint=first.fingerprint,
        strategy="farm_t19",
        configuration_fingerprint="a" * 64,
        evidence={"valid": True, "failed_checks": []},
    )

    same, relation = store.bind(
        first,
        reason="automation_resumed",
        operation_id="resume-1",
        acquisition=acquisition,
    )
    assert relation is BattleIdentityRelation.SAME_BATTLE
    assert same.session_preflight is not None

    later_identity = _identity(seed=54321, counter=10)
    later, relation = store.bind(
        later_identity,
        reason="automation_resumed",
        operation_id="resume-2",
        acquisition=_acquisition(later_identity),
    )
    assert relation is BattleIdentityRelation.LATER_BATTLE
    assert later.fingerprint == later_identity.fingerprint
    assert later.session_preflight is None


def test_forced_inactive_save_closes_retained_active_identity(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )

    store.mark_inactive(
        reason="home_new_battle",
        operation_id="home-1",
        acquisition=_acquisition(None),
    )

    assert store.active() is None
    payload = json.loads((tmp_path / "battle_identity.json").read_text())
    assert payload["status"] == "inactive"
    assert payload["previous_active_identity"]["fingerprint"] == (
        identity.fingerprint
    )


def test_coordinator_binds_only_the_forced_active_identity(tmp_path):
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="launch-1",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    coordinator = _coordinator(tmp_path, context)
    identity = _identity()
    acquisition = _acquisition(identity)
    serialized = GuardedSerializationResult(
        GuardedSerializationStatus.COMPLETE,
        "save_acquired",
        acquisition=acquisition,
        lifecycle_input_attempted=True,
        background_dispatched=True,
        restoration_completed=True,
    )

    with patch(
        "core.battle_identity.GuardedPlayerSaveSerializer.acquire",
        return_value=serialized,
    ) as acquire:
        result = coordinator.bind(
            context=context,
            action_guard_fn=lambda: True,
            reason="battle_started",
            initial_frame=object(),
        )

    assert result.complete
    assert result.identity == identity
    assert result.relation is BattleIdentityRelation.FIRST_OBSERVATION
    assert result.recapture_required
    acquire.assert_called_once()


def test_coordinator_refuses_changed_identity_during_exact_verification(
    tmp_path,
):
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="cleanup-1",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    coordinator = _coordinator(tmp_path, context)
    identity = _identity(seed=99999)
    serialized = GuardedSerializationResult(
        GuardedSerializationStatus.COMPLETE,
        "save_acquired",
        acquisition=_acquisition(identity),
        lifecycle_input_attempted=True,
        background_dispatched=True,
        restoration_completed=True,
    )

    with patch(
        "core.battle_identity.GuardedPlayerSaveSerializer.acquire",
        return_value=serialized,
    ):
        result = coordinator.bind(
            context=context,
            action_guard_fn=lambda: True,
            reason="exclusive_validation_cleanup",
            expected_identity_fingerprint=_identity().fingerprint,
        )

    assert result.status is BattleIdentityCheckStatus.BLOCKED
    assert result.reason == "active_round_identity_changed"
    assert BattleIdentityStore(tmp_path / "battle_identity.json").active() is None


def test_coordinator_never_falls_back_from_inactive_or_unreadable_save(tmp_path):
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="attach-1",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    coordinator = _coordinator(tmp_path, context)
    serialized = GuardedSerializationResult(
        GuardedSerializationStatus.COMPLETE,
        "save_acquired",
        acquisition=_acquisition(None),
        lifecycle_input_attempted=True,
        background_dispatched=True,
        restoration_completed=True,
    )

    with patch(
        "core.battle_identity.GuardedPlayerSaveSerializer.acquire",
        return_value=serialized,
    ):
        result = coordinator.bind(
            context=context,
            action_guard_fn=lambda: True,
            reason="attach_battle",
        )

    assert result.status is BattleIdentityCheckStatus.UNAVAILABLE
    assert result.reason == (
        "active_round_identity_unavailable_after_forced_serialization"
    )
    assert result.source_restored
    assert result.recapture_required


def test_visual_lifecycle_repairs_a_boundary_hidden_during_pause():
    manager = MissionManager(None, None)
    manager.start()
    assert manager.maybe_run_start({"state": "RUNNING"}) is True
    assert manager.active_battle_observed() is True

    assert manager.observe_active_round_identity(
        _identity().fingerprint,
        changed_from_retained=False,
    ) is False
    assert manager.observe_active_round_identity(
        _identity(seed=98765, counter=10).fingerprint,
        changed_from_retained=True,
    ) is True
    assert manager.active_battle_observed() is False
    assert manager.maybe_run_start({"state": "RUNNING"}) is True


def test_home_new_battle_requires_forced_inactive_save(tmp_path):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._player_save_preflight_session_id = "home-check-1"
    app._player_save_preflight_coordinator = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    result = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.READY,
        "save_reconciled",
        "save_first",
        {},
        {},
        False,
        acquisition=_acquisition(None),
    )

    bound = app._bind_forced_home_inactive_identity(result)

    assert bound.ready
    assert app._battle_identity_home_verified_preflight_id == "home-check-1"
    assert app._battle_identity_store.active() is None
    app._flag_recoverable_runtime_failure.assert_not_called()


def test_home_new_battle_rejects_forced_active_save(tmp_path):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._player_save_preflight_session_id = "home-check-1"
    app._player_save_preflight_coordinator = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    result = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.READY,
        "save_reconciled",
        "save_first",
        {},
        {},
        False,
        acquisition=_acquisition(_identity()),
    )

    blocked = app._bind_forced_home_inactive_identity(result)

    assert blocked.status is PlayerSavePreflightStatus.BLOCKED
    assert blocked.reason == "home_inactive_round_identity_unavailable"
    assert app._battle_identity_store.active() is None
    app._player_save_preflight_coordinator.discard_carry.assert_called_once()


def test_running_start_binds_forced_identity_before_completion(tmp_path):
    app = App.__new__(App)
    workflow = {
        "request_id": "start-1",
        "intent": "start_battle",
        "status": "action_dispatched",
    }
    app._supervisor = SimpleNamespace(
        is_paused=False,
        battle_workflow=workflow,
        manual_control=None,
    )
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = False
    app._mission_mgr.observe_active_round_identity.return_value = False
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    identity = _identity()
    acquisition = _acquisition(identity)
    result = SimpleNamespace(
        complete=True,
        identity=identity,
        relation=BattleIdentityRelation.FIRST_OBSERVATION,
        acquisition=acquisition,
    )
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = result
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._publish_forced_battle_identity_bundle = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = None

    consumed = app._force_battle_identity(
        {"state": "RUNNING"},
        object(),
    )

    assert consumed is True
    assert app._active_round_identity_fingerprint == identity.fingerprint
    assert app._battle_identity_reconciliation_required is False
    app._mission_mgr.observe_active_round_identity.assert_called_once_with(
        identity.fingerprint,
        changed_from_retained=False,
    )
    app._publish_forced_battle_identity_bundle.assert_called_once_with(
        acquisition,
        relation=BattleIdentityRelation.FIRST_OBSERVATION,
        identity_fingerprint=identity.fingerprint,
    )


def test_coordinator_preserves_serializer_block_classification(tmp_path):
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="attach-1",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    coordinator = _coordinator(tmp_path, context)
    serialized = GuardedSerializationResult(
        GuardedSerializationStatus.BLOCKED,
        "restored_target_binding_unverified",
        lifecycle_input_attempted=True,
        background_dispatched=True,
        restoration_completed=True,
    )

    with patch(
        "core.battle_identity.GuardedPlayerSaveSerializer.acquire",
        return_value=serialized,
    ):
        result = coordinator.bind(
            context=context,
            action_guard_fn=Mock(return_value=True),
            reason="attach_battle",
        )

    assert result.status is BattleIdentityCheckStatus.BLOCKED
    assert result.reason == "restored_target_binding_unverified"
    assert result.source_restored
    assert result.recapture_required
