from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, call, patch

import numpy as np
import pytest

from automation.missions.base import MissionContext
from core.app import App
from core.battle_lifecycle import HomeBattleControl
from core.input import TapDispatchOutcome, TapDispatchStatus
from core.no_strategy_inventory import (
    NoStrategyInventoryResult,
    NoStrategyInventoryStatus,
)
from core.no_strategy_observer import NoStrategyRunObserver
from core.no_strategy_post_run import NoStrategyPostRunError, NoStrategyPostRunPaused
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)
from core.player_save_history import PlayerSaveAttachmentContext
from core.run_state import AUTOMATION, ExecMode
from handlers.game_over_handler import GameOverHandlingOutcome
from test.player_save_temporal_fixtures import (
    running_attachment_observations,
)


def _app_without_strategy():
    app = App.__new__(App)
    app._mission_mgr = MagicMock()
    app._mission_mgr.strategy = None
    app._mission_mgr.ctx = MissionContext(data={"mission_vars": {}})
    app._mission_mgr.session_preflight_repair_in_progress.return_value = False
    app._mission_mgr.active_battle_observed.return_value = True
    app._fast_game_over = True
    app._last_wave_value = 2470
    app._last_wave_conf = 99.0
    app._supervisor = MagicMock()
    app._supervisor.is_paused = False
    app._status_reporter = MagicMock()
    app._status_reporter.coin_rate_samples = []
    app._game_speed_guard = MagicMock()
    app._game_speed_guard.snapshot.return_value = {
        "mode": "REDUCED",
        "target": 4.0,
        "target_semantics": "exact",
        "timeline": [],
    }
    app._pending_strategy_request = None
    app._strategy_boundary_confirmed = False
    app._handle_daily_gem_if_due = MagicMock(return_value=False)
    app._handle_mission_rewards_if_due = MagicMock(return_value=False)
    app._apply_pending_strategy = MagicMock()
    app._no_strategy_observer = MagicMock()
    app._no_strategy_observer.snapshot.return_value = {
        "fields": {"run_identity": {"status": "observed"}}
    }
    app._no_strategy_observation_active = True
    app._no_strategy_inventory_complete = False
    app._no_strategy_inventory_retry_at = 0.0
    app._pending_no_strategy_record = None
    app._no_strategy_post_run_stage = None
    app._no_strategy_post_run_retry_at = 0.0
    app._no_strategy_post_run_recovery_checked = True
    app._current_run_scope_id = lambda: "test-no-strategy"
    app._observed_active_battle_scope_id = "test-no-strategy"
    return app


def test_activation_evidence_uses_timestamped_bounded_runtime_path():
    app = App.__new__(App)
    frame = np.full((20, 30, 3), 17, dtype=np.uint8)

    with patch("core.app.cv2.imwrite", return_value=True) as imwrite:
        path = app._retain_activation_evidence(
            {
                "ability": "second_wind",
                "sequence": 1,
                "detected_at": "2099-07-27T19:45:12-07:00",
                "frame": frame,
            }
        )

    assert path == (
        "screenshots/matches/"
        "SurvivalActivation20990727T194512-0700_second_wind_01_first_absent.png"
    )
    assert imwrite.call_args.args[0].endswith(path)
    assert imwrite.call_args.args[1] is frame


def test_no_strategy_game_over_forces_full_capture_and_home_inventory():
    app = _app_without_strategy()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    record = {"battle_id": "Battle1"}
    continuation = {"schema_version": 1, "source": "no_strategy_post_run"}
    app._build_terminal_home_continuation_claim = MagicMock(
        return_value=continuation
    )
    app._commit_terminal_home_continuation = MagicMock(return_value=True)

    with patch(
        "core.app.handle_game_over",
        return_value=GameOverHandlingOutcome(
            True,
            "home",
            record,
            "saved",
        ),
    ) as game_over:
        app._handle_primary_states("GAME_OVER", set(), frame)

    kwargs = game_over.call_args.kwargs
    assert kwargs["capture_stats"] is True
    assert kwargs["return_home_after_battle"] is True
    assert kwargs["battle_context"]["strategy"] is None
    assert kwargs["battle_context"]["observed_run_configuration"] == {
        "fields": {"run_identity": {"status": "observed"}}
    }
    assert kwargs["battle_context"]["survival_ability_activations"] == {
        "schema_version": 4,
        "source": "visual_transition_detection",
        "second_wind_activations": [],
        "demon_mode_first_activation": None,
        "nuke_activations": [],
    }
    assert kwargs["battle_context"]["game_speed_control"] == {
        "mode": "REDUCED",
        "target": 4.0,
        "target_semantics": "exact",
        "timeline": [],
    }
    assert app._pending_no_strategy_record is record
    assert app._no_strategy_post_run_stage == "locks"
    app._build_terminal_home_continuation_claim.assert_called_once_with(
        source="no_strategy_post_run",
        evidence=None,
    )
    app._commit_terminal_home_continuation.assert_called_once_with(
        continuation
    )


def test_no_strategy_terminal_retry_arms_continuation_only_after_home():
    app = _app_without_strategy()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    binding = {
        "runtime_id": "runtime-1",
        "pid": 123,
        "adb_target": "localhost:5555",
        "target_generation": 4,
        "activity_scope_run_id": "run-1",
        "game_state": "game_over",
    }
    app._current_control_workflow_evidence = MagicMock(return_value=binding)
    continuation = {"schema_version": 1, "source": "no_strategy_post_run"}
    app._build_terminal_home_continuation_claim = MagicMock(
        return_value=continuation
    )
    app._commit_terminal_home_continuation = MagicMock(return_value=True)

    with patch(
        "core.app.handle_game_over",
        side_effect=(
            GameOverHandlingOutcome(
                False,
                "pending_retry",
                None,
                "unavailable",
                "Game Stats recovery",
            ),
            GameOverHandlingOutcome(True, "home", None, "unavailable"),
        ),
    ) as game_over:
        app._handle_primary_states("GAME_OVER", set(), frame)
        app._commit_terminal_home_continuation.assert_not_called()
        assert app._pending_game_over_route[
            "terminal_home_continuation"
        ] == continuation

        app._handle_primary_states("GAME_OVER", set(), frame)

    app._build_terminal_home_continuation_claim.assert_called_once_with(
        source="no_strategy_post_run",
        evidence=binding,
    )
    assert game_over.call_count == 2
    assert game_over.call_args_list[1].kwargs["capture_stats"] is False
    app._commit_terminal_home_continuation.assert_called_once_with(
        continuation
    )


def test_degraded_continue_retries_home_then_arms_profile_repair_launch():
    app = _app_without_strategy()
    app._runtime_policy = MagicMock(return_value={})
    app._mission_mgr.strategy = SimpleNamespace(name="farm_t19")
    degradation = {
        "schema_version": 1,
        "sources": ["session_preflight"],
        "reason": "modules do not match",
        "failed_checks": ["modules"],
    }
    app._mission_mgr.running_configuration_degradation.return_value = degradation
    binding = {
        "runtime_id": "runtime-1",
        "pid": 123,
        "adb_target": "localhost:5555",
        "target_generation": 4,
        "activity_scope_run_id": "run-1",
        "game_state": "game_over",
    }
    app._current_control_workflow_evidence = MagicMock(return_value=binding)
    app._terminal_battle_bundle = MagicMock(return_value=({}, None, None))
    continuation = {
        "schema_version": 1,
        "source": "degraded_battle_repair",
    }
    app._build_terminal_home_continuation_claim = MagicMock(
        return_value=continuation
    )
    app._commit_terminal_home_continuation = MagicMock(return_value=True)
    outcomes = iter(
        (
            GameOverHandlingOutcome(
                False,
                "pending_retry",
                None,
                "unavailable",
                "Go Home from Game Stats",
            ),
            GameOverHandlingOutcome(True, "home", None, "unavailable"),
        )
    )

    def handle_terminal(**kwargs):
        kwargs["before_terminal_action"]()
        return next(outcomes)

    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.NEXT_BATTLE
    try:
        with patch("core.app.handle_game_over", side_effect=handle_terminal) as game_over:
            app._handle_primary_states("GAME_OVER", set(), object())
            assert app._pending_game_over_route["desired_route"] == "home"
            assert app._pending_game_over_route["degraded_home_repair"] == (
                degradation
            )
            app._commit_terminal_home_continuation.assert_not_called()

            app._handle_primary_states("GAME_OVER", set(), object())
    finally:
        AUTOMATION.mode = original_mode

    assert game_over.call_count == 2
    assert all(
        call_args.kwargs["return_home_after_battle"] is True
        for call_args in game_over.call_args_list
    )
    app._build_terminal_home_continuation_claim.assert_called_once_with(
        source="degraded_battle_repair",
        evidence=binding,
    )
    app._mission_mgr.prepare_degraded_home_repair.assert_called_once_with(
        degradation
    )
    app._commit_terminal_home_continuation.assert_called_once_with(continuation)


def test_degraded_observer_applies_pending_strategy_before_home_repair():
    app = _app_without_strategy()
    app._runtime_policy = MagicMock(return_value={})
    degradation = {
        "schema_version": 1,
        "sources": ["attachment_applicability"],
        "reason": "selected Strategy expects Tier 19, active battle is Tier 18",
        "failed_checks": ["battle_tier"],
    }
    app._mission_mgr.running_configuration_degradation.return_value = degradation
    app._pending_strategy_request = (
        "farm_t19",
        "strategy-request-1",
        "next_boundary",
    )

    def apply_pending_strategy():
        app._mission_mgr.strategy = SimpleNamespace(name="farm_t19")
        return True

    app._apply_pending_strategy.side_effect = apply_pending_strategy
    binding = {
        "runtime_id": "runtime-1",
        "pid": 123,
        "adb_target": "localhost:5555",
        "target_generation": 4,
        "activity_scope_run_id": "run-1",
        "game_state": "game_over",
    }
    app._current_control_workflow_evidence = MagicMock(return_value=binding)
    app._terminal_battle_bundle = MagicMock(return_value=({}, None, None))
    continuation = {
        "schema_version": 1,
        "source": "degraded_battle_repair",
    }
    app._build_terminal_home_continuation_claim = MagicMock(
        return_value=continuation
    )
    app._commit_terminal_home_continuation = MagicMock(return_value=True)

    def handle_terminal(**kwargs):
        kwargs["before_terminal_action"]()
        return GameOverHandlingOutcome(True, "home", None, "unavailable")

    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.NEXT_BATTLE
    try:
        with patch("core.app.handle_game_over", side_effect=handle_terminal):
            app._handle_primary_states("GAME_OVER", set(), object())
    finally:
        AUTOMATION.mode = original_mode

    app._build_terminal_home_continuation_claim.assert_called_once_with(
        source="degraded_battle_repair",
        evidence=binding,
    )
    app._apply_pending_strategy.assert_called_once()
    app._mission_mgr.prepare_degraded_home_repair.assert_called_once_with(
        degradation
    )


def test_degraded_battle_does_not_force_home_when_future_policy_is_wait():
    app = _app_without_strategy()
    app._runtime_policy = MagicMock(return_value={})
    app._mission_mgr.strategy = SimpleNamespace(name="farm_t19")
    app._mission_mgr.running_configuration_degradation.return_value = {
        "schema_version": 1,
        "sources": ["session_preflight"],
        "reason": "modules do not match",
        "failed_checks": ["modules"],
    }
    app._terminal_battle_bundle = MagicMock(return_value=({}, None, None))

    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.WAIT
    try:
        with patch(
            "core.app.handle_game_over",
            return_value=GameOverHandlingOutcome(
                True,
                "wait",
                None,
                "unavailable",
            ),
        ) as game_over:
            app._handle_primary_states("GAME_OVER", set(), object())
    finally:
        AUTOMATION.mode = original_mode

    assert game_over.call_args.kwargs["return_home_after_battle"] is False
    app._mission_mgr.running_configuration_degradation.assert_not_called()
    app._mission_mgr.prepare_degraded_home_repair.assert_not_called()


def test_save_resolved_post_run_fields_skip_home_configuration_navigation():
    app = _app_without_strategy()
    resolved = {
        field: {"status": "observed", "value": "saved"}
        for field in (
            "cards_deck",
            "workshop_preset",
            "free_upgrade_locks",
            "perk_first_choice",
            "perk_bans",
            "perk_auto_pick_order",
        )
    }
    app._no_strategy_observer.snapshot.return_value = {"fields": resolved}
    record = {"battle_id": "BattleSaveResolved"}
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with patch("core.app.handle_game_over", return_value=record):
        app._handle_primary_states("GAME_OVER", set(), frame)

    assert app._no_strategy_post_run_stage == "finalize"
    app._persist_pending_no_strategy_record = MagicMock()
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.NEXT_BATTLE
    try:
        with (
            patch(
                "core.app.inspect_post_run_free_upgrade_locks",
                side_effect=AssertionError("save-resolved locks must not open UI"),
            ),
            patch(
                "core.app.open_perks_configuration_for_post_run_capture",
                side_effect=AssertionError("save-resolved perks must not open UI"),
            ),
        ):
            handled = app._handle_no_strategy_post_run("HOME_SCREEN", frame)
    finally:
        AUTOMATION.mode = original_mode

    assert handled is True
    app._persist_pending_no_strategy_record.assert_called_once_with(finalized=True)
    assert app._pending_no_strategy_record is None


def test_save_resolved_post_run_waits_for_verified_home_before_finalizing():
    app = _app_without_strategy()
    app._pending_no_strategy_record = {"battle_id": "BattleSaveResolved"}
    app._no_strategy_post_run_stage = "finalize"
    app._persist_pending_no_strategy_record = MagicMock()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with patch("core.app.restore_post_run_home") as restore_home:
        handled = app._handle_no_strategy_post_run("CARDS", frame)

    assert handled is True
    restore_home.assert_called_once_with(
        frame,
        action_guard_fn=app._no_strategy_action_guard,
    )
    app._persist_pending_no_strategy_record.assert_not_called()


def test_post_run_home_records_locks_then_automatically_opens_perks():
    app = _app_without_strategy()
    app._pending_no_strategy_record = {"battle_id": "Battle1"}
    app._no_strategy_post_run_stage = "locks"
    app._persist_pending_no_strategy_record = MagicMock()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    restored = np.ones((1920, 1080, 3), dtype=np.uint8)
    lock_result = SimpleNamespace(
        values={"locks": [{"label": "Shockwave Size", "state": "checked"}]},
        home_screenshot=restored,
        workshop_screenshot=np.full((1920, 1080, 3), 2, dtype=np.uint8),
    )

    with (
        patch(
            "core.app.inspect_post_run_free_upgrade_locks",
            return_value=lock_result,
        ),
        patch(
            "core.app.open_perks_configuration_for_post_run_capture",
            side_effect=NoStrategyPostRunPaused("paused"),
        ) as open_perks,
    ):
        handled = app._handle_no_strategy_post_run("HOME_SCREEN", frame)

    assert handled is True
    app._no_strategy_observer.record_post_run_value.assert_called_once_with(
        "free_upgrade_locks",
        lock_result.values,
        source="home_workshop_lock_details",
    )
    app._persist_pending_no_strategy_record.assert_called_once_with(finalized=False)
    open_perks.assert_called_once()
    assert open_perks.call_args.args == (restored,)
    assert app._no_strategy_post_run_stage == "perks"


def test_post_run_perk_capture_finalizes_same_record_and_releases_boundary():
    app = _app_without_strategy()
    app._pending_no_strategy_record = {"battle_id": "Battle1"}
    app._no_strategy_post_run_stage = "perks"
    app._persist_pending_no_strategy_record = MagicMock()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    capture = SimpleNamespace(
        fields={
            "perk_first_choice": {"evidence_images": ["first.png"]},
            "perk_bans": {"evidence_images": ["bans.png"]},
            "perk_auto_pick_order": {"evidence_images": ["order.png"]},
        }
    )

    with (
        patch(
            "core.app.capture_post_run_perk_configuration",
            return_value=capture,
        ),
    ):
        handled = app._handle_no_strategy_post_run("PERKS", frame)

    assert handled is True
    assert app._no_strategy_observer.record_post_run_evidence.call_count == 3
    app._persist_pending_no_strategy_record.assert_called_once_with(finalized=True)
    app._no_strategy_observer.reset.assert_called_once_with()
    assert app._pending_no_strategy_record is None
    assert app._no_strategy_post_run_stage is None
    assert app._no_strategy_observation_active is False


def test_post_run_collection_failure_releases_next_battle_with_incomplete_data():
    app = _app_without_strategy()
    app._pending_no_strategy_record = {"battle_id": "BattleIncomplete"}
    app._no_strategy_post_run_stage = "locks"
    app._persist_pending_no_strategy_record = MagicMock()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.NEXT_BATTLE
    try:
        with patch(
            "core.app.inspect_post_run_free_upgrade_locks",
            side_effect=NoStrategyPostRunError("lock evidence unavailable"),
        ):
            handled = app._handle_no_strategy_post_run("HOME_SCREEN", frame)
    finally:
        AUTOMATION.mode = original_mode

    assert handled is True
    app._persist_pending_no_strategy_record.assert_called_once_with(
        finalized=True
    )
    assert app._pending_no_strategy_record is None
    assert app._no_strategy_post_run_stage is None
    app._supervisor.persist_state.assert_not_called()


def test_completed_post_run_inventory_holds_home_until_wait_mode_changes():
    app = _app_without_strategy()
    record = {"battle_id": "Battle1"}
    app._pending_no_strategy_record = record
    app._no_strategy_post_run_stage = "perks"
    app._persist_pending_no_strategy_record = MagicMock()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    capture = SimpleNamespace(fields={})
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.WAIT

    try:
        with patch(
            "core.app.capture_post_run_perk_configuration",
            return_value=capture,
        ):
            handled = app._handle_no_strategy_post_run("PERKS", frame)

        assert handled is True
        app._persist_pending_no_strategy_record.assert_called_once_with(
            finalized=True
        )
        assert app._pending_no_strategy_record is record
        assert app._no_strategy_post_run_stage == "complete_wait"
        app._no_strategy_observer.reset.assert_not_called()

        assert app._handle_no_strategy_post_run("HOME_SCREEN", frame) is True
        assert app._pending_no_strategy_record is record

        AUTOMATION.mode = ExecMode.NEXT_BATTLE
        assert app._handle_no_strategy_post_run("HOME_SCREEN", frame) is True
    finally:
        AUTOMATION.mode = original_mode

    assert app._pending_no_strategy_record is None
    assert app._no_strategy_post_run_stage is None
    app._no_strategy_observer.reset.assert_called_once_with()


def test_wait_mode_never_auto_starts_from_home():
    app = _app_without_strategy()
    app._auto_start_enabled = True
    app._handler_enabled = MagicMock(side_effect=lambda name: name == "home")
    app._runtime_policy = MagicMock(return_value={})
    app._mission_mgr.no_battle_setup_requirements.return_value = {}
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.WAIT

    try:
        with (
            patch(
                "core.app.detect_home_battle_control",
                return_value=SimpleNamespace(control=HomeBattleControl.NEW_BATTLE),
            ),
            patch("core.app.handle_home_screen") as home,
        ):
            app._handle_primary_states("HOME_SCREEN", set(), frame)
    finally:
        AUTOMATION.mode = original_mode

    home.assert_called_once_with(restart_enabled=False)


def test_home_mode_stays_home_without_auto_starting():
    app = _app_without_strategy()
    app._auto_start_enabled = True
    app._handler_enabled = MagicMock(side_effect=lambda name: name == "home")
    app._runtime_policy = MagicMock(return_value={})
    app._mission_mgr.no_battle_setup_requirements.return_value = {}
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.HOME

    try:
        with (
            patch(
                "core.app.detect_home_battle_control",
                return_value=SimpleNamespace(control=HomeBattleControl.NEW_BATTLE),
            ),
            patch("core.app.handle_home_screen") as home,
        ):
            app._handle_primary_states("HOME_SCREEN", set(), frame)
    finally:
        AUTOMATION.mode = original_mode

    home.assert_called_once_with(restart_enabled=False)


def test_next_battle_mode_auto_starts_from_home():
    app = _app_without_strategy()
    app._auto_start_enabled = True
    app._handler_enabled = MagicMock(side_effect=lambda name: name == "home")
    app._runtime_policy = MagicMock(return_value={})
    app._mission_mgr.no_battle_setup_requirements.return_value = {}
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.NEXT_BATTLE

    try:
        with (
            patch(
                "core.app.detect_home_battle_control",
                return_value=SimpleNamespace(control=HomeBattleControl.NEW_BATTLE),
            ),
            patch("core.app.handle_home_screen") as home,
        ):
            app._handle_primary_states("HOME_SCREEN", set(), frame)
    finally:
        AUTOMATION.mode = original_mode

    home.assert_called_once_with(
        restart_enabled=True,
        action_guard_fn=ANY,
        return_dispatch_outcome=True,
    )


def test_legacy_home_launch_uncertainty_is_tombstoned_across_enable():
    app = _app_without_strategy()
    app._auto_start_enabled = True
    app._handler_enabled = MagicMock(side_effect=lambda name: name == "home")
    app._runtime_policy = MagicMock(return_value={})
    app._mission_mgr.no_battle_setup_requirements.return_value = {}
    app._current_control_workflow_evidence = MagicMock(
        return_value={
            "runtime_id": "runtime-1",
            "pid": 100,
            "adb_target": "localhost:5555",
            "target_generation": 7,
            "activity_scope_run_id": "scope-1",
        }
    )
    app._runtime_uncertain_mutation_result = MagicMock()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    uncertain = TapDispatchOutcome(TapDispatchStatus.UNCERTAIN)
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.NEXT_BATTLE

    try:
        with (
            patch(
                "core.app.detect_home_battle_control",
                side_effect=[
                    SimpleNamespace(control=HomeBattleControl.UNKNOWN),
                    SimpleNamespace(control=HomeBattleControl.NEW_BATTLE),
                ],
            ),
            patch(
                "core.app.handle_home_screen",
                side_effect=[uncertain, False],
            ) as home,
        ):
            app._handle_primary_states("HOME_SCREEN", set(), frame)
            app._handle_primary_states("HOME_SCREEN", set(), frame)
    finally:
        AUTOMATION.mode = original_mode

    assert home.call_args_list[0].kwargs["return_dispatch_outcome"] is True
    assert home.call_args_list[1] == call(restart_enabled=False)
    epoch = app._legacy_home_launch_epoch(HomeBattleControl.UNKNOWN)
    assert epoch in app._uncertain_legacy_home_launch_epochs
    assert (
        app._legacy_home_launch_epoch(HomeBattleControl.NEW_BATTLE) == epoch
    )
    app._runtime_uncertain_mutation_result.assert_called_once()


def test_managed_terminal_policy_does_not_start_from_idle_home():
    app = _app_without_strategy()
    app._operator_battle_intent_required = True
    app._auto_start_enabled = True
    app._handler_enabled = MagicMock(side_effect=lambda name: name == "home")
    app._runtime_policy = MagicMock(return_value={})
    app._mission_mgr.awaiting_initial_battle_intent.return_value = False
    app._mission_mgr.no_battle_setup_requirements.return_value = {}
    app._supervisor.battle_workflow = None
    app._supervisor.manual_control = None
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.NEXT_BATTLE

    try:
        with (
            patch(
                "core.app.detect_home_battle_control",
                return_value=SimpleNamespace(
                    control=HomeBattleControl.NEW_BATTLE
                ),
            ),
            patch("core.app.handle_home_screen") as home,
        ):
            app._handle_primary_states("HOME_SCREEN", set(), frame)
    finally:
        AUTOMATION.mode = original_mode

    home.assert_called_once_with(restart_enabled=False)


def test_terminal_bound_continuation_dispatches_exact_new_battle_once():
    app = _app_without_strategy()
    app._operator_battle_intent_required = True
    app._auto_start_enabled = True
    app._handler_enabled = MagicMock(side_effect=lambda name: name == "home")
    app._runtime_policy = MagicMock(return_value={})
    app._mission_mgr.awaiting_initial_battle_intent.return_value = False
    app._mission_mgr.no_battle_setup_requirements.return_value = {}
    app._supervisor.battle_workflow = None
    app._supervisor.manual_control = None
    app._terminal_home_continuation = {
        "source": "no_strategy_post_run"
    }
    app._terminal_home_continuation_ready = MagicMock(return_value=True)
    app._runtime_action_guard = MagicMock(return_value=True)
    app._mark_terminal_home_continuation_dispatched = MagicMock(
        return_value=True
    )
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.NEXT_BATTLE

    try:
        with (
            patch(
                "core.app.detect_home_battle_control",
                return_value=SimpleNamespace(
                    control=HomeBattleControl.NEW_BATTLE
                ),
            ),
            patch("core.app.handle_home_screen", return_value=True) as home,
        ):
            app._handle_primary_states("HOME_SCREEN", set(), frame)
    finally:
        AUTOMATION.mode = original_mode

    assert app._terminal_home_continuation_ready.call_count == 2
    home_call = home.call_args
    assert home_call.kwargs["restart_enabled"] is True
    assert home_call.kwargs["require_new_battle"] is True
    assert home_call.kwargs["operation_id"]
    assert home_call.kwargs["action_purpose"] == (
        "Continuing after the completed battle"
    )
    assert "bounded Home continuation" in home_call.kwargs["action_reason"]
    app._mark_terminal_home_continuation_dispatched.assert_called_once_with()


def test_automatic_in_battle_inventory_is_exclusive_and_runs_once():
    app = _app_without_strategy()
    result = NoStrategyInventoryResult(
        NoStrategyInventoryStatus.COMPLETE,
        "complete",
    )

    with patch(
        "core.app.run_no_strategy_in_battle_inventory",
        return_value=result,
    ) as inventory:
        handled = app._handle_no_strategy_in_battle_inventory({"state": "RUNNING"})
        repeated = app._handle_no_strategy_in_battle_inventory({"state": "RUNNING"})

    assert handled is True
    assert repeated is False
    inventory.assert_called_once()
    assert app._no_strategy_inventory_complete is True


@pytest.mark.parametrize(
    "state",
    (
        "CARDS",
        "PERKS",
        "MODULES",
        "EVENT",
        "GUILD",
        "TARGET_PRIORITY",
        "DAMAGE_ADJUSTER",
    ),
)
def test_released_no_strategy_inventory_never_claims_operator_submenu(state):
    app = _app_without_strategy()
    app._release_no_strategy_post_run()
    app._mission_mgr.active_battle_observed.return_value = False

    with patch("core.app.run_no_strategy_in_battle_inventory") as inventory:
        handled = app._handle_no_strategy_in_battle_inventory({"state": state})

    assert handled is False
    inventory.assert_not_called()


def test_no_strategy_inventory_requires_running_to_grant_a_new_route():
    app = _app_without_strategy()

    with patch("core.app.run_no_strategy_in_battle_inventory") as inventory:
        handled = app._handle_no_strategy_in_battle_inventory(
            {"state": "PERKS"}
        )

    assert handled is False
    inventory.assert_not_called()


def test_activity_continuity_applies_guarded_save_to_no_strategy_observer():
    app = App.__new__(App)
    app._mission_mgr = MagicMock()
    app._mission_mgr.strategy = None
    app._no_strategy_observer = NoStrategyRunObserver()
    app._no_strategy_observation_active = True
    app._exclusive_validation_ownership_hold = False
    observations = running_attachment_observations(
        {"cards_deck": {"value": "Farm"}}
    )
    app._current_player_save_attachment_context = lambda: (
        PlayerSaveAttachmentContext(
            runtime_session_id="runtime-1",
            activity_scope_id="scope-1",
            target="private-target",
            target_generation=3,
            active_battle_observed=True,
        )
    )

    with patch("core.app.log") as logged:
        app._apply_activity_continuity_outcome(
            SimpleNamespace(running_attachment_observations=observations)
        )

    cards = app._no_strategy_observer.snapshot()["fields"]["cards_deck"]
    assert cards["status"] == "observed"
    assert cards["value"] == {"label": "Farm"}
    assert cards["source"] == "guarded_active_attachment_player_save"
    assert "Applied guarded attachment save" in logged.call_args.args[0]


def test_managed_strategy_clears_stale_observer_before_later_no_strategy_run():
    app = App.__new__(App)
    app._no_strategy_observer = NoStrategyRunObserver()
    app._no_strategy_observation_active = False
    app._no_strategy_attachment_boundary_id = None
    app._no_strategy_inventory_complete = False
    app._no_strategy_inventory_retry_at = 0.0
    app._pending_no_strategy_record = None
    observations = running_attachment_observations(
        {"cards_deck": {"value": "Farm"}}
    )

    app._begin_no_strategy_observation_boundary(
        "attach-1",
        observations=observations,
    )
    assert app._no_strategy_observer.snapshot()["fields"]["cards_deck"][
        "status"
    ] == "observed"

    app._end_no_strategy_observation_boundary()
    app._begin_no_strategy_observation_boundary("attach-2")

    cards = app._no_strategy_observer.snapshot()["fields"]["cards_deck"]
    assert cards["status"] == "not_observed"
    assert cards["value"] is None


def test_activity_continuity_accepts_expected_scope_transition_before_recapture():
    app = App.__new__(App)
    app._mission_mgr = MagicMock()
    app._mission_mgr.strategy = None
    app._no_strategy_observer = NoStrategyRunObserver()
    app._no_strategy_observation_active = True
    app._exclusive_validation_ownership_hold = False
    observations = running_attachment_observations(
        {"cards_deck": {"value": "Farm"}},
        source_scope_id="scope-before-continuity",
        final_scope_id="scope-after-continuity",
    )
    final_context = PlayerSaveAttachmentContext(
        runtime_session_id="runtime-1",
        activity_scope_id="scope-after-continuity",
        target="private-target",
        target_generation=3,
        active_battle_observed=True,
    )
    app._current_player_save_attachment_context = MagicMock(
        side_effect=[
            RuntimeError("observation still names the source scope"),
            final_context,
        ]
    )

    app._apply_activity_continuity_outcome(
        SimpleNamespace(running_attachment_observations=observations)
    )

    assert app._current_player_save_attachment_context.call_args_list == [
        call(),
        call(
            transition_source_activity_scope_id="scope-before-continuity"
        ),
    ]
    cards = app._no_strategy_observer.snapshot()["fields"]["cards_deck"]
    assert cards["status"] == "observed"
    assert cards["value"] == {"label": "Farm"}


def test_attachment_bundle_reaches_perk_monitor_without_profile_facts():
    app = App.__new__(App)
    app._mission_mgr = MagicMock()
    app._mission_mgr.strategy = None
    app._no_strategy_observation_active = False
    app._exclusive_validation_ownership_hold = False
    app._perk_save_monitor = MagicMock()
    app._perk_save_monitor.bind_context.return_value = True
    app._player_save_audit_collector = MagicMock()
    attachment_context = PlayerSaveAttachmentContext(
        runtime_session_id="runtime-1",
        activity_scope_id="scope-1",
        target="private-target",
        target_generation=3,
        active_battle_observed=True,
    )
    app._current_player_save_attachment_context = lambda: attachment_context
    captured = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=PlayerSaveTargetBinding("private-target", 3),
        acquisition_started_at=captured,
        captured_at=captured,
        acquisition_completed_at=captured,
        transport_stable=True,
        snapshot=object(),
    )

    app._apply_activity_continuity_outcome(
        SimpleNamespace(
            running_attachment_observations=None,
            running_attachment_context=attachment_context,
            running_attachment_acquisition=acquisition,
        )
    )

    observed = app._perk_save_monitor.observe_bundle.call_args
    assert observed.args == (acquisition,)
    assert observed.kwargs["context"].target_binding == acquisition.binding
    assert observed.kwargs["context"].activity_scope_id == "scope-1"
    audit = app._player_save_audit_collector.observe_acquisition.call_args
    assert audit.args == (acquisition,)
    assert audit.kwargs == {"reason_code": "forced_running_attachment"}


def test_pending_post_run_inventory_blocks_normal_home_handler():
    app = _app_without_strategy()
    app._pending_no_strategy_record = {"battle_id": "Battle1"}
    app._handle_no_strategy_post_run = MagicMock(return_value=True)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with patch("core.app.handle_home_screen") as home:
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    home.assert_not_called()


def test_pending_game_over_modal_recovery_preserves_enabled_authority():
    app = _app_without_strategy()
    binding = {
        "runtime_id": "runtime-1",
        "pid": 123,
        "adb_target": "localhost:5555",
        "target_generation": 4,
        "activity_scope_run_id": "run-1",
    }
    app._pending_game_over_route = {
        "binding": binding,
        "desired_route": "retry",
        "retry_at": 0.0,
    }
    app._current_control_workflow_evidence = MagicMock(return_value=binding)
    app._runtime_action_guard = MagicMock(return_value=True)
    app._supervisor.control_state = "RUNNING"
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with patch(
        "core.app.restore_game_stats_for_terminal_route",
        return_value=True,
    ) as restore:
        handled = app._advance_pending_game_over_route_recovery("PERKS", frame)

    assert handled is True
    assert app._pending_game_over_route["retry_at"] == 0.0
    restore.assert_called_once()
    assert restore.call_args.args == (frame,)
    assert restore.call_args.kwargs["action_guard_fn"]() is True
    app._supervisor.persist_state.assert_not_called()


def test_pending_game_over_modal_recovery_rejects_changed_battle_binding():
    app = _app_without_strategy()
    expected = {
        "runtime_id": "runtime-1",
        "pid": 123,
        "adb_target": "localhost:5555",
        "target_generation": 4,
        "activity_scope_run_id": "run-1",
    }
    current = {**expected, "activity_scope_run_id": "run-2"}
    app._pending_game_over_route = {
        "binding": expected,
        "desired_route": "retry",
        "retry_at": 0.0,
    }
    app._current_control_workflow_evidence = MagicMock(return_value=current)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with patch("core.app.restore_game_stats_for_terminal_route") as restore:
        handled = app._advance_pending_game_over_route_recovery("PERKS", frame)

    assert handled is False
    assert app._pending_game_over_route is None
    restore.assert_not_called()


def test_no_battle_home_recovers_unfinished_inventory_after_process_reload():
    app = _app_without_strategy()
    app._no_strategy_post_run_recovery_checked = False
    app._no_strategy_observer = MagicMock()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    snapshot = {
        "collection_mode": "no_strategy_observation",
        "finalized": False,
        "fields": {"free_upgrade_locks": {"status": "not_observed"}},
    }
    record = {
        "battle_id": "BattleRecovered",
        "observed_run_configuration": snapshot,
    }

    with (
        patch("core.app.load_pending_no_strategy_record", return_value=record),
        patch(
            "core.app.detect_home_battle_control",
            return_value=SimpleNamespace(control=HomeBattleControl.NEW_BATTLE),
        ),
    ):
        app._recover_no_strategy_post_run("HOME_SCREEN", frame)

    app._no_strategy_observer.restore_snapshot.assert_called_once_with(snapshot)
    assert app._pending_no_strategy_record is record
    assert app._no_strategy_post_run_stage == "locks"
    assert app._no_strategy_post_run_recovery_checked is True
