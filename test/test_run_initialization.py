import copy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import yaml

from automation.missions.base import MissionContext
from automation.missions.manager import MissionManager
from automation.strategies import get_strategy
from automation.strategies.base import BaseStrategy
from automation.strategies.yaml_strategy import YamlStrategy
from core.app import App
from core.action_executor import execute_actions
from core.app_setup import config_from_args, parse_args
from core.automation_supervisor import AutomationSupervisor
from core.run_state import AUTOMATION, RunState
from core.status_report import StatusReporter
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE_NAMES = ("gc_farm_t18", "gc_farm_t19_experiment")
SOURCE_PATHS = {
    name: ROOT / "config" / "strategies" / f"{name}.source.yaml"
    for name in PROFILE_NAMES
}
STRATEGY_PATHS = {
    name: ROOT / "config" / "strategies" / f"{name}.strategy.yaml"
    for name in PROFILE_NAMES
}


class _RunCountingStrategy(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.run_starts = 0

    def on_run_start(self, ctx) -> None:
        super().on_run_start(ctx)
        self.run_starts += 1


class _IncompleteInitializationStrategy(BaseStrategy):
    def requires_run_initialization(self) -> bool:
        return True

    def is_run_initialization_complete(self, ctx: MissionContext) -> bool:
        return False


class AdbPortTests(unittest.TestCase):
    def test_adb_port_defaults_to_5555(self):
        config = config_from_args(parse_args([]))
        self.assertEqual(config.adb_port, 5555)

    def test_adb_port_accepts_override(self):
        config = config_from_args(parse_args(["--adb-port", "5565"]))
        self.assertEqual(config.adb_port, 5565)

    def test_adb_port_rejects_out_of_range_value(self):
        with self.assertRaises(SystemExit):
            parse_args(["--adb-port", "65536"])


class DefaultStrategyTests(unittest.TestCase):
    def test_gc_is_the_default_strategy(self):
        config = config_from_args(parse_args([]))
        self.assertEqual(config.strategy_name, "gc")

    def test_gc_default_can_be_explicitly_disabled(self):
        config = config_from_args(parse_args(["--strategy", "none"]))
        self.assertEqual(config.strategy_name, "none")

    def test_named_gc_profiles_are_selectable(self):
        for profile_name in PROFILE_NAMES:
            with self.subTest(profile=profile_name):
                config = config_from_args(parse_args(["--strategy", profile_name]))
                self.assertEqual(config.strategy_name, profile_name)

    def test_gc_alias_resolves_to_tier_18_profile(self):
        strategy = get_strategy("gc")
        self.assertIsInstance(strategy, YamlStrategy)
        self.assertEqual(strategy.name, "gc_farm_t18")

    def test_tactical_alias_resolves_to_profile_without_seeded_completion(self):
        strategy = get_strategy("gc_manual_target_priority")
        self.assertIsInstance(strategy, YamlStrategy)
        self.assertEqual(strategy.name, "gc_farm_t19_experiment")
        self.assertNotIn("target_priority_checked", strategy.vars)


class RunBoundaryTests(unittest.TestCase):
    def test_unknown_frame_does_not_create_a_second_run_start(self):
        strategy = _RunCountingStrategy()
        manager = MissionManager(None, strategy)
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        manager.maybe_run_start({"state": "UNKNOWN"})
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertEqual(strategy.run_starts, 1)

    def test_home_resume_preserves_the_current_run_boundary(self):
        strategy = _RunCountingStrategy()
        manager = MissionManager(None, strategy)
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "RESUME_BATTLE"}
        )
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertEqual(strategy.run_starts, 1)

    def test_home_new_battle_establishes_a_run_boundary(self):
        strategy = _RunCountingStrategy()
        manager = MissionManager(None, strategy)
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
        )
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertEqual(strategy.run_starts, 2)

    def test_unknown_home_control_does_not_invent_a_run_boundary(self):
        strategy = _RunCountingStrategy()
        manager = MissionManager(None, strategy)
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        manager.maybe_run_start({"state": "HOME_SCREEN"})
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertEqual(strategy.run_starts, 1)

    def test_game_over_establishes_a_run_boundary(self):
        strategy = _RunCountingStrategy()
        manager = MissionManager(None, strategy)
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        manager.maybe_run_start({"state": "GAME_OVER"})
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertEqual(strategy.run_starts, 2)


class GcFarmProfileTests(unittest.TestCase):
    def setUp(self):
        self.strategy = YamlStrategy.from_file(str(STRATEGY_PATHS["gc_farm_t18"]))
        self.ctx = MissionContext()
        self.strategy.on_start(self.ctx)
        self.strategy.on_run_start(self.ctx)
        self.detection = {"state": "RUNNING"}
        self.screen = object()

    def _tick(self):
        with patch("automation.strategies.yaml_strategy.log_mission"):
            return self.strategy.tick(self.ctx, self.screen, self.detection)

    def test_generated_profiles_match_compact_sources(self):
        for profile_name in PROFILE_NAMES:
            with self.subTest(profile=profile_name):
                source = yaml.safe_load(
                    SOURCE_PATHS[profile_name].read_text(encoding="utf-8")
                )
                generated = yaml.safe_load(
                    STRATEGY_PATHS[profile_name].read_text(encoding="utf-8")
                )
                self.assertEqual(generated, build_strategy_yaml(source))

    def test_profiles_reuse_the_same_gc_behavior(self):
        plans = {
            name: yaml.safe_load(path.read_text(encoding="utf-8"))
            for name, path in STRATEGY_PATHS.items()
        }
        self.assertEqual(plans["gc_farm_t18"]["meta"]["family"], "gc_farm")
        self.assertEqual(
            plans["gc_farm_t19_experiment"]["meta"]["family"], "gc_farm"
        )
        shared_t18_rules = [
            rule
            for rule in plans["gc_farm_t18"]["rules"]
            if rule["name"] != "ensure_target_priority"
        ]
        self.assertEqual(plans["gc_farm_t19_experiment"]["rules"], shared_t18_rules)

    def test_enforce_profile_runs_level_skips_then_requested_target_priority(self):
        actions = self._tick()
        self.assertEqual(actions, [{"type": "level_skip_initialize"}])

        mv = self.ctx.data["mission_vars"]
        mv.update(ehls_completed=True, eals_completed=True)
        actions = self._tick()
        expected_order = yaml.safe_load(
            SOURCE_PATHS["gc_farm_t18"].read_text(encoding="utf-8")
        )["initialization"]["target_priority"]["order"]
        self.assertEqual(
            actions,
            [{"type": "target_priority_ensure", "order": expected_order}],
        )
        self.assertTrue(mv["ehls_completed"])
        self.assertTrue(mv["eals_completed"])

    def test_preserve_profile_runs_level_skips_without_target_priority_action(self):
        strategy = get_strategy("gc_farm_t19_experiment")
        self.assertIsInstance(strategy, YamlStrategy)
        manager = MissionManager(None, strategy)
        manager.start()
        manager.maybe_run_start({"state": "RUNNING"})

        with patch("automation.strategies.yaml_strategy.log_mission"):
            actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})
        self.assertEqual(actions, [{"type": "level_skip_initialize"}])

        mv = manager.ctx.data["mission_vars"]
        self.assertNotIn("target_priority_checked", mv)
        mv.update(ehls_completed=True, eals_completed=True)
        with patch("automation.strategies.yaml_strategy.log_mission"):
            actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

        self.assertIsNone(actions)
        self.assertFalse(manager.run_initialization_pending())
        self.assertFalse(
            any(
                action.get("type") == "target_priority_ensure"
                for rule in strategy.rules
                for action in rule.get("do", [])
            )
        )

    def test_target_priority_check_persists_across_run_boundaries(self):
        mv = self.ctx.data["mission_vars"]
        mv["target_priority_checked"] = True

        self.strategy.on_run_start(self.ctx)

        self.assertTrue(mv["target_priority_checked"])

    def test_enforce_gate_waits_until_requested_order_is_verified(self):
        manager = MissionManager(None, self.strategy)
        manager.ctx = self.ctx
        detection = {"state": "RUNNING"}
        mv = self.ctx.data["mission_vars"]
        manager.maybe_run_start(detection)

        self.assertTrue(manager.run_initialization_pending())
        with patch("automation.strategies.yaml_strategy.log_mission"):
            self.assertEqual(
                self.strategy.tick(self.ctx, object(), detection),
                [{"type": "level_skip_initialize"}],
            )
        mv["ehls_completed"] = True
        mv["eals_completed"] = True
        self.assertTrue(manager.run_initialization_pending())

        expected_order = yaml.safe_load(
            SOURCE_PATHS["gc_farm_t18"].read_text(encoding="utf-8")
        )["initialization"]["target_priority"]["order"]
        with patch("automation.strategies.yaml_strategy.log_mission"):
            actions = self.strategy.tick(self.ctx, object(), detection)
        self.assertEqual(
            actions,
            [{"type": "target_priority_ensure", "order": expected_order}],
        )
        mv["last_detection_state"] = "RUNNING"

        with patch(
            "core.action_executor.ensure_target_priority_order", return_value=False
        ) as ensure:
            execute_actions(
                object(),
                [{**actions[0], "_strategy": True}],
                self.ctx,
            )
        ensure.assert_called_once_with(expected=expected_order)
        self.assertTrue(manager.run_initialization_pending())

        with patch(
            "core.action_executor.ensure_target_priority_order", return_value=True
        ) as ensure:
            execute_actions(
                object(),
                [{**actions[0], "_strategy": True}],
                self.ctx,
            )
        ensure.assert_called_once_with(expected=expected_order)
        self.assertFalse(manager.run_initialization_pending())

    def test_profiles_remain_incomplete_across_transient_unknown_frames(self):
        for profile_name in PROFILE_NAMES:
            with self.subTest(profile=profile_name):
                strategy = get_strategy(profile_name)
                manager = MissionManager(None, strategy)
                manager.start()

                for state in ("RUNNING", "UNKNOWN", "RUNNING"):
                    manager.maybe_run_start({"state": state})
                    self.assertTrue(manager.run_initialization_pending(), state)

    def test_tiers_can_generate_different_enforced_orders(self):
        tier_18_source = yaml.safe_load(
            SOURCE_PATHS["gc_farm_t18"].read_text(encoding="utf-8")
        )
        tier_19_source = copy.deepcopy(tier_18_source)
        tier_19_source["meta"].update(name="gc_farm_t19_experiment", tier=19)
        tier_19_source["initialization"]["target_priority"]["order"] = list(
            reversed(
                tier_18_source["initialization"]["target_priority"]["order"]
            )
        )

        tier_18_plan = build_strategy_yaml(tier_18_source)
        tier_19_plan = build_strategy_yaml(tier_19_source)
        tier_18_action = tier_18_plan["rules"][-1]["do"][0]
        tier_19_action = tier_19_plan["rules"][-1]["do"][0]

        self.assertNotEqual(tier_18_action["order"], tier_19_action["order"])
        self.assertEqual(
            tier_19_action["order"],
            tier_19_source["initialization"]["target_priority"]["order"],
        )

    def test_preserve_profile_rejects_an_order(self):
        source = yaml.safe_load(
            SOURCE_PATHS["gc_farm_t19_experiment"].read_text(encoding="utf-8")
        )
        source["initialization"]["target_priority"]["order"] = ["Basic"]

        with self.assertRaisesRegex(ValueError, "preserve mode must not supply"):
            build_strategy_yaml(source)

    def test_enforce_profile_rejects_an_incomplete_order(self):
        source = yaml.safe_load(
            SOURCE_PATHS["gc_farm_t18"].read_text(encoding="utf-8")
        )
        source["initialization"]["target_priority"]["order"].pop()

        with self.assertRaisesRegex(ValueError, "every target exactly once"):
            build_strategy_yaml(source)


class PausedStartupObservationTests(unittest.TestCase):
    def test_paused_startup_observes_and_reports_without_actions(self):
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        strategy = _IncompleteInitializationStrategy()
        manager = MagicMock()
        manager.ctx = MissionContext(data={"mission_vars": {}})
        manager.strategy = strategy
        manager.run_initialization_pending.return_value = True

        supervisor = MagicMock()
        supervisor.is_paused = True
        supervisor.auto_return_secs = 900

        app = App.__new__(App)
        app._config = SimpleNamespace(wait_on_start=False)
        app._supervisor = supervisor
        app._mission_mgr = manager
        app._state_tracker = MagicMock()
        app._status_reporter = MagicMock()
        app._match_trace = False
        app._last_wave_value = None
        app._last_wave_conf = -1.0
        app._last_wave_ts = 0.0
        app._blind_tapper_suspended = False
        app._run_initialization_gate_logged = False
        app._capture_frame = MagicMock(side_effect=[frame, KeyboardInterrupt])
        app._resolve_upgrade_detail_overlay = MagicMock()
        app._handle_primary_states = MagicMock()

        with (
            patch("core.app.ensure_adb_connected", return_value=False),
            patch("core.app.threading.Thread") as thread,
            patch(
                "core.app.detect_state_and_overlays",
                return_value={"state": "RUNNING", "overlays": []},
            ) as detect,
            patch("core.app.detect_wave_number_from_image", return_value=(1, 99.0)),
            patch("core.app.stop_blind_gem_tapper", return_value=False),
            patch("core.app.start_blind_gem_tapper") as start_tapper,
            patch("core.app.handle_unknown_state") as recover,
            patch("core.app.time.sleep"),
            patch("core.app.log") as runtime_log,
        ):
            app.run()

        thread.return_value.start.assert_called_once_with()
        detect.assert_called_once_with(frame, log_matches=False)
        manager.maybe_run_start.assert_called_once()
        app._state_tracker.update.assert_called_once()
        app._status_reporter.maybe_report.assert_called_once_with(
            img=frame,
            ui_state="RUNNING",
            menu=None,
            secondary=set(),
            overlays=set(),
            allow_actions=False,
        )

        manager.tick.assert_not_called()
        manager.handle_overlays.assert_not_called()
        manager.on_state.assert_not_called()
        app._resolve_upgrade_detail_overlay.assert_not_called()
        app._handle_primary_states.assert_not_called()
        supervisor.auto_return_check.assert_not_called()
        start_tapper.assert_not_called()
        recover.assert_not_called()
        self.assertFalse(
            any(
                call.args and "Startup gate complete" in str(call.args[0])
                for call in runtime_log.call_args_list
            )
        )

    def test_read_only_status_reporting_does_not_refresh_coin_display(self):
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        supervisor = AutomationSupervisor(
            control_file="logs/unused-test-control.json",
            auto_return_enabled=False,
        )
        supervisor.schedule_total_snapshot("test")
        supervisor._last_coins_val = Decimal("1")
        reporter = StatusReporter(
            interval_secs=1,
            supervisor=supervisor,
            save_wave_samples=None,
            save_coin_samples=None,
            coins_log_base="logs/unused-test-coins.csv",
            coins_log_enabled=False,
        )

        original_state = AUTOMATION.state
        try:
            AUTOMATION.state = RunState.RUNNING
            with (
                patch(
                    "core.status_report.detect_wave_number_from_image",
                    return_value=(1, 99.0),
                ),
                patch(
                    "core.status_report.detect_coins_from_image",
                    return_value=(Decimal("100"), 99.0, False),
                ),
                patch.object(supervisor, "capture_total_snapshot") as total_snapshot,
                patch("core.automation_supervisor.tap_if_visible") as tap,
                patch("core.status_report.log_status") as status_log,
            ):
                reporter.maybe_report(
                    img=frame,
                    ui_state="RUNNING",
                    menu=None,
                    secondary=set(),
                    overlays=set(),
                    now_ts=1.0,
                    allow_actions=False,
                )
        finally:
            AUTOMATION.state = original_state

        total_snapshot.assert_not_called()
        tap.assert_not_called()
        status_log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
