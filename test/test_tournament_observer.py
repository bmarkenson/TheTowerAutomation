import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import yaml

from automation.missions.base import MissionContext
from automation.missions.manager import MissionManager
from automation.strategies import get_strategy
from core.action_executor import execute_actions
from core.app import App
from core.gc_preflight_navigation import (
    GcLivePreflightResult,
    GcPreflightNavigationStatus,
)
from core.run_state import AUTOMATION, ExecMode
from core.tournament_preflight import (
    validate_tournament_session_preflight_screens,
)
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "config" / "strategies" / "tournament.source.yaml"
PLAN_PATH = ROOT / "config" / "strategies" / "tournament.strategy.yaml"


def _load(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict)
    return data


def test_tournament_generated_plan_matches_compact_source():
    assert build_strategy_yaml(_load(SOURCE_PATH)) == _load(PLAN_PATH)


def test_tournament_strategy_declares_exclusive_validation_then_observes():
    strategy = get_strategy("tournament")

    assert strategy is not None
    assert strategy.name == "tournament"
    assert strategy.runtime_policy() == {
        "handlers": ["ad_gem", "game_over", "game_speed"],
        "auto_return": False,
        "game_over_mode": "wait",
        "home_preflight": True,
        "session_preflight_on_attach": True,
        "exclusive_validation": {
            "battle_kind": "ordinary_new_battle",
            "timeout_seconds": 300,
            "ready_message": (
                "Tournament validation passed; waiting for operator confirmation"
            ),
            "failure_prefix": "Tournament validation failed",
            "operator_launch": {
                "kind": "tournament_battle",
                "timeout_seconds": 60,
                "prompt_title": "Tournament validation passed",
                "prompt_message": (
                    "Start the Tournament now? Automation will verify the "
                    "current Home or Tournament entry screen and start exactly "
                    "one Tournament battle."
                ),
                "reminder": (
                    "When the Tournament battle begins, set Target Priorities "
                    "for the current Tournament Battle Conditions."
                ),
            },
        },
    }
    assert strategy.run_configuration()["profile"] == "tournament"
    assert strategy._run_initialization_assertions == [
        "ehls_completed",
        "eals_completed",
    ]
    assert len(strategy.rules) == 5
    attached_validation = strategy.rules[0]
    assert attached_validation["name"] == (
        "validate_requested_attached_session_preflight"
    )
    assert attached_validation["attached_validation_only"] is True
    assert attached_validation["do"][0]["type"] == "session_preflight"
    assert attached_validation["do"][0]["allow_repair"] is False
    level_skip_rule = next(
        rule
        for rule in strategy.rules
        if rule["name"] == "initialize_tournament_level_skips"
    )
    assert level_skip_rule["gate_phase"] == "run_initialization"
    assert level_skip_rule["assert"] == [
        "!exclusive_validation_battle",
        "!eals_completed",
    ]
    assert level_skip_rule["do"] == [{"type": "level_skip_initialize"}]
    damage_rule = next(
        rule
        for rule in strategy.rules
        if rule["name"] == "enforce_tournament_damage_slider"
    )
    damage_action = damage_rule["do"][0]
    assert damage_rule["run_when_attached"] is True
    assert damage_action == {
        "type": "damage_slider_configure",
        "mode": "enforce",
        "value": "1E2%",
    }
    orb_rule = next(
        rule
        for rule in strategy.rules
        if rule["name"] == "enforce_tournament_orb_distance"
    )
    orb_action = orb_rule["do"][0]
    assert orb_rule["run_when_attached"] is True
    assert orb_rule["assert"] == [
        "damage_slider_checked",
        "!orb_distance_checked",
    ]
    assert orb_action == {
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
    session_rule = next(
        rule
        for rule in strategy.rules
        if rule["name"] == "validate_tournament_session_preflight"
    )
    action = session_rule["do"][0]
    assert action["type"] == "session_preflight"
    assert action["validator"] == "tournament"
    assert "auto_pick_perks" not in action["requirements"]
    assert action["requirements"]["card_recharge_modes"] == {
        "Demon Mode": "auto_reactivate",
        "Nuke": "ready_after_recharge",
    }
    assert action["allow_repair"] is False
    assert action["mismatch_policy"] == "notify"
    assert action["requirements"]["ultimate_weapons"]["Poison Swamp"]["stun"] == "on"
    assert session_rule["run_when_attached"] is True
    assert strategy._session_preflight_assertions == [
        "damage_slider_checked",
        "orb_distance_checked",
        "gc_session_preflight_attempted",
    ]
    assert "auto_pick_perks" not in strategy.run_configuration()["settings"]
    assert strategy.run_configuration()["settings"]["card_recharge_modes"] == {
        "Demon Mode": "auto_reactivate",
        "Nuke": "ready_after_recharge",
    }
    assert (
        strategy.run_configuration()["settings"]["ultimate_weapons"][
            "Poison Swamp"
        ]["stun"]
        == "on"
    )
    assert strategy.run_configuration()["loadout"]["damage_slider"] == {
        "mode": "enforce",
        "value": "1E2%",
    }
    assert strategy.run_configuration()["loadout"]["orb_distance"] == {
        "mode": "enforce",
        "preset": "tournament_range_98_38",
        "resolved": {
            "range_basis": "98.38m",
            "extra": "87.16m",
            "workshop": "80.37m",
        },
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


def test_tournament_mismatch_is_recorded_without_requesting_repair():
    strategy = get_strategy("tournament")
    assert strategy is not None
    action = next(
        rule
        for rule in strategy.rules
        if rule["name"] == "validate_tournament_session_preflight"
    )["do"][0]
    ctx = MissionContext(
        data={
            "mission_vars": {
                "last_detection_state": "RUNNING",
                "damage_slider_checked": True,
                "orb_distance_checked": True,
                "eals_completed": True,
            }
        }
    )
    evidence_payload = {
        "valid": False,
        "failed_checks": ["modules"],
        "modules": {
            "slots": [
                {
                    "slot_key": "generator_assist",
                    "expected": "Singularity Harness",
                    "actual": "Galaxy Compressor",
                    "match_status": "matched",
                    "valid": False,
                }
            ]
        },
    }
    evidence = SimpleNamespace(
        as_dict=lambda: evidence_payload,
        requires_no_battle_repair=True,
        failed_checks=("modules",),
    )
    result = GcLivePreflightResult(
        GcPreflightNavigationStatus.MISMATCH,
        "configuration mismatch",
        evidence,
    )

    with (
        patch(
            "core.action_executor.run_read_only_gc_preflight",
            return_value=result,
        ) as run_preflight,
        patch("core.action_executor.log_mission") as mission_log,
    ):
        execute_actions(object(), [{**action, "_strategy": True}], ctx)

    run_preflight.assert_called_once_with(
        action["requirements"],
        validate_fn=validate_tournament_session_preflight_screens,
    )
    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_attempted"]
    assert not variables["gc_session_preflight_completed"]
    assert not variables["gc_session_preflight_blocked"]
    assert not variables["gc_session_preflight_repair_required"]
    assert variables["gc_session_preflight_failed_checks"] == ["modules"]
    assert strategy.is_session_preflight_complete(ctx)
    assert not strategy.tick(ctx, object(), {"state": "RUNNING"})
    mission_log.assert_any_call(
        "[SESSION_PREFLIGHT] Read-only observer mismatch recorded — "
        "Modules generator assist: expected Singularity Harness, observed "
        "Galaxy Compressor. Observation and terminal capture continue without "
        "operator action.",
        "WARN",
    )
    mission_log.assert_any_call(
        "[SESSION_PREFLIGHT] mismatch_evidence="
        + json.dumps(evidence_payload, sort_keys=True),
        "DEBUG",
    )


def test_tournament_attachment_enforces_battle_loadout_before_preflight():
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(
        None,
        strategy,
        defer_startup_gates_until_next_run=True,
    )
    manager.start()

    manager.maybe_run_start({"state": "RUNNING"})
    actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

    assert manager.session_preflight_pending()
    assert len(actions) == 1
    assert actions[0]["type"] == "damage_slider_configure"
    assert actions[0]["mode"] == "enforce"
    assert actions[0]["value"] == "1E2%"

    manager.ctx.data["mission_vars"]["damage_slider_checked"] = True
    actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

    assert len(actions) == 1
    assert actions[0] == {
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

    manager.ctx.data["mission_vars"]["orb_distance_checked"] = True
    actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

    assert len(actions) == 1
    assert actions[0]["type"] == "session_preflight"
    assert actions[0]["validator"] == "tournament"


def test_tournament_policy_suppresses_unrelated_runtime_handlers():
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(strategy=get_strategy("tournament"))

    assert app._handler_enabled("ad_gem")
    assert app._handler_enabled("game_over")
    assert app._handler_enabled("game_speed")
    assert not app._handler_enabled("floating_gem")
    assert not app._handler_enabled("daily_gem")
    assert not app._handler_enabled("mission_rewards")
    assert not app._handler_enabled("event_mission_warnings")
    assert not app._handler_enabled("home")
    assert not app._handler_enabled("auto_return")
    assert not app._handler_enabled("coin_display")
    assert not app._handler_enabled("upgrade_detail")
    assert not app._handler_enabled("unknown_recovery")


def test_tournament_does_not_start_floating_gem_tapper_without_ad_gem():
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(strategy=get_strategy("tournament"))
    app._blind_tapper_suspended = False

    with (
        patch("core.app.start_blind_gem_tapper") as start,
        patch("core.app.stop_blind_gem_tapper") as stop,
    ):
        app._sync_floating_gem_tapper(
            state="RUNNING",
            actions_blocked=False,
        )

    start.assert_not_called()
    stop.assert_not_called()


def test_tournament_main_loop_keeps_status_and_recovery_read_only():
    strategy = get_strategy("tournament")
    assert strategy is not None
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    manager = MagicMock()
    manager.strategy = strategy
    manager.ctx = MissionContext(data={"mission_vars": {}})
    manager.run_initialization_pending.return_value = False
    manager.session_preflight_pending.return_value = False

    app = App.__new__(App)
    app._config = SimpleNamespace(wait_on_start=False)
    app._supervisor = MagicMock(is_paused=False, auto_return_secs=900)
    app._adb_connection_coordinator = MagicMock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    app._mission_mgr = manager
    app._state_tracker = MagicMock()
    app._status_reporter = MagicMock()
    app._event_mission_tracker = MagicMock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_repair_denial_logged = False
    app._capture_frame = MagicMock(side_effect=[frame, KeyboardInterrupt])
    app._resolve_upgrade_detail_overlay = MagicMock()
    app._handle_primary_states = MagicMock()
    previous_mode = AUTOMATION.mode

    try:
        with (
            patch("core.app.threading.Thread"),
            patch(
                "core.app.detect_state_and_overlays",
                return_value={
                    "state": "RUNNING",
                    "menu": "UW_MENU",
                    "secondary_states": ["TOURNAMENT"],
                    "overlays": ["MENU_OPEN"],
                },
            ),
            patch("core.app.detect_wave_number_from_image", return_value=(1800, 99.0)),
            patch("core.app.start_blind_gem_tapper") as start_tapper,
            patch("core.app.stop_blind_gem_tapper", return_value=False),
            patch("core.app.handle_unknown_state") as unknown_recovery,
            patch("core.app.time.sleep"),
        ):
            app.run()
    finally:
        AUTOMATION.mode = previous_mode

    app._resolve_upgrade_detail_overlay.assert_not_called()
    unknown_recovery.assert_not_called()
    app._supervisor.auto_return_check.assert_not_called()
    app._status_reporter.maybe_report.assert_called_once_with(
        img=frame,
        ui_state="RUNNING",
        menu="UW_MENU",
        secondary={"TOURNAMENT"},
        overlays={"MENU_OPEN"},
        wave=1800,
        wave_conf=99.0,
        allow_actions=False,
    )
    start_tapper.assert_not_called()


def test_tournament_running_handler_collects_only_visible_ad_gem():
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(strategy=get_strategy("tournament"))
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    app._handle_daily_gem_if_due = lambda *_args: (_ for _ in ()).throw(
        AssertionError("daily gem handler should be disabled")
    )
    app._handle_mission_rewards_if_due = lambda *_args: (_ for _ in ()).throw(
        AssertionError("mission reward handler should be disabled")
    )

    with patch("core.app.handle_ad_gem") as ad_gem:
        app._handle_primary_states("RUNNING", {"AD_GEMS_AVAILABLE"}, frame)

    ad_gem.assert_called_once_with()


def test_tournament_game_over_waits_and_records_profile_evidence():
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MagicMock()
    manager.strategy = strategy
    manager.ctx = MissionContext(
        data={"mission_vars": {"gc_session_preflight_evidence": {"valid": True}}}
    )
    manager.session_preflight_repair_in_progress.return_value = False
    app = App.__new__(App)
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = 1970
    app._last_wave_conf = 99.0
    app._supervisor = MagicMock()
    app._status_reporter = MagicMock()
    app._status_reporter.coin_rate_samples = []
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    previous_mode = AUTOMATION.mode

    try:
        with patch("core.app.handle_game_over") as game_over:
            app._handle_primary_states("GAME_OVER", set(), frame)
    finally:
        AUTOMATION.mode = previous_mode

    kwargs = game_over.call_args.kwargs
    app._supervisor.persist_mode.assert_called_once_with("WAIT")
    assert kwargs["capture_stats"] is True
    assert kwargs["battle_context"]["run_configuration"]["profile"] == "tournament"
    assert kwargs["battle_context"]["session_preflight_evidence"] == {
        "valid": True
    }
    manager.on_game_over.assert_called_once_with()
    app._status_reporter.reset_coin_rate_samples.assert_called_once_with()


def test_tournament_results_are_recorded_once_without_dismissing_dialog():
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MagicMock()
    manager.strategy = strategy
    manager.ctx = MissionContext(data={"mission_vars": {}})
    app = App.__new__(App)
    app._mission_mgr = manager
    app._supervisor = MagicMock()
    app._status_reporter = MagicMock()
    app._status_reporter.coin_rate_samples = []
    app._last_wave_value = 2028
    app._last_wave_conf = 99.0
    app._tournament_results_captured = False
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with (
        patch(
            "core.app.handle_tournament_results",
            return_value={"tournament_id": "Tournament20260718"},
        ) as tournament_results,
        patch("core.app.handle_game_over") as normal_game_over,
    ):
        app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)
        app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)

    app._supervisor.persist_mode.assert_called_once_with("WAIT")
    tournament_results.assert_called_once()
    assert tournament_results.call_args.args == (frame,)
    assert (
        tournament_results.call_args.kwargs["battle_context"]["run_configuration"][
            "profile"
        ]
        == "tournament"
    )
    normal_game_over.assert_not_called()
    manager.on_game_over.assert_called_once_with()
    app._status_reporter.reset_coin_rate_samples.assert_called_once_with()
