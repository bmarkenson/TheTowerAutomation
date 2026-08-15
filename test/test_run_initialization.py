import copy
from datetime import datetime, timedelta, timezone
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
from core.battle_lifecycle import HomeBattleControl
from core.free_upgrade_locks import FARM_FREE_UPGRADE_LOCKS
from core.gc_preflight_navigation import (
    GcLivePreflightResult,
    GcPreflightNavigationStatus,
)
from core.home_battle import HomeBattleEvidence
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
)
from core.perk_configuration import FARM_AUTO_PICK_ORDER, FARM_PERK_BANS
from core.run_state import AUTOMATION, RunState
from core.status_report import StatusReporter
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE_NAMES = ("gc_farm_t18", "gc_farm_t19_experiment")
FARM_PROFILE_NAMES = ("farm_t18", "farm_t19")
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


def _bind_terminal_context(app: App, scope_id: str = "test-run") -> None:
    app._current_run_scope_id = lambda: scope_id
    app._observed_active_battle_scope_id = scope_id


def _repair_authority() -> dict[str, object]:
    return {
        "schema_version": 1,
        "runtime_id": "runtime-1",
        "pid": 1234,
        "adb_target": "localhost:5555",
        "target_generation": 7,
        "activity_scope_run_id": "scope-1",
        "game_state": "active_battle",
    }


def _repair_terminal_acquisition() -> PlayerSaveAcquisitionBundle:
    started = datetime.now(timezone.utc)
    captured = started + timedelta(milliseconds=1)
    return PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.NATURAL_BOUNDARY,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=PlayerSaveTargetBinding("localhost:5555", 7),
        acquisition_started_at=started,
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=SimpleNamespace(),
        boundary=PlayerSaveNaturalBoundary(
            kind=PlayerSaveBoundaryKind.GAME_OVER,
            observed_at=started,
            runtime_session_id="save-runtime-1",
            activity_scope_id="scope-1",
        ),
    )


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

    def test_direct_runtime_owns_adb_connection_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            config = config_from_args(parse_args([]))
        self.assertEqual(config.adb_connection_owner, "runtime")

    def test_connection_owner_accepts_explicit_managed_service_value(self):
        config = config_from_args(
            parse_args(["--adb-connection-owner", "control-surface"])
        )
        self.assertEqual(config.adb_connection_owner, "control-surface")

    def test_connection_owner_rejects_unknown_value(self):
        with self.assertRaises(SystemExit):
            parse_args(["--adb-connection-owner", "other"])

    def test_connection_owner_rejects_unknown_managed_environment_value(self):
        with patch.dict(
            "os.environ",
            {"THETOWER_ADB_CONNECTION_OWNER": "other"},
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                parse_args([])


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
        self.assertEqual(strategy.name, "farm_t19")
        self.assertFalse(strategy.vars["target_priority_checked"])

    def test_retired_t19_experiment_name_resolves_to_t19_farm(self):
        strategy = get_strategy("farm_t19_experiment")
        self.assertIsInstance(strategy, YamlStrategy)
        self.assertEqual(strategy.name, "farm_t19")


class StartupLoggingTests(unittest.TestCase):
    def test_home_control_logging_ignores_repeated_semantic_evidence(self):
        app = App.__new__(App)
        detection = {"state": "HOME_SCREEN"}
        observations = (
            HomeBattleEvidence(HomeBattleControl.NEW_BATTLE, "ocr", 96.0),
            HomeBattleEvidence(HomeBattleControl.NEW_BATTLE, "template", 99.0),
            HomeBattleEvidence(HomeBattleControl.RESUME_BATTLE, "ocr", 95.0),
        )

        with (
            patch(
                "core.app.detect_home_battle_control",
                side_effect=observations,
            ),
            patch("core.app.log") as runtime_log,
        ):
            for _ in observations:
                app._annotate_home_battle_control(object(), detection)

        self.assertEqual(detection["home_battle_control"], "RESUME_BATTLE")
        self.assertEqual(runtime_log.call_count, 2)
        self.assertIn(
            "Home control=NEW_BATTLE",
            runtime_log.call_args_list[0].args[0],
        )
        self.assertIn(
            "Home control=RESUME_BATTLE",
            runtime_log.call_args_list[1].args[0],
        )

    def test_home_control_logging_reports_home_reentry(self):
        app = App.__new__(App)
        home = {"state": "HOME_SCREEN"}
        evidence = HomeBattleEvidence(
            HomeBattleControl.NEW_BATTLE,
            "ocr",
            96.0,
        )

        with (
            patch(
                "core.app.detect_home_battle_control",
                return_value=evidence,
            ) as detect,
            patch("core.app.log") as runtime_log,
        ):
            app._annotate_home_battle_control(object(), home)
            app._annotate_home_battle_control(
                object(),
                {"state": "RUNNING"},
            )
            app._annotate_home_battle_control(object(), home)

        self.assertEqual(detect.call_count, 2)
        self.assertEqual(runtime_log.call_count, 2)

    def test_steady_run_entry_log_names_the_completed_transition(self):
        app = App.__new__(App)

        with patch("core.app.log_result") as runtime_log:
            app._log_steady_run_entry()

        runtime_log.assert_called_once_with(
            "[RUN] All configured checks complete; entering steady run state",
            detail="[RUN] result=steady_state",
            console=True,
        )

    def test_steady_run_entry_waits_for_the_first_actionable_frame(self):
        app = App.__new__(App)
        app._steady_run_entry_pending = True

        with patch("core.app.log_result") as runtime_log:
            self.assertFalse(
                app._maybe_log_steady_run_entry(actions_blocked=True)
            )
            self.assertTrue(app._steady_run_entry_pending)
            runtime_log.assert_not_called()

            self.assertTrue(
                app._maybe_log_steady_run_entry(actions_blocked=False)
            )

        self.assertFalse(app._steady_run_entry_pending)
        runtime_log.assert_called_once_with(
            "[RUN] All configured checks complete; entering steady run state",
            detail="[RUN] result=steady_state",
            console=True,
        )


class RunBoundaryTests(unittest.TestCase):
    def test_running_degradation_merges_sources_checks_and_attachment_details(self):
        manager = MissionManager(None, get_strategy("farm_t19"))
        manager.start()

        manager.mark_running_configuration_degraded(
            source="attachment_applicability",
            reason="selected Tier does not match",
            failed_checks=("battle_tier",),
            details={"selected_strategy": "farm_t19", "observed_tier": 18},
        )
        manager.mark_running_configuration_degraded(
            source="attachment_observation",
            reason="damage slider could not be verified",
            failed_checks=("damage_slider",),
        )

        self.assertEqual(
            manager.running_configuration_degradation(),
            {
                "schema_version": 1,
                "sources": [
                    "attachment_applicability",
                    "attachment_observation",
                ],
                "reason": (
                    "selected Tier does not match; "
                    "damage slider could not be verified"
                ),
                "failed_checks": ["battle_tier", "damage_slider"],
                "details": {
                    "selected_strategy": "farm_t19",
                    "observed_tier": 18,
                },
            },
        )

    def test_resolved_reporting_degradation_preserves_configuration_problem(self):
        manager = MissionManager(None, get_strategy("farm_t19"))
        manager.start()
        manager.mark_running_configuration_degraded(
            source="attachment_reporting",
            reason="workflow report is pending",
            failed_checks=("workflow_reporting",),
            details={"reporting_status": "retrying"},
        )
        manager.mark_running_configuration_degraded(
            source="attachment_applicability",
            reason="selected Tier does not match",
            failed_checks=("battle_tier",),
            details={"observed_tier": 18},
        )

        self.assertTrue(
            manager.resolve_running_configuration_degradation(
                source="attachment_reporting",
                failed_checks=("workflow_reporting",),
                detail_keys=("reporting_status",),
            )
        )

        self.assertEqual(
            manager.running_configuration_degradation(),
            {
                "schema_version": 1,
                "sources": ["attachment_applicability"],
                "reason": "selected Tier does not match",
                "failed_checks": ["battle_tier"],
                "details": {"observed_tier": 18},
            },
        )

    def test_active_battle_strategy_switch_preserves_running_degradation(self):
        manager = MissionManager(None, get_strategy("farm_t19"))
        manager.start()
        manager.mark_running_configuration_degraded(
            source="attachment_configuration",
            reason="attached controls do not match",
            failed_checks=("damage_slider",),
        )

        manager.adopt_strategy_for_active_battle(get_strategy("farm_t18"))

        degradation = manager.running_configuration_degradation()
        self.assertIsNotNone(degradation)
        self.assertEqual(
            degradation["sources"],
            ["attachment_configuration"],
        )
        self.assertEqual(degradation["failed_checks"], ["damage_slider"])

    def test_degraded_battle_rearms_profile_setup_for_terminal_home_repair(self):
        manager = MissionManager(None, get_strategy("farm_t19"))
        manager.start()
        mv = manager.ctx.data["mission_vars"]
        mv["gc_no_battle_setup_completed"] = True
        mv["gc_session_preflight_attempted"] = True
        mv["gc_session_preflight_completed"] = True
        mv["gc_session_preflight_degraded"] = True
        mv["gc_session_preflight_last_reason"] = "modules do not match"
        mv["gc_session_preflight_failed_checks"] = ["modules"]
        manager.mark_running_configuration_degraded(
            source="return_control",
            reason="Return Control found: workshop_preset",
            failed_checks=("workshop_preset",),
        )

        degradation = manager.running_configuration_degradation()

        self.assertEqual(
            degradation,
            {
                "schema_version": 1,
                "sources": ["return_control", "session_preflight"],
                "reason": (
                    "Return Control found: workshop_preset; "
                    "modules do not match"
                ),
                "failed_checks": ["modules", "workshop_preset"],
            },
        )
        self.assertTrue(manager.prepare_degraded_home_repair(degradation))
        self.assertFalse(mv["gc_no_battle_setup_completed"])
        self.assertTrue(manager.no_battle_setup_requirements())
        self.assertFalse(mv["gc_session_preflight_completed"])
        self.assertIn("gc_degraded_home_repair", mv)

        manager.mark_no_battle_setup_complete({"modules": {"valid": True}})

        self.assertIsNone(manager.running_configuration_degradation())
        self.assertNotIn("gc_degraded_home_repair", mv)
        self.assertTrue(mv["gc_no_battle_setup_completed"])
        self.assertFalse(mv["gc_session_preflight_completed"])

    def test_exhausted_terminal_home_repair_keeps_next_battle_degraded(self):
        manager = MissionManager(None, get_strategy("farm_t19"))
        manager.start()
        degradation = {
            "schema_version": 1,
            "sources": ["session_preflight"],
            "reason": "modules do not match",
            "failed_checks": ["modules"],
        }

        self.assertTrue(manager.prepare_degraded_home_repair(degradation))
        manager.mark_no_battle_setup_degraded(
            {},
            failed_check="modules",
            reason="bounded Home repair exhausted",
        )

        self.assertEqual(
            manager.running_configuration_degradation(),
            {
                "schema_version": 1,
                "sources": ["home_setup"],
                "reason": "bounded Home repair exhausted",
                "failed_checks": ["modules"],
            },
        )
        self.assertNotIn("gc_degraded_home_repair", manager.ctx.data["mission_vars"])

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
        self.assertTrue(manager.session_preflight_pending())
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

    def test_app_reloads_changed_definition_for_same_strategy_id_at_boundary(self):
        app = App.__new__(App)
        current_strategy = SimpleNamespace(
            name="farm_t18",
            config={"meta": {"name": "farm_t18", "version": 1}},
        )
        updated_strategy = SimpleNamespace(
            name="farm_t18",
            config={"meta": {"name": "farm_t18", "version": 2}},
        )
        app._mission_mgr = MagicMock()
        app._mission_mgr.strategy = current_strategy
        app._supervisor = MagicMock()
        app._supervisor.strategy_request = (
            "farm_t18",
            "request-new",
            "next_boundary",
        )
        app._config = SimpleNamespace(strategy_name="farm_t18")
        app._last_strategy_request = (
            "farm_t18",
            "request-old",
            "next_boundary",
        )
        app._pending_strategy_request = None
        app._strategy_boundary_confirmed = False
        app._startup_gate_waivers = {}

        with (
            patch(
                "core.app.get_strategy",
                side_effect=[updated_strategy, updated_strategy],
            ) as load_strategy,
            patch("core.app.log"),
        ):
            app._observe_strategy_request()
            self.assertEqual(
                app._pending_strategy_request,
                ("farm_t18", "request-new", "next_boundary"),
            )
            app._process_strategy_boundary(
                {
                    "state": "HOME_SCREEN",
                    "home_battle_control": "NEW_BATTLE",
                }
            )

        self.assertEqual(load_strategy.call_count, 2)
        app._mission_mgr.replace_strategy_at_boundary.assert_called_once_with(
            updated_strategy
        )
        self.assertEqual(app._config.strategy_name, "farm_t18")
        self.assertIsNone(app._pending_strategy_request)

    def test_app_same_strategy_id_and_definition_remains_a_no_op(self):
        app = App.__new__(App)
        current_strategy = SimpleNamespace(
            name="farm_t18",
            config={"meta": {"name": "farm_t18", "version": 2}},
        )
        matching_strategy = SimpleNamespace(
            name="farm_t18",
            config={"meta": {"name": "farm_t18", "version": 2}},
        )
        app._mission_mgr = MagicMock()
        app._mission_mgr.strategy = current_strategy
        app._supervisor = MagicMock()
        app._supervisor.strategy_request = (
            "farm_t18",
            "request-new",
            "next_boundary",
        )
        app._config = SimpleNamespace(strategy_name="farm_t18")
        app._last_strategy_request = (
            "farm_t18",
            "request-old",
            "next_boundary",
        )
        app._pending_strategy_request = (
            "tournament",
            "request-pending",
            "next_boundary",
        )

        with (
            patch("core.app.get_strategy", return_value=matching_strategy),
            patch("core.app.log") as mock_log,
        ):
            app._observe_strategy_request()

        self.assertIsNone(app._pending_strategy_request)
        app._mission_mgr.replace_strategy_at_boundary.assert_not_called()
        app._mission_mgr.adopt_strategy_for_active_battle.assert_not_called()
        mock_log.assert_called_once_with(
            "[CTRL] Strategy set to farm_t18 via control file",
            "INFO",
            console=True,
        )

    def test_app_does_not_ack_same_strategy_when_definition_cannot_load(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._mission_mgr.strategy = SimpleNamespace(
            name="farm_t18",
            config={"meta": {"name": "farm_t18", "version": 1}},
        )
        app._supervisor = MagicMock()
        app._supervisor.strategy_request = (
            "farm_t18",
            "request-new",
            "next_boundary",
        )
        app._last_strategy_request = (
            "farm_t18",
            "request-old",
            "next_boundary",
        )
        app._pending_strategy_request = None

        with (
            patch("core.app.get_strategy", side_effect=ValueError("invalid plan")),
            patch("core.app.log") as mock_log,
        ):
            app._observe_strategy_request()

        self.assertEqual(
            app._pending_strategy_request,
            ("farm_t18", "request-new", "next_boundary"),
        )
        mock_log.assert_called_once_with(
            "[CTRL] Strategy farm_t18 queued for the next run boundary",
            "INFO",
            console=True,
        )

    def test_app_adopts_requested_strategy_at_resumable_home(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._supervisor = MagicMock()
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

    def test_active_strategy_request_cannot_replace_an_inflight_attach_snapshot(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._supervisor = MagicMock()
        app._supervisor.battle_workflow = {
            "intent": "attach_battle",
            "status": "ready",
        }
        app._pending_strategy_request = (
            "farm_t18",
            "request-after-attach",
            "active_battle",
        )
        app._strategy_boundary_confirmed = False

        app._process_strategy_boundary({"state": "RUNNING"})

        app._mission_mgr.adopt_strategy_for_active_battle.assert_not_called()
        self.assertEqual(
            app._pending_strategy_request,
            ("farm_t18", "request-after-attach", "active_battle"),
        )

    def test_active_strategy_request_cannot_convert_degraded_attach_observer(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._mission_mgr.strategy = None
        app._mission_mgr.running_configuration_degradation.return_value = {
            "sources": ["attachment_applicability"],
            "failed_checks": ["battle_tier"],
        }
        app._supervisor = MagicMock()
        app._supervisor.battle_workflow = {
            "intent": "attach_battle",
            "status": "completed",
        }
        app._supervisor.defer_strategy_request_to_next_boundary.return_value = (
            True
        )
        app._pending_strategy_request = (
            "farm_t18",
            "request-after-attach",
            "active_battle",
        )
        app._strategy_boundary_confirmed = False

        with patch("core.app.log"):
            app._process_strategy_boundary({"state": "RUNNING"})

        app._mission_mgr.adopt_strategy_for_active_battle.assert_not_called()
        app._supervisor.defer_strategy_request_to_next_boundary.assert_called_once_with(
            "farm_t18",
            "request-after-attach",
            source="runtime-attachment-strategy-deferral",
        )
        self.assertEqual(
            app._pending_strategy_request,
            ("farm_t18", "request-after-attach", "next_boundary"),
        )

    def test_active_adoption_request_at_new_battle_uses_boundary_replacement(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._supervisor = MagicMock()
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

        with patch(
            "automation.missions.manager.start_activity_scope"
        ) as start_activity_scope:
            manager.maybe_run_start({"state": "RUNNING"})
            manager.maybe_run_start(
                {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
            )
            manager.maybe_run_start(
                {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
            )
            manager.maybe_run_start({"state": "RUNNING"})

        self.assertEqual(strategy.run_starts, 2)
        start_activity_scope.assert_called_once_with(
            reason="new_battle_preflight",
            carry_terminal_history_handoff=True,
        )

    def test_strategy_replacement_preserves_observed_home_run_boundary(self):
        manager = MissionManager(None, _RunCountingStrategy())
        manager.start()

        with patch(
            "automation.missions.manager.start_activity_scope"
        ) as start_activity_scope:
            manager.maybe_run_start({"state": "RUNNING"})
            manager.maybe_run_start(
                {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
            )
            manager.replace_strategy_at_boundary(_RunCountingStrategy())
            manager.maybe_run_start(
                {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
            )

        start_activity_scope.assert_called_once_with(
            reason="new_battle_preflight",
            carry_terminal_history_handoff=True,
        )

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
    _BATTLE_IDENTITY = "b" * 64

    @staticmethod
    def _report_evidence():
        return {
            "valid": True,
            "failed_checks": [],
            "deferred_checks": ["free_upgrade_locks"],
            "configuration": {"cards": {"label": "Farm"}},
        }

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

    def test_attached_strategy_without_preflight_has_no_synthetic_hold(self):
        strategy = YamlStrategy(
            {
                "meta": {"name": "passive_strategy", "family": "farm"},
                "vars": {},
                "rules": [],
            }
        )
        manager = MissionManager(
            None,
            None,
            await_initial_battle_intent=True,
        )
        manager.start()

        self.assertTrue(
            manager.authorize_initial_battle_intent(
                "attach_battle",
                request_id="attach-passive",
                strategy=strategy,
            )
        )
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertTrue(manager.active_battle_observed())
        self.assertFalse(manager.attached_validation_requested())
        self.assertFalse(manager.session_preflight_pending())

    def test_attached_gate_action_carries_read_only_provenance_and_stays_done(self):
        strategy = self._strategy()
        session_rule = next(
            rule for rule in strategy.rules if rule["name"] == "session_gate"
        )
        session_rule["run_when_attached"] = True
        session_rule["do"] = [{"type": "ultimate_ensure_state"}]
        ctx = MissionContext(
            data={
                "startup_gates_deferred": True,
                "mission_vars": {},
            }
        )
        strategy.on_start(ctx)

        actions = strategy.tick(ctx, object(), {"state": "RUNNING"})

        self.assertEqual(
            actions,
            [
                {
                    "type": "ultimate_ensure_state",
                    "_attachment_validation": True,
                    "_attachment_rule_id": "session_gate",
                }
            ],
        )
        ctx.data["mission_vars"]["attached_validation_rule_dispositions"] = {
            "session_gate": {"disposition": "suppressed_degraded"}
        }
        self.assertEqual(
            strategy.tick(ctx, object(), {"state": "RUNNING"}),
            [{"type": "normal_action"}],
        )

    @classmethod
    def _identity_receipt(cls, strategy, *, identity=None):
        fingerprint = strategy.session_preflight_fingerprint()
        return {
            "schema_version": 1,
            "identity_fingerprint": identity or cls._BATTLE_IDENTITY,
            "strategy": strategy.name,
            "configuration_fingerprint": fingerprint,
            "completed_at": "2026-08-06T12:34:56-07:00",
            "evidence": cls._report_evidence(),
        }

    def _bind_identity(self, manager, *, identity=None):
        manager.observe_active_round_identity(
            identity or self._BATTLE_IDENTITY,
            changed_from_retained=False,
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

    def test_attached_validation_runs_read_only_session_check(self):
        strategy = self._strategy()
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
            validate_attached_battle=True,
        )
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

        self.assertTrue(manager.attached_validation_requested())
        self.assertTrue(manager.session_preflight_pending())
        self.assertEqual(
            actions,
            [
                {
                    "type": "gc_session_preflight",
                    "requirements": {"cards_deck": "Farm"},
                    "allow_repair": False,
                    "mismatch_policy": "block",
                    "_attachment_validation": True,
                    "_attachment_rule_id": (
                        "validate_requested_attached_session_preflight"
                    ),
                }
            ],
        )

    def test_completed_session_check_persists_identity_bound_receipt(self):
        strategy = self._strategy()
        manager = MissionManager(None, strategy)
        record = MagicMock(return_value=True)
        manager.configure_battle_identity_persistence(record)
        manager.start()
        self._bind_identity(manager)
        manager.maybe_run_start({"state": "RUNNING"})
        mission_vars = manager.ctx.data["mission_vars"]
        mission_vars["session_gate_done"] = True
        evidence = self._report_evidence()
        mission_vars["gc_session_preflight_evidence"] = evidence
        persisted = manager.persist_session_preflight_completion()

        self.assertTrue(persisted)
        record.assert_called_once_with(
            identity_fingerprint=self._BATTLE_IDENTITY,
            strategy=strategy.name,
            configuration_fingerprint=(
                strategy.session_preflight_fingerprint()
            ),
            evidence=evidence,
        )

    def test_degraded_attached_check_is_never_persisted_for_restart_reuse(self):
        strategy = self._strategy()
        manager = MissionManager(None, strategy)
        record = MagicMock(return_value=True)
        manager.configure_battle_identity_persistence(record)
        manager.start()
        self._bind_identity(manager)
        manager.maybe_run_start({"state": "RUNNING"})
        mission_vars = manager.ctx.data["mission_vars"]
        mission_vars["session_gate_done"] = True
        mission_vars["gc_session_preflight_completed"] = True
        mission_vars["gc_session_preflight_degraded"] = True
        mission_vars["gc_session_preflight_evidence"] = self._report_evidence()

        persisted = manager.persist_session_preflight_completion()

        self.assertFalse(persisted)
        record.assert_not_called()

    def test_unsafe_completed_report_is_not_persisted_or_warned_each_tick(self):
        strategy = self._strategy()
        manager = MissionManager(None, strategy)
        record = MagicMock(return_value=True)
        manager.configure_battle_identity_persistence(record)
        manager.start()
        self._bind_identity(manager)
        manager.maybe_run_start({"state": "RUNNING"})
        mission_vars = manager.ctx.data["mission_vars"]
        mission_vars["session_gate_done"] = True
        mission_vars["gc_session_preflight_evidence"] = {
            "valid": True,
            "failed_checks": [],
            "score": float("nan"),
        }

        with patch("automation.missions.manager.log") as log:
            self.assertFalse(manager.persist_session_preflight_completion())
            self.assertFalse(manager.persist_session_preflight_completion())

        record.assert_not_called()
        log.assert_called_once()

    def test_confirmed_same_battle_reuses_matching_session_receipt(self):
        strategy = self._strategy()
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
            validate_attached_battle=True,
        )
        manager.start()
        self._bind_identity(manager)
        manager.maybe_run_start({"state": "RUNNING"})
        placeholder = {
            "free_upgrade_locks": {"status": "unavailable_deferred"}
        }
        manager.ctx.data["mission_vars"][
            "gc_session_preflight_evidence"
        ] = placeholder
        receipt = self._identity_receipt(strategy)

        reused = manager.reuse_session_preflight_for_confirmed_attachment(
            self._BATTLE_IDENTITY,
            receipt,
        )

        self.assertTrue(reused)
        self.assertTrue(
            manager.ctx.data["attached_session_preflight_reused"]
        )
        self.assertFalse(manager.attached_validation_requested())
        self.assertFalse(manager.session_preflight_pending())
        self.assertEqual(
            manager.ctx.data["restored_session_preflight_report_evidence"],
            self._report_evidence(),
        )
        self.assertEqual(
            manager.ctx.data["mission_vars"][
                "gc_session_preflight_evidence"
            ],
            placeholder,
        )
        self.assertFalse(
            manager.ctx.data["mission_vars"].get(
                "gc_session_preflight_completed",
                False,
            )
        )
        self.assertFalse(manager.ctx.data["mission_vars"]["session_gate_done"])
        self.assertEqual(
            strategy.tick(manager.ctx, object(), {"state": "RUNNING"}),
            [{"type": "normal_action"}],
        )

    def test_two_replacements_restore_the_same_detailed_report(self):
        strategy = self._strategy()
        receipt = self._identity_receipt(strategy)

        for _ in range(2):
            replacement_strategy = self._strategy()
            manager = MissionManager(
                None,
                replacement_strategy,
                defer_startup_gates_until_next_run=True,
                validate_attached_battle=True,
            )
            manager.start()
            self._bind_identity(manager)
            manager.maybe_run_start({"state": "RUNNING"})
            reused = (
                manager.reuse_session_preflight_for_confirmed_attachment(
                    self._BATTLE_IDENTITY,
                    receipt,
                )
            )

            self.assertTrue(reused)
            self.assertEqual(
                manager.ctx.data[
                    "restored_session_preflight_report_evidence"
                ],
                self._report_evidence(),
            )

    def test_legacy_scope_receipt_cannot_suppress_attached_checks(self):
        strategy = self._strategy()
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
            validate_attached_battle=True,
        )
        manager.start()
        self._bind_identity(manager)
        manager.maybe_run_start({"state": "RUNNING"})
        manager.ctx.data["mission_vars"][
            "gc_session_preflight_evidence"
        ] = {"free_upgrade_locks": {"status": "unavailable_deferred"}}
        legacy_receipt = {
            "schema_version": 2,
            "status": "completed",
            "activity_scope_run_id": "current-run",
            "strategy": strategy.name,
            "configuration_fingerprint": (
                strategy.session_preflight_fingerprint()
            ),
            "completed_at": "2026-08-05T12:34:56-07:00",
        }

        reused = manager.reuse_session_preflight_for_confirmed_attachment(
            self._BATTLE_IDENTITY,
            legacy_receipt,
        )

        self.assertFalse(reused)
        self.assertTrue(manager.attached_validation_requested())
        self.assertTrue(manager.session_preflight_pending())

    def test_malformed_identity_receipt_does_not_suppress_attached_checks(self):
        strategy = self._strategy()
        base_receipt = self._identity_receipt(strategy)
        malformed_receipts = []
        for mutation in (
            lambda value: value.update(identity_fingerprint="c" * 64),
            lambda value: value.update(strategy="other-strategy"),
            lambda value: value.update(
                configuration_fingerprint="d" * 64
            ),
            lambda value: value["evidence"].update(valid=False),
            lambda value: value["evidence"].update(
                failed_checks=["cards_deck"]
            ),
            lambda value: value.update(completed_at="not-a-timestamp"),
            lambda value: value.update(schema_version=2),
        ):
            receipt = copy.deepcopy(base_receipt)
            mutation(receipt)
            malformed_receipts.append(receipt)

        for receipt in malformed_receipts:
            with self.subTest(receipt=receipt):
                manager = MissionManager(
                    None,
                    self._strategy(),
                    defer_startup_gates_until_next_run=True,
                    validate_attached_battle=True,
                )
                manager.start()
                self._bind_identity(manager)
                manager.maybe_run_start({"state": "RUNNING"})
                reused = (
                    manager.reuse_session_preflight_for_confirmed_attachment(
                        self._BATTLE_IDENTITY,
                        receipt,
                    )
                )

                self.assertFalse(reused)
                self.assertTrue(manager.attached_validation_requested())
                self.assertTrue(manager.session_preflight_pending())

    def test_restored_report_clears_at_the_next_battle_start(self):
        strategy = self._strategy()
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
            validate_attached_battle=True,
        )
        manager.start()
        self._bind_identity(manager)
        manager.maybe_run_start({"state": "RUNNING"})
        self.assertTrue(
            manager.reuse_session_preflight_for_confirmed_attachment(
                self._BATTLE_IDENTITY,
                self._identity_receipt(strategy),
            )
        )

        manager.maybe_run_start({"state": "GAME_OVER"})
        self.assertIn(
            "restored_session_preflight_report_evidence",
            manager.ctx.data,
        )
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertNotIn(
            "restored_session_preflight_report_evidence",
            manager.ctx.data,
        )

    def test_same_battle_without_matching_receipt_still_runs_checks(self):
        strategy = self._strategy()
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
            validate_attached_battle=True,
        )
        manager.start()
        self._bind_identity(manager)
        manager.maybe_run_start({"state": "RUNNING"})
        receipt = {
            **self._identity_receipt(strategy),
            "configuration_fingerprint": "older-configuration",
        }

        reused = manager.reuse_session_preflight_for_confirmed_attachment(
            self._BATTLE_IDENTITY,
            receipt,
        )

        self.assertFalse(reused)
        self.assertFalse(
            manager.ctx.data["attached_session_preflight_reused"]
        )
        self.assertTrue(manager.attached_validation_requested())
        self.assertTrue(manager.session_preflight_pending())

    def test_same_battle_continuity_releases_legacy_orphaned_validation_hold(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._exclusive_validation_ownership_hold = True
        app._active_round_identity_fingerprint = self._BATTLE_IDENTITY
        receipt = self._identity_receipt(self._strategy())
        app._battle_identity_store = MagicMock()
        app._battle_identity_store.active.return_value = SimpleNamespace(
            fingerprint=self._BATTLE_IDENTITY,
            session_preflight=receipt,
        )

        app._apply_activity_continuity_outcome(
            SimpleNamespace(
                confirmed_same_battle_scope_id="current-run"
            )
        )

        reuse = (
            app._mission_mgr.reuse_session_preflight_for_confirmed_attachment
        )
        reuse.assert_called_once_with(self._BATTLE_IDENTITY, receipt)
        self.assertFalse(app._exclusive_validation_ownership_hold)

    def test_later_battle_continuity_clears_orphaned_validation_hold(self):
        app = App.__new__(App)
        app._mission_mgr = MagicMock()
        app._exclusive_validation_ownership_hold = True

        with patch("core.app.log"):
            app._apply_activity_continuity_outcome(
                SimpleNamespace(
                    confirmed_later_battle_scope_id="later-run"
                )
            )

        self.assertFalse(app._exclusive_validation_ownership_hold)
        reuse = (
            app._mission_mgr.reuse_session_preflight_for_confirmed_attachment
        )
        reuse.assert_not_called()

    def test_explicit_skip_suppresses_even_profile_attached_checks(self):
        strategy = self._strategy()
        session_rule = next(
            rule for rule in strategy.rules if rule["name"] == "session_gate"
        )
        session_rule["run_when_attached"] = True
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
            skip_attached_checks=True,
        )
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

        self.assertTrue(manager.ctx.data["skip_attached_checks"])
        self.assertEqual(actions, [{"type": "normal_action"}])
        self.assertFalse(manager.session_preflight_pending())

        manager.maybe_run_start({"state": "GAME_OVER"})
        manager.maybe_run_start({"state": "RUNNING"})

        self.assertFalse(manager.ctx.data["skip_attached_checks"])
        self.assertTrue(manager.run_initialization_pending())

    def test_attached_tournament_validation_stages_observer_then_battle_controls(self):
        strategy = get_strategy("tournament")
        manager = MissionManager(
            None,
            strategy,
            defer_startup_gates_until_next_run=True,
            validate_attached_battle=True,
        )
        manager.start()

        manager.maybe_run_start({"state": "RUNNING"})
        actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "session_preflight")
        self.assertFalse(actions[0]["allow_repair"])
        self.assertEqual(actions[0]["mismatch_policy"], "notify")

        mission_vars = manager.ctx.data["mission_vars"]
        mission_vars["gc_session_preflight_attempted"] = True
        mission_vars["gc_session_preflight_completed"] = True
        actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "damage_slider_configure")
        self.assertFalse(manager.session_preflight_pending())
        self.assertFalse(strategy.is_session_preflight_complete(manager.ctx))

    def test_new_battle_home_ignores_attached_battle_choice(self):
        manager = MissionManager(
            None,
            self._strategy(),
            defer_startup_gates_until_next_run=True,
            validate_attached_battle=True,
            skip_attached_checks=True,
        )
        manager.start()

        manager.maybe_run_start(
            {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
        )

        self.assertFalse(manager.ctx.data["startup_gates_deferred"])
        self.assertFalse(manager.attached_validation_requested())
        self.assertFalse(manager.ctx.data["skip_attached_checks"])
        self.assertTrue(manager.no_battle_setup_requirements())

    def test_legacy_attached_repair_request_migrates_to_degraded(self):
        manager = MissionManager(
            None,
            self._strategy(),
            defer_startup_gates_until_next_run=True,
            validate_attached_battle=True,
        )
        manager.start()
        manager.maybe_run_start({"state": "RUNNING"})
        mv = manager.ctx.data["mission_vars"]
        mv["gc_session_preflight_blocked"] = True
        mv["gc_session_preflight_restart_available"] = True
        mv["gc_session_preflight_repair_required"] = True

        self.assertFalse(manager.session_preflight_terminally_blocked())
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertTrue(mv["gc_session_preflight_degraded"])
        self.assertEqual(
            mv["gc_session_preflight_disposition"],
            "continue_degraded",
        )
        self.assertFalse(mv["gc_session_preflight_repair_required"])
        self.assertFalse(mv["gc_session_preflight_restart_available"])
        self.assertFalse(
            hasattr(manager, "authorize_session_preflight_restart")
        )

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
        self.assertFalse(mv["gc_session_preflight_repair_required"])

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

    def test_shared_auto_pick_order_applies_to_current_and_future_farm_profiles(self):
        sources = [self._source(name) for name in FARM_PROFILE_NAMES]
        future_source = self._source()
        future_source["meta"].update(name="farm_t20", tier=20)
        sources.append(future_source)

        for source in sources:
            with self.subTest(profile=source["meta"]["name"]):
                plan = build_strategy_yaml(source)
                self.assertEqual(
                    plan["session_preflight"]["requirements"][
                        "perk_auto_pick_order"
                    ],
                    list(FARM_AUTO_PICK_ORDER),
                )
                self.assertEqual(
                    plan["run_configuration"]["settings"][
                        "perk_auto_pick_order"
                    ],
                    list(FARM_AUTO_PICK_ORDER),
                )

    def test_farm_profile_resolves_invariants_and_tier_loadout(self):
        plan = build_strategy_yaml(self._source())
        requirements = plan["session_preflight"]["requirements"]
        configuration = plan["run_configuration"]

        self.assertEqual(plan["meta"]["family"], "farm")
        self.assertEqual(requirements["cards_deck"], "Farm")
        self.assertEqual(
            requirements["card_recharge_modes"],
            {
                "Demon Mode": "auto_reactivate",
                "Nuke": "ready_after_recharge",
            },
        )
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
        self.assertEqual(
            configuration["settings"]["card_recharge_modes"],
            {
                "Demon Mode": "auto_reactivate",
                "Nuke": "ready_after_recharge",
            },
        )
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
            plan["session_preflight"]["recovery"],
            {"repair_mismatch_attempts": 3},
        )
        preflight_action = next(
            rule
            for rule in plan["rules"]
            if rule["name"] == "validate_gc_session_preflight"
        )["do"][0]
        self.assertEqual(preflight_action["repair_mismatch_attempts"], 3)
        self.assertEqual(
            configuration["session_preflight_recovery"],
            {"repair_mismatch_attempts": 3},
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

    def test_tier_19_enforces_orb_distance_for_configured_ranges(self):
        plan = build_strategy_yaml(self._source("farm_t19"))
        configuration = plan["run_configuration"]
        orb_rule = next(
            rule for rule in plan["rules"]
            if rule["name"] == "enforce_orb_distance"
        )

        self.assertEqual(
            configuration["loadout"]["orb_distance"]["mode"],
            "enforce",
        )
        self.assertEqual(
            [
                preset["range_basis"]
                for preset in configuration["loadout"]["orb_distance"][
                    "range_presets"
                ]
            ],
            ["30.00m", "98.38m"],
        )
        self.assertIn(
            "orb_distance_checked",
            plan["run_initialization"]["complete_when"],
        )
        self.assertEqual(
            orb_rule["assert"],
            ["ehls_completed", "eals_completed", "!orb_distance_checked"],
        )
        self.assertEqual(
            orb_rule["do"][0]["type"],
            "orb_distance_configure",
        )
        self.assertEqual(
            orb_rule["do"][0]["mode"],
            "enforce",
        )

    def test_tier_19_enforces_damage_slider_control_value(self):
        plan = build_strategy_yaml(self._source("farm_t19"))
        configuration = plan["run_configuration"]
        damage_rule = next(
            rule for rule in plan["rules"]
            if rule["name"] == "enforce_damage_slider"
        )

        self.assertEqual(
            configuration["loadout"]["damage_slider"],
            {"mode": "enforce", "value": "1E-19%"},
        )
        self.assertIn(
            "damage_slider_checked",
            plan["run_initialization"]["complete_when"],
        )
        self.assertEqual(
            damage_rule["assert"],
            ["ehls_completed", "eals_completed", "!damage_slider_checked"],
        )
        self.assertEqual(
            damage_rule["do"],
            [
                {
                    "type": "damage_slider_configure",
                    "mode": "enforce",
                    "value": "1E-19%",
                }
            ],
        )

    def test_tier_19_enforces_hypothesis_target_priority_order(self):
        plan = build_strategy_yaml(self._source("farm_t19"))
        configuration = plan["run_configuration"]
        expected_order = [
            "Fast",
            "Protector",
            "Fleets",
            "Boss",
            "Elites",
            "In Spotlight",
            "Tank",
            "Closest (Default)",
            "Ranged",
            "Basic",
        ]
        target_rule = next(
            rule for rule in plan["rules"]
            if rule["name"] == "ensure_target_priority"
        )

        self.assertEqual(
            configuration["loadout"]["target_priority"],
            {
                "mode": "enforce",
                "preset": "farm_t19",
                "resolved": expected_order,
            },
        )
        self.assertEqual(
            plan["session_preflight"]["requirements"]["target_priority"],
            expected_order,
        )
        self.assertIn(
            "target_priority_checked",
            plan["run_initialization"]["complete_when"],
        )
        self.assertEqual(
            target_rule["do"],
            [{"type": "target_priority_ensure", "order": expected_order}],
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
            changed=True,
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
            changed=True,
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

    def test_t19_runs_damage_slider_then_orb_distance_then_target_priority(self):
        strategy = get_strategy("gc_farm_t19_experiment")
        self.assertIsInstance(strategy, YamlStrategy)
        manager = MissionManager(None, strategy)
        manager.start()
        manager.maybe_run_start({"state": "RUNNING"})

        with patch("automation.strategies.yaml_strategy.log_mission"):
            actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})
        self.assertEqual(actions, [{"type": "level_skip_initialize"}])

        mv = manager.ctx.data["mission_vars"]
        self.assertFalse(mv["target_priority_checked"])
        mv.update(ehls_completed=True, eals_completed=True)
        with patch("automation.strategies.yaml_strategy.log_mission"):
            actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

        damage_action = next(
            rule["do"][0]
            for rule in strategy.rules
            if rule["name"] == "enforce_damage_slider"
        )
        self.assertEqual(actions, [damage_action])
        self.assertTrue(manager.run_initialization_pending())
        self.assertFalse(manager.session_preflight_pending())

        mv["damage_slider_checked"] = True
        with patch("automation.strategies.yaml_strategy.log_mission"):
            actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})
        orb_action = next(
            rule["do"][0]
            for rule in strategy.rules
            if rule["name"] == "enforce_orb_distance"
        )
        self.assertEqual(actions, [orb_action])
        self.assertTrue(manager.run_initialization_pending())
        self.assertFalse(manager.session_preflight_pending())

        mv["orb_distance_checked"] = True
        with patch("automation.strategies.yaml_strategy.log_mission"):
            actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})
        target_action = next(
            rule["do"][0]
            for rule in strategy.rules
            if rule["name"] == "ensure_target_priority"
        )
        self.assertEqual(actions, [target_action])
        self.assertTrue(manager.run_initialization_pending())
        self.assertFalse(manager.session_preflight_pending())

        mv["target_priority_checked"] = True
        with patch("automation.strategies.yaml_strategy.log_mission"):
            actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})
        self.assertEqual(
            actions,
            [strategy.config["rules"][-1]["do"][0]],
        )
        self.assertFalse(manager.run_initialization_pending())
        self.assertTrue(manager.session_preflight_pending())

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
        self.assertTrue(manager.run_initialization_pending())
        mv["damage_slider_checked"] = True
        self.assertTrue(manager.run_initialization_pending())
        mv["orb_distance_checked"] = True
        self.assertTrue(manager.run_initialization_pending())
        mv["target_priority_checked"] = True
        self.assertFalse(manager.run_initialization_pending())
        self.assertTrue(manager.session_preflight_pending())

        manager.maybe_run_start({"state": "UNKNOWN"})
        self.assertTrue(manager.session_preflight_pending())

        mv["gc_session_preflight_completed"] = True
        self.assertFalse(manager.session_preflight_pending())

        manager.maybe_run_start({"state": "GAME_OVER"})
        manager.maybe_run_start({"state": "RUNNING"})
        mv.update(
            ehls_completed=True,
            eals_completed=True,
            damage_slider_checked=True,
            orb_distance_checked=True,
            target_priority_checked=True,
        )
        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertFalse(manager.session_preflight_pending())

    def test_legacy_terminal_preflight_block_migrates_to_degraded_completion(self):
        strategy = get_strategy("gc_farm_t19_experiment")
        manager = MissionManager(None, strategy)
        manager.start()
        mv = manager.ctx.data["mission_vars"]

        mv.update(
            gc_session_preflight_blocked=True,
            gc_session_preflight_repair_required=False,
            gc_session_preflight_repair_in_progress=False,
        )
        self.assertFalse(manager.session_preflight_terminally_blocked())
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertTrue(mv["gc_session_preflight_attempted"])
        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertTrue(mv["gc_session_preflight_degraded"])
        self.assertEqual(
            mv["gc_session_preflight_disposition"],
            "continue_degraded",
        )
        self.assertFalse(mv["gc_session_preflight_repair_required"])
        self.assertFalse(mv["gc_session_preflight_repair_in_progress"])

    def test_session_preflight_action_records_one_continuous_session_completion(self):
        strategy = get_strategy("farm_t19")
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
        restored_report = {
            "valid": True,
            "failed_checks": [],
            "free_upgrade_locks": boundary_evidence,
            "configuration": {"cards": {"label": "Farm"}},
        }
        manager = MagicMock()
        manager.strategy = strategy
        manager.ctx = MissionContext(
            data={
                "restored_session_preflight_report_evidence": restored_report,
                "mission_vars": {
                    "gc_session_preflight_evidence": {
                        "free_upgrade_locks": {
                            "status": "unavailable_deferred"
                        },
                    }
                },
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
        app._accept_pending_terminal_history_handoff = MagicMock()
        _bind_terminal_context(app)
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

        with (
            patch("core.app.handle_game_over") as game_over,
            patch("core.app.start_retry_activity_scope") as retry_scope,
        ):
            retry_scope.return_value = {"run_id": "retry-scope"}
            app._handle_primary_states("GAME_OVER", set(), frame)

            game_over.call_args.kwargs["after_retry_started"]()

        battle_context = game_over.call_args.kwargs["battle_context"]
        reported = battle_context["session_preflight_evidence"]
        self.assertEqual(reported, restored_report)
        self.assertEqual(reported["free_upgrade_locks"], boundary_evidence)
        self.assertNotIn("observed_run_configuration", battle_context)
        retry_scope.assert_called_once_with()
        app._accept_pending_terminal_history_handoff.assert_called_once_with()
        manager.on_game_over.assert_called_once_with()

    def test_home_repair_records_surrender_before_returning_home(self):
        strategy = get_strategy("farm_t18")
        manager = MagicMock()
        manager.strategy = strategy
        manager.ctx = MissionContext(
            data={
                "mission_vars": {
                    "gc_session_preflight_evidence": {
                        "valid": False,
                        "failed_checks": ["cards_deck"],
                    }
                }
            }
        )
        manager.session_preflight_repair_in_progress.return_value = True
        manager.session_preflight_repair_grant.return_value = {
            **_repair_authority(),
            "request_id": "repair-1",
            "check_id": "cards_deck",
            "reason": "Cards deck does not match",
        }
        app = App.__new__(App)
        app._mission_mgr = manager
        app._fast_game_over = False
        app._last_wave_value = 130
        app._last_wave_conf = 95.0
        app._supervisor = MagicMock()
        app._status_reporter = MagicMock()
        app._status_reporter.coin_rate_samples = []
        app._pending_strategy_request = None
        app._strategy_boundary_confirmed = False
        app._handle_daily_gem_if_due = MagicMock(return_value=False)
        app._handle_mission_rewards_if_due = MagicMock(return_value=False)
        app._player_save_runtime_session_id = "save-runtime-1"
        terminal_evidence = {
            **_repair_authority(),
            "game_state": "game_over",
            "observation_id": "runtime-1:terminal",
        }
        app._current_control_workflow_evidence = lambda: terminal_evidence
        continuation = {
            "schema_version": 1,
            "source": "session_preflight_repair",
        }
        app._build_terminal_home_continuation_claim = MagicMock(
            return_value=continuation
        )
        app._commit_terminal_home_continuation = MagicMock(return_value=True)
        acquisition = _repair_terminal_acquisition()
        terminal_context = {"terminal_save_report": {}}
        app._terminal_battle_bundle = MagicMock(
            return_value=(terminal_context, acquisition, None)
        )
        events = []
        app._persist_minimal_surrender_record = MagicMock(
            side_effect=lambda *_args, **_kwargs: (
                events.append("record")
                or {"battle_id": "Battle20260807T200000-0700"}
            )
        )
        _bind_terminal_context(app)
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

        with patch(
            "core.app.return_home_from_game_over",
            side_effect=lambda **_kwargs: events.append("home") or True,
        ) as return_home:
            app._handle_primary_states("GAME_OVER", set(), frame)

        return_home.assert_called_once()
        self.assertEqual(events, ["record", "home"])
        app._persist_minimal_surrender_record.assert_called_once_with(
            terminal_context,
            acquisition,
            initiator="automation_config_repair",
            disposition_provenance={
                "acquisition": acquisition.redacted_provenance(),
                "repair_request_id": "repair-1",
                "check_id": "cards_deck",
                "reason": "Cards deck does not match",
            },
        )
        manager.on_game_over.assert_called_once_with()
        app._supervisor.persist_state.assert_not_called()
        app._build_terminal_home_continuation_claim.assert_called_once_with(
            source="session_preflight_repair",
            evidence=terminal_evidence,
        )
        app._commit_terminal_home_continuation.assert_called_once_with(
            continuation
        )

    def test_repair_record_failure_routes_terminal_without_global_pause(self):
        strategy = get_strategy("farm_t18")
        manager = MagicMock()
        manager.strategy = strategy
        manager.ctx = MissionContext(data={"mission_vars": {}})
        manager.session_preflight_repair_in_progress.return_value = True
        manager.session_preflight_repair_grant.return_value = {
            **_repair_authority(),
            "request_id": "repair-1",
            "check_id": "cards_deck",
            "reason": "Cards deck does not match",
        }
        app = App.__new__(App)
        app._mission_mgr = manager
        app._fast_game_over = False
        app._last_wave_value = 130
        app._last_wave_conf = 95.0
        app._supervisor = MagicMock()
        app._status_reporter = MagicMock()
        app._status_reporter.coin_rate_samples = []
        app._pending_strategy_request = None
        app._strategy_boundary_confirmed = False
        app._handle_daily_gem_if_due = MagicMock(return_value=False)
        app._handle_mission_rewards_if_due = MagicMock(return_value=False)
        app._player_save_runtime_session_id = "save-runtime-1"
        app._current_control_workflow_evidence = MagicMock(
            return_value={
                **_repair_authority(),
                "game_state": "game_over",
                "observation_id": "runtime-1:terminal",
            }
        )
        acquisition = _repair_terminal_acquisition()
        app._terminal_battle_bundle = MagicMock(
            return_value=({"terminal_save_report": {}}, acquisition, None)
        )
        app._persist_minimal_surrender_record = MagicMock(
            side_effect=OSError("record store unavailable")
        )
        app._runtime_action_guard = MagicMock(return_value=True)
        app._accept_pending_terminal_history_handoff = MagicMock()
        _bind_terminal_context(app)
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

        with patch("core.app.handle_game_over", return_value=None) as game_over:
            app._handle_primary_states("GAME_OVER", set(), frame)

        assert game_over.call_args.kwargs["capture_stats"] is False
        assert game_over.call_args.kwargs["return_home_after_battle"] is False
        manager.fail_session_preflight_repair.assert_called_once()
        app._supervisor.persist_state.assert_not_called()

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
        _bind_terminal_context(app)
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

        with patch("core.app.handle_game_over") as game_over:
            app._handle_primary_states("GAME_OVER", set(), frame)

        battle_context = game_over.call_args.kwargs["battle_context"]
        self.assertEqual(battle_context["strategy"], "farm_t18")
        self.assertEqual(battle_context["run_configuration"]["profile"], "farm")

    def test_terminal_only_restart_omits_unbound_strategy_and_tracker_evidence(self):
        strategy = get_strategy("farm_t18")
        manager = MagicMock()
        manager.strategy = strategy
        manager.ctx = MissionContext(
            data={"mission_vars": {"gc_session_preflight_evidence": {"valid": True}}}
        )
        manager.session_preflight_repair_in_progress.return_value = False
        app = App.__new__(App)
        app._mission_mgr = manager
        app._fast_game_over = False
        app._last_wave_value = 4991
        app._last_wave_conf = 99.0
        app._supervisor = MagicMock()
        app._status_reporter = MagicMock()
        app._status_reporter.coin_rate_samples = [{"wave": 4991}]
        app._pending_strategy_request = None
        app._strategy_boundary_confirmed = False
        app._handle_daily_gem_if_due = MagicMock(return_value=False)
        app._handle_mission_rewards_if_due = MagicMock(return_value=False)
        app._current_run_scope_id = lambda: "stale-scope"
        app._observed_active_battle_scope_id = None
        app._perk_timeline_observer = MagicMock()
        app._battle_activation_tracker = MagicMock()
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

        with patch("core.app.handle_game_over") as game_over:
            app._handle_primary_states("GAME_OVER", set(), frame)

        context = game_over.call_args.kwargs["battle_context"]
        self.assertIsNone(context["strategy"])
        self.assertEqual(context["run_configuration"], {})
        self.assertEqual(
            context["run_binding"],
            {
                "schema_version": 1,
                "status": "unbound",
                "reason": "terminal_without_observed_active_battle",
                "activity_scope_run_id": "stale-scope",
                "observed_active_scope_run_id": None,
            },
        )
        self.assertEqual(
            context["profile_progression"]["reason"],
            "adb_target_session_unavailable",
        )
        self.assertNotIn("perk_selection_timeline", context)
        self.assertNotIn("survival_ability_activations", context)
        self.assertNotIn("session_preflight_evidence", context)
        self.assertNotIn("coin_rate_samples", context)
        app._perk_timeline_observer.reset.assert_called_once_with(
            fresh_battle=False
        )
        app._perk_timeline_observer.snapshot.assert_not_called()
        app._battle_activation_tracker.reset.assert_called_once_with()
        app._battle_activation_tracker.snapshot.assert_not_called()

    def test_terminal_binding_waits_for_continuity_and_clears_at_new_battle(self):
        app = App.__new__(App)
        scope = {"id": "run-1"}
        app._current_run_scope_id = lambda: scope["id"]
        app._observed_active_battle_scope_id = None

        app._observe_terminal_run_binding(
            {"state": "RUNNING"},
            continuity_pending=True,
        )
        self.assertIsNone(app._observed_active_battle_scope_id)

        app._observe_terminal_run_binding(
            {"state": "RUNNING"},
            continuity_pending=False,
        )
        self.assertEqual(app._observed_active_battle_scope_id, "run-1")

        scope["id"] = "run-2"
        self.assertEqual(
            app._terminal_run_binding()["reason"],
            "activity_scope_changed_after_active_observation",
        )

        app._observe_terminal_run_binding(
            {
                "state": "HOME_SCREEN",
                "home_battle_control": "NEW_BATTLE",
            },
            continuity_pending=False,
        )
        self.assertIsNone(app._observed_active_battle_scope_id)

    def test_session_preflight_mismatch_completes_degraded_without_blocking(self):
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
        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertTrue(mv["gc_session_preflight_degraded"])
        self.assertEqual(
            mv["gc_session_preflight_disposition"],
            "continue_degraded",
        )
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertFalse(mv["gc_session_preflight_repair_required"])

    def test_read_only_attached_mismatch_does_not_request_restart(self):
        strategy = get_strategy("farm_t18")
        ctx = MissionContext()
        strategy.on_start(ctx)
        mv = ctx.data["mission_vars"]
        mv["last_detection_state"] = "RUNNING"
        action = next(
            rule
            for rule in strategy.rules
            if rule["name"] == "validate_requested_attached_session_preflight"
        )["do"][0]
        evidence = SimpleNamespace(
            as_dict=lambda: {
                "valid": False,
                "failed_checks": ["modules"],
            },
            requires_no_battle_repair=True,
            failed_checks=("modules",),
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

        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertTrue(mv["gc_session_preflight_degraded"])
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertFalse(mv["gc_session_preflight_restart_available"])
        self.assertFalse(mv["gc_session_preflight_repair_required"])

    def test_session_preflight_no_battle_mismatch_continues_without_surrender(self):
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
            as_dict=lambda: {
                "valid": False,
                "failed_checks": ["modules"],
                "modules": {"valid": False},
            },
            requires_no_battle_repair=True,
            failed_checks=("modules",),
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
        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertTrue(mv["gc_session_preflight_degraded"])
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertFalse(mv["gc_session_preflight_repair_required"])
        self.assertFalse(mv["gc_session_preflight_repair_in_progress"])
        self.assertTrue(mv["gc_no_battle_setup_completed"])
        self.assertEqual(mv["gc_session_preflight_repair_attempts"], 0)

    def test_later_successful_validation_clears_degraded_evidence(self):
        strategy = get_strategy("farm_t19")
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
        mismatch_evidence = SimpleNamespace(
            as_dict=lambda: {
                "valid": False,
                "failed_checks": ["perk_configuration"],
            },
            requires_no_battle_repair=True,
            failed_checks=("perk_configuration",),
        )
        complete_evidence = SimpleNamespace(
            as_dict=lambda: {"valid": True},
        )
        results = [
            GcLivePreflightResult(
                GcPreflightNavigationStatus.MISMATCH,
                "configuration mismatch",
                mismatch_evidence,
            ),
            GcLivePreflightResult(
                GcPreflightNavigationStatus.COMPLETE,
                "all requirements verified",
                complete_evidence,
            ),
        ]

        with patch(
            "core.action_executor.run_read_only_gc_preflight",
            side_effect=results,
        ):
            execute_actions(object(), [{**action, "_strategy": True}], ctx)
            self.assertTrue(mv["gc_session_preflight_attempted"])
            self.assertTrue(mv["gc_session_preflight_completed"])
            self.assertTrue(mv["gc_session_preflight_degraded"])
            self.assertFalse(mv["gc_session_preflight_repair_required"])
            self.assertTrue(mv["gc_no_battle_setup_completed"])
            mv["gc_no_battle_setup_degraded"] = True
            mv["gc_no_battle_setup_failure"] = {
                "failed_check": "perk_configuration",
                "reason": "prior Home repair exhausted",
            }
            mv["gc_degraded_home_repair"] = {
                "status": "pending_home_repair"
            }
            mv["gc_running_configuration_degradation"] = {
                "source": "return_control"
            }
            execute_actions(object(), [{**action, "_strategy": True}], ctx)

        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertFalse(mv["gc_session_preflight_degraded"])
        self.assertEqual(mv["gc_session_preflight_disposition"], "verified")
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertFalse(mv["gc_session_preflight_repair_required"])
        self.assertEqual(mv["gc_session_preflight_repair_attempts"], 0)
        self.assertEqual(mv["gc_session_preflight_repair_failure_key"], "")
        self.assertTrue(mv["gc_no_battle_setup_completed"])
        self.assertFalse(mv["gc_no_battle_setup_degraded"])
        self.assertEqual(mv["gc_no_battle_setup_failure"], {})
        self.assertNotIn("gc_degraded_home_repair", mv)
        self.assertNotIn("gc_running_configuration_degradation", mv)

    def test_mismatches_do_not_accumulate_automatic_repair_attempts(self):
        strategy = get_strategy("farm_t19")
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

        def mismatch(check_id):
            evidence = SimpleNamespace(
                as_dict=lambda: {
                    "valid": False,
                    "failed_checks": [check_id],
                },
                requires_no_battle_repair=True,
                failed_checks=(check_id,),
            )
            return GcLivePreflightResult(
                GcPreflightNavigationStatus.MISMATCH,
                "configuration mismatch",
                evidence,
            )

        with patch(
            "core.action_executor.run_read_only_gc_preflight",
            side_effect=[
                mismatch("modules"),
                mismatch("modules"),
                mismatch("perk_configuration"),
            ],
        ):
            for _ in range(3):
                execute_actions(
                    object(),
                    [{**action, "_strategy": True}],
                    ctx,
                )

        self.assertEqual(mv["gc_session_preflight_repair_attempts"], 0)
        self.assertEqual(mv["gc_session_preflight_repair_failure_key"], "")
        self.assertTrue(mv["gc_session_preflight_attempted"])
        self.assertTrue(mv["gc_session_preflight_completed"])
        self.assertTrue(mv["gc_session_preflight_degraded"])
        self.assertFalse(mv["gc_session_preflight_blocked"])
        self.assertFalse(mv["gc_session_preflight_repair_required"])
        self.assertTrue(mv["gc_no_battle_setup_completed"])

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
        self.assertEqual(mv["gc_session_preflight_repair_attempts"], 0)
        self.assertEqual(mv["gc_session_preflight_repair_failure_key"], "")

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
        app._adb_connection_coordinator = MagicMock()
        app._adb_connection_coordinator.ensure_connected.return_value = False
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
        # A paused RUNNING frame is not proof that the retained battle is
        # still active.  Lifecycle adoption waits for the forced save ID after
        # automation is enabled.
        manager.maybe_run_start.assert_not_called()
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
        app._adb_connection_coordinator = MagicMock()
        app._adb_connection_coordinator.ensure_connected.return_value = False
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

        # Do not mutate visual lifecycle state while battle identity is
        # deliberately non-authoritative during Pause.
        manager.maybe_run_start.assert_not_called()
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

    def test_legacy_terminal_block_is_released_for_normal_automation(self):
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        strategy = _IncompleteSessionPreflightStrategy()
        manager = MagicMock()
        manager.ctx = MissionContext(data={"mission_vars": {}})
        manager.strategy = strategy
        manager.run_initialization_pending.return_value = False
        manager.session_preflight_pending.return_value = True
        manager.session_preflight_terminally_blocked.return_value = True
        manager.session_preflight_failure_checks.return_value = ["modules"]

        supervisor = MagicMock()
        supervisor.is_paused = False
        supervisor.auto_return_secs = 900
        supervisor.game_speed_target = 6.3
        supervisor.apply_control.return_value = False
        supervisor.gate_decision = None
        supervisor.publish_gate_decision.return_value = None

        app = App.__new__(App)
        app._config = SimpleNamespace(wait_on_start=False)
        app._supervisor = supervisor
        app._adb_connection_coordinator = MagicMock()
        app._adb_connection_coordinator.ensure_connected.return_value = False
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
        app._handler_enabled = MagicMock(return_value=False)
        app._perk_timeline_enabled = MagicMock(return_value=False)
        app._observe_player_save_audit_visual_events = MagicMock()
        app._sync_floating_gem_tapper = MagicMock()
        app._game_speed_guard = MagicMock()
        app._game_speed_guard.handle.return_value = False
        app._battle_activation_tracker = MagicMock()
        app._battle_activation_tracker.observe.return_value = []
        app._battle_activation_tracker.drain_evidence_captures.return_value = []

        with (
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
            patch("core.app.handle_daily_gem") as handle_daily_gem,
            patch("core.app.handle_mission_rewards") as handle_mission_rewards,
            patch("core.app.handle_unknown_state") as recover,
            patch("core.app.surrender_run") as surrender,
            patch("core.app.handle_home_screen") as home,
            patch("core.app.handle_game_over") as game_over,
            patch("core.app.time.sleep"),
        ):
            app.run()

        handle_ad_gem.assert_not_called()
        manager.observe_detection.assert_called_once()
        observed_detection = manager.observe_detection.call_args.args[0]
        self.assertEqual(observed_detection["state"], "RUNNING")
        self.assertEqual(
            observed_detection["overlays"],
            ["AD_GEMS_AVAILABLE"],
        )
        manager.tick.assert_called_once_with(frame, observed_detection)
        manager.handle_overlays.assert_called_once_with(observed_detection)
        manager.on_state.assert_called_once_with(observed_detection)
        app._resolve_upgrade_detail_overlay.assert_not_called()
        app._handle_primary_states.assert_called_once_with(
            "RUNNING",
            {"AD_GEMS_AVAILABLE"},
            frame,
        )
        handle_daily_gem.assert_not_called()
        handle_mission_rewards.assert_not_called()
        recover.assert_not_called()
        surrender.assert_not_called()
        home.assert_not_called()
        game_over.assert_not_called()
        supervisor.pause_for_catastrophic_failure.assert_not_called()
        supervisor.auto_return_check.assert_not_called()
        start_tapper.assert_not_called()
        self.assertEqual(manager.ctx.data["mission_vars"]["last_wave"], 1)
        app._battle_activation_tracker.observe.assert_called_once()
        app._state_tracker.update.assert_called_once_with(
            state="RUNNING",
            menu=None,
            secondary=set(),
            overlays={"AD_GEMS_AVAILABLE"},
        )
        app._game_speed_guard.handle.assert_called_once()
        speed_guard = app._game_speed_guard.handle.call_args.kwargs[
            "action_guard_fn"
        ]
        self.assertFalse(speed_guard())
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
                patch(
                    "core.status_report.read_game_speed_control",
                    return_value=SimpleNamespace(
                        valid=True,
                        value=4.5,
                        confidence=97.0,
                    ),
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
            "State=RUNNING | Wave=1 | Coins/min=1 | Speed=x4.5",
            detail=(
                "[STATUS_DETAIL] State=RUNNING | Wave=1 | Coins/min=1 | "
                "Speed=x4.5 | Menu=— | Secondary=[—] | Overlays=[—]"
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
                patch(
                    "core.status_report.read_game_speed_control",
                    return_value=SimpleNamespace(
                        valid=True,
                        value=5.0,
                        confidence=96.5,
                    ),
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
        assert sample["game_speed"] == 5.0
        assert sample["game_speed_confidence"] == 96.5
        reporter.reset_coin_rate_samples()
        assert reporter.coin_rate_samples == []


if __name__ == "__main__":
    unittest.main()
