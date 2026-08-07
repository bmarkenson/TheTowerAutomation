from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from core.action_authority import (
    AuthorityHold,
    AuthorityHoldState,
    AuxiliaryCollector,
    RuntimeActionAuthority,
    RuntimeActionAuthorityPublisher,
    RuntimeActionClass,
)
from core.app import App
from core.adb_connection import AdbConnectionCoordinator
from core.automation_supervisor import AutomationSupervisor
from core.battle_lifecycle import HomeBattleControl
from core.control_directives import ControlDirectiveStore
from core.run_state import AUTOMATION
from core.watchdog import _watchdog_process_check_once
import handlers.ad_gem_handler as ad_gems


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )


@pytest.fixture(autouse=True)
def _restore_runtime_state():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    try:
        AUTOMATION.state = "RUNNING"
        yield
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode


def _runtime_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    runtime_override: dict[str, object] | None = None,
    battle_active: bool = True,
    screen_state: str = "RUNNING",
    scope: str = "run-1",
    now: float = 1_000.0,
) -> tuple[App, AutomationSupervisor, ControlDirectiveStore, dict[str, object]]:
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    control_path = tmp_path / "logs" / "automation_ctl.json"
    store = ControlDirectiveStore(control_path)
    store.replace({"state": "RUNNING", "mode": "WAIT", "custom": "keep"})
    supervisor = AutomationSupervisor(
        control_file=str(control_path),
        auto_return_enabled=False,
    )
    supervisor.apply_control()
    runtime = supervisor.current_exclusive_validation_owner()
    if runtime_override:
        runtime = {**runtime, **runtime_override}
    lease = store.request_interactive_development_lease(
        owner_label="worker interactive lease",
        runtime=runtime,
        starting_evidence={
            "screen_state": screen_state,
            "battle_active": battle_active,
            "battle_scope": scope,
            "observed_at": _timestamp(now),
        },
        now=now,
    )
    supervisor.apply_control()

    app = App.__new__(App)
    app._supervisor = supervisor
    app._action_authority = RuntimeActionAuthority()
    app._action_authority_publisher = RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner=supervisor.current_exclusive_validation_owner(),
    )
    app._authority_battle_active = battle_active
    app._authority_primary_state = screen_state
    app._authority_holds = ()
    app._external_development_hold_active = False
    app._interactive_development_ack = None
    app._current_run_scope_id = lambda: scope
    return app, supervisor, store, lease


def _activate(
    app: App,
    *,
    now: float = 1_001.0,
    detection: dict[str, object] | None = None,
) -> None:
    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_control_boundary(now=now)
        app._sync_interactive_development_observation(
            detection or {"state": "RUNNING"},
            now=now + 1,
        )
    assert app._interactive_development_ack["state"] == "active"


def test_control_directive_lease_lifecycle_preserves_unrelated_fields(tmp_path):
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.replace({"state": "RUNNING", "mode": "WAIT", "custom": "keep"})
    runtime = {
        "runtime_id": "runtime-1",
        "pid": 4321,
        "adb_target": "localhost:5555",
    }
    evidence = {
        "screen_state": "RUNNING",
        "battle_active": True,
        "battle_scope": "run-1",
        "observed_at": _timestamp(1_000.0),
    }

    lease = store.request_interactive_development_lease(
        owner_label="worker one",
        runtime=runtime,
        starting_evidence=evidence,
        now=1_000.0,
    )
    with pytest.raises(ValueError, match="busy"):
        store.request_interactive_development_lease(
            owner_label="worker two",
            runtime=runtime,
            starting_evidence=evidence,
            now=1_001.0,
        )
    with pytest.raises(ValueError, match="does not match"):
        store.heartbeat_interactive_development_lease("0" * 32, now=1_002.0)
    with pytest.raises(ValueError, match="does not match"):
        store.release_interactive_development_lease("0" * 32, now=1_002.0)

    heartbeat = store.heartbeat_interactive_development_lease(
        lease["lease_id"],
        now=1_005.0,
    )
    assert heartbeat["heartbeat_at"] == _timestamp(1_005.0)
    assert heartbeat["expires_at"] == _timestamp(1_035.0)
    released = store.release_interactive_development_lease(
        lease["lease_id"],
        now=1_006.0,
    )
    assert released["request_state"] == "release_requested"
    with pytest.raises(ValueError, match="no longer accepts"):
        store.heartbeat_interactive_development_lease(
            lease["lease_id"],
            now=1_007.0,
        )
    terminal = store.finish_interactive_development_lease(
        lease["lease_id"],
        disposition="released",
        reason="fresh post-release observation",
        now=1_008.0,
    )
    assert terminal["request_state"] == "terminal"
    assert terminal["terminal_disposition"] == "released"
    saved = store.read()
    assert saved["state"] == "RUNNING"
    assert saved["mode"] == "WAIT"
    assert saved["custom"] == "keep"


def test_hold_precedes_ack_and_waits_for_background_input_quiescence(
    tmp_path,
    monkeypatch,
):
    app, _supervisor, _store, lease = _runtime_app(tmp_path, monkeypatch)
    snapshot_path = tmp_path / "logs" / "strategy_action_gate.json"

    with patch("core.app.stop_blind_gem_tapper", return_value=True) as stop:
        app._sync_interactive_development_control_boundary(now=1_001.0)

    pending = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert pending["interactive_development_lease"]["state"] == "pending"
    assert pending["interactive_development_lease"].get("acknowledged_at") is None
    assert pending["holds"] == [
        {
            "hold": "external_development",
            "reason": "interactive development owns the cooperative input window",
        }
    ]
    assert pending["observation_authority"]["allowed"] is True
    assert pending["auxiliary_collection_authority"]["allowed"] is False
    assert pending["strategy_action_authority"]["allowed"] is False
    assert pending["lifecycle_action_authority"]["allowed"] is False
    stop.assert_called()

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=True),
        patch("core.app.is_blind_gem_tapper_active", return_value=True),
    ):
        app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=1_002.0,
        )
    assert app._interactive_development_ack["state"] == "pending"

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=1_003.0,
        )
    active = json.loads(snapshot_path.read_text(encoding="utf-8"))
    acknowledgement = active["interactive_development_lease"]
    assert acknowledgement["lease_id"] == lease["lease_id"]
    assert acknowledgement["state"] == "active"
    assert acknowledgement["acknowledged_at"] == _timestamp(1_003.0)
    assert acknowledgement["starting_evidence"] == {
        "screen_state": "RUNNING",
        "battle_active": True,
        "battle_scope": "run-1",
        "observed_at": _timestamp(1_003.0),
    }
    assert active["holds"][0]["hold"] == "external_development"


def test_hold_install_waits_for_an_inflight_watchdog_mutation(
    tmp_path,
    monkeypatch,
):
    app, _supervisor, _store, _lease = _runtime_app(tmp_path, monkeypatch)
    app._update_action_authority()
    guard = app._get_watchdog_mutation_guard()
    connection = AdbConnectionCoordinator(is_connected=lambda _target: True)
    mutation_started = threading.Event()
    mutation_release = threading.Event()
    mutation_finished = threading.Event()
    boundary_started = threading.Event()
    boundary_finished = threading.Event()

    def restart_game():
        mutation_started.set()
        assert mutation_release.wait(2)
        mutation_finished.set()

    def install_hold():
        boundary_started.set()
        app._sync_interactive_development_control_boundary(now=1_001.0)
        boundary_finished.set()

    watchdog_thread = threading.Thread(
        target=_watchdog_process_check_once,
        args=(connection, guard),
        daemon=True,
    )
    boundary_thread = threading.Thread(target=install_hold, daemon=True)
    with (
        patch("core.watchdog.time.sleep"),
        patch("core.watchdog._pid_running", return_value=False),
        patch("core.watchdog.is_game_foregrounded", return_value=True),
        patch("core.watchdog.restart_game", side_effect=restart_game),
    ):
        watchdog_thread.start()
        try:
            assert mutation_started.wait(1)

            boundary_thread.start()
            assert boundary_started.wait(1)
            assert not boundary_finished.wait(0.05)
            assert not app._external_development_hold_active
        finally:
            mutation_release.set()
            watchdog_thread.join(timeout=2)
            if boundary_thread.ident is not None:
                boundary_thread.join(timeout=2)

    assert not watchdog_thread.is_alive()
    assert not boundary_thread.is_alive()
    assert mutation_finished.is_set()
    assert boundary_finished.is_set()
    assert app._external_development_hold_active
    assert app._interactive_development_ack["state"] == "pending"

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=1_002.0,
        )
    assert mutation_finished.is_set()
    assert app._interactive_development_ack["state"] == "active"


@pytest.mark.parametrize(
    ("pid_running", "foregrounded"),
    (
        (False, True),
        (True, False),
    ),
)
def test_external_hold_blocks_watchdog_mutation_but_not_observation(
    tmp_path,
    monkeypatch,
    pid_running,
    foregrounded,
):
    app, _supervisor, _store, _lease = _runtime_app(tmp_path, monkeypatch)
    app._sync_interactive_development_control_boundary(now=1_001.0)
    assert app._external_development_hold_active
    connection = AdbConnectionCoordinator(is_connected=lambda _target: True)

    with (
        patch("core.watchdog.time.sleep"),
        patch(
            "core.watchdog._pid_running",
            return_value=pid_running,
        ) as process_observation,
        patch(
            "core.watchdog.is_game_foregrounded",
            return_value=foregrounded,
        ) as foreground_observation,
        patch("core.watchdog.restart_game") as restart,
        patch("core.watchdog.bring_to_foreground") as foreground,
    ):
        _watchdog_process_check_once(
            connection,
            app._get_watchdog_mutation_guard(),
        )

    process_observation.assert_called_once_with("com.TechTreeGames.TheTower")
    foreground_observation.assert_called_once_with()
    restart.assert_not_called()
    foreground.assert_not_called()


def test_inflight_blind_gem_tap_prevents_active_acknowledgement(
    tmp_path,
    monkeypatch,
):
    app, _supervisor, _store, _lease = _runtime_app(tmp_path, monkeypatch)
    tap_started = threading.Event()
    tap_release = threading.Event()
    tap_finished = threading.Event()

    def blocking_tap(*_args, **_kwargs):
        tap_started.set()
        assert tap_release.wait(2)
        tap_finished.set()
        return True

    try:
        with (
            patch.object(ad_gems, "get_click", return_value=(250, 1200)),
            patch.object(ad_gems, "tap_now", side_effect=blocking_tap) as tap_now,
            patch.object(ad_gems, "log_action_intent"),
            patch.object(ad_gems, "log_result"),
        ):
            ad_gems.start_blind_gem_tapper(
                duration=30,
                interval=30,
                blocking=False,
                action_guard_fn=lambda: True,
            )
            assert tap_started.wait(1)

            app._sync_interactive_development_control_boundary(now=1_001.0)
            app._sync_interactive_development_observation(
                {"state": "RUNNING"},
                now=1_002.0,
            )
            assert app._interactive_development_ack["state"] == "pending"
            assert ad_gems.is_blind_gem_tapper_active()
            assert not tap_finished.is_set()

            tap_release.set()
            assert tap_finished.wait(1)
            for _attempt in range(100):
                if not ad_gems.is_blind_gem_tapper_active():
                    break
                threading.Event().wait(0.01)
            assert not ad_gems.is_blind_gem_tapper_active()

            app._sync_interactive_development_observation(
                {"state": "RUNNING"},
                now=1_003.0,
            )
            assert app._interactive_development_ack["state"] == "active"
            assert tap_finished.is_set()
            assert tap_now.call_count == 1
            threading.Event().wait(0.05)
            assert tap_now.call_count == 1
    finally:
        tap_release.set()
        ad_gems.stop_blind_gem_tapper()


def test_external_hold_blocks_every_runtime_input_owner_but_keeps_observation(
    tmp_path,
    monkeypatch,
):
    app, _supervisor, _store, _lease = _runtime_app(tmp_path, monkeypatch)
    _activate(app)
    authority = app._get_action_authority()

    app._update_action_authority(
        holds=(
            AuthorityHoldState(
                AuthorityHold.RUN_INITIALIZATION,
                "initialization also remains pending",
            ),
        )
    )
    assert authority.decision(RuntimeActionClass.OBSERVATION).allowed
    for collector in AuxiliaryCollector:
        assert not authority.decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=collector,
            owner=AuthorityHold.EXTERNAL_DEVELOPMENT.value,
        ).allowed
    for action_class in (
        RuntimeActionClass.STRATEGY_ACTION,
        RuntimeActionClass.LIFECYCLE_ACTION,
    ):
        for owner in AuthorityHold:
            assert not authority.decision(
                action_class,
                owner=owner.value,
            ).allowed
    assert AUTOMATION.state.value == "RUNNING"

    blocked_app = App.__new__(App)
    blocked_app._external_development_hold_active = True
    assert not blocked_app._maybe_start_exclusive_validation(
        home_control=HomeBattleControl.NEW_BATTLE
    )
    assert not blocked_app._advance_exclusive_validation({"state": "RUNNING"})
    assert not blocked_app._advance_exclusive_validation_launch(
        object(),
        {"state": "HOME_SCREEN"},
        battle_started=False,
    )


@pytest.mark.parametrize("operator_state", ("PAUSED", "STOPPED"))
def test_operator_pause_or_stop_revokes_lease_and_resume_does_not_reactivate(
    tmp_path,
    monkeypatch,
    operator_state,
):
    app, supervisor, store, lease = _runtime_app(tmp_path, monkeypatch)
    _activate(app)

    store.set_state(operator_state, source="test")
    supervisor.apply_control()
    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        app._sync_interactive_development_control_boundary(now=1_005.0)

    terminal = store.status()["interactive_development_lease"]
    assert terminal["lease_id"] == lease["lease_id"]
    assert terminal["request_state"] == "terminal"
    assert terminal["terminal_disposition"] == "revoked"
    assert operator_state in terminal["terminal_reason"]
    assert not app._external_development_hold_active

    store.set_state("RUNNING", source="test")
    supervisor.apply_control()
    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        app._sync_interactive_development_control_boundary(now=1_006.0)
    assert app._interactive_development_ack["state"] == "terminal"
    assert not app._external_development_hold_active


def test_pause_terminates_locally_if_terminal_persistence_fails(
    tmp_path,
    monkeypatch,
):
    app, supervisor, store, lease = _runtime_app(tmp_path, monkeypatch)
    _activate(app)
    store.set_state("PAUSED", source="test")
    supervisor.apply_control()

    with (
        patch.object(
            supervisor,
            "finish_interactive_development_lease",
            return_value=None,
        ),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
    ):
        app._sync_interactive_development_control_boundary(now=1_005.0)

    assert app._interactive_development_ack["lease_id"] == lease["lease_id"]
    assert app._interactive_development_ack["state"] == "terminal"
    assert app._interactive_development_ack["terminal_disposition"] == "revoked"
    assert not app._external_development_hold_active

    store.set_state("RUNNING", source="test")
    supervisor.apply_control()
    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        app._sync_interactive_development_control_boundary(now=1_006.0)
    assert app._interactive_development_ack["state"] == "terminal"
    assert not app._external_development_hold_active


def test_heartbeat_expiry_waits_for_fresh_observation_then_restores_production(
    tmp_path,
    monkeypatch,
):
    app, supervisor, store, _lease = _runtime_app(tmp_path, monkeypatch)
    _activate(app)

    supervisor.apply_control()
    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        app._sync_interactive_development_control_boundary(now=1_031.0)
    assert app._interactive_development_ack["state"] == "expiry_pending"
    assert app._external_development_hold_active

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=1_032.0,
        )
    terminal = store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == "expired"
    assert not app._external_development_hold_active
    assert app._get_action_authority().decision(
        RuntimeActionClass.STRATEGY_ACTION
    ).allowed


@pytest.mark.parametrize(
    ("runtime_override", "reason_fragment"),
    (
        ({"runtime_id": "prior-runtime"}, "runtime/session"),
        ({"pid": 999_999}, "PID"),
        ({"adb_target": "localhost:5565"}, "ADB target"),
    ),
)
def test_runtime_pid_session_or_target_mismatch_terminates_request(
    tmp_path,
    monkeypatch,
    runtime_override,
    reason_fragment,
):
    app, _supervisor, store, _lease = _runtime_app(
        tmp_path,
        monkeypatch,
        runtime_override=runtime_override,
    )

    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        app._sync_interactive_development_control_boundary(now=1_001.0)

    terminal = store.status()["interactive_development_lease"]
    assert terminal["request_state"] == "terminal"
    assert terminal["terminal_disposition"] == "abnormal"
    assert reason_fragment in terminal["terminal_reason"]
    assert not app._external_development_hold_active


def test_replacement_runtime_preserves_already_released_terminal_lease(
    tmp_path,
    monkeypatch,
):
    app, supervisor, store, lease = _runtime_app(
        tmp_path,
        monkeypatch,
        runtime_override={"runtime_id": "prior-runtime"},
    )
    store.finish_interactive_development_lease(
        str(lease["lease_id"]),
        disposition="released",
        reason="fresh post-release observation confirmed",
        now=1_000.5,
    )
    supervisor.apply_control()

    with (
        patch.object(
            app,
            "_terminate_interactive_development_lease",
            wraps=app._terminate_interactive_development_lease,
        ) as terminate,
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.log") as lifecycle_log,
        patch("core.app.log_result") as lifecycle_result,
    ):
        app._sync_interactive_development_control_boundary(now=1_001.0)

    terminal = store.status()["interactive_development_lease"]
    assert terminal["request_state"] == "terminal"
    assert terminal["terminal_disposition"] == "released"
    assert app._interactive_development_ack["state"] == "terminal"
    assert app._interactive_development_ack["terminal_disposition"] == "released"
    terminate.assert_not_called()
    lifecycle_log.assert_not_called()
    lifecycle_result.assert_not_called()


def test_live_target_change_revokes_an_active_lease(tmp_path, monkeypatch):
    app, _supervisor, store, _lease = _runtime_app(tmp_path, monkeypatch)
    _activate(app)

    monkeypatch.setenv("ADB_DEVICE", "localhost:5565")
    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        app._sync_interactive_development_control_boundary(now=1_004.0)

    terminal = store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == "abnormal"
    assert "ADB target changed" in terminal["terminal_reason"]


def test_natural_game_over_ends_lease_before_normal_terminal_authority(
    tmp_path,
    monkeypatch,
):
    app, _supervisor, store, _lease = _runtime_app(tmp_path, monkeypatch)
    _activate(app)

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_observation(
            {"state": "GAME_OVER"},
            now=1_004.0,
        )

    terminal = store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == "natural_game_over"
    assert not app._external_development_hold_active
    assert app._get_action_authority().decision(
        RuntimeActionClass.LIFECYCLE_ACTION
    ).allowed


def test_battle_scope_change_terminates_without_cross_battle_continuation(
    tmp_path,
    monkeypatch,
):
    app, _supervisor, store, _lease = _runtime_app(tmp_path, monkeypatch)
    _activate(app)
    app._current_run_scope_id = lambda: "run-2"

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_observation(
            {"state": "STORE"},
            now=1_004.0,
        )

    terminal = store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == "battle_boundary"
    assert "identity changed" in terminal["terminal_reason"]


def test_home_lease_ends_when_a_running_battle_boundary_appears(
    tmp_path,
    monkeypatch,
):
    app, _supervisor, store, _lease = _runtime_app(
        tmp_path,
        monkeypatch,
        battle_active=False,
        screen_state="HOME_SCREEN",
    )
    _activate(
        app,
        detection={
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        },
    )

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=1_004.0,
        )

    terminal = store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == "battle_boundary"
    assert "running-battle boundary changed" in terminal["terminal_reason"]


def test_release_stays_held_through_ambiguous_and_then_fresh_observation(
    tmp_path,
    monkeypatch,
):
    app, supervisor, store, lease = _runtime_app(tmp_path, monkeypatch)
    _activate(app)
    store.release_interactive_development_lease(
        lease["lease_id"],
        now=1_004.0,
    )
    supervisor.apply_control()

    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        app._sync_interactive_development_control_boundary(now=1_004.0)
    assert app._interactive_development_ack["state"] == "release_pending"
    assert app._external_development_hold_active

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_observation(
            {"state": "UNKNOWN"},
            now=1_005.0,
        )
    assert app._interactive_development_ack["state"] == "release_blocked"
    assert app._external_development_hold_active
    assert store.status()["interactive_development_lease"][
        "request_state"
    ] == "release_requested"

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=1_006.0,
        )
    terminal = store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == "released"
    assert app._interactive_development_ack["terminal_evidence"][
        "observed_at"
    ] == _timestamp(1_006.0)
    assert not app._external_development_hold_active


def test_transition_logging_is_concise_and_heartbeats_are_silent(
    tmp_path,
    monkeypatch,
):
    app, supervisor, store, lease = _runtime_app(tmp_path, monkeypatch)
    _activate(app)
    for now in (1_003.0, 1_004.0, 1_005.0):
        store.heartbeat_interactive_development_lease(
            lease["lease_id"],
            now=now,
        )
        supervisor.apply_control()
        with patch("core.app.stop_blind_gem_tapper", return_value=False):
            app._sync_interactive_development_control_boundary(now=now)
    store.release_interactive_development_lease(
        lease["lease_id"],
        now=1_006.0,
    )
    supervisor.apply_control()
    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=1_007.0,
        )

    log_text = (tmp_path / "logs" / "actions.log").read_text(encoding="utf-8")
    assert log_text.count("Lease request observed") == 1
    assert log_text.count("Production acknowledged lease") == 1
    assert log_text.count("Lease activated") == 1
    assert log_text.count("Release request observed") == 1
    assert log_text.count("Interactive development lease ended") == 1
    assert "heartbeat" not in log_text.lower()
