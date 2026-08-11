from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from automation.missions.manager import MissionManager
from automation.strategies import get_strategy
from core.automation_supervisor import AutomationSupervisor
from core.battle_lifecycle import HomeBattleControl
from core.control_directives import ControlDirectiveStore
from core.exclusive_validation import (
    exclusive_validation_definition_for_strategy,
)
from core.app import App
from core.run_state import AUTOMATION
from core.runtime_failure_policy import RuntimeFailureKind
from handlers.tournament_launch_handler import TournamentLaunchDispatch


OWNER_ONE = {
    "runtime_id": "runtime-one",
    "pid": 101,
    "adb_target": "localhost:5555",
}
OWNER_TWO = {
    "runtime_id": "runtime-two",
    "pid": 202,
    "adb_target": "localhost:5555",
}


@pytest.fixture(autouse=True)
def restore_automation_state():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    try:
        yield
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode


def _current_receipt(store: ControlDirectiveStore):
    ledger = store.status()["exclusive_validation"]
    return ledger["receipts"][ledger["current_request_id"]]


def _app_for_pending_validation(tmp_path, *, home_preflight_complete=True):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_strategy("tournament", source="test")
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(control_file))
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(None, strategy)
    manager.start()
    manager.prepare_exclusive_validation_request(
        _current_receipt(store)["request_id"]
    )
    if home_preflight_complete:
        manager.mark_no_battle_setup_complete({})

    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._daily_gem_scheduler = Mock()
    app._mission_reward_scheduler = Mock()
    app._mission_reward_scheduler.should_attempt.return_value = False
    app._active_exclusive_validation_request_id = None
    app._exclusive_validation_terminal_hold = None
    return app, store, manager


def _ready_validation(store: ControlDirectiveStore):
    saved = store.set_strategy("tournament", source="test")
    definition = exclusive_validation_definition_for_strategy("tournament")
    assert definition is not None
    claimed = store.claim_exclusive_validation(
        strategy_request_id=saved["strategy_request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=OWNER_ONE,
        timeout_seconds=definition.timeout_seconds,
    )
    assert claimed is not None
    running = store.mark_exclusive_validation_running(
        claimed["request_id"],
        owner=OWNER_ONE,
    )
    assert running is not None
    cleanup = store.begin_exclusive_validation_cleanup(
        running["request_id"],
        owner=OWNER_ONE,
        outcome="ready",
        reason="checks passed",
    )
    assert cleanup is not None
    ready = store.finish_exclusive_validation(
        cleanup["request_id"],
        owner=OWNER_ONE,
        outcome="ready",
        reason="checks passed",
    )
    assert ready is not None
    return ready, definition


def _app_for_ready_launch(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    ready, definition = _ready_validation(store)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(control_file))
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(None, strategy)
    manager.start()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._active_exclusive_validation_request_id = None
    app._active_exclusive_validation_launch_request_id = None
    app._exclusive_validation_terminal_hold = None
    app._exclusive_validation_ownership_hold = False
    return app, store, manager, ready, definition


def test_explicit_tournament_requests_get_distinct_durable_receipts(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    first = store.set_strategy("tournament", source="test")
    first_receipt = _current_receipt(store)

    second = store.set_strategy("tournament", source="test")
    second_receipt = _current_receipt(store)

    assert first["strategy_request_id"] != second["strategy_request_id"]
    assert first_receipt["request_id"] != second_receipt["request_id"]
    assert first_receipt["strategy_request_id"] == first["strategy_request_id"]
    assert second_receipt["strategy_request_id"] == second["strategy_request_id"]
    assert first_receipt["configuration_fingerprint"] == (
        second_receipt["configuration_fingerprint"]
    )
    ledger = store.status()["exclusive_validation"]
    assert ledger["receipts"][first_receipt["request_id"]]["outcome"] == "cancelled"
    assert second_receipt["status"] == "pending"


def test_running_tournament_cancels_pending_pre_tournament_validation(tmp_path):
    app, store, _manager = _app_for_pending_validation(tmp_path)
    result_log = Mock()

    with patch("core.app.log_result", new=result_log):
        cancelled = app._cancel_pending_tournament_validation_after_boundary(
            {"state": "RUNNING", "secondary_states": ["TOURNAMENT"]}
        )

    assert cancelled
    receipt = _current_receipt(store)
    assert receipt["status"] == "result"
    assert receipt["outcome"] == "cancelled"
    assert "already running" in receipt["reason"]
    assert "only before a Tournament begins" in receipt["reason"]
    result_log.assert_called_once()
    assert result_log.call_args.args[0].startswith(
        "No Tournament validation is planned"
    )
    assert not app._maybe_start_exclusive_validation(
        home_control=HomeBattleControl.NEW_BATTLE
    )


def test_tournament_results_cancel_pending_validation_as_a_failsafe(tmp_path):
    app, store, _manager = _app_for_pending_validation(tmp_path)

    with patch("core.app.log_result"):
        cancelled = app._cancel_pending_tournament_validation_after_boundary(
            {"state": "TOURNAMENT_RESULTS", "secondary_states": []}
        )

    assert cancelled
    receipt = _current_receipt(store)
    assert receipt["status"] == "result"
    assert receipt["outcome"] == "cancelled"
    assert "already completed" in receipt["reason"]


def test_non_tournament_battle_keeps_pre_tournament_validation_pending(tmp_path):
    app, store, _manager = _app_for_pending_validation(tmp_path)

    assert not app._cancel_pending_tournament_validation_after_boundary(
        {"state": "RUNNING", "secondary_states": []}
    )
    assert _current_receipt(store)["status"] == "pending"


def test_ready_receipt_offers_one_durable_start_or_cancel_decision(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    ready, definition = _ready_validation(store)

    assert ready["launch_policy"] == {
        "kind": "tournament_battle",
        "timeout_seconds": 60.0,
        "prompt_title": "Tournament validation passed",
        "prompt_message": (
            "Start the Tournament now? Automation will verify the current Home "
            "or Tournament entry screen and start exactly one Tournament battle."
        ),
        "reminder": (
            "When the Tournament battle begins, set Target Priorities for the "
            "current Tournament Battle Conditions."
        ),
    }
    assert ready["launch"]["status"] == "awaiting_operator"

    requested = store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    assert requested is not None
    assert requested["launch"]["status"] == "requested"
    assert requested["launch"]["launch_request_id"]
    assert (
        store.resolve_exclusive_validation_launch(
            ready["request_id"],
            "cancel",
            source="test",
        )
        is None
    )

    claimed = store.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=OWNER_ONE,
    )
    assert claimed is not None
    assert claimed["launch"]["status"] == "claimed"
    assert claimed["launch"]["owner"] == OWNER_ONE
    assert (
        store.finish_exclusive_validation_launch(
            ready["request_id"],
            owner=OWNER_TWO,
            outcome="started",
            reason="wrong owner",
        )
        is None
    )
    started = store.finish_exclusive_validation_launch(
        ready["request_id"],
        owner=OWNER_ONE,
        outcome="started",
        reason="Tournament RUNNING verified",
    )
    assert started is not None
    assert started["launch"]["status"] == "started"


def test_cancel_consumes_launch_without_changing_validation_result(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    ready, definition = _ready_validation(store)

    cancelled = store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "cancel",
        source="test",
    )

    assert cancelled is not None
    assert cancelled["outcome"] == "ready"
    assert cancelled["launch"]["status"] == "cancelled"
    assert (
        store.claim_exclusive_validation_launch(
            ready["request_id"],
            configuration_fingerprint=definition.configuration_fingerprint,
            owner=OWNER_ONE,
        )
        is None
    )


def test_new_strategy_request_cancels_unclaimed_launch(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    ready, _definition = _ready_validation(store)

    store.set_strategy("tournament", source="test")

    old = store.status()["exclusive_validation"]["receipts"][
        ready["request_id"]
    ]
    assert old["outcome"] == "ready"
    assert old["launch"]["status"] == "cancelled"
    assert "superseded" in old["launch"]["reason"]
    assert _current_receipt(store)["status"] == "pending"


def test_restart_fails_claimed_launch_without_replay(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    ready, definition = _ready_validation(store)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    claimed = store.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=OWNER_ONE,
    )
    assert claimed is not None

    failed = store.fail_orphaned_exclusive_validation_launch(
        ready["request_id"],
        current_owner=OWNER_TWO,
        reason="prior launch owner is unavailable",
    )

    assert failed is not None
    assert failed["launch"]["status"] == "failed"
    assert failed["launch"]["owner"] == OWNER_ONE


def test_launch_action_guard_stops_after_strategy_request_is_superseded(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    ready, definition = _ready_validation(store)
    store.set_state("RUNNING", source="test")
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    supervisor = AutomationSupervisor(control_file=str(control_file))
    claimed = supervisor.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
    )
    assert claimed is not None
    assert supervisor.exclusive_validation_launch_action_allowed(
        ready["request_id"]
    )

    store.set_strategy("tournament", source="test")

    assert supervisor.owns_exclusive_validation_launch(ready["request_id"])
    assert not supervisor.exclusive_validation_launch_action_allowed(
        ready["request_id"]
    )


def test_claim_is_atomic_and_only_same_owner_can_advance_or_finish(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    saved = store.set_strategy("tournament", source="test")
    definition = exclusive_validation_definition_for_strategy("tournament")
    assert definition is not None

    claimed = store.claim_exclusive_validation(
        strategy_request_id=saved["strategy_request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=OWNER_ONE,
        timeout_seconds=definition.timeout_seconds,
    )
    assert claimed is not None
    assert claimed["status"] == "claimed"
    assert claimed["owner"] == OWNER_ONE
    assert (
        store.claim_exclusive_validation(
            strategy_request_id=saved["strategy_request_id"],
            configuration_fingerprint=definition.configuration_fingerprint,
            owner=OWNER_TWO,
            timeout_seconds=definition.timeout_seconds,
        )
        is None
    )
    assert (
        store.mark_exclusive_validation_running(
            claimed["request_id"],
            owner=OWNER_TWO,
        )
        is None
    )

    running = store.mark_exclusive_validation_running(
        claimed["request_id"],
        owner=OWNER_ONE,
    )
    assert running is not None
    assert (
        store.begin_exclusive_validation_cleanup(
            running["request_id"],
            owner=OWNER_TWO,
            outcome="ready",
            reason="not the owner",
        )
        is None
    )
    cleanup = store.begin_exclusive_validation_cleanup(
        running["request_id"],
        owner=OWNER_ONE,
        outcome="ready",
        reason="checks passed",
    )
    assert cleanup is not None
    assert (
        store.finish_exclusive_validation(
            cleanup["request_id"],
            outcome="ready",
            reason="wrong owner",
            owner=OWNER_TWO,
        )
        is None
    )


def test_restart_fails_claimed_receipt_closed_without_reclaim(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    saved = store.set_strategy("tournament", source="test")
    definition = exclusive_validation_definition_for_strategy("tournament")
    assert definition is not None
    claimed = store.claim_exclusive_validation(
        strategy_request_id=saved["strategy_request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=OWNER_ONE,
        timeout_seconds=definition.timeout_seconds,
    )
    assert claimed is not None

    restarted_store = ControlDirectiveStore(store.path)
    assert (
        restarted_store.claim_exclusive_validation(
            strategy_request_id=saved["strategy_request_id"],
            configuration_fingerprint=definition.configuration_fingerprint,
            owner=OWNER_TWO,
            timeout_seconds=definition.timeout_seconds,
        )
        is None
    )
    failed = restarted_store.fail_orphaned_exclusive_validation(
        claimed["request_id"],
        current_owner=OWNER_TWO,
        reason="prior runtime ownership is unavailable",
    )

    assert failed is not None
    assert failed["status"] == "result"
    assert failed["outcome"] == "failed"
    assert failed["owner"] == OWNER_ONE


def test_restart_retires_old_owner_without_holding_new_pending_request(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    first = store.set_strategy("tournament", source="test")
    definition = exclusive_validation_definition_for_strategy("tournament")
    assert definition is not None
    claimed = store.claim_exclusive_validation(
        strategy_request_id=first["strategy_request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=OWNER_ONE,
        timeout_seconds=definition.timeout_seconds,
    )
    assert claimed is not None
    second = store.set_strategy("tournament", source="test")

    app = App.__new__(App)
    app._supervisor = AutomationSupervisor(control_file=str(control_file))
    app._mission_mgr = MissionManager(None, get_strategy("tournament"))
    app._mission_mgr.start()
    app._active_exclusive_validation_request_id = None
    app._exclusive_validation_ownership_hold = False
    with patch("core.app.log"):
        receipt = app._reconcile_exclusive_validation()

    assert receipt is not None
    assert receipt["status"] == "pending"
    assert receipt["strategy_request_id"] == second["strategy_request_id"]
    assert not app._exclusive_validation_ownership_hold
    old = store.status()["exclusive_validation"]["receipts"][claimed["request_id"]]
    assert old["outcome"] == "failed"


def test_new_explicit_request_stays_current_while_owned_run_finishes(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    first = store.set_strategy("tournament", source="test")
    definition = exclusive_validation_definition_for_strategy("tournament")
    assert definition is not None
    claimed = store.claim_exclusive_validation(
        strategy_request_id=first["strategy_request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=OWNER_ONE,
        timeout_seconds=definition.timeout_seconds,
    )
    assert claimed is not None
    running = store.mark_exclusive_validation_running(
        claimed["request_id"],
        owner=OWNER_ONE,
    )
    assert running is not None

    second = store.set_strategy("tournament", source="test")
    second_receipt = _current_receipt(store)
    assert second_receipt["strategy_request_id"] == second["strategy_request_id"]
    cleanup = store.begin_exclusive_validation_cleanup(
        running["request_id"],
        owner=OWNER_ONE,
        outcome="ready",
        reason="checks passed",
    )
    assert cleanup is not None
    result = store.finish_exclusive_validation(
        cleanup["request_id"],
        owner=OWNER_ONE,
        outcome="ready",
        reason="checks passed",
    )
    assert result is not None

    assert _current_receipt(store)["request_id"] == second_receipt["request_id"]


def test_each_validation_request_rearms_home_preflight_once():
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(None, strategy)
    manager.start()

    assert manager.prepare_exclusive_validation_request("request-one")
    manager.mark_no_battle_setup_complete({})
    assert manager.no_battle_setup_requirements() == {}
    assert not manager.prepare_exclusive_validation_request("request-one")
    assert manager.no_battle_setup_requirements() == {}

    assert manager.prepare_exclusive_validation_request("request-two")
    assert manager.no_battle_setup_requirements()


def test_explicit_start_owns_tournament_validation_launch_through_adoption(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_strategy("tournament", source="test")
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(control_file))
    supervisor.apply_control()
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(
        None,
        strategy,
        await_initial_battle_intent=True,
    )
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    observed_at = datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )
    evidence = {
        "schema_version": 1,
        "runtime_id": owner["runtime_id"],
        "pid": owner["pid"],
        "adb_target": owner["adb_target"],
        "observation_id": "tournament-workflow:home",
        "observed_at": observed_at,
        "primary_state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
        "game_state": "home_new_battle",
        "active_battle": False,
        "activity_scope_run_id": "tournament-preflight",
        "target_generation": 1,
    }
    store.request_battle_workflow("start_battle", evidence=evidence)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._active_exclusive_validation_request_id = None
    app._exclusive_validation_terminal_hold = None
    app._startup_gate_waivers = {}
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "acknowledged"
    assert manager.maybe_run_start(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        }
    ) is False
    definition = app._exclusive_validation_definition()
    assert definition is not None
    assert app._prepare_exclusive_validation_home_request(definition) is True
    manager.mark_no_battle_setup_complete({})

    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        ) is True

    assert supervisor.battle_workflow["status"] == "action_dispatched"
    app._control_observation = {
        **app._control_observation,
        "observation_id": "tournament-workflow:running",
        "primary_state": "RUNNING",
        "home_battle_control": "UNKNOWN",
        "game_state": "active_battle",
        "active_battle": True,
    }
    app._sync_operator_control_workflows({"state": "RUNNING"})
    battle_started = manager.maybe_run_start({"state": "RUNNING"})
    assert battle_started is True
    assert app._complete_started_battle_workflow(battle_started) is True
    assert supervisor.battle_workflow["status"] == "completed"


def test_validation_lifecycle_taps_one_new_battle_surrenders_and_returns_home(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    action_log = Mock()
    result_log = Mock()

    with (
        patch("core.app.tap_verified_new_battle", return_value=True) as start,
        patch("core.app.log"),
        patch("core.app.log_action_intent", new=action_log),
        patch("core.app.log_result", new=result_log),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
        assert not app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    start.assert_called_once_with()
    claimed = _current_receipt(store)
    assert claimed["status"] == "claimed"

    detection = {"state": "RUNNING", "secondary_states": []}
    battle_started = manager.maybe_run_start(detection)
    assert battle_started
    with patch("core.app.log"):
        app._observe_exclusive_validation_battle_start(
            detection,
            battle_started=battle_started,
        )
    assert manager.ctx.data["exclusive_validation_battle"] is True
    assert not manager.run_initialization_pending()
    running = _current_receipt(store)
    assert running["status"] == "running"

    mission_vars = manager.ctx.data["mission_vars"]
    mission_vars.update(
        damage_slider_checked=True,
        gc_session_preflight_attempted=True,
        gc_session_preflight_completed=True,
    )
    with (
        patch("core.app.surrender_run", return_value=True) as surrender,
        patch("core.app.log"),
        patch("core.app.log_action_intent", new=action_log),
        patch("core.app.log_result", new=result_log),
    ):
        assert app._advance_exclusive_validation(detection)
    assert surrender.call_count == 1
    assert surrender.call_args.kwargs["timeout_s"] == 12.0
    assert _current_receipt(store)["status"] == "cleanup"

    with (
        patch("core.app.return_home_from_game_over", return_value=True) as go_home,
        patch("core.app.log"),
        patch("core.app.log_result", new=result_log),
    ):
        assert app._handle_exclusive_validation_game_over()
    go_home.assert_called_once()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "ready"
    assert "Spotlight Missiles" in result["reason"]
    action_log.assert_called_once()
    assert action_log.call_args.args[0] == (
        "Starting the one-shot Tournament validation battle"
    )
    result_log.assert_called_once()
    assert result_log.call_args.args[0].startswith(
        "Tournament validation complete — "
    )


def test_home_preflight_failure_consumes_request_without_waiver_or_battle(
    tmp_path,
):
    app, store, _manager = _app_for_pending_validation(
        tmp_path,
        home_preflight_complete=False,
    )
    app._auto_start_enabled = False
    app._startup_gate_waivers = {}
    setup = type(
        "SetupResult",
        (),
        {
            "complete": False,
            "interrupted": False,
            "failed_check": "guardian_chips",
            "reason": "Attack chip could not be equipped",
        },
    )()
    frame = object()

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=type(
                "HomeEvidence",
                (),
                {"control": HomeBattleControl.NEW_BATTLE},
            )(),
        ),
        patch("core.app.run_gc_no_battle_setup", return_value=setup) as run_setup,
        patch.object(app, "_publish_gate_decision") as decision,
        patch("core.app.tap_verified_new_battle") as start,
        patch("core.app.log"),
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    run_setup.assert_called_once()
    assert run_setup.call_args.kwargs == {
        "screenshot": frame,
        "action_guard_fn": app._runtime_action_guard,
    }
    decision.assert_not_called()
    start.assert_not_called()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "guardian_chips" in result["reason"]


def test_exclusive_validation_observes_modules_without_a_waiver(tmp_path):
    app, store, manager = _app_for_pending_validation(
        tmp_path,
        home_preflight_complete=False,
    )
    app._auto_start_enabled = False
    app._startup_gate_waivers = {}
    setup = type(
        "SetupResult",
        (),
        {
            "complete": True,
            "interrupted": False,
            "failed_check": None,
            "reason": "ok",
            "evidence": {
                "modules": {"mode": "observe", "checked": True}
            },
        },
    )()
    frame = object()

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=type(
                "HomeEvidence",
                (),
                {"control": HomeBattleControl.NEW_BATTLE},
            )(),
        ),
        patch("core.app.run_gc_no_battle_setup", return_value=setup) as run_setup,
        patch.object(
            manager,
            "mark_no_battle_setup_complete",
            wraps=manager.mark_no_battle_setup_complete,
        ) as mark_complete,
        patch.object(
            app,
            "_maybe_start_exclusive_validation",
            return_value=True,
        ),
        patch("core.app.log"),
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    assert "waivers" not in run_setup.call_args.kwargs
    assert run_setup.call_args.kwargs["screenshot"] is frame
    mark_complete.assert_called_once_with(setup.evidence)
    assert store.status()["startup_gate_waivers"] == {}


def test_failed_validation_home_navigation_retries_without_pausing():
    receipt = {
        "request_id": "validation-1",
        "status": "cleanup",
        "pending_outcome": "ready",
        "pending_reason": "validation checks completed",
    }
    result = {
        **receipt,
        "status": "result",
        "outcome": "failed",
    }
    app = App.__new__(App)
    app._exclusive_validation_terminal_hold = None
    app._supervisor = Mock()
    app._supervisor.is_paused = False
    app._supervisor.owns_exclusive_validation.return_value = True
    app._supervisor.finish_exclusive_validation.return_value = result
    app._reconcile_exclusive_validation = Mock(return_value=receipt)
    app._announce_exclusive_validation_result = Mock()
    app._flag_recoverable_runtime_failure = Mock()

    reason = (
        "validation checks completed; the owned battle ended, but verified "
        "NEW_BATTLE Home was not reached"
    )
    with patch("core.app.return_home_from_game_over", return_value=False):
        assert app._handle_exclusive_validation_game_over() is True

    app._flag_recoverable_runtime_failure.assert_called_once_with(
        RuntimeFailureKind.VALIDATION_UNAVAILABLE,
        reason,
    )
    app._supervisor.pause_for_catastrophic_failure.assert_not_called()
    assert app._exclusive_validation_terminal_hold is None
    app._supervisor.finish_exclusive_validation.assert_not_called()


def test_claimed_validation_timeout_fails_without_surrender(tmp_path):
    app, store, _manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    claimed = _current_receipt(store)

    with (
        patch("core.app.time.time", return_value=claimed["deadline_at"] + 1),
        patch("core.app.surrender_run") as surrender,
        patch("core.app.log"),
    ):
        app._observe_exclusive_validation_battle_start(
            {"state": "HOME_SCREEN", "secondary_states": []},
            battle_started=False,
        )

    surrender.assert_not_called()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "did not reach fresh RUNNING" in result["reason"]


def test_tournament_identity_never_authorizes_validation_surrender(tmp_path):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    detection = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        detection,
        battle_started=manager.maybe_run_start(detection),
    )

    tournament_detection = {
        "state": "RUNNING",
        "secondary_states": ["TOURNAMENT"],
    }
    with (
        patch("core.app.surrender_run") as surrender,
        patch("core.app.log"),
    ):
        assert not app._advance_exclusive_validation(tournament_detection)
    surrender.assert_not_called()
    result = _current_receipt(store)
    assert result["outcome"] == "failed"
    assert "refusing Surrender" in result["reason"]


def test_active_strategy_change_cleans_up_only_the_owned_validation_battle(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    detection = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        detection,
        battle_started=manager.maybe_run_start(detection),
    )
    manager.adopt_strategy_for_active_battle(get_strategy("farm_t18"))

    with (
        patch("core.app.surrender_run", return_value=True) as surrender,
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._advance_exclusive_validation(detection)

    surrender.assert_called_once()
    cleanup = _current_receipt(store)
    assert cleanup["status"] == "cleanup"
    assert cleanup["pending_outcome"] == "failed"
    assert "active strategy changed" in cleanup["pending_reason"]


def test_manual_tournament_runs_level_skip_initialization_after_readiness():
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(None, strategy)
    manager.start()

    validation_detection = {"state": "RUNNING", "secondary_states": []}
    assert manager.maybe_run_start(validation_detection)
    manager.set_exclusive_validation_battle(True)
    mission_vars = manager.ctx.data["mission_vars"]
    mission_vars.update(
        damage_slider_checked=True,
        gc_session_preflight_attempted=True,
        gc_session_preflight_completed=True,
    )
    manager.maybe_run_start({"state": "GAME_OVER", "secondary_states": []})
    manager.on_game_over()

    detection = {"state": "RUNNING", "secondary_states": ["TOURNAMENT"]}
    assert manager.maybe_run_start(detection)
    assert manager.run_initialization_pending()
    assert mission_vars["gc_session_preflight_completed"]
    assert not mission_vars["ehls_completed"]
    assert not mission_vars["eals_completed"]
    actions = strategy.tick(manager.ctx, object(), detection)

    assert actions == [{"type": "level_skip_initialize"}]


def test_confirmed_launch_starts_once_then_runs_level_skip_initialization(
    tmp_path,
):
    app, store, manager, ready, _definition = _app_for_ready_launch(tmp_path)
    action_log = Mock()
    result_log = Mock()
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    frame = object()
    home = {"state": "HOME_SCREEN", "secondary_states": []}

    with (
        patch(
            "core.app.dispatch_tournament_launch",
            return_value=TournamentLaunchDispatch(
                True,
                "verified Tournament BATTLE was dispatched",
            ),
        ) as dispatch,
        patch("core.app.log"),
        patch("core.app.log_action_intent", new=action_log),
        patch("core.app.log_result", new=result_log),
    ):
        assert app._advance_exclusive_validation_launch(
            frame,
            home,
            battle_started=False,
        )

    dispatch.assert_called_once()
    assert _current_receipt(store)["launch"]["status"] == "claimed"

    tournament = {"state": "RUNNING", "secondary_states": ["TOURNAMENT"]}
    battle_started = manager.maybe_run_start(tournament)
    assert battle_started
    with (
        patch("core.app.log"),
        patch("core.app.log_result", new=result_log),
    ):
        assert not app._advance_exclusive_validation_launch(
            frame,
            tournament,
            battle_started=battle_started,
        )

    result = _current_receipt(store)
    assert result["launch"]["status"] == "started"
    assert manager.run_initialization_pending()
    mission_vars = manager.ctx.data["mission_vars"]
    assert not mission_vars["ehls_completed"]
    assert not mission_vars["eals_completed"]
    assert manager.strategy.tick(manager.ctx, frame, tournament) == [
        {"type": "level_skip_initialize"}
    ]
    action_log.assert_called_once()
    assert action_log.call_args.args[0] == (
        "Starting the operator-confirmed Tournament"
    )
    result_log.assert_called_once_with(
        "Tournament launch complete — battle started",
        detail=(
            f"[TOURNAMENT_LAUNCH] result=started "
            f"request_id={ready['request_id']} "
            "reason=Tournament battle started from the operator-confirmed "
            "launch; EHLS/EALS initialization is active"
        ),
    )


def test_manual_tournament_start_consumes_unclaimed_prompt(tmp_path):
    app, store, manager, ready, _definition = _app_for_ready_launch(tmp_path)
    tournament = {"state": "RUNNING", "secondary_states": ["TOURNAMENT"]}
    battle_started = manager.maybe_run_start(tournament)

    with patch("core.app.log"):
        assert not app._advance_exclusive_validation_launch(
            object(),
            tournament,
            battle_started=battle_started,
        )

    result = _current_receipt(store)
    assert result["launch"]["status"] == "started"
    assert result["launch"]["started_by"] == "manual_observation"
    assert manager.run_initialization_pending()


def test_requested_launch_waits_while_paused_without_claiming(tmp_path):
    app, store, _manager, ready, _definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    store.set_state("PAUSED", source="test")
    app._supervisor.apply_control()

    assert app._advance_exclusive_validation_launch(
        object(),
        {"state": "HOME_SCREEN", "secondary_states": []},
        battle_started=False,
    )
    assert _current_receipt(store)["launch"]["status"] == "requested"


def test_validation_battle_bypasses_level_skips_without_seeding_completion():
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(None, strategy)
    manager.start()
    detection = {"state": "RUNNING", "secondary_states": []}

    assert manager.maybe_run_start(detection)
    mission_vars = manager.ctx.data["mission_vars"]
    mission_vars.update(
        damage_slider_checked=True,
        gc_session_preflight_attempted=True,
        gc_session_preflight_completed=True,
        gc_session_preflight_waivers={"ultimate_weapons": {"status": "claimed"}},
    )
    manager.set_exclusive_validation_battle(True)
    assert not mission_vars["ehls_completed"]
    assert not mission_vars["eals_completed"]
    assert not mission_vars["damage_slider_checked"]
    assert not mission_vars["gc_session_preflight_attempted"]
    assert not mission_vars["gc_session_preflight_completed"]
    assert mission_vars["gc_session_preflight_waivers"] == {}
    assert not manager.run_initialization_pending()

    actions = strategy.tick(manager.ctx, object(), detection)
    assert actions == [
        {
            "type": "damage_slider_configure",
            "mode": "enforce",
            "value": "1E2%",
        }
    ]
