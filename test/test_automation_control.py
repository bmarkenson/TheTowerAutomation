import json
from datetime import datetime
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from automation.missions.manager import MissionManager
from core.automation_supervisor import AutomationSupervisor
from core.control_directives import (
    ControlDirectiveError,
    ControlDirectiveStore,
    VALID_MODES,
)
from core.gate_decisions import (
    build_gate_decision_options,
    prompt_for_gate_decision,
    startup_gate_context_for_strategy,
)
from core.control_surface import ControlSurfaceService
from core.run_state import AUTOMATION, AutomationControl, ExecMode
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


def _route_cli_to_live_service(monkeypatch, tmp_path) -> None:
    def service_for(path: str) -> ControlSurfaceService:
        control_path = Path(path)
        service = ControlSurfaceService(
            repository_root=tmp_path,
            control_file=control_path,
            action_log=control_path.parent / "actions.log",
            strategy_action_gate_file=(
                control_path.parent / "strategy_action_gate.json"
            ),
        )
        service._runtime_evidence = lambda: {
            "active": True,
            "instances": [
                {
                    "active": True,
                    "pid": os.getpid(),
                    "target": "localhost:5555",
                }
            ],
        }
        return service

    monkeypatch.setattr(
        "tools.automation_ctl._better_control_service",
        service_for,
    )


def test_next_battle_is_the_default_canonical_mode():
    control = AutomationControl()

    assert VALID_MODES == frozenset({"NEXT_BATTLE", "WAIT", "HOME"})
    assert control.mode is ExecMode.NEXT_BATTLE


def test_legacy_retry_mode_loads_and_rewrites_as_next_battle(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    control_file.write_text(
        json.dumps({"state": "RUNNING", "mode": "RETRY"}),
        encoding="utf-8",
    )
    store = ControlDirectiveStore(control_file)

    assert store.read()["mode"] == "NEXT_BATTLE"

    supervisor = _supervisor(control_file)
    supervisor.apply_control()
    assert AUTOMATION.mode is ExecMode.NEXT_BATTLE

    store.set_state("PAUSED", source="test")
    persisted = json.loads(control_file.read_text(encoding="utf-8"))
    assert persisted["mode"] == "NEXT_BATTLE"


def test_cli_sets_explicit_future_terminal_policy(tmp_path):
    control_file = tmp_path / "automation_ctl.json"

    assert automation_ctl_main(
        [
            "--control-file",
            str(control_file),
            "when-battle-ends",
            "continue",
        ]
    ) == 0
    assert ControlDirectiveStore(control_file).read()["mode"] == "NEXT_BATTLE"


def test_pause_remains_authoritative_until_explicit_enable(
    tmp_path, monkeypatch
):
    _route_cli_to_live_service(monkeypatch, tmp_path)
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
        ["--control-file", str(control_file), "enable"]
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
        and call.args[0].startswith(
            "[CTRL] State set to PAUSED via control file request_id="
        )
    ]
    assert len(acknowledgements) == 2


def test_repeated_mode_directive_is_acknowledged_by_request_identity(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    first = store.set_mode("WAIT", source="test")
    supervisor = _supervisor(control_file)

    with patch("core.automation_supervisor.log") as runtime_log:
        assert supervisor.apply_control()
        second = store.set_mode("WAIT", source="test-repeat")
        assert supervisor.apply_control()

    acknowledgements = [
        call.args[0]
        for call in runtime_log.call_args_list
        if call.args
        and call.args[0].startswith(
            "[CTRL] Mode set to WAIT via control file request_id="
        )
    ]
    assert acknowledgements == [
        "[CTRL] Mode set to WAIT via control file "
        f"request_id={first['mode_request_id']}",
        "[CTRL] Mode set to WAIT via control file "
        f"request_id={second['mode_request_id']}",
    ]


def test_runtime_publishes_exact_receipts_for_every_control_dimension(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    state = store.set_state("PAUSED", source="test")
    mode = store.set_mode("WAIT", source="test")
    speed = store.set_game_speed_target(4.5, source="test")
    adb = store.set_adb_port(5555, source="test")
    strategy = store.set_strategy("farm_t18", source="test")
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=lambda _port: True,
    )

    assert supervisor.apply_control()
    assert supervisor.acknowledge_strategy(
        "farm_t18",
        strategy["strategy_request_id"],
    )

    acknowledgements = supervisor.control_acknowledgements
    assert acknowledgements["schema_version"] == 1
    assert acknowledgements["state"] == {
        "value": "PAUSED",
        "request_id": state["state_request_id"],
        "acknowledged_at": acknowledgements["state"]["acknowledged_at"],
    }
    assert acknowledgements["mode"]["request_id"] == mode["mode_request_id"]
    assert acknowledgements["mode"]["value"] == "WAIT"
    assert acknowledgements["game_speed_target"]["request_id"] == (
        speed["game_speed_target_request_id"]
    )
    assert acknowledgements["game_speed_target"]["value"] == "x4.5"
    assert acknowledgements["adb_target"]["request_id"] == (
        adb["adb_port_request_id"]
    )
    assert acknowledgements["adb_target"]["value"] == "localhost:5555"
    assert acknowledgements["strategy"]["request_id"] == (
        strategy["strategy_request_id"]
    )
    assert acknowledgements["strategy"]["value"] == "farm_t18"

    replacement = store.set_state("PAUSED", source="replacement")
    assert supervisor.apply_control()
    assert supervisor.control_acknowledgements["state"]["request_id"] == (
        replacement["state_request_id"]
    )
    assert not supervisor.acknowledge_strategy(
        "farm_t18",
        "wrong-request-id",
    )
    assert supervisor.control_acknowledgements["strategy"]["request_id"] == (
        strategy["strategy_request_id"]
    )


def test_legacy_directives_gain_exact_ids_without_operator_refresh(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    control_file = tmp_path / "automation_ctl.json"
    original = {
        "state": "PAUSED",
        "mode": "WAIT",
        "game_speed_target": 4.5,
        "adb_port": 5555,
        "strategy": "farm_t18",
        "strategy_apply_mode": "next_boundary",
        "updated_at": "2026-08-10T12:00:00-07:00",
    }
    control_file.write_text(json.dumps(original), encoding="utf-8")
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=lambda _port: True,
    )

    migrated = ControlDirectiveStore(control_file).status()
    assert migrated["updated_at"] == original["updated_at"]
    for field in (
        "state_request_id",
        "mode_request_id",
        "game_speed_target_request_id",
        "adb_port_request_id",
        "strategy_request_id",
    ):
        assert migrated[field]

    assert supervisor.apply_control()
    assert supervisor.acknowledge_strategy(
        "farm_t18",
        migrated["strategy_request_id"],
    )
    acknowledgements = supervisor.control_acknowledgements
    assert acknowledgements["state"]["request_id"] == (
        migrated["state_request_id"]
    )
    assert acknowledgements["mode"]["request_id"] == (
        migrated["mode_request_id"]
    )
    assert acknowledgements["game_speed_target"]["request_id"] == (
        migrated["game_speed_target_request_id"]
    )
    assert acknowledgements["adb_target"]["request_id"] == (
        migrated["adb_port_request_id"]
    )
    assert acknowledgements["strategy"]["request_id"] == (
        migrated["strategy_request_id"]
    )


def test_implicit_control_defaults_gain_exact_runtime_receipts(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
    )

    migrated = ControlDirectiveStore(control_file).status()
    assert migrated["state"] == "RUNNING"
    assert migrated["mode"] == "NEXT_BATTLE"
    assert migrated["game_speed_target"] == 6.3
    assert migrated["state_request_id"]
    assert migrated["mode_request_id"]
    assert migrated["game_speed_target_request_id"]

    assert supervisor.apply_control()
    acknowledgements = supervisor.control_acknowledgements
    assert acknowledgements["state"]["request_id"] == (
        migrated["state_request_id"]
    )
    assert acknowledgements["mode"]["request_id"] == (
        migrated["mode_request_id"]
    )
    assert acknowledgements["game_speed_target"]["request_id"] == (
        migrated["game_speed_target_request_id"]
    )


def test_game_speed_target_is_persistent_and_applies_to_a_live_supervisor(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)

    assert store.status()["game_speed_target"] == 6.3
    assert automation_ctl_main(
        ["--control-file", str(control_file), "game-speed", "4.5"]
    ) == 0
    supervisor = _supervisor(control_file)
    assert supervisor.game_speed_target == 4.5

    with patch("core.automation_supervisor.log") as runtime_log:
        assert supervisor.apply_control()
        assert not supervisor.apply_control()
        assert automation_ctl_main(
            ["--control-file", str(control_file), "game-speed", "max"]
        ) == 0
        assert supervisor.apply_control()

    assert supervisor.game_speed_target == 6.3
    assert ControlDirectiveStore(control_file).status()["game_speed_target"] == 6.3
    assert any(
        call.args
        and call.args[0]
        == "[CTRL] Game speed target set to x6.3 via control file"
        for call in runtime_log.call_args_list
    )


def test_timed_pause_expiry_persists_resume_before_changing_memory(
    tmp_path, monkeypatch
):
    _route_cli_to_live_service(monkeypatch, tmp_path)
    control_file = tmp_path / "automation_ctl.json"
    supervisor = _supervisor(control_file)

    before = datetime.now().timestamp()
    assert automation_ctl_main(
        [
            "--control-file",
            str(control_file),
            "pause",
            "--minutes",
            "5",
        ]
    ) == 0
    after = datetime.now().timestamp()

    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "PAUSED"
    assert before + 300 <= saved["resume_at"] <= after + 300

    deadline = saved["resume_at"]
    with patch(
        "core.automation_supervisor.time.time", return_value=deadline - 1
    ):
        supervisor.apply_control()
    assert supervisor.is_paused

    with patch(
        "core.automation_supervisor.time.time", return_value=deadline + 1
    ):
        supervisor.apply_control()
    assert not supervisor.is_paused
    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "RUNNING"
    assert "resume_at" not in saved

    supervisor.apply_control()
    assert not supervisor.is_paused


def test_indefinite_pause_replaces_existing_timed_pause(tmp_path, monkeypatch):
    _route_cli_to_live_service(monkeypatch, tmp_path)
    control_file = tmp_path / "automation_ctl.json"

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
        json.dumps({"state": "RUNNING", "mode": "NEXT_BATTLE"}),
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
                "mode": "NEXT_BATTLE",
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


def test_attached_mismatch_can_offer_guarded_restart():
    options = build_gate_decision_options(
        "modules",
        allow_repair_restart=True,
    )

    restart = next(
        option for option in options
        if option["id"] == "restart_and_repair"
    )
    assert restart["action"] == "repair_restart"
    assert restart["label"] == "Surrender this battle and repair setup"
    assert "separate authority" in restart["description"]


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
