from __future__ import annotations

from datetime import datetime, timezone
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from automation.missions.manager import MissionManager
from automation.strategies import get_strategy
from core.automation_supervisor import AutomationSupervisor
from core.action_authority import (
    AuthorityHold,
    AuthorityHoldState,
    RuntimeActionClass,
)
from core.battle_lifecycle import HomeBattleControl
from core.control_directives import ControlDirectiveError, ControlDirectiveStore
from core.dispatch_control_boundary import dispatch_control_boundary
from core.exclusive_validation import (
    exclusive_validation_definition_for_strategy,
)
from core.app import App
from core.input import TapDispatchOutcome, TapDispatchStatus
from core.run_state import AUTOMATION
from core.runtime_failure_policy import RuntimeFailureKind
from handlers.free_ticket_handler import (
    FreeTicketRecoveryResult,
    FreeTicketRecoveryStatus,
)
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
VALIDATION_BATTLE_IDENTITY = "e" * 64


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


def _install_forced_running_identity_once(
    app: App,
    identity: str,
) -> Mock:
    """Model one successful force-save transaction and its recapture."""

    forced = False

    def force(detection, _frame):
        nonlocal forced
        if (
            forced
            or app._supervisor.is_paused
            or str(detection.get("state") or "").upper() != "RUNNING"
            or getattr(app, "_active_round_identity_fingerprint", None)
        ):
            return False
        forced = True
        app._active_round_identity_fingerprint = identity
        app._observed_active_round_identity_fingerprint = identity
        app._battle_identity_reconciliation_required = False
        return True

    installed = Mock(side_effect=force)
    app._force_battle_identity = installed
    return installed


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
    app._status_reporter = Mock()
    app._active_exclusive_validation_request_id = None
    app._active_exclusive_validation_battle_identity = None
    app._exclusive_validation_terminal_hold = None
    app._active_round_identity_fingerprint = VALIDATION_BATTLE_IDENTITY
    return app, store, manager


def _ready_validation(
    store: ControlDirectiveStore,
    *,
    owner=OWNER_ONE,
):
    saved = store.set_strategy("tournament", source="test")
    definition = exclusive_validation_definition_for_strategy("tournament")
    assert definition is not None
    claimed = store.claim_exclusive_validation(
        strategy_request_id=saved["strategy_request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=owner,
        timeout_seconds=definition.timeout_seconds,
    )
    assert claimed is not None
    running = store.mark_exclusive_validation_running(
        claimed["request_id"],
        owner=owner,
    )
    assert running is not None
    cleanup = store.begin_exclusive_validation_cleanup(
        running["request_id"],
        owner=owner,
        outcome="ready",
        reason="checks passed",
    )
    assert cleanup is not None
    ready = store.finish_exclusive_validation(
        cleanup["request_id"],
        owner=owner,
        outcome="ready",
        reason="checks passed",
    )
    assert ready is not None
    return ready, definition


def _app_for_ready_launch(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    supervisor = AutomationSupervisor(control_file=str(control_file))
    ready, definition = _ready_validation(
        store,
        owner=supervisor.current_exclusive_validation_owner(),
    )
    store.set_state("RUNNING", source="test")
    supervisor.apply_control()
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(None, strategy)
    manager.start()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._active_exclusive_validation_request_id = None
    app._active_exclusive_validation_battle_identity = None
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


def test_start_tournament_decision_uses_device_dispatch_boundary(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    ready, _definition = _ready_validation(store)
    request_started = threading.Event()
    request_completed = threading.Event()
    results = []
    failures = []

    def resolve_start():
        request_started.set()
        try:
            results.append(
                store.resolve_exclusive_validation_launch(
                    ready["request_id"],
                    "start",
                    source="test",
                )
            )
        except Exception as exc:  # pragma: no cover - surfaced below
            failures.append(exc)
        finally:
            request_completed.set()

    request_thread = threading.Thread(target=resolve_start)
    with dispatch_control_boundary(store.dispatch_lock_path):
        request_thread.start()
        assert request_started.wait(timeout=2)
        assert not request_completed.wait(timeout=0.05)

    request_thread.join(timeout=2)
    assert not request_thread.is_alive()
    assert failures == []
    assert results[0] is not None
    assert results[0]["launch"]["status"] == "requested"


def test_new_confirmed_launch_owner_blocks_prior_route_final_guard(tmp_path):
    app, store, _manager, ready, _definition = _app_for_ready_launch(tmp_path)
    app._status_reporter = Mock()
    app._runtime_shutting_down = False
    app._authority_holds = ()
    app._update_action_authority(
        detection={
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        },
        holds=(),
    )

    requested = store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )

    assert requested is not None
    assert app._active_exclusive_validation_launch_request_id is None
    assert not app._runtime_control_mutation_guard()
    assert not app._runtime_action_guard(
        action_class=RuntimeActionClass.LIFECYCLE_ACTION,
    )
    app._status_reporter.request_immediate_report.assert_not_called()


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


def test_ready_launch_can_be_reclaimed_only_on_its_validated_target(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    ready, definition = _ready_validation(store)
    requested = store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    assert requested is not None
    different_target = {
        **OWNER_TWO,
        "adb_target": "localhost:5565",
    }

    assert (
        store.claim_exclusive_validation_launch(
            ready["request_id"],
            configuration_fingerprint=definition.configuration_fingerprint,
            owner=different_target,
        )
        is None
    )

    reclaimed = store.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=OWNER_TWO,
    )
    assert reclaimed is not None
    assert reclaimed["launch"]["owner"] == OWNER_TWO
    assert reclaimed["owner"] == OWNER_ONE


def test_manual_launch_observation_is_bound_to_its_validated_target(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    ready, _definition = _ready_validation(store)
    different_target = {
        **OWNER_TWO,
        "adb_target": "localhost:5565",
    }

    assert (
        store.record_manual_exclusive_validation_launch(
            ready["request_id"],
            observer=different_target,
            reason="fresh Tournament battle on a different target",
        )
        is None
    )

    started = store.record_manual_exclusive_validation_launch(
        ready["request_id"],
        observer=OWNER_TWO,
        reason="fresh Tournament battle on the validated target",
    )
    assert started is not None
    assert started["launch"]["status"] == "started"
    assert started["launch"]["started_by"] == "manual_observation"


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
    supervisor = AutomationSupervisor(control_file=str(control_file))
    ready, definition = _ready_validation(
        store,
        owner=supervisor.current_exclusive_validation_owner(),
    )
    store.set_state("RUNNING", source="test")
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    supervisor.apply_control()
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
    app._active_exclusive_validation_battle_identity = None
    app._exclusive_validation_ownership_hold = False
    with patch("core.app.log"):
        receipt = app._reconcile_exclusive_validation()

    assert receipt is not None
    assert receipt["status"] == "pending"
    assert receipt["strategy_request_id"] == second["strategy_request_id"]
    assert not app._exclusive_validation_ownership_hold
    old = store.status()["exclusive_validation"]["receipts"][claimed["request_id"]]
    assert old["outcome"] == "failed"


def test_transient_validation_ownership_reread_retains_suppressive_hold(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    detection = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        detection,
        battle_started=manager.maybe_run_start(detection),
    )
    running = _current_receipt(store)

    with patch.object(
        app._supervisor._control_store,
        "status",
        side_effect=ControlDirectiveError("transient ownership reread"),
    ):
        retained = app._reconcile_exclusive_validation()

    assert retained is not None
    assert retained["request_id"] == running["request_id"]
    assert retained["status"] == "running"
    assert app._active_exclusive_validation_request_id == running["request_id"]
    assert app._exclusive_validation_ownership_hold is True
    assert not app._exclusive_validation_in_progress()
    assert app._exclusive_validation_blocks_target_handoff()
    holds = app._heartbeat_action_authority_holds(
        operator_workflow_hold=None,
        exclusive_validation_terminal_finalization_pending=False,
        exclusive_validation_passive_battle_hold=False,
        exclusive_validation_ownership_hold=(
            app._exclusive_validation_ownership_hold
        ),
        exclusive_validation_in_progress=(
            app._exclusive_validation_in_progress()
        ),
        exclusive_validation_launch_in_progress=False,
        initialization_pending=False,
        session_preflight_pending=False,
    )
    assert tuple(item.hold for item in holds) == (
        AuthorityHold.EXCLUSIVE_OWNERSHIP,
    )

    recovered = app._reconcile_exclusive_validation()
    assert recovered is not None
    assert recovered["request_id"] == running["request_id"]
    assert app._exclusive_validation_ownership_hold is False
    assert app._exclusive_validation_in_progress()


def test_claimed_validation_start_proof_survives_transient_mark_failure(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    request_id = _current_receipt(store)["request_id"]
    detection = {"state": "RUNNING", "secondary_states": []}
    battle_started = manager.maybe_run_start(detection)
    assert battle_started
    original_mark = app._supervisor.mark_exclusive_validation_running
    mark_calls = 0

    def transient_mark(candidate_id):
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 1:
            return None
        return original_mark(candidate_id)

    app._supervisor.mark_exclusive_validation_running = Mock(
        side_effect=transient_mark
    )

    app._observe_exclusive_validation_battle_start(
        detection,
        battle_started=battle_started,
    )

    assert _current_receipt(store)["status"] == "claimed"
    assert app._exclusive_validation_claimed_start_hold == request_id
    assert manager.ctx.data["exclusive_validation_battle"] is False

    app._observe_exclusive_validation_battle_start(
        detection,
        battle_started=False,
    )

    assert _current_receipt(store)["status"] == "running"
    assert app._exclusive_validation_claimed_start_hold is None
    assert manager.ctx.data["exclusive_validation_battle"] is True
    assert mark_calls == 2


def test_ambiguous_claimed_running_battle_becomes_passive(tmp_path):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    request_id = _current_receipt(store)["request_id"]
    detection = {"state": "RUNNING", "secondary_states": []}

    with (
        patch("core.app.surrender_run") as surrender,
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        app._observe_exclusive_validation_battle_start(
            detection,
            battle_started=False,
        )

    surrender.assert_not_called()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "did not establish a new ordinary battle" in result["reason"]
    assert app._exclusive_validation_passive_battle_hold == request_id
    assert manager.ctx.data["startup_gates_deferred"] is True
    assert manager.ctx.data["skip_attached_checks"] is True
    app._authority_holds = ()
    assert tuple(
        hold.hold
        for hold in app._refreshed_operator_authority_holds(
            release_stale=False
        )
    ) == (AuthorityHold.EXCLUSIVE_OWNERSHIP,)


def test_main_loop_suppresses_claimed_ambiguous_battle_same_heartbeat(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    request_id = _current_receipt(store)["request_id"]
    frame = object()
    running = {
        "state": "RUNNING",
        "menu": "ATTACK_MENU",
        "secondary_states": [],
        "overlays": [],
    }
    assert manager.maybe_run_start(running)
    app._config = SimpleNamespace(
        wait_on_start=False,
        strategy_name="tournament",
    )
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._authority_holds = ()
    app._capture_frame = Mock(side_effect=[frame, KeyboardInterrupt])
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._yield_on_unexpected_manual_activity = Mock()
    app._sync_operator_control_workflows = Mock()
    app._advance_pending_home_setup_recovery = Mock(return_value=False)
    app._observe_player_save_audit_screen = Mock()
    app._sync_interactive_development_observation = Mock()
    app._observe_no_strategy_frame = Mock()
    app._process_strategy_boundary = Mock()
    app._observe_strategy_gate_boundary = Mock()
    app._clear_terminal_home_continuation = Mock()
    app._accept_pending_terminal_history_handoff = Mock()
    app._cancel_pending_tournament_validation_after_boundary = Mock()
    app._complete_started_battle_workflow = Mock()
    app._battle_activation_tracker = Mock()
    app._battle_activation_tracker.observe.return_value = []
    app._battle_activation_tracker.drain_evidence_captures.return_value = []
    app._perk_timeline = Mock(return_value=Mock())
    app._reset_player_save_audit_perk_mapping_evidence = Mock()
    app._bind_started_battle_player_save_preflight = Mock()
    app._complete_ready_attachment_after_adoption = Mock()
    app._observe_terminal_run_binding = Mock()
    app._sync_strategy_action_gate = Mock()
    app._claim_proactive_gate_waivers = Mock(return_value=False)
    app._maybe_log_steady_run_entry = Mock()
    app._observe_player_save_audit_visual_events = Mock()
    app._emit_event_mission_warnings = Mock()
    app._sync_floating_gem_tapper = Mock()
    app._handle_primary_states = Mock()
    manager.tick = Mock()

    with (
        patch("core.app.threading.Thread"),
        patch("core.app.detect_state_and_overlays", return_value=running),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep"),
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        app.run()

    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert app._exclusive_validation_passive_battle_hold == request_id
    assert tuple(hold.hold for hold in app._authority_holds) == (
        AuthorityHold.EXCLUSIVE_OWNERSHIP,
    )
    manager.tick.assert_not_called()
    app._handle_primary_states.assert_not_called()


def test_main_loop_records_claimed_start_before_owned_validation_tick(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    frame = object()
    running = {
        "state": "RUNNING",
        "menu": "ATTACK_MENU",
        "secondary_states": [],
        "overlays": [],
    }
    app._config = SimpleNamespace(
        wait_on_start=False,
        strategy_name="tournament",
    )
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = Mock()
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._authority_holds = ()
    app._capture_frame = Mock(return_value=frame)
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._yield_on_unexpected_manual_activity = Mock()
    app._sync_operator_control_workflows = Mock()
    app._advance_pending_home_setup_recovery = Mock(return_value=False)
    app._observe_player_save_audit_screen = Mock()
    app._sync_interactive_development_observation = Mock()
    app._observe_no_strategy_frame = Mock()
    app._process_strategy_boundary = Mock()
    app._observe_strategy_gate_boundary = Mock()
    app._clear_terminal_home_continuation = Mock()
    app._accept_pending_terminal_history_handoff = Mock()
    app._cancel_pending_tournament_validation_after_boundary = Mock()
    app._complete_started_battle_workflow = Mock()
    app._battle_activation_tracker = Mock()
    app._perk_timeline = Mock(return_value=Mock())
    app._reset_player_save_audit_perk_mapping_evidence = Mock()
    app._bind_started_battle_player_save_preflight = Mock()
    app._complete_ready_attachment_after_adoption = Mock()
    app._observe_terminal_run_binding = Mock()
    app._sync_strategy_action_gate = Mock()
    observed_owners = []

    def stop_after_owned_tick(*_args, **_kwargs):
        observed_owners.append(app._active_action_authority_owner)
        assert _current_receipt(store)["status"] == "running"
        assert manager.ctx.data["exclusive_validation_battle"] is True
        raise KeyboardInterrupt

    manager.tick = Mock(side_effect=stop_after_owned_tick)

    with (
        patch("core.app.threading.Thread"),
        patch("core.app.detect_state_and_overlays", return_value=running),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep"),
        patch("core.app.log"),
    ):
        app.run()

    assert observed_owners == [AuthorityHold.EXCLUSIVE_VALIDATION]
    assert app._exclusive_validation_claimed_start_hold is None
    assert _current_receipt(store)["status"] == "running"


def test_active_strategy_request_waits_for_validation_start_proof(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    request_id = _current_receipt(store)["request_id"]
    app._exclusive_validation_claimed_start_hold = request_id
    replacement = store.set_strategy(
        "farm_t18",
        apply_mode="active_battle",
        source="test",
    )
    app._pending_strategy_request = (
        "farm_t18",
        replacement["strategy_request_id"],
        "active_battle",
    )
    detection = {"state": "RUNNING", "secondary_states": []}
    manager.adopt_strategy_for_active_battle = Mock()

    app._process_strategy_boundary(detection)

    manager.adopt_strategy_for_active_battle.assert_not_called()
    assert app._pending_strategy_request is not None
    assert app._exclusive_validation_claimed_start_hold == request_id
    assert _current_receipt(store)["status"] == "claimed"

    app._observe_exclusive_validation_battle_start(
        detection,
        battle_started=False,
    )
    assert _current_receipt(store)["status"] == "running"

    app._update_action_authority(
        detection=detection,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "queued Strategy must retire validation first",
            ),
        ),
    )
    with (
        patch("core.app.surrender_run", return_value=False) as surrender,
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        assert app._advance_owned_exclusive_validation(detection)

    surrender.assert_called_once()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "newer Strategy request" in result["reason"]
    assert app._pending_strategy_request is not None


def test_claimed_start_proof_quarantines_terminal_while_paused(tmp_path):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    request_id = _current_receipt(store)["request_id"]
    running = {"state": "RUNNING", "secondary_states": []}
    original_mark = app._supervisor.mark_exclusive_validation_running
    mark_calls = 0

    def transient_mark(candidate_id):
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 1:
            return None
        return original_mark(candidate_id)

    app._supervisor.mark_exclusive_validation_running = Mock(
        side_effect=transient_mark
    )
    app._observe_exclusive_validation_battle_start(
        running,
        battle_started=manager.maybe_run_start(running),
    )
    assert app._exclusive_validation_claimed_start_hold == request_id

    store.set_state("PAUSED", source="test")
    app._supervisor.apply_control()
    app._authority_holds = ()
    game_over = {
        "state": "GAME_OVER",
        "secondary_states": [],
        "overlays": [],
    }
    with patch("core.app.log"):
        assert app._quarantine_exclusive_validation_terminal_finalization(
            game_over
        )

    assert _current_receipt(store)["status"] == "claimed"
    assert app._exclusive_validation_terminal_hold == request_id
    assert app._exclusive_validation_terminal_mode == (
        "running_terminal_observed"
    )
    assert mark_calls == 1

    store.set_state("RUNNING", source="test-resume")
    app._supervisor.apply_control()
    app._flag_recoverable_runtime_failure = Mock()
    with (
        patch("core.app.return_home_from_game_over", return_value=False),
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        assert app._quarantine_exclusive_validation_terminal_finalization(
            game_over
        )

    assert _current_receipt(store)["status"] == "cleanup"
    assert app._exclusive_validation_claimed_start_hold is None
    assert app._exclusive_validation_terminal_mode == "game_over_observed"
    assert manager.ctx.data["exclusive_validation_battle"] is True
    assert mark_calls == 2


def test_claimed_start_proof_releases_resumable_home_after_mark_retry(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    request_id = _current_receipt(store)["request_id"]
    running = {"state": "RUNNING", "secondary_states": []}
    original_mark = app._supervisor.mark_exclusive_validation_running
    mark_calls = 0

    def transient_mark(candidate_id):
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 1:
            return None
        return original_mark(candidate_id)

    app._supervisor.mark_exclusive_validation_running = Mock(
        side_effect=transient_mark
    )
    app._observe_exclusive_validation_battle_start(
        running,
        battle_started=manager.maybe_run_start(running),
    )
    assert app._exclusive_validation_claimed_start_hold == request_id

    home_resume = {
        "state": "HOME_SCREEN",
        "home_battle_control": "RESUME_BATTLE",
        "secondary_states": [],
    }
    with (
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        app._observe_exclusive_validation_battle_start(
            home_resume,
            battle_started=False,
        )

    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "returned to resumable Home" in result["reason"]
    assert app._exclusive_validation_terminal_mode == (
        "release_without_cleanup"
    )
    assert app._exclusive_validation_claimed_start_hold is None
    assert manager.ctx.data["exclusive_validation_battle"] is True
    assert mark_calls == 2


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


def _dispatch_explicit_start_validation(
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
    app._status_reporter = Mock()
    app._runtime_shutting_down = False
    app._emulator_maintenance_hold_active = False
    app._emulator_recovery_terminal_pending = None
    app._free_ticket_recovery_attempts = {}
    app._free_ticket_recovery_cleared = set()
    app._free_ticket_recovery_warnings = set()
    app._uncertain_lifecycle_actions = set()
    app._blocking_primary_hold_active = False
    app._blocking_primary_state = None
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._active_round_identity_fingerprint = VALIDATION_BATTLE_IDENTITY

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

    def start_under_operator_owner(
        *,
        action_guard_fn,
        return_dispatch_outcome,
    ):
        assert tuple(hold.hold for hold in app._authority_holds) == (
            AuthorityHold.OPERATOR_WORKFLOW,
        )
        assert action_guard_fn()
        assert return_dispatch_outcome is True
        return True

    with (
        patch(
            "core.app.tap_verified_new_battle",
            side_effect=start_under_operator_owner,
        ) as start,
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        ) is True

    start.assert_called_once()
    assert supervisor.battle_workflow["status"] == "action_dispatched"
    launch_scope = supervisor.battle_workflow["acknowledgement"][
        "activity_scope_run_id"
    ]
    return app, store, supervisor, manager, launch_scope


def test_explicit_start_owns_tournament_validation_launch_through_adoption(
    tmp_path,
    monkeypatch,
):
    app, store, supervisor, manager, launch_scope = (
        _dispatch_explicit_start_validation(tmp_path, monkeypatch)
    )
    app._control_observation = {
        **app._control_observation,
        "activity_scope_run_id": launch_scope,
        "observation_id": "tournament-workflow:running",
        "primary_state": "RUNNING",
        "home_battle_control": "UNKNOWN",
        "game_state": "active_battle",
        "active_battle": True,
    }
    app._sync_operator_control_workflows({"state": "RUNNING"})
    battle_started = manager.maybe_run_start({"state": "RUNNING"})
    assert battle_started is True
    app._observe_exclusive_validation_battle_start(
        {"state": "RUNNING", "secondary_states": []},
        battle_started=battle_started,
    )
    assert _current_receipt(store)["status"] == "running"
    original_transition = supervisor.transition_battle_workflow
    completion_calls = 0

    def transient_completion(*args, **kwargs):
        nonlocal completion_calls
        if len(args) >= 2 and args[1] == "completed":
            completion_calls += 1
            if completion_calls == 1:
                return None
        return original_transition(*args, **kwargs)

    supervisor.transition_battle_workflow = Mock(
        side_effect=transient_completion
    )
    assert not app._complete_started_battle_workflow(
        battle_started,
        {"state": "RUNNING", "secondary_states": []},
    )
    assert app._started_battle_workflow_completion is not None
    assert app._operator_workflow_authority_hold() is None
    assert app._exclusive_validation_in_progress()
    assert app._complete_started_battle_workflow(
        False,
        {"state": "RUNNING", "secondary_states": []},
    )
    assert completion_calls == 2
    assert app._started_battle_workflow_completion is None
    assert supervisor.battle_workflow["status"] == "completed"


@pytest.mark.parametrize("failed_write", ("workflow", "validation"))
def test_explicit_start_validation_free_ticket_uncertainty_retries_only_writes(
    tmp_path,
    monkeypatch,
    failed_write,
):
    app, store, supervisor, manager, launch_scope = (
        _dispatch_explicit_start_validation(tmp_path, monkeypatch)
    )
    workflow = supervisor.battle_workflow
    assert workflow is not None
    workflow_id = str(workflow["request_id"])
    validation = _current_receipt(store)
    validation_id = str(validation["request_id"])
    assert workflow["status"] == "action_dispatched"
    assert validation["status"] == "claimed"
    assert app._exclusive_validation_battle_dispatch_hold == validation_id

    app._control_observation = {
        **app._control_observation,
        "activity_scope_run_id": launch_scope,
        "observation_id": "tournament-workflow:free-ticket",
        "primary_state": "UNKNOWN",
        "home_battle_control": "UNKNOWN",
        "game_state": "unknown",
        "active_battle": False,
    }
    original_transition = supervisor.transition_battle_workflow
    original_validation_finish = (
        app._finish_exclusive_validation_without_cleanup
    )
    write_calls = {"workflow": 0, "validation": 0}

    def transient_workflow_failure(*args, **kwargs):
        if len(args) >= 2 and args[1] == "failed":
            write_calls["workflow"] += 1
            if (
                failed_write == "workflow"
                and write_calls["workflow"] == 1
            ):
                return None
        return original_transition(*args, **kwargs)

    def transient_validation_failure(*args, **kwargs):
        write_calls["validation"] += 1
        if (
            failed_write == "validation"
            and write_calls["validation"] == 1
        ):
            return None
        return original_validation_finish(*args, **kwargs)

    supervisor.transition_battle_workflow = Mock(
        side_effect=transient_workflow_failure
    )
    app._finish_exclusive_validation_without_cleanup = Mock(
        side_effect=transient_validation_failure
    )
    recovery = FreeTicketRecoveryResult(
        FreeTicketRecoveryStatus.UNCERTAIN,
        True,
        1,
        "FREE_TICKET",
        "Claim dispatch outcome was uncertain",
        True,
    )

    def uncertain_claim(_frame, *, action_guard_fn, **_kwargs):
        assert action_guard_fn()
        return recovery

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch(
            "core.app.handle_free_ticket_modal",
            side_effect=uncertain_claim,
        ) as claim,
        patch("core.app.log"),
    ):
        assert app._advance_blocking_primary_recovery(
            object(),
            {"state": "FREE_TICKET"},
        )
        assert claim.call_count == 1
        expected_workflow_status = (
            "action_dispatched" if failed_write == "workflow" else "failed"
        )
        assert supervisor.battle_workflow["status"] == expected_workflow_status
        assert _current_receipt(store)["status"] == "claimed"
        assert supervisor.is_paused

        # A later heartbeat may finish the two linked durable receipts, but
        # the already-uncertain Claim transaction is never dispatched again.
        assert app._advance_blocking_primary_recovery(
            object(),
            {"state": "FREE_TICKET"},
        )

    claim.assert_called_once()
    assert write_calls == {
        "workflow": 2 if failed_write == "workflow" else 1,
        "validation": 1 if failed_write == "workflow" else 2,
    }
    assert app._free_ticket_recovery_attempts == {
        f"battle:{workflow_id}": 1,
        f"exclusive-validation:{validation_id}": 1,
    }
    assert supervisor.battle_workflow["status"] == "failed"
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert app._exclusive_validation_battle_dispatch_hold is None
    assert app._exclusive_validation_passive_battle_hold == validation_id
    assert not manager.ctx.data.get("exclusive_validation_battle", False)


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
    start.assert_called_once()
    assert start.call_args.kwargs["return_dispatch_outcome"] is True
    start_guard = start.call_args.kwargs["action_guard_fn"]
    assert tuple(hold.hold for hold in app._authority_holds) == (
        AuthorityHold.EXCLUSIVE_VALIDATION,
    )
    assert start_guard()

    def accept_setup_capture():
        app._supervisor._setup_capture = {
            "status": "requested",
            "request_id": "take-over-validation-start",
        }
        return True

    with patch.object(
        app._supervisor,
        "apply_control",
        side_effect=accept_setup_capture,
    ):
        assert not start_guard()
    app._supervisor._setup_capture = None
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
    exclusive_hold = AuthorityHoldState(
        AuthorityHold.EXCLUSIVE_VALIDATION,
        "exclusive validation owns cleanup",
    )
    app._update_action_authority(
        detection=detection,
        holds=(exclusive_hold,),
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
    surrender_guard = surrender.call_args.kwargs["action_guard"]
    assert surrender_guard()
    with patch.object(
        app,
        "_operator_workflow_authority_hold",
        return_value=AuthorityHoldState(
            AuthorityHold.SETUP_CAPTURE,
            "setup capture interrupted validation cleanup",
        ),
    ):
        # Conclusive Surrender has already installed exact terminal proof. A
        # newly queued successor workflow is future work and cannot displace
        # receipt-owned cleanup; Setup Capture admission rejects this state in
        # production.
        assert surrender_guard()

    app._update_action_authority(
        detection={"state": "GAME_OVER", "secondary_states": []},
        holds=(exclusive_hold,),
    )

    def return_home_under_typed_owner(*, timeout_s, action_guard):
        assert timeout_s == 8.0
        assert action_guard()
        return True

    with (
        patch(
            "core.app.return_home_from_game_over",
            side_effect=return_home_under_typed_owner,
        ) as go_home,
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


def test_uncertain_validation_battle_dispatch_pauses_and_never_replays(
    tmp_path,
):
    app, store, _manager = _app_for_pending_validation(tmp_path)
    uncertain = TapDispatchOutcome(TapDispatchStatus.UNCERTAIN)

    with (
        patch(
            "core.app.tap_verified_new_battle",
            return_value=uncertain,
        ) as start,
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
        assert not app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )

    start.assert_called_once()
    assert start.call_args.kwargs["return_dispatch_outcome"] is True
    assert callable(start.call_args.kwargs["action_guard_fn"])
    result = _current_receipt(store)
    assert result["status"] == "claimed"
    assert app._exclusive_validation_battle_dispatch_hold == result[
        "request_id"
    ]
    assert app._supervisor.control_state == "PAUSED"


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


def test_fresh_verified_home_finishes_cleanup_without_repeating_home_tap():
    receipt = {
        "request_id": "validation-1",
        "status": "cleanup",
        "pending_outcome": "ready",
        "pending_reason": "validation checks completed",
    }
    result = {
        **receipt,
        "status": "result",
        "outcome": "ready",
        "reason": "validation checks completed",
    }
    app = App.__new__(App)
    app._exclusive_validation_terminal_hold = None
    app._supervisor = Mock()
    app._supervisor.owns_exclusive_validation.return_value = True
    app._supervisor.finish_exclusive_validation.return_value = result
    app._reconcile_exclusive_validation = Mock(
        side_effect=(receipt, receipt, result)
    )
    app._announce_exclusive_validation_result = Mock()
    app._mission_mgr = Mock()
    app._status_reporter = Mock()
    app._apply_pending_strategy = Mock()

    home = {
        "state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
    }
    with patch("core.app.return_home_from_game_over") as return_home:
        assert app._dispatch_exclusive_validation_game_over(home)

    return_home.assert_not_called()
    app._supervisor.finish_exclusive_validation.assert_called_once_with(
        receipt["request_id"],
        outcome="ready",
        reason="validation checks completed",
        allowed_statuses=("cleanup",),
    )
    app._announce_exclusive_validation_result.assert_called_once_with(result)
    app._mission_mgr.finalize_exclusive_validation_game_over_boundary.assert_called_once()
    app._mission_mgr.set_exclusive_validation_battle.assert_called_once_with(
        False
    )
    app._apply_pending_strategy.assert_called_once()
    assert app._exclusive_validation_terminal_hold is None


def test_cleanup_result_finalizes_before_new_strategy_boundary_applies():
    request_id = "validation-1"
    result = {
        "request_id": request_id,
        "status": "result",
        "outcome": "ready",
        "reason": "validation checks completed",
    }
    app = App.__new__(App)
    app._supervisor = Mock()
    app._supervisor.strategy_request = (
        "farm_t18",
        "new-strategy-request",
        "next_boundary",
    )
    app._supervisor.exclusive_validation_receipt.side_effect = (
        lambda *, request_id=None, strategy_request_id=None: (
            result if request_id == "validation-1" else None
        )
    )
    app._mission_mgr = Mock()
    app._mission_mgr.strategy = get_strategy("tournament")
    app._status_reporter = Mock()
    app._active_exclusive_validation_request_id = request_id
    app._exclusive_validation_terminal_hold = request_id
    app._pending_strategy_request = (
        "farm_t18",
        "new-strategy-request",
        "next_boundary",
    )
    app._strategy_boundary_confirmed = False
    app._exclusive_validation_ownership_hold = False
    app._reconcile_exclusive_validation = Mock(return_value=result)
    events = []
    app._mission_mgr.finalize_exclusive_validation_game_over_boundary.side_effect = (
        lambda: events.append("validation-finalized")
    )
    app._apply_pending_strategy = Mock(
        side_effect=lambda: events.append("strategy-applied")
    )

    home = {
        "state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
    }
    app._process_strategy_boundary(home)

    # The new current Strategy request must not hide the exact terminal
    # receipt or apply before validation's process-local boundary is released.
    assert app._exclusive_validation_receipt() == result
    app._apply_pending_strategy.assert_not_called()

    assert app._dispatch_exclusive_validation_game_over(home)

    assert events == ["validation-finalized", "strategy-applied"]
    assert app._active_exclusive_validation_request_id is None
    assert app._exclusive_validation_terminal_hold is None


@pytest.mark.parametrize(
    ("detection", "passive_expected"),
    (
        ({"state": "UNKNOWN"}, True),
        (
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "UNKNOWN",
            },
            True,
        ),
        (
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "NEW_BATTLE",
            },
            False,
        ),
        ({"state": "TOURNAMENT_SCREEN"}, False),
    ),
)
def test_claimed_validation_timeout_fails_without_surrender(
    tmp_path,
    detection,
    passive_expected,
):
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
            detection,
            battle_started=False,
        )

    surrender.assert_not_called()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "did not reach fresh RUNNING" in result["reason"]
    assert bool(
        getattr(app, "_exclusive_validation_passive_battle_hold", None)
    ) is passive_expected


def test_running_validation_timeout_surrenders_once_then_finishes_cleanup(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    running_detection = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        running_detection,
        battle_started=manager.maybe_run_start(running_detection),
    )
    running = _current_receipt(store)
    app._update_action_authority(
        detection=running_detection,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "exclusive validation owns timeout cleanup",
            ),
        ),
    )

    def surrender_under_typed_owner(*, action_guard, **_kwargs):
        assert action_guard()
        return True

    with (
        patch("core.app.time.time", return_value=running["deadline_at"] + 1),
        patch(
            "core.app.surrender_run",
            side_effect=surrender_under_typed_owner,
        ) as surrender,
        patch("core.app.log"),
    ):
        assert app._advance_exclusive_validation(running_detection)

    surrender.assert_called_once()
    cleanup = _current_receipt(store)
    assert cleanup["status"] == "cleanup"
    assert cleanup["pending_outcome"] == "failed"
    assert "timed out" in cleanup["pending_reason"]

    app._update_action_authority(
        detection={"state": "GAME_OVER", "secondary_states": []},
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "exclusive validation owns Game Over cleanup",
            ),
        ),
    )
    with (
        patch("core.app.return_home_from_game_over", return_value=True),
        patch("core.app.log_result"),
    ):
        assert app._handle_exclusive_validation_game_over()

    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "timed out" in result["reason"]


def test_running_validation_timeout_and_cleanup_use_main_loop_owner(tmp_path):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    running_detection = {
        "state": "RUNNING",
        "secondary_states": [],
        "overlays": [],
    }
    app._observe_exclusive_validation_battle_start(
        running_detection,
        battle_started=manager.maybe_run_start(running_detection),
    )
    running = _current_receipt(store)
    frame = object()
    detections = [
        running_detection,
        {
            "state": "GAME_OVER",
            "secondary_states": [],
            "overlays": [],
        },
    ]
    owners = []
    app._config = SimpleNamespace(wait_on_start=False)
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = Mock()
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._authority_holds = ()
    app._capture_frame = Mock(side_effect=[frame, frame, KeyboardInterrupt])
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._yield_on_unexpected_manual_activity = Mock()
    app._sync_operator_control_workflows = Mock()
    app._advance_pending_home_setup_recovery = Mock(return_value=False)
    app._observe_player_save_audit_screen = Mock()
    app._sync_interactive_development_observation = Mock()
    app._observe_no_strategy_frame = Mock()
    app._process_strategy_boundary = Mock()
    app._observe_strategy_gate_boundary = Mock()
    app._accept_pending_terminal_history_handoff = Mock()
    app._cancel_pending_tournament_validation_after_boundary = Mock()
    app._complete_ready_attachment_after_adoption = Mock()
    app._observe_terminal_run_binding = Mock()
    app._sync_strategy_action_gate = Mock()
    app._apply_pending_strategy = Mock()

    def surrender_under_loop_owner(*, action_guard, **_kwargs):
        owners.append(app._active_action_authority_owner)
        assert tuple(hold.hold for hold in app._authority_holds) == (
            AuthorityHold.EXCLUSIVE_VALIDATION,
        )
        assert action_guard()
        return True

    def home_under_loop_owner(*, action_guard, **_kwargs):
        owners.append(app._active_action_authority_owner)
        assert tuple(hold.hold for hold in app._authority_holds) == (
            AuthorityHold.EXCLUSIVE_VALIDATION,
        )
        assert action_guard()
        return True

    with (
        patch("core.app.threading.Thread"),
        patch(
            "core.app.detect_state_and_overlays",
            side_effect=detections,
        ),
        patch(
            "core.app.time.time",
            return_value=running["deadline_at"] + 1,
        ),
        patch(
            "core.app.surrender_run",
            side_effect=surrender_under_loop_owner,
        ) as surrender,
        patch(
            "core.app.return_home_from_game_over",
            side_effect=home_under_loop_owner,
        ) as return_home,
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep"),
    ):
        app.run()

    surrender.assert_called_once()
    return_home.assert_called_once()
    assert owners == [
        AuthorityHold.EXCLUSIVE_VALIDATION,
        AuthorityHold.EXCLUSIVE_VALIDATION,
    ]
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "timed out" in result["reason"]
    assert manager.ctx.data["exclusive_validation_battle"] is False
    app._apply_pending_strategy.assert_called_once()


def test_natural_validation_game_over_finalizes_before_mission_observation(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    running_detection = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        running_detection,
        battle_started=manager.maybe_run_start(running_detection),
    )
    running = _current_receipt(store)
    frame = object()
    game_over = {
        "state": "GAME_OVER",
        "secondary_states": [],
        "overlays": [],
    }
    app._config = SimpleNamespace(wait_on_start=False)
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = Mock()
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._authority_holds = ()
    app._capture_frame = Mock(side_effect=[frame, KeyboardInterrupt])
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._sync_operator_control_workflows = Mock()
    app._apply_pending_strategy = Mock()
    app._handle_primary_states = Mock()
    manager.tick = Mock()
    manager.maybe_run_start = Mock(wraps=manager.maybe_run_start)
    owners = []

    def return_home_under_owner(*, action_guard, **_kwargs):
        owners.append(app._active_action_authority_owner)
        assert action_guard()
        return True

    with (
        patch("core.app.threading.Thread"),
        patch("core.app.detect_state_and_overlays", return_value=game_over),
        patch("core.app.surrender_run") as surrender,
        patch(
            "core.app.return_home_from_game_over",
            side_effect=return_home_under_owner,
        ) as return_home,
        patch("automation.missions.manager.start_activity_scope") as scope,
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep") as sleep,
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        app.run()

    surrender.assert_not_called()
    return_home.assert_called_once()
    assert owners == [AuthorityHold.EXCLUSIVE_VALIDATION]
    app._record_control_observation.assert_not_called()
    app._sync_operator_control_workflows.assert_not_called()
    manager.maybe_run_start.assert_not_called()
    manager.tick.assert_not_called()
    app._handle_primary_states.assert_not_called()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "ended before its battle-only checks completed" in result["reason"]
    assert "fresh Game Over" in result["reason"]
    assert manager.ctx.data["exclusive_validation_battle"] is False
    app._apply_pending_strategy.assert_called_once()
    scope.assert_called_once_with(
        reason="exclusive_validation_game_over_boundary",
        carry_terminal_history_handoff=True,
    )
    assert app._exclusive_validation_terminal_hold is None
    assert app._active_exclusive_validation_request_id is None
    sleep.assert_called_once_with(1.0)
    assert running["request_id"] == result["request_id"]


def test_natural_validation_tournament_entry_finalizes_without_input(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    running = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        running,
        battle_started=manager.maybe_run_start(running),
    )
    app._authority_holds = ()
    app._status_reporter = Mock()
    app._strategy_boundary_confirmed = False
    app._pending_strategy_request = None
    app._apply_pending_strategy = Mock()
    tournament_entry = {
        "state": "TOURNAMENT_SCREEN",
        "secondary_states": [],
    }

    with (
        patch("core.app.surrender_run") as surrender,
        patch("core.app.return_home_from_game_over") as return_home,
        patch("core.app.log"),
        patch("core.app.log_result"),
        patch("automation.missions.manager.start_activity_scope"),
    ):
        assert app._quarantine_exclusive_validation_terminal_finalization(
            tournament_entry
        )

    surrender.assert_not_called()
    return_home.assert_not_called()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "fresh Tournament-entry no-battle evidence" in result["reason"]
    assert app._exclusive_validation_terminal_hold is None
    assert manager.ctx.data["exclusive_validation_battle"] is False


def test_paused_natural_game_over_quarantines_later_running_successor(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    running_detection = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        running_detection,
        battle_started=manager.maybe_run_start(running_detection),
    )
    frame = object()
    game_over = {
        "state": "GAME_OVER",
        "secondary_states": [],
        "overlays": [],
    }
    app._config = SimpleNamespace(wait_on_start=False)
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = Mock()
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._authority_holds = ()
    app._capture_frame = Mock(
        side_effect=[frame, frame, frame, frame, frame, frame]
    )
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._sync_operator_control_workflows = Mock()
    app._apply_pending_strategy = Mock()
    app._handle_primary_states = Mock()
    manager.tick = Mock()
    apply_count = 0
    paused_state = True

    def apply_control():
        nonlocal apply_count, paused_state
        apply_count += 1
        return False

    app._supervisor.apply_control = Mock(side_effect=apply_control)
    events = []
    original_maybe_run_start = manager.maybe_run_start

    def observe_successor(detection):
        battle_started = original_maybe_run_start(detection)
        if battle_started:
            events.append("adopt-successor")
            raise KeyboardInterrupt
        return battle_started

    manager.maybe_run_start = Mock(side_effect=observe_successor)
    original_finalize = manager.finalize_exclusive_validation_game_over_boundary

    def finalize_old():
        original_finalize()
        events.append("finalize-old")

    manager.finalize_exclusive_validation_game_over_boundary = Mock(
        side_effect=finalize_old
    )
    forced_identity = _install_forced_running_identity_once(app, "d" * 64)
    capture_count = 0

    def capture_frame():
        nonlocal capture_count, paused_state
        capture_count += 1
        if capture_count == 3:
            paused_state = False
        if capture_count > 6:
            raise KeyboardInterrupt
        return frame

    app._capture_frame = Mock(side_effect=capture_frame)

    with (
        patch("core.app.threading.Thread"),
        patch(
            "core.app.detect_state_and_overlays",
            side_effect=(
                game_over,
                running_detection,
                running_detection,
                running_detection,
                running_detection,
                running_detection,
            ),
        ),
        patch("core.app.surrender_run") as surrender,
        patch("core.app.return_home_from_game_over") as return_home,
        patch.object(
            type(app._supervisor),
            "is_paused",
            new=property(lambda _self: paused_state),
        ),
        patch("automation.missions.manager.start_activity_scope"),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep") as sleep,
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        app.run()

    surrender.assert_not_called()
    return_home.assert_not_called()
    assert events == ["finalize-old", "adopt-successor"]
    assert forced_identity.call_count >= 1
    # Game Over, paused later RUNNING, and resumed receipt finalization all
    # remain ahead of ordinary workflow and lifecycle observation.
    assert app._record_control_observation.call_count == 2
    assert app._sync_operator_control_workflows.call_count == 2
    assert manager.maybe_run_start.call_count == 1
    manager.tick.assert_not_called()
    app._handle_primary_states.assert_not_called()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "fresh Game Over" in result["reason"]
    assert "later RUNNING screen appeared" in result["reason"]
    assert app._exclusive_validation_terminal_hold is None
    assert manager.ctx.data["exclusive_validation_battle"] is False
    assert sleep.call_count == 4
    assert all(item.args == (1.0,) for item in sleep.call_args_list)


def test_conclusive_surrender_later_running_closes_old_boundary_before_successor(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    running_detection = {
        "state": "RUNNING",
        "secondary_states": [],
        "overlays": [],
    }
    game_over_detection = {
        "state": "GAME_OVER",
        "secondary_states": [],
        "overlays": [],
    }
    app._observe_exclusive_validation_battle_start(
        running_detection,
        battle_started=manager.maybe_run_start(running_detection),
    )
    running = _current_receipt(store)
    frame = object()
    events = []
    owners = []
    app._config = SimpleNamespace(wait_on_start=False)
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = Mock()
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._authority_holds = ()
    app._capture_frame = Mock(
        side_effect=[frame, frame, frame, frame, frame]
    )
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._yield_on_unexpected_manual_activity = Mock()
    app._sync_operator_control_workflows = Mock(
        side_effect=lambda detection, **_kwargs: events.append(
            f"workflow:{detection['state']}"
        )
    )
    app._advance_pending_home_setup_recovery = Mock(return_value=False)
    app._observe_player_save_audit_screen = Mock()
    app._sync_interactive_development_observation = Mock()
    app._observe_no_strategy_frame = Mock()
    app._process_strategy_boundary = Mock()
    app._observe_strategy_gate_boundary = Mock()
    app._accept_pending_terminal_history_handoff = Mock()
    app._cancel_pending_tournament_validation_after_boundary = Mock()
    app._complete_ready_attachment_after_adoption = Mock()
    app._observe_terminal_run_binding = Mock()
    app._sync_strategy_action_gate = Mock()
    app._apply_pending_strategy = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    app._handle_primary_states = Mock()

    original_finish = app._supervisor.finish_exclusive_validation

    def finish_under_loop_owner(*args, **kwargs):
        owners.append(app._active_action_authority_owner)
        events.append("persist-old-result")
        return original_finish(*args, **kwargs)

    app._supervisor.finish_exclusive_validation = Mock(
        side_effect=finish_under_loop_owner
    )
    original_finalize = manager.finalize_exclusive_validation_game_over_boundary
    original_on_game_over = manager.on_game_over

    def finalize_under_loop_owner():
        owners.append(app._active_action_authority_owner)
        original_finalize()
        events.append("finalize-old-boundary")

    def apply_old_game_over_hooks():
        events.append("old-game-over-hooks")
        original_on_game_over()

    manager.finalize_exclusive_validation_game_over_boundary = Mock(
        side_effect=finalize_under_loop_owner
    )
    manager.tick = Mock()
    original_maybe_run_start = manager.maybe_run_start

    def observe_successor(detection):
        battle_started = original_maybe_run_start(detection)
        if battle_started:
            events.append("adopt-successor")
            raise KeyboardInterrupt
        return battle_started

    manager.maybe_run_start = Mock(side_effect=observe_successor)
    forced_identity = _install_forced_running_identity_once(app, "d" * 64)

    def surrender_under_loop_owner(*, action_guard, **_kwargs):
        owners.append(app._active_action_authority_owner)
        events.append("surrender-validation")
        assert action_guard()
        return True

    def failed_home_under_loop_owner(*, action_guard, **_kwargs):
        owners.append(app._active_action_authority_owner)
        events.append("home-cleanup-failed")
        assert action_guard()
        return False

    with (
        patch("core.app.threading.Thread"),
        patch(
            "core.app.detect_state_and_overlays",
            side_effect=(
                running_detection,
                game_over_detection,
                running_detection,
                running_detection,
                running_detection,
            ),
        ),
        patch(
            "core.app.time.time",
            return_value=running["deadline_at"] + 1,
        ),
        patch(
            "core.app.surrender_run",
            side_effect=surrender_under_loop_owner,
        ) as surrender,
        patch(
            "core.app.return_home_from_game_over",
            side_effect=failed_home_under_loop_owner,
        ) as return_home,
        patch(
            "automation.missions.manager.start_activity_scope",
            side_effect=lambda **_kwargs: events.append("rotate-scope"),
        ) as start_scope,
        patch.object(
            manager,
            "on_game_over",
            side_effect=apply_old_game_over_hooks,
        ) as on_game_over,
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep") as sleep,
    ):
        app.run()

    surrender.assert_called_once()
    return_home.assert_called_once()
    assert forced_identity.call_count >= 1
    assert owners == [AuthorityHold.EXCLUSIVE_VALIDATION] * 4
    assert events == [
        "workflow:RUNNING",
        "surrender-validation",
        "home-cleanup-failed",
        "persist-old-result",
        "old-game-over-hooks",
        "rotate-scope",
        "finalize-old-boundary",
        "workflow:RUNNING",
        "workflow:RUNNING",
        "adopt-successor",
    ]
    # The Game Over and first later-RUNNING frames were both quarantined before
    # operator-workflow synchronization or successor lifecycle observation.
    assert app._sync_operator_control_workflows.call_count == 3
    assert manager.maybe_run_start.call_count == 2
    assert app._advance_pending_home_setup_recovery.call_count == 2
    manager.tick.assert_not_called()
    app._handle_primary_states.assert_not_called()
    app._supervisor.finish_exclusive_validation.assert_called_once()
    manager.finalize_exclusive_validation_game_over_boundary.assert_called_once()
    on_game_over.assert_called_once()
    start_scope.assert_called_once_with(
        reason="exclusive_validation_game_over_boundary",
        carry_terminal_history_handoff=True,
    )
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "timed out" in result["reason"]
    assert "verified NEW_BATTLE Home was not reached" in result["reason"]
    assert "later RUNNING screen appeared" in result["reason"]
    assert manager.ctx.data["exclusive_validation_battle"] is False
    assert manager.run_initialization_pending()
    app._apply_pending_strategy.assert_called_once()
    assert app._exclusive_validation_terminal_hold is None
    assert not app._exclusive_validation_in_progress()
    assert sleep.call_count == 2
    assert all(item.args == (1.0,) for item in sleep.call_args_list)


@pytest.mark.parametrize(
    ("detection", "reason_fragment", "expected_outcome"),
    (
        (
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "RESUME_BATTLE",
                "secondary_states": [],
            },
            "later resumable Home screen",
            "failed",
        ),
        (
            {"state": "TOURNAMENT_RESULTS", "secondary_states": []},
            "later Tournament Results",
            "failed",
        ),
        (
            {"state": "WORKSHOP", "secondary_states": []},
            "later Workshop no-battle evidence",
            "ready",
        ),
        (
            {"state": "TOURNAMENT_SCREEN", "secondary_states": []},
            "later Tournament-entry no-battle evidence",
            "ready",
        ),
    ),
)
def test_proven_game_over_departure_releases_cleanup_without_input(
    tmp_path,
    detection,
    reason_fragment,
    expected_outcome,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    running_detection = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        running_detection,
        battle_started=manager.maybe_run_start(running_detection),
    )
    receipt = _current_receipt(store)
    cleanup = app._supervisor.begin_exclusive_validation_cleanup(
        receipt["request_id"],
        outcome="ready",
        reason="checks passed",
    )
    assert cleanup is not None
    app._stage_exclusive_validation_terminal_result(
        cleanup,
        mode="game_over_observed",
        outcome="ready",
        reason="checks passed",
    )
    app._authority_holds = ()
    app._status_reporter = Mock()
    app._strategy_boundary_confirmed = False
    app._pending_strategy_request = None

    with (
        patch("core.app.surrender_run") as surrender,
        patch("core.app.return_home_from_game_over") as return_home,
        patch("core.app.log"),
        patch("core.app.log_result"),
        patch("automation.missions.manager.start_activity_scope"),
    ):
        assert app._quarantine_exclusive_validation_terminal_finalization(
            detection
        )

    surrender.assert_not_called()
    return_home.assert_not_called()
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == expected_outcome
    assert reason_fragment in result["reason"]
    assert app._exclusive_validation_terminal_hold is None
    assert manager.ctx.data["exclusive_validation_battle"] is False


def test_failed_surrender_result_write_retries_without_further_input(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    running_detection = {
        "state": "RUNNING",
        "secondary_states": [],
        "overlays": [],
    }
    app._observe_exclusive_validation_battle_start(
        running_detection,
        battle_started=manager.maybe_run_start(running_detection),
    )
    running = _current_receipt(store)
    frame = object()
    finish_attempts = 0
    finish_owners = []
    original_finish = app._supervisor.finish_exclusive_validation

    def flaky_finish(*args, **kwargs):
        nonlocal finish_attempts
        finish_attempts += 1
        finish_owners.append(app._active_action_authority_owner)
        if finish_attempts == 1:
            return None
        return original_finish(*args, **kwargs)

    app._supervisor.finish_exclusive_validation = Mock(
        side_effect=flaky_finish
    )
    app._config = SimpleNamespace(wait_on_start=False)
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = Mock()
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._authority_holds = ()
    app._capture_frame = Mock(
        side_effect=[frame, frame, frame, KeyboardInterrupt]
    )
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._yield_on_unexpected_manual_activity = Mock()
    app._sync_operator_control_workflows = Mock()
    app._advance_pending_home_setup_recovery = Mock(return_value=False)
    app._observe_player_save_audit_screen = Mock()
    app._sync_interactive_development_observation = Mock()
    app._observe_no_strategy_frame = Mock()
    app._process_strategy_boundary = Mock()
    app._observe_strategy_gate_boundary = Mock()
    app._accept_pending_terminal_history_handoff = Mock()
    app._cancel_pending_tournament_validation_after_boundary = Mock()
    app._complete_ready_attachment_after_adoption = Mock()
    app._observe_terminal_run_binding = Mock()
    app._sync_strategy_action_gate = Mock()
    app._apply_pending_strategy = Mock()
    app._battle_activation_tracker = Mock()
    app._battle_activation_tracker.observe.return_value = []
    app._battle_activation_tracker.drain_evidence_captures.return_value = []
    app._perk_timeline_enabled = Mock(return_value=False)
    app._sync_floating_gem_tapper = Mock()
    app._handle_primary_states = Mock()
    manager.tick = Mock()

    def inconclusive_surrender(*, action_guard, **_kwargs):
        assert app._active_action_authority_owner is (
            AuthorityHold.EXCLUSIVE_VALIDATION
        )
        assert action_guard()
        return False

    with (
        patch("core.app.threading.Thread"),
        patch(
            "core.app.detect_state_and_overlays",
            side_effect=(
                running_detection,
                running_detection,
                running_detection,
            ),
        ),
        patch(
            "core.app.time.time",
            return_value=running["deadline_at"] + 1,
        ),
        patch(
            "core.app.surrender_run",
            side_effect=inconclusive_surrender,
        ) as surrender,
        patch("core.app.return_home_from_game_over") as return_home,
        patch.object(
            manager,
            "on_game_over",
            wraps=manager.on_game_over,
        ) as on_game_over,
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep"),
    ):
        app.run()

    surrender.assert_called_once()
    return_home.assert_not_called()
    on_game_over.assert_not_called()
    assert finish_attempts == 2
    assert finish_owners == [AuthorityHold.EXCLUSIVE_VALIDATION] * 2
    result = _current_receipt(store)
    assert result["status"] == "result"
    assert result["outcome"] == "failed"
    assert "no retry or further battle action" in result["reason"]
    assert manager.ctx.data["exclusive_validation_battle"] is False
    assert manager.ctx.data["startup_gates_deferred"] is True
    assert manager.ctx.data["skip_attached_checks"] is True
    assert not manager.run_initialization_pending()
    assert not manager.session_preflight_pending()
    app._apply_pending_strategy.assert_not_called()
    assert app._exclusive_validation_terminal_hold is None
    assert app._exclusive_validation_passive_battle_hold == result[
        "request_id"
    ]
    assert tuple(hold.hold for hold in app._authority_holds) == (
        AuthorityHold.EXCLUSIVE_OWNERSHIP,
    )
    assert not app._runtime_action_guard()
    manager.tick.assert_not_called()
    app._handle_primary_states.assert_not_called()
    assert app._sync_operator_control_workflows.call_count == 1

    assert not app._observe_exclusive_validation_passive_battle_boundary(
        {"state": "GAME_OVER"}
    )
    assert app._exclusive_validation_passive_battle_hold is None
    manager.maybe_run_start({"state": "GAME_OVER"})
    assert manager.ctx.data["startup_gates_deferred"] is False
    assert manager.ctx.data["skip_attached_checks"] is False
    manager.maybe_run_start(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        }
    )
    assert manager.maybe_run_start(running_detection)
    assert manager.run_initialization_pending()


def test_dispatched_validation_owns_free_ticket_recovery_with_exact_receipt(
    tmp_path,
):
    app, store, _manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )

    receipt = _current_receipt(store)
    owner = (
        "exclusive_validation",
        f"exclusive-validation:{receipt['request_id']}",
    )
    assert app._free_ticket_recovery_owner() == owner
    holds, ready = app._blocking_recovery_handoff(owner)
    assert ready
    assert tuple(item.hold for item in holds) == (
        AuthorityHold.EXCLUSIVE_VALIDATION,
    )
    app._update_action_authority(
        detection={"state": "FREE_TICKET"},
        holds=holds,
    )
    assert app._action_decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=AuthorityHold.EXCLUSIVE_VALIDATION,
    ).allowed
    assert not app._action_decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=AuthorityHold.BLOCKING_MODAL_RECOVERY,
    ).allowed


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
        assert app._advance_exclusive_validation(tournament_detection)
    surrender.assert_not_called()
    result = _current_receipt(store)
    assert result["outcome"] == "failed"
    assert "refusing Surrender" in result["reason"]


@pytest.mark.parametrize(
    ("detection", "retained"),
    (
        ({"state": "UNKNOWN"}, True),
        (
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "UNKNOWN",
            },
            True,
        ),
        ({"state": "RUNNING", "secondary_states": []}, True),
        (
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "RESUME_BATTLE",
            },
            True,
        ),
        ({"state": "GAME_OVER"}, False),
        ({"state": "TOURNAMENT_RESULTS"}, False),
        ({"state": "WORKSHOP"}, False),
        ({"state": "TOURNAMENT_SCREEN"}, False),
        (
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "NEW_BATTLE",
            },
            False,
        ),
    ),
)
def test_passive_validation_battle_releases_only_at_real_boundary(
    detection,
    retained,
):
    app = App.__new__(App)
    app._exclusive_validation_passive_battle_hold = "validation-result"
    app._exclusive_validation_passive_battle_scope_id = "owned-scope"

    with patch("core.app.log"):
        assert (
            app._observe_exclusive_validation_passive_battle_boundary(
                detection
            )
            is retained
        )

    assert bool(app._exclusive_validation_passive_battle_hold) is retained
    assert (
        bool(app._exclusive_validation_passive_battle_scope_id) is retained
    )


@pytest.mark.parametrize("state", ("WORKSHOP", "TOURNAMENT_SCREEN"))
def test_passive_no_battle_release_rearms_real_successor_lifecycle(state):
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(None, strategy)
    manager.start()
    assert manager.maybe_run_start({"state": "RUNNING"})
    manager.set_exclusive_validation_battle(True)
    manager.release_exclusive_validation_battle_without_boundary()

    app = App.__new__(App)
    app._mission_mgr = manager
    app._exclusive_validation_passive_battle_hold = "validation-result"
    app._exclusive_validation_passive_battle_scope_id = "owned-scope"

    with patch("core.app.log"):
        assert not app._observe_exclusive_validation_passive_battle_boundary(
            {"state": state}
        )

    assert manager.maybe_run_start({"state": "RUNNING"})
    assert manager.run_initialization_pending()


def test_tournament_entry_applies_pending_strategy_before_successor_adoption():
    strategy = get_strategy("tournament")
    replacement = get_strategy("farm_t18")
    assert strategy is not None
    assert replacement is not None
    manager = MissionManager(None, strategy)
    manager.start()
    assert manager.maybe_run_start({"state": "RUNNING"})
    manager.set_exclusive_validation_battle(True)
    manager.release_exclusive_validation_battle_without_boundary()

    app = App.__new__(App)
    app._mission_mgr = manager
    app._exclusive_validation_passive_battle_hold = "validation-result"
    app._exclusive_validation_passive_battle_scope_id = "owned-scope"
    app._exclusive_validation_ownership_hold = False
    app._strategy_boundary_confirmed = False
    app._pending_strategy_request = (
        "farm_t18",
        "replacement-request",
        "next_boundary",
    )
    app._exclusive_validation_blocks_strategy_application = Mock(
        return_value=False
    )

    def apply_replacement():
        manager.replace_strategy_at_boundary(replacement)
        app._pending_strategy_request = None
        return True

    app._apply_pending_strategy = Mock(side_effect=apply_replacement)
    tournament_entry = {"state": "TOURNAMENT_SCREEN"}

    with patch("core.app.log"):
        assert not app._observe_exclusive_validation_passive_battle_boundary(
            tournament_entry
        )
        app._process_strategy_boundary(tournament_entry)

    app._apply_pending_strategy.assert_called_once_with()
    assert manager.strategy is replacement
    assert manager.maybe_run_start({"state": "RUNNING"})


def test_tournament_identity_releases_validation_mode_without_boundary_hooks(
    tmp_path,
):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    ordinary = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        ordinary,
        battle_started=manager.maybe_run_start(ordinary),
    )
    tournament = {
        "state": "RUNNING",
        "secondary_states": ["TOURNAMENT"],
    }
    app._update_action_authority(
        detection=tournament,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "validation identity is being retired without input",
            ),
        ),
    )
    app._status_reporter = Mock()
    app._apply_pending_strategy = Mock()

    with (
        patch("core.app.surrender_run") as surrender,
        patch.object(
            manager,
            "on_game_over",
            wraps=manager.on_game_over,
        ) as on_game_over,
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        assert app._advance_owned_exclusive_validation(tournament)
        result = _current_receipt(store)
        assert result["status"] == "result"
        assert app._dispatch_exclusive_validation_game_over(tournament)

    surrender.assert_not_called()
    on_game_over.assert_not_called()
    assert result["outcome"] == "failed"
    assert "Tournament identity appeared" in result["reason"]
    assert manager.ctx.data["exclusive_validation_battle"] is False
    assert manager.ctx.data["startup_gates_deferred"] is True
    assert manager.ctx.data["skip_attached_checks"] is True
    app._apply_pending_strategy.assert_not_called()
    assert app._exclusive_validation_terminal_hold is None


def test_validation_claim_waits_for_forced_save_identity(tmp_path):
    app, store, manager = _app_for_pending_validation(tmp_path)
    app._active_round_identity_fingerprint = None
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )

    ordinary = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        ordinary,
        battle_started=manager.maybe_run_start(ordinary),
    )

    assert _current_receipt(store)["status"] == "claimed"
    assert app._active_exclusive_validation_battle_identity is None

    app._active_round_identity_fingerprint = VALIDATION_BATTLE_IDENTITY
    app._observe_exclusive_validation_battle_start(
        ordinary,
        battle_started=False,
    )

    assert _current_receipt(store)["status"] == "running"
    assert (
        app._active_exclusive_validation_battle_identity
        == VALIDATION_BATTLE_IDENTITY
    )


def test_changed_save_identity_never_authorizes_validation_surrender(tmp_path):
    app, store, manager = _app_for_pending_validation(tmp_path)
    with (
        patch("core.app.tap_verified_new_battle", return_value=True),
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._maybe_start_exclusive_validation(
            home_control=HomeBattleControl.NEW_BATTLE
        )
    ordinary = {"state": "RUNNING", "secondary_states": []}
    app._observe_exclusive_validation_battle_start(
        ordinary,
        battle_started=manager.maybe_run_start(ordinary),
    )
    app._active_round_identity_fingerprint = "f" * 64

    with (
        patch("core.app.surrender_run") as surrender,
        patch("core.app.log"),
    ):
        assert app._advance_exclusive_validation(ordinary)

    surrender.assert_not_called()
    result = _current_receipt(store)
    assert result["outcome"] == "failed"
    assert "save identity no longer matches" in result["reason"]


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


def test_strategy_replacement_preserves_consumed_validation_activity_boundary():
    strategy = get_strategy("tournament")
    replacement = get_strategy("farm_t18")
    assert strategy is not None
    assert replacement is not None
    manager = MissionManager(None, strategy)
    manager.start()
    assert manager.maybe_run_start({"state": "RUNNING"})
    manager.set_exclusive_validation_battle(True)
    home = {
        "state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
    }

    with patch(
        "automation.missions.manager.start_activity_scope"
    ) as start_scope:
        manager.finalize_exclusive_validation_game_over_boundary()
        manager.replace_strategy_at_boundary(replacement)
        assert not manager.maybe_run_start(home)

    start_scope.assert_called_once_with(
        reason="exclusive_validation_game_over_boundary",
        carry_terminal_history_handoff=True,
    )


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
    assert app._exclusive_validation_launch_in_progress()
    frame = object()
    home = {"state": "HOME_SCREEN", "secondary_states": []}
    app._update_action_authority(
        detection=home,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "operator-confirmed Tournament launch owns the screen",
            ),
        ),
    )

    def dispatch_under_typed_owner(_frame, *, action_guard):
        assert action_guard()
        assert not app._runtime_action_guard(
            action_class=RuntimeActionClass.LIFECYCLE_ACTION,
            owner=AuthorityHold.SESSION_PREFLIGHT,
        )
        return TournamentLaunchDispatch(
            True,
            "verified Tournament BATTLE was dispatched",
        )

    with (
        patch(
            "core.app.dispatch_tournament_launch",
            side_effect=dispatch_under_typed_owner,
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
    recovery_owner = (
        "exclusive_validation_launch",
        f"exclusive-validation-launch:{ready['request_id']}",
    )
    assert app._free_ticket_recovery_owner() == recovery_owner
    recovery_holds, recovery_ready = app._blocking_recovery_handoff(
        recovery_owner
    )
    assert recovery_ready
    assert tuple(item.hold for item in recovery_holds) == (
        AuthorityHold.EXCLUSIVE_VALIDATION,
    )

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


def test_uncertain_confirmed_launch_pauses_and_never_replays(tmp_path):
    app, store, _manager, ready, _definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    home = {
        "state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
        "secondary_states": [],
    }
    app._update_action_authority(
        detection=home,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "confirmed Tournament launch owns its final input",
            ),
        ),
    )
    uncertain = TournamentLaunchDispatch(
        False,
        "verified Tournament BATTLE input had an uncertain outcome",
        uncertain=True,
    )

    with (
        patch(
            "core.app.dispatch_tournament_launch",
            return_value=uncertain,
        ) as dispatch,
        patch("core.app.log"),
        patch("core.app.log_action_intent"),
    ):
        assert app._advance_exclusive_validation_launch(
            object(),
            home,
            battle_started=False,
        )
        assert not app._advance_exclusive_validation_launch(
            object(),
            home,
            battle_started=False,
        )

    dispatch.assert_called_once()
    launch = _current_receipt(store)["launch"]
    assert launch["status"] == "claimed"
    assert app._exclusive_validation_launch_dispatch_hold == ready[
        "request_id"
    ]
    assert app._supervisor.control_state == "PAUSED"


def test_confirmed_launch_main_loop_installs_matching_typed_owner(tmp_path):
    app, store, manager, ready, _definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    frame = object()
    home = {
        "state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
        "secondary_states": [],
        "overlays": [],
    }
    app._config = SimpleNamespace(wait_on_start=False)
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = Mock()
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._authority_holds = ()
    app._capture_frame = Mock(side_effect=[frame, KeyboardInterrupt])
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._yield_on_unexpected_manual_activity = Mock()
    app._sync_operator_control_workflows = Mock()
    app._advance_pending_home_setup_recovery = Mock(return_value=False)
    app._observe_player_save_audit_screen = Mock()
    app._sync_interactive_development_observation = Mock()
    app._observe_no_strategy_frame = Mock()
    app._process_strategy_boundary = Mock()
    app._observe_strategy_gate_boundary = Mock()
    app._accept_pending_terminal_history_handoff = Mock()
    app._cancel_pending_tournament_validation_after_boundary = Mock()
    app._complete_ready_attachment_after_adoption = Mock()
    app._observe_terminal_run_binding = Mock()
    app._observe_exclusive_validation_battle_start = Mock()
    app._sync_strategy_action_gate = Mock()

    def dispatch_under_loop_owner(_frame, *, action_guard):
        assert tuple(hold.hold for hold in app._authority_holds) == (
            AuthorityHold.EXCLUSIVE_VALIDATION,
        )
        assert app._action_decision(
            RuntimeActionClass.LIFECYCLE_ACTION,
            owner=AuthorityHold.EXCLUSIVE_VALIDATION,
        ).allowed
        assert action_guard()
        return TournamentLaunchDispatch(
            True,
            "verified Tournament BATTLE was dispatched",
        )

    with (
        patch("core.app.threading.Thread"),
        patch("core.app.detect_state_and_overlays", return_value=home),
        patch(
            "core.app.dispatch_tournament_launch",
            side_effect=dispatch_under_loop_owner,
        ) as dispatch,
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep"),
    ):
        app.run()

    dispatch.assert_called_once()
    launch = _current_receipt(store)["launch"]
    assert launch["status"] == "claimed"
    assert launch["owner"] == app._supervisor.current_exclusive_validation_owner()
    assert manager.ctx.data["exclusive_validation_battle"] is False


@pytest.mark.parametrize(
    "launch_status",
    ("awaiting_operator", "requested", "claimed"),
)
def test_tournament_start_proof_survives_pause_and_transient_result_write(
    tmp_path,
    launch_status,
):
    app, store, manager, ready, definition = _app_for_ready_launch(tmp_path)
    if launch_status in {"requested", "claimed"}:
        store.resolve_exclusive_validation_launch(
            ready["request_id"],
            "start",
            source="test",
        )
        app._supervisor.apply_control()
    if launch_status == "claimed":
        claimed = app._supervisor.claim_exclusive_validation_launch(
            ready["request_id"],
            configuration_fingerprint=definition.configuration_fingerprint,
        )
        assert claimed is not None
        app._active_exclusive_validation_launch_request_id = ready[
            "request_id"
        ]
    store.set_state("PAUSED", source="test")

    frame = object()
    tournament = {
        "state": "RUNNING",
        "menu": "ATTACK_MENU",
        "secondary_states": ["TOURNAMENT"],
        "overlays": [],
    }
    app._config = SimpleNamespace(
        wait_on_start=False,
        strategy_name="tournament",
    )
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = Mock()
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._authority_holds = ()
    app._capture_frame = Mock(
        side_effect=[frame, frame, frame, frame, frame, KeyboardInterrupt]
    )
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._yield_on_unexpected_manual_activity = Mock()
    app._sync_operator_control_workflows = Mock()
    app._operator_workflow_authority_hold = Mock(return_value=None)
    app._advance_pending_home_setup_recovery = Mock(return_value=False)
    app._observe_player_save_audit_screen = Mock()
    app._sync_interactive_development_observation = Mock()
    app._observe_no_strategy_frame = Mock()
    app._process_strategy_boundary = Mock()
    app._observe_strategy_gate_boundary = Mock()
    app._clear_terminal_home_continuation = Mock()
    app._accept_pending_terminal_history_handoff = Mock()
    app._cancel_pending_tournament_validation_after_boundary = Mock()
    app._complete_started_battle_workflow = Mock()
    app._battle_activation_tracker = Mock()
    app._battle_activation_tracker.observe.return_value = []
    app._battle_activation_tracker.drain_evidence_captures.return_value = []
    app._perk_timeline_enabled = Mock(return_value=False)
    app._reset_player_save_audit_perk_mapping_evidence = Mock()
    app._bind_started_battle_player_save_preflight = Mock()
    app._complete_ready_attachment_after_adoption = Mock()
    app._observe_terminal_run_binding = Mock()
    app._observe_exclusive_validation_battle_start = Mock()
    app._claim_proactive_gate_waivers = Mock(return_value=False)
    app._sync_strategy_action_gate = Mock()
    app._maybe_log_steady_run_entry = Mock()
    app._observe_player_save_audit_visual_events = Mock()
    app._emit_event_mission_warnings = Mock()
    app._sync_floating_gem_tapper = Mock()
    app._handle_primary_states = Mock()
    manager.tick = Mock()
    forced_identity = _install_forced_running_identity_once(
        app,
        "d" * 64,
    )

    if launch_status == "claimed":
        persistence_method = "finish_exclusive_validation_launch"
    else:
        persistence_method = "record_manual_exclusive_validation_launch"
    original_persist = getattr(app._supervisor, persistence_method)
    persistence_calls = 0

    def transient_persist(*args, **kwargs):
        nonlocal persistence_calls
        persistence_calls += 1
        if persistence_calls == 1:
            return None
        return original_persist(*args, **kwargs)

    setattr(
        app._supervisor,
        persistence_method,
        Mock(side_effect=transient_persist),
    )
    resumed = False

    def sleep_and_resume(_seconds):
        nonlocal resumed
        if not resumed:
            resumed = True
            store.set_state("RUNNING", source="test-resume")

    with (
        patch("core.app.threading.Thread"),
        patch("core.app.detect_state_and_overlays", return_value=tournament),
        patch("core.app.detect_wave_number_from_image", return_value=(1, 99.0)),
        patch("core.app.dispatch_tournament_launch") as dispatch,
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep", side_effect=sleep_and_resume),
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        app.run()

    dispatch.assert_not_called()
    assert forced_identity.call_count >= 1
    assert persistence_calls == 2
    result = _current_receipt(store)
    assert result["launch"]["status"] == "started"
    if launch_status == "claimed":
        persist = app._supervisor.finish_exclusive_validation_launch
        assert all(
            call.kwargs["outcome"] == "started"
            for call in persist.call_args_list
        )
    else:
        assert result["launch"]["started_by"] == "manual_observation"
    assert app._exclusive_validation_launch_start_hold is None
    assert manager.run_initialization_pending()
    manager.tick.assert_called_once()
    assert manager.tick.call_args.kwargs == {"strategy_only": True}
    app._handle_primary_states.assert_not_called()


def test_active_strategy_request_waits_for_consumed_launch_start_proof(
    tmp_path,
):
    app, store, manager, ready, definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    claimed = app._supervisor.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
    )
    assert claimed is not None
    app._active_exclusive_validation_launch_request_id = ready["request_id"]
    tournament = {
        "state": "RUNNING",
        "secondary_states": ["TOURNAMENT"],
    }
    assert manager.maybe_run_start(tournament)
    assert app._retain_exclusive_validation_launch_start_proof(
        tournament,
        battle_started=True,
    )
    replacement = store.set_strategy(
        "farm_t18",
        apply_mode="active_battle",
        source="test",
    )
    app._pending_strategy_request = (
        "farm_t18",
        replacement["strategy_request_id"],
        "active_battle",
    )
    manager.adopt_strategy_for_active_battle = Mock()

    app._process_strategy_boundary(tournament)

    manager.adopt_strategy_for_active_battle.assert_not_called()
    assert app._exclusive_validation_launch_start_hold == ready["request_id"]
    assert _current_receipt(store)["launch"]["status"] == "claimed"

    app._update_action_authority(
        detection=tournament,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "persist observed launch before Strategy replacement",
            ),
        ),
    )
    with (
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        assert not app._advance_owned_exclusive_validation_launch(
            object(),
            tournament,
            battle_started=False,
        )

    assert _current_receipt(store)["launch"]["status"] == "started"
    assert app._exclusive_validation_launch_start_hold is None
    assert app._pending_strategy_request is not None

    def complete_replacement(*_args, **_kwargs):
        app._pending_strategy_request = None

    app._complete_strategy_application = Mock(
        side_effect=complete_replacement
    )
    app._process_strategy_boundary(tournament)

    manager.adopt_strategy_for_active_battle.assert_called_once()
    app._complete_strategy_application.assert_called_once()
    assert app._pending_strategy_request is None


def test_claimed_launch_without_fresh_boundary_becomes_passive(tmp_path):
    app, store, manager, ready, definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    claimed = app._supervisor.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
    )
    assert claimed is not None
    app._active_exclusive_validation_launch_request_id = ready["request_id"]
    tournament = {
        "state": "RUNNING",
        "secondary_states": ["TOURNAMENT"],
    }
    app._update_action_authority(
        detection=tournament,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "resolve ambiguous claimed Tournament launch",
            ),
        ),
    )

    with (
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        assert app._advance_owned_exclusive_validation_launch(
            object(),
            tournament,
            battle_started=False,
        )

    result = _current_receipt(store)
    assert result["launch"]["status"] == "failed"
    assert "without a fresh Tournament battle boundary" in result[
        "launch"
    ]["reason"]
    assert app._exclusive_validation_passive_battle_hold == ready["request_id"]
    assert manager.ctx.data["startup_gates_deferred"] is True
    assert manager.ctx.data["skip_attached_checks"] is True


@pytest.mark.parametrize(
    ("detection", "passive_expected"),
    (
        (
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "RESUME_BATTLE",
            },
            True,
        ),
        ({"state": "UNKNOWN"}, True),
        (
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "UNKNOWN",
            },
            True,
        ),
        (
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "NEW_BATTLE",
            },
            False,
        ),
        ({"state": "TOURNAMENT_SCREEN", "secondary_states": []}, False),
    ),
)
def test_claimed_launch_timeout_retains_only_ambiguous_battle(
    tmp_path,
    detection,
    passive_expected,
):
    app, store, manager, ready, definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    claimed = app._supervisor.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
    )
    assert claimed is not None
    app._active_exclusive_validation_launch_request_id = ready["request_id"]
    app._update_action_authority(
        detection=detection,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "resolve bounded confirmed-launch timeout",
            ),
        ),
    )

    with (
        patch(
            "core.app.time.time",
            return_value=float(claimed["launch"]["deadline_at"]) + 1,
        ),
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        assert app._advance_owned_exclusive_validation_launch(
            object(),
            detection,
            battle_started=False,
        )

    result = _current_receipt(store)
    assert result["launch"]["status"] == "failed"
    assert "bounded launch timeout" in result["launch"]["reason"]
    assert bool(
        getattr(app, "_exclusive_validation_passive_battle_hold", None)
    ) is passive_expected
    assert manager.ctx.data["startup_gates_deferred"] is passive_expected
    assert manager.ctx.data["skip_attached_checks"] is passive_expected


@pytest.mark.parametrize(
    "detection",
    (
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "RESUME_BATTLE",
        },
        {"state": "UNKNOWN"},
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "UNKNOWN",
        },
    ),
)
def test_superseded_claimed_launch_retains_ambiguous_battle(
    tmp_path,
    detection,
):
    app, store, manager, ready, definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    claimed = app._supervisor.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
    )
    assert claimed is not None
    app._active_exclusive_validation_launch_request_id = ready["request_id"]
    replacement = store.set_strategy("farm_t18", source="test")
    assert replacement["strategy_request_id"] != ready[
        "strategy_request_id"
    ]
    app._supervisor.apply_control()
    replacement_strategy = get_strategy("farm_t18")
    assert replacement_strategy is not None
    manager.replace_strategy_at_boundary(replacement_strategy)
    app._update_action_authority(
        detection=detection,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "retire superseded confirmed launch",
            ),
        ),
    )

    with (
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        assert app._advance_owned_exclusive_validation_launch(
            object(),
            detection,
            battle_started=False,
        )

    old = store.status()["exclusive_validation"]["receipts"][
        ready["request_id"]
    ]
    assert old["launch"]["status"] == "failed"
    assert "superseded" in old["launch"]["reason"]
    assert app._exclusive_validation_passive_battle_hold == ready[
        "request_id"
    ]
    assert manager.ctx.data["startup_gates_deferred"] is True
    assert manager.ctx.data["skip_attached_checks"] is True


def test_transient_confirmed_launch_ownership_reread_stays_suppressive(
    tmp_path,
):
    app, store, _manager, ready, definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    claimed = app._supervisor.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
    )
    assert claimed is not None
    app._active_exclusive_validation_launch_request_id = ready["request_id"]

    with patch.object(
        app._supervisor._control_store,
        "status",
        side_effect=ControlDirectiveError("transient launch ownership reread"),
    ):
        retained = app._reconcile_exclusive_validation_launch()

    assert retained is not None
    assert retained["launch"]["status"] == "claimed"
    assert app._active_exclusive_validation_launch_request_id == ready[
        "request_id"
    ]
    assert app._exclusive_validation_ownership_hold is True
    assert not app._exclusive_validation_launch_in_progress()
    assert app._exclusive_validation_blocks_target_handoff()

    recovered = app._reconcile_exclusive_validation_launch()
    assert recovered is not None
    assert app._exclusive_validation_ownership_hold is False
    assert app._exclusive_validation_launch_in_progress()


def test_claimed_launch_superseded_by_strategy_retires_under_exact_owner(
    tmp_path,
):
    app, store, manager, ready, definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    claimed = app._supervisor.claim_exclusive_validation_launch(
        ready["request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
    )
    assert claimed is not None
    app._active_exclusive_validation_launch_request_id = ready["request_id"]
    replacement = store.set_strategy("farm_t18", source="test")
    assert replacement["strategy_request_id"] != ready[
        "strategy_request_id"
    ]
    frame = object()
    home = {
        "state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
        "secondary_states": [],
        "overlays": [],
    }
    app._config = SimpleNamespace(
        wait_on_start=False,
        strategy_name="tournament",
    )
    app._adb_connection_coordinator = Mock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = Mock()
    app._state_tracker = Mock()
    app._event_mission_tracker = Mock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._authority_holds = ()
    app._capture_frame = Mock(side_effect=[frame, KeyboardInterrupt])
    app._sync_interactive_development_control_boundary = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._yield_on_unexpected_manual_activity = Mock()
    app._sync_operator_control_workflows = Mock()
    app._advance_pending_home_setup_recovery = Mock(return_value=False)
    app._observe_player_save_audit_screen = Mock()
    app._sync_interactive_development_observation = Mock()
    app._observe_no_strategy_frame = Mock()
    app._observe_strategy_gate_boundary = Mock()
    app._accept_pending_terminal_history_handoff = Mock()
    app._cancel_pending_tournament_validation_after_boundary = Mock()
    app._complete_ready_attachment_after_adoption = Mock()
    app._observe_terminal_run_binding = Mock()
    app._sync_strategy_action_gate = Mock()
    app._handle_primary_states = Mock()
    manager.tick = Mock()
    finish_owners = []
    original_finish = app._supervisor.finish_exclusive_validation_launch

    def finish_under_exact_owner(*args, **kwargs):
        finish_owners.append(app._active_action_authority_owner)
        return original_finish(*args, **kwargs)

    app._supervisor.finish_exclusive_validation_launch = Mock(
        side_effect=finish_under_exact_owner
    )

    with (
        patch("core.app.threading.Thread"),
        patch("core.app.detect_state_and_overlays", return_value=home),
        patch("core.app.dispatch_tournament_launch") as dispatch,
        patch("automation.missions.manager.start_activity_scope"),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.time.sleep"),
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        app.run()

    dispatch.assert_not_called()
    app._supervisor.finish_exclusive_validation_launch.assert_called_once()
    finish_call = app._supervisor.finish_exclusive_validation_launch.call_args
    assert finish_call.args == (ready["request_id"],)
    assert finish_call.kwargs["outcome"] == "failed"
    assert "superseded" in finish_call.kwargs["reason"]
    assert finish_owners == [AuthorityHold.EXCLUSIVE_VALIDATION]
    old = store.status()["exclusive_validation"]["receipts"][
        ready["request_id"]
    ]
    assert old["launch"]["status"] == "failed"
    assert app._active_exclusive_validation_launch_request_id is None
    assert manager.strategy is not None
    assert manager.strategy.name == "farm_t18"
    manager.tick.assert_not_called()
    app._handle_primary_states.assert_not_called()


def test_confirmed_launch_waits_for_local_validation_finalization(tmp_path):
    app, store, manager, ready, _definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    result = _current_receipt(store)
    app._active_exclusive_validation_request_id = ready["request_id"]
    app._exclusive_validation_terminal_hold = ready["request_id"]
    app._exclusive_validation_terminal_mode = "home_cleanup"
    app._exclusive_validation_terminal_announced = ready["request_id"]
    app._status_reporter = Mock()
    app._apply_pending_strategy = Mock()
    manager.on_game_over = Mock()
    home = {
        "state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
        "secondary_states": [],
    }
    app._update_action_authority(
        detection=home,
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "validation finalization precedes its successor launch",
            ),
        ),
    )
    app._advance_exclusive_validation_launch = Mock(return_value=True)

    assert not app._advance_owned_exclusive_validation_launch(
        object(),
        home,
        battle_started=False,
    )
    app._advance_exclusive_validation_launch.assert_not_called()

    assert app._dispatch_exclusive_validation_game_over(home)
    manager.on_game_over.assert_called_once()
    assert app._exclusive_validation_terminal_hold is None
    assert result["launch"]["status"] == "requested"

    assert app._advance_owned_exclusive_validation_launch(
        object(),
        home,
        battle_started=False,
    )
    app._advance_exclusive_validation_launch.assert_called_once()


def test_ready_launch_prompt_blocks_cross_target_handoff(tmp_path):
    app, store, _manager, ready, _definition = _app_for_ready_launch(tmp_path)
    app._adb_target_session = Mock()

    with patch("core.app.log"):
        assert not app._handoff_adb_port(5565)

    app._adb_target_session.handoff.assert_not_called()
    receipt = store.status()["exclusive_validation"]["receipts"][
        ready["request_id"]
    ]
    assert receipt["launch"]["status"] == "awaiting_operator"


@pytest.mark.parametrize("launch_status", ("awaiting_operator", "requested"))
def test_unclaimed_launch_prompt_is_retired_on_a_different_runtime_target(
    tmp_path,
    launch_status,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    ready, _definition = _ready_validation(store)
    if launch_status == "requested":
        requested = store.resolve_exclusive_validation_launch(
            ready["request_id"],
            "start",
            source="test",
        )
        assert requested is not None
    app = App.__new__(App)
    app._supervisor = AutomationSupervisor(control_file=str(control_file))
    app._mission_mgr = MissionManager(None, get_strategy("tournament"))
    app._mission_mgr.start()
    app._active_exclusive_validation_request_id = None
    app._active_exclusive_validation_launch_request_id = None
    app._exclusive_validation_launch_start_hold = None
    app._exclusive_validation_ownership_hold = False

    with (
        patch("core.app.log"),
        patch("core.app.log_result"),
    ):
        retired = app._reconcile_exclusive_validation_launch()

    assert retired is not None
    assert retired["launch"]["status"] == "failed"
    assert "not current target" in retired["launch"]["reason"]
    assert not app._exclusive_validation_launch_in_progress()
    assert not app._exclusive_validation_blocks_target_handoff()


def test_confirmed_launch_waits_for_conflicting_operator_owner(tmp_path):
    app, store, _manager, ready, _definition = _app_for_ready_launch(tmp_path)
    store.resolve_exclusive_validation_launch(
        ready["request_id"],
        "start",
        source="test",
    )
    app._supervisor.apply_control()
    home = {"state": "HOME_SCREEN", "secondary_states": []}
    app._update_action_authority(
        detection=home,
        holds=(
            AuthorityHoldState(
                AuthorityHold.SETUP_CAPTURE,
                "setup capture owns the Android lifecycle boundary",
            ),
        ),
    )

    with patch("core.app.dispatch_tournament_launch") as dispatch:
        assert not app._advance_exclusive_validation_launch(
            object(),
            home,
            battle_started=False,
        )

    dispatch.assert_not_called()
    launch = _current_receipt(store)["launch"]
    assert launch["status"] == "requested"
    assert launch.get("owner") is None


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
    assert manager.session_preflight_pending()

    mission_vars["damage_slider_checked"] = True
    actions = strategy.tick(manager.ctx, object(), detection)
    assert actions == [
        {
            "type": "orb_distance_configure",
            "mode": "enforce",
            "range_basis": "98.38m",
            "extra": "87.16m",
            "workshop": "80.37m",
            "range_presets": [
                {
                    "range_basis": "30.00m",
                    "extra": "30.00m",
                    "workshop": "39.00m",
                },
                {
                    "range_basis": "98.38m",
                    "extra": "87.16m",
                    "workshop": "80.37m",
                },
            ],
        }
    ]
    assert manager.session_preflight_pending()

    mission_vars["orb_distance_checked"] = True
    actions = strategy.tick(manager.ctx, object(), detection)
    assert len(actions) == 1
    assert actions[0]["type"] == "session_preflight"
    assert actions[0]["validator"] == "tournament"
    assert manager.session_preflight_pending()
