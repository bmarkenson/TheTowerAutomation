import json
from datetime import datetime
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from automation.missions.manager import MissionManager
from core.automation_supervisor import AutomationSupervisor
from core.adb_utils import adb_shell, screencap_png, screencap_raw
from core.app import App
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
from core.runtime_failure_policy import RuntimeFailureKind
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


def _emulator_location(*, port: int = 5555) -> dict[str, object]:
    return {
        "schema_version": 1,
        "host_id": "13f12ca2-13af-41fc-a8bf-f4fb2fd6e686",
        "host_name": "WORKSTATION-B",
        "linux_adb_port": port,
        "bluestacks_listener": {
            "adb_port": 5565,
            "process_id": 4242,
            "process_started_at": "2026-08-15T20:00:00+00:00",
            "executable_path": (
                r"C:\Program Files\BlueStacks_nxt\HD-Player.exe"
            ),
            "instance_name": "Nougat32",
        },
    }


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


def test_pause_terminal_save_refresh_policy_defaults_allowed_and_strict_persists(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)

    default_pause = store.set_state("PAUSED", source="test-default")
    default_policy = store.status()["pause_terminal_save_refresh"]
    assert default_policy == {
        "schema_version": 1,
        "state_request_id": default_pause["state_request_id"],
        "allowed": True,
        "attempts": [],
    }

    store.set_state("RUNNING", source="test-enable")
    strict_pause = store.set_state(
        "PAUSED",
        allow_terminal_save_refresh=False,
        source="test-strict",
    )
    restarted_store = ControlDirectiveStore(control_file)
    strict_policy = restarted_store.status()["pause_terminal_save_refresh"]
    assert strict_policy["state_request_id"] == strict_pause["state_request_id"]
    assert strict_policy["allowed"] is False

    internally_reasserted = restarted_store.set_paused_unless_stopped(
        source="test-safety-repause"
    )
    preserved = restarted_store.status()["pause_terminal_save_refresh"]
    assert preserved["state_request_id"] == internally_reasserted[
        "state_request_id"
    ]
    assert preserved["allowed"] is False

    restarted_store.set_state("RUNNING", source="test-enable-again")
    assert restarted_store.status()["pause_terminal_save_refresh"] is None
    next_pause = restarted_store.set_state("PAUSED", source="test-next-pause")
    next_policy = restarted_store.status()["pause_terminal_save_refresh"]
    assert next_policy["state_request_id"] == next_pause["state_request_id"]
    assert next_policy["allowed"] is True


def test_cli_can_request_strict_pause(tmp_path, monkeypatch):
    _route_cli_to_live_service(monkeypatch, tmp_path)
    control_file = tmp_path / "automation_ctl.json"

    assert automation_ctl_main(
        [
            "--control-file",
            str(control_file),
            "pause",
            "--strict-pause",
        ]
    ) == 0

    policy = ControlDirectiveStore(control_file).status()[
        "pause_terminal_save_refresh"
    ]
    assert policy["allowed"] is False


def test_paused_terminal_save_refresh_claim_is_once_per_durable_boundary(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    paused = store.set_state("PAUSED", source="test")
    boundary = "a" * 64

    claim = store.claim_paused_terminal_save_refresh(
        state_request_id=paused["state_request_id"],
        terminal_state="TOURNAMENT_RESULTS",
        boundary_fingerprint=boundary,
        now=100.0,
    )

    assert claim is not None
    assert claim["status"] == "claimed"
    assert (
        ControlDirectiveStore(control_file).claim_paused_terminal_save_refresh(
            state_request_id=paused["state_request_id"],
            terminal_state="TOURNAMENT_RESULTS",
            boundary_fingerprint=boundary,
            now=101.0,
        )
        is None
    )

    restarted = _supervisor(control_file)
    restarted.apply_control()
    assert restarted.paused_terminal_save_refresh_claim_current(claim)
    assert restarted.complete_paused_terminal_save_refresh(
        claim["claim_id"],
        status="complete",
        reason="serialized and restored",
    )
    assert not restarted.paused_terminal_save_refresh_claim_current(claim)
    attempt = restarted.pause_terminal_save_refresh["attempts"][0]
    assert attempt["boundary_fingerprint"] == boundary
    assert attempt["status"] == "complete"
    assert attempt["reason"] == "serialized and restored"


def test_final_adb_boundary_consumes_pause_before_dispatch(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    supervisor = _supervisor(control_file)
    supervisor.apply_control()
    token = AUTOMATION.install_mutation_guard(
        lambda: (
            supervisor.apply_control() is not None
            and supervisor.control_state == "RUNNING"
        )
    )
    try:
        paused = store.set_state("PAUSED", source="test")
        with patch("core.adb_utils.subprocess.run") as dispatch:
            result = adb_shell(
                ["input", "tap", "100", "200"],
                check=False,
            )

        assert result is None
        dispatch.assert_not_called()
        assert supervisor.is_paused
        assert supervisor.control_acknowledgements["state"]["request_id"] == (
            paused["state_request_id"]
        )
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_final_adb_boundary_keeps_passive_shell_observation_available():
    AUTOMATION.state = "PAUSED"
    token = AUTOMATION.install_mutation_guard(lambda: False)
    observed = object()
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            return_value=observed,
        ) as dispatch:
            result = adb_shell(["pidof", "com.prineside.tdi2"], check=False)

        assert result is observed
        dispatch.assert_called_once()
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_clipboard_observation_remains_available_while_paused():
    AUTOMATION.state = "PAUSED"
    token = AUTOMATION.install_mutation_guard(lambda: False)
    observed = SimpleNamespace(returncode=0)
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            return_value=observed,
        ) as dispatch:
            result = adb_shell(
                [
                    "service",
                    "call",
                    "clipboard",
                    "3",
                    "s16",
                    "com.android.shell",
                ],
                check=False,
            )

        assert result is observed
        dispatch.assert_called_once()
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_unlisted_dumpsys_command_requires_mutation_authority():
    token = AUTOMATION.install_mutation_guard(lambda: False)
    try:
        with patch("core.adb_utils.subprocess.run") as dispatch:
            result = adb_shell(
                ["dumpsys", "package", "com.prineside.tdi2"],
                check=False,
            )

        assert result is None
        dispatch.assert_not_called()
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_mutating_adb_timeout_pauses_and_reports_uncertain_result():
    AUTOMATION.state = "RUNNING"
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            side_effect=subprocess.TimeoutExpired("adb", 10.0),
        ) as dispatch:
            result = adb_shell(
                ["input", "tap", "100", "200"],
                check=False,
            )

        assert result is None
        assert AUTOMATION.state.value == "PAUSED"
        assert len(failures) == 1
        assert "timed out after dispatch" in failures[0]
        assert dispatch.call_args.kwargs["timeout"] == 10.0
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_mutating_adb_nonzero_result_pauses_as_uncertain():
    AUTOMATION.state = "RUNNING"
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=1),
        ):
            result = adb_shell(
                ["input", "swipe", "1", "2", "3", "4", "100"],
                check=False,
            )

        assert result is None
        assert AUTOMATION.state.value == "PAUSED"
        assert len(failures) == 1
        assert "nonzero result" in failures[0]
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_mutating_adb_process_creation_failure_is_not_catastrophic():
    AUTOMATION.state = "RUNNING"
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            side_effect=FileNotFoundError("adb is unavailable"),
        ):
            result = adb_shell(
                ["input", "tap", "100", "200"],
                check=False,
                report_errors=False,
            )

        assert result is None
        assert AUTOMATION.state.value == "RUNNING"
        assert failures == []
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_mutating_adb_typed_outcome_distinguishes_pre_dispatch_failure():
    AUTOMATION.state = "RUNNING"
    token = AUTOMATION.install_mutation_guard(lambda: True)
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            side_effect=FileNotFoundError("adb is unavailable"),
        ):
            outcome = adb_shell(
                ["input", "keyevent", "KEYCODE_HOME"],
                check=False,
                report_errors=False,
                return_dispatch_outcome=True,
            )

        assert outcome.accepted is False
        assert outcome.attempted is False
        assert outcome.uncertain is False
        assert AUTOMATION.state.value == "RUNNING"
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_mutating_adb_typed_outcome_marks_timeout_attempted_and_uncertain():
    AUTOMATION.state = "RUNNING"
    token = AUTOMATION.install_mutation_guard(lambda: True)
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            side_effect=subprocess.TimeoutExpired("adb", 10.0),
        ):
            outcome = adb_shell(
                ["input", "keyevent", "KEYCODE_HOME"],
                check=False,
                report_errors=False,
                return_dispatch_outcome=True,
            )

        assert outcome.accepted is False
        assert outcome.attempted is True
        assert outcome.uncertain is True
        assert AUTOMATION.state.value == "PAUSED"
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_mutating_adb_keyboard_interrupt_is_reported_before_propagation():
    AUTOMATION.state = "RUNNING"
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            side_effect=KeyboardInterrupt(),
        ):
            with pytest.raises(KeyboardInterrupt):
                adb_shell(
                    ["input", "tap", "100", "200"],
                    check=False,
                )

        assert AUTOMATION.state.value == "PAUSED"
        assert len(failures) == 1
        assert "interrupted after dispatch may have started" in failures[0]
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_mutating_adb_post_start_oserror_is_uncertain():
    AUTOMATION.state = "RUNNING"
    failures = []
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=failures.append,
    )
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            side_effect=BrokenPipeError("child transport failed"),
        ):
            outcome = adb_shell(
                ["input", "tap", "100", "200"],
                check=False,
                return_dispatch_outcome=True,
            )

        assert outcome.attempted is True
        assert outcome.uncertain is True
        assert AUTOMATION.state.value == "PAUSED"
        assert len(failures) == 1
    finally:
        AUTOMATION.clear_mutation_guard(token)


def test_screenshot_subprocesses_have_finite_timeouts():
    observed = SimpleNamespace(stdout=b"frame")
    with patch(
        "core.adb_utils.subprocess.run",
        return_value=observed,
    ) as dispatch:
        assert screencap_png(report_errors=False) == b"frame"
        assert screencap_raw() == b"frame"

    assert dispatch.call_count == 2
    assert [call.kwargs["timeout"] for call in dispatch.call_args_list] == [
        10.0,
        10.0,
    ]


def test_screenshot_timeouts_return_without_hanging():
    with patch(
        "core.adb_utils.subprocess.run",
        side_effect=subprocess.TimeoutExpired("adb", 0.1),
    ):
        assert screencap_png(report_errors=False, timeout_s=0.1) is None
        assert screencap_raw(timeout_s=0.1) is None


def test_runtime_mutation_guard_exception_is_reported_once_and_fails_closed():
    control = AutomationControl()
    failures = []

    def fail_guard():
        raise RuntimeError("control refresh failed")

    token = control.install_mutation_guard(
        fail_guard,
        guard_failure_handler=failures.append,
    )
    try:
        with control.authorize_mutation() as allowed:
            assert not allowed
        control.state = "RUNNING"
        with control.authorize_mutation() as allowed:
            assert not allowed

        assert control.state.value == "PAUSED"
        assert len(failures) == 1
        assert "control refresh failed" in failures[0]
    finally:
        control.clear_mutation_guard(token)


def test_recoverable_runtime_failure_survives_diagnostic_log_io_failure():
    with patch("core.app.log", side_effect=OSError("disk full")):
        decision = App._flag_recoverable_runtime_failure(
            RuntimeFailureKind.REPORTING_FAILURE,
            "durable status report unavailable",
        )

    assert decision.disposition.value == "continue_degraded"


def test_shutdown_latch_denies_mutation_after_runtime_guard_is_removed():
    control = AutomationControl()
    token = control.install_mutation_guard(lambda: True)
    assert control.shutdown_mutations(token)
    assert control.clear_mutation_guard(token)

    control.state = "RUNNING"
    with control.authorize_mutation() as allowed:
        assert not allowed

    replacement = control.install_mutation_guard(lambda: True)
    try:
        with control.authorize_mutation() as allowed:
            assert allowed
    finally:
        control.clear_mutation_guard(replacement)


def test_pause_acknowledgement_follows_current_dispatch_and_blocks_the_next(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    supervisor = _supervisor(control_file)
    supervisor.apply_control()

    def runtime_guard() -> bool:
        supervisor.apply_control()
        return supervisor.control_state == "RUNNING"

    token = AUTOMATION.install_mutation_guard(
        runtime_guard,
        dispatch_control_lock_path=str(store.dispatch_lock_path),
    )
    dispatch_started = threading.Event()
    release_dispatch = threading.Event()
    pause_applied = threading.Event()

    def dispatch(_command, **_kwargs):
        dispatch_started.set()
        assert release_dispatch.wait(timeout=2)
        return object()

    paused = []

    def apply_pause():
        paused.append(store.set_state("PAUSED", source="test"))
        supervisor.apply_control()
        pause_applied.set()

    try:
        with patch("core.adb_utils.subprocess.run", side_effect=dispatch) as run:
            first_input = threading.Thread(
                target=lambda: adb_shell(
                    ["input", "tap", "100", "200"],
                    check=False,
                )
            )
            first_input.start()
            assert dispatch_started.wait(timeout=2)

            pause_thread = threading.Thread(target=apply_pause)
            pause_thread.start()
            assert not pause_applied.wait(timeout=0.05)

            release_dispatch.set()
            first_input.join(timeout=2)
            pause_thread.join(timeout=2)
            assert not first_input.is_alive()
            assert not pause_thread.is_alive()
            assert pause_applied.is_set()
            assert supervisor.control_acknowledgements["state"]["request_id"] == (
                paused[0]["state_request_id"]
            )

            assert adb_shell(
                ["input", "tap", "300", "400"],
                check=False,
            ) is None
            run.assert_called_once()
    finally:
        release_dispatch.set()
        AUTOMATION.clear_mutation_guard(token)


def test_pause_persists_during_lifecycle_prechecks_and_blocks_first_input(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    AUTOMATION.state = "RUNNING"
    prechecks_started = threading.Event()
    release_prechecks = threading.Event()
    pause_persisted = threading.Event()
    input_dispatched = []

    def guard() -> bool:
        return store.status()["state"] == "RUNNING"

    token = AUTOMATION.install_mutation_guard(
        guard,
        dispatch_control_lock_path=str(store.dispatch_lock_path),
    )

    def lifecycle():
        with AUTOMATION.authorize_mutation(
            guard,
            defer_dispatch_boundary=True,
        ) as allowed:
            assert allowed
            prechecks_started.set()
            assert release_prechecks.wait(timeout=2)
            if AUTOMATION.refresh_mutation_authority(guard):
                input_dispatched.append(True)

    def pause():
        store.set_state("PAUSED", source="test")
        pause_persisted.set()

    lifecycle_thread = threading.Thread(target=lifecycle)
    pause_thread = threading.Thread(target=pause)
    try:
        lifecycle_thread.start()
        assert prechecks_started.wait(timeout=2)
        pause_thread.start()
        assert pause_persisted.wait(timeout=1)
        release_prechecks.set()
        lifecycle_thread.join(timeout=2)
        pause_thread.join(timeout=2)

        assert not lifecycle_thread.is_alive()
        assert not pause_thread.is_alive()
        assert input_dispatched == []
    finally:
        release_prechecks.set()
        lifecycle_thread.join(timeout=2)
        pause_thread.join(timeout=2)
        AUTOMATION.clear_mutation_guard(token)


def test_control_state_is_reasserted_after_process_local_drift(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    paused = store.set_state("PAUSED", source="test")
    supervisor = _supervisor(control_file)
    supervisor.apply_control()
    AUTOMATION.state = "UNKNOWN"

    supervisor.apply_control()

    assert supervisor.is_paused
    assert supervisor.control_acknowledgements["state"]["request_id"] == (
        paused["state_request_id"]
    )


def test_unreadable_control_authority_fails_closed(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    ControlDirectiveStore(control_file).set_state("RUNNING", source="test")
    supervisor = _supervisor(control_file)
    supervisor.apply_control()
    assert supervisor.control_state == "RUNNING"

    with patch.object(
        supervisor._control_store,
        "read",
        side_effect=ControlDirectiveError("unreadable"),
    ):
        supervisor.apply_control()

    assert supervisor.is_paused
    assert supervisor.control_acknowledgements["state"]["value"] == "RUNNING"

    # Recovering the same old RUNNING snapshot is not an Enable request.
    supervisor.apply_control()
    assert supervisor.is_paused

    enabled = ControlDirectiveStore(control_file).set_state(
        "RUNNING",
        source="test-enable",
    )
    supervisor.apply_control()
    assert not supervisor.is_paused
    assert supervisor.control_acknowledgements["state"]["request_id"] == (
        enabled["state_request_id"]
    )


def test_failed_catastrophic_pause_persistence_requires_fresh_enable(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    supervisor = _supervisor(control_file)
    supervisor.apply_control()
    assert supervisor.control_state == "RUNNING"

    with patch.object(
        supervisor._control_store,
        "set_paused_unless_stopped",
        side_effect=ControlDirectiveError("write failed"),
    ):
        persisted = supervisor.pause_for_catastrophic_failure(
            RuntimeFailureKind.INPUT_RESULT_UNCERTAIN,
            reason="ADB result unknown",
        )

    assert not persisted
    assert supervisor.is_paused
    supervisor.apply_control()
    assert supervisor.is_paused

    enabled = store.set_state("RUNNING", source="test-enable")
    supervisor.apply_control()
    assert not supervisor.is_paused
    assert supervisor.control_acknowledgements["state"]["request_id"] == (
        enabled["state_request_id"]
    )


def test_catastrophic_pause_latches_before_fallible_logging(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    supervisor = _supervisor(control_file)
    supervisor.apply_control()

    with patch(
        "core.automation_supervisor._write_log",
        side_effect=OSError("disk full"),
    ):
        assert supervisor.pause_for_catastrophic_failure(
            RuntimeFailureKind.INPUT_RESULT_UNCERTAIN,
            reason="ADB result unknown",
        )

    assert supervisor.is_paused
    assert supervisor.catastrophic_pause_hold["active"] is True
    supervisor.apply_control()
    assert supervisor.is_paused

    store.set_state("RUNNING", source="fresh-enable")
    supervisor.apply_control()
    assert supervisor.is_paused is False


def test_explicit_stop_wins_over_late_catastrophic_result(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    supervisor = _supervisor(control_file)
    supervisor.apply_control()
    stopped = store.set_state("STOPPED", source="operator-stop")

    assert supervisor.pause_for_catastrophic_failure(
        RuntimeFailureKind.INPUT_RESULT_UNCERTAIN,
        reason="late result from an in-flight command",
    )

    persisted = store.status()
    assert persisted["state"] == "STOPPED"
    assert persisted["state_request_id"] == stopped["state_request_id"]
    assert supervisor.control_state == "STOPPED"
    assert supervisor.catastrophic_pause_hold["active"] is False


def test_initial_control_read_failure_requires_a_fresh_enable(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    running = store.set_state("RUNNING", source="old-running")
    original_read = ControlDirectiveStore.read
    reads = 0

    def fail_first_read(instance):
        nonlocal reads
        reads += 1
        if reads == 1:
            raise ControlDirectiveError("initial read failed")
        return original_read(instance)

    with patch.object(ControlDirectiveStore, "read", new=fail_first_read):
        supervisor = _supervisor(control_file)
        supervisor.apply_control()

    assert supervisor.is_paused
    acknowledgement = supervisor.control_acknowledgements["state"]
    assert not (
        isinstance(acknowledgement, dict)
        and acknowledgement.get("request_id") == running["state_request_id"]
        and acknowledgement.get("value") == "RUNNING"
    )

    enabled = store.set_state("RUNNING", source="test-enable")
    supervisor.apply_control()
    assert not supervisor.is_paused
    assert supervisor.control_acknowledgements["state"]["request_id"] == (
        enabled["state_request_id"]
    )


def test_deleted_control_authority_requires_a_fresh_enable(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    running = store.set_state("RUNNING", source="old-running")
    old_payload = store.read()
    supervisor = _supervisor(control_file)
    supervisor.apply_control()

    control_file.unlink()
    supervisor.apply_control()

    assert supervisor.is_paused
    assert supervisor.catastrophic_pause_hold["active"] is True

    # Recreating the same stale authority cannot resume input.
    control_file.write_text(json.dumps(old_payload), encoding="utf-8")
    supervisor.apply_control()
    assert supervisor.is_paused
    assert supervisor.control_request_identity["state_request_id"] == (
        running["state_request_id"]
    )

    enabled = store.set_state("RUNNING", source="test-enable")
    supervisor.apply_control()
    assert not supervisor.is_paused
    assert supervisor.control_request_identity["state_request_id"] == (
        enabled["state_request_id"]
    )


@pytest.mark.parametrize(
    "corrupt",
    (
        lambda payload: payload.clear(),
        lambda payload: payload.pop("state_request_id"),
        lambda payload: payload.__setitem__("state", "DANCING"),
    ),
)
def test_malformed_core_state_authority_fails_closed(tmp_path, corrupt):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    supervisor = _supervisor(control_file)
    supervisor.apply_control()
    payload = store.read()
    corrupt(payload)
    control_file.write_text(json.dumps(payload), encoding="utf-8")

    supervisor.apply_control()

    assert supervisor.is_paused
    assert supervisor.catastrophic_pause_hold["active"] is True


def test_failed_initial_control_materialization_starts_paused(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    with patch.object(
        ControlDirectiveStore,
        "ensure_request_identities",
        side_effect=ControlDirectiveError("bootstrap write failed"),
    ):
        supervisor = _supervisor(control_file)

    assert supervisor.is_paused
    assert supervisor.catastrophic_pause_hold["active"] is True

    # Materialized defaults establish a baseline; only the later explicit
    # Enable request may release the process-local catastrophic hold.
    store = ControlDirectiveStore(control_file)
    store.ensure_request_identities()
    supervisor.apply_control()
    assert supervisor.is_paused
    enabled = store.set_state("RUNNING", source="test-enable")
    supervisor.apply_control()
    assert not supervisor.is_paused
    assert supervisor.control_request_identity["state_request_id"] == (
        enabled["state_request_id"]
    )


@pytest.mark.parametrize(
    "field",
    (
        "interactive_development_lease",
        "manual_control",
        "battle_workflow",
        "setup_capture",
    ),
)
def test_final_mutation_guard_rejects_malformed_input_authority(
    tmp_path,
    field,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    supervisor = _supervisor(control_file)
    supervisor.apply_control()
    payload = store.read()
    payload[field] = {}
    control_file.write_text(json.dumps(payload), encoding="utf-8")
    app = App.__new__(App)
    app._supervisor = supervisor
    app._runtime_shutting_down = False
    app._status_reporter = None

    assert app._runtime_control_mutation_guard() is False
    assert supervisor.is_paused
    assert supervisor.input_authority_error is not None
    assert store.read()["state"] == "PAUSED"


def test_auto_return_pairs_intent_and_terminal_result(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    ControlDirectiveStore(control_file).set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
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


def test_control_store_keeps_emulator_location_coupled_to_adb_request(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)

    selected = store.set_adb_port(
        5555,
        emulator_location=_emulator_location(),
        source="test",
    )
    location = store.status()["emulator_location"]
    assert location["request_id"] == selected["adb_port_request_id"]
    assert location["host_name"] == "WORKSTATION-B"
    assert location["bluestacks_listener"]["adb_port"] == 5565

    reasserted = store.set_adb_port(5555, source="test")
    assert reasserted["emulator_location"]["request_id"] == (
        reasserted["adb_port_request_id"]
    )
    moved = store.set_adb_port(5575, source="test")
    assert "emulator_location" not in moved

    malformed = _emulator_location()
    malformed["bluestacks_listener"] = {
        "adb_port": 5565,
        "instance_name": "Nougat32",
        "process_started_at": {"not": "a timestamp"},
    }
    with pytest.raises(ValueError, match="emulator_location is malformed"):
        store.set_adb_port(
            5555,
            emulator_location=malformed,
            source="test",
        )


def test_paused_runtime_revalidates_declared_host_on_unchanged_port(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("PAUSED", source="test")
    selected = store.set_adb_port(
        5555,
        emulator_location=_emulator_location(),
        source="test",
    )
    handoffs = []
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=lambda _port: pytest.fail(
            "plain port callback must not own a declared-host handoff"
        ),
        emulator_location_handoff=(
            lambda port, location: handoffs.append((port, location)) or True
        ),
    )

    supervisor.apply_control()

    assert len(handoffs) == 1
    assert handoffs[0][0] == 5555
    assert handoffs[0][1]["host_name"] == "WORKSTATION-B"
    assert supervisor.emulator_location == selected["emulator_location"]
    receipt = supervisor.control_acknowledgements["adb_target"]
    assert receipt["request_id"] == selected["adb_port_request_id"]


def test_running_runtime_defers_declared_host_even_on_unchanged_port(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    store.set_adb_port(
        5555,
        emulator_location=_emulator_location(),
        source="test",
    )
    handoffs = []
    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        emulator_location_handoff=(
            lambda port, location: handoffs.append((port, location)) or True
        ),
    )

    supervisor.apply_control()
    assert handoffs == []

    store.set_state("PAUSED", source="test")
    supervisor.apply_control()
    assert len(handoffs) == 1


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
    assert migrated["state"] == "PAUSED"
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
    assert supervisor.is_paused


def test_malformed_present_state_identity_is_preserved_and_rejected(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.replace(
        {
            "state": "RUNNING",
            "state_request_id": 123,
        }
    )

    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
    )

    assert store.read()["state_request_id"] == 123
    assert supervisor.is_paused
    assert supervisor.catastrophic_pause_hold["active"] is True


def test_legacy_running_without_identity_is_migrated_to_safe_pause(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.replace({"state": "RUNNING"})

    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
    )
    migrated = store.read()

    assert migrated["state"] == "PAUSED"
    assert isinstance(migrated["state_request_id"], str)
    assert migrated["state_request_id"]
    assert supervisor.is_paused


def test_malformed_stopped_identity_preserves_stop_without_acknowledgement(
    tmp_path,
):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.replace({"state": "STOPPED", "state_request_id": 123})

    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
    )

    assert store.read()["state_request_id"] == 123
    assert supervisor.control_state == "STOPPED"
    assert supervisor.catastrophic_pause_hold["active"] is False
    assert supervisor.control_acknowledgements["state"] is None


def test_control_read_failure_never_weakens_explicit_stop(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    stopped = store.set_state("STOPPED", source="test")
    supervisor = _supervisor(control_file)
    supervisor.apply_control()

    with patch.object(
        supervisor._control_store,
        "read",
        side_effect=ControlDirectiveError("unreadable"),
    ):
        assert supervisor.apply_control() is False

    assert supervisor.control_state == "STOPPED"
    assert supervisor.catastrophic_pause_hold["active"] is False
    assert supervisor.control_acknowledgements["state"]["request_id"] == (
        stopped["state_request_id"]
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
    assert supervisor.timed_pause_expiry_pending == saved["state_request_id"]

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
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    store.set_mode("NEXT_BATTLE", source="test")
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


def test_paused_runtime_retries_deferred_adb_port_handoff(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("PAUSED", source="test")
    store.set_adb_port(5565, source="test")
    attempts = []

    def handoff(port):
        attempts.append(port)
        return len(attempts) >= 2

    supervisor = AutomationSupervisor(
        control_file=str(control_file),
        auto_return_enabled=False,
        adb_port_handoff=handoff,
    )

    with patch(
        "core.automation_supervisor.time.monotonic",
        side_effect=(100.0, 105.0, 111.0),
    ):
        supervisor.apply_control()
        supervisor.apply_control()
        supervisor.apply_control()

    assert attempts == [5565, 5565]


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
        active_battle_identity="a" * 64,
        source="test",
    )
    supervisor.apply_control()

    second_request = supervisor.strategy_request
    assert second_request is not None
    assert second_request[0] == "tournament"
    assert second_request[1] != first_request[1]
    assert second_request[2] == "active_battle"
    assert supervisor.strategy_active_battle_identity == "a" * 64


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


def test_advisory_gate_decision_has_no_failure_owned_pause_choice(tmp_path):
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
        "continue_observing",
        source="test",
    )

    assert requested["blocking"] is False
    assert [option["id"] for option in requested["options"]] == [
        "retry",
        "continue_observing",
    ]
    assert resolved is not None
    assert resolved["blocking"] is False
    assert resolved["selected_option"]["action"] == "waive"


def test_failed_check_options_never_offer_pause_or_battle_restart():
    options = build_gate_decision_options("modules")
    advisory = build_gate_decision_options("modules", advisory=True)

    assert {option["action"] for option in options} <= {"retry", "waive"}
    assert {option["action"] for option in advisory} <= {"retry", "waive"}


def test_runtime_can_persist_operator_authority_pause(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    supervisor = _supervisor(control_file)

    assert supervisor.pause_for_operator_authority("operator selected Pause")

    saved = json.loads(control_file.read_text(encoding="utf-8"))
    assert saved["state"] == "PAUSED"
    assert saved["updated_by"] == "runtime-operator-authority"
    assert supervisor.is_paused


def test_recoverable_failure_cannot_use_catastrophic_pause_api(tmp_path):
    control_file = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(control_file)
    store.set_state("RUNNING", source="test")
    supervisor = _supervisor(control_file)

    with pytest.raises(ValueError, match="recoverable"):
        supervisor.pause_for_catastrophic_failure(
            RuntimeFailureKind.CONFIGURATION_MISMATCH,
            reason="configuration differs",
        )

    assert store.status()["state"] == "RUNNING"


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
