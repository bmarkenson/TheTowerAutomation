from core.adb_target_session import AdbTargetSnapshot
from core.player_save_serialization import (
    GuardedPlayerSaveSerializer,
    GuardedSerializationStatus,
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
