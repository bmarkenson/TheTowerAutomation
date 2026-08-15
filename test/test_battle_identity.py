from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from automation.missions.manager import MissionManager
from core.action_authority import AuthorityHold
from core.app import App
from core.battle_identity import (
    ActiveBattleIdentityCoordinator,
    BattleIdentityCheckContext,
    BattleIdentityCheckResult,
    BattleIdentityCheckStatus,
    BattleIdentityRelation,
    BattleIdentityStore,
    BattleIdentityStoreError,
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
from core.run_state import AUTOMATION, RunState
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


def test_store_rejects_identity_that_is_not_in_the_forced_save(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")

    with pytest.raises(BattleIdentityStoreError):
        store.bind(
            _identity(seed=54321),
            reason="battle_started",
            operation_id="launch-1",
            acquisition=_acquisition(_identity()),
        )


def test_store_rejects_active_save_as_inactive_proof(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")

    with pytest.raises(BattleIdentityStoreError):
        store.mark_inactive(
            reason="home_new_battle",
            operation_id="home-1",
            acquisition=_acquisition(_identity()),
        )


def test_fresh_forced_identity_replaces_corrupt_advisory_state(tmp_path):
    path = tmp_path / "battle_identity.json"
    path.write_text("{not-json", encoding="utf-8")
    store = BattleIdentityStore(path)
    identity = _identity()

    record, relation = store.bind(
        identity,
        reason="automation_resumed",
        operation_id="resume-1",
        acquisition=_acquisition(identity),
    )

    assert relation is BattleIdentityRelation.FIRST_OBSERVATION
    assert record.fingerprint == identity.fingerprint
    assert store.active() == record


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


def test_home_never_adopts_inactive_bytes_from_a_blocked_preflight(tmp_path):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._player_save_preflight_session_id = "home-check-1"
    app._player_save_preflight_coordinator = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    result = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.BLOCKED,
        "workflow_changed_after_restore",
        "save_first",
        {},
        {},
        False,
        acquisition=_acquisition(None),
    )

    blocked = app._bind_forced_home_inactive_identity(result)

    assert blocked.status is PlayerSavePreflightStatus.BLOCKED
    assert app._battle_identity_store.active() is None
    assert app._battle_identity_home_verified_preflight_id is None


def test_home_start_retries_forced_serialization_then_accepts_inactive_save(
    tmp_path,
):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    unavailable = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.BLOCKED,
        "save_acquisition_failed",
        "save_first",
        {},
        {},
        False,
    )
    ready = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.READY,
        "save_reconciled",
        "save_first",
        {},
        {},
        False,
        acquisition=_acquisition(None),
    )
    app._player_save_preflight_coordinator = Mock()
    app._player_save_preflight_coordinator.acquire.side_effect = (
        unavailable,
        ready,
    )
    app._home_battle_identity_attempt_key = Mock(
        return_value=("runtime-1", "start-1", "home_new")
    )
    app._runtime_policy = Mock(return_value={})
    app._flag_recoverable_runtime_failure = Mock()
    app._activity_history_reporter = None

    first = app._acquire_player_save_home_preflight({}, screenshot=object())
    second = app._acquire_player_save_home_preflight({}, screenshot=object())

    assert not first.ready
    assert second.ready
    assert app._player_save_preflight_coordinator.acquire.call_count == 2
    assert app._battle_identity_home_verified_preflight_id == (
        app._player_save_preflight_session_id
    )
    assert app._battle_identity_home_failed_attempt_key is None
    assert app._battle_identity_home_attempt_count == 0


def test_home_start_exhaustion_is_retryable_instead_of_wedging(tmp_path):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    workflow = {
        "request_id": "start-1",
        "intent": "start_battle",
        "status": "acknowledged",
    }
    app._supervisor = Mock()
    app._supervisor.battle_workflow = workflow
    app._mission_mgr = Mock()
    unavailable = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.BLOCKED,
        "save_acquisition_failed",
        "save_first",
        {},
        {},
        False,
    )
    app._player_save_preflight_coordinator = Mock()
    app._player_save_preflight_coordinator.acquire.return_value = unavailable
    attempt_key = ("runtime-1", "start-1", "home_new")
    app._home_battle_identity_attempt_key = Mock(return_value=attempt_key)
    app._runtime_policy = Mock(return_value={})
    app._flag_recoverable_runtime_failure = Mock()
    app._current_control_workflow_evidence = Mock(
        return_value={"observation_id": "runtime-1:1"}
    )
    app._activity_history_reporter = None

    first = app._acquire_player_save_home_preflight({}, screenshot=object())
    second = app._acquire_player_save_home_preflight({}, screenshot=object())
    cached = app._acquire_player_save_home_preflight({}, screenshot=object())

    assert first.status is PlayerSavePreflightStatus.BLOCKED
    assert second.status is PlayerSavePreflightStatus.BLOCKED
    assert cached is second
    assert first.reason == "home_inactive_round_identity_unavailable"
    assert app._player_save_preflight_coordinator.acquire.call_count == 2
    assert app._battle_identity_home_failed_attempt_key == attempt_key
    app._mission_mgr.revoke_initial_battle_intent.assert_called_once_with(
        "start_battle",
        request_id="start-1",
    )
    app._supervisor.transition_battle_workflow.assert_called_once()
    assert app._supervisor.transition_battle_workflow.call_args.args[:2] == (
        "start-1",
        "interrupted",
    )
    assert "retry Start" in (
        app._supervisor.transition_battle_workflow.call_args.kwargs["reason"]
    )


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


def test_running_changed_identity_repairs_a_boundary_hidden_during_pause(
    tmp_path,
):
    app = App.__new__(App)
    workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    app._supervisor = SimpleNamespace(
        is_paused=False,
        battle_workflow=workflow,
        manual_control=None,
    )
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._mission_mgr.observe_active_round_identity.return_value = True
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    identity = _identity(seed=98765, counter=10)
    acquisition = _acquisition(identity)
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.COMPLETE,
            "active_round_identity_bound",
            identity=identity,
            relation=BattleIdentityRelation.LATER_BATTLE,
            acquisition=acquisition,
            source_restored=True,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._publish_forced_battle_identity_bundle = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = _identity().fingerprint

    with patch("core.app.start_activity_scope") as rotate_scope:
        assert app._force_battle_identity(
            {"state": "RUNNING"},
            object(),
        )

    app._mission_mgr.observe_active_round_identity.assert_called_once_with(
        identity.fingerprint,
        changed_from_retained=True,
    )
    rotate_scope.assert_called_once()
    assert app._active_round_identity_fingerprint == identity.fingerprint


def test_terminal_home_continuation_owns_successor_forced_identity(tmp_path):
    app = App.__new__(App)
    operation_id = "terminal-launch-1"
    app._supervisor = SimpleNamespace(
        is_paused=False,
        battle_workflow=None,
        manual_control=None,
    )
    app._terminal_home_continuation = {
        "operation_id": operation_id,
        "phase": "action_dispatched",
    }
    app._terminal_home_continuation_owner_current = Mock(return_value=True)
    app._awaiting_initial_battle_intent = Mock(return_value=False)
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
    identity = _identity(seed=98765, counter=10)
    acquisition = _acquisition(identity)
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.COMPLETE,
            "active_round_identity_bound",
            identity=identity,
            relation=BattleIdentityRelation.FIRST_OBSERVATION,
            acquisition=acquisition,
            source_restored=True,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._publish_forced_battle_identity_bundle = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = None

    assert app._force_battle_identity({"state": "RUNNING"}, object())

    context = app._battle_identity_coordinator.bind.call_args.kwargs[
        "context"
    ]
    assert context.operation_id == operation_id
    assert app._active_round_identity_fingerprint == identity.fingerprint
    app._mission_mgr.observe_active_round_identity.assert_called_once_with(
        identity.fingerprint,
        changed_from_retained=False,
    )


def test_runtime_identity_retries_by_forced_serialization_after_cooldown(
    tmp_path,
):
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="runtime-check",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    app._battle_identity_check_owner = Mock(
        return_value=(
            "runtime-check",
            "runtime_reconciliation",
            AuthorityHold.BATTLE_IDENTITY,
            "runtime_reconciliation",
        )
    )
    app._current_battle_identity_check_context = Mock(return_value=context)
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.UNAVAILABLE,
            "save_acquisition_failed",
            source_restored=True,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._flag_recoverable_runtime_failure = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = None

    now = [100.0]
    with patch("core.app.time.monotonic", side_effect=lambda: now[0]):
        assert app._force_battle_identity({"state": "RUNNING"}, object())
        assert app._force_battle_identity({"state": "RUNNING"}, object())
        now[0] = 129.0
        assert not app._force_battle_identity({"state": "RUNNING"}, object())
        now[0] = 131.0
        assert app._force_battle_identity({"state": "RUNNING"}, object())

    assert app._battle_identity_coordinator.bind.call_count == 3


def test_safe_identity_failure_never_retains_stale_authority(tmp_path):
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.battle_workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    app._supervisor.manual_control = None
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.UNAVAILABLE,
            "save_acquisition_failed",
            source_restored=True,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._flag_recoverable_runtime_failure = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = _identity().fingerprint

    assert app._force_battle_identity({"state": "RUNNING"}, object())
    assert app._force_battle_identity({"state": "RUNNING"}, object())
    assert not app._force_battle_identity({"state": "RUNNING"}, object())

    assert app._active_round_identity_fingerprint is None
    assert app._battle_identity_reconciliation_required is True
    assert app._battle_identity_coordinator.bind.call_count == 2
    assert app._flag_recoverable_runtime_failure.call_count == 2
    app._supervisor.pause_for_catastrophic_failure.assert_not_called()
    app._mission_mgr.revoke_initial_battle_intent.assert_called_once_with(
        "attach_battle",
        request_id="attach-1",
    )
    app._supervisor.transition_battle_workflow.assert_called_once()
    assert (
        app._supervisor.transition_battle_workflow.call_args.args[:2]
        == ("attach-1", "interrupted")
    )


def test_unrestored_identity_serialization_pauses_before_any_battle_work(
    tmp_path,
):
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.battle_workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    app._supervisor.manual_control = None
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.BLOCKED,
            "source_restoration_failed",
            source_restored=False,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._flag_recoverable_runtime_failure = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = _identity().fingerprint

    assert app._force_battle_identity({"state": "RUNNING"}, object())

    assert app._active_round_identity_fingerprint is None
    app._supervisor.pause_for_catastrophic_failure.assert_called_once()
    app._flag_recoverable_runtime_failure.assert_not_called()


@pytest.mark.parametrize(
    "reason",
    [
        "exact_target_ownership_unverified",
        "restored_target_binding_unverified",
    ],
)
def test_identity_target_loss_is_catastrophic(reason, tmp_path):
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.battle_workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    app._supervisor.manual_control = None
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.BLOCKED,
            reason,
            source_restored=(reason.startswith("restored_")),
            lifecycle_input_attempted=(reason.startswith("restored_")),
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._flag_recoverable_runtime_failure = Mock()
    app._battle_identity_reconciliation_required = True

    assert app._force_battle_identity({"state": "RUNNING"}, object())

    app._supervisor.pause_for_catastrophic_failure.assert_called_once()
    kind = app._supervisor.pause_for_catastrophic_failure.call_args.args[0]
    assert kind.value == "target_ownership_lost"
    app._flag_recoverable_runtime_failure.assert_not_called()


def test_pause_and_enable_revoke_identity_until_forced_revalidation():
    app = App.__new__(App)
    prior_state = AUTOMATION.state
    try:
        AUTOMATION.state = RunState.RUNNING
        app._battle_identity_last_control_state = "RUNNING"
        app._active_round_identity = _identity()
        app._active_round_identity_fingerprint = _identity().fingerprint
        app._terminal_round_identity_fingerprint = _identity().fingerprint
        app._battle_identity_reconciliation_required = False
        app._battle_identity_home_verified_preflight_id = "home-1"
        app._battle_identity_operation_id = "operation-1"
        app._battle_identity_operation_kind = "runtime_reconciliation"
        app._battle_identity_failed_attempt_key = (
            "runtime_reconciliation",
            "runtime-1",
            "binding",
            "RUNNING",
        )

        AUTOMATION.state = RunState.PAUSED
        app._sync_battle_identity_control_boundary()

        assert app._active_round_identity is None
        assert app._active_round_identity_fingerprint is None
        assert app._terminal_round_identity_fingerprint is None
        assert app._battle_identity_reconciliation_required is True
        assert app._battle_identity_home_verified_preflight_id is None
        assert app._battle_identity_failed_attempt_key is None

        AUTOMATION.state = RunState.RUNNING
        app._sync_battle_identity_control_boundary()
        assert app._battle_identity_reconciliation_required is True
    finally:
        AUTOMATION.state = prior_state


def test_pause_surrender_home_enable_then_start_uses_forced_save_boundaries(
    tmp_path,
):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    old_identity = _identity()
    store.bind(
        old_identity,
        reason="battle_started",
        operation_id="old-launch",
        acquisition=_acquisition(old_identity),
    )
    app = App.__new__(App)
    app._battle_identity_store = store
    app._battle_identity_last_control_state = "RUNNING"
    app._active_round_identity = old_identity
    app._active_round_identity_fingerprint = old_identity.fingerprint
    app._terminal_round_identity_fingerprint = old_identity.fingerprint
    app._battle_identity_reconciliation_required = False
    app._player_save_preflight_session_id = "home-after-surrender"
    app._player_save_preflight_coordinator = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    prior_state = AUTOMATION.state
    try:
        AUTOMATION.state = RunState.PAUSED
        app._sync_battle_identity_control_boundary()
        assert app._active_round_identity_fingerprint is None
        assert store.active().fingerprint == old_identity.fingerprint

        home_result = PlayerSavePreflightResult(
            PlayerSavePreflightStatus.READY,
            "save_reconciled",
            "save_first",
            {},
            {},
            False,
            acquisition=_acquisition(None),
        )
        assert app._bind_forced_home_inactive_identity(home_result).ready
        assert store.active() is None

        AUTOMATION.state = RunState.RUNNING
        app._sync_battle_identity_control_boundary()
        assert app._battle_identity_reconciliation_required is True

        new_identity = _identity(seed=54321, counter=10)
        acquisition = _acquisition(new_identity)
        app._supervisor = SimpleNamespace(
            is_paused=False,
            battle_workflow={
                "request_id": "new-launch",
                "intent": "start_battle",
                "status": "action_dispatched",
            },
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

        def _bind_successor(**kwargs):
            record, relation = store.bind(
                new_identity,
                reason="battle_started",
                operation_id=kwargs["context"].operation_id,
                acquisition=acquisition,
            )
            assert record.fingerprint == new_identity.fingerprint
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.COMPLETE,
                "active_round_identity_bound",
                identity=new_identity,
                relation=relation,
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )

        app._battle_identity_coordinator = Mock()
        app._battle_identity_coordinator.bind.side_effect = _bind_successor
        app._update_action_authority = Mock()
        app._runtime_action_guard = Mock(return_value=True)
        app._publish_forced_battle_identity_bundle = Mock()

        assert app._force_battle_identity({"state": "RUNNING"}, object())
        assert store.active().fingerprint == new_identity.fingerprint
        assert app._active_round_identity_fingerprint == (
            new_identity.fingerprint
        )
        app._mission_mgr.observe_active_round_identity.assert_called_once_with(
            new_identity.fingerprint,
            changed_from_retained=False,
        )
    finally:
        AUTOMATION.state = prior_state


def test_pause_hidden_manual_successor_enable_classifies_later_save_identity(
    tmp_path,
):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    old_identity = _identity()
    store.bind(
        old_identity,
        reason="battle_started",
        operation_id="old-launch",
        acquisition=_acquisition(old_identity),
    )
    app = App.__new__(App)
    app._battle_identity_last_control_state = "RUNNING"
    app._active_round_identity = old_identity
    app._active_round_identity_fingerprint = old_identity.fingerprint
    app._terminal_round_identity_fingerprint = old_identity.fingerprint
    app._battle_identity_reconciliation_required = False
    app._battle_identity_store = store
    prior_state = AUTOMATION.state
    try:
        AUTOMATION.state = RunState.PAUSED
        app._sync_battle_identity_control_boundary()
        AUTOMATION.state = RunState.RUNNING
        app._sync_battle_identity_control_boundary()

        successor = _identity(seed=98765, counter=10)
        acquisition = _acquisition(successor)
        app._supervisor = SimpleNamespace(
            is_paused=False,
            battle_workflow=None,
            manual_control={
                "manual_control_id": "resume-after-manual-start",
                "status": "reconciling",
            },
        )
        app._mission_mgr = Mock()
        app._mission_mgr.active_battle_observed.return_value = True
        app._mission_mgr.observe_active_round_identity.return_value = True
        app._player_save_runtime_session_id = "runtime-1"
        app._adb_target_session = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                target="localhost:5555",
                generation=7,
                owned=True,
            )
        )

        def _bind_successor(**kwargs):
            record, relation = store.bind(
                successor,
                reason="automation_resumed",
                operation_id=kwargs["context"].operation_id,
                acquisition=acquisition,
            )
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.COMPLETE,
                "active_round_identity_bound",
                identity=record.identity,
                relation=relation,
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )

        app._battle_identity_coordinator = Mock()
        app._battle_identity_coordinator.bind.side_effect = _bind_successor
        app._update_action_authority = Mock()
        app._runtime_action_guard = Mock(return_value=True)
        app._publish_forced_battle_identity_bundle = Mock()

        with patch("core.app.start_activity_scope", return_value=None):
            assert app._force_battle_identity(
                {"state": "RUNNING"},
                object(),
            )

        record = store.active()
        assert record.fingerprint == successor.fingerprint
        assert record.session_preflight is None
        assert app._active_round_identity_fingerprint == successor.fingerprint
        app._mission_mgr.observe_active_round_identity.assert_called_once_with(
            successor.fingerprint,
            changed_from_retained=True,
        )
    finally:
        AUTOMATION.state = prior_state


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
