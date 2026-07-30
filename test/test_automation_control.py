import json
from pathlib import Path
from unittest.mock import patch

import pytest

from automation.missions.manager import MissionManager
from core.automation_supervisor import AutomationSupervisor
from core.control_directives import ControlDirectiveError, ControlDirectiveStore
from core.gate_decisions import (
    build_gate_decision_options,
    prompt_for_gate_decision,
    startup_gate_context_for_strategy,
)
from core.run_state import AUTOMATION
from tools.automation_ctl import main as automation_ctl_main


@pytest.fixture(autouse=True)
def restore_automation_state():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    try:
        yield
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode


def _supervisor(control_file: Path) -> AutomationSupervisor:
    return AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
    )


def test_pause_remains_authoritative_until_explicit_resume(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    supervisor = _supervisor(control_file)

    assert automation_ctl_main(
        ["--control-file", str(control_file), "pause"]
    ) == 0
    supervisor.apply_control()
    assert supervisor.is_paused

    with patch("core.automation_supervisor.time.time", return_value=10**12):
        supervisor.apply_control()
    assert supervisor.is_paused
    assert json.loads(control_file.read_text(encoding="utf-8"))["state"] == "PAUSED"

    assert automation_ctl_main(
        ["--control-file", str(control_file), "resume"]
    ) == 0
    supervisor.apply_control()
    assert not supervisor.is_paused
    assert AUTOMATION.state.value == "RUNNING"


def test_auto_return_pairs_intent_and_terminal_result(tmp_path):
    supervisor = AutomationSupervisor(
        control_file=str(tmp_path / "automation_ctl.json"),
        auto_return_secs=5,
    )
    supervisor._rtg_visible_since_ts = 0.0

    with (
        patch("core.automation_supervisor.time.time", return_value=6.0),
        patch("core.automation_supervisor.is_visible", return_value=True),
        patch(
            "core.automation_supervisor.tap_if_visible",
            return_value=True,
        ) as tap,
        patch(
            "core.automation_supervisor.log_action_intent",
        ) as action_log,
        patch("core.automation_supervisor.log_result") as result_log,
    ):
        supervisor.auto_return_check(object(), "HOME_SCREEN")

    tap.assert_called_once_with("buttons.return_to_game", retries=1)
    action_log.assert_called_once_with(
        "Returning to the active battle",
        reason="the Return to Game control remained visible for 6s",
        detail="[AUTO_RETURN] elapsed_s=6 threshold_s=5",
    )
    result_log.assert_called_once_with(
        "Automatic Return to Game complete — battle resumed",
        detail="[AUTO_RETURN] result=completed elapsed_s=6",
    )
    assert supervisor._rtg_visible_since_ts is None


def test_repeated_state_directive_is_acknowledged_and_requests_fresh_status(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("PAUSED", source="test")
    supervisor = _supervisor(control_file)

    with patch("core.automation_supervisor.log") as runtime_log:
        assert supervisor.apply_control()
        assert not supervisor.apply_control()
        store.set_state("PAUSED", source="attached-restart")
        assert supervisor.apply_control()

    acknowledgements = [
        call
        for call in runtime_log.call_args_list
        if call.args
        and call.args[0] == "[CTRL] State set to PAUSED via control file"
    ]
    assert len(acknowledgements) == 2


def test_game_speed_mode_is_persistent_and_applies_to_a_live_supervisor(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)

    assert store.status()["game_speed_mode"] == "AUTO"
    assert automation_ctl_main(
        ["--control-file", str(control_file), "game-speed", "reduced"]
    ) == 0
    supervisor = _supervisor(control_file)
    assert supervisor.game_speed_mode == "REDUCED"

    with patch("core.automation_supervisor.log") as runtime_log:
        assert supervisor.apply_control()
        assert not supervisor.apply_control()
        assert automation_ctl_main(
            ["--control-file", str(control_file), "game-speed", "auto"]
        ) == 0
        assert supervisor.apply_control()

    assert supervisor.game_speed_mode == "AUTO"
    assert ControlDirectiveStore(control_file).status()["game_speed_mode"] == "AUTO"
    assert any(
        call.args
        and call.args[0]
        == "[CTRL] Game speed mode set to AUTO via control file"
        for call in runtime_log.call_args_list
    )


def test_timed_pause_expiry_persists_resume_before_changing_memory(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    supervisor = _supervisor(control_file)

    with patch("tools.automation_ctl.time.time", return_value=1_000.0):
        assert automation_ctl_main(
            [
                "--control-file",
                str(control_file),
                "pause",
                "--minutes",
                "5",
            ]
        ) == 0

    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "PAUSED"
    assert saved["resume_at"] == 1_300.0

    with patch("core.automation_supervisor.time.time", return_value=1_299.0):
        supervisor.apply_control()
    assert supervisor.is_paused

    with patch("core.automation_supervisor.time.time", return_value=1_301.0):
        supervisor.apply_control()
    assert not supervisor.is_paused
    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "RUNNING"
    assert "resume_at" not in saved

    supervisor.apply_control()
    assert not supervisor.is_paused


def test_indefinite_pause_replaces_existing_timed_pause(tmp_path):
    control_file = tmp_path / "automation_ctl.json"

    with patch("tools.automation_ctl.time.time", return_value=1_000.0):
        assert automation_ctl_main(
            [
                "--control-file",
                str(control_file),
                "pause",
                "--minutes",
                "5",
            ]
        ) == 0
    assert automation_ctl_main(
        ["--control-file", str(control_file), "pause"]
    ) == 0

    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "PAUSED"
    assert "resume_at" not in saved


def test_timed_pause_stays_paused_when_persisted_resume_fails(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    control_file.write_text(
        json.dumps({"state": "PAUSED", "resume_at": 1_300.0}),
        encoding="utf-8",
    )
    supervisor = _supervisor(control_file)

    with (
        patch("core.automation_supervisor.time.time", return_value=1_301.0),
        patch.object(
            supervisor._control_store,
            "resume_expired_pause",
            side_effect=ControlDirectiveError("simulated persistence failure"),
        ),
    ):
        supervisor.apply_control()

    assert supervisor.is_paused
    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "PAUSED"
    assert saved["resume_at"] == 1_300.0


def test_default_runtime_configuration_has_no_global_pause_expiry_options():
    from core.app_setup import config_from_args, parse_args

    config = config_from_args(parse_args([]))

    assert not hasattr(config, "auto_resume_enabled")
    assert not hasattr(config, "auto_resume_secs")
    with pytest.raises(SystemExit):
        parse_args(["--auto-resume-minutes", "15"])


def test_runtime_owned_mode_transition_is_persisted_before_waiting(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    control_file.write_text(
        json.dumps({"state": "RUNNING", "mode": "RETRY"}),
        encoding="utf-8",
    )
    supervisor = _supervisor(control_file)
    supervisor.apply_control()

    assert supervisor.persist_mode("WAIT")

    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "RUNNING"
    assert saved["mode"] == "WAIT"
    assert saved["updated_at"]
    assert AUTOMATION.mode.value == "WAIT"


def test_paused_runtime_applies_adb_port_handoff(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    control_file.write_text(
        json.dumps(
            {
                "state": "PAUSED",
                "mode": "RETRY",
                "adb_port": 5565,
                "adb_port_updated_at": "2026-07-20T04:00:00-07:00",
            }
        ),
        encoding="utf-8",
    )
    handoffs = []
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=lambda port: handoffs.append(port) or True,
    )

    with patch("core.automation_supervisor.log") as runtime_log:
        supervisor.apply_control()
        supervisor.apply_control()

    assert handoffs == [5565]
    assert any(
        call.args
        and call.args[0]
        == "[CTRL] ADB target set to localhost:5565 via control file"
        for call in runtime_log.call_args_list
    )


def test_running_runtime_defers_adb_port_until_paused(tmp_path, monkeypatch):
    monkeypatch.delenv("ADB_DEVICE", raising=False)
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    store.set_adb_port(5565, source="test")
    handoffs = []
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=lambda port: handoffs.append(port) or True,
    )

    supervisor.apply_control()
    assert handoffs == []

    store.set_state("PAUSED", source="test")
    supervisor.apply_control()
    assert handoffs == [5565]


def test_running_runtime_acknowledges_already_selected_adb_target(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5565")
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    store.set_adb_port(5565, source="test")
    handoffs = []
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=lambda port: handoffs.append(port) or True,
    )

    with patch("core.automation_supervisor.log") as runtime_log:
        supervisor.apply_control()

    assert handoffs == []
    assert any(
        call.args
        and call.args[0]
        == "[CTRL] ADB target set to localhost:5565 via control file"
        for call in runtime_log.call_args_list
    )


def test_runtime_exposes_latest_valid_strategy_request(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_strategy("farm_t18", source="test")
    supervisor = _supervisor(control_file)

    first_request = supervisor.strategy_request
    assert first_request is not None
    assert first_request[0] == "farm_t18"
    assert first_request[2] == "next_boundary"

    store.set_strategy(
        "tournament",
        apply_mode="active_battle",
        source="test",
    )
    supervisor.apply_control()

    second_request = supervisor.strategy_request
    assert second_request is not None
    assert second_request[0] == "tournament"
    assert second_request[1] != first_request[1]
    assert second_request[2] == "active_battle"


def test_gate_decision_has_guarded_lifecycle(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)

    requested = store.publish_gate_decision(
        strategy="farm_t18",
        phase="home_setup",
        check_id="bots_preset",
        reason="Farm Bot preset requires 240 medals",
        expected="Farm",
        options=build_gate_decision_options("bots_preset"),
    )
    duplicate = store.publish_gate_decision(
        strategy="farm_t18",
        phase="home_setup",
        check_id="bots_preset",
        reason="Farm Bot preset requires 240 medals",
        expected="Farm",
        options=build_gate_decision_options("bots_preset"),
    )
    assert duplicate["request_id"] == requested["request_id"]
    assert duplicate["status"] == "pending"

    resolved = store.resolve_gate_decision(
        requested["request_id"],
        "bypass_once",
        source="test",
    )
    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["selected_option"]["action"] == "waive"
    assert store.resolve_gate_decision(
        requested["request_id"],
        "retry",
        source="test",
    ) is None

    consumed = store.consume_gate_decision(
        requested["request_id"],
        completion_reason="waiver applied",
    )
    assert consumed is not None
    assert consumed["status"] == "consumed"
    assert consumed["completion_reason"] == "waiver applied"


def test_advisory_gate_decision_persists_nonblocking_pause_choice(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    options = build_gate_decision_options(
        "ultimate_weapons",
        advisory=True,
    )

    requested = store.publish_gate_decision(
        strategy="tournament",
        phase="session_preflight",
        check_id="ultimate_weapons",
        reason="Tournament Ultimate Weapon mismatch",
        expected={"Golden Tower": {"primary": "on"}},
        options=options,
        blocking=False,
    )
    resolved = store.resolve_gate_decision(
        requested["request_id"],
        "pause_for_changes",
        source="test",
    )

    assert requested["blocking"] is False
    assert [option["id"] for option in requested["options"]] == [
        "pause_for_changes",
        "retry",
        "continue_observing",
    ]
    assert resolved is not None
    assert resolved["blocking"] is False
    assert resolved["selected_option"]["action"] == "pause"


def test_runtime_can_persist_advisory_pause(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    supervisor = _supervisor(control_file)

    assert supervisor.persist_state("PAUSED")

    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "PAUSED"
    assert saved["updated_by"] == "runtime"
    assert supervisor.is_paused


def test_terminal_gate_prompt_displays_issue_and_returns_shared_option_id():
    lines = []
    decision = {
        "check_id": "bots_preset",
        "reason": "Farm preset requires 240 medals",
        "expected": "Farm",
        "options": build_gate_decision_options(
            "bots_preset",
            [{"id": "flame", "label": "Continue with Flame", "value": "Flame"}],
        ),
    }

    selected = prompt_for_gate_decision(
        decision,
        input_fn=lambda _prompt: "1",
        output_fn=lines.append,
    )

    assert selected == "flame"
    assert "Check: bots_preset" in lines
    assert "Issue: Farm preset requires 240 medals" in lines
    assert any("Continue with Flame" in line for line in lines)


def test_gate_decision_cli_and_supervisor_share_persistent_resolution(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    published = store.publish_gate_decision(
        strategy="farm_t18",
        phase="home_setup",
        check_id="bots_preset",
        reason="Farm Bot preset requires 240 medals",
        expected="Farm",
        options=build_gate_decision_options(
            "bots_preset",
            [{"id": "flame", "label": "Continue with Flame", "value": "Flame"}],
        ),
    )

    assert automation_ctl_main(
        ["--control-file", str(control_file), "gate", "flame"]
    ) == 0
    supervisor = _supervisor(control_file)
    resolved = supervisor.gate_decision
    assert resolved is not None
    assert resolved["request_id"] == published["request_id"]
    assert resolved["status"] == "resolved"
    assert resolved["decision_id"] == "flame"

    restarted = _supervisor(control_file)
    assert restarted.gate_decision == resolved


def test_legacy_force_continue_alias_only_resolves_a_pending_scoped_bypass(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    with pytest.raises(SystemExit, match="No pending startup-gate decision"):
        automation_ctl_main(
            ["--control-file", str(control_file), "force-continue"]
        )

    store = ControlDirectiveStore(control_file)
    published = store.publish_gate_decision(
        strategy="farm_t18",
        phase="session_preflight",
        check_id="auto_pick_perks",
        reason="Auto Pick Perks is disabled",
        expected=True,
        options=build_gate_decision_options("auto_pick_perks"),
    )

    assert automation_ctl_main(
        ["--control-file", str(control_file), "force-continue"]
    ) == 0
    resolved = store.status()["gate_decision"]
    assert resolved["request_id"] == published["request_id"]
    assert resolved["check_id"] == "auto_pick_perks"
    assert resolved["decision_id"] == "bypass_once"
    assert resolved["selected_option"]["action"] == "waive"


def test_scoped_home_waiver_preserves_session_preflight():
    manager = MissionManager(None, None)
    waiver = {
        "request_id": "gate-1",
        "decision_id": "flame",
        "label": "Continue with Flame",
    }

    manager.mark_no_battle_setup_complete(
        {"cards_deck": "Farm", "bots_preset": {"status": "waived"}},
        waivers={"bots_preset": waiver},
    )
    mv = manager.ctx.data["mission_vars"]
    assert mv["gc_no_battle_setup_completed"]
    assert not mv.get("gc_session_preflight_completed", False)
    assert mv["gc_session_preflight_waivers"] == {"bots_preset": waiver}


def test_run_boundary_rearms_normal_gates_after_a_scoped_waiver():
    manager = MissionManager(None, None)
    mv = manager.ctx.data.setdefault("mission_vars", {})
    mv.update(
        gc_no_battle_setup_completed=True,
        gc_no_battle_setup_evidence={"bots_preset": {"status": "waived"}},
        gc_session_preflight_attempted=True,
        gc_session_preflight_completed=True,
        gc_session_preflight_blocked=False,
        gc_session_preflight_waivers={
            "bots_preset": {"decision_id": "flame"}
        },
    )

    manager.on_game_over()

    assert not mv["gc_no_battle_setup_completed"]
    assert not mv["gc_session_preflight_attempted"]
    assert not mv["gc_session_preflight_completed"]
    assert mv["gc_session_preflight_waivers"] == {}


def test_proactive_gate_waiver_is_strategy_scoped_and_claimed_once(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    staged = store.request_startup_gate_waiver(
        "bots_preset",
        strategy="farm_t18",
        source="test",
    )
    duplicate = store.request_startup_gate_waiver(
        "bots_preset",
        strategy="farm_t18",
        source="test",
    )
    assert duplicate["request_id"] == staged["request_id"]

    assert store.claim_startup_gate_waivers(
        ["bots_preset"],
        strategy="tournament",
    ) == {}
    claimed = store.claim_startup_gate_waivers(
        ["bots_preset", "auto_pick_perks"],
        strategy="farm_t18",
    )

    assert claimed["bots_preset"]["status"] == "claimed"
    assert claimed["bots_preset"]["request_id"] == staged["request_id"]
    assert store.status()["startup_gate_waivers"] == {}
    assert store.claim_startup_gate_waivers(
        ["bots_preset"],
        strategy="farm_t18",
    ) == {}


def test_configure_run_replaces_only_the_selected_strategy_waivers(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    first = store.configure_startup_gate_waivers(
        ["bots_preset", "auto_pick_perks"],
        strategy="farm_t18",
        source="test",
    )
    second = store.configure_startup_gate_waivers(
        ["bots_preset"],
        strategy="farm_t18",
        source="test",
    )

    assert set(second) == {"bots_preset"}
    assert second["bots_preset"]["request_id"] == first["bots_preset"]["request_id"]
    assert set(store.status()["startup_gate_waivers"]) == {"bots_preset"}

    store.set_strategy("none", source="test")
    assert store.status()["startup_gate_waivers"] == {}


def test_cli_can_stage_and_restore_a_strategy_aware_run_skip(tmp_path):
    control_file = tmp_path / "automation_ctl.json"

    assert automation_ctl_main(
        [
            "--control-file",
            str(control_file),
            "configure-run",
            "skip",
            "bots_preset",
        ]
    ) == 0
    staged = ControlDirectiveStore(control_file).status()["startup_gate_waivers"]
    assert staged["bots_preset"]["strategy"] == "farm_t18"

    assert automation_ctl_main(
        [
            "--control-file",
            str(control_file),
            "configure-run",
            "default",
            "bots_preset",
        ]
    ) == 0
    assert ControlDirectiveStore(control_file).status()["startup_gate_waivers"] == {}


def test_cli_configure_run_prompt_dynamically_toggles_a_check(tmp_path):
    control_file = tmp_path / "automation_ctl.json"

    with patch("builtins.input", side_effect=["5", ""]):
        assert automation_ctl_main(
            ["--control-file", str(control_file), "configure-run"]
        ) == 0

    staged = ControlDirectiveStore(control_file).status()["startup_gate_waivers"]
    assert set(staged) == {"bots_preset"}


def test_configure_run_catalog_contains_only_checks_enforced_by_strategy():
    farm = startup_gate_context_for_strategy("farm_t18")
    tournament = startup_gate_context_for_strategy("tournament")

    assert "free_upgrade_locks" in {check["id"] for check in farm["checks"]}
    assert "auto_pick_perks" in {check["id"] for check in farm["checks"]}
    assert "free_upgrade_locks" not in {
        check["id"] for check in tournament["checks"]
    }
    assert "auto_pick_perks" not in {
        check["id"] for check in tournament["checks"]
    }
