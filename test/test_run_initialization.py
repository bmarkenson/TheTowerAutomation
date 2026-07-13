from pathlib import Path
import unittest
from unittest.mock import patch

import yaml

from automation.missions.base import MissionContext
from automation.missions.manager import MissionManager
from automation.strategies.base import BaseStrategy
from automation.strategies.yaml_strategy import YamlStrategy
from core.app_setup import config_from_args, parse_args
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "config" / "strategies" / "gc_skipper.source.yaml"
STRATEGY_PATH = ROOT / "config" / "strategies" / "gc_skipper.strategy.yaml"


class _RunCountingStrategy(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.run_starts = 0

    def on_run_start(self, ctx) -> None:
        super().on_run_start(ctx)
        self.run_starts += 1


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


class RunBoundaryTests(unittest.TestCase):
    def test_unknown_frame_does_not_create_a_second_run_start(self):
        strategy = _RunCountingStrategy()
        manager = MissionManager(None, strategy)
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        manager.maybe_run_start({"state": "UNKNOWN"})
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertEqual(strategy.run_starts, 1)

    def test_home_screen_establishes_a_new_run_boundary(self):
        strategy = _RunCountingStrategy()
        manager = MissionManager(None, strategy)
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        manager.maybe_run_start({"state": "HOME_SCREEN"})
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertEqual(strategy.run_starts, 2)


class GcSkipperSequenceTests(unittest.TestCase):
    def setUp(self):
        self.strategy = YamlStrategy.from_file(str(STRATEGY_PATH))
        self.ctx = MissionContext()
        self.strategy.on_start(self.ctx)
        self.strategy.on_run_start(self.ctx)
        self.detection = {"state": "RUNNING"}
        self.screen = object()

    def _tick(self):
        with patch("automation.strategies.yaml_strategy.log_mission"):
            return self.strategy.tick(self.ctx, self.screen, self.detection)

    def test_generated_strategy_matches_manual_source(self):
        source = yaml.safe_load(SOURCE_PATH.read_text(encoding="utf-8"))
        generated = yaml.safe_load(STRATEGY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(generated, build_strategy_yaml(source))

    def test_ehls_then_eals_then_target_priority(self):
        actions = self._tick()
        self.assertEqual(actions, [{"type": "level_skip_initialize"}])

        mv = self.ctx.data["mission_vars"]
        mv.update(ehls_completed=True, eals_completed=True)
        actions = self._tick()
        self.assertEqual(actions, [{"type": "target_priority_ensure"}])
        self.assertTrue(mv["ehls_completed"])
        self.assertTrue(mv["eals_completed"])

    def test_target_priority_check_persists_across_run_boundaries(self):
        mv = self.ctx.data["mission_vars"]
        mv["target_priority_checked"] = True

        self.strategy.on_run_start(self.ctx)

        self.assertTrue(mv["target_priority_checked"])

    def test_initialization_gate_requires_both_skips_and_target_priority(self):
        manager = MissionManager(None, self.strategy)
        manager.ctx = self.ctx
        detection = {"state": "RUNNING"}
        mv = self.ctx.data["mission_vars"]

        self.assertTrue(manager.run_initialization_pending(detection))
        mv["ehls_completed"] = True
        mv["eals_completed"] = True
        self.assertTrue(manager.run_initialization_pending(detection))
        mv["target_priority_checked"] = True
        self.assertFalse(manager.run_initialization_pending(detection))


if __name__ == "__main__":
    unittest.main()
