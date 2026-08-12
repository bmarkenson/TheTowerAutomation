import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.adb_target_session import AdbTargetSnapshot
from core.adb_utils import AdbShellDispatchOutcome
from core.player_save_serialization import (
    GuardedPlayerSaveSerializer,
    GuardedSerializationStatus,
)
from core.run_state import AUTOMATION, RunState


def _serializer(
    *,
    source_guard_fn,
    action_guard_fn=lambda: True,
    sleep_fn=lambda _seconds: None,
    monotonic_fn=lambda: 0.0,
    restoration_timeout_seconds=12.0,
    restoration_retry_interval_seconds=0.5,
    restoration_max_attempts=6,
    debug_log_fn=lambda *_args, **_kwargs: None,
):
    return GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=action_guard_fn,
        source_guard_fn=source_guard_fn,
        background_fn=lambda _target: True,
        foreground_fn=lambda _target: True,
        pull_fn=lambda **_kwargs: b"stable-save",
        decode_fn=lambda _payload, **_kwargs: object(),
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
        restoration_timeout_seconds=restoration_timeout_seconds,
        restoration_retry_interval_seconds=(
            restoration_retry_interval_seconds
        ),
        restoration_max_attempts=restoration_max_attempts,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=debug_log_fn,
    )


def test_failed_home_dispatch_still_reports_attempted_source_mutation():
    foreground = []
    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: True,
        source_guard_fn=lambda _frame, _stable: True,
        background_fn=lambda _target: False,
        foreground_fn=lambda target: foreground.append(target) or True,
        sleep_fn=lambda _seconds: None,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=object(),
    )

    assert result.status is GuardedSerializationStatus.BLOCKED
    assert result.reason == "background_serialization_dispatch_uncertain"
    assert result.lifecycle_input_attempted is True
    assert result.background_dispatched is False
    assert result.source_restored is True
    assert result.acquisition is None
    assert foreground == ["private-target"]


def test_definite_pre_dispatch_home_failure_does_not_attempt_restoration():
    foreground = []
    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: True,
        source_guard_fn=lambda _frame, _stable: True,
        background_fn=lambda _target: AdbShellDispatchOutcome(),
        foreground_fn=lambda target: foreground.append(target) or True,
        sleep_fn=lambda _seconds: None,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=object(),
    )

    assert result.status is GuardedSerializationStatus.BLOCKED
    assert result.reason == "background_serialization_dispatch_unavailable"
    assert result.lifecycle_input_attempted is False
    assert result.background_dispatched is False
    assert result.source_restored is False
    assert foreground == []


def test_uncertain_home_dispatch_restores_before_reporting_boundary_failure():
    foreground = []
    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: True,
        source_guard_fn=lambda _frame, _stable: True,
        background_fn=lambda _target: AdbShellDispatchOutcome(
            attempted=True,
            uncertain=True,
        ),
        foreground_fn=lambda target: foreground.append(target) or True,
        sleep_fn=lambda _seconds: None,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=object(),
    )

    assert result.status is GuardedSerializationStatus.BLOCKED
    assert result.reason == "background_serialization_dispatch_uncertain"
    assert result.lifecycle_input_attempted is True
    assert result.background_dispatched is False
    assert result.source_restored is True
    assert foreground == ["private-target"]


def test_production_adb_timeout_does_not_latch_after_verified_restoration():
    uncertain_results = []
    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: True,
        source_guard_fn=lambda _frame, _stable: True,
        pull_fn=lambda **_kwargs: b"stable-save",
        decode_fn=lambda _payload, **_kwargs: object(),
        sleep_fn=lambda _seconds: None,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )
    AUTOMATION.state = RunState.RUNNING
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=uncertain_results.append,
    )
    try:
        with patch(
            "core.adb_utils.subprocess.run",
            side_effect=(
                subprocess.TimeoutExpired("adb", 10),
                SimpleNamespace(returncode=0),
            ),
        ):
            result = serializer.acquire(
                expected_target="private-target",
                expected_generation=3,
                target_generation_detail="private-generation",
                source_label="active battle",
                initial_frame=object(),
            )
    finally:
        AUTOMATION.clear_mutation_guard(token)

    assert result.status is GuardedSerializationStatus.BLOCKED
    assert result.reason == "background_serialization_dispatch_uncertain"
    assert result.source_restored is True
    assert uncertain_results == []
    assert AUTOMATION.state is RunState.RUNNING


def test_uncertain_foreground_dispatch_uses_bounded_restoration_evidence():
    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: True,
        source_guard_fn=lambda _frame, _stable: True,
        background_fn=lambda _target: True,
        foreground_fn=lambda _target: AdbShellDispatchOutcome(
            attempted=True,
            uncertain=True,
        ),
        pull_fn=lambda **_kwargs: b"stable-save",
        decode_fn=lambda _payload, **_kwargs: object(),
        sleep_fn=lambda _seconds: None,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=object(),
    )

    assert result.status is GuardedSerializationStatus.BLOCKED
    assert result.reason == "foreground_restoration_dispatch_uncertain"
    assert result.lifecycle_input_attempted is True
    assert result.background_dispatched is True
    assert result.source_restored is True


def test_definite_pre_dispatch_foreground_failure_is_unrestored():
    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: True,
        source_guard_fn=lambda _frame, _stable: True,
        background_fn=lambda _target: True,
        foreground_fn=lambda _target: AdbShellDispatchOutcome(),
        pull_fn=lambda **_kwargs: b"stable-save",
        decode_fn=lambda _payload, **_kwargs: object(),
        sleep_fn=lambda _seconds: None,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=object(),
    )

    assert result.status is GuardedSerializationStatus.BLOCKED
    assert result.reason == "foreground_restoration_failed"
    assert result.lifecycle_input_attempted is True
    assert result.background_dispatched is True
    assert result.source_restored is False


def test_shutdown_interrupt_after_background_restores_before_propagating():
    lifecycle = []
    uncertain_results = []

    def interrupt_acquisition(**_kwargs):
        lifecycle.append("acquire")
        raise KeyboardInterrupt()

    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: True,
        source_guard_fn=lambda _frame, _stable: True,
        background_fn=lambda _target: lifecycle.append("background") or True,
        foreground_fn=lambda _target: lifecycle.append("foreground") or True,
        pull_fn=interrupt_acquisition,
        decode_fn=lambda _payload, **_kwargs: object(),
        sleep_fn=lambda _seconds: None,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )

    AUTOMATION.state = RunState.RUNNING
    token = AUTOMATION.install_mutation_guard(
        lambda: True,
        uncertain_result_handler=uncertain_results.append,
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            serializer.acquire(
                expected_target="private-target",
                expected_generation=3,
                target_generation_detail="private-generation",
                source_label="active battle",
                initial_frame=object(),
            )
    finally:
        AUTOMATION.clear_mutation_guard(token)

    assert lifecycle == ["background", "acquire", "foreground"]
    assert uncertain_results == []
    assert AUTOMATION.state is RunState.RUNNING


def test_interrupted_launcher_dispatch_is_retried_before_shutdown():
    lifecycle = []
    foreground_calls = 0

    def foreground(_target):
        nonlocal foreground_calls
        foreground_calls += 1
        lifecycle.append(f"foreground-{foreground_calls}")
        if foreground_calls == 1:
            raise KeyboardInterrupt()
        return True

    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: True,
        source_guard_fn=lambda _frame, _stable: True,
        background_fn=lambda _target: lifecycle.append("background") or True,
        foreground_fn=foreground,
        pull_fn=lambda **_kwargs: b"stable-save",
        decode_fn=lambda _payload, **_kwargs: object(),
        sleep_fn=lambda _seconds: None,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(KeyboardInterrupt):
        serializer.acquire(
            expected_target="private-target",
            expected_generation=3,
            target_generation_detail="private-generation",
            source_label="active battle",
            initial_frame=object(),
        )

    assert lifecycle == ["background", "foreground-1", "foreground-2"]


def test_restoration_waits_for_stable_source_convergence():
    initial_frame = object()
    restored_results = iter((False, True))
    source_checks = []
    sleeps = []
    now = [0.0]
    diagnostics = []

    def source_guard(frame, stable):
        source_checks.append((frame, stable))
        if frame is initial_frame:
            return True
        return next(restored_results)

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    serializer = _serializer(
        source_guard_fn=source_guard,
        sleep_fn=sleep,
        monotonic_fn=lambda: now[0],
        debug_log_fn=lambda message, _level: diagnostics.append(message),
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=initial_frame,
    )

    assert result.complete
    assert source_checks == [
        (initial_frame, False),
        (initial_frame, False),
        (None, True),
        (None, True),
    ]
    assert sleeps == [0.25, 0.5, 0.5]
    assert any(
        "result=source_not_yet_stable attempt=1/6" in message
        for message in diagnostics
    )
    assert any(
        "result=verified attempt=2/6" in message
        for message in diagnostics
    )


def test_restoration_source_convergence_timeout_is_bounded():
    initial_frame = object()
    source_checks = []
    now = [0.0]

    def source_guard(frame, stable):
        source_checks.append((frame, stable))
        return frame is initial_frame

    def sleep(seconds):
        now[0] += seconds

    serializer = _serializer(
        source_guard_fn=source_guard,
        sleep_fn=sleep,
        monotonic_fn=lambda: now[0],
        restoration_timeout_seconds=1.0,
        restoration_retry_interval_seconds=0.25,
        restoration_max_attempts=10,
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=initial_frame,
    )

    assert result.status is GuardedSerializationStatus.BLOCKED
    assert result.reason == "restored_source_convergence_timeout"
    assert result.background_dispatched is True
    assert source_checks == [
        (initial_frame, False),
        (initial_frame, False),
        (None, True),
        (None, True),
        (None, True),
    ]


def test_restoration_logger_failure_cannot_reclassify_verified_source():
    initial_frame = object()

    def debug_log(message, _level):
        if "restored source convergence" in message:
            raise OSError("diagnostic disk full")

    serializer = _serializer(
        source_guard_fn=lambda _frame, _stable: True,
        debug_log_fn=debug_log,
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=initial_frame,
    )

    assert result.complete
    assert result.source_restored is True


def test_pause_persisted_during_prechecks_blocks_first_lifecycle_input():
    allowed = [True]
    background = []

    def input_log(*_args, **_kwargs):
        allowed[0] = False

    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: allowed[0],
        source_guard_fn=lambda _frame, _stable: True,
        background_fn=lambda target: background.append(target) or True,
        foreground_fn=lambda _target: True,
        pull_fn=lambda **_kwargs: b"stable-save",
        decode_fn=lambda _payload, **_kwargs: object(),
        sleep_fn=lambda _seconds: None,
        input_log_fn=input_log,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=object(),
    )

    assert result.status is GuardedSerializationStatus.BLOCKED
    assert result.reason == "control_authority_interrupted_before_background"
    assert result.lifecycle_input_attempted is False
    assert background == []


def test_pause_waits_for_required_foreground_restoration():
    original_state = AUTOMATION.state
    foreground_started = threading.Event()
    release_foreground = threading.Event()
    pause_applied = threading.Event()
    lifecycle = []
    result_holder = []

    def foreground(_target):
        lifecycle.append("foreground")
        foreground_started.set()
        assert release_foreground.wait(timeout=2)
        return True

    serializer = GuardedPlayerSaveSerializer(
        target_snapshot_fn=lambda: AdbTargetSnapshot(
            "private-target",
            3,
            True,
        ),
        context_guard_fn=lambda: True,
        action_guard_fn=lambda: True,
        source_guard_fn=lambda _frame, _stable: True,
        background_fn=lambda _target: lifecycle.append("background") or True,
        foreground_fn=foreground,
        pull_fn=lambda **_kwargs: b"stable-save",
        decode_fn=lambda _payload, **_kwargs: object(),
        sleep_fn=lambda _seconds: None,
        input_log_fn=lambda *_args, **_kwargs: None,
        debug_log_fn=lambda *_args, **_kwargs: None,
    )

    def serialize():
        result_holder.append(
            serializer.acquire(
                expected_target="private-target",
                expected_generation=3,
                target_generation_detail="private-generation",
                source_label="active battle",
                initial_frame=object(),
            )
        )

    def pause():
        AUTOMATION.state = RunState.PAUSED
        pause_applied.set()

    AUTOMATION.state = RunState.RUNNING
    serialization_thread = threading.Thread(target=serialize)
    pause_thread = threading.Thread(target=pause)
    try:
        serialization_thread.start()
        assert foreground_started.wait(timeout=2)
        pause_thread.start()
        assert not pause_applied.wait(timeout=0.05)

        release_foreground.set()
        serialization_thread.join(timeout=2)
        pause_thread.join(timeout=2)

        assert not serialization_thread.is_alive()
        assert not pause_thread.is_alive()
        assert pause_applied.is_set()
        assert lifecycle == ["background", "foreground"]
        assert result_holder[0].complete
        assert AUTOMATION.state is RunState.PAUSED
    finally:
        release_foreground.set()
        serialization_thread.join(timeout=2)
        pause_thread.join(timeout=2)
        AUTOMATION.state = original_state
