from core.adb_target_session import AdbTargetSnapshot
from core.player_save_serialization import (
    GuardedPlayerSaveSerializer,
    GuardedSerializationStatus,
)


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
    assert result.reason == "background_serialization_boundary_failed"
    assert result.lifecycle_input_attempted is True
    assert result.background_dispatched is False
    assert result.source_restored is False
    assert result.acquisition is None
    assert foreground == []


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
        (None, True),
        (None, True),
        (None, True),
    ]


def test_restoration_authority_loss_interrupts_retry_immediately():
    initial_frame = object()
    authority = iter((True, True, True, False))
    source_checks = []

    def source_guard(frame, stable):
        source_checks.append((frame, stable))
        return frame is initial_frame

    serializer = _serializer(
        source_guard_fn=source_guard,
        action_guard_fn=lambda: next(authority),
    )

    result = serializer.acquire(
        expected_target="private-target",
        expected_generation=3,
        target_generation_detail="private-generation",
        source_label="active battle",
        initial_frame=initial_frame,
    )

    assert result.status is GuardedSerializationStatus.BLOCKED
    assert result.reason == "restored_control_authority_interrupted"
    assert result.background_dispatched is True
    assert source_checks == [
        (initial_frame, False),
        (None, True),
    ]
