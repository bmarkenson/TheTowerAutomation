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
from core.input import safe_tap
from core.run_state import AUTOMATION


def _set_running_context(
    authority: RuntimeActionAuthority,
    *,
    paused: bool = False,
    holds: tuple[AuthorityHoldState, ...] = (),
    state: str = "RUNNING",
    scope: str = "run-1",
    stopped: bool = False,
) -> None:
    authority.update_context(
        global_pause=paused,
        active_battle=True,
        battle_scope=scope,
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
    app._supervisor.persist_state.return_value = True
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
    for collector in STRATEGY_GATE_AUXILIARY_ALLOWLIST:
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
    for collector in STRATEGY_GATE_AUXILIARY_ALLOWLIST:
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
    assert authority.resume_auxiliary_route(lease) is None
    assert not authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.DAILY_MISSION_REWARDS,
        route_id=lease.route_id,
    ).allowed


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
    app._action_authority = RuntimeActionAuthority()
    app._action_authority.update_context(
        global_pause=False,
        active_battle=True,
        battle_scope="run-1",
        primary_state="RUNNING",
    )
    _activate_gate(app._action_authority)
    app._current_run_scope_id = Mock(return_value="run-1")
    app._publish_action_authority = Mock()
    app._capture_frame = Mock()

    guard = app._auxiliary_action_guard(
        AuxiliaryCollector.FLOATING_GEM_SCAN
    )
    assert guard()
    # One read binds the worker and one refreshes the identity before its tap;
    # _runtime_action_guard reuses that refresh rather than parsing it twice.
    assert app._current_run_scope_id.call_count == 2
    app._supervisor.apply_control.assert_called_once_with()
    app._capture_frame.assert_not_called()
    app._publish_action_authority.assert_not_called()

    app._supervisor.is_paused = True
    assert not guard()
    assert app._current_run_scope_id.call_count == 3
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
    assert app._current_run_scope_id.call_count == 3
    assert app._supervisor.apply_control.call_count == 2


def test_gate_activation_never_mutates_pause_or_dispatches_lifecycle_input():
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
    assert gate is not None
    assert gate.strategy == "farm_t18"
    assert gate.battle_scope == "run-1"
    assert gate.failed_check_ids == ("modules",)
    assert AUTOMATION.state == original_state
    app._supervisor.persist_state.assert_not_called()
    surrender.assert_not_called()
    home.assert_not_called()
    game_over.assert_not_called()
    exit_battle.assert_not_called()


def test_gate_decisions_clear_only_the_authorized_non_pause_transitions():
    app = _terminal_gate_app()
    manager = app._mission_mgr
    supervisor = app._supervisor

    for action, expected_event in (
        ("retry", "retry_session_preflight"),
        ("waive", "waive_session_preflight_check"),
        ("repair_restart", "authorize_session_preflight_restart"),
    ):
        _activate_gate(app._get_action_authority())
        manager.reset_mock()
        supervisor.reset_mock()
        supervisor.persist_state.return_value = True
        manager.authorize_session_preflight_restart.return_value = True
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
        if action == "repair_restart":
            directive["repair_authority"] = (
                app._current_control_workflow_evidence()
            )

        assert app._apply_gate_decision(
            directive,
            phase="session_preflight",
        )
        assert app._get_action_authority().strategy_gate is None
        assert getattr(manager, expected_event).call_count == 1
        supervisor.consume_gate_decision.assert_called_once()

    _activate_gate(app._get_action_authority())
    pause = {
        "request_id": "request-pause",
        "status": "resolved",
        "check_id": "modules",
        "decision_id": "pause_for_changes",
        "reason": "Modules do not match",
        "selected_option": {
            "action": "pause",
            "label": "Pause",
            "kind": "standard",
            "value": "",
        },
    }
    assert app._apply_gate_decision(pause, phase="session_preflight")
    assert app._get_action_authority().strategy_gate is not None
    supervisor.persist_state.assert_called_once_with("PAUSED")


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
    assert authority.strategy_gate is None


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
