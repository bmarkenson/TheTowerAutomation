from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pytest

from core.development_adb_input import (
    ActionLogAudit,
    AdbInputError,
    AdbReadError,
    DevelopmentInputRequest,
    EXIT_ADB_FAILURE,
    EXIT_AUDIT_FAILURE,
    EXIT_REJECTED,
    EXIT_SUCCESS,
    LeaseAuthority,
    LeaseStatusError,
    SubprocessAdbBoundary,
    execute_development_input,
    fetch_control_status,
    validate_active_lease_status,
)
from core.screen_geometry import clear_recorded_device_screen_sizes
from tools import development_adb_input as cli


LEASE_ID = "0123456789abcdef0123456789abcdef"
OTHER_LEASE_ID = "fedcba9876543210fedcba9876543210"
TARGET = "localhost:5565"


@pytest.fixture(autouse=True)
def _clear_geometry():
    clear_recorded_device_screen_sizes()
    yield
    clear_recorded_device_screen_sizes()


def _active_status(*, target: str = TARGET, lease_id: str = LEASE_ID) -> dict[str, Any]:
    requested_at = "2026-08-05T12:00:00-07:00"
    heartbeat_at = "2026-08-05T12:00:10-07:00"
    expires_at = "2026-08-05T12:00:40-07:00"
    runtime = {
        "runtime_id": "runtime-development-input",
        "pid": 4312,
        "adb_target": target,
    }
    request = {
        "schema_version": 1,
        "lease_id": lease_id,
        "owner_label": "lease-aware helper test",
        "request_state": "requested",
        "requested_at": requested_at,
        "heartbeat_at": heartbeat_at,
        "expires_at": expires_at,
        "runtime": deepcopy(runtime),
        "starting_evidence": {
            "screen_state": "RUNNING",
            "battle_active": True,
            "battle_scope": "run-development-input",
            "observed_at": "2026-08-05T12:00:00-07:00",
        },
    }
    acknowledgement = {
        "schema_version": 1,
        "lease_id": lease_id,
        "owner_label": "lease-aware helper test",
        "state": "active",
        "requested_at": requested_at,
        "heartbeat_at": heartbeat_at,
        "expires_at": expires_at,
        "runtime": deepcopy(runtime),
        "hold_installed_at": "2026-08-05T12:00:01-07:00",
        "acknowledged_at": "2026-08-05T12:00:02-07:00",
        "activated_at": "2026-08-05T12:00:02-07:00",
        "updated_at": "2026-08-05T12:00:20-07:00",
        "starting_evidence": {
            "screen_state": "RUNNING",
            "battle_active": True,
            "battle_scope": "run-development-input",
            "observed_at": "2026-08-05T12:00:02-07:00",
        },
    }
    gate = {
        "schema_version": 1,
        "available": True,
        "active": False,
        "stale": False,
        "age_seconds": 1,
        "observed_at": "2026-08-05T12:00:20-07:00",
        "stale_after_seconds": 30,
        "runtime_active": True,
        "owner": deepcopy(runtime),
        "owner_matches_active_runtime": True,
        "global_pause": False,
        "runtime_stopped": False,
        "holds": [
            {
                "hold": "external_development",
                "reason": "interactive development owns the input window",
            }
        ],
        "observation_authority": {
            "action_class": "observation",
            "allowed": True,
            "reason": "observation remains allowed",
            "collector": None,
            "owner": None,
        },
        "auxiliary_collection_authority": {
            "action_class": "auxiliary_collection",
            "allowed": False,
            "reason": "external development suppresses input",
            "collector": None,
            "owner": None,
        },
        "strategy_action_authority": {
            "action_class": "strategy_action",
            "allowed": False,
            "reason": "external development suppresses input",
            "collector": None,
            "owner": None,
        },
        "lifecycle_action_authority": {
            "action_class": "lifecycle_action",
            "allowed": False,
            "reason": "external development suppresses input",
            "collector": None,
            "owner": None,
        },
        "allowed_auxiliary_collectors": [],
        "auxiliary_route": None,
        "interactive_development_lease": deepcopy(acknowledgement),
    }
    return {
        "api_version": 1,
        "server_revision": 26,
        "capabilities": ["interactive_development_lease_v1"],
        "server_time": "2026-08-05T12:00:20-07:00",
        "control": {"state": "RUNNING"},
        "interactive_development_lease": {
            "schema_version": 1,
            "request": request,
            "runtime_acknowledgement": acknowledgement,
            "request_expired": False,
            "acknowledgement_fresh": True,
            "owner_matches_request": True,
            "external_hold_installed": True,
            "active": True,
            "reason": "the matching production runtime acknowledged the lease",
        },
        "strategy_action_gate": gate,
        "runtime": {
            "active": True,
            "instances": [
                {
                    "file": "automation-localhost_5565.lock",
                    "pid": runtime["pid"],
                    "target": target,
                    "lock_held": True,
                    "pid_alive": True,
                    "active": True,
                }
            ],
        },
    }


def _sync_published_ack(status: dict[str, Any]) -> None:
    status["strategy_action_gate"]["interactive_development_lease"] = deepcopy(
        status["interactive_development_lease"]["runtime_acknowledgement"]
    )


class SequenceStatusReader:
    def __init__(self, *statuses: Mapping[str, Any]) -> None:
        self.statuses = [deepcopy(status) for status in statuses]
        self.calls = 0

    def __call__(self) -> Mapping[str, Any]:
        index = min(self.calls, len(self.statuses) - 1)
        self.calls += 1
        return deepcopy(self.statuses[index])


class FakeAdb:
    def __init__(
        self,
        *,
        geometry: tuple[int, int] = (1080, 1920),
        geometry_error: Exception | None = None,
        input_error: Exception | None = None,
    ) -> None:
        self.geometry = geometry
        self.geometry_error = geometry_error
        self.input_error = input_error
        self.geometry_targets: list[str] = []
        self.input_calls: list[tuple[str, list[str]]] = []

    def acquire_geometry(self, target: str) -> tuple[int, int]:
        self.geometry_targets.append(target)
        if self.geometry_error is not None:
            raise self.geometry_error
        return self.geometry

    def run_input(self, target: str, arguments: Sequence[str]) -> None:
        self.input_calls.append((target, list(arguments)))
        if self.input_error is not None:
            raise self.input_error


class RecordingAudit:
    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.events: list[tuple[str, dict[str, Any]]] = []

    def intent(
        self,
        request: DevelopmentInputRequest,
        *,
        lease_id: str,
        operation_id: str,
    ) -> None:
        if self.fail == "intent":
            raise OSError("intent unavailable")
        self.events.append(
            (
                "ACTION",
                {
                    "request": request,
                    "lease_id": lease_id,
                    "operation_id": operation_id,
                },
            )
        )

    def input_attempt(
        self,
        request: DevelopmentInputRequest,
        *,
        authority: LeaseAuthority,
        mapped_coordinates: str,
        outcome: str,
    ) -> None:
        if self.fail == "input":
            raise OSError("input audit unavailable")
        self.events.append(
            (
                "INPUT",
                {
                    "request": request,
                    "authority": authority,
                    "mapped_coordinates": mapped_coordinates,
                    "outcome": outcome,
                },
            )
        )

    def result(
        self,
        request: DevelopmentInputRequest,
        *,
        operation_id: str,
        disposition: str,
        detail: str,
    ) -> None:
        if self.fail == "result":
            raise OSError("result unavailable")
        self.events.append(
            (
                "RESULT",
                {
                    "request": request,
                    "operation_id": operation_id,
                    "disposition": disposition,
                    "detail": detail,
                },
            )
        )


def _execute(
    request: DevelopmentInputRequest,
    *,
    initial: Mapping[str, Any] | None = None,
    final: Mapping[str, Any] | None = None,
    lease_id: str = LEASE_ID,
    adb: FakeAdb | None = None,
    audit: RecordingAudit | ActionLogAudit | None = None,
):
    initial_status = _active_status() if initial is None else initial
    final_status = initial_status if final is None else final
    reader = SequenceStatusReader(initial_status, final_status)
    selected_adb = adb or FakeAdb()
    selected_audit = audit or RecordingAudit()
    result = execute_development_input(
        request,
        lease_id=lease_id,
        status_reader=reader,
        adb=selected_adb,
        audit=selected_audit,
    )
    return result, reader, selected_adb, selected_audit


def test_active_lease_tap_uses_exact_target_and_one_1080p_input():
    result, reader, adb, audit = _execute(
        DevelopmentInputRequest.tap(540, 960),
        adb=FakeAdb(geometry=(1080, 1920)),
    )

    assert result.exit_code == EXIT_SUCCESS
    assert result.input_attempted is True
    assert reader.calls == 2
    assert adb.geometry_targets == [TARGET]
    assert adb.input_calls == [(TARGET, ["input", "tap", "540", "960"])]
    assert [event[0] for event in audit.events] == ["ACTION", "INPUT", "RESULT"]
    assert audit.events[1][1]["outcome"] == "completed"


def test_active_lease_swipe_maps_720p_and_attempts_exactly_one_input():
    result, reader, adb, audit = _execute(
        DevelopmentInputRequest.swipe(270, 1440, 810, 480, 260),
        adb=FakeAdb(geometry=(720, 1280)),
    )

    assert result.exit_code == EXIT_SUCCESS
    assert reader.calls == 2
    assert adb.geometry_targets == [TARGET]
    assert adb.input_calls == [
        (
            TARGET,
            ["input", "swipe", "180", "960", "540", "320", "260"],
        )
    ]
    assert audit.events[1][1]["mapped_coordinates"] == "(180,960)->(540,320)"


def test_validator_returns_the_exact_authoritative_binding():
    authority = validate_active_lease_status(_active_status(), lease_id=LEASE_ID)

    assert authority == LeaseAuthority(
        lease_id=LEASE_ID,
        owner_label="lease-aware helper test",
        runtime_id="runtime-development-input",
        runtime_pid=4312,
        adb_target=TARGET,
        requested_at="2026-08-05T12:00:00-07:00",
    )


def _pending(status: dict[str, Any]) -> None:
    status["interactive_development_lease"]["runtime_acknowledgement"][
        "state"
    ] = "pending"
    status["interactive_development_lease"]["active"] = False
    _sync_published_ack(status)


def _released(status: dict[str, Any]) -> None:
    status["interactive_development_lease"]["request"][
        "request_state"
    ] = "release_requested"
    status["interactive_development_lease"]["active"] = False


def _expired(status: dict[str, Any]) -> None:
    status["interactive_development_lease"]["request_expired"] = True
    status["interactive_development_lease"]["active"] = False


def _terminal(status: dict[str, Any]) -> None:
    request = status["interactive_development_lease"]["request"]
    request["request_state"] = "terminal"
    request["terminal_reason"] = "runtime boundary changed"
    status["interactive_development_lease"]["active"] = False


def _otherwise_inactive(status: dict[str, Any]) -> None:
    status["interactive_development_lease"]["active"] = False
    status["interactive_development_lease"]["reason"] = "inactive test status"


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (_pending, "acknowledgement is pending"),
        (_released, "release_requested"),
        (_expired, "deadline has expired"),
        (_terminal, "runtime boundary changed"),
        (_otherwise_inactive, "composite lease is inactive"),
    ],
)
def test_inactive_lease_lifecycle_states_reject_without_adb_input(mutator, message):
    status = _active_status()
    mutator(status)

    result, reader, adb, audit = _execute(
        DevelopmentInputRequest.tap(100, 200),
        initial=status,
    )

    assert result.exit_code == EXIT_REJECTED
    assert message in result.message
    assert reader.calls == 1
    assert adb.geometry_targets == []
    assert adb.input_calls == []
    assert [event[0] for event in audit.events] == ["ACTION", "RESULT"]


@pytest.mark.parametrize("state", ["PAUSED", "STOPPED"])
def test_pause_and_stop_take_precedence_without_adb_input(state):
    status = _active_status()
    status["control"]["state"] = state

    result, _reader, adb, _audit = _execute(
        DevelopmentInputRequest.tap(100, 200),
        initial=status,
    )

    assert result.exit_code == EXIT_REJECTED
    assert state in result.message
    assert adb.geometry_targets == []
    assert adb.input_calls == []


def _missing_ack(status: dict[str, Any]) -> None:
    status["interactive_development_lease"]["runtime_acknowledgement"] = None
    status["interactive_development_lease"]["active"] = False
    status["strategy_action_gate"]["interactive_development_lease"] = None


def _stale_ack(status: dict[str, Any]) -> None:
    status["interactive_development_lease"]["acknowledgement_fresh"] = False
    status["interactive_development_lease"]["active"] = False
    status["strategy_action_gate"]["stale"] = True


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (_missing_ack, "runtime acknowledgement is missing"),
        (_stale_ack, "runtime acknowledgement is not fresh"),
    ],
)
def test_missing_or_stale_runtime_ack_rejects_without_adb_input(mutator, message):
    status = _active_status()
    mutator(status)

    result, _reader, adb, _audit = _execute(
        DevelopmentInputRequest.tap(100, 200),
        initial=status,
    )

    assert result.exit_code == EXIT_REJECTED
    assert message in result.message
    assert adb.input_calls == []


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ({}, "API version"),
        (
            {**_active_status(), "capabilities": []},
            "does not advertise",
        ),
    ],
)
def test_missing_capability_or_malformed_status_rejects_without_adb_input(
    status,
    message,
):
    result, _reader, adb, _audit = _execute(
        DevelopmentInputRequest.tap(100, 200),
        initial=status,
    )

    assert result.exit_code == EXIT_REJECTED
    assert message in result.message
    assert adb.input_calls == []


def _ack_lease_mismatch(status: dict[str, Any]) -> None:
    status["interactive_development_lease"]["runtime_acknowledgement"][
        "lease_id"
    ] = OTHER_LEASE_ID
    _sync_published_ack(status)


def _ack_target_mismatch(status: dict[str, Any]) -> None:
    status["interactive_development_lease"]["runtime_acknowledgement"]["runtime"][
        "adb_target"
    ] = "localhost:5575"
    _sync_published_ack(status)


def _authority_owner_mismatch(status: dict[str, Any]) -> None:
    status["strategy_action_gate"]["owner"]["runtime_id"] = "other-runtime"


def _active_lock_target_mismatch(status: dict[str, Any]) -> None:
    status["runtime"]["instances"][0]["target"] = "localhost:5575"


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (_ack_lease_mismatch, "different lease"),
        (_ack_target_mismatch, "acknowledgement ownership differ"),
        (_authority_owner_mismatch, "runtime ownership differs"),
        (_active_lock_target_mismatch, "exact ADB target differs"),
    ],
)
def test_request_ack_runtime_and_target_mismatches_reject_without_input(
    mutator,
    message,
):
    status = _active_status()
    mutator(status)

    result, _reader, adb, _audit = _execute(
        DevelopmentInputRequest.tap(100, 200),
        initial=status,
    )

    assert result.exit_code == EXIT_REJECTED
    assert message in result.message
    assert adb.input_calls == []


def test_wrong_supplied_lease_id_rejects_without_adb_read_or_input():
    result, reader, adb, _audit = _execute(
        DevelopmentInputRequest.tap(100, 200),
        lease_id=OTHER_LEASE_ID,
    )

    assert result.exit_code == EXIT_REJECTED
    assert "supplied lease ID" in result.message
    assert reader.calls == 1
    assert adb.geometry_targets == []
    assert adb.input_calls == []


def _change_complete_binding(
    status: dict[str, Any],
    *,
    target: str | None = None,
    lease_id: str | None = None,
) -> None:
    lease = status["interactive_development_lease"]
    request = lease["request"]
    acknowledgement = lease["runtime_acknowledgement"]
    if target is not None:
        request["runtime"]["adb_target"] = target
        acknowledgement["runtime"]["adb_target"] = target
        status["strategy_action_gate"]["owner"]["adb_target"] = target
        status["runtime"]["instances"][0]["target"] = target
    if lease_id is not None:
        request["lease_id"] = lease_id
        acknowledgement["lease_id"] = lease_id
    _sync_published_ack(status)


@pytest.mark.parametrize("change", ["target", "lease"])
def test_target_or_lease_change_after_geometry_rejects_before_input(change):
    final = _active_status()
    if change == "target":
        _change_complete_binding(final, target="localhost:5575")
    else:
        _change_complete_binding(final, lease_id=OTHER_LEASE_ID)
    adb = FakeAdb(geometry=(720, 1280))

    result, reader, adb, audit = _execute(
        DevelopmentInputRequest.tap(540, 960),
        final=final,
        adb=adb,
    )

    assert result.exit_code == EXIT_REJECTED
    assert "Rejected after geometry read" in result.message
    assert reader.calls == 2
    assert adb.geometry_targets == [TARGET]
    assert adb.input_calls == []
    assert [event[0] for event in audit.events] == ["ACTION", "RESULT"]


@pytest.mark.parametrize(
    ("input_request", "message"),
    [
        (DevelopmentInputRequest.tap(float("nan"), 1), "finite numbers"),
        (DevelopmentInputRequest.tap(-1, 1), "outside"),
        (DevelopmentInputRequest.tap(1080, 1), "outside"),
        (DevelopmentInputRequest.tap(1, 1920), "outside"),
        (
            DevelopmentInputRequest.swipe(1, 2, 3, 4, 0),
            "swipe duration",
        ),
        (
            DevelopmentInputRequest.swipe(1, 2, 3, 4, 5001),
            "swipe duration",
        ),
    ],
)
def test_invalid_coordinates_and_duration_reject_before_status_or_adb(
    input_request,
    message,
):
    result, reader, adb, audit = _execute(input_request)

    assert result.exit_code == EXIT_REJECTED
    assert message in result.message
    assert reader.calls == 0
    assert adb.geometry_targets == []
    assert adb.input_calls == []
    assert [event[0] for event in audit.events] == ["ACTION", "RESULT"]


@pytest.mark.parametrize(
    ("input_error", "message", "outcome"),
    [
        (
            AdbInputError(
                "ADB input returned nonzero status and will not be retried",
                outcome="nonzero",
            ),
            "nonzero",
            "nonzero",
        ),
        (OSError("adb executable disappeared"), "exception", "exception"),
        (
            subprocess.TimeoutExpired(["adb"], 5),
            "timed out",
            "timeout",
        ),
    ],
)
def test_adb_nonzero_exception_and_timeout_are_never_retried(
    input_error,
    message,
    outcome,
):
    adb = FakeAdb(input_error=input_error)

    result, reader, adb, audit = _execute(
        DevelopmentInputRequest.tap(100, 200),
        adb=adb,
    )

    assert result.exit_code == EXIT_ADB_FAILURE
    assert message in result.message
    assert reader.calls == 2
    assert len(adb.input_calls) == 1
    assert audit.events[1][0] == "INPUT"
    assert audit.events[1][1]["outcome"] == outcome
    assert audit.events[2][0] == "RESULT"


def test_geometry_failure_never_reaches_mutating_adb_command():
    adb = FakeAdb(geometry_error=AdbReadError("malformed screenshot"))

    result, reader, adb, audit = _execute(
        DevelopmentInputRequest.tap(100, 200),
        adb=adb,
    )

    assert result.exit_code == EXIT_ADB_FAILURE
    assert "geometry" in result.message
    assert reader.calls == 1
    assert adb.input_calls == []
    assert [event[0] for event in audit.events] == ["ACTION", "RESULT"]


def test_required_action_intent_failure_prevents_status_and_adb():
    reader = SequenceStatusReader(_active_status())
    adb = FakeAdb()

    result = execute_development_input(
        DevelopmentInputRequest.tap(100, 200),
        lease_id=LEASE_ID,
        status_reader=reader,
        adb=adb,
        audit=RecordingAudit(fail="intent"),
    )

    assert result.exit_code == EXIT_AUDIT_FAILURE
    assert "unable to write" in result.message
    assert reader.calls == 0
    assert adb.geometry_targets == []
    assert adb.input_calls == []


def test_action_input_result_use_only_selected_audit_path(tmp_path, monkeypatch):
    environment_log = tmp_path / "environment" / "actions.log"
    selected_log = tmp_path / "production" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(environment_log))

    result, _reader, adb, _audit = _execute(
        DevelopmentInputRequest.swipe(270, 1440, 810, 480, 260),
        adb=FakeAdb(geometry=(720, 1280)),
        audit=ActionLogAudit(selected_log),
    )

    assert result.exit_code == EXIT_SUCCESS
    assert len(adb.input_calls) == 1
    lines = selected_log.read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[0] for line in lines] == [
        "[ACTION",
        "[DEBUG",
        "[INPUT",
        "[DEBUG",
        "[RESULT",
        "[DEBUG",
    ]
    input_detail = lines[3]
    assert f"lease_id={LEASE_ID}" in input_detail
    assert f"target={TARGET}" in input_detail
    assert "canonical=(270,1440)->(810,480)" in input_detail
    assert "device=(180,960)->(540,320)" in input_detail
    assert "outcome=completed" in input_detail
    assert not environment_log.exists()


@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280)])
def test_subprocess_geometry_capture_is_bounded_and_exact_target(size):
    width, height = size
    frame = np.full((height, width, 3), 64, dtype=np.uint8)
    encoded, payload = cv2.imencode(".png", frame)
    assert encoded is True
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command, **kwargs):
        calls.append((list(command), kwargs))
        return subprocess.CompletedProcess(command, 0, payload.tobytes(), b"")

    boundary = SubprocessAdbBoundary(run=run, read_timeout_seconds=7)

    assert boundary.acquire_geometry(TARGET) == size
    assert calls[0][0] == [
        "adb",
        "-s",
        TARGET,
        "exec-out",
        "screencap",
        "-p",
    ]
    assert calls[0][1]["timeout"] == 7
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("behavior", "outcome"),
    [
        ("nonzero", "nonzero"),
        ("exception", "exception"),
        ("timeout", "timeout"),
    ],
)
def test_subprocess_input_uses_exact_target_finite_timeout_and_no_retry(
    behavior,
    outcome,
):
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(list(command))
        assert kwargs["timeout"] == 4
        if behavior == "exception":
            raise OSError("missing adb")
        if behavior == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 1, b"", b"device offline")

    boundary = SubprocessAdbBoundary(run=run, input_timeout_seconds=4)

    with pytest.raises(AdbInputError) as failure:
        boundary.run_input(TARGET, ["input", "tap", "10", "20"])

    assert failure.value.outcome == outcome
    assert calls == [
        ["adb", "-s", TARGET, "shell", "input", "tap", "10", "20"]
    ]


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload


def test_http_status_reader_accepts_bounded_json_object():
    calls: list[tuple[str, float]] = []

    def opener(request, *, timeout):
        calls.append((request.full_url, timeout))
        return _Response(json.dumps(_active_status()).encode("utf-8"))

    status = fetch_control_status(timeout_seconds=2, opener=opener)

    assert status["interactive_development_lease"]["active"] is True
    assert calls == [("http://127.0.0.1:8787/api/v1/status", 2.0)]


def test_http_status_reader_rejects_malformed_response():
    with pytest.raises(LeaseStatusError, match="malformed JSON"):
        fetch_control_status(opener=lambda *_args, **_kwargs: _Response(b"not-json"))


def test_cli_tap_success_and_wrong_lease_exit_codes(tmp_path, capsys):
    success_reader = SequenceStatusReader(_active_status(), _active_status())
    success_adb = FakeAdb()
    success_code = cli.main(
        [
            "--lease-id",
            LEASE_ID,
            "--action-log",
            str(tmp_path / "actions.log"),
            "tap",
            "540",
            "960",
        ],
        status_reader=success_reader,
        adb=success_adb,
        audit=RecordingAudit(),
    )
    success_output = capsys.readouterr()

    assert success_code == EXIT_SUCCESS
    assert "Completed one tap" in success_output.out
    assert success_output.err == ""
    assert len(success_adb.input_calls) == 1

    rejected_adb = FakeAdb()
    rejected_code = cli.main(
        [
            "--lease-id",
            OTHER_LEASE_ID,
            "--action-log",
            str(tmp_path / "actions.log"),
            "swipe",
            "10",
            "20",
            "30",
            "40",
            "250",
        ],
        status_reader=SequenceStatusReader(_active_status()),
        adb=rejected_adb,
        audit=RecordingAudit(),
    )
    rejected_output = capsys.readouterr()

    assert rejected_code == EXIT_REJECTED
    assert "Rejected" in rejected_output.err
    assert rejected_adb.input_calls == []


def test_cli_help_and_usage_errors_are_useful(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as help_exit:
        parser.parse_args(["--help"])
    help_output = capsys.readouterr().out
    normalized_help = " ".join(help_output.split())
    assert help_exit.value.code == 0
    assert "exactly one canonical-coordinate" in normalized_help
    assert "Uncertain input is never retried" in normalized_help
    assert "tap" in help_output
    assert "swipe" in help_output

    with pytest.raises(SystemExit) as usage_exit:
        cli.main(["--lease-id", LEASE_ID, "tap", "1"])
    usage_output = capsys.readouterr().err
    assert usage_exit.value.code == 2
    assert "the following arguments are required: y" in usage_output


def test_cli_default_audit_path_is_production_not_worktree():
    expected = Path("/home/brianm/dev/python/TheTower/logs/actions.log")

    assert cli.production_action_log_path() == expected
    args = cli.build_parser().parse_args(
        ["--lease-id", LEASE_ID, "tap", "10", "20"]
    )
    assert Path(args.action_log) == expected
    assert Path(args.action_log) != Path.cwd() / "logs" / "actions.log"
