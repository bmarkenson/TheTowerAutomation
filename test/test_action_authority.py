import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.action_authority import (
    AuthorityHold,
    AuthorityHoldState,
    AuxiliaryCollector,
    RuntimeActionAuthority,
    RuntimeActionAuthorityPublisher,
    RuntimeActionClass,
    STRATEGY_GATE_AUXILIARY_ALLOWLIST,
)
from core.app import App
from core.action_executor import execute_actions
from core.emulator_recovery import RestartReplayWindow
from core.input import safe_tap
from core.run_state import AUTOMATION
from core.runtime_failure_policy import RuntimeFailureKind


ACTIVE_BATTLE_IDENTITY = "a" * 64


def _set_running_context(
    authority: RuntimeActionAuthority,
    *,
    paused: bool = False,
    holds: tuple[AuthorityHoldState, ...] = (),
    state: str = "RUNNING",
    scope: str = "run-1",
    stopped: bool = False,
    active_battle: bool = True,
    battle_identity: str | None = ACTIVE_BATTLE_IDENTITY,
) -> None:
    authority.update_context(
        global_pause=paused,
        active_battle=active_battle,
        battle_scope=scope,
        battle_identity=(battle_identity if active_battle else None),
        primary_state=state,
        holds=holds,
        runtime_stopped=stopped,
    )


def _activate_gate(authority: RuntimeActionAuthority, *, scope="run-1"):
    return authority.activate_strategy_gate(
        strategy="farm_t18",
        battle_scope=scope,
        source="session_preflight",
        phase="running_battle",
        failed_check_ids=("modules", "target_priority"),
        reason="Modules and Target Priority do not match",
        now=1_700_000_000,
    )


def _terminal_gate_app() -> App:
    strategy = Mock(name="strategy")
    strategy.name = "farm_t18"
    strategy.requires_session_preflight.return_value = True
    strategy.is_session_preflight_complete.return_value = False
    manager = Mock()
    manager.strategy = strategy
    manager.ctx = SimpleNamespace(
        data={
            "mission_vars": {
                "gc_session_preflight_last_reason": "Modules do not match",
                "gc_session_preflight_failed_checks": ["modules"],
            }
        }
    )
    manager.session_preflight_failure_checks.return_value = ["modules"]
    app = App.__new__(App)
    app._mission_mgr = manager
    app._supervisor = Mock()
    app._supervisor.pause_for_operator_authority.return_value = True
    app._supervisor.consume_gate_decision.return_value = True
    app._action_authority = RuntimeActionAuthority()
    app._current_run_scope_id = Mock(return_value="run-1")
    app._pending_auxiliary_cleanup = None
    app._config = SimpleNamespace(strategy_name="farm_t18")
    app._current_control_workflow_evidence = Mock(
        return_value={
            "schema_version": 1,
            "runtime_id": "runtime-1",
            "pid": 1234,
            "adb_target": "localhost:5555",
            "target_generation": 7,
            "activity_scope_run_id": "run-1",
            "game_state": "active_battle",
        }
    )
    return app


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, None),
        ({"session_preflight_pending": True}, AuthorityHold.SESSION_PREFLIGHT),
        (
            {
                "initialization_pending": True,
                "session_preflight_pending": True,
            },
            AuthorityHold.RUN_INITIALIZATION,
        ),
        (
            {
                "exclusive_validation_in_progress": True,
                "initialization_pending": True,
            },
            AuthorityHold.EXCLUSIVE_VALIDATION,
        ),
        (
            {"exclusive_validation_launch_in_progress": True},
            AuthorityHold.EXCLUSIVE_VALIDATION,
        ),
        (
            {
                "exclusive_validation_ownership_hold": True,
                "exclusive_validation_in_progress": True,
            },
            AuthorityHold.EXCLUSIVE_OWNERSHIP,
        ),
        (
            {
                "operator_workflow_hold": AuthorityHoldState(
                    AuthorityHold.SETUP_CAPTURE,
                    "setup capture owns the screen",
                ),
                "exclusive_validation_in_progress": True,
            },
            AuthorityHold.SETUP_CAPTURE,
        ),
        (
            {
                "exclusive_validation_terminal_finalization_pending": True,
                "operator_workflow_hold": AuthorityHoldState(
                    AuthorityHold.OPERATOR_WORKFLOW,
                    "a successor Start Battle is queued",
                ),
            },
            AuthorityHold.EXCLUSIVE_VALIDATION,
        ),
        (
            {
                "exclusive_validation_passive_battle_hold": True,
                "operator_workflow_hold": AuthorityHoldState(
                    AuthorityHold.OPERATOR_WORKFLOW,
                    "a successor workflow is queued",
                ),
            },
            AuthorityHold.EXCLUSIVE_OWNERSHIP,
        ),
    ],
)
def test_heartbeat_owner_selection_has_one_explicit_priority(
    overrides,
    expected,
):
    inputs = {
        "operator_workflow_hold": None,
        "exclusive_validation_terminal_finalization_pending": False,
        "exclusive_validation_passive_battle_hold": False,
        "exclusive_validation_ownership_hold": False,
        "exclusive_validation_in_progress": False,
        "exclusive_validation_launch_in_progress": False,
        "initialization_pending": False,
        "session_preflight_pending": False,
    }
    inputs.update(overrides)

    holds = App._heartbeat_action_authority_holds(**inputs)

    assert len(holds) == (0 if expected is None else 1)
    if expected is not None:
        assert holds[0].hold is expected


def test_validation_lifecycle_waits_for_higher_priority_operator_owner():
    app = App.__new__(App)
    app._advance_exclusive_validation = Mock(return_value=True)
    app._update_action_authority(
        detection={"state": "RUNNING", "secondary_states": []},
        holds=(
            AuthorityHoldState(
                AuthorityHold.SETUP_CAPTURE,
                "setup capture owns the screen",
            ),
        ),
    )

    assert not app._advance_owned_exclusive_validation(
        {"state": "RUNNING", "secondary_states": []}
    )
    app._advance_exclusive_validation.assert_not_called()


def test_validation_terminal_claim_defers_new_operator_hold_until_release():
    app = App.__new__(App)
    exclusive = AuthorityHoldState(
        AuthorityHold.EXCLUSIVE_VALIDATION,
        "exact validation cleanup is finalizing",
    )
    app._authority_holds = (exclusive,)
    app._exclusive_validation_terminal_hold = "validation-cleanup"
    app._operator_workflow_authority_hold = Mock(
        return_value=AuthorityHoldState(
            AuthorityHold.OPERATOR_WORKFLOW,
            "a successor Start Battle is queued",
        )
    )

    assert app._refreshed_operator_authority_holds(
        release_stale=False
    ) == (exclusive,)
    app._operator_workflow_authority_hold.assert_not_called()


def test_validation_launch_waits_for_higher_priority_operator_owner():
    app = App.__new__(App)
    app._advance_exclusive_validation_launch = Mock(return_value=True)
    app._update_action_authority(
        detection={"state": "HOME_SCREEN", "secondary_states": []},
        holds=(
            AuthorityHoldState(
                AuthorityHold.SETUP_CAPTURE,
                "setup capture owns the screen",
            ),
        ),
    )

    assert not app._advance_owned_exclusive_validation_launch(
        object(),
        {"state": "HOME_SCREEN", "secondary_states": []},
        battle_started=False,
    )
    app._advance_exclusive_validation_launch.assert_not_called()


def test_complete_normal_pause_and_strategy_gate_authority_matrix():
    authority = RuntimeActionAuthority()
    _set_running_context(authority)

    assert authority.decision(RuntimeActionClass.OBSERVATION).allowed
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION
    ).allowed
    for collector in STRATEGY_GATE_AUXILIARY_ALLOWLIST:
        assert authority.decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
        ).allowed
    assert authority.decision(RuntimeActionClass.STRATEGY_ACTION).allowed
    assert authority.decision(RuntimeActionClass.LIFECYCLE_ACTION).allowed

    _set_running_context(authority, paused=True)
    assert authority.decision(RuntimeActionClass.OBSERVATION).allowed
    for collector in STRATEGY_GATE_AUXILIARY_ALLOWLIST:
        decision = authority.decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
        )
        assert not decision.allowed
        assert "global Pause" in decision.reason
    for action_class in (
        RuntimeActionClass.STRATEGY_ACTION,
        RuntimeActionClass.LIFECYCLE_ACTION,
    ):
        decision = authority.decision(action_class)
        assert not decision.allowed
        assert "global Pause" in decision.reason

    _set_running_context(authority)
    gate = _activate_gate(authority)
    assert authority.decision(RuntimeActionClass.OBSERVATION).allowed
    for collector in STRATEGY_GATE_AUXILIARY_ALLOWLIST:
        assert authority.decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
        ).allowed
    assert not authority.decision(
        RuntimeActionClass.STRATEGY_ACTION
    ).allowed
    assert not authority.decision(
        RuntimeActionClass.LIFECYCLE_ACTION
    ).allowed

    _set_running_context(authority, stopped=True)
    assert authority.decision(RuntimeActionClass.OBSERVATION).allowed
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.IN_BATTLE_AD_GEM,
    ).allowed
    assert not authority.decision(RuntimeActionClass.STRATEGY_ACTION).allowed
    assert "STOPPED" in authority.decision(
        RuntimeActionClass.LIFECYCLE_ACTION
    ).reason

    _set_running_context(authority)
    snapshot = authority.snapshot(now=1_700_000_001)
    assert snapshot.active
    assert snapshot.gate_id == gate.gate_id
    assert snapshot.strategy == "farm_t18"
    assert snapshot.battle_scope == "run-1"
    assert snapshot.failed_check_ids == ("modules", "target_priority")
    assert snapshot.observation_authority.allowed
    assert snapshot.allowed_auxiliary_collectors == (
        STRATEGY_GATE_AUXILIARY_ALLOWLIST
    )
    assert not snapshot.strategy_action_authority.allowed
    assert not snapshot.lifecycle_action_authority.allowed


def test_emulator_recovery_supersedes_ordinary_internal_holds_only():
    app = App.__new__(App)
    app._action_authority = RuntimeActionAuthority()
    app._authority_battle_active = False
    app._authority_primary_state = "HOME_SCREEN"
    app._external_development_hold_active = False
    app._emulator_maintenance_hold_active = True
    app._supervisor = SimpleNamespace(is_paused=False)
    app._current_run_scope_id = Mock(return_value="run-1")
    superseded = tuple(
        AuthorityHoldState(hold, "ordinary recovery boundary")
        for hold in (
            AuthorityHold.RUN_INITIALIZATION,
            AuthorityHold.SESSION_PREFLIGHT,
            AuthorityHold.OPERATOR_WORKFLOW,
            AuthorityHold.BLOCKING_MODAL_RECOVERY,
        )
    )

    app._update_action_authority(holds=superseded)

    snapshot = app._action_authority.snapshot()
    assert tuple(item.hold for item in snapshot.holds) == (
        AuthorityHold.EMULATOR_MAINTENANCE,
    )
    assert app._action_decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=AuthorityHold.EMULATOR_MAINTENANCE,
    ).allowed

    app._update_action_authority(
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_OWNERSHIP,
                "genuine competing owner",
            ),
        )
    )
    assert not app._action_decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=AuthorityHold.EMULATOR_MAINTENANCE,
    ).allowed


def test_emulator_replay_hold_allows_only_independent_collectors():
    app = App.__new__(App)
    app._action_authority = RuntimeActionAuthority()
    app._authority_battle_active = False
    app._authority_primary_state = "UNKNOWN"
    app._authority_holds = ()
    app._external_development_hold_active = False
    app._emulator_maintenance_hold_active = True
    app._emulator_recovery_request_id = "maintenance-1"
    app._emulator_recovery_force_new_battle = False
    app._active_round_identity_fingerprint = ACTIVE_BATTLE_IDENTITY
    app._emulator_replay_window = RestartReplayWindow(
        "maintenance-1",
        100,
        battle_scope=ACTIVE_BATTLE_IDENTITY,
    )
    app._emulator_replay_window.mark_resume_dispatched()
    app._supervisor = SimpleNamespace(
        is_paused=False,
        emulator_maintenance={
            "request_id": "maintenance-1",
            "state": "host_restarted",
        },
    )
    app._current_run_scope_id = Mock(return_value="run-1")

    app._update_action_authority(
        detection={"state": "RUNNING"},
        holds=(),
    )

    snapshot = app._action_authority.snapshot()
    assert snapshot.holds == (
        AuthorityHoldState(
            AuthorityHold.EMULATOR_MAINTENANCE,
            (
                "BlueStacks maintenance owns recovery while independent "
                "in-battle collectors remain available"
            ),
            allowed_auxiliary_collectors=STRATEGY_GATE_AUXILIARY_ALLOWLIST,
        ),
    )
    for collector in STRATEGY_GATE_AUXILIARY_ALLOWLIST:
        assert app._action_decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
        ).allowed
    assert not app._action_decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.HOME_AD_GEM,
    ).allowed
    assert not app._action_decision(
        RuntimeActionClass.STRATEGY_ACTION
    ).allowed
    assert not app._action_decision(
        RuntimeActionClass.LIFECYCLE_ACTION
    ).allowed
    assert app._action_decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=AuthorityHold.EMULATOR_MAINTENANCE,
    ).allowed

    app._supervisor.is_paused = True
    app._update_action_authority(detection={"state": "RUNNING"})

    for collector in STRATEGY_GATE_AUXILIARY_ALLOWLIST:
        assert not app._action_decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
        ).allowed
    assert not app._action_decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=AuthorityHold.EMULATOR_MAINTENANCE,
    ).allowed


def test_initial_battle_intent_hold_allows_only_fresh_home_ad_gem():
    authority = RuntimeActionAuthority()
    hold = AuthorityHoldState(
        AuthorityHold.OPERATOR_WORKFLOW,
        "runtime is waiting for explicit Start Battle or Attach to Battle intent",
        allowed_auxiliary_collectors=(AuxiliaryCollector.HOME_AD_GEM,),
    )
    _set_running_context(
        authority,
        state="HOME_SCREEN",
        active_battle=False,
        holds=(hold,),
    )

    decision = authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.HOME_AD_GEM,
    )
    assert decision.allowed is True
    for collector in AuxiliaryCollector:
        if collector is AuxiliaryCollector.HOME_AD_GEM:
            continue
        assert authority.decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
        ).allowed is False
    assert authority.decision(RuntimeActionClass.STRATEGY_ACTION).allowed is False
    assert authority.decision(RuntimeActionClass.LIFECYCLE_ACTION).allowed is False

    snapshot = authority.snapshot(now=1_700_000_001)
    assert snapshot.allowed_auxiliary_collectors == (
        AuxiliaryCollector.HOME_AD_GEM,
    )
    assert snapshot.auxiliary_collection_authority.allowed is True
    assert snapshot.as_dict()["holds"] == [
        {
            "hold": "operator_workflow",
            "reason": (
                "runtime is waiting for explicit Start Battle or Attach to "
                "Battle intent"
            ),
            "allowed_auxiliary_collectors": ["home_ad_gem"],
        }
    ]

    _set_running_context(
        authority,
        paused=True,
        state="HOME_SCREEN",
        active_battle=False,
        holds=(hold,),
    )
    paused = authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.HOME_AD_GEM,
    )
    assert paused.allowed is False
    assert "global Pause" in paused.reason

    _set_running_context(
        authority,
        state="RUNNING",
        holds=(hold,),
    )
    wrong_screen = authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.HOME_AD_GEM,
    )
    assert wrong_screen.allowed is False
    assert "Home frame" in wrong_screen.reason


def test_home_ad_gem_allowance_does_not_bypass_another_hold():
    authority = RuntimeActionAuthority()
    waiting = AuthorityHoldState(
        AuthorityHold.OPERATOR_WORKFLOW,
        "runtime is waiting for explicit battle intent",
        allowed_auxiliary_collectors=(AuxiliaryCollector.HOME_AD_GEM,),
    )
    manual = AuthorityHoldState(
        AuthorityHold.MANUAL_CONTROL_RETURN,
        "manual control owns input",
    )
    _set_running_context(
        authority,
        state="HOME_SCREEN",
        active_battle=False,
        holds=(waiting, manual),
    )

    decision = authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.HOME_AD_GEM,
    )
    assert decision.allowed is False
    assert "operator_workflow, manual_control_return" in decision.reason


@pytest.mark.parametrize(
    "hold",
    tuple(
        hold
        for hold in AuthorityHold
        if hold is not AuthorityHold.EXTERNAL_DEVELOPMENT
    ),
)
def test_exclusive_holds_precede_gate_and_block_all_auxiliary_collection(hold):
    authority = RuntimeActionAuthority()
    hold_state = AuthorityHoldState(hold, f"{hold.value} owns the screen")
    _set_running_context(authority, holds=(hold_state,))
    _activate_gate(authority)

    assert authority.decision(RuntimeActionClass.OBSERVATION).allowed
    for collector in AuxiliaryCollector:
        assert not authority.decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
        ).allowed
        assert not authority.decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
            owner=hold.value,
        ).allowed
    assert not authority.decision(
        RuntimeActionClass.STRATEGY_ACTION
    ).allowed
    assert not authority.decision(
        RuntimeActionClass.LIFECYCLE_ACTION
    ).allowed

    # The hold, not the Strategy Gate, authorizes its own already-bounded
    # initialization/validation/repair work.
    assert authority.decision(
        RuntimeActionClass.STRATEGY_ACTION,
        owner=hold.value,
    ).allowed
    assert authority.decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=hold.value,
    ).allowed

    _set_running_context(authority, paused=True, holds=(hold_state,))
    assert not authority.decision(
        RuntimeActionClass.STRATEGY_ACTION,
        owner=hold.value,
    ).allowed
    assert "global Pause" in authority.decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=hold.value,
    ).reason


def test_external_development_hold_is_suppressive_without_owner_bypass():
    authority = RuntimeActionAuthority()
    hold = AuthorityHoldState(
        AuthorityHold.EXTERNAL_DEVELOPMENT,
        "interactive development owns the cooperative input window",
    )
    _set_running_context(authority, holds=(hold,))

    assert authority.decision(RuntimeActionClass.OBSERVATION).allowed
    for collector in AuxiliaryCollector:
        assert not authority.decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
            owner=AuthorityHold.EXTERNAL_DEVELOPMENT.value,
        ).allowed
    for action_class in (
        RuntimeActionClass.STRATEGY_ACTION,
        RuntimeActionClass.LIFECYCLE_ACTION,
    ):
        for owner in (None, *tuple(item.value for item in AuthorityHold)):
            decision = authority.decision(action_class, owner=owner)
            assert not decision.allowed
            assert "external_development" in decision.reason


def test_gate_survives_transient_screens_but_requires_fresh_running_for_taps():
    authority = RuntimeActionAuthority()
    _set_running_context(authority)
    gate = _activate_gate(authority)

    _set_running_context(authority, state="STORE")

    assert authority.strategy_gate == gate
    assert authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.DAILY_GEM_STORE,
    ).allowed
    assert authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.EVENT_MISSION_REWARDS,
    ).allowed
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.IN_BATTLE_AD_GEM,
    ).allowed
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.FLOATING_GEM_SCAN,
    ).allowed
    assert not authority.decision(RuntimeActionClass.STRATEGY_ACTION).allowed


def test_multi_screen_route_requires_running_source_and_exclusive_lease():
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=False,
        battle_scope="run-1",
        primary_state="RUNNING",
    )
    assert authority.begin_auxiliary_route(
        (AuxiliaryCollector.DAILY_GEM_STORE,),
        battle_scope="run-1",
        source_state="RUNNING",
    ) is None

    _set_running_context(authority)
    assert authority.begin_auxiliary_route(
        (AuxiliaryCollector.DAILY_GEM_STORE,),
        battle_scope="run-1",
        source_state="HOME_SCREEN",
    ) is None

    _activate_gate(authority)
    lease = authority.begin_auxiliary_route(
        (
            AuxiliaryCollector.DAILY_MISSION_REWARDS,
            AuxiliaryCollector.WEEKLY_MISSION_REWARDS,
        ),
        battle_scope="run-1",
        source_state="RUNNING",
    )
    assert lease is not None
    assert not authority.decision(RuntimeActionClass.STRATEGY_ACTION).allowed
    assert not authority.decision(RuntimeActionClass.LIFECYCLE_ACTION).allowed
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.EVENT_MISSION_REWARDS,
    ).allowed
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.DAILY_MISSION_REWARDS,
    ).allowed
    assert authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.DAILY_MISSION_REWARDS,
        route_id=lease.route_id,
    ).allowed
    assert authority.begin_auxiliary_route(
        (AuxiliaryCollector.DAILY_GEM_STORE,),
        battle_scope="run-1",
        source_state="RUNNING",
    ) is None

    _set_running_context(authority, paused=True)
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.DAILY_MISSION_REWARDS,
        route_id=lease.route_id,
    ).allowed
    _set_running_context(authority)
    assert authority.resume_auxiliary_route(lease) is not None

    _set_running_context(authority, scope="run-2")
    # Activity scope is a log/report cursor. It cannot invalidate an exact
    # route lease while the forced save battle identity remains current.
    assert authority.resume_auxiliary_route(lease) is not None
    assert authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.DAILY_MISSION_REWARDS,
        route_id=lease.route_id,
    ).allowed

    _set_running_context(authority, battle_identity="b" * 64)
    assert authority.resume_auxiliary_route(lease) is None
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.DAILY_MISSION_REWARDS,
        route_id=lease.route_id,
    ).allowed


def test_battle_bound_input_waits_for_forced_save_identity():
    authority = RuntimeActionAuthority()
    _set_running_context(authority, battle_identity=None)

    assert authority.decision(RuntimeActionClass.OBSERVATION).allowed
    assert not authority.decision(RuntimeActionClass.STRATEGY_ACTION).allowed
    assert not authority.decision(RuntimeActionClass.LIFECYCLE_ACTION).allowed
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.IN_BATTLE_AD_GEM,
    ).allowed
    assert authority.begin_auxiliary_route(
        (AuxiliaryCollector.DAILY_MISSION_REWARDS,),
        battle_scope="log-scope",
        source_state="RUNNING",
    ) is None


def test_gate_transition_timestamps_change_only_for_real_transitions():
    authority = RuntimeActionAuthority()
    _set_running_context(authority)
    first = _activate_gate(authority)

    unchanged = authority.activate_strategy_gate(
        strategy="farm_t18",
        battle_scope="run-1",
        source="session_preflight",
        phase="running_battle",
        failed_check_ids=("modules", "target_priority"),
        reason="Modules and Target Priority do not match",
        now=1_700_000_100,
    )
    assert unchanged is first

    updated = authority.activate_strategy_gate(
        strategy="farm_t18",
        battle_scope="run-1",
        source="session_preflight",
        phase="running_battle",
        failed_check_ids=("modules",),
        reason="Modules do not match",
        now=1_700_000_200,
    )
    assert updated.gate_id == first.gate_id
    assert updated.activated_at == first.activated_at
    assert updated.updated_at != first.updated_at
    with pytest.raises(ValueError):
        authority.clear_strategy_gate(
            event="arbitrary_caller_request",
            reason="not an authoritative transition",
        )
    assert authority.strategy_gate is updated
    assert authority.clear_strategy_gate(
        event="successful_validation",
        reason="all checks passed",
    )
    assert authority.strategy_gate is None


def test_runtime_owned_structured_snapshot_is_atomic_and_complete(tmp_path):
    authority = RuntimeActionAuthority()
    _set_running_context(authority)
    gate = _activate_gate(authority)
    path = tmp_path / "strategy_action_gate.json"
    publisher = RuntimeActionAuthorityPublisher(
        path,
        owner={
            "runtime_id": "runtime-1",
            "pid": 4321,
            "adb_target": "localhost:5555",
        },
        stale_after_seconds=17,
    )

    assert publisher.publish(
        authority.snapshot(now=1_700_000_001),
        now=1_700_000_002,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["runtime_active"] is True
    assert payload["owner"] == {
        "runtime_id": "runtime-1",
        "pid": 4321,
        "adb_target": "localhost:5555",
    }
    assert payload["active"] is True
    assert payload["gate_id"] == gate.gate_id
    assert payload["strategy"] == "farm_t18"
    assert payload["battle_scope"] == "run-1"
    assert payload["source"] == "session_preflight"
    assert payload["phase"] == "running_battle"
    assert payload["failed_check_ids"] == ["modules", "target_priority"]
    assert payload["observation_authority"]["allowed"] is True
    assert payload["strategy_action_authority"]["allowed"] is False
    assert payload["lifecycle_action_authority"]["allowed"] is False
    assert payload["allowed_auxiliary_collectors"] == [
        collector.value for collector in STRATEGY_GATE_AUXILIARY_ALLOWLIST
    ]


def test_app_auxiliary_guard_has_no_capture_or_status_work_in_tap_hot_path():
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.apply_control.return_value = False
    app._status_reporter = Mock()
    app._authority_battle_active = True
    app._authority_primary_state = "RUNNING"
    app._authority_holds = ()
    app._active_round_identity_fingerprint = ACTIVE_BATTLE_IDENTITY
    app._action_authority = RuntimeActionAuthority()
    _set_running_context(app._action_authority)
    _activate_gate(app._action_authority)
    app._current_run_scope_id = Mock(return_value="run-1")
    app._publish_action_authority = Mock()
    app._capture_frame = Mock()

    guard = app._auxiliary_action_guard(
        AuxiliaryCollector.FLOATING_GEM_SCAN
    )
    assert guard()
    # The scope is published once as metadata; it is not reread as tap
    # authority in the hot path.
    assert app._current_run_scope_id.call_count == 1
    app._supervisor.apply_control.assert_called_once_with()
    app._capture_frame.assert_not_called()
    app._publish_action_authority.assert_not_called()

    app._supervisor.is_paused = True
    assert not guard()
    assert app._current_run_scope_id.call_count == 2
    assert app._supervisor.apply_control.call_count == 2

    app._supervisor.is_paused = False
    app._action_authority.activate_strategy_gate(
        strategy="none",
        battle_scope="run-1",
        source="active_strategy_change",
        phase="running_battle",
        failed_check_ids=(),
        reason="replacement gate",
    )
    assert not guard()
    # Gate replacement fails before any new device-adjacent or status work.
    assert app._current_run_scope_id.call_count == 2
    assert app._supervisor.apply_control.call_count == 2


def test_runtime_guard_merges_new_operator_owner_into_active_route_hold():
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.apply_control.return_value = False
    app._status_reporter = Mock()
    app._authority_battle_active = True
    app._authority_primary_state = "RUNNING"
    app._authority_holds = (
        AuthorityHoldState(
            AuthorityHold.EXCLUSIVE_VALIDATION,
            "validation owns the screen",
        ),
    )
    app._action_authority = RuntimeActionAuthority()
    app._action_authority.update_context(
        global_pause=False,
        active_battle=True,
        battle_scope="run-1",
        primary_state="RUNNING",
        holds=app._authority_holds,
    )
    app._operator_workflow_authority_hold = Mock(
        return_value=AuthorityHoldState(
            AuthorityHold.SETUP_CAPTURE,
            "setup capture was accepted during the route",
        )
    )

    assert not app._runtime_action_guard(
        owner=AuthorityHold.EXCLUSIVE_VALIDATION
    )
    assert [hold.hold for hold in app._authority_holds] == [
        AuthorityHold.EXCLUSIVE_VALIDATION,
        AuthorityHold.SETUP_CAPTURE,
    ]


def test_legacy_gate_is_retired_without_pause_or_lifecycle_input():
    app = _terminal_gate_app()
    original_state = AUTOMATION.state
    with (
        patch("core.app.surrender_run") as surrender,
        patch("core.app.handle_home_screen") as home,
        patch("core.app.handle_game_over") as game_over,
        patch("core.app.return_home_from_game_over") as exit_battle,
    ):
        app._sync_strategy_action_gate(terminally_blocked=True)

    gate = app._get_action_authority().strategy_gate
    assert gate is None
    assert AUTOMATION.state == original_state
    app._supervisor.pause_for_operator_authority.assert_not_called()
    app._supervisor.pause_for_catastrophic_failure.assert_not_called()
    surrender.assert_not_called()
    home.assert_not_called()
    game_over.assert_not_called()
    exit_battle.assert_not_called()


def test_malformed_workflow_authority_uses_catastrophic_pause_policy():
    app = App.__new__(App)
    supervisor = Mock()
    supervisor.manual_control_error = False
    supervisor.battle_workflow_error = True
    supervisor.setup_capture_error = False
    supervisor.is_paused = False
    app._supervisor = supervisor
    app._sync_setup_capture = Mock()

    app._sync_operator_control_workflows({"state": "RUNNING"})

    supervisor.pause_for_catastrophic_failure.assert_called_once_with(
        RuntimeFailureKind.CONTROL_AUTHORITY_LOST,
        reason=(
            "malformed battle-workflow directive made device-input "
            "ownership unknowable"
        ),
    )
    app._sync_setup_capture.assert_not_called()


def test_terminal_boundary_does_not_recreate_a_failed_battle_gate():
    app = _terminal_gate_app()

    app._sync_strategy_action_gate(
        terminally_blocked=True,
        detection={"state": "GAME_OVER"},
    )

    assert app._get_action_authority().strategy_gate is None
    app._supervisor.pause_for_operator_authority.assert_not_called()
    app._supervisor.pause_for_catastrophic_failure.assert_not_called()


def test_gate_decisions_clear_only_retry_and_scoped_waiver_transitions():
    app = _terminal_gate_app()
    manager = app._mission_mgr
    supervisor = app._supervisor

    for action, expected_event in (
        ("retry", "retry_session_preflight"),
        ("waive", "waive_session_preflight_check"),
    ):
        _activate_gate(app._get_action_authority())
        manager.reset_mock()
        supervisor.reset_mock()
        directive = {
            "request_id": f"request-{action}",
            "status": "resolved",
            "check_id": "modules",
            "decision_id": action,
            "reason": "Modules do not match",
            "selected_option": {
                "action": action,
                "label": action,
                "kind": "standard",
                "value": "",
            },
        }

        assert app._apply_gate_decision(
            directive,
            phase="session_preflight",
        )
        assert app._get_action_authority().strategy_gate is None
        assert getattr(manager, expected_event).call_count == 1
        supervisor.consume_gate_decision.assert_called_once()


@pytest.mark.parametrize("action", ["pause", "repair_restart"])
def test_legacy_failure_choices_are_retired_without_input(action):
    app = _terminal_gate_app()
    manager = app._mission_mgr
    supervisor = app._supervisor
    _activate_gate(app._get_action_authority())
    directive = {
        "request_id": f"legacy-{action}",
        "status": "resolved",
        "check_id": "modules",
        "decision_id": action,
        "reason": "Modules do not match",
        "selected_option": {
            "action": action,
            "label": action,
            "kind": "standard",
            "value": "",
        },
    }

    assert app._apply_gate_decision(directive, phase="session_preflight")

    assert app._get_action_authority().strategy_gate is None
    supervisor.pause_for_operator_authority.assert_not_called()
    manager.authorize_session_preflight_restart.assert_not_called()
    supervisor.consume_gate_decision.assert_called_once_with(
        f"legacy-{action}",
        completion_reason=(
            f"retired legacy {action} decision for modules; "
            "automation continues degraded"
        ),
    )


def test_success_strategy_change_and_natural_boundary_end_the_scoped_gate():
    app = _terminal_gate_app()
    authority = app._get_action_authority()

    _activate_gate(authority)
    app._mission_mgr.strategy.is_session_preflight_complete.return_value = True
    app._sync_strategy_action_gate(terminally_blocked=False)
    assert authority.strategy_gate is None

    _activate_gate(authority)
    app._complete_strategy_application("none")
    assert authority.strategy_gate is None

    _activate_gate(authority)
    app._observe_strategy_gate_boundary({"state": "STORE"})
    assert authority.strategy_gate is not None
    app._observe_strategy_gate_boundary(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "RESUME_BATTLE",
        }
    )
    assert authority.strategy_gate is not None
    app._observe_strategy_gate_boundary({"state": "GAME_OVER"})
    assert authority.strategy_gate is None

    _activate_gate(authority)
    app._current_run_scope_id.return_value = "run-2"
    app._observe_strategy_gate_boundary({"state": "STORE"})
    assert authority.strategy_gate is not None
    app._observe_strategy_gate_boundary(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        }
    )
    assert authority.strategy_gate is None


@pytest.mark.parametrize("status", ["pending", "resolved"])
def test_success_retires_same_strategy_session_preflight_decision(status):
    app = _terminal_gate_app()
    authority = app._get_action_authority()
    _activate_gate(authority)
    app._mission_mgr.strategy.is_session_preflight_complete.return_value = True
    app._mission_mgr.ctx.data["mission_vars"].update(
        gc_session_preflight_completed=True,
        gc_session_preflight_last_status="complete",
    )
    app._supervisor.gate_decision = {
        "request_id": f"session-{status}",
        "status": status,
        "strategy": "farm_t18",
        "phase": "session_preflight",
        "check_id": "modules",
    }

    app._sync_strategy_action_gate(terminally_blocked=False)

    assert authority.strategy_gate is None
    app._supervisor.consume_gate_decision.assert_called_once_with(
        f"session-{status}",
        completion_reason=(
            "session preflight subsequently completed successfully"
        ),
    )


def test_degraded_completion_preserves_session_preflight_advisory():
    app = _terminal_gate_app()
    app._mission_mgr.strategy.is_session_preflight_complete.return_value = True
    app._mission_mgr.session_preflight_degraded.return_value = True
    app._mission_mgr.ctx.data["mission_vars"].update(
        gc_session_preflight_completed=True,
        gc_session_preflight_last_status="complete",
        gc_session_preflight_degraded=True,
    )
    app._supervisor.gate_decision = {
        "request_id": "degraded-session",
        "status": "pending",
        "strategy": "farm_t18",
        "phase": "session_preflight",
        "check_id": "free_upgrade_locks",
        "blocking": False,
    }

    app._sync_strategy_action_gate(terminally_blocked=False)

    app._supervisor.consume_gate_decision.assert_not_called()


def test_recovered_completion_retires_advisory_and_records_recovery():
    app = _terminal_gate_app()
    app._mission_mgr.strategy.is_session_preflight_complete.return_value = True
    app._mission_mgr.session_preflight_degraded.return_value = False
    app._mission_mgr.ctx.data["mission_vars"].update(
        gc_session_preflight_completed=True,
        gc_session_preflight_last_status="complete",
        gc_session_preflight_degraded=False,
    )
    app._supervisor.gate_decision = {
        "request_id": "recovered-session",
        "status": "pending",
        "strategy": "farm_t18",
        "phase": "session_preflight",
        "check_id": "free_upgrade_locks",
        "blocking": False,
    }

    with patch("core.app.log") as runtime_log:
        app._sync_strategy_action_gate(terminally_blocked=False)

    app._supervisor.consume_gate_decision.assert_called_once_with(
        "recovered-session",
        completion_reason=(
            "session preflight subsequently completed successfully"
        ),
    )
    runtime_log.assert_called_once_with(
        "[RUNTIME_ADVISORY] Session configuration recovered; "
        "the persistent advisory is cleared",
        "INFO",
        console=True,
    )


@pytest.mark.parametrize(
    ("strategy", "phase"),
    [("tournament", "session_preflight"), ("farm_t18", "home_setup")],
)
def test_success_preserves_unrelated_gate_decisions(strategy, phase):
    app = _terminal_gate_app()
    app._mission_mgr.strategy.is_session_preflight_complete.return_value = True
    app._mission_mgr.ctx.data["mission_vars"].update(
        gc_session_preflight_completed=True,
        gc_session_preflight_last_status="complete",
    )
    app._supervisor.gate_decision = {
        "request_id": "unrelated-gate",
        "status": "pending",
        "strategy": strategy,
        "phase": phase,
        "check_id": "modules",
    }

    app._sync_strategy_action_gate(terminally_blocked=False)

    app._supervisor.consume_gate_decision.assert_not_called()


def test_strategy_gate_does_not_make_reward_collectors_due():
    app = _terminal_gate_app()
    app._handler_enabled = Mock(return_value=True)
    app._daily_gem_scheduler = Mock()
    app._daily_gem_scheduler.should_attempt.return_value = False
    app._mission_reward_scheduler = Mock()
    app._mission_reward_scheduler.should_attempt.return_value = False
    app._event_mission_tracker = Mock()
    app._authority_battle_active = True
    app._authority_primary_state = "RUNNING"
    app._authority_holds = ()
    app._get_action_authority().update_context(
        global_pause=False,
        active_battle=True,
        battle_scope="run-1",
        primary_state="RUNNING",
    )
    _activate_gate(app._get_action_authority())

    with (
        patch("core.app.handle_daily_gem") as daily,
        patch("core.app.handle_mission_rewards") as missions,
        patch("core.app.handle_ad_gem") as ad_gem,
    ):
        app._handle_strategy_gate_auxiliary_actions(
            "RUNNING",
            set(),
            object(),
        )

    app._daily_gem_scheduler.should_attempt.assert_called_once()
    app._mission_reward_scheduler.should_attempt.assert_called_once_with(
        alert_visible=False
    )
    daily.assert_not_called()
    missions.assert_not_called()
    ad_gem.assert_not_called()


def test_action_executor_rechecks_central_authority_before_material_input():
    guard = Mock(return_value=False)
    context = SimpleNamespace(
        data={"mission_vars": {"last_detection_state": "RUNNING"}}
    )
    with patch("core.action_executor.tap_if_visible") as tap:
        execute_actions(
            object(),
            [
                {
                    "type": "tap_label",
                    "key": "buttons.example",
                    "_strategy": True,
                }
            ],
            context,
            action_guard_fn=guard,
        )

    guard.assert_called_once_with()
    tap.assert_not_called()


def test_action_executor_scopes_guard_through_final_nested_mutation():
    guard = Mock(side_effect=(True, False))
    final_decisions = []
    context = SimpleNamespace(
        data={"mission_vars": {"last_detection_state": "RUNNING"}}
    )

    def nested_tap(_key):
        with AUTOMATION.authorize_mutation() as allowed:
            final_decisions.append(allowed)
        return bool(final_decisions[-1])

    with patch("core.action_executor.tap_if_visible", side_effect=nested_tap):
        execute_actions(
            object(),
            [
                {
                    "type": "tap_label",
                    "key": "buttons.example",
                    "_strategy": True,
                }
            ],
            context,
            action_guard_fn=guard,
        )

    assert final_decisions == [False]
    assert guard.call_count == 2


def test_card_exit_resume_transports_guard_to_async_blind_tapper():
    guard = Mock(return_value=True)
    context = SimpleNamespace(
        data={
            "mission_vars": {
                "last_detection_state": "RUNNING",
                "blind_tapper_paused_for_cards": True,
            }
        }
    )

    with (
        patch("core.action_executor.tap_if_visible", return_value=True),
        patch("core.action_executor.start_blind_gem_tapper") as start,
    ):
        execute_actions(
            object(),
            [
                {
                    "type": "tap_label",
                    "key": "buttons.return_to_game",
                    "_strategy": True,
                }
            ],
            context,
            action_guard_fn=guard,
        )

    start.assert_called_once_with(
        duration=10,
        interval=1,
        blocking=False,
        action_guard_fn=guard,
    )


def test_scoped_action_guard_does_not_hold_or_leak_mutation_authority():
    guard = Mock(return_value=False)

    with AUTOMATION.action_guard_scope(guard):
        with AUTOMATION.authorize_mutation() as allowed:
            assert not allowed

    with AUTOMATION.authorize_mutation() as allowed:
        assert allowed
    guard.assert_called_once_with()


def test_verified_tap_rechecks_authority_after_match_before_dispatch():
    events = []

    def matched(*_args, **_kwargs):
        events.append("match")
        return (10, 20, 30, 40)

    def denied():
        events.append("authority")
        return False

    with (
        patch(
            "core.input.resolve_dot_path",
            return_value={"match_template": "example.png"},
        ),
        patch("core.input.get_label_match", side_effect=matched),
        patch("core.input._dispatch_tap") as dispatch,
        patch("core.input.log_input") as input_log,
    ):
        assert not safe_tap(
            "buttons.example",
            action_guard_fn=denied,
        )

    assert events == ["match", "authority"]
    input_log.assert_not_called()
    dispatch.assert_not_called()
