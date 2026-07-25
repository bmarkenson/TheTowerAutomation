import copy
from datetime import datetime
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
from core.free_upgrade_locks import FARM_FREE_UPGRADE_LOCKS
from core.gc_preflight_navigation import (
    GcLivePreflightResult,
    GcPreflightNavigationStatus,
)
from core.perk_configuration import FARM_AUTO_PICK_ORDER, FARM_PERK_BANS
from core.run_state import AUTOMATION, RunState
from core.status_report import StatusReporter
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE_NAMES = ("gc_farm_t18", "gc_farm_t19_experiment")
FARM_PROFILE_NAMES = ("farm_t18", "farm_t19_experiment")
SOURCE_PATHS = {
    name: ROOT / "config" / "strategies" / f"{name}.source.yaml"
    for name in PROFILE_NAMES
}
STRATEGY_PATHS = {
    name: ROOT / "config" / "strategies" / f"{name}.strategy.yaml"
    for name in PROFILE_NAMES
}
FARM_SOURCE_PATHS = {
    name: ROOT / "config" / "strategies" / f"{name}.source.yaml"
    for name in FARM_PROFILE_NAMES
}
FARM_STRATEGY_PATHS = {
    name: ROOT / "config" / "strategies" / f"{name}.strategy.yaml"
    for name in FARM_PROFILE_NAMES
}


def _free_upgrade_lock_boundary_evidence():
    return {
        "status": "verified",
        "boundary": "NEW_BATTLE",
        "required": list(FARM_FREE_UPGRADE_LOCKS),
        "checked": True,
        "valid": True,
        "has_authoritative_mismatch": False,
        "locks": [
            {"label": label, "state": "checked", "valid": True}
            for label in FARM_FREE_UPGRADE_LOCKS
        ],
        "changed_labels": [],
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


class _IncompleteSessionPreflightStrategy(BaseStrategy):
    def requires_session_preflight(self) -> bool:
        return True

    def is_session_preflight_complete(self, ctx: MissionContext) -> bool:
        return False


class _BoundaryStrategy(BaseStrategy):
    def __init__(self, name, variable):
        super().__init__()
        self.name = name
        self.vars = {variable: False}
        self.variable = variable
        self.starts = 0

    def on_start(self, ctx: MissionContext) -> None:
        self.starts += 1
        ctx.data.setdefault("mission_vars", {})[self.variable] = True


class AdbPortTests(unittest.TestCase):
    def test_adb_port_defaults_to_5555(self):
        with patch.dict("os.environ", {}, clear=True):
            config = config_from_args(parse_args([]))
        self.assertEqual(config.adb_port, 5555)

    def test_adb_port_uses_managed_environment_default(self):
        with patch.dict("os.environ", {"THETOWER_ADB_PORT": "5565"}):
            config = config_from_args(parse_args([]))
        self.assertEqual(config.adb_port, 5565)

    def test_adb_port_accepts_override(self):
        with patch.dict("os.environ", {"THETOWER_ADB_PORT": "5565"}):
            config = config_from_args(parse_args(["--adb-port", "5575"]))
        self.assertEqual(config.adb_port, 5575)

    def test_adb_port_rejects_invalid_managed_environment_default(self):
        with patch.dict("os.environ", {"THETOWER_ADB_PORT": "invalid"}):
            with self.assertRaises(SystemExit):
                parse_args([])

    def test_adb_port_accepts_original_override(self):
        config = config_from_args(parse_args(["--adb-port", "5565"]))
        self.assertEqual(config.adb_port, 5565)

    def test_adb_port_rejects_out_of_range_value(self):
        with self.assertRaises(SystemExit):
            parse_args(["--adb-port", "65536"])


class DefaultStrategyTests(unittest.TestCase):
    def test_farm_is_the_default_strategy(self):
        config = config_from_args(parse_args([]))
        self.assertEqual(config.strategy_name, "farm")

    def test_gc_default_can_be_explicitly_disabled(self):
        config = config_from_args(parse_args(["--strategy", "none"]))
        self.assertEqual(config.strategy_name, "none")

        strategy = get_strategy(config.strategy_name)
        self.assertIsNone(strategy)

        manager = MissionManager(None, strategy)
        manager.start()
        manager.maybe_run_start({"state": "RUNNING"})
        self.assertFalse(manager.run_initialization_pending())
        self.assertFalse(manager.session_preflight_pending())

    def test_named_farm_and_legacy_profiles_are_selectable(self):
        for profile_name in (*FARM_PROFILE_NAMES, *PROFILE_NAMES):
            with self.subTest(profile=profile_name):
                config = config_from_args(parse_args(["--strategy", profile_name]))
                self.assertEqual(config.strategy_name, profile_name)

    def test_farm_and_gc_aliases_resolve_to_tier_18_profile(self):
        farm = get_strategy("farm")
        strategy = get_strategy("gc")
        self.assertIsInstance(farm, YamlStrategy)
        self.assertIsInstance(strategy, YamlStrategy)
        self.assertEqual(farm.name, "farm_t18")
        self.assertEqual(strategy.name, "farm_t18")

    def test_tactical_alias_resolves_to_profile_without_seeded_completion(self):
        strategy = get_strategy("gc_manual_target_priority")
        self.assertIsInstance(strategy, YamlStrategy)
        self.assertEqual(strategy.name, "farm_t19_experiment")
        self.assertNotIn("target_priority_checked", strategy.vars)


class RunBoundaryTests(unittest.TestCase):
    def test_strategy_replacement_clears_old_owned_state_and_starts_new_strategy(self):
        old_strategy = _BoundaryStrategy("farm_t18", "old_owned")
        new_strategy = _BoundaryStrategy("tournament", "new_owned")
        manager = MissionManager(None, old_strategy)
        manager.start()
        mission_vars = manager.ctx.data["mission_vars"]
        mission_vars["unrelated"] = "keep"
        manager.ctx.data["rule_last_fire"] = {"old-rule": 123.0}

        manager.replace_strategy_at_boundary(new_strategy)

        self.assertIs(manager.strategy, new_strategy)
        self.assertNotIn("old_owned", mission_vars)
        self.assertTrue(mission_vars["new_owned"])
        self.assertEqual(mission_vars["unrelated"], "keep")
        self.assertEqual(manager.ctx.data["rule_last_fire"], {})
        self.assertEqual(new_strategy.starts, 1)

    def test_active_battle_strategy_adoption_defers_gates_until_next_boundary(self):
        manager = MissionManager(None, None)
        manager.start()
        manager.maybe_run_start({"state": "RUNNING"})
        strategy = get_strategy("farm_t18")

        manager.adopt_strategy_for_active_battle(strategy)
        mv = manager.ctx.data["mission_vars"]

        self.assertIs(manager.strategy, strategy)
        self.assertTrue(manager.ctx.data["startup_gates_deferred"])
        self.assertFalse(manager.run_initialization_pending())
        self.assertFalse(manager.session_preflight_pending())
        self.assertEqual(
            mv["gc_session_preflight_evidence"]["free_upgrade_locks"]["status"],
            "unavailable_deferred",
        )

        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "RESUME_BATTLE"}
        )
        manager.maybe_run_start({"state": "RUNNING"})
        self.assertTrue(manager.ctx.data["startup_gates_deferred"])

        manager.maybe_run_start({"state": "GAME_OVER"})
        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
        )
        self.assertFalse(manager.ctx.data["startup_gates_deferred"])
        self.assertEqual(
            manager.no_battle_setup_requirements()["free_upgrade_locks"],
            list(FARM_FREE_UPGRADE_LOCKS),
        )

    def test_app_adopts_requested_strategy_on_fresh_running_evidence(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._mission_mgr.strategy = None
        app._supervisor = MagicMock()
        app._supervisor.strategy_request = (
            "farm_t18",
            "request-active",
            "active_battle",
        )
        app._config = SimpleNamespace(strategy_name="none")
        app._last_strategy_request = None
        app._pending_strategy_request = None
        app._strategy_boundary_confirmed = False
        app._run_initialization_gate_logged = True
        app._session_preflight_gate_logged = True
        app._session_preflight_terminal_blocked_logged = True
        app._session_preflight_repair_denial_logged = True
        app._startup_gate_waivers = {"bots_preset": {"status": "claimed"}}
        new_strategy = SimpleNamespace(name="farm_t18")

        with (
            patch("core.app.get_strategy", return_value=new_strategy),
            patch("core.app.log"),
        ):
            app._observe_strategy_request()
            app._process_strategy_boundary({"state": "RUNNING"})

        app._mission_mgr.adopt_strategy_for_active_battle.assert_called_once_with(
            new_strategy
        )
        app._mission_mgr.replace_strategy_at_boundary.assert_not_called()
        self.assertEqual(app._config.strategy_name, "farm_t18")
        self.assertIsNone(app._pending_strategy_request)
        self.assertEqual(app._startup_gate_waivers, {})

    def test_app_adopts_requested_strategy_at_resumable_home(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._pending_strategy_request = (
            "farm_t18",
            "request-active",
            "active_battle",
        )
        app._strategy_boundary_confirmed = False
        app._config = SimpleNamespace(strategy_name="none")
        app._startup_gate_waivers = {}
        new_strategy = SimpleNamespace(name="farm_t18")

        with (
            patch("core.app.get_strategy", return_value=new_strategy),
            patch("core.app.log"),
        ):
            app._process_strategy_boundary(
                {
                    "state": "HOME_SCREEN",
                    "home_battle_control": "RESUME_BATTLE",
                }
            )

        app._mission_mgr.adopt_strategy_for_active_battle.assert_called_once_with(
            new_strategy
        )
        app._mission_mgr.replace_strategy_at_boundary.assert_not_called()

    def test_active_adoption_request_at_new_battle_uses_boundary_replacement(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._pending_strategy_request = (
            "farm_t18",
            "request-active",
            "active_battle",
        )
        app._strategy_boundary_confirmed = False
        app._config = SimpleNamespace(strategy_name="none")
        app._startup_gate_waivers = {}
        new_strategy = SimpleNamespace(name="farm_t18")

        with (
            patch("core.app.get_strategy", return_value=new_strategy),
            patch("core.app.log"),
        ):
            app._process_strategy_boundary(
                {
                    "state": "HOME_SCREEN",
                    "home_battle_control": "NEW_BATTLE",
                }
            )

        app._mission_mgr.replace_strategy_at_boundary.assert_called_once_with(
            new_strategy
        )
        app._mission_mgr.adopt_strategy_for_active_battle.assert_not_called()

    def test_app_applies_pending_strategy_on_paused_workshop_boundary(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._mission_mgr.strategy = SimpleNamespace(name="farm_t18")
        app._supervisor = MagicMock()
        app._supervisor.strategy_request = ("tournament", "request-1")
        app._config = SimpleNamespace(strategy_name="farm_t18")
        app._last_strategy_request = None
        app._pending_strategy_request = None
        app._strategy_boundary_confirmed = False
        app._run_initialization_gate_logged = True
        app._session_preflight_gate_logged = True
        app._session_preflight_terminal_blocked_logged = True
        app._session_preflight_repair_denial_logged = True
        new_strategy = SimpleNamespace(name="tournament")

        with (
            patch("core.app.get_strategy", return_value=new_strategy),
            patch("core.app.log"),
        ):
            app._observe_strategy_request()
            app._process_strategy_boundary({"state": "WORKSHOP"})

        app._mission_mgr.replace_strategy_at_boundary.assert_called_once_with(
            new_strategy
        )
        self.assertEqual(app._config.strategy_name, "tournament")
        self.assertIsNone(app._pending_strategy_request)

    def test_app_does_not_apply_pending_strategy_at_resumable_home(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._mission_mgr.strategy = SimpleNamespace(name="farm_t18")
        app._supervisor = MagicMock()
        app._supervisor.strategy_request = ("tournament", "request-1")
        app._config = SimpleNamespace(strategy_name="farm_t18")
        app._last_strategy_request = None
        app._pending_strategy_request = None
        app._strategy_boundary_confirmed = False

        with patch("core.app.log"):
            app._observe_strategy_request()
            app._process_strategy_boundary(
                {
                    "state": "HOME_SCREEN",
                    "home_battle_control": "RESUME_BATTLE",
                }
            )

        app._mission_mgr.replace_strategy_at_boundary.assert_not_called()
        self.assertEqual(app._pending_strategy_request[0], "tournament")

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

    def test_tournament_results_establish_a_run_boundary(self):
        strategy = _RunCountingStrategy()
        manager = MissionManager(None, strategy)
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        manager.maybe_run_start({"state": "TOURNAMENT_RESULTS"})
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertEqual(strategy.run_starts, 2)


class DeferredStartupGateTests(unittest.TestCase):
    @staticmethod
    def _strategy():
        return YamlStrategy(
            {
                "vars": {
                    "startup_gate_done": False,
                    "session_gate_done": False,
                },
                "per_run_reset": ["startup_gate_done", "session_gate_done"],
                "run_initialization": {
                    "complete_when": ["startup_gate_done"],
                },
                "session_preflight": {
                    "complete_when": ["session_gate_done"],
                    "requirements": {"cards_deck": "Farm"},
                },
                "rules": [
                    {
                        "name": "startup_gate",
                        "gate_phase": "run_initialization",
                        "when": {"state": "RUNNING"},
                        "assert": ["!startup_gate_done"],
                        "do": [
                            {
                                "type": "set",
                                "var": "startup_gate_done",
                                "value": True,
                            }
                        ],
                    },
                    {
                        "name": "session_gate",
                        "gate_phase": "session_preflight",
                        "when": {"state": "RUNNING"},
                        "assert": ["!session_gate_done"],
                        "do": [
                            {
                                "type": "set",
                                "var": "session_gate_done",
                                "value": True,
                            }
                        ],
                    },
                    {
                        "name": "normal_automation",
                        "when": {"state": "RUNNING"},
                        "do": [{"type": "normal_action"}],
                    },
                ],
            }
        )

    def test_existing_battle_skips_only_gate_rules(self):
        strategy = self._strategy()
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
        )
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

        self.assertEqual(actions, [{"type": "normal_action"}])
        self.assertFalse(manager.ctx.data["mission_vars"]["startup_gate_done"])
        self.assertFalse(manager.ctx.data["mission_vars"]["session_gate_done"])
        self.assertFalse(manager.run_initialization_pending())
        self.assertFalse(manager.session_preflight_pending())
        self.assertEqual(manager.no_battle_setup_requirements(), {})

    def test_terminal_boundary_rearms_gates_for_next_battle(self):
        strategy = self._strategy()
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
        )
        manager.start()
        manager.maybe_run_start({"state": "RUNNING"})

        manager.maybe_run_start({"state": "GAME_OVER"})
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertTrue(manager.run_initialization_pending())
        strategy.tick(manager.ctx, object(), {"state": "RUNNING"})
        self.assertTrue(manager.ctx.data["mission_vars"]["startup_gate_done"])
        self.assertTrue(manager.ctx.data["mission_vars"]["session_gate_done"])
        self.assertFalse(manager.run_initialization_pending())
        self.assertFalse(manager.session_preflight_pending())

    def test_resume_home_preserves_attachment_but_new_battle_arms_gates(self):
        strategy = _RunCountingStrategy()
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
        )
        manager.start()

        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "RESUME_BATTLE"}
        )
        manager.maybe_run_start({"state": "RUNNING"})
        self.assertEqual(strategy.run_starts, 0)

        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
        )
        manager.maybe_run_start({"state": "RUNNING"})
        self.assertEqual(strategy.run_starts, 1)

    def test_attached_farm_defers_lock_evidence_until_new_battle_boundary(self):
        strategy = get_strategy("farm_t18")
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
        )
        manager.start()
        mv = manager.ctx.data["mission_vars"]
        deferred = mv["gc_session_preflight_evidence"]["free_upgrade_locks"]

        self.assertEqual(deferred["status"], "unavailable_deferred")
        self.assertIsNone(deferred["valid"])
        self.assertTrue(deferred["blocking_valid"])
        self.assertFalse(manager.session_preflight_repair_required())

        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "RESUME_BATTLE"}
        )
        manager.maybe_run_start({"state": "RUNNING"})
        self.assertEqual(
            mv["gc_session_preflight_evidence"]["free_upgrade_locks"],
            deferred,
        )
        self.assertEqual(manager.no_battle_setup_requirements(), {})

        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
        )
        self.assertNotIn(
            "free_upgrade_locks",
            mv["gc_session_preflight_evidence"],
        )
        self.assertEqual(
            manager.no_battle_setup_requirements()["free_upgrade_locks"],
            list(FARM_FREE_UPGRADE_LOCKS),
        )

        boundary_evidence = _free_upgrade_lock_boundary_evidence()
        manager.mark_no_battle_setup_complete(
            {"free_upgrade_locks": boundary_evidence}
        )
        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
        )
        self.assertEqual(manager.no_battle_setup_requirements(), {})
        self.assertEqual(
            mv["gc_session_preflight_evidence"]["free_upgrade_locks"],
            boundary_evidence,
        )

    def test_no_battle_setup_defers_target_priority_gate_to_running(self):
        strategy = get_strategy("farm_t18")
        manager = MissionManager(None, strategy)
        manager.start()
        mv = manager.ctx.data["mission_vars"]

        manager.mark_no_battle_setup_complete(
            {
                "target_priority": {
                    "mode": "enforce",
                    "checked": False,
                    "valid": None,
                    "boundary": "RUNNING",
                    "reason": "battle_only_control",
                }
            }
        )

        self.assertFalse(mv["target_priority_checked"])


class FarmProfileTests(unittest.TestCase):
    def _source(self, name="farm_t18"):
        return yaml.safe_load(
            FARM_SOURCE_PATHS[name].read_text(encoding="utf-8")
        )

    def test_generated_farm_profiles_match_compact_sources(self):
        for profile_name in FARM_PROFILE_NAMES:
            with self.subTest(profile=profile_name):
                generated = yaml.safe_load(
                    FARM_STRATEGY_PATHS[profile_name].read_text(encoding="utf-8")
                )
                self.assertEqual(generated, build_strategy_yaml(self._source(profile_name)))

    def test_farm_profile_resolves_invariants_and_tier_loadout(self):
        plan = build_strategy_yaml(self._source())
        requirements = plan["session_preflight"]["requirements"]
        configuration = plan["run_configuration"]

        self.assertEqual(plan["meta"]["family"], "farm")
        self.assertEqual(requirements["cards_deck"], "Farm")
        self.assertEqual(requirements["workshop_preset"], "Farm")
        self.assertEqual(requirements["bots_preset"], "Farm")
        self.assertTrue(requirements["auto_pick_perks"])
        self.assertEqual(requirements["perk_bans"], list(FARM_PERK_BANS))
        self.assertEqual(
            requirements["perk_auto_pick_order"],
            list(FARM_AUTO_PICK_ORDER),
        )
        self.assertEqual(
            requirements["ultimate_weapons"]["Poison Swamp"]["stun"],
            "off",
        )
        self.assertEqual(requirements["loadout_policies"]["modules"], "enforce")
        self.assertEqual(
            requirements["loadout_policies"]["target_priority"],
            "enforce",
        )
        self.assertEqual(
            requirements["target_priority"],
            configuration["loadout"]["target_priority"]["resolved"],
        )
        self.assertEqual(configuration["profile"], "farm")
        self.assertEqual(configuration["tier"], 18)
        self.assertEqual(configuration["schema_version"], 2)
        self.assertEqual(configuration["settings"]["cards_deck"], "Farm")
        self.assertEqual(configuration["settings"]["bots_preset"], "Farm")
        self.assertEqual(
            configuration["settings"]["perk_auto_pick_order"][2],
            "coin_tradeoff",
        )
        self.assertEqual(
            plan["session_preflight"]["fallbacks"]["bots_preset"][0],
            {
                "id": "flame",
                "label": "Continue with Flame for this run",
                "value": "Flame",
                "description": (
                    "Keep the currently selected Flame Bot preset and waive only "
                    "the Farm Bot preset check."
                ),
            },
        )
        self.assertEqual(
            configuration["gate_fallbacks"],
            plan["session_preflight"]["fallbacks"],
        )
        self.assertEqual(
            configuration["settings"]["guardian_chips"],
            ["Fetch", "Summon", "Scout"],
        )
        self.assertEqual(
            configuration["settings"]["ultimate_weapons"]["Poison Swamp"]["stun"],
            "off",
        )
        self.assertEqual(
            configuration["loadout"]["modules"]["preset"],
            "farm_standard",
        )
        self.assertEqual(
            configuration["loadout"]["target_priority"]["preset"],
            "farm_t18",
        )
        self.assertEqual(
            configuration["loadout"]["damage_slider"],
            {"mode": "enforce", "value": "1E-22%"},
        )
        self.assertEqual(
            configuration["loadout"]["orb_distance"],
            {
                "mode": "enforce",
                "preset": "farm_min_range",
                "resolved": {
                    "range_basis": "30.00m",
                    "extra": "30.00m",
                    "workshop": "39.00m",
                },
            },
        )
        self.assertIn(
            "damage_slider_checked",
            plan["run_initialization"]["complete_when"],
        )
        self.assertIn(
            "orb_distance_checked",
            plan["run_initialization"]["complete_when"],
        )
        damage_rule = next(
            rule for rule in plan["rules"]
            if rule["name"] == "enforce_damage_slider"
        )
        self.assertEqual(
            damage_rule["do"],
            [
                {
                    "type": "damage_slider_configure",
                    "mode": "enforce",
                    "value": "1E-22%",
                }
            ],
        )
        self.assertGreater(
            plan["rules"].index(damage_rule),
            next(
                index for index, rule in enumerate(plan["rules"])
                if rule["name"] == "initialize_level_skips_fast"
            ),
        )
        self.assertEqual(
            damage_rule["assert"],
            ["ehls_completed", "eals_completed", "!damage_slider_checked"],
        )
        orb_rule = next(
            rule for rule in plan["rules"]
            if rule["name"] == "enforce_orb_distance"
        )
        self.assertEqual(
            orb_rule["do"],
            [
                {
                    "type": "orb_distance_configure",
                    "mode": "enforce",
                    "range_basis": "30.00m",
                    "extra": "30.00m",
                    "workshop": "39.00m",
                }
            ],
        )
        self.assertGreater(
            plan["rules"].index(orb_rule),
            plan["rules"].index(damage_rule),
        )
        self.assertEqual(
            orb_rule["assert"],
            ["ehls_completed", "eals_completed", "!orb_distance_checked"],
        )

    def test_damage_slider_gate_and_evidence_reset_for_every_run(self):
        strategy = get_strategy("farm")
        ctx = MissionContext()
        strategy.on_start(ctx)
        mv = ctx.data["mission_vars"]
        mv["damage_slider_checked"] = True
        mv["damage_slider_observation"] = {"final": "1E-22%"}

        strategy.on_run_start(ctx)

        self.assertFalse(mv["damage_slider_checked"])
        self.assertEqual(mv["damage_slider_observation"], {})

    def test_orb_distance_gate_and_evidence_reset_for_every_run(self):
        strategy = get_strategy("farm")
        ctx = MissionContext()
        strategy.on_start(ctx)
        mv = ctx.data["mission_vars"]
        mv["orb_distance_checked"] = True
        mv["orb_distance_observation"] = {
            "final_extra": "30.00m",
            "final_workshop": "39.00m",
        }

        strategy.on_run_start(ctx)

        self.assertFalse(mv["orb_distance_checked"])
        self.assertEqual(mv["orb_distance_observation"], {})

    def test_runtime_exposes_an_isolated_resolved_configuration_snapshot(self):
        strategy = get_strategy("farm")
        self.assertIsInstance(strategy, YamlStrategy)

        configuration = strategy.run_configuration()
        configuration["loadout"]["modules"]["mode"] = "changed-in-test"

        self.assertEqual(strategy.run_configuration()["profile"], "farm")
        self.assertEqual(strategy.run_configuration()["tier"], 18)
        self.assertEqual(
            strategy.run_configuration()["loadout"]["modules"]["mode"],
            "enforce",
        )

    def test_farm_source_cannot_override_invariants(self):
        source = self._source()
        source["session_preflight"] = {"cards_deck": "Anything"}

        with self.assertRaisesRegex(ValueError, "derive session_preflight"):
            build_strategy_yaml(source)

    def test_farm_source_requires_every_variable_policy(self):
        source = self._source()
        del source["loadout"]["modules"]

        with self.assertRaisesRegex(ValueError, "must define exactly"):
            build_strategy_yaml(source)

    def test_damage_slider_rejects_invalid_negative_value(self):
        source = self._source()
        source["loadout"]["damage_slider"] = {
            "mode": "enforce",
            "value": "-1e22",
        }

        with self.assertRaisesRegex(ValueError, "invalid Damage Slider"):
            build_strategy_yaml(source)

    def test_preserved_damage_slider_rejects_a_value(self):
        source = self._source()
        source["loadout"]["damage_slider"] = {
            "mode": "preserve",
            "value": "1e-22",
        }

        with self.assertRaisesRegex(ValueError, "must not supply a value"):
            build_strategy_yaml(source)

    def test_orb_distance_rejects_an_unknown_preset(self):
        source = self._source()
        source["loadout"]["orb_distance"]["preset"] = "missing"

        with self.assertRaisesRegex(
            ValueError,
            "unknown orb_distance preset",
        ):
            build_strategy_yaml(source)

    def test_observed_damage_slider_is_nonblocking_and_emits_one_read(self):
        source = self._source()
        source["loadout"]["damage_slider"]["mode"] = "observe"

        plan = build_strategy_yaml(source)
        rule = next(
            rule for rule in plan["rules"]
            if rule["name"] == "observe_damage_slider"
        )

        self.assertNotIn(
            "damage_slider_checked",
            plan["run_initialization"]["complete_when"],
        )
        self.assertFalse(plan["vars"]["damage_slider_observed"])
        self.assertEqual(
            rule["do"],
            [
                {
                    "type": "damage_slider_configure",
                    "mode": "observe",
                    "value": "1E-22%",
                }
            ],
        )

    def test_damage_slider_enforcement_result_updates_run_gate(self):
        plan = build_strategy_yaml(self._source())
        action = next(
            rule for rule in plan["rules"]
            if rule["name"] == "enforce_damage_slider"
        )["do"][0]
        ctx = MissionContext()
        ctx.data["mission_vars"] = {"last_detection_state": "RUNNING"}
        payload = {
            "expected": "1E-22%",
            "final": "1E-22%",
            "success": True,
        }
        result = SimpleNamespace(
            success=True,
            expected="1E-22%",
            initial="1E-21%",
            final="1E-22%",
            steps=1,
            reason="matched",
            as_dict=lambda: payload,
        )

        with patch(
            "core.action_executor.configure_damage_slider",
            return_value=result,
        ):
            execute_actions(object(), [{**action, "_strategy": True}], ctx)

        mv = ctx.data["mission_vars"]
        self.assertTrue(mv["damage_slider_checked"])
        self.assertEqual(mv["damage_slider_observation"], payload)

    def test_damage_slider_result_log_uses_operator_percentage_notation(self):
        ctx = MissionContext()
        ctx.data["mission_vars"] = {"last_detection_state": "RUNNING"}
        result = SimpleNamespace(
            success=True,
            expected="1E2%",
            initial="1E-22%",
            final="100%",
            steps=24,
            reason="matched",
            as_dict=lambda: {"expected": "1E2%", "success": True},
        )

        with (
            patch(
                "core.action_executor.configure_damage_slider",
                return_value=result,
            ),
            patch("core.action_executor.log_mission") as mission_log,
        ):
            execute_actions(
                object(),
                [
                    {
                        "type": "damage_slider_configure",
                        "mode": "enforce",
                        "value": "1E2%",
                        "_strategy": True,
                    }
                ],
                ctx,
            )

        mission_log.assert_called_once_with(
            "[DAMAGE_SLIDER] mode=enforce expected=100% "
            "initial=1E-22% final=100% steps=24 success=True reason=matched",
            "INFO",
        )

    def test_preserved_modules_are_omitted_from_runtime_requirements(self):
        source = self._source()
        source["loadout"]["modules"] = {"mode": "preserve"}

        plan = build_strategy_yaml(source)
        requirements = plan["session_preflight"]["requirements"]

        self.assertNotIn("modules", requirements)
        self.assertEqual(
            requirements["loadout_policies"],
            {"modules": "preserve", "target_priority": "enforce"},
        )
        self.assertEqual(
            plan["run_configuration"]["loadout"]["modules"],
            {"mode": "preserve"},
        )

    def test_observed_modules_retain_expected_preset_without_blocking_policy(self):
        source = self._source()
        source["loadout"]["modules"]["mode"] = "observe"

        plan = build_strategy_yaml(source)
        requirements = plan["session_preflight"]["requirements"]

        self.assertEqual(
            requirements["loadout_policies"],
            {"modules": "observe", "target_priority": "enforce"},
        )
        self.assertEqual(
            requirements["modules"]["generator_primary"],
            "Black Hole Digestor",
        )

    def test_observed_target_priority_is_nonblocking_and_emits_one_read(self):
        source = self._source()
        source["loadout"]["target_priority"]["mode"] = "observe"

        plan = build_strategy_yaml(source)
        observe_rule = next(
            rule for rule in plan["rules"]
            if rule["name"] == "observe_target_priority"
        )

        self.assertNotIn(
            "target_priority_checked",
            plan["run_initialization"]["complete_when"],
        )
        self.assertFalse(plan["vars"]["target_priority_observed"])
        self.assertEqual(
            observe_rule["do"][0]["type"],
            "target_priority_observe",
        )

    def test_target_priority_observation_is_recorded_without_gating(self):
        source = self._source()
        source["loadout"]["target_priority"]["mode"] = "observe"
        plan = build_strategy_yaml(source)
        action = next(
            rule for rule in plan["rules"]
            if rule["name"] == "observe_target_priority"
        )["do"][0]
        ctx = MissionContext()
        ctx.data["mission_vars"] = {"last_detection_state": "RUNNING"}
        payload = {
            "observed": True,
            "matches": False,
            "actual": ["Basic"],
        }
        observation = SimpleNamespace(
            observed=True,
            matches=False,
            as_dict=lambda: payload,
        )

        with patch(
            "core.action_executor.observe_target_priority_order",
            return_value=observation,
        ):
            execute_actions(object(), [{**action, "_strategy": True}], ctx)

        mv = ctx.data["mission_vars"]
        self.assertTrue(mv["target_priority_observed"])
        self.assertEqual(mv["target_priority_observation"], payload)


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
            copy.deepcopy(rule)
            for rule in plans["gc_farm_t18"]["rules"]
            if rule["name"] != "ensure_target_priority"
        ]
        preflight_rule = next(
            rule
            for rule in shared_t18_rules
            if rule["name"] == "validate_gc_session_preflight"
        )
        preflight_rule["assert"].remove("target_priority_checked")
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

        self.assertEqual(
            actions,
            [
                {
                    "type": "gc_session_preflight",
                    "requirements": strategy.config["rules"][-1]["do"][0][
                        "requirements"
                    ],
                }
            ],
        )
        self.assertFalse(manager.run_initialization_pending())
        self.assertTrue(manager.session_preflight_pending())
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
        tier_18_action = next(
            rule for rule in tier_18_plan["rules"]
            if rule["name"] == "ensure_target_priority"
        )["do"][0]
        tier_19_action = next(
            rule for rule in tier_19_plan["rules"]
            if rule["name"] == "ensure_target_priority"
        )["do"][0]

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

    def test_session_requirements_are_carried_by_each_profile(self):
        tier_18_source = yaml.safe_load(
            SOURCE_PATHS["gc_farm_t18"].read_text(encoding="utf-8")
        )
        tier_19_source = copy.deepcopy(tier_18_source)
        tier_19_source["meta"].update(name="gc_farm_t19_experiment", tier=19)
        tier_19_source["session_preflight"]["ultimate_weapons"][
            "Golden Tower"
        ]["primary"] = "off"

        tier_18_plan = build_strategy_yaml(tier_18_source)
        tier_19_plan = build_strategy_yaml(tier_19_source)
        tier_18_requirements = tier_18_plan["rules"][-1]["do"][0][
            "requirements"
        ]
        tier_19_requirements = tier_19_plan["rules"][-1]["do"][0][
            "requirements"
        ]

        self.assertEqual(
            tier_18_requirements["ultimate_weapons"]["Golden Tower"]["primary"],
            "on",
        )
        self.assertEqual(
            tier_19_requirements["ultimate_weapons"]["Golden Tower"]["primary"],
            "off",
        )
        self.assertEqual(
            tier_18_requirements["ultimate_weapons"]["Poison Swamp"]["stun"],
            "off",
        )
        self.assertEqual(
            tier_19_requirements["ultimate_weapons"]["Poison Swamp"]["stun"],
            "off",
        )

    def test_session_requirements_reject_unsupported_stun_state(self):
        source = yaml.safe_load(
            SOURCE_PATHS["gc_farm_t18"].read_text(encoding="utf-8")
        )
        source["session_preflight"]["ultimate_weapons"]["Poison Swamp"][
            "stun"
        ] = "on"

        with self.assertRaisesRegex(
            ValueError,
            "supports only Poison Swamp stun=off",
        ):
            build_strategy_yaml(source)

    def test_enforce_profile_rejects_an_incomplete_order(self):
        source = yaml.safe_load(
            SOURCE_PATHS["gc_farm_t18"].read_text(encoding="utf-8")
        )
        source["initialization"]["target_priority"]["order"].pop()

        with self.assertRaisesRegex(ValueError, "every target exactly once"):
            build_strategy_yaml(source)


    def test_session_preflight_waits_for_run_initialization_and_survives_unknown(self):
        strategy = get_strategy("gc_farm_t19_experiment")
        manager = MissionManager(None, strategy)
        manager.start()
        manager.maybe_run_start({"state": "RUNNING"})
        mv = manager.ctx.data["mission_vars"]

        self.assertTrue(manager.run_initialization_pending())
        self.assertFalse(manager.session_preflight_pending())

        mv.update(ehls_completed=True, eals_completed=True)
        self.assertFalse(manager.run_initialization_pending())
        self.assertTrue(manager.session_preflight_pending())

        manager.maybe_run_start({"state": "UNKNOWN"})
        self.assertTrue(manager.session_preflight_pending())

        mv["gc_session_preflight_completed"] = True
        self.assertFalse(manager.session_preflight_pending())

        manager.maybe_run_start({"state": "GAME_OVER"})
        manager.maybe_run_start({"state": "RUNNING"})
        mv.update(ehls_completed=True, eals_completed=True)
        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertFalse(manager.session_preflight_pending())

    def test_terminal_preflight_block_excludes_owned_home_repair(self):
        strategy = get_strategy("gc_farm_t19_experiment")
        manager = MissionManager(None, strategy)
        manager.start()
        mv = manager.ctx.data["mission_vars"]

        mv.update(
            gc_session_preflight_blocked=True,
            gc_session_preflight_repair_required=False,
            gc_session_preflight_repair_in_progress=False,
        )
        self.assertTrue(manager.session_preflight_terminally_blocked())

        mv["gc_session_preflight_repair_required"] = True
        self.assertFalse(manager.session_preflight_terminally_blocked())

        mv["gc_session_preflight_repair_required"] = False
        mv["gc_session_preflight_repair_in_progress"] = True
        self.assertFalse(manager.session_preflight_terminally_blocked())

    def test_session_preflight_action_records_one_continuous_session_completion(self):
        strategy = get_strategy("farm_t19_experiment")
        ctx = MissionContext()
        strategy.on_start(ctx)
        mv = ctx.data["mission_vars"]
        mv["last_detection_state"] = "RUNNING"
        boundary_evidence = _free_upgrade_lock_boundary_evidence()
        mv["gc_no_battle_setup_evidence"] = {
            "free_upgrade_locks": boundary_evidence
        }
        mv["gc_no_battle_setup_completed"] = True
        action = next(
            rule
            for rule in strategy.rules
            if rule["name"] == "validate_gc_session_preflight"
        )["do"][0]
        evidence = SimpleNamespace(as_dict=lambda: {"valid": True})
        result = GcLivePreflightResult(
            GcPreflightNavigationStatus.COMPLETE,
            "all requirements verified",
            evidence,
        )

        with patch(
            "core.action_executor.run_read_only_gc_preflight",
            return_value=result,
        ) as run_preflight:
            execute_actions(
                object(),
                [{**action, "_strategy": True}],
                ctx,
            )

        run_preflight.assert_called_once_with(
            action["requirements"],
            no_battle_setup_evidence={
                "free_upgrade_locks": boundary_evidence
            },
            free_upgrade_lock_boundary_evidence=boundary_evidence,
        )
        self.assertTrue(mv["gc_session_preflight_attempted"])
        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertEqual(mv["gc_session_preflight_evidence"], {"valid": True})

    def test_completed_farm_run_reports_new_battle_lock_evidence(self):
        strategy = get_strategy("farm_t18")
        boundary_evidence = _free_upgrade_lock_boundary_evidence()
        manager = MagicMock()
        manager.strategy = strategy
        manager.ctx = MissionContext(
            data={
                "mission_vars": {
                    "gc_session_preflight_evidence": {
                        "valid": True,
                        "free_upgrade_locks": boundary_evidence,
                    }
                }
            }
        )
        manager.session_preflight_repair_in_progress.return_value = False
        app = App.__new__(App)
        app._mission_mgr = manager
        app._fast_game_over = False
        app._last_wave_value = 2500
        app._last_wave_conf = 99.0
        app._supervisor = MagicMock()
        app._status_reporter = MagicMock()
        app._status_reporter.coin_rate_samples = []
        app._pending_strategy_request = None
        app._strategy_boundary_confirmed = False
        app._handle_daily_gem_if_due = MagicMock(return_value=False)
        app._handle_mission_rewards_if_due = MagicMock(return_value=False)
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

        with patch("core.app.handle_game_over") as game_over:
            app._handle_primary_states("GAME_OVER", set(), frame)

        reported = game_over.call_args.kwargs["battle_context"][
            "session_preflight_evidence"
        ]
        self.assertEqual(reported["free_upgrade_locks"], boundary_evidence)
        manager.on_game_over.assert_called_once_with()

    def test_mid_run_farm_adoption_supplies_battle_end_identity(self):
        manager = MissionManager(None, None)
        manager.start()
        manager.maybe_run_start({"state": "RUNNING"})
        manager.adopt_strategy_for_active_battle(get_strategy("farm_t18"))
        app = App.__new__(App)
        app._mission_mgr = manager
        app._fast_game_over = False
        app._last_wave_value = 1800
        app._last_wave_conf = 99.0
        app._supervisor = MagicMock()
        app._status_reporter = MagicMock()
        app._status_reporter.coin_rate_samples = []
        app._pending_strategy_request = None
        app._strategy_boundary_confirmed = False
        app._handle_daily_gem_if_due = MagicMock(return_value=False)
        app._handle_mission_rewards_if_due = MagicMock(return_value=False)
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

        with patch("core.app.handle_game_over") as game_over:
            app._handle_primary_states("GAME_OVER", set(), frame)

        battle_context = game_over.call_args.kwargs["battle_context"]
        self.assertEqual(battle_context["strategy"], "farm_t18")
        self.assertEqual(battle_context["run_configuration"]["profile"], "farm")

    def test_session_preflight_mismatch_blocks_without_correction(self):
        strategy = get_strategy("gc_farm_t19_experiment")
        ctx = MissionContext()
        strategy.on_start(ctx)
        mv = ctx.data["mission_vars"]
        mv["last_detection_state"] = "RUNNING"
        action = next(
            rule
            for rule in strategy.rules
            if rule["name"] == "validate_gc_session_preflight"
        )["do"][0]
        evidence = SimpleNamespace(as_dict=lambda: {"valid": False})
        result = GcLivePreflightResult(
            GcPreflightNavigationStatus.MISMATCH,
            "configuration mismatch",
            evidence,
        )

        with patch(
            "core.action_executor.run_read_only_gc_preflight",
            return_value=result,
        ):
            execute_actions(
                object(),
                [{**action, "_strategy": True}],
                ctx,
            )

        self.assertTrue(mv["gc_session_preflight_attempted"])
        self.assertFalse(mv["gc_session_preflight_completed"])
        self.assertTrue(mv["gc_session_preflight_blocked"])
        self.assertFalse(mv["gc_session_preflight_repair_required"])

    def test_session_preflight_no_battle_mismatch_requests_guarded_repair(self):
        strategy = get_strategy("gc_farm_t19_experiment")
        ctx = MissionContext()
        strategy.on_start(ctx)
        mv = ctx.data["mission_vars"]
        mv["last_detection_state"] = "RUNNING"
        mv["gc_no_battle_setup_completed"] = True
        action = next(
            rule
            for rule in strategy.rules
            if rule["name"] == "validate_gc_session_preflight"
        )["do"][0]
        evidence = SimpleNamespace(
            as_dict=lambda: {"valid": False, "modules": {"valid": False}},
            requires_no_battle_repair=True,
        )
        result = GcLivePreflightResult(
            GcPreflightNavigationStatus.MISMATCH,
            "configuration mismatch",
            evidence,
        )

        with patch(
            "core.action_executor.run_read_only_gc_preflight",
            return_value=result,
        ):
            execute_actions(
                object(),
                [{**action, "_strategy": True}],
                ctx,
            )

        self.assertTrue(mv["gc_session_preflight_attempted"])
        self.assertFalse(mv["gc_session_preflight_completed"])
        self.assertTrue(mv["gc_session_preflight_blocked"])
        self.assertTrue(mv["gc_session_preflight_repair_required"])
        self.assertFalse(mv["gc_session_preflight_repair_in_progress"])
        self.assertFalse(mv["gc_no_battle_setup_completed"])

    def test_completed_home_repair_requires_fresh_preflight_on_next_run(self):
        strategy = get_strategy("gc_farm_t19_experiment")
        manager = MissionManager(None, strategy)
        manager.start()
        mv = manager.ctx.data["mission_vars"]
        mv.update(
            gc_session_preflight_attempted=True,
            gc_session_preflight_completed=False,
            gc_session_preflight_blocked=True,
            gc_session_preflight_repair_required=True,
            gc_session_preflight_repair_in_progress=True,
        )

        manager.mark_no_battle_setup_complete({"modules": {"valid": True}})

        self.assertTrue(mv["gc_no_battle_setup_completed"])
        self.assertFalse(mv["gc_session_preflight_attempted"])
        self.assertFalse(mv["gc_session_preflight_completed"])
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertFalse(mv["gc_session_preflight_repair_required"])
        self.assertFalse(mv["gc_session_preflight_repair_in_progress"])

    def test_app_surrenders_once_for_claimed_gc_home_repair(self):
        manager = MagicMock()
        manager.begin_session_preflight_repair.return_value = True
        app = App.__new__(App)
        app._auto_start_enabled = True
        app._session_preflight_repair_denial_logged = False
        app._mission_mgr = manager

        with patch("core.app.surrender_run", return_value=True) as surrender:
            app._attempt_session_preflight_repair({"state": "RUNNING"})

        manager.begin_session_preflight_repair.assert_called_once_with()
        surrender.assert_called_once_with()
        manager.fail_session_preflight_repair.assert_not_called()

    def test_app_fails_closed_when_guarded_surrender_does_not_complete(self):
        manager = MagicMock()
        manager.begin_session_preflight_repair.return_value = True
        app = App.__new__(App)
        app._auto_start_enabled = True
        app._session_preflight_repair_denial_logged = False
        app._mission_mgr = manager

        with patch("core.app.surrender_run", return_value=False):
            app._attempt_session_preflight_repair({"state": "RUNNING"})

        manager.fail_session_preflight_repair.assert_called_once_with(
            "guarded Surrender did not reach Game Over"
        )

    def test_natural_game_over_during_preflight_remains_pending_for_next_run(self):
        strategy = get_strategy("gc_farm_t19_experiment")
        ctx = MissionContext()
        strategy.on_start(ctx)
        mv = ctx.data["mission_vars"]
        mv["last_detection_state"] = "RUNNING"
        action = next(
            rule
            for rule in strategy.rules
            if rule["name"] == "validate_gc_session_preflight"
        )["do"][0]
        result = GcLivePreflightResult(
            GcPreflightNavigationStatus.BATTLE_ENDED,
            "natural Game Over observed during GC preflight",
        )

        with patch(
            "core.action_executor.run_read_only_gc_preflight",
            return_value=result,
        ):
            execute_actions(
                object(),
                [{**action, "_strategy": True}],
                ctx,
            )

        self.assertFalse(mv["gc_session_preflight_attempted"])
        self.assertFalse(mv["gc_session_preflight_completed"])
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertEqual(
            mv["gc_session_preflight_last_status"],
            GcPreflightNavigationStatus.BATTLE_ENDED.value,
        )


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
        app._event_mission_tracker = MagicMock()
        app._event_mission_tracker.due_warnings.return_value = ()
        app._match_trace = False
        app._last_wave_value = None
        app._last_wave_conf = -1.0
        app._last_wave_ts = 0.0
        app._blind_tapper_suspended = False
        app._run_initialization_gate_logged = False
        app._session_preflight_gate_logged = False
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
            wave=1,
            wave_conf=99.0,
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

    def test_paused_session_preflight_observes_without_actions(self):
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        strategy = _IncompleteSessionPreflightStrategy()
        manager = MagicMock()
        manager.ctx = MissionContext(data={"mission_vars": {}})
        manager.strategy = strategy
        manager.run_initialization_pending.return_value = False
        manager.session_preflight_pending.return_value = True
        manager.session_preflight_terminally_blocked.return_value = False

        supervisor = MagicMock()
        supervisor.is_paused = True
        supervisor.auto_return_secs = 900

        app = App.__new__(App)
        app._config = SimpleNamespace(wait_on_start=False)
        app._supervisor = supervisor
        app._mission_mgr = manager
        app._state_tracker = MagicMock()
        app._status_reporter = MagicMock()
        app._event_mission_tracker = MagicMock()
        app._event_mission_tracker.due_warnings.return_value = ()
        app._match_trace = False
        app._last_wave_value = None
        app._last_wave_conf = -1.0
        app._last_wave_ts = 0.0
        app._blind_tapper_suspended = False
        app._run_initialization_gate_logged = False
        app._session_preflight_gate_logged = False
        app._capture_frame = MagicMock(side_effect=[frame, KeyboardInterrupt])
        app._resolve_upgrade_detail_overlay = MagicMock()
        app._handle_primary_states = MagicMock()

        with (
            patch("core.app.ensure_adb_connected", return_value=False),
            patch("core.app.threading.Thread"),
            patch(
                "core.app.detect_state_and_overlays",
                return_value={"state": "RUNNING", "overlays": []},
            ),
            patch("core.app.detect_wave_number_from_image", return_value=(1, 99.0)),
            patch("core.app.stop_blind_gem_tapper", return_value=False),
            patch("core.app.start_blind_gem_tapper") as start_tapper,
            patch("core.app.handle_unknown_state") as recover,
            patch("core.app.time.sleep"),
        ):
            app.run()

        manager.maybe_run_start.assert_called_once()
        manager.tick.assert_not_called()
        manager.handle_overlays.assert_not_called()
        manager.on_state.assert_not_called()
        app._resolve_upgrade_detail_overlay.assert_not_called()
        app._handle_primary_states.assert_not_called()
        supervisor.auto_return_check.assert_not_called()
        start_tapper.assert_not_called()
        recover.assert_not_called()
        app._status_reporter.maybe_report.assert_called_once_with(
            img=frame,
            ui_state="RUNNING",
            menu=None,
            secondary=set(),
            overlays=set(),
            wave=1,
            wave_conf=99.0,
            allow_actions=False,
        )

    def test_terminally_blocked_preflight_allows_only_safe_ad_gem_handler(self):
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        strategy = _IncompleteSessionPreflightStrategy()
        manager = MagicMock()
        manager.ctx = MissionContext(data={"mission_vars": {}})
        manager.strategy = strategy
        manager.run_initialization_pending.return_value = False
        manager.session_preflight_pending.return_value = True
        manager.session_preflight_terminally_blocked.return_value = True

        supervisor = MagicMock()
        supervisor.is_paused = False
        supervisor.auto_return_secs = 900

        app = App.__new__(App)
        app._config = SimpleNamespace(wait_on_start=False)
        app._supervisor = supervisor
        app._mission_mgr = manager
        app._state_tracker = MagicMock()
        app._status_reporter = MagicMock()
        app._event_mission_tracker = MagicMock()
        app._event_mission_tracker.due_warnings.return_value = ()
        app._match_trace = False
        app._last_wave_value = None
        app._last_wave_conf = -1.0
        app._last_wave_ts = 0.0
        app._blind_tapper_suspended = False
        app._run_initialization_gate_logged = False
        app._session_preflight_gate_logged = True
        app._session_preflight_terminal_blocked_logged = False
        app._session_preflight_repair_denial_logged = False
        app._capture_frame = MagicMock(side_effect=[frame, KeyboardInterrupt])
        app._resolve_upgrade_detail_overlay = MagicMock()
        app._handle_primary_states = MagicMock()

        with (
            patch("core.app.ensure_adb_connected", return_value=False),
            patch("core.app.threading.Thread"),
            patch(
                "core.app.detect_state_and_overlays",
                return_value={
                    "state": "RUNNING",
                    "overlays": ["AD_GEMS_AVAILABLE"],
                },
            ),
            patch("core.app.detect_wave_number_from_image", return_value=(1, 99.0)),
            patch("core.app.stop_blind_gem_tapper", return_value=False),
            patch("core.app.start_blind_gem_tapper") as start_tapper,
            patch("core.app.handle_ad_gem") as handle_ad_gem,
            patch("core.app.time.sleep"),
        ):
            app.run()

        handle_ad_gem.assert_called_once_with()
        manager.tick.assert_not_called()
        manager.handle_overlays.assert_not_called()
        manager.on_state.assert_not_called()
        app._resolve_upgrade_detail_overlay.assert_not_called()
        app._handle_primary_states.assert_not_called()
        supervisor.auto_return_check.assert_not_called()
        start_tapper.assert_not_called()
        app._status_reporter.maybe_report.assert_called_once_with(
            img=frame,
            ui_state="RUNNING",
            menu=None,
            secondary=set(),
            overlays={"AD_GEMS_AVAILABLE"},
            wave=1,
            wave_conf=99.0,
            allow_actions=False,
        )

    def test_read_only_status_reporting_does_not_refresh_coin_display(self):
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        supervisor = AutomationSupervisor(
            control_file="logs/unused-test-control.json",
            auto_return_enabled=False,
        )
        supervisor._last_coins_val = Decimal("1")
        reporter = StatusReporter(
            interval_secs=1,
            supervisor=supervisor,
            save_wave_samples=None,
            save_coin_samples=None,
        )

        original_state = AUTOMATION.state
        try:
            AUTOMATION.state = RunState.RUNNING
            with (
                patch(
                    "core.status_report.detect_coins_from_image",
                    return_value=(Decimal("100"), 99.0, False),
                ),
                patch("core.automation_supervisor.tap_if_visible") as tap,
                patch("core.status_report.log_status") as status_log,
            ):
                reporter.maybe_report(
                    img=frame,
                    ui_state="RUNNING",
                    menu=None,
                    secondary=set(),
                    overlays=set(),
                    wave=1,
                    wave_conf=99.0,
                    now_ts=1.0,
                    allow_actions=False,
                )
        finally:
            AUTOMATION.state = original_state

        tap.assert_not_called()
        status_log.assert_called_once_with(
            "State=RUNNING | Wave=1 | Coins/min=1",
            detail=(
                "[STATUS_DETAIL] State=RUNNING | Wave=1 | Coins/min=1 | "
                "Menu=— | Secondary=[—] | Overlays=[—]"
            ),
        )

    def test_status_reporting_collects_structured_coin_rate_samples(self):
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        supervisor = AutomationSupervisor(
            control_file="logs/unused-test-control.json",
            auto_return_enabled=False,
        )
        reporter = StatusReporter(
            interval_secs=1,
            supervisor=supervisor,
            save_wave_samples=None,
            save_coin_samples=None,
        )

        original_state = AUTOMATION.state
        try:
            AUTOMATION.state = RunState.RUNNING
            with (
                patch(
                    "core.status_report.detect_coins_from_image",
                    return_value=(Decimal("1230000"), 98.5, True),
                ),
                patch("core.status_report.log_status"),
            ):
                reporter.maybe_report(
                    img=frame,
                    ui_state="RUNNING",
                    menu=None,
                    secondary=set(),
                    overlays=set(),
                    wave=321,
                    wave_conf=99.0,
                    now_ts=1_700_000_000.0,
                    allow_actions=False,
                )
        finally:
            AUTOMATION.state = original_state

        assert len(reporter.coin_rate_samples) == 1
        sample = reporter.coin_rate_samples[0]
        assert datetime.fromisoformat(str(sample["captured_at"])).timestamp() == 1_700_000_000.0
        assert sample["wave"] == 321
        assert sample["coins_per_minute_decimal"] == "1230000"
        assert sample["display"] == "1.23M"
        assert sample["confidence"] == 98.5
        reporter.reset_coin_rate_samples()
        assert reporter.coin_rate_samples == []


if __name__ == "__main__":
    unittest.main()
