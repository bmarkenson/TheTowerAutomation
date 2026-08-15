from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import cv2

from core.action_authority import AuthorityHold, AuthorityHoldState
from core.input import TapDispatchOutcome, TapDispatchStatus
from core.matcher import get_match
from handlers.free_ticket_handler import (
    FreeTicketRecoveryResult,
    FreeTicketRecoveryStatus,
    handle_free_ticket_modal,
)


ROOT = Path(__file__).resolve().parents[1]


def test_free_ticket_templates_have_positive_and_independent_negative_evidence():
    positive = cv2.imread(
        str(ROOT / "test" / "fixtures" / "free_ticket_home_20260812.png")
    )
    negative = cv2.imread(
        str(
            ROOT
            / "test"
            / "fixtures"
            / "home_screen_no_reward_badges_20260714.png"
        )
    )
    assert positive is not None
    assert negative is not None

    for key in (
        "indicators.free_ticket_dialog",
        "buttons.claim:free_ticket",
    ):
        point, confidence = get_match(key, screenshot=positive)
        negative_point, negative_confidence = get_match(
            key,
            screenshot=negative,
        )
        assert point is not None
        assert confidence >= 0.9
        assert negative_point is None
        assert negative_confidence < 0.9


def test_free_ticket_handler_claims_once_and_verifies_fresh_absence():
    source = object()
    cleared = object()
    guard = Mock(return_value=True)

    with (
        patch(
            "handlers.free_ticket_handler._state",
            side_effect=["FREE_TICKET", "FREE_TICKET", "HOME_SCREEN"],
        ),
        patch(
            "handlers.free_ticket_handler.safe_tap",
            return_value=True,
        ) as tap,
        patch("handlers.free_ticket_handler.time.sleep"),
        patch("handlers.free_ticket_handler.log_action_intent") as action,
        patch("handlers.free_ticket_handler.log_result") as result_log,
    ):
        result = handle_free_ticket_modal(
            source,
            action_guard_fn=guard,
            capture_fn=Mock(side_effect=[source, cleared]),
            operation_id="recovery-1",
        )

    assert result.status is FreeTicketRecoveryStatus.DISMISSED
    assert result.input_dispatched is True
    assert result.attempts == 1
    assert result.final_state == "HOME_SCREEN"
    tap.assert_called_once_with(
        "buttons.claim:free_ticket",
        screenshot=source,
        retries=0,
        dispatch="now",
        action_guard_fn=guard,
        return_dispatch_outcome=True,
    )
    action.assert_called_once()
    assert action.call_args.kwargs["operation_id"] == "recovery-1"
    result_log.assert_called_once()
    assert result_log.call_args.kwargs["operation_id"] == "recovery-1"
    assert "result=dismissed" in result_log.call_args.kwargs["detail"]


def test_free_ticket_handler_pause_before_input_yields_without_tap():
    guard = Mock(return_value=False)
    with (
        patch("handlers.free_ticket_handler._state", return_value="FREE_TICKET"),
        patch("handlers.free_ticket_handler.safe_tap") as tap,
        patch("handlers.free_ticket_handler.log_action_intent") as action,
        patch("handlers.free_ticket_handler.log_result") as result_log,
    ):
        result = handle_free_ticket_modal(
            object(),
            action_guard_fn=guard,
            capture_fn=Mock(),
        )

    assert result.status is FreeTicketRecoveryStatus.INTERRUPTED
    assert result.input_dispatched is False
    tap.assert_not_called()
    action.assert_not_called()
    result_log.assert_not_called()


def test_free_ticket_handler_does_not_infer_retry_when_modal_changed_pre_input():
    source = object()
    home = object()
    with (
        patch(
            "handlers.free_ticket_handler._state",
            side_effect=["FREE_TICKET", "HOME_SCREEN"],
        ),
        patch("handlers.free_ticket_handler.safe_tap") as tap,
        patch("handlers.free_ticket_handler.log_action_intent") as action,
        patch("handlers.free_ticket_handler.log_result") as result_log,
    ):
        result = handle_free_ticket_modal(
            source,
            action_guard_fn=Mock(return_value=True),
            capture_fn=Mock(return_value=home),
        )

    assert result.status is FreeTicketRecoveryStatus.ALREADY_RESOLVED
    assert result.input_dispatched is False
    assert result.final_state == "HOME_SCREEN"
    tap.assert_not_called()
    action.assert_not_called()
    result_log.assert_not_called()


def test_free_ticket_handler_missing_claim_target_is_failed_not_retried():
    source = object()
    guard = Mock(return_value=True)
    with (
        patch("handlers.free_ticket_handler._state", return_value="FREE_TICKET"),
        patch(
            "handlers.free_ticket_handler.safe_tap",
            return_value=False,
        ) as tap,
        patch("handlers.free_ticket_handler.log_action_intent") as action,
        patch("handlers.free_ticket_handler.log_result") as result_log,
    ):
        result = handle_free_ticket_modal(
            source,
            action_guard_fn=guard,
            capture_fn=Mock(return_value=source),
        )

    assert result.status is FreeTicketRecoveryStatus.FAILED
    assert result.input_dispatched is False
    assert result.attempts == 1
    tap.assert_called_once()


def test_free_ticket_handler_defers_transient_pre_input_capture_failure():
    source = object()
    with (
        patch(
            "handlers.free_ticket_handler._state",
            side_effect=["FREE_TICKET", None],
        ),
        patch("handlers.free_ticket_handler.safe_tap") as tap,
        patch("handlers.free_ticket_handler.log_action_intent") as action,
        patch("handlers.free_ticket_handler.log_result") as result_log,
    ):
        result = handle_free_ticket_modal(
            source,
            action_guard_fn=Mock(return_value=True),
            capture_fn=Mock(return_value=None),
        )

    assert result.status is FreeTicketRecoveryStatus.DEFERRED
    assert result.input_dispatched is False
    assert result.attempts == 0
    tap.assert_not_called()
    action.assert_not_called()
    result_log.assert_not_called()


def test_free_ticket_handler_has_one_bounded_transaction_when_modal_persists():
    guard = Mock(return_value=True)
    with (
        patch("handlers.free_ticket_handler._state", return_value="FREE_TICKET"),
        patch(
            "handlers.free_ticket_handler.safe_tap",
            return_value=True,
        ) as tap,
        patch("handlers.free_ticket_handler.time.sleep"),
        patch("handlers.free_ticket_handler.log_action_intent") as action,
        patch("handlers.free_ticket_handler.log_result") as result_log,
    ):
        result = handle_free_ticket_modal(
            object(),
            action_guard_fn=guard,
            capture_fn=Mock(return_value=object()),
            max_attempts=2,
            verification_polls=2,
        )

    assert result.status is FreeTicketRecoveryStatus.FAILED
    assert result.input_dispatched is True
    assert result.attempts == 2
    assert tap.call_count == 2
    action.assert_called_once()
    result_log.assert_called_once()
    assert "result=failed" in result_log.call_args.kwargs["detail"]


def test_free_ticket_handler_never_reuses_pixels_without_post_input_frame():
    frame = object()
    with (
        patch(
            "handlers.free_ticket_handler._state",
            side_effect=["FREE_TICKET", "FREE_TICKET", None, None],
        ),
        patch(
            "handlers.free_ticket_handler.safe_tap",
            return_value=True,
        ) as tap,
        patch("handlers.free_ticket_handler.time.sleep"),
        patch("handlers.free_ticket_handler.log_action_intent"),
        patch("handlers.free_ticket_handler.log_result"),
    ):
        result = handle_free_ticket_modal(
            frame,
            action_guard_fn=Mock(return_value=True),
            capture_fn=Mock(return_value=frame),
            max_attempts=2,
            verification_polls=2,
        )

    assert result.status is FreeTicketRecoveryStatus.UNCERTAIN
    assert result.dispatch_uncertain is True
    assert tap.call_count == 1


def test_free_ticket_handler_preserves_uncertain_claim_dispatch():
    frame = object()
    with (
        patch(
            "handlers.free_ticket_handler._state",
            side_effect=["FREE_TICKET", "FREE_TICKET"],
        ),
        patch(
            "handlers.free_ticket_handler.safe_tap",
            return_value=TapDispatchOutcome(TapDispatchStatus.UNCERTAIN),
        ) as tap,
        patch("handlers.free_ticket_handler.log_action_intent"),
        patch("handlers.free_ticket_handler.log_result"),
    ):
        result = handle_free_ticket_modal(
            frame,
            action_guard_fn=Mock(return_value=True),
            capture_fn=Mock(return_value=frame),
        )

    assert result.status is FreeTicketRecoveryStatus.UNCERTAIN
    assert result.input_dispatched is True
    assert result.dispatch_uncertain is True
    tap.assert_called_once()


def test_app_blocking_modal_path_requires_an_exact_launch_owner():
    from core.app import App

    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._free_ticket_recovery_owner = Mock(return_value=None)
    app._free_ticket_recovery_warnings = set()

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.handle_free_ticket_modal") as recover,
    ):
        handled = app._advance_blocking_primary_recovery(
            object(),
            {"state": "FREE_TICKET"},
        )

    assert handled is True
    recover.assert_not_called()
    hold = app._update_action_authority.call_args.kwargs["holds"][0]
    assert hold.hold is AuthorityHold.BLOCKING_MODAL_RECOVERY
    assert hold.allowed_auxiliary_collectors == ()


def test_app_blocking_modal_marks_the_same_terminal_owner_recovered():
    from core.app import App

    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._capture_frame = Mock()
    app._free_ticket_recovery_owner = Mock(
        return_value=("terminal_continuation", "terminal:obs-1")
    )
    app._free_ticket_recovery_owner_current = Mock(return_value=True)
    app._operator_workflow_authority_hold = Mock(
        return_value=AuthorityHoldState(
            AuthorityHold.OPERATOR_WORKFLOW,
            "exact launch owner",
        )
    )
    app._mark_terminal_home_continuation_modal_cleared = Mock(return_value=True)
    app._free_ticket_recovery_attempts = {}
    app._free_ticket_recovery_cleared = set()
    app._free_ticket_recovery_warnings = set()
    recovered = FreeTicketRecoveryResult(
        FreeTicketRecoveryStatus.DISMISSED,
        True,
        1,
        "HOME_SCREEN",
        "cleared",
    )

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.handle_free_ticket_modal", return_value=recovered) as handler,
    ):
        handled = app._advance_blocking_primary_recovery(
            object(),
            {"state": "FREE_TICKET"},
        )

    assert handled is True
    handler.assert_called_once()
    guard = handler.call_args.kwargs["action_guard_fn"]
    assert guard() is True
    assert app._free_ticket_recovery_attempts["terminal:obs-1"] == 1
    assert "terminal:obs-1" in app._free_ticket_recovery_cleared
    app._mark_terminal_home_continuation_modal_cleared.assert_called_once_with()


def test_app_uncertain_claim_terminalizes_owner_before_catastrophic_pause():
    from core.app import App

    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.battle_workflow = {
        "request_id": "start-1",
        "intent": "start_battle",
        "status": "action_dispatched",
    }
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._capture_frame = Mock()
    app._free_ticket_recovery_owner = Mock(
        return_value=("battle_workflow", "battle:start-1")
    )
    app._free_ticket_recovery_owner_current = Mock(return_value=True)
    app._operator_workflow_authority_hold = Mock(
        return_value=AuthorityHoldState(
            AuthorityHold.OPERATOR_WORKFLOW,
            "exact launch owner",
        )
    )
    app._fail_free_ticket_recovery = Mock()
    app._runtime_uncertain_mutation_result = Mock()
    app._free_ticket_recovery_attempts = {}
    app._free_ticket_recovery_cleared = set()
    app._free_ticket_recovery_warnings = set()
    uncertain = FreeTicketRecoveryResult(
        FreeTicketRecoveryStatus.UNCERTAIN,
        True,
        1,
        "FREE_TICKET",
        "dispatch unknown",
        True,
    )

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.handle_free_ticket_modal", return_value=uncertain),
    ):
        assert app._advance_blocking_primary_recovery(
            object(),
            {"state": "FREE_TICKET"},
        )

    app._fail_free_ticket_recovery.assert_called_once()
    app._runtime_uncertain_mutation_result.assert_called_once()
    assert "battle:start-1" in app._uncertain_lifecycle_actions
    assert "start-1" in app._uncertain_lifecycle_actions


def test_app_deferred_claim_preserves_exact_owner_for_later_fresh_frame():
    from core.app import App

    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._capture_frame = Mock()
    owner = ("terminal_continuation", "terminal:obs-1")
    app._free_ticket_recovery_owner = Mock(return_value=owner)
    app._free_ticket_recovery_owner_current = Mock(return_value=True)
    app._operator_workflow_authority_hold = Mock(
        return_value=AuthorityHoldState(
            AuthorityHold.OPERATOR_WORKFLOW,
            "exact launch owner",
        )
    )
    app._fail_free_ticket_recovery = Mock()
    app._free_ticket_recovery_attempts = {}
    app._free_ticket_recovery_cleared = set()
    app._free_ticket_recovery_warnings = set()
    deferred = FreeTicketRecoveryResult(
        FreeTicketRecoveryStatus.DEFERRED,
        False,
        0,
        "FREE_TICKET",
        "capture unavailable",
    )

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.handle_free_ticket_modal", return_value=deferred),
    ):
        assert app._advance_blocking_primary_recovery(
            object(),
            {"state": "FREE_TICKET"},
        )

    app._fail_free_ticket_recovery.assert_not_called()
    assert app._free_ticket_recovery_attempts == {}


def test_blocking_recovery_preserves_setup_capture_hold_and_sends_no_input():
    from core.app import App

    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.setup_capture = {"status": "capturing"}
    app._supervisor.setup_capture_error = False
    app._supervisor.battle_workflow_error = False
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._free_ticket_recovery_owner = Mock(
        return_value=("battle_workflow", "battle:start-1")
    )
    app._free_ticket_recovery_owner_current = Mock(return_value=True)
    app._operator_workflow_authority_hold = Mock(
        return_value=AuthorityHoldState(
            AuthorityHold.SETUP_CAPTURE,
            "setup owns Android lifecycle",
        )
    )
    app._free_ticket_recovery_warnings = set()

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.handle_free_ticket_modal") as handler,
    ):
        assert app._advance_blocking_primary_recovery(
            object(),
            {"state": "FREE_TICKET"},
        )

    handler.assert_not_called()
    holds = app._update_action_authority.call_args.kwargs["holds"]
    assert {item.hold for item in holds} == {
        AuthorityHold.SETUP_CAPTURE,
        AuthorityHold.BLOCKING_MODAL_RECOVERY,
    }


def test_blocking_recovery_final_guard_denies_new_stronger_hold():
    from core.app import App

    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.setup_capture = None
    app._supervisor.setup_capture_error = False
    app._supervisor.battle_workflow_error = False
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._capture_frame = Mock()
    owner = ("terminal_continuation", "terminal:obs-1")
    app._free_ticket_recovery_owner = Mock(return_value=owner)
    app._free_ticket_recovery_owner_current = Mock(return_value=True)
    app._operator_workflow_authority_hold = Mock(
        side_effect=[
            AuthorityHoldState(
                AuthorityHold.OPERATOR_WORKFLOW,
                "exact launch owner",
            ),
            AuthorityHoldState(
                AuthorityHold.SETUP_CAPTURE,
                "setup arrived at final guard",
            ),
        ]
    )
    app._free_ticket_recovery_attempts = {}
    app._free_ticket_recovery_cleared = set()
    app._free_ticket_recovery_warnings = set()

    def interrupted(_frame, *, action_guard_fn, **_kwargs):
        assert action_guard_fn() is False
        return FreeTicketRecoveryResult(
            FreeTicketRecoveryStatus.INTERRUPTED,
            False,
            0,
            "FREE_TICKET",
            "stronger hold",
        )

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.handle_free_ticket_modal", side_effect=interrupted),
    ):
        assert app._advance_blocking_primary_recovery(
            object(),
            {"state": "FREE_TICKET"},
        )

    holds = app._update_action_authority.call_args.kwargs["holds"]
    assert {item.hold for item in holds} == {
        AuthorityHold.SETUP_CAPTURE,
        AuthorityHold.BLOCKING_MODAL_RECOVERY,
    }


def test_blocking_hold_survives_unknown_capture_until_known_absence():
    from core.app import App

    app = App.__new__(App)
    app._operator_workflow_authority_hold = Mock(return_value=None)
    app._blocking_primary_hold_active = True

    assert {
        item.hold for item in app._pre_capture_authority_holds()
    } == {AuthorityHold.BLOCKING_MODAL_RECOVERY}
    app._observe_blocking_primary_boundary({"state": "UNKNOWN"})
    assert app._blocking_primary_hold_active is True
    app._observe_blocking_primary_boundary({"state": "HOME_SCREEN"})
    assert app._blocking_primary_hold_active is False


def test_stale_dispatched_workflow_reconciles_before_blocker_handoff():
    from core.app import App

    workflow = {
        "request_id": "start-1",
        "intent": "start_battle",
        "status": "action_dispatched",
    }
    current = {"game_state": "unknown"}
    app = App.__new__(App)
    app._supervisor = Mock(battle_workflow=workflow)
    app._mission_mgr = Mock()
    app._current_control_workflow_evidence = Mock(return_value=current)
    app._workflow_dispatch_receipt_mismatch = Mock(
        return_value="runtime evidence changed"
    )
    app._tournament_battle_guard = Mock(return_value=False)

    app._reconcile_dispatched_workflow_behind_blocker(
        {"state": "FREE_TICKET"}
    )

    app._mission_mgr.revoke_initial_battle_intent.assert_called_once_with(
        "start_battle",
        request_id="start-1",
    )
    assert app._supervisor.transition_battle_workflow.call_args.args == (
        "start-1",
        "interrupted",
    )
    assert (
        app._supervisor.transition_battle_workflow.call_args.kwargs["reason"]
        == "runtime evidence changed"
    )


def test_main_loop_intercepts_free_ticket_before_ordinary_automation():
    from core.app import App

    frame = object()
    app = App.__new__(App)
    app._config = SimpleNamespace(wait_on_start=False)
    app._supervisor = MagicMock(
        is_paused=False,
        game_speed_target=6.3,
        interactive_development_lease=None,
    )
    app._supervisor.apply_control.return_value = False
    app._adb_connection_coordinator = MagicMock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._status_reporter = MagicMock()
    app._mission_mgr = MagicMock()
    app._match_trace = False
    app._last_wave_value = None
    app._blind_tapper_suspended = False
    app._capture_frame = Mock(side_effect=[frame, KeyboardInterrupt])
    app._prune_generated_artifacts = Mock()
    app._observe_strategy_request = Mock()
    app._sync_interactive_development_control_boundary = Mock()
    app._pre_capture_authority_holds = Mock(return_value=())
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._annotate_home_battle_control = Mock()
    app._record_control_observation = Mock()
    app._observe_blocking_primary_boundary = Mock()
    app._yield_on_unexpected_manual_activity = Mock()
    app._reconcile_terminal_home_continuation = Mock(return_value=False)
    app._reconcile_dispatched_workflow_behind_blocker = Mock()
    app._observe_action_circuits = Mock()
    app._advance_blocking_primary_recovery = Mock(return_value=True)
    app._sync_operator_control_workflows = Mock()
    app._handle_primary_states = Mock()

    with (
        patch("core.app.threading.Thread"),
        patch(
            "core.app.detect_state_and_overlays",
            return_value={"state": "FREE_TICKET", "overlays": []},
        ),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.handle_unknown_state") as unknown_recovery,
        patch("core.app.AUTOMATION.install_mutation_guard", return_value=object()),
        patch("core.app.AUTOMATION.shutdown_mutations"),
        patch("core.app.AUTOMATION.clear_mutation_guard"),
        patch("core.app.log"),
    ):
        app.run()

    app._advance_blocking_primary_recovery.assert_called_once_with(
        frame,
        {"state": "FREE_TICKET", "overlays": []},
    )
    app._reconcile_dispatched_workflow_behind_blocker.assert_called_once_with(
        {"state": "FREE_TICKET", "overlays": []}
    )
    app._sync_operator_control_workflows.assert_not_called()
    app._mission_mgr.observe_detection.assert_not_called()
    app._mission_mgr.maybe_run_start.assert_not_called()
    app._mission_mgr.tick.assert_not_called()
    app._mission_mgr.handle_overlays.assert_not_called()
    app._mission_mgr.on_state.assert_not_called()
    app._handle_primary_states.assert_not_called()
    unknown_recovery.assert_not_called()
