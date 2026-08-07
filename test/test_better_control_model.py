from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from automation.missions.manager import MissionManager
from core.action_authority import (
    AuthorityHold,
    RuntimeActionAuthority,
    RuntimeActionAuthorityPublisher,
    RuntimeActionClass,
)
from core.app import App
from core.automation_supervisor import AutomationSupervisor
from core.control_directives import ControlDirectiveStore
from core.control_model import (
    intent_matches_evidence,
    observed_game_state,
    validate_workflow_evidence,
)
from core.control_surface import ControlSurfaceRequestError, ControlSurfaceService
from core.run_state import AUTOMATION
from tools import automation_ctl


@pytest.fixture(autouse=True)
def _restore_automation_control():
    prior_state = AUTOMATION.state
    prior_mode = AUTOMATION.mode
    try:
        yield
    finally:
        AUTOMATION.state = prior_state
        AUTOMATION.mode = prior_mode


def _timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _evidence(
    *,
    game_state: str = "home_new_battle",
    observation_id: str = "runtime-1:1",
    runtime_id: str = "runtime-1",
    scope: str = "scope-1",
) -> dict[str, object]:
    primary_state = {
        "home_new_battle": "HOME_SCREEN",
        "home_resume_battle": "HOME_SCREEN",
        "active_battle": "RUNNING",
        "game_over": "GAME_OVER",
        "tournament_results": "TOURNAMENT_RESULTS",
    }.get(game_state, "UNKNOWN")
    home_control = {
        "home_new_battle": "NEW_BATTLE",
        "home_resume_battle": "RESUME_BATTLE",
    }.get(game_state, "UNKNOWN")
    return {
        "schema_version": 1,
        "runtime_id": runtime_id,
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "observation_id": observation_id,
        "observed_at": _timestamp(),
        "primary_state": primary_state,
        "home_battle_control": home_control,
        "game_state": game_state,
        "active_battle": game_state
        in {"home_resume_battle", "active_battle"},
        "activity_scope_run_id": scope,
        "target_generation": 7,
    }


def _publish_runtime_observation(
    service: ControlSurfaceService,
    evidence: dict[str, object],
    *,
    published_at: float | None = None,
    paused: bool = True,
    active_battle_adopted: bool = False,
) -> None:
    owner = {
        "runtime_id": evidence["runtime_id"],
        "pid": evidence["pid"],
        "adb_target": evidence["adb_target"],
    }
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=paused,
        active_battle=bool(evidence["active_battle"]),
        battle_scope=evidence["activity_scope_run_id"],
        primary_state=str(evidence["primary_state"]),
    )
    publisher = RuntimeActionAuthorityPublisher(
        service.strategy_action_gate_path,
        owner=owner,
    )
    publisher.publish(
        authority.snapshot(),
        owner=owner,
        now=published_at,
        control_model={
            "schema_version": 1,
            "observation": {
                key: value
                for key, value in evidence.items()
                if key not in {"runtime_id", "pid", "adb_target"}
            },
            "battle_lifecycle": {
                "awaiting_initial_intent": not active_battle_adopted,
                "active_battle_adopted": active_battle_adopted,
            },
        },
    )
    service._runtime_evidence = lambda: {
        "active": True,
        "instances": [
            {
                "active": True,
                "pid": evidence["pid"],
                "target": evidence["adb_target"],
            }
        ],
    }


def test_observed_game_dimensions_do_not_infer_a_workflow():
    assert (
        observed_game_state(
            "HOME_SCREEN", "NEW_BATTLE", active_battle=False
        )
        == "home_new_battle"
    )
    assert (
        observed_game_state(
            "HOME_SCREEN", "RESUME_BATTLE", active_battle=True
        )
        == "home_resume_battle"
    )
    assert observed_game_state("RUNNING", "UNKNOWN", active_battle=True) == (
        "active_battle"
    )
    assert intent_matches_evidence("start_battle", _evidence())
    assert not intent_matches_evidence("attach_battle", _evidence())


def test_workflow_evidence_requires_exact_aware_owner_and_observation():
    evidence = _evidence()
    assert validate_workflow_evidence(evidence) == evidence

    naive = {**evidence, "observed_at": "2026-08-07T12:00:00"}
    assert validate_workflow_evidence(naive) is None
    unknown_target = {**evidence, "adb_target": "unknown"}
    assert validate_workflow_evidence(unknown_target) is None
    mismatched_classification = {**evidence, "game_state": "active_battle"}
    assert validate_workflow_evidence(mismatched_classification) is None


def test_directive_store_rejects_mismatched_intent_and_serializes_workflows(
    tmp_path,
):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    evidence = _evidence()

    with pytest.raises(ValueError, match="does not match"):
        store.request_battle_workflow(
            "attach_battle",
            evidence=evidence,
        )

    first = store.request_battle_workflow(
        "start_battle",
        evidence=evidence,
        source="test",
    )
    with pytest.raises(ValueError, match="already in progress"):
        store.request_battle_workflow(
            "start_battle",
            evidence=evidence,
        )

    acknowledged = store.transition_battle_workflow(
        first["request_id"],
        "awaiting_enable",
        reason="Pause still owns input",
    )
    assert acknowledged is not None
    assert acknowledged["status"] == "awaiting_enable"
    assert store.transition_battle_workflow(
        "not-current", "interrupted"
    ) is None
    with pytest.raises(ValueError, match="cannot transition"):
        store.transition_battle_workflow(first["request_id"], "completed")


def test_take_manual_control_atomically_requests_indefinite_pause(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    store.set_state("RUNNING", source="test")
    workflow = store.request_battle_workflow(
        "start_battle",
        evidence=_evidence(),
        source="test",
    )

    manual = store.request_manual_control(
        evidence=_evidence(observation_id="runtime-1:2"),
        source="test",
    )
    current = store.status()

    assert manual["status"] == "pause_requested"
    assert current["state"] == "PAUSED"
    assert current["resume_at"] is None
    assert current["battle_workflow"]["request_id"] == workflow["request_id"]
    assert current["battle_workflow"]["status"] == "interrupted"

    active = store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=_evidence(observation_id="runtime-1:3"),
    )
    assert active is not None
    returned = store.request_return_control(
        manual["manual_control_id"],
        evidence=_evidence(observation_id="runtime-1:4"),
    )
    assert returned["status"] == "return_requested"
    assert store.status()["state"] == "PAUSED"


def test_control_surface_exposes_separate_dimensions_and_exact_actions(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence()
    _publish_runtime_observation(service, evidence)

    status = service.status()
    model = status["control_model"]
    assert model["process"]["state"] == "live"
    assert model["action_authority"]["effective"] == "paused"
    assert model["observation"]["game_state"] == "home_new_battle"
    assert model["strategy_scope"]["startup_default"]
    assert model["when_battle_ends"]["compatibility_value"] == "NEXT_BATTLE"
    assert model["actions"]["start_battle"]["available"] is True
    assert model["actions"]["attach_battle"]["available"] is False

    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.apply_control({"action": "attach_battle"})
    assert exc_info.value.code == "intent_mismatch"

    response = service.apply_control({"action": "start_battle"})
    assert response["request"]["disposition"] == "requested"
    assert response["control_model"]["battle_workflow"]["intent"] == (
        "start_battle"
    )
    with pytest.raises(ControlSurfaceRequestError) as busy:
        service.apply_control({"action": "start_battle"})
    assert busy.value.code == "workflow_busy"


@pytest.mark.parametrize(
    ("game_state", "start_available", "attach_available"),
    [
        ("home_new_battle", True, False),
        ("home_resume_battle", False, True),
        ("active_battle", False, True),
        ("game_over", False, False),
        ("tournament_results", False, False),
        ("unknown", False, False),
    ],
)
@pytest.mark.parametrize("paused", [True, False])
def test_server_intent_availability_matrix_is_state_exact(
    tmp_path,
    game_state,
    start_available,
    attach_available,
    paused,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state=game_state)
    _publish_runtime_observation(service, evidence, paused=paused)

    model = service.status()["control_model"]

    assert model["process"]["state"] == "live"
    assert model["action_authority"]["effective"] == (
        "paused" if paused else "pending"
    )
    assert model["observation"]["game_state"] == game_state
    assert model["actions"]["start_battle"]["available"] is start_available
    assert model["actions"]["attach_battle"]["available"] is attach_available


def test_stopped_process_rejects_action_authority_and_battle_workflows(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    service._runtime_evidence = lambda: {"active": False, "instances": []}

    model = service.status()["control_model"]

    assert model["process"]["state"] == "stopped"
    for action in ("pause", "enable", "start_battle", "attach_battle"):
        assert model["actions"][action]["available"] is False
        assert model["actions"][action]["code"] == "process_stopped"
        with pytest.raises(ControlSurfaceRequestError) as stopped:
            service.apply_control({"action": action})
        assert stopped.value.code == "process_stopped"


def test_adopted_active_battle_makes_redundant_attach_unavailable(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    _publish_runtime_observation(
        service,
        _evidence(game_state="active_battle"),
        active_battle_adopted=True,
    )

    attach = service.status()["control_model"]["actions"]["attach_battle"]

    assert attach["available"] is False
    assert attach["code"] == "battle_already_adopted"


def test_strategy_scope_keeps_active_and_pending_values_separate(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    service.control_store.set_strategy("farm_t19", source="test")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    service.action_log.write_text(
        "[INFO "
        + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        + "] [CTRL] Strategy set to farm_t18 via control file\n",
        encoding="utf-8",
    )
    _publish_runtime_observation(
        service,
        _evidence(game_state="active_battle"),
    )

    scope = service.status()["control_model"]["strategy_scope"]

    assert scope["startup_default"] == "farm_t19"
    assert scope["active_battle"] == "farm_t18"
    assert scope["pending_next_boundary"] == "farm_t19"


def test_start_battle_atomically_rearms_normal_tournament_gates(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    selected = service.control_store.set_strategy(
        "tournament",
        source="test",
    )
    old_request_id = selected["exclusive_validation"]["current_request_id"]
    old_strategy_request_id = selected["strategy_request_id"]
    service.control_store.finish_exclusive_validation(
        old_request_id,
        outcome="cancelled",
        reason="completed prior request",
        allowed_statuses=("pending",),
    )
    _publish_runtime_observation(service, _evidence())

    response = service.apply_control({"action": "start_battle"})

    control = response["control"]
    ledger = control["exclusive_validation"]
    new_request_id = ledger["current_request_id"]
    assert response["control_model"]["battle_workflow"]["status"] == (
        "requested"
    )
    assert control["strategy_request_id"] != old_strategy_request_id
    assert new_request_id != old_request_id
    assert ledger["receipts"][new_request_id]["status"] == "pending"
    assert ledger["receipts"][new_request_id]["strategy_request_id"] == (
        control["strategy_request_id"]
    )


def test_control_surface_rejects_stale_runtime_observation(tmp_path):
    service = ControlSurfaceService(
        repository_root=tmp_path,
        stale_after_seconds=1,
    )
    _publish_runtime_observation(
        service,
        _evidence(),
        published_at=1.0,
    )

    model = service.status()["control_model"]
    assert model["observation"]["freshness"] == "stale"
    assert model["actions"]["start_battle"] == {
        "available": False,
        "code": "fresh_observation_unavailable",
        "reason": "fresh runtime-owned game observation is required",
    }
    with pytest.raises(ControlSurfaceRequestError) as stale:
        service.apply_control({"action": "start_battle"})
    assert stale.value.code == "fresh_observation_unavailable"


def test_fresh_authority_heartbeat_cannot_refresh_an_old_game_observation(
    tmp_path,
):
    now = datetime.now(timezone.utc).timestamp()
    service = ControlSurfaceService(
        repository_root=tmp_path,
        stale_after_seconds=1,
    )
    evidence = _evidence()
    evidence["observed_at"] = datetime.fromtimestamp(
        now - 10,
        tz=timezone.utc,
    ).astimezone().isoformat(timespec="seconds")
    _publish_runtime_observation(service, evidence, published_at=now)

    model = service.status(now=now)["control_model"]

    assert model["observation"]["freshness"] == "stale"
    assert model["observation"]["age_seconds"] >= 9
    assert "authority heartbeat is current" in model["observation"]["reason"]
    assert model["workflow_evidence"] is None
    assert model["actions"]["start_battle"]["code"] == (
        "fresh_observation_unavailable"
    )


def test_control_surface_take_and_return_control_require_pause_ack(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="active_battle")
    _publish_runtime_observation(service, evidence)

    taken = service.apply_control({"action": "take_manual_control"})
    manual = taken["control_model"]["manual_control"]
    assert manual["status"] == "pause_requested"
    repeated = service.apply_control({"action": "take_manual_control"})
    assert repeated["request"]["disposition"] == "no_op"
    with pytest.raises(ControlSurfaceRequestError) as unacknowledged:
        service.apply_control({"action": "return_control"})
    assert unacknowledged.value.code == "manual_control_not_acknowledged"

    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    returned = service.apply_control({"action": "return_control"})
    assert returned["control_model"]["manual_control"]["status"] == (
        "return_requested"
    )
    assert returned["control"]["state"] == "PAUSED"


def test_operator_startup_waits_for_matching_intent_and_enable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("PAUSED", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(
        None,
        None,
        await_initial_battle_intent=True,
    )
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(runtime_id=str(owner["runtime_id"]))
    evidence["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=evidence)
    supervisor.apply_control()

    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "awaiting_enable"
    assert manager.awaiting_initial_battle_intent() is True

    store.set_state("RUNNING", source="test")
    supervisor.apply_control()
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "acknowledged"
    assert manager.awaiting_initial_battle_intent() is False
    assert manager.maybe_run_start(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        }
    ) is False
    assert manager.maybe_run_start({"state": "RUNNING"}) is True


def test_start_dispatch_survives_preflight_scope_change_and_completes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH", str(tmp_path / "actions.log")
    )
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    requested = _evidence(runtime_id=str(owner["runtime_id"]))
    requested["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=requested)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in requested.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "acknowledged"
    workflow_hold = app._operator_workflow_authority_hold()
    assert workflow_hold is not None
    assert workflow_hold.hold is AuthorityHold.OPERATOR_WORKFLOW
    app._update_action_authority(holds=(workflow_hold,))
    assert app._action_decision(
        RuntimeActionClass.STRATEGY_ACTION
    ).allowed is False
    assert app._action_decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=AuthorityHold.OPERATOR_WORKFLOW,
    ).allowed is True

    preflight = _evidence(
        observation_id="runtime-1:2",
        runtime_id=str(owner["runtime_id"]),
        scope="scope-preflight",
    )
    preflight["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in preflight.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "acknowledged"
    assert manager.maybe_run_start(
        {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
    ) is False
    assert app._mark_operator_battle_action_dispatched(True) is True
    assert supervisor.battle_workflow["status"] == "action_dispatched"
    assert app._operator_workflow_authority_hold() is not None

    running = _evidence(
        game_state="active_battle",
        observation_id="runtime-1:3",
        runtime_id=str(owner["runtime_id"]),
        scope="scope-preflight",
    )
    running["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in running.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert supervisor.battle_workflow["status"] == "action_dispatched"
    battle_started = manager.maybe_run_start({"state": "RUNNING"})
    assert battle_started is True
    assert app._complete_started_battle_workflow(battle_started) is True
    assert supervisor.battle_workflow["status"] == "completed"


@pytest.mark.parametrize("changed_state", ["home_resume_battle", "game_over"])
def test_dispatched_start_interrupts_on_a_definitive_wrong_boundary(
    tmp_path,
    monkeypatch,
    changed_state,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    requested = _evidence(runtime_id=str(owner["runtime_id"]))
    requested["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=requested)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in requested.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert app._mark_operator_battle_action_dispatched(True) is True

    changed = _evidence(
        game_state=changed_state,
        observation_id="runtime-1:changed-after-dispatch",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows(
        {"state": str(changed["primary_state"])}
    )

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True


def test_dispatched_start_fails_closed_after_bounded_home_timeout(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(runtime_id=str(owner["runtime_id"]))
    evidence["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=evidence)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert app._mark_operator_battle_action_dispatched(True) is True
    dispatched_at = datetime.fromisoformat(
        str(supervisor.battle_workflow["updated_at"])
    )
    app._control_observation = {
        **app._control_observation,
        "observation_id": "runtime-1:timeout",
        "observed_at": (
            dispatched_at + timedelta(seconds=21)
        ).isoformat(timespec="seconds"),
    }

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    assert supervisor.battle_workflow["status"] == "failed"
    assert "within 20 seconds" in supervisor.battle_workflow["reason"]


def test_dispatched_resumable_attach_completes_after_same_battle_adoption(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_resume_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(
        "attach_battle",
        evidence=evidence,
    )
    for status in ("acknowledged", "validating_save", "ready"):
        store.transition_battle_workflow(
            workflow["request_id"],
            status,
            acknowledgement=evidence,
            **(
                {"save_receipt": {"schema_version": 1, "status": "ready"}}
                if status == "ready"
                else {}
            ),
        )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert app._mark_operator_battle_action_dispatched(True) is True

    active = _evidence(
        game_state="active_battle",
        observation_id="runtime-1:active",
        runtime_id=str(owner["runtime_id"]),
        scope=str(evidence["activity_scope_run_id"]),
    )
    active["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in active.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert manager.maybe_run_start({"state": "RUNNING"}) is False

    assert app._complete_ready_attachment_after_adoption() is True
    assert supervisor.battle_workflow["status"] == "completed"


def test_return_control_stays_input_blocked_during_reconciliation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert supervisor.manual_control["status"] == "active"
    store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    supervisor.apply_control()
    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert supervisor.manual_control["status"] == "awaiting_enable"

    store.enable_after_return_control(
        manual["manual_control_id"],
        source="test",
    )
    supervisor.apply_control()
    app._sync_operator_control_workflows({"state": "RUNNING"})

    assert supervisor.manual_control["status"] == "reconciling"
    hold = app._operator_workflow_authority_hold()
    assert hold is not None
    assert hold.hold.value == "manual_control_return"


def test_attach_stays_pending_before_battle_adoption(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("PAUSED", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    store.request_battle_workflow("attach_battle", evidence=evidence)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert supervisor.battle_workflow["status"] == "awaiting_enable"
    store.set_state("RUNNING", source="test")
    supervisor.apply_control()
    app._sync_operator_control_workflows({"state": "RUNNING"})

    assert supervisor.battle_workflow["status"] == "validating_save"
    assert manager.awaiting_initial_battle_intent() is True
    assert manager.maybe_run_start({"state": "RUNNING"}) is False
    hold = app._operator_workflow_authority_hold()
    assert hold is not None
    assert hold.hold.value == "operator_workflow"


def test_validated_attach_completes_only_after_lifecycle_adoption(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(
        "attach_battle",
        evidence=evidence,
    )
    store.transition_battle_workflow(
        workflow["request_id"],
        "acknowledged",
        acknowledgement=evidence,
    )
    store.transition_battle_workflow(
        workflow["request_id"],
        "validating_save",
        acknowledgement=evidence,
    )
    store.transition_battle_workflow(
        workflow["request_id"],
        "ready",
        save_receipt={"schema_version": 1, "status": "ready"},
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert manager.awaiting_initial_battle_intent() is False
    assert supervisor.battle_workflow["status"] == "ready"
    assert app._complete_ready_attachment_after_adoption() is False

    assert manager.maybe_run_start({"state": "RUNNING"}) is False
    assert app._complete_ready_attachment_after_adoption() is True
    assert supervisor.battle_workflow["status"] == "completed"


@pytest.mark.parametrize("changed_state", ["home_new_battle", "game_over"])
def test_ready_attach_interrupts_if_boundary_changes_before_adoption(
    tmp_path,
    monkeypatch,
    changed_state,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_resume_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(
        "attach_battle", evidence=evidence
    )
    for status in ("acknowledged", "validating_save", "ready"):
        store.transition_battle_workflow(
            workflow["request_id"],
            status,
            acknowledgement=evidence,
        )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert manager.awaiting_initial_battle_intent() is False

    changed = _evidence(
        game_state=changed_state,
        observation_id="runtime-1:changed",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows(
        {"state": str(changed["primary_state"])}
    )

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True


def test_runtime_rejects_changed_boundary_before_first_acknowledgement(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("PAUSED", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    requested = _evidence(runtime_id=str(owner["runtime_id"]))
    requested["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=requested)
    supervisor.apply_control()
    changed = _evidence(
        game_state="home_resume_battle",
        observation_id="runtime-1:2",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    assert supervisor.battle_workflow["status"] == "rejected"
    assert manager.awaiting_initial_battle_intent() is True


def test_runtime_interrupts_and_revokes_start_if_boundary_changes_after_ack(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    requested = _evidence(runtime_id=str(owner["runtime_id"]))
    requested["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=requested)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in requested.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "acknowledged"
    assert manager.awaiting_initial_battle_intent() is False

    changed = _evidence(
        game_state="home_resume_battle",
        observation_id="runtime-1:2",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True


@pytest.mark.parametrize(
    ("intent", "game_state", "ready"),
    [
        ("start_battle", "home_new_battle", False),
        ("attach_battle", "home_resume_battle", True),
    ],
)
def test_manual_control_interrupt_revokes_unadopted_battle_authorization(
    tmp_path,
    monkeypatch,
    intent,
    game_state,
    ready,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH", str(tmp_path / "actions.log")
    )
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state=game_state,
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(intent, evidence=evidence)
    if ready:
        for status in ("acknowledged", "validating_save", "ready"):
            store.transition_battle_workflow(
                workflow["request_id"],
                status,
                acknowledgement=evidence,
            )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows(
        {"state": str(evidence["primary_state"])}
    )
    assert manager.awaiting_initial_battle_intent() is False

    store.request_manual_control(evidence=evidence, source="test")
    supervisor.apply_control()
    app._sync_operator_control_workflows(
        {"state": str(evidence["primary_state"])}
    )

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True
    assert app._operator_workflow_authority_hold() is not None


def test_battle_intent_authorization_can_change_after_a_natural_boundary(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH", str(tmp_path / "actions.log")
    )
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()

    assert manager.authorize_initial_battle_intent("attach_battle") is True
    assert manager.maybe_run_start({"state": "RUNNING"}) is False
    assert manager.active_battle_observed() is True
    manager.maybe_run_start({"state": "GAME_OVER"})
    assert manager.active_battle_observed() is False

    assert manager.authorize_initial_battle_intent("start_battle") is True
    manager.maybe_run_start(
        {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
    )
    assert manager.maybe_run_start({"state": "RUNNING"}) is True


def test_operator_workflow_is_interrupted_when_boundary_changes_before_enable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("PAUSED", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(runtime_id=str(owner["runtime_id"]))
    evidence["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=evidence)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    changed = _evidence(
        game_state="home_resume_battle",
        observation_id="runtime-1:2",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True


def test_unexpected_manual_activity_yields_with_an_indefinite_pause(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    current = _evidence(
        game_state="home_resume_battle",
        observation_id="manual-runtime:2",
        runtime_id=str(owner["runtime_id"]),
    )
    current["pid"] = owner["pid"]

    app = App.__new__(App)
    app._supervisor = supervisor
    app._prior_control_observation = {
        **{
            key: value
            for key, value in current.items()
            if key not in {"runtime_id", "pid", "adb_target"}
        },
        "observation_id": "manual-runtime:1",
        "primary_state": "RUNNING",
        "home_battle_control": "UNKNOWN",
        "game_state": "active_battle",
        "active_battle": True,
    }
    app._control_observation = {
        key: value
        for key, value in current.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    assert app._yield_on_unexpected_manual_activity() is True
    status = store.status()
    assert status["state"] == "PAUSED"
    assert status["resume_at"] is None
    assert status["manual_control"]["reason"] == "unexpected_manual_activity"
    assert status["manual_control"]["status"] == "pause_requested"


def test_malformed_manual_control_fails_closed_without_overwrite(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    service = ControlSurfaceService(repository_root=tmp_path)
    raw_manual = {"schema_version": 999, "status": "active"}
    service.control_store.replace(
        {
            "state": "RUNNING",
            "mode": "NEXT_BATTLE",
            "manual_control": raw_manual,
        }
    )
    _publish_runtime_observation(
        service,
        _evidence(),
        paused=False,
    )

    model = service.status()["control_model"]
    for action in ("start_battle", "enable"):
        assert model["actions"][action]["available"] is False
        assert model["actions"][action]["code"] == "manual_control_invalid"
        with pytest.raises(ControlSurfaceRequestError) as invalid:
            service.apply_control({"action": action})
        assert invalid.value.code == "manual_control_invalid"
    assert service.control_store.read()["manual_control"] == raw_manual

    supervisor = AutomationSupervisor(
        control_file=str(service.control_store.path)
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)
    hold = app._operator_workflow_authority_hold()
    assert hold is not None
    assert hold.hold.value == "manual_control_return"


def test_malformed_whole_control_file_disables_every_model_action(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    _publish_runtime_observation(service, _evidence())
    service.control_store.path.parent.mkdir(parents=True, exist_ok=True)
    service.control_store.path.write_text("{malformed", encoding="utf-8")

    status = service.status()

    assert status["control"]["error"]
    for action, availability in status["control_model"]["actions"].items():
        assert availability["available"] is False, action
        assert availability["code"] == "control_invalid", action
    for action in ("start_battle", "attach_battle", "pause", "enable"):
        with pytest.raises(ControlSurfaceRequestError) as invalid:
            service.apply_control({"action": action})
        assert invalid.value.code == "control_invalid"


def test_manual_control_cannot_cross_a_runtime_owner_boundary(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    first_supervisor = AutomationSupervisor(control_file=str(path))
    first_owner = first_supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(first_owner["runtime_id"]),
    )
    evidence["pid"] = first_owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )

    replacement_supervisor = AutomationSupervisor(control_file=str(path))
    replacement_supervisor.apply_control()
    replacement_owner = (
        replacement_supervisor.current_exclusive_validation_owner()
    )
    replacement_evidence = _evidence(
        game_state="active_battle",
        observation_id="replacement-runtime:1",
        runtime_id=str(replacement_owner["runtime_id"]),
    )
    replacement_evidence["pid"] = replacement_owner["pid"]
    app = App.__new__(App)
    app._supervisor = replacement_supervisor
    app._mission_mgr = MissionManager(None, None)
    app._control_observation = {
        key: value
        for key, value in replacement_evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "RUNNING"})

    assert replacement_supervisor.manual_control["status"] == "interrupted"
    assert replacement_supervisor.is_paused is True
    assert "runtime evidence changed" in (
        replacement_supervisor.manual_control["detail"]
    )


def test_manual_handoff_hold_survives_concurrent_running_directive(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    evidence = _evidence()
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    store.set_state("RUNNING", source="concurrent-legacy-writer")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)

    hold = app._operator_workflow_authority_hold()

    assert supervisor.is_paused is False
    assert hold is not None
    assert hold.hold.value == "manual_control_return"


def test_repeated_return_enable_is_pending_and_keeps_request_identity(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="active_battle")
    _publish_runtime_observation(service, evidence)
    manual = service.control_store.request_manual_control(
        evidence=evidence,
        source="test",
    )
    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    service.control_store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )

    first = service.apply_control({"action": "enable"})
    request_id = first["control"]["state_request_id"]
    updated_at = first["control_model"]["manual_control"]["updated_at"]
    second = service.apply_control({"action": "enable"})

    assert first["request"]["disposition"] == "requested"
    assert second["request"]["disposition"] == "pending"
    assert second["control"]["state_request_id"] == request_id
    assert second["control_model"]["manual_control"]["updated_at"] == updated_at


def test_same_value_state_ack_requires_the_exact_request_identity(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    first = service.control_store.set_state("PAUSED", source="first")
    second = service.control_store.set_state("PAUSED", source="second")
    timestamp = datetime.fromisoformat(
        str(second["state_updated_at"])
    ).strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    service.action_log.write_text(
        f"[INFO {timestamp}] [CTRL] State set to PAUSED via control file "
        f"request_id={first['state_request_id']}\n",
        encoding="utf-8",
    )

    stale_ack = service.status()["acknowledgements"]["state"]
    assert stale_ack["request_id"] == first["state_request_id"]
    assert stale_ack["acknowledges_current"] is False

    with service.action_log.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[INFO {timestamp}] [CTRL] State set to PAUSED via control file "
            f"request_id={second['state_request_id']}\n"
        )
    current_ack = service.status()["acknowledgements"]["state"]
    assert current_ack["request_id"] == second["state_request_id"]
    assert current_ack["acknowledges_current"] is True


def test_same_value_terminal_policy_ack_requires_exact_request_identity(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    first = service.control_store.set_mode("WAIT", source="first")
    second = service.control_store.set_mode("WAIT", source="second")
    timestamp = datetime.fromisoformat(
        str(second["mode_updated_at"])
    ).strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    service.action_log.write_text(
        f"[INFO {timestamp}] [CTRL] Mode set to WAIT via control file "
        f"request_id={first['mode_request_id']}\n",
        encoding="utf-8",
    )

    stale_ack = service.status()["acknowledgements"]["mode"]
    assert stale_ack["request_id"] == first["mode_request_id"]
    assert stale_ack["acknowledges_current"] is False

    with service.action_log.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[INFO {timestamp}] [CTRL] Mode set to WAIT via control file "
            f"request_id={second['mode_request_id']}\n"
        )
    current_ack = service.status()["acknowledgements"]["mode"]
    assert current_ack["request_id"] == second["mode_request_id"]
    assert current_ack["acknowledges_current"] is True


def test_terminal_policy_requests_have_stable_pending_and_noop_identity(
    tmp_path,
):
    stopped = ControlSurfaceService(repository_root=tmp_path / "stopped")
    first = stopped.apply_control(
        {"action": "terminal_policy", "policy": "continue_automatically"}
    )
    first_id = first["control"]["mode_request_id"]
    repeated = stopped.apply_control(
        {"action": "terminal_policy", "policy": "continue_automatically"}
    )
    assert first["request"]["disposition"] == "requested"
    assert first["control"]["exists"] is True
    assert repeated["request"]["disposition"] == "no_op"
    assert repeated["control"]["mode_request_id"] == first_id

    live = ControlSurfaceService(repository_root=tmp_path / "live")
    selected = live.control_store.set_mode("WAIT", source="test")
    _publish_runtime_observation(live, _evidence())
    pending = live.apply_control(
        {"action": "terminal_policy", "policy": "wait"}
    )
    assert pending["request"]["disposition"] == "pending"
    assert pending["control"]["mode_request_id"] == (
        selected["mode_request_id"]
    )


def test_return_control_is_unavailable_if_indefinite_pause_was_superseded(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="active_battle")
    manual = service.control_store.request_manual_control(
        evidence=evidence,
        source="test",
    )
    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    service.control_store.set_state("RUNNING", source="concurrent-writer")
    _publish_runtime_observation(service, evidence, paused=False)

    availability = service.status()["control_model"]["actions"][
        "return_control"
    ]

    assert availability["available"] is False
    assert availability["code"] == "pause_not_acknowledged"


def test_tournament_results_preserve_unexecutable_policy_as_pending(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    service.control_store.set_mode("HOME", source="test")
    _publish_runtime_observation(
        service,
        _evidence(game_state="tournament_results"),
    )

    policy = service.status()["control_model"]["when_battle_ends"]

    assert policy["compatibility_value"] == "HOME"
    assert policy["status"] == "pending_verified_terminal_dismissal"


def test_cli_does_not_expose_raw_authority_aliases(tmp_path, capsys):
    control_path = tmp_path / "logs" / "automation_ctl.json"

    assert automation_ctl.main(
        ["resume", "--control-file", str(control_path)]
    ) == 2
    assert automation_ctl.main(
        ["set", "state", "RUNNING", "--control-file", str(control_path)]
    ) == 2
    assert not control_path.exists()
    capsys.readouterr()


def test_cli_uses_same_exact_intent_validation(tmp_path, monkeypatch, capsys):
    control_path = tmp_path / "logs" / "automation_ctl.json"
    service = ControlSurfaceService(
        repository_root=tmp_path,
        control_file=control_path,
    )
    _publish_runtime_observation(service, _evidence())
    monkeypatch.setattr(
        automation_ctl,
        "_better_control_service",
        lambda _path: service,
    )

    assert automation_ctl.main(
        ["start-battle", "--control-file", str(control_path)]
    ) == 0
    assert service.control_store.status()["battle_workflow"]["intent"] == (
        "start_battle"
    )
    assert "runtime status=requested" in capsys.readouterr().out


def test_cli_labels_terminal_policy_as_future_behavior(tmp_path, capsys):
    control_path = tmp_path / "logs" / "automation_ctl.json"

    assert automation_ctl.main(
        ["when-battle-ends", "wait", "--control-file", str(control_path)]
    ) == 0
    assert ControlDirectiveStore(control_path).status()["mode"] == "WAIT"
    assert "terminal_policy" in capsys.readouterr().out
