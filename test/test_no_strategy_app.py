from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from automation.missions.base import MissionContext
from core.app import App
from core.battle_lifecycle import HomeBattleControl
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
