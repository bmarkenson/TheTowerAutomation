from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import yaml

from automation.missions.base import MissionContext
from automation.missions.manager import MissionManager
from automation.strategies.base import BaseStrategy
from automation.strategies.yaml_strategy import YamlStrategy
from core.app import App
from core.app_setup import config_from_args, parse_args
from core.automation_supervisor import AutomationSupervisor
from core.run_state import AUTOMATION, RunState
from core.status_report import StatusReporter
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
        manager.maybe_run_start(detection)

        self.assertTrue(manager.run_initialization_pending())
        mv["ehls_completed"] = True
        mv["eals_completed"] = True
        self.assertTrue(manager.run_initialization_pending())
        mv["target_priority_checked"] = True
        self.assertFalse(manager.run_initialization_pending())

    def test_unknown_frame_does_not_complete_initialization_gate(self):
        strategy = _IncompleteInitializationStrategy()
        manager = MissionManager(None, strategy)
        manager.start()

        for state in ("RUNNING", "UNKNOWN", "RUNNING"):
            manager.maybe_run_start({"state": state})
            self.assertTrue(manager.run_initialization_pending(), state)


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
