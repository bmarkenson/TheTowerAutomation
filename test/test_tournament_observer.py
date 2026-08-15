import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import yaml

from automation.missions.base import MissionContext
from automation.missions.manager import MissionManager
from automation.strategies import get_strategy
from core.automation_supervisor import AutomationSupervisor
from core.action_authority import (
    ActionAuthorityDecision,
    AuthorityHold,
    AuthorityHoldState,
    AuxiliaryCollector,
    RuntimeActionClass,
)
from core.action_executor import execute_actions
from core.app import App
from core.control_directives import ControlDirectiveStore
from core.exclusive_validation import exclusive_validation_definition
from core.gc_preflight_navigation import (
    GcLivePreflightResult,
    GcPreflightNavigationStatus,
)
from core.player_save_history import PlayerSaveAttachmentContext
from core.run_state import AUTOMATION, ExecMode
from core.strategy_authoring import tournament_source_to_strategy_source
from core.strategy_profiles import StrategyProfileStore
from core.tournament_preflight import (
    validate_tournament_session_preflight_screens,
)
from tools.strategy_builders.lib import build_strategy_yaml
from test.player_save_temporal_fixtures import (
    running_attachment_observations,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "config" / "strategies" / "tournament.source.yaml"
PLAN_PATH = ROOT / "config" / "strategies" / "tournament.strategy.yaml"


def _load(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict)
    return data


def _bind_terminal_context(app: App, scope_id: str = "test-tournament") -> None:
    app._current_run_scope_id = lambda: scope_id
    app._observed_active_battle_scope_id = scope_id
    app._observed_active_round_identity_fingerprint = "a" * 64
    app._terminal_round_identity_fingerprint = "a" * 64


def test_tournament_generated_plan_matches_compact_source():
    assert build_strategy_yaml(_load(SOURCE_PATH)) == _load(PLAN_PATH)


def test_tournament_strategy_declares_exclusive_validation_then_observes():
    strategy = get_strategy("tournament")

    assert strategy is not None
    assert strategy.name == "tournament"
    assert strategy.runtime_policy() == {
        "player_save_preflight": "save_first",
        "handlers": [
            "ad_gem",
            "daily_gem",
            "mission_rewards",
            "game_over",
            "game_speed",
        ],
        "auto_return": False,
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
    assert (
        attached_validation["do"][0]["stay_in_battle_when_attached"] is True
    )
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
    assert action["stay_in_battle_when_attached"] is True
    assert action["requirements"]["loadout_policies"] == {
        "modules": "observe"
    }
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
    assert strategy.run_configuration()["loadout"]["modules"]["mode"] == (
        "observe"
    )
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


def test_tournament_module_variation_completes_and_is_logged_as_information():
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
        "valid": True,
        "module_mode": "observe",
        "failed_checks": [],
        "modules": {
            "mode": "observe",
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
        requires_no_battle_repair=False,
        failed_checks=(),
    )
    result = GcLivePreflightResult(
        GcPreflightNavigationStatus.COMPLETE,
        "all requirements verified",
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
        allow_repair=False,
    )
    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_attempted"]
    assert variables["gc_session_preflight_completed"]
    assert not variables["gc_session_preflight_blocked"]
    assert not variables["gc_session_preflight_repair_required"]
    assert variables["gc_session_preflight_failed_checks"] == []
    assert strategy.is_session_preflight_complete(ctx)
    assert not strategy.tick(ctx, object(), {"state": "RUNNING"})
    mission_log.assert_any_call(
        "[SESSION_PREFLIGHT] Session validation completed; module variation "
        "observed — Generator Assist module: reference Singularity Harness, "
        "observed Galaxy Compressor",
        "INFO",
    )
    mission_log.assert_any_call(
        "[SESSION_PREFLIGHT] completed_evidence="
        + json.dumps(evidence_payload, sort_keys=True),
        "DEBUG",
    )


def test_attached_tournament_executor_requests_an_in_battle_only_route():
    strategy = get_strategy("tournament")
    action = strategy.rules[0]["do"][0]
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )
    evidence = SimpleNamespace(
        as_dict=lambda: {
            "valid": True,
            "deferred_checks": ["workshop_preset"],
        }
    )
    result = GcLivePreflightResult(
        GcPreflightNavigationStatus.COMPLETE,
        "active requirements verified; boundary checks deferred",
        evidence,
    )

    with patch(
        "core.action_executor.run_read_only_gc_preflight",
        return_value=result,
    ) as run_preflight:
        execute_actions(object(), [{**action, "_strategy": True}], ctx)

    run_preflight.assert_called_once_with(
        action["requirements"],
        validate_fn=validate_tournament_session_preflight_screens,
        stay_in_battle=True,
        allow_repair=False,
    )


def test_continuity_supplies_bound_workshop_evidence_to_attached_tournament():
    strategy = get_strategy("tournament")
    assert strategy is not None
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(strategy=strategy, ctx=ctx)
    app._exclusive_validation_ownership_hold = False
    app._current_player_save_attachment_context = lambda: (
        PlayerSaveAttachmentContext(
            runtime_session_id="runtime-1",
            activity_scope_id="scope-1",
            active_round_identity_fingerprint="active-round-fingerprint",
            target="private-target",
            target_generation=3,
            active_battle_observed=True,
        )
    )
    observations = running_attachment_observations(
        {"workshop_preset": "Tourney"}
    )

    with patch("core.app.log"):
        app._apply_running_attachment_projection(
            SimpleNamespace(running_attachment_observations=observations)
        )

    bound = ctx.data["player_save_attachment_evidence"]
    action = strategy.rules[0]["do"][0]
    evidence = SimpleNamespace(
        as_dict=lambda: {"valid": True},
        deferred_checks=(),
    )
    result = GcLivePreflightResult(
        GcPreflightNavigationStatus.COMPLETE,
        "all requirements verified",
        evidence,
    )
    with patch(
        "core.action_executor.run_read_only_gc_preflight",
        return_value=result,
    ) as run_preflight:
        execute_actions(object(), [{**action, "_strategy": True}], ctx)

    kwargs = run_preflight.call_args.kwargs
    assert kwargs["player_save_preflight"] is bound
    assert kwargs["stay_in_battle"] is True
    assert bound.consume("workshop_preset") == "Tourney"


def test_tournament_attachment_rejects_target_change_before_publish():
    strategy = get_strategy("tournament")
    assert strategy is not None
    ctx = MissionContext(data={"mission_vars": {}})
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(strategy=strategy, ctx=ctx)
    app._exclusive_validation_ownership_hold = False
    app._current_player_save_attachment_context = lambda: (
        PlayerSaveAttachmentContext(
            runtime_session_id="runtime-1",
            activity_scope_id="scope-1",
            active_round_identity_fingerprint="active-round-fingerprint",
            target="private-target",
            target_generation=4,
            active_battle_observed=True,
        )
    )

    with patch("core.app.log") as logged:
        app._apply_running_attachment_projection(
            SimpleNamespace(
                running_attachment_observations=(
                    running_attachment_observations(
                        {"workshop_preset": "Tourney"}
                    )
                )
            )
        )

    assert "player_save_attachment_evidence" not in ctx.data
    assert "runtime or target binding changed" in logged.call_args.args[0]


def test_tournament_invariant_mismatch_is_nonblocking_but_warned():
    strategy = get_strategy("tournament")
    action = next(
        rule
        for rule in strategy.rules
        if rule["name"] == "validate_tournament_session_preflight"
    )["do"][0]
    ctx = MissionContext(
        data={"mission_vars": {"last_detection_state": "RUNNING"}}
    )
    evidence_payload = {
        "valid": False,
        "failed_checks": ["ultimate_weapons"],
        "ultimate_weapons": {
            "weapons": [
                {
                    "label": "Spotlight",
                    "valid": False,
                    "mismatched_toggles": ["missiles=on (actual=off)"],
                }
            ]
        },
    }
    evidence = SimpleNamespace(
        as_dict=lambda: evidence_payload,
        requires_no_battle_repair=False,
        failed_checks=("ultimate_weapons",),
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
        ),
        patch("core.action_executor.log_mission") as mission_log,
    ):
        execute_actions(object(), [{**action, "_strategy": True}], ctx)

    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_attempted"]
    assert variables["gc_session_preflight_completed"]
    assert variables["gc_session_preflight_degraded"]
    assert variables["gc_session_preflight_disposition"] == "continue_degraded"
    assert not variables["gc_session_preflight_blocked"]
    assert variables["gc_session_preflight_failed_checks"] == [
        "ultimate_weapons"
    ]
    mission_log.assert_any_call(
        "[SESSION_PREFLIGHT] Configuration mismatch flagged — Ultimate Weapons "
        "Spotlight: missiles=on (actual=off). Automation continues in degraded "
        "mode; only a safe Home boundary may repair it.",
        "WARN",
    )
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(strategy=strategy, ctx=ctx)
    app._current_run_scope_id = lambda: "run-observe"
    app._sync_strategy_action_gate(
        terminally_blocked=bool(
            variables["gc_session_preflight_blocked"]
        )
    )
    assert app._get_action_authority().strategy_gate is None


def test_tournament_attachment_never_enforces_battle_loadout():
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(
        None,
        strategy,
        defer_startup_gates_until_next_run=True,
        validate_attached_battle=True,
    )
    manager.start()

    manager.maybe_run_start({"state": "RUNNING"})
    actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

    assert manager.session_preflight_pending()
    assert len(actions) == 1
    assert actions[0]["type"] == "session_preflight"
    assert actions[0]["allow_repair"] is False

    manager.ctx.data["mission_vars"]["gc_session_preflight_attempted"] = True
    manager.ctx.data["mission_vars"]["gc_session_preflight_completed"] = True
    actions = strategy.tick(manager.ctx, object(), {"state": "RUNNING"})

    assert len(actions) == 1
    assert actions[0]["type"] == "damage_slider_configure"
    assert actions[0]["mode"] == "enforce"
    assert not manager.session_preflight_pending()
    assert not strategy.is_session_preflight_complete(manager.ctx)


def test_tournament_policy_allows_guarded_reward_collectors_only():
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(strategy=get_strategy("tournament"))

    assert app._handler_enabled("ad_gem")
    assert app._handler_enabled("daily_gem")
    assert app._handler_enabled("mission_rewards")
    assert app._handler_enabled("game_over")
    assert app._handler_enabled("game_speed")
    assert not app._handler_enabled("floating_gem")
    assert not app._handler_enabled("event_mission_warnings")
    assert not app._handler_enabled("home")
    assert not app._handler_enabled("auto_return")
    assert not app._handler_enabled("coin_display")
    assert not app._handler_enabled("upgrade_detail")
    assert not app._handler_enabled("unknown_recovery")


def test_tournament_does_not_start_independent_floating_gem_tapper():
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(strategy=get_strategy("tournament"))
    app._blind_tapper_suspended = False
    app._active_round_identity_fingerprint = "a" * 64

    with (
        patch("core.app.start_blind_gem_tapper") as start,
        patch("core.app.stop_blind_gem_tapper") as stop,
    ):
        app._sync_floating_gem_tapper(
            state="RUNNING",
            auxiliary_authority=ActionAuthorityDecision(
                RuntimeActionClass.AUXILIARY_COLLECTION,
                True,
                "test authority",
                collector=AuxiliaryCollector.FLOATING_GEM_SCAN,
            ),
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
    app._active_round_identity_fingerprint = "a" * 64
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


@pytest.mark.parametrize(
    "terminal_policy",
    [ExecMode.NEXT_BATTLE, ExecMode.WAIT, ExecMode.HOME],
)
def test_tournament_main_loop_preserves_policy_after_attached_gate_releases(
    terminal_policy,
):
    strategy = get_strategy("tournament")
    assert strategy is not None
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    gate_complete = False
    preflight_owners = []

    manager = MagicMock()
    manager.strategy = strategy
    manager.ctx = MissionContext(data={"mission_vars": {}})
    manager.run_initialization_pending.return_value = False
    manager.session_preflight_pending.side_effect = lambda: not gate_complete
    manager.session_preflight_terminally_blocked.return_value = False
    manager.session_preflight_repair_required.return_value = False

    def complete_attached_gate(*_args, strategy_only=False, **_kwargs):
        nonlocal gate_complete
        if strategy_only:
            preflight_owners.append(app._active_action_authority_owner)
            assert app._runtime_action_guard()
            assert not app._runtime_action_guard(
                owner=AuthorityHold.EXCLUSIVE_VALIDATION
            )
            gate_complete = True

    manager.tick.side_effect = complete_attached_gate

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
    app._active_round_identity_fingerprint = "a" * 64
    app._run_initialization_gate_logged = False
    app._session_preflight_gate_logged = False
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._capture_frame = MagicMock(side_effect=[frame, frame, KeyboardInterrupt])
    app._resolve_upgrade_detail_overlay = MagicMock()
    app._run_perk_selector = MagicMock()
    app._run_perk_selector.handle.return_value = False
    app._battle_activation_tracker = MagicMock()
    app._battle_activation_tracker.observe.return_value = []
    app._battle_activation_tracker.drain_evidence_captures.return_value = []
    app._advance_exclusive_validation = MagicMock(return_value=False)
    app._advance_exclusive_validation_launch = MagicMock(return_value=False)
    app._observe_strategy_request = MagicMock()
    app._handle_daily_gem_if_due = MagicMock(return_value=False)
    app._handle_mission_rewards_if_due = MagicMock(return_value=False)
    previous_mode = AUTOMATION.mode

    try:
        AUTOMATION.mode = terminal_policy
        with (
            patch("core.app.threading.Thread"),
            patch(
                "core.app.detect_state_and_overlays",
                return_value={
                    "state": "RUNNING",
                    "menu": "UW_MENU",
                    "secondary_states": ["TOURNAMENT"],
                    "overlays": ["AD_GEMS_AVAILABLE", "MENU_OPEN"],
                },
            ),
            patch("core.app.detect_wave_number_from_image", return_value=(900, 99.0)),
            patch("core.app.stop_blind_gem_tapper", return_value=False),
            patch("core.app.handle_ad_gem") as handle_ad_gem,
            patch("core.app.time.sleep"),
        ):
            app.run()
        assert AUTOMATION.mode is terminal_policy
    finally:
        AUTOMATION.mode = previous_mode

    assert gate_complete
    assert preflight_owners == [AuthorityHold.SESSION_PREFLIGHT]
    handle_ad_gem.assert_called_once()
    assert callable(handle_ad_gem.call_args.kwargs["action_guard_fn"])
    assert callable(
        handle_ad_gem.call_args.kwargs["floating_action_guard_fn"]
    )


def _assert_owned_validation_main_loop(
    strategy,
    expected_actions,
    *,
    supervisor=None,
    active_request_id=None,
    pause_after_actions=None,
):
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    running_detection = {
        "state": "RUNNING",
        "menu": "ATTACK_MENU",
        "secondary_states": [],
        "overlays": ["MENU_CLOSED"],
    }
    dispatched_actions = []
    observed_owners = []
    pre_capture_holds = []

    manager = MissionManager(None, strategy)
    manager.start()
    assert manager.maybe_run_start(running_detection)
    manager.set_exclusive_validation_battle(True)
    assert manager.session_preflight_pending()

    app = App.__new__(App)

    def execute_validation_phase(
        screen,
        actions,
        ctx,
        *,
        action_guard_fn,
    ):
        assert screen is frame
        assert action_guard_fn()
        observed_owners.append(app._active_action_authority_owner)
        assert app._runtime_action_guard()
        assert not app._runtime_action_guard(
            owner=AuthorityHold.SESSION_PREFLIGHT
        )
        assert len(actions) == 1
        action_type = actions[0]["type"]
        assert actions[0]["_strategy"] is True
        dispatched_actions.append((action_type, actions[0].get("mode")))
        mission_vars = ctx.data["mission_vars"]
        if action_type == "damage_slider_configure":
            mission_vars["damage_slider_checked"] = True
        elif action_type == "orb_distance_configure":
            if actions[0].get("mode") == "observe":
                mission_vars["orb_distance_observed"] = True
            else:
                mission_vars["orb_distance_checked"] = True
        else:
            assert action_type == "session_preflight"
            mission_vars["gc_session_preflight_attempted"] = True
            mission_vars["gc_session_preflight_completed"] = True
        if (
            len(dispatched_actions) == len(expected_actions)
            and pause_after_actions is not None
        ):
            pause_after_actions()

    app._config = SimpleNamespace(wait_on_start=False)
    real_validation = supervisor is not None
    app._supervisor = supervisor or MagicMock(
        is_paused=False,
        auto_return_secs=900,
    )
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
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._active_round_identity_fingerprint = "a" * 64
    app._observed_active_round_identity_fingerprint = "a" * 64
    app._active_exclusive_validation_battle_identity = "a" * 64
    app._battle_identity_reconciliation_required = False
    app._authority_holds = (
        AuthorityHoldState(
            AuthorityHold.EXCLUSIVE_VALIDATION,
            "validation owns capture-adjacent input",
        ),
    )

    def capture_frame():
        pre_capture_holds.append(
            tuple(hold.hold for hold in app._authority_holds)
        )
        if len(pre_capture_holds) <= len(expected_actions):
            return frame
        raise KeyboardInterrupt

    app._capture_frame = MagicMock(side_effect=capture_frame)
    app._resolve_upgrade_detail_overlay = MagicMock()
    app._run_perk_selector = MagicMock()
    app._run_perk_selector.handle.return_value = False
    app._battle_activation_tracker = MagicMock()
    app._battle_activation_tracker.observe.return_value = []
    app._battle_activation_tracker.drain_evidence_captures.return_value = []
    if real_validation:
        app._active_exclusive_validation_request_id = active_request_id
        app._active_exclusive_validation_launch_request_id = None
        app._exclusive_validation_battle_dispatch_hold = None
        app._exclusive_validation_launch_dispatch_hold = None
        app._exclusive_validation_claimed_start_hold = None
        app._exclusive_validation_launch_start_hold = None
        app._exclusive_validation_passive_battle_hold = None
        app._exclusive_validation_passive_battle_scope_id = None
        app._exclusive_validation_terminal_hold = None
        app._exclusive_validation_terminal_mode = None
        app._exclusive_validation_terminal_outcome = None
        app._exclusive_validation_terminal_reason = None
        app._exclusive_validation_terminal_announced = None
        app._exclusive_validation_terminal_proof_kind_value = None
        app._exclusive_validation_ownership_hold = False
    else:
        app._observe_exclusive_validation_battle_start = MagicMock()
        app._exclusive_validation_in_progress = MagicMock(return_value=True)
        app._advance_exclusive_validation = MagicMock(return_value=False)
        app._advance_exclusive_validation_launch = MagicMock(return_value=False)
    app._observe_strategy_request = MagicMock()
    app._handle_daily_gem_if_due = MagicMock(return_value=False)
    app._handle_mission_rewards_if_due = MagicMock(return_value=False)
    app._handle_primary_states = MagicMock()
    manager._action_guard_fn = lambda: app._runtime_action_guard()

    previous_state = AUTOMATION.state
    previous_mode = AUTOMATION.mode
    try:
        with (
            patch("core.app.threading.Thread"),
            patch(
                "core.app.detect_state_and_overlays",
                return_value=running_detection,
            ),
            patch(
                "core.app.detect_wave_number_from_image",
                return_value=(1, 99.0),
            ),
            patch("core.app.stop_blind_gem_tapper", return_value=False),
            patch(
                "automation.missions.manager.execute_actions",
                side_effect=execute_validation_phase,
            ) as execute_actions,
            patch("core.app.time.sleep"),
        ):
            app.run()
    finally:
        AUTOMATION.state = previous_state
        AUTOMATION.mode = previous_mode

    assert execute_actions.call_count == len(expected_actions)
    assert dispatched_actions == expected_actions
    assert strategy.is_session_preflight_complete(manager.ctx)
    assert observed_owners == [
        AuthorityHold.EXCLUSIVE_VALIDATION
    ] * len(expected_actions)
    assert pre_capture_holds == [
        (AuthorityHold.EXCLUSIVE_VALIDATION,),
    ] * (len(expected_actions) + 1)
    assert app._active_action_authority_owner is None
    app._handle_primary_states.assert_not_called()


def test_owned_validation_main_loop_dispatches_under_exclusive_owner():
    strategy = get_strategy("tournament")
    assert strategy is not None
    _assert_owned_validation_main_loop(
        strategy,
        [
            ("damage_slider_configure", "enforce"),
            ("orb_distance_configure", "enforce"),
            ("session_preflight", None),
        ],
    )


def test_custom_tournament_validation_uses_generic_exclusive_owner(
    tmp_path,
    monkeypatch,
):
    profile_directory = tmp_path / "strategy_profiles"
    profile_store = StrategyProfileStore(
        profile_directory=profile_directory
    )
    source = tournament_source_to_strategy_source(
        _load(SOURCE_PATH),
        display_name="Tournament Observe",
    )
    source.update(
        id="tournament_observe",
        display_name="Tournament Observe",
    )
    source["settings"]["modules"] = {"policy": "ignore"}
    source["settings"]["orb_distance"]["policy"] = "observe"
    profile_store.publish_authoring_strategy(source)
    monkeypatch.setenv(
        "THETOWER_STRATEGY_PROFILE_DIR",
        str(profile_directory),
    )
    strategy = get_strategy("tournament_observe")
    assert strategy is not None
    definition = exclusive_validation_definition(strategy)
    assert definition is not None

    control_store = ControlDirectiveStore(
        tmp_path / "automation_ctl.json",
        strategy_profile_dir=profile_directory,
    )
    control_store.set_strategy("tournament_observe", source="test")
    control_store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(
        control_file=str(control_store.path),
    )
    supervisor.apply_control()
    ledger = control_store.status()["exclusive_validation"]
    receipt = ledger["receipts"][ledger["current_request_id"]]
    assert receipt["strategy"] == "tournament_observe"
    assert receipt["configuration_fingerprint"] == (
        definition.configuration_fingerprint
    )
    claimed = supervisor.claim_exclusive_validation(
        strategy_request_id=receipt["strategy_request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        timeout_seconds=definition.timeout_seconds,
    )
    assert claimed is not None
    running = supervisor.mark_exclusive_validation_running(
        claimed["request_id"]
    )
    assert running is not None

    def pause_after_custom_phases():
        control_store.set_state("PAUSED", source="test-boundary")
        supervisor.apply_control()

    _assert_owned_validation_main_loop(
        strategy,
        [
            ("damage_slider_configure", "enforce"),
            ("orb_distance_configure", "observe"),
            ("session_preflight", None),
        ],
        supervisor=supervisor,
        active_request_id=running["request_id"],
        pause_after_actions=pause_after_custom_phases,
    )
    retained = supervisor.exclusive_validation_receipt(
        request_id=running["request_id"]
    )
    assert retained is not None
    assert retained["status"] == "running"


def test_owned_validation_cleanup_retries_result_write_from_home():
    strategy = get_strategy("tournament")
    assert strategy is not None
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    observed_owners = []
    receipt = {
        "request_id": "owned-cleanup",
        "status": "cleanup",
        "pending_outcome": "failed",
        "pending_reason": "validation failed",
    }
    result = {
        **receipt,
        "status": "result",
        "outcome": "failed",
        "reason": "validation failed",
    }

    manager = MagicMock()
    manager.strategy = strategy
    manager.ctx = MissionContext(
        data={
            "exclusive_validation_battle": True,
            "mission_vars": {"exclusive_validation_battle": True},
        }
    )
    manager.run_initialization_pending.return_value = False
    manager.session_preflight_pending.return_value = False
    manager.session_preflight_terminally_blocked.return_value = False

    app = App.__new__(App)
    app._config = SimpleNamespace(wait_on_start=False)
    app._supervisor = MagicMock(is_paused=False, auto_return_secs=900)
    app._supervisor.apply_control.return_value = False
    app._supervisor.owns_exclusive_validation.return_value = True
    finish_attempts = 0
    finish_owners = []

    def finish_result(*_args, **_kwargs):
        nonlocal finish_attempts
        finish_attempts += 1
        finish_owners.append(app._active_action_authority_owner)
        # The first result write fails after physical Home cleanup. The
        # validation boundary must remain installed until a later heartbeat
        # persists the result without tapping Home again.
        assert (
            manager.finalize_exclusive_validation_game_over_boundary.call_count
            == 0
        )
        if finish_attempts == 1:
            app._supervisor.battle_workflow = {
                "request_id": "queued-successor",
                "intent": "start_battle",
                "status": "acknowledged",
            }
            return None
        return result

    app._supervisor.finish_exclusive_validation.side_effect = finish_result
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
    app._session_preflight_terminal_blocked_logged = False
    app._session_preflight_repair_denial_logged = False
    app._steady_run_entry_pending = False
    app._exclusive_validation_terminal_hold = None
    app._active_exclusive_validation_request_id = receipt["request_id"]
    app._capture_frame = MagicMock(
        side_effect=[frame, frame, KeyboardInterrupt]
    )
    app._resolve_upgrade_detail_overlay = MagicMock()
    app._run_perk_selector = MagicMock()
    app._run_perk_selector.handle.return_value = False
    app._battle_activation_tracker = MagicMock()
    app._battle_activation_tracker.observe.return_value = []
    app._battle_activation_tracker.drain_evidence_captures.return_value = []
    app._observe_exclusive_validation_battle_start = MagicMock()
    app._exclusive_validation_in_progress = MagicMock(return_value=True)
    app._exclusive_validation_cleanup_in_progress = MagicMock(return_value=True)
    app._reconcile_exclusive_validation = MagicMock(
        side_effect=lambda: result if finish_attempts >= 2 else receipt
    )
    app._exclusive_validation_receipt = MagicMock(return_value=receipt)
    app._advance_exclusive_validation = MagicMock(return_value=False)
    app._advance_exclusive_validation_launch = MagicMock(return_value=False)
    app._observe_strategy_request = MagicMock()
    app._process_strategy_boundary = MagicMock()
    app._handle_daily_gem_if_due = MagicMock(return_value=False)
    app._handle_mission_rewards_if_due = MagicMock(return_value=False)
    app._handle_primary_states = MagicMock()
    app._apply_pending_strategy = MagicMock()
    app._announce_exclusive_validation_result = MagicMock()

    def return_home_under_exclusive_owner(*, action_guard, **_kwargs):
        observed_owners.append(app._active_action_authority_owner)
        assert action_guard()
        assert app._runtime_action_guard(
            action_class=RuntimeActionClass.LIFECYCLE_ACTION
        )
        return True

    game_over = {
        "state": "GAME_OVER",
        "menu": "UNKNOWN",
        "secondary_states": [],
        "overlays": [],
    }
    home = {
        "state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
        "menu": "UNKNOWN",
        "secondary_states": [],
        "overlays": [],
    }
    app._annotate_home_battle_control = MagicMock()

    with (
        patch("core.app.threading.Thread"),
        patch(
            "core.app.detect_state_and_overlays",
            side_effect=(game_over, home),
        ),
        patch("core.app.detect_wave_number_from_image", return_value=(None, -1.0)),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch(
            "core.app.return_home_from_game_over",
            side_effect=return_home_under_exclusive_owner,
        ) as return_home,
        patch("core.app.time.sleep"),
    ):
        app.run()

    manager.tick.assert_not_called()
    app._handle_primary_states.assert_not_called()
    assert app._supervisor.battle_workflow["request_id"] == (
        "queued-successor"
    )
    return_home.assert_called_once()
    assert app._supervisor.finish_exclusive_validation.call_count == 2
    for call in app._supervisor.finish_exclusive_validation.call_args_list:
        assert call.args == (receipt["request_id"],)
        assert call.kwargs == {
            "outcome": "failed",
            "reason": "validation failed",
            "allowed_statuses": ("cleanup",),
        }
    app._announce_exclusive_validation_result.assert_called_once_with(result)
    manager.finalize_exclusive_validation_game_over_boundary.assert_called_once()
    manager.set_exclusive_validation_battle.assert_called_once_with(False)
    app._status_reporter.reset_coin_rate_samples.assert_called_once()
    app._apply_pending_strategy.assert_called_once()
    assert observed_owners == [AuthorityHold.EXCLUSIVE_VALIDATION]
    assert finish_attempts == 2
    assert finish_owners == [AuthorityHold.EXCLUSIVE_VALIDATION] * 2
    assert app._exclusive_validation_terminal_hold is None
    assert app._active_action_authority_owner is None


def test_paused_manual_successor_waits_for_validation_finalization():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    running = {
        "state": "RUNNING",
        "menu": "ATTACK_MENU",
        "secondary_states": [],
        "overlays": ["MENU_CLOSED"],
    }
    cleanup = {
        "request_id": "finished-validation",
        "status": "cleanup",
        "pending_outcome": "failed",
        "pending_reason": "validation cleanup persisted",
    }
    result = {
        **cleanup,
        "status": "result",
        "outcome": "failed",
        "reason": "validation cleanup persisted",
    }
    events = []
    app = App.__new__(App)
    app._config = SimpleNamespace(wait_on_start=False)
    app._supervisor = MagicMock(auto_return_secs=900)
    apply_count = 0

    def apply_control():
        nonlocal apply_count
        apply_count += 1
        # Startup and the first visible successor frame remain paused. Resume
        # permits receipt-only finalization on the second frame.
        app._supervisor.is_paused = apply_count < 3
        return False

    app._supervisor.apply_control.side_effect = apply_control
    finish_persisted = False

    def finish_result(*_args, **_kwargs):
        nonlocal finish_persisted
        finish_persisted = True
        return result

    app._supervisor.finish_exclusive_validation.side_effect = finish_result
    app._supervisor.owns_exclusive_validation.return_value = True
    app._adb_connection_coordinator = MagicMock()
    app._adb_connection_coordinator.ensure_connected.return_value = False
    strategy = get_strategy("tournament")
    assert strategy is not None
    manager = MissionManager(None, strategy)
    manager.start()
    assert manager.maybe_run_start(running)
    manager.set_exclusive_validation_battle(True)
    finalize_old = manager.finalize_exclusive_validation_game_over_boundary

    def finalize_validation_boundary():
        finalize_old()
        events.append("finalize-old")

    manager.finalize_exclusive_validation_game_over_boundary = MagicMock(
        side_effect=finalize_validation_boundary
    )
    observe_successor = manager.maybe_run_start

    def adopt_successor(_detection):
        assert observe_successor(_detection) is True
        events.append("adopt-successor")
        raise KeyboardInterrupt

    manager.maybe_run_start = MagicMock(side_effect=adopt_successor)
    app._mission_mgr = manager
    app._status_reporter = MagicMock()
    app._state_tracker = MagicMock()
    app._event_mission_tracker = MagicMock()
    app._match_trace = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._last_wave_ts = 0.0
    app._blind_tapper_suspended = False
    app._authority_holds = ()
    app._active_exclusive_validation_request_id = result["request_id"]
    app._exclusive_validation_terminal_hold = result["request_id"]
    app._exclusive_validation_terminal_mode = "home_cleanup"
    app._exclusive_validation_terminal_outcome = "failed"
    app._exclusive_validation_terminal_reason = cleanup["pending_reason"]
    app._exclusive_validation_terminal_announced = None
    app._exclusive_validation_receipt = MagicMock(
        side_effect=lambda: result if finish_persisted else cleanup
    )
    app._reconcile_exclusive_validation = MagicMock(
        side_effect=lambda: result if finish_persisted else cleanup
    )
    app._apply_pending_strategy = MagicMock()
    app._announce_exclusive_validation_result = MagicMock()
    app._refreshed_operator_authority_holds = MagicMock(return_value=())
    app._observe_strategy_request = MagicMock()
    app._sync_interactive_development_control_boundary = MagicMock()
    app._annotate_home_battle_control = MagicMock()
    app._record_control_observation = MagicMock()
    app._yield_on_unexpected_manual_activity = MagicMock()
    app._sync_operator_control_workflows = MagicMock()
    app._operator_workflow_authority_hold = MagicMock(return_value=None)
    app._advance_pending_home_setup_recovery = MagicMock(return_value=False)
    app._observe_player_save_audit_screen = MagicMock()
    app._sync_interactive_development_observation = MagicMock()
    app._observe_no_strategy_frame = MagicMock()
    app._process_strategy_boundary = MagicMock()
    app._observe_strategy_gate_boundary = MagicMock()
    app._capture_frame = MagicMock(return_value=frame)
    identity_forced = False

    def force_successor_identity(detection, _frame):
        nonlocal identity_forced
        if app._supervisor.is_paused or identity_forced:
            return False
        identity_forced = True
        app._active_round_identity_fingerprint = "b" * 64
        app._observed_active_round_identity_fingerprint = "b" * 64
        app._battle_identity_reconciliation_required = False
        return True

    app._force_battle_identity = MagicMock(
        side_effect=force_successor_identity
    )

    previous_state = AUTOMATION.state
    try:
        AUTOMATION.state = "RUNNING"
        with (
            patch("core.app.threading.Thread"),
            patch("core.app.detect_state_and_overlays", return_value=running),
            patch("core.app.stop_blind_gem_tapper", return_value=False),
            patch("core.app.return_home_from_game_over") as return_home,
            patch(
                "automation.missions.manager.start_activity_scope"
            ) as start_scope,
            patch("core.app.time.sleep") as sleep,
        ):
            app.run()
    finally:
        AUTOMATION.state = previous_state

    assert events == ["finalize-old", "adopt-successor"]
    assert manager.ctx.data["last_detection_state"] == "RUNNING"
    assert manager.maybe_run_start.call_count == 1
    # Neither the paused frame nor the resumed finalization frame was allowed
    # to acknowledge a workflow or mutate the successor's lifecycle.
    assert identity_forced
    assert app._sync_operator_control_workflows.call_count == 2
    return_home.assert_not_called()
    app._supervisor.finish_exclusive_validation.assert_called_once_with(
        cleanup["request_id"],
        outcome="failed",
        reason=cleanup["pending_reason"],
        allowed_statuses=("cleanup",),
    )
    app._announce_exclusive_validation_result.assert_called_once_with(result)
    manager.finalize_exclusive_validation_game_over_boundary.assert_called_once()
    start_scope.assert_called_once_with(
        reason="exclusive_validation_game_over_boundary",
        carry_terminal_history_handoff=True,
    )
    assert manager.ctx.data["exclusive_validation_battle"] is False
    app._apply_pending_strategy.assert_called_once()
    assert app._exclusive_validation_terminal_hold is None
    assert sleep.call_count == 2
    assert all(item.args == (1.0,) for item in sleep.call_args_list)


def test_owned_validation_cleanup_retries_before_releasing_boundary():
    app = App.__new__(App)
    cleanup = {
        "request_id": "owned-cleanup",
        "status": "cleanup",
    }
    result = {
        **cleanup,
        "status": "result",
        "outcome": "ready",
    }
    app._mission_mgr = MagicMock()
    app._status_reporter = MagicMock()
    app._apply_pending_strategy = MagicMock()
    app._exclusive_validation_terminal_hold = cleanup["request_id"]
    app._handle_exclusive_validation_game_over = MagicMock(return_value=True)
    app._reconcile_exclusive_validation = MagicMock(
        side_effect=(cleanup, cleanup, result)
    )

    assert app._dispatch_exclusive_validation_game_over()
    app._mission_mgr.finalize_exclusive_validation_game_over_boundary.assert_not_called()
    app._mission_mgr.set_exclusive_validation_battle.assert_not_called()
    app._status_reporter.reset_coin_rate_samples.assert_not_called()
    app._apply_pending_strategy.assert_not_called()

    assert app._dispatch_exclusive_validation_game_over()
    app._mission_mgr.finalize_exclusive_validation_game_over_boundary.assert_called_once()
    app._mission_mgr.set_exclusive_validation_battle.assert_called_once_with(
        False
    )
    app._status_reporter.reset_coin_rate_samples.assert_called_once()
    app._apply_pending_strategy.assert_called_once()
    app._handle_exclusive_validation_game_over.assert_called_once_with(
        home_cleanup_verified=False
    )
    assert app._exclusive_validation_terminal_hold is None


def test_tournament_running_handler_checks_guarded_rewards_before_visible_ad_gem():
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(strategy=get_strategy("tournament"))
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    app._handle_daily_gem_if_due = MagicMock(return_value=False)
    app._handle_mission_rewards_if_due = MagicMock(return_value=False)
    overlays = {"AD_GEMS_AVAILABLE", "DAILY_GEMS_AVAILABLE", "MENU_OPEN"}

    with patch("core.app.handle_ad_gem") as ad_gem:
        app._handle_primary_states("RUNNING", overlays, frame)

    app._handle_daily_gem_if_due.assert_called_once_with("RUNNING", overlays)
    app._handle_mission_rewards_if_due.assert_called_once_with(
        "RUNNING",
        frame,
        overlays,
    )
    ad_gem.assert_called_once()
    assert callable(ad_gem.call_args.kwargs["action_guard_fn"])
    assert callable(ad_gem.call_args.kwargs["floating_action_guard_fn"])


@pytest.mark.parametrize(
    "terminal_policy",
    [ExecMode.NEXT_BATTLE, ExecMode.WAIT, ExecMode.HOME],
)
def test_tournament_game_over_preserves_terminal_policy_and_profile_evidence(
    terminal_policy,
):
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
    _bind_terminal_context(app)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    previous_mode = AUTOMATION.mode

    try:
        AUTOMATION.mode = terminal_policy
        with patch("core.app.handle_game_over") as game_over:
            app._handle_primary_states("GAME_OVER", set(), frame)
        assert AUTOMATION.mode is terminal_policy
    finally:
        AUTOMATION.mode = previous_mode

    kwargs = game_over.call_args.kwargs
    app._supervisor.persist_mode.assert_not_called()
    assert kwargs["capture_stats"] is True
    assert kwargs["battle_context"]["run_configuration"]["profile"] == "tournament"
    assert kwargs["battle_context"]["session_preflight_evidence"] == {
        "valid": True
    }
    assert "observed_run_configuration" not in kwargs["battle_context"]
    manager.on_game_over.assert_called_once_with()
    app._status_reporter.reset_coin_rate_samples.assert_called_once_with()


@pytest.mark.parametrize(
    "terminal_policy",
    [ExecMode.NEXT_BATTLE, ExecMode.WAIT, ExecMode.HOME],
)
def test_tournament_results_are_recorded_once_without_changing_policy(
    terminal_policy,
):
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
    continuation = {
        "schema_version": 1,
        "source": "tournament_results",
    }
    app._build_terminal_home_continuation_claim = MagicMock(
        return_value=(
            continuation
            if terminal_policy is ExecMode.NEXT_BATTLE
            else None
        )
    )
    app._commit_terminal_home_continuation = MagicMock(return_value=True)
    _bind_terminal_context(app)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    previous_mode = AUTOMATION.mode
    try:
        AUTOMATION.mode = terminal_policy
        with (
            patch(
                "core.app.handle_tournament_results",
                return_value={"tournament_id": "Tournament20260718"},
            ) as tournament_results,
            patch(
                "core.app.dismiss_tournament_results_to_home",
                return_value=True,
            ) as dismiss_results,
            patch("core.app.handle_game_over") as normal_game_over,
            patch("core.app.log_action_intent") as action_log,
            patch("core.app.log_result") as result_log,
        ):
            app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)
            if terminal_policy is ExecMode.WAIT:
                app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)
        assert AUTOMATION.mode is terminal_policy
    finally:
        AUTOMATION.mode = previous_mode

    app._supervisor.persist_mode.assert_not_called()
    tournament_results.assert_called_once()
    assert tournament_results.call_args.args == (frame,)
    assert (
        tournament_results.call_args.kwargs["battle_context"]["run_configuration"][
            "profile"
        ]
        == "tournament"
    )
    normal_game_over.assert_not_called()
    app._build_terminal_home_continuation_claim.assert_called_once_with(
        source="tournament_results"
    )
    manager.on_game_over.assert_called_once_with()
    app._status_reporter.reset_coin_rate_samples.assert_called_once_with()
    action_log.assert_called_once()
    operation_id = action_log.call_args.kwargs["operation_id"]
    assert action_log.call_args.args == ("Capturing the finished Tournament",)
    if terminal_policy is ExecMode.WAIT:
        # Terminal records always include any current degradation, even when
        # WAIT deliberately retains the results screen without Home repair.
        manager.running_configuration_degradation.assert_called_once_with()
        manager.prepare_degraded_home_repair.assert_not_called()
        dismiss_results.assert_not_called()
        app._commit_terminal_home_continuation.assert_not_called()
        result_log.assert_called_once_with(
            "Tournament finished — result saved; Tournament Results remains "
            "visible under the explicit wait policy",
            detail=(
                "[TOURNAMENT_RESULTS] result=completed "
                "tournament_id=Tournament20260718 "
                "terminal_policy=WAIT screen=retained"
            ),
            operation_id=operation_id,
        )
    else:
        dismiss_results.assert_called_once()
        app._commit_terminal_home_continuation.assert_called_once_with(
            continuation
            if terminal_policy is ExecMode.NEXT_BATTLE
            else None
        )
        result_log.assert_called_once_with(
            "Tournament result saved and verified Home reached; the selected "
            "future battle policy remains separate",
            detail=(
                "[TOURNAMENT_RESULTS] result=completed "
                f"terminal_policy={terminal_policy.value} "
                "screen=home_new_battle"
            ),
            operation_id=operation_id,
        )


def test_observation_only_tournament_record_keeps_observed_config_and_degradation():
    degradation = {
        "schema_version": 1,
        "sources": ["attachment_applicability"],
        "reason": "selected farming Strategy does not match Tournament",
        "failed_checks": ["battle_kind"],
    }
    observed = {
        "schema_version": 1,
        "fields": {"cards_deck": {"status": "observed", "value": "Farm"}},
    }
    manager = MagicMock()
    manager.strategy = None
    manager.ctx = MissionContext(data={"mission_vars": {}})
    manager.running_configuration_degradation.return_value = degradation
    app = App.__new__(App)
    app._mission_mgr = manager
    app._supervisor = MagicMock()
    app._status_reporter = MagicMock()
    app._status_reporter.coin_rate_samples = []
    app._last_wave_value = 2028
    app._last_wave_conf = 99.0
    app._tournament_results_captured = False
    app._build_terminal_home_continuation_claim = MagicMock(return_value=None)
    app._no_strategy_observer = MagicMock()
    app._no_strategy_observer.snapshot.return_value = observed
    app._no_strategy_observation_active = True
    app._no_strategy_attachment_boundary_id = "attach-observer"
    app._no_strategy_inventory_complete = False
    app._no_strategy_inventory_retry_at = 0.0
    app._pending_no_strategy_record = None
    _bind_terminal_context(app)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    previous_mode = AUTOMATION.mode

    try:
        AUTOMATION.mode = ExecMode.WAIT
        with patch(
            "core.app.handle_tournament_results",
            return_value={"tournament_id": "TournamentObserved"},
        ) as tournament_results:
            app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)
    finally:
        AUTOMATION.mode = previous_mode

    context = tournament_results.call_args.kwargs["battle_context"]
    assert context["strategy"] is None
    assert context["observed_run_configuration"] == observed
    assert context["running_configuration_degradation"] == degradation
    app._no_strategy_observer.snapshot.assert_called_once_with(finalized=True)
    app._no_strategy_observer.reset.assert_called_once_with()


def test_degraded_tournament_continue_prepares_home_repair_before_dismissal():
    strategy = get_strategy("tournament")
    assert strategy is not None
    degradation = {
        "schema_version": 1,
        "sources": ["attachment_observation"],
        "reason": "damage_slider attached observation: observed_mismatch",
        "failed_checks": ["damage_slider"],
    }
    manager = MagicMock()
    manager.strategy = strategy
    manager.ctx = MissionContext(data={"mission_vars": {}})
    manager.running_configuration_degradation.return_value = degradation
    manager.prepare_degraded_home_repair.return_value = True
    app = App.__new__(App)
    app._mission_mgr = manager
    app._supervisor = MagicMock()
    app._status_reporter = MagicMock()
    app._status_reporter.coin_rate_samples = []
    app._last_wave_value = 2028
    app._last_wave_conf = 99.0
    app._tournament_results_captured = False
    app._apply_pending_strategy = MagicMock(return_value=False)
    continuation = {
        "schema_version": 1,
        "source": "tournament_results",
    }
    app._build_terminal_home_continuation_claim = MagicMock(
        return_value=continuation
    )
    app._commit_terminal_home_continuation = MagicMock(return_value=True)
    _bind_terminal_context(app)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    previous_mode = AUTOMATION.mode

    try:
        AUTOMATION.mode = ExecMode.NEXT_BATTLE
        with (
            patch(
                "core.app.handle_tournament_results",
                return_value={"tournament_id": "Tournament20260718"},
            ),
            patch(
                "core.app.dismiss_tournament_results_to_home",
                return_value=True,
            ),
        ):
            app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)
    finally:
        AUTOMATION.mode = previous_mode

    assert manager.running_configuration_degradation.call_count == 2
    manager.on_game_over.assert_called_once_with()
    app._apply_pending_strategy.assert_called_once_with()
    manager.prepare_degraded_home_repair.assert_called_once_with(degradation)
    app._commit_terminal_home_continuation.assert_called_once_with(continuation)


@pytest.mark.parametrize("terminal_policy", [ExecMode.NEXT_BATTLE, ExecMode.HOME])
def test_tournament_dismissal_failure_retries_without_changing_authority(
    terminal_policy,
):
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
    app._build_terminal_home_continuation_claim = MagicMock(
        return_value={
            "schema_version": 1,
            "source": "tournament_results",
        }
    )
    app._commit_terminal_home_continuation = MagicMock(return_value=True)
    _bind_terminal_context(app)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    previous_mode = AUTOMATION.mode
    try:
        AUTOMATION.mode = terminal_policy
        with (
            patch(
                "core.app.handle_tournament_results",
                return_value={"tournament_id": "Tournament20260718"},
            ),
            patch(
                "core.app.dismiss_tournament_results_to_home",
                return_value=False,
            ),
            patch("core.app.log_action_intent") as action_log,
            patch("core.app.log_result") as result_log,
        ):
            app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)
        assert AUTOMATION.mode is terminal_policy
    finally:
        AUTOMATION.mode = previous_mode

    operation_id = action_log.call_args.kwargs["operation_id"]
    app._supervisor.persist_state.assert_not_called()
    app._commit_terminal_home_continuation.assert_not_called()
    result_log.assert_called_once_with(
        "Tournament result was saved, but verified Home was not reached; "
        "the same terminal route will retry without changing Automation authority",
        detail=(
            "[TOURNAMENT_RESULTS] result=pending_retry "
            f"terminal_policy={terminal_policy.value} screen=retained "
            "action_authority=RUNNING retry=true"
        ),
        operation_id=operation_id,
    )


def test_changing_wait_to_next_after_tournament_end_does_not_create_launch():
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
    app._build_terminal_home_continuation_claim = MagicMock(return_value=None)
    app._commit_terminal_home_continuation = MagicMock(return_value=False)
    _bind_terminal_context(app)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    previous_mode = AUTOMATION.mode

    try:
        with (
            patch(
                "core.app.handle_tournament_results",
                return_value={"tournament_id": "Tournament20260718"},
            ),
            patch(
                "core.app.dismiss_tournament_results_to_home",
                return_value=True,
            ) as dismiss_results,
        ):
            AUTOMATION.mode = ExecMode.WAIT
            app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)
            AUTOMATION.mode = ExecMode.NEXT_BATTLE
            app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)
    finally:
        AUTOMATION.mode = previous_mode

    app._build_terminal_home_continuation_claim.assert_called_once_with(
        source="tournament_results"
    )
    assert dismiss_results.call_count == 1
    assert callable(dismiss_results.call_args.kwargs["action_guard_fn"])
    app._commit_terminal_home_continuation.assert_called_once_with(None)


@pytest.mark.parametrize(
    "terminal_policy",
    [ExecMode.NEXT_BATTLE, ExecMode.WAIT, ExecMode.HOME],
)
def test_tournament_result_capture_failure_preserves_policy_and_retries(
    terminal_policy,
):
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
    _bind_terminal_context(app)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    previous_mode = AUTOMATION.mode
    try:
        AUTOMATION.mode = terminal_policy
        with (
            patch(
                "core.app.handle_tournament_results", return_value=None
            ) as tournament_results,
            patch("core.app.log_action_intent") as action_log,
            patch("core.app.log_result") as result_log,
        ):
            app._handle_primary_states("TOURNAMENT_RESULTS", set(), frame)
        assert AUTOMATION.mode is terminal_policy
    finally:
        AUTOMATION.mode = previous_mode

    operation_id = action_log.call_args.kwargs["operation_id"]
    result_log.assert_called_once_with(
        "Tournament result capture failed — Tournament Results remains visible "
        f"(policy {terminal_policy.value} preserved) and capture will retry",
        detail=(
            "[TOURNAMENT_RESULTS] result=failed "
            f"terminal_policy={terminal_policy.value} screen=retained retry=true"
        ),
        operation_id=operation_id,
    )
    app._supervisor.persist_mode.assert_not_called()
    tournament_results.assert_called_once()
    manager.on_game_over.assert_not_called()
    app._status_reporter.reset_coin_rate_samples.assert_not_called()
    assert app._tournament_results_captured is False
