from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from automation.missions.base import MissionContext
from core.app import App
from core.battle_lifecycle import HomeBattleControl
from core.no_strategy_inventory import (
    NoStrategyInventoryResult,
    NoStrategyInventoryStatus,
)
from core.no_strategy_observer import NoStrategyRunObserver
from core.no_strategy_post_run import NoStrategyPostRunPaused
from core.run_state import AUTOMATION, ExecMode


def _app_without_strategy():
    app = App.__new__(App)
    app._mission_mgr = MagicMock()
    app._mission_mgr.strategy = None
    app._mission_mgr.ctx = MissionContext(data={"mission_vars": {}})
    app._mission_mgr.session_preflight_repair_in_progress.return_value = False
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

    with patch("core.app.handle_game_over", return_value=record) as game_over:
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

    home.assert_called_once_with(restart_enabled=True)


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


def test_activity_continuity_applies_guarded_save_to_no_strategy_observer():
    app = App.__new__(App)
    app._mission_mgr = MagicMock()
    app._mission_mgr.strategy = None
    app._no_strategy_observer = NoStrategyRunObserver()
    app._no_strategy_observation_active = True
    app._exclusive_validation_ownership_hold = False
    observations = {
        "schema_version": 1,
        "source": "guarded_active_attachment_player_save",
        "mapping_id": "data-9-game-1073",
        "captured_at": "2026-08-06T23:31:05+00:00",
        "checks": {"cards_deck": {"value": "Farm"}},
    }

    with patch("core.app.log") as logged:
        app._apply_activity_continuity_outcome(
            SimpleNamespace(validated_profile_observations=observations)
        )

    cards = app._no_strategy_observer.snapshot()["fields"]["cards_deck"]
    assert cards["status"] == "observed"
    assert cards["value"] == {"label": "Farm"}
    assert cards["source"] == "guarded_active_attachment_player_save"
    assert "Applied guarded attachment save" in logged.call_args.args[0]


def test_pending_post_run_inventory_blocks_normal_home_handler():
    app = _app_without_strategy()
    app._pending_no_strategy_record = {"battle_id": "Battle1"}
    app._handle_no_strategy_post_run = MagicMock(return_value=True)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with patch("core.app.handle_home_screen") as home:
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    home.assert_not_called()


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
