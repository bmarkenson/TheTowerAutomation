from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from core.action_authority import (
    RuntimeActionAuthority,
    RuntimeActionAuthorityPublisher,
    RuntimeActionClass,
)
from core.app import App
from core.automation_supervisor import AutomationSupervisor
from core.control_directives import (
    ControlDirectiveStore,
    INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS,
)
from core.control_surface import (
    ControlSurfaceRequestError,
    ControlSurfaceService,
)
from core.development_adb_input import (
    ActionLogAudit,
    DevelopmentInputRequest,
    EXIT_REJECTED,
    EXIT_SUCCESS,
    SubprocessAdbBoundary,
    execute_development_input,
)
from core.run_state import AUTOMATION
from core.screen_geometry import clear_recorded_device_screen_sizes
import core.ss_capture as ss_capture
from tools import development


TARGET = "localhost:5555"
IDENTITY = development.InterpreterIdentity(
    implementation="cpython",
    version="3.12.3",
    system="Linux",
    machine="x86_64",
    platform_tag="linux-x86_64",
)


@pytest.fixture(autouse=True)
def _restore_runtime_globals():
    original_state = AUTOMATION.state
    original_mode = AUTOMATION.mode
    clear_recorded_device_screen_sizes()
    AUTOMATION.state = "RUNNING"
    try:
        yield
    finally:
        AUTOMATION.state = original_state
        AUTOMATION.mode = original_mode
        clear_recorded_device_screen_sizes()


def _development_config(tmp_path: Path) -> development.EnvironmentConfig:
    worktrees = tmp_path / "TheTower-worktrees"
    repository = worktrees / "workers" / "combined-boundary"
    inputs = (
        ".python-version",
        "pyproject.toml",
        "requirements/bootstrap.in",
        "requirements/bootstrap.lock",
        "requirements/runtime.lock",
        "requirements/development.lock",
    )
    config_path = repository / development.CONFIG_RELATIVE_PATH
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"bootstrap_schema": 3}\n', encoding="utf-8")
    for relative in inputs:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"combined-boundary={relative}\n", encoding="utf-8")
    return development.EnvironmentConfig(
        repository_root=repository,
        config_path=config_path,
        bootstrap_schema=3,
        dependency_inputs=inputs,
        environment_root=worktrees / ".environments",
        production_environment=tmp_path / "production" / ".venv",
        interpreter=Path("/usr/bin/python3.12"),
        supported_identity=IDENTITY,
        runtime_root=tmp_path / "runtime" / "thetower",
    )


def _verify_payload(
    environment: Path,
    _config: development.EnvironmentConfig,
    _identity: development.InterpreterIdentity,
    _fingerprint: development.EnvironmentFingerprint,
) -> None:
    assert (environment / "payload.txt").read_text(encoding="utf-8") == "ready\n"


def test_interrupted_bootstrap_recovers_before_atomic_worktree_selection(
    tmp_path: Path,
) -> None:
    config = _development_config(tmp_path)
    fingerprint = development.compute_environment_fingerprint(config, IDENTITY)
    final = development.expected_environment_path(
        config,
        IDENTITY,
        fingerprint.digest,
    )
    old_environment = tmp_path / "previous-development-environment"
    old_environment.mkdir()
    worktree_link = config.repository_root / ".venv"
    worktree_link.symlink_to(old_environment, target_is_directory=True)
    config.environment_root.mkdir(mode=0o700)
    sibling = config.environment_root / f"cpython-3.12-{'f' * 64}"
    sibling.mkdir()
    (sibling / "preserved.txt").write_text("keep\n", encoding="utf-8")

    def interrupted_build(
        _config: development.EnvironmentConfig,
        _identity: development.InterpreterIdentity,
        _fingerprint: development.EnvironmentFingerprint,
        environment: Path,
    ) -> None:
        (environment / "partial.txt").write_text("interrupted\n", encoding="utf-8")
        raise RuntimeError("injected interruption")

    with pytest.raises(RuntimeError, match="injected interruption"):
        development.provision_environment(
            config,
            IDENTITY,
            fingerprint,
            build=interrupted_build,
            verify_contents=_verify_payload,
        )

    assert os.readlink(worktree_link) == str(old_environment)
    assert (final / "partial.txt").is_file()
    assert not (final / development.COMPLETION_MARKER_NAME).exists()
    assert (sibling / "preserved.txt").read_text(encoding="utf-8") == "keep\n"

    build_ready = threading.Event()
    release_build = threading.Event()
    results: list[development.ProvisionResult] = []
    failures: list[BaseException] = []

    def completed_build(
        _config: development.EnvironmentConfig,
        _identity: development.InterpreterIdentity,
        _fingerprint: development.EnvironmentFingerprint,
        environment: Path,
    ) -> None:
        assert not (environment / "partial.txt").exists()
        (environment / "payload.txt").write_text("ready\n", encoding="utf-8")
        build_ready.set()
        if not release_build.wait(2):
            raise RuntimeError("test did not release completed builder")

    def provision() -> None:
        try:
            results.append(
                development.provision_environment(
                    config,
                    IDENTITY,
                    fingerprint,
                    build=completed_build,
                    verify_contents=_verify_payload,
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    builder = threading.Thread(target=provision, daemon=True)
    builder.start()
    try:
        assert build_ready.wait(1)
        assert os.readlink(worktree_link) == str(old_environment)
        assert not (final / development.COMPLETION_MARKER_NAME).exists()
    finally:
        release_build.set()
        builder.join(timeout=3)

    assert not builder.is_alive()
    assert failures == []
    assert len(results) == 1 and results[0].reused is False
    development.verify_completion_marker(
        final,
        config=config,
        identity=IDENTITY,
        fingerprint=fingerprint,
    )
    development.verify_worktree_environment_link(config, final)
    assert os.readlink(worktree_link) == str(final)
    assert (sibling / "preserved.txt").read_text(encoding="utf-8") == "keep\n"

    reused = development.provision_environment(
        config,
        IDENTITY,
        fingerprint,
        build=lambda *_args: pytest.fail("completed environment was rebuilt"),
        verify_contents=_verify_payload,
    )
    assert reused.reused is True


def _decode_png(payload: bytes) -> np.ndarray:
    decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    return decoded


def test_concurrent_frame_reader_sees_complete_old_then_new_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    target = screenshot_dir / "latest.png"
    metadata = screenshot_dir / "latest.json"
    old_frame = np.full((1920, 1080, 3), 19, dtype=np.uint8)
    encoded_old_ok, encoded_old = cv2.imencode(".png", old_frame)
    assert encoded_old_ok
    target.write_bytes(encoded_old.tobytes())
    metadata.write_text('{"schema_version": 0}\n', encoding="utf-8")

    native_frame = np.full((1280, 720, 3), 73, dtype=np.uint8)
    encoded_new_ok, encoded_new = cv2.imencode(".png", native_frame)
    assert encoded_new_ok
    png_staged = threading.Event()
    release_publish = threading.Event()
    real_write = ss_capture._write_temporary_payload
    blocked_once = False

    def blocking_write(temporary, payload: bytes) -> None:
        nonlocal blocked_once
        real_write(temporary, payload)
        if not blocked_once and ".latest.png." in Path(temporary.name).name:
            blocked_once = True
            png_staged.set()
            if not release_publish.wait(2):
                raise RuntimeError("test did not release frame publisher")

    monkeypatch.setattr(ss_capture, "resolve_adb_device", lambda: TARGET)
    monkeypatch.setattr(
        ss_capture,
        "screencap_png",
        lambda **_kwargs: encoded_new.tobytes(),
    )
    monkeypatch.setattr(ss_capture, "_write_temporary_payload", blocking_write)
    results: list[ss_capture.ScreenshotCaptureResult] = []
    failures: list[BaseException] = []

    def publish() -> None:
        try:
            results.append(
                ss_capture.capture_and_save_screenshot_result(log_capture=False)
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    writer = threading.Thread(target=publish, daemon=True)
    writer.start()
    try:
        assert png_staged.wait(1)
        during_publication = _decode_png(target.read_bytes())
        assert np.array_equal(during_publication, old_frame)
        assert json.loads(metadata.read_text(encoding="utf-8")) == {
            "schema_version": 0
        }
    finally:
        release_publish.set()
        writer.join(timeout=3)

    assert not writer.is_alive()
    assert failures == []
    assert len(results) == 1
    result = results[0]
    assert result.frame is not None
    after_publication = _decode_png(target.read_bytes())
    assert np.array_equal(after_publication, result.frame)
    assert np.all(after_publication == 73)
    assert json.loads(metadata.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "captured_at": result.captured_at.isoformat().replace("+00:00", "Z"),
        "adb_target": TARGET,
        "native_width": 720,
        "native_height": 1280,
        "canonical_width": 1080,
        "canonical_height": 1920,
    }
    assert not list(screenshot_dir.glob(".latest.*.tmp"))


@dataclass
class CoordinationHarness:
    root: Path
    base_time: float
    store: ControlDirectiveStore
    supervisor: AutomationSupervisor
    app: App
    service: ControlSurfaceService
    lock_handle: Any

    def close(self) -> None:
        fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_UN)
        self.lock_handle.close()


def _build_coordination_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stale_after_seconds: int = 30,
) -> CoordinationHarness:
    base_time = float(int(time.time()))
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setenv("ADB_DEVICE", TARGET)
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(logs / "actions.log"))
    control_path = logs / "automation_ctl.json"
    store = ControlDirectiveStore(control_path)
    store.replace({"custom": "preserved"})
    store.set_state("RUNNING", source="test-harness")
    store.set_mode("WAIT", source="test-harness")
    supervisor = AutomationSupervisor(
        control_file=str(control_path),
        auto_return_enabled=False,
    )
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()

    lock_path = logs / "automation-localhost_5555.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "target": TARGET,
                "started_at": datetime.fromtimestamp(
                    base_time,
                    tz=timezone.utc,
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    lock_handle = lock_path.open("r+", encoding="utf-8")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    app = App.__new__(App)
    app._supervisor = supervisor
    app._action_authority = RuntimeActionAuthority()
    app._action_authority_publisher = RuntimeActionAuthorityPublisher(
        logs / "strategy_action_gate.json",
        owner=owner,
        stale_after_seconds=stale_after_seconds,
    )
    app._authority_battle_active = True
    app._authority_primary_state = "RUNNING"
    app._active_round_identity_fingerprint = "a" * 64
    app._authority_holds = ()
    app._external_development_hold_active = False
    app._interactive_development_ack = None
    app._current_run_scope_id = lambda: "run-combined-boundary"
    app._update_action_authority()
    assert app._publish_action_authority()

    service = ControlSurfaceService(
        repository_root=tmp_path,
        stale_after_seconds=stale_after_seconds,
    )
    return CoordinationHarness(
        root=tmp_path,
        base_time=base_time,
        store=store,
        supervisor=supervisor,
        app=app,
        service=service,
        lock_handle=lock_handle,
    )


@pytest.fixture
def coordination_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    harness = _build_coordination_harness(tmp_path, monkeypatch)
    try:
        yield harness
    finally:
        harness.close()


def _activate_lease(harness: CoordinationHarness) -> str:
    requested = harness.service.apply_interactive_development_lease(
        {
            "operation": "request",
            "owner_label": "combined coordination boundary",
        },
        now=harness.base_time,
    )
    lease_id = requested["operation"]["lease_id"]
    harness.supervisor.apply_control()

    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        harness.app._sync_interactive_development_control_boundary(
            now=harness.base_time + 1,
        )
    pending = harness.service.status(now=harness.base_time + 1)[
        "interactive_development_lease"
    ]
    assert pending["active"] is False
    assert pending["runtime_acknowledgement"]["state"] == "pending"
    assert pending["external_hold_installed"] is True
    gate = harness.service.status(now=harness.base_time + 1)[
        "strategy_action_gate"
    ]
    assert gate["observation_authority"]["allowed"] is True
    assert gate["auxiliary_collection_authority"]["allowed"] is False
    assert gate["strategy_action_authority"]["allowed"] is False
    assert gate["lifecycle_action_authority"]["allowed"] is False

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=True),
    ):
        harness.app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=harness.base_time + 2,
        )
    still_pending = harness.service.status(now=harness.base_time + 2)[
        "interactive_development_lease"
    ]
    assert still_pending["active"] is False
    assert still_pending["runtime_acknowledgement"]["state"] == "pending"

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        harness.app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=harness.base_time + 3,
        )
    active = harness.service.status(now=harness.base_time + 3)[
        "interactive_development_lease"
    ]
    assert active["active"] is True
    assert active["runtime_acknowledgement"]["state"] == "active"
    return lease_id


class FakeAdbRunner:
    def __init__(self) -> None:
        native_frame = np.full((1280, 720, 3), 81, dtype=np.uint8)
        encoded_ok, encoded = cv2.imencode(".png", native_frame)
        assert encoded_ok
        self.png = encoded.tobytes()
        self.commands: list[tuple[list[str], float]] = []

    def __call__(self, command: Sequence[str], **kwargs):
        normalized = [str(value) for value in command]
        self.commands.append((normalized, float(kwargs["timeout"])))
        if normalized[3:] == ["exec-out", "screencap", "-p"]:
            return subprocess.CompletedProcess(normalized, 0, self.png, b"")
        assert normalized[3:5] == ["shell", "input"]
        return subprocess.CompletedProcess(normalized, 0, b"", b"")


class ServiceStatusReader:
    def __init__(
        self,
        harness: CoordinationHarness,
        offsets: Sequence[float],
    ) -> None:
        self.harness = harness
        self.offsets = list(offsets)
        self.calls = 0

    def __call__(self) -> Mapping[str, Any]:
        index = min(self.calls, len(self.offsets) - 1)
        self.calls += 1
        return self.harness.service.status(
            now=self.harness.base_time + self.offsets[index]
        )


def _execute_input(
    harness: CoordinationHarness,
    lease_id: str,
    *,
    offsets: Sequence[float],
    request: DevelopmentInputRequest | None = None,
    runner: FakeAdbRunner | None = None,
):
    selected_runner = runner or FakeAdbRunner()
    reader = ServiceStatusReader(harness, offsets)
    result = execute_development_input(
        request or DevelopmentInputRequest.tap(540, 960),
        lease_id=lease_id,
        status_reader=reader,
        adb=SubprocessAdbBoundary(run=selected_runner),
        audit=ActionLogAudit(harness.root / "logs" / "actions.log"),
    )
    return result, reader, selected_runner


def test_full_lease_path_excludes_competitors_dispatches_once_and_releases_cleanly(
    coordination_harness: CoordinationHarness,
) -> None:
    harness = coordination_harness
    lease_id = _activate_lease(harness)
    with pytest.raises(ControlSurfaceRequestError) as busy:
        harness.service.apply_interactive_development_lease(
            {"operation": "request", "owner_label": "competing worker"},
            now=harness.base_time + 4,
        )
    assert busy.value.status == 409
    assert busy.value.code == "busy"

    result, reader, runner = _execute_input(
        harness,
        lease_id,
        offsets=(4, 4),
    )
    assert result.exit_code == EXIT_SUCCESS
    assert result.input_attempted is True
    assert reader.calls == 2
    assert [command for command, _timeout in runner.commands] == [
        ["adb", "-s", TARGET, "exec-out", "screencap", "-p"],
        [
            "adb",
            "-s",
            TARGET,
            "shell",
            "input",
            "tap",
            "360",
            "640",
        ],
    ]

    harness.service.apply_interactive_development_lease(
        {"operation": "release", "lease_id": lease_id},
        now=harness.base_time + 5,
    )
    harness.supervisor.apply_control()
    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        harness.app._sync_interactive_development_control_boundary(
            now=harness.base_time + 5,
        )
    assert harness.app._interactive_development_ack["state"] == "release_pending"
    assert harness.app._external_development_hold_active

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        harness.app._sync_interactive_development_observation(
            {"state": "UNKNOWN"},
            now=harness.base_time + 6,
        )
    assert harness.app._interactive_development_ack["state"] == "release_blocked"
    assert harness.app._external_development_hold_active

    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        harness.app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=harness.base_time + 7,
        )
    terminal = harness.store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == "released"
    assert not harness.app._external_development_hold_active
    decision = harness.app._get_action_authority().decision(
        RuntimeActionClass.STRATEGY_ACTION
    )
    assert not decision.allowed
    assert decision.reason == (
        "the active battle has not been bound to a forced save identity"
    )
    assert harness.app._battle_identity_reconciliation_required is True

    rejected, rejected_reader, rejected_runner = _execute_input(
        harness,
        lease_id,
        offsets=(8,),
    )
    assert rejected.exit_code == EXIT_REJECTED
    assert rejected_reader.calls == 1
    assert rejected_runner.commands == []
    action_log = (harness.root / "logs" / "actions.log").read_text(
        encoding="utf-8"
    )
    assert "Development ADB tap attempted" in action_log
    assert "Interactive development lease ended" in action_log


@pytest.mark.parametrize(
    ("action", "operator_state"),
    (("pause", "PAUSED"), ("stop", "STOPPED")),
)
def test_operator_pause_and_stop_precede_helper_and_never_revive_the_lease(
    coordination_harness: CoordinationHarness,
    action: str,
    operator_state: str,
) -> None:
    harness = coordination_harness
    lease_id = _activate_lease(harness)
    harness.service.apply_control({"action": action})

    rejected, _reader, runner = _execute_input(
        harness,
        lease_id,
        offsets=(4,),
    )
    assert rejected.exit_code == EXIT_REJECTED
    assert operator_state in rejected.message
    assert runner.commands == []

    harness.supervisor.apply_control()
    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        harness.app._sync_interactive_development_control_boundary(
            now=harness.base_time + 4,
        )
    terminal = harness.store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == "revoked"
    assert operator_state in terminal["terminal_reason"]
    assert not harness.app._external_development_hold_active

    if action == "stop":
        with pytest.raises(ControlSurfaceRequestError) as error:
            harness.service.apply_control({"action": "resume"})
        assert error.value.code == "process_stopping"
        assert harness.store.status()["state"] == "STOPPED"
        return

    harness.service.apply_control({"action": "resume"})
    harness.supervisor.apply_control()
    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        harness.app._sync_interactive_development_control_boundary(
            now=harness.base_time + 5,
        )
    status = harness.service.status(now=harness.base_time + 5)
    assert status["control"]["state"] == "RUNNING"
    assert status["interactive_development_lease"]["active"] is False
    assert harness.app._interactive_development_ack["state"] == "terminal"


def test_heartbeat_expiry_rejects_input_then_restores_after_fresh_observation(
    coordination_harness: CoordinationHarness,
) -> None:
    harness = coordination_harness
    expired_offset = 10 + INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS + 1
    lease_id = _activate_lease(harness)
    harness.service.apply_interactive_development_lease(
        {"operation": "heartbeat", "lease_id": lease_id},
        now=harness.base_time + 10,
    )
    harness.supervisor.apply_control()
    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        harness.app._sync_interactive_development_control_boundary(
            now=harness.base_time + 10,
        )
    renewed = harness.service.status(now=harness.base_time + 10)[
        "interactive_development_lease"
    ]
    assert renewed["active"] is True
    assert renewed["request"]["expires_at"] == renewed[
        "runtime_acknowledgement"
    ]["expires_at"]

    rejected, _reader, runner = _execute_input(
        harness,
        lease_id,
        offsets=(expired_offset,),
    )
    assert rejected.exit_code == EXIT_REJECTED
    assert "expired" in rejected.message
    assert runner.commands == []

    with patch("core.app.stop_blind_gem_tapper", return_value=False):
        harness.app._sync_interactive_development_control_boundary(
            now=harness.base_time + expired_offset,
        )
    assert harness.app._interactive_development_ack["state"] == "expiry_pending"
    assert harness.app._external_development_hold_active
    with (
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch("core.app.is_blind_gem_tapper_active", return_value=False),
    ):
        harness.app._sync_interactive_development_observation(
            {"state": "RUNNING"},
            now=harness.base_time + expired_offset + 1,
        )
    terminal = harness.store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == "expired"
    assert not harness.app._external_development_hold_active


@pytest.mark.parametrize(
    ("boundary", "disposition"),
    (
        ("runtime", "abnormal"),
        ("target", "abnormal"),
    ),
)
def test_runtime_and_target_boundaries_revoke_helper_authority(
    coordination_harness: CoordinationHarness,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    disposition: str,
) -> None:
    harness = coordination_harness
    lease_id = _activate_lease(harness)
    if boundary == "runtime":
        prior_owner = harness.supervisor.current_exclusive_validation_owner()
        monkeypatch.setattr(
            harness.supervisor,
            "current_exclusive_validation_owner",
            lambda: {**prior_owner, "runtime_id": "replacement-runtime"},
        )
        with patch("core.app.stop_blind_gem_tapper", return_value=False):
            harness.app._sync_interactive_development_control_boundary(
                now=harness.base_time + 4,
            )
    elif boundary == "target":
        monkeypatch.setenv("ADB_DEVICE", "localhost:5565")
        with patch("core.app.stop_blind_gem_tapper", return_value=False):
            harness.app._sync_interactive_development_control_boundary(
                now=harness.base_time + 4,
            )
    terminal = harness.store.status()["interactive_development_lease"]
    assert terminal["terminal_disposition"] == disposition
    assert not harness.app._external_development_hold_active
    rejected, _reader, runner = _execute_input(
        harness,
        lease_id,
        offsets=(5,),
    )
    assert rejected.exit_code == EXIT_REJECTED
    assert runner.commands == []


def test_stale_runtime_acknowledgement_rejects_before_any_fake_adb_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _build_coordination_harness(
        tmp_path,
        monkeypatch,
        stale_after_seconds=5,
    )
    try:
        lease_id = _activate_lease(harness)
        stale = harness.service.status(now=harness.base_time + 12)[
            "interactive_development_lease"
        ]
        assert stale["active"] is False
        assert stale["acknowledgement_fresh"] is False
        rejected, _reader, runner = _execute_input(
            harness,
            lease_id,
            offsets=(12,),
        )
        assert rejected.exit_code == EXIT_REJECTED
        assert "fresh runtime acknowledgement is unavailable" in rejected.message
        assert runner.commands == []
    finally:
        harness.close()


def test_near_expiry_is_rejected_after_geometry_without_mutating_adb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The synthetic harness does not publish periodic wall-clock observations.
    # Keep its authority snapshot fresh across the longer lease so this test
    # isolates the helper's dispatch reserve rather than status staleness.
    harness = _build_coordination_harness(
        tmp_path,
        monkeypatch,
        stale_after_seconds=INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS,
    )
    near_expiry_offset = INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS - 6
    try:
        lease_id = _activate_lease(harness)
        status = harness.service.status(
            now=harness.base_time + near_expiry_offset
        )
        assert status["interactive_development_lease"]["active"] is True

        rejected, reader, runner = _execute_input(
            harness,
            lease_id,
            offsets=(near_expiry_offset, near_expiry_offset),
        )
        assert rejected.exit_code == EXIT_REJECTED
        assert "Heartbeat the lease" in rejected.message
        assert reader.calls == 2
        assert [command for command, _timeout in runner.commands] == [
            ["adb", "-s", TARGET, "exec-out", "screencap", "-p"]
        ]
    finally:
        harness.close()
