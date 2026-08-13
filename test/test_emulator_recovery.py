from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from core.control_directives import ControlDirectiveStore
from core.app import App
from core.action_authority import AuthorityHold
from core.battle_lifecycle import HomeBattleControl
from core.emulator_degradation import (
    assess_emulator_degradation,
    load_comparable_battles,
)
from core.emulator_recovery import (
    EMULATOR_HOME_POSTCONDITION_TIMEOUT_SECONDS,
    RecoveryUiDispatchOutcome,
    RecoveryUiDispatchStatus,
    RestartReplayWindow,
    normalize_emulator_maintenance,
    normalize_runtime_recovery_ack,
)
from core.runtime_failure_policy import RuntimeFailureKind
from core.ss_capture import ScreenshotCaptureResult, ScreenshotFailure
from handlers.game_restarted_handler import (
    GameRestartedAction,
    handle_game_restarted,
)


REQUEST_ID = "0123456789abcdef0123456789abcdef"
RUNTIME = {
    "runtime_id": "runtime-recovery",
    "pid": 1234,
    "adb_target": "localhost:5555",
    "target_generation": 7,
    "state_request_id": "state-enable-1",
}
HOST_TARGET = {
    "host_id": "WINDOWS-HOST",
    "adb_port": 5555,
    "process_id": 90,
    "process_started_at": "2026-08-10T10:00:00+00:00",
    "executable_path": r"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
    "instance_name": "Nougat32",
    "observed_at": "2026-08-10T10:00:30+00:00",
}


def test_restart_replay_window_holds_until_the_actual_high_water():
    replay = RestartReplayWindow(
        REQUEST_ID,
        100,
        battle_scope="run-1",
    )
    replay.mark_resume_dispatched()

    rolled_back = replay.observe(95)
    assert rolled_back.active is True
    assert rolled_back.caught_up is False
    assert rolled_back.regressed is True
    assert rolled_back.expected_floor == 95
    assert replay.observe(99).active is True

    caught_up = replay.observe(100)
    assert caught_up.caught_up is True
    assert caught_up.active is False
    assert replay.as_dict() == {
        "request_id": REQUEST_ID,
        "request_initiator": "automatic_detector",
        "battle_scope": "run-1",
        "high_water_wave": 100,
        "intro_sprint_active": False,
        "expected_rollback_waves": 5,
        "expected_floor": 95,
        "resume_dispatched": True,
        "replay_active": False,
        "caught_up": True,
        "lowest_observed_wave": 95,
        "exclude_from_degradation": True,
    }


def test_restart_replay_window_retains_operator_initiator():
    replay = RestartReplayWindow(
        REQUEST_ID,
        100,
        request_initiator="operator",
        battle_scope="run-operator",
    )

    assert replay.as_dict()["request_initiator"] == "operator"


def test_restart_replay_window_uses_intro_sprint_only_as_expected_floor():
    replay = RestartReplayWindow(
        REQUEST_ID,
        700,
        battle_scope="run-intro",
        intro_sprint_active=True,
    )
    replay.mark_resume_dispatched()

    observation = replay.observe(647)

    assert observation.expected_floor == 650
    assert observation.active is True
    assert replay.expected_rollback_waves == 50
    assert replay.observe(700).caught_up is True


def test_replay_without_trusted_wave_finishes_on_first_fresh_running_wave():
    replay = RestartReplayWindow(
        REQUEST_ID,
        None,
        battle_scope="run-no-wave",
    )
    replay.mark_resume_dispatched()

    assert replay.observe(None).caught_up is False
    assert replay.observe(12).caught_up is True


def test_emulator_maintenance_directive_lifecycle_is_idempotent(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    control = store.set_state("RUNNING", source="test")
    runtime = {
        **RUNTIME,
        "state_request_id": control["state_request_id"],
    }
    request = store.request_emulator_maintenance(
        reason="confirmed degradation",
        source="test",
        runtime=runtime,
        battle_scope="run-1",
        host_target=HOST_TARGET,
        trigger={"candidate_cph_ratio": 0.88},
        now=1_000.0,
    )
    assert request["state"] == "requested"
    assert normalize_emulator_maintenance(request) == request
    host_ack = {
        "host_id": "WINDOWS-HOST",
        "adb_port": 5555,
        "process_id": 90,
        "process_started_at": "2026-08-10T10:00:00+00:00",
        "executable_path": r"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
        "instance_name": "Nougat32",
        "observed_at": "2026-08-10T10:01:00+00:00",
    }
    acknowledged = store.acknowledge_emulator_maintenance_host(
        request["request_id"],
        host_ack=host_ack,
        now=1_001.0,
    )
    repeated_ack = store.acknowledge_emulator_maintenance_host(
        request["request_id"],
        host_ack={
            **host_ack,
            "observed_at": "2026-08-10T10:02:00+00:00",
        },
        now=1_002.0,
    )
    assert repeated_ack == acknowledged

    with pytest.raises(ValueError, match="must prove a new process"):
        store.complete_emulator_maintenance_host(
            request["request_id"],
            host_completion={
                **host_ack,
                "previous_process_id": host_ack["process_id"],
                "previous_process_started_at": host_ack["process_started_at"],
            },
            now=1_002.5,
        )

    completion = {
        "host_id": "WINDOWS-HOST",
        "adb_port": 5555,
        "process_id": 91,
        "process_started_at": "2026-08-10T10:03:00+00:00",
        "previous_process_id": 90,
        "previous_process_started_at": "2026-08-10T10:00:00+00:00",
        "executable_path": r"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
        "instance_name": "Nougat32",
        "observed_at": "2026-08-10T10:03:05+00:00",
    }
    restarted = store.complete_emulator_maintenance_host(
        request["request_id"],
        host_completion=completion,
        now=1_003.0,
    )
    repeated_completion = store.complete_emulator_maintenance_host(
        request["request_id"],
        host_completion={
            **completion,
            "observed_at": "2026-08-10T10:04:00+00:00",
        },
        now=1_004.0,
    )
    assert repeated_completion == restarted

    terminal = store.finish_emulator_maintenance(
        request["request_id"],
        disposition="resumed",
        reason="caught up",
        source="test-runtime",
        now=1_005.0,
    )
    assert terminal["state"] == "terminal"
    assert terminal["terminal_disposition"] == "resumed"
    assert store.finish_emulator_maintenance(
        request["request_id"],
        disposition="different-result",
        reason="must not overwrite",
        source="test-runtime",
        now=1_006.0,
    ) == terminal


def test_present_malformed_or_operator_unbound_host_target_is_invalid():
    base = {
        "schema_version": 1,
        "request_id": REQUEST_ID,
        "action": "restart_bluestacks",
        "state": "requested",
        "reason": "operator requested restart",
        "source": "test",
        "initiator": "operator",
        "requested_at": "2026-08-10T10:00:00+00:00",
        "updated_at": "2026-08-10T10:00:00+00:00",
        "runtime": RUNTIME,
        "battle_scope": "run-1",
        "trigger": {"request_kind": "operator"},
    }

    assert normalize_emulator_maintenance(base) is None
    assert normalize_emulator_maintenance(
        {**base, "host_target": {"process_id": 90}}
    ) is None


def test_pause_wins_before_host_ack_but_ack_before_pause_is_durable(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    enabled = store.set_state("RUNNING", source="test")
    runtime = {
        **RUNTIME,
        "state_request_id": enabled["state_request_id"],
    }
    request = store.request_emulator_maintenance(
        reason="confirmed degradation",
        source="test",
        runtime=runtime,
        battle_scope="run-1",
        host_target=HOST_TARGET,
    )
    host_ack = {
        "host_id": "WINDOWS-HOST",
        "adb_port": 5555,
        "process_id": 90,
        "process_started_at": "2026-08-10T10:00:00+00:00",
        "executable_path": r"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
        "instance_name": "Nougat32",
        "observed_at": "2026-08-10T10:01:00+00:00",
    }

    store.set_state("PAUSED", source="test")
    with pytest.raises(ValueError, match="Enabled control boundary"):
        store.acknowledge_emulator_maintenance_host(
            request["request_id"],
            host_ack=host_ack,
        )

    enabled = store.set_state("RUNNING", source="test")
    replacement_request = store.finish_emulator_maintenance(
        request["request_id"],
        disposition="cancelled",
        reason="test boundary",
        source="test",
    )
    assert replacement_request["state"] == "terminal"
    request = store.request_emulator_maintenance(
        reason="confirmed degradation",
        source="test",
        runtime={
            **RUNTIME,
            "state_request_id": enabled["state_request_id"],
        },
        battle_scope="run-2",
        host_target=HOST_TARGET,
    )
    acknowledged = store.acknowledge_emulator_maintenance_host(
        request["request_id"],
        host_ack=host_ack,
    )
    store.set_state("PAUSED", source="test")

    assert acknowledged["state"] == "host_acknowledged"
    assert store.status()["emulator_maintenance"]["state"] == (
        "host_acknowledged"
    )


def test_maintenance_and_runtime_ack_require_exact_runtime_and_scope():
    maintenance = {
        "schema_version": 1,
        "request_id": REQUEST_ID,
        "action": "restart_bluestacks",
        "state": "requested",
        "reason": "degraded",
        "source": "test",
        "requested_at": "2026-08-10T10:00:00+00:00",
        "updated_at": "2026-08-10T10:00:00+00:00",
        "runtime": RUNTIME,
        "battle_scope": "run-1",
    }
    assert normalize_emulator_maintenance(maintenance) is not None
    assert normalize_emulator_maintenance(
        {**maintenance, "battle_scope": None}
    ) is None
    assert normalize_emulator_maintenance(
        {**maintenance, "runtime": {**RUNTIME, "adb_target": "unknown"}}
    ) is None

    runtime_ack = {
        "schema_version": 1,
        "request_id": REQUEST_ID,
        "state": "replaying",
        "runtime": RUNTIME,
        "battle_scope": "run-1",
        "observed_at": "2026-08-10T10:05:00+00:00",
        "replay_active": True,
        "exclude_from_degradation": True,
    }
    assert normalize_runtime_recovery_ack(runtime_ack) is not None
    assert normalize_runtime_recovery_ack(
        {**runtime_ack, "request_id": "wrong"}
    ) is None
    assert normalize_runtime_recovery_ack(
        {**runtime_ack, "battle_scope": None}
    ) is None


def test_game_restarted_handler_dispatches_only_from_fresh_modal_evidence():
    frame = np.full((1920, 1080, 3), 255, dtype=np.uint8)
    with (
        patch(
            "handlers.game_restarted_handler.detect_state_and_overlays",
            side_effect=(
                {"state": "GAME_RESTARTED"},
                {"state": "GAME_RESTARTED"},
                {"state": "HOME_SCREEN"},
            ),
        ),
        patch(
            "handlers.game_restarted_handler.safe_tap",
            return_value=True,
        ) as tap,
    ):
        assert handle_game_restarted(
            frame,
            action=GameRestartedAction.RESUME,
            action_guard_fn=lambda: True,
            capture_fn=lambda: frame,
            poll_interval_s=0,
        )
    assert tap.call_args.args == ("buttons.resume_game:game_restarted",)
    assert tap.call_args.kwargs["dispatch"] == "now"
    assert tap.call_args.kwargs["retries"] == 0

    with (
        patch(
            "handlers.game_restarted_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch("handlers.game_restarted_handler.safe_tap") as refused,
    ):
        assert not handle_game_restarted(
            frame,
            action=GameRestartedAction.END_RUN,
        )
    refused.assert_not_called()


def _battle(
    battle_id: str,
    cph: str,
    *,
    speed: str = "6.0",
    fingerprint: str = "same-config",
) -> dict:
    return {
        "battle_id": battle_id,
        "strategy": "farm_t18",
        "configuration_fingerprint": fingerprint,
        "coins_per_hour": cph,
        "effective_game_speed": speed,
    }


def _host_aggregates(
    now: datetime,
    *,
    saturated: bool = False,
) -> list[dict]:
    aggregates = []
    for index in range(121):
        handles = 1_000 if index == 0 else 6_000
        aggregates.append(
            {
                "session_id": "sampler-a" if index < 60 else "sampler-b",
                "sample_count": 10,
                "sample_interval_ms": 1_000,
                "bluestacks_listener": {
                    "host_id": "ALIEN",
                    "adb_port": 5555,
                    "process_id": 90,
                    "process_started_at": "2026-08-10T10:00:00+00:00",
                    "executable_path": (
                        "C:\\Program Files\\BlueStacks_nxt\\HD-Player.exe"
                    ),
                    "instance_name": "Nougat32",
                },
                "window_end_utc": (
                    now - timedelta(seconds=(120 - index) * 10)
                ).isoformat(),
                "metrics": {
                    "bluestacks_handle_count_avg": handles,
                    "bluestacks_process_count_min": 3,
                    "bluestacks_process_count_max": 3,
                    "host_cpu_percent_max": 98 if saturated else 55,
                    "host_memory_used_percent_max": 70,
                },
            }
        )
    return aggregates


def test_degradation_requires_two_slow_runs_normal_speed_and_host_growth():
    now = datetime(2026, 8, 10, 20, 0, tzinfo=timezone.utc)
    battles = [
        _battle("candidate-1", "80"),
        _battle("candidate-2", "82"),
        _battle("baseline-1", "100"),
        _battle("baseline-2", "101"),
        _battle("baseline-3", "99"),
        _battle("baseline-4", "100"),
        _battle("baseline-5", "100"),
    ]

    assessment = assess_emulator_degradation(
        battles,
        _host_aggregates(now),
        current_strategy="farm_t18",
        current_run_id="run-1",
        assessed_at=now,
    )

    assert assessment["status"] == "automatic_ready"
    assert assessment["automatic_ready"] is True
    assert assessment["candidate_battle_ids"] == [
        "candidate-1",
        "candidate-2",
    ]
    assert assessment["host_evidence"]["handle_delta"] == 5000.0
    assert assessment["host_evidence"]["identity_scope"] == (
        "exact_listener_lifetime"
    )
    assert assessment["host_evidence"]["sampler_session_count"] == 2
    assert assessment["host_evidence"]["stable_process_windows"] == 121

    saturated = assess_emulator_degradation(
        battles,
        _host_aggregates(now, saturated=True),
        current_strategy="farm_t18",
        current_run_id="run-1",
        assessed_at=now,
    )
    assert saturated["status"] == "deferred_host_contention"
    assert saturated["automatic_ready"] is False

    missing_contention_evidence = _host_aggregates(now)
    for aggregate in missing_contention_evidence:
        aggregate["metrics"].pop("host_cpu_percent_max")
    incomplete = assess_emulator_degradation(
        battles,
        missing_contention_evidence,
        current_strategy="farm_t18",
        current_run_id="run-1",
        assessed_at=now,
    )
    assert incomplete["status"] == "recommend"
    assert incomplete["automatic_ready"] is False

    legacy_evidence = _host_aggregates(now)
    for aggregate in legacy_evidence:
        aggregate.pop("bluestacks_listener")
    unbound = assess_emulator_degradation(
        battles,
        legacy_evidence,
        current_strategy="farm_t18",
        current_run_id="run-1",
        assessed_at=now,
    )
    assert unbound["status"] == "recommend"
    assert unbound["host_evidence"]["status"] == "unavailable"

    healthy = assess_emulator_degradation(
        [_battle("candidate-1", "98"), *battles[1:]],
        _host_aggregates(now),
        current_strategy="farm_t18",
        current_run_id="run-1",
        assessed_at=now,
    )
    assert healthy["status"] == "healthy"


def test_degradation_requires_sampled_coverage_not_partial_window_count():
    now = datetime(2026, 8, 10, 20, 0, tzinfo=timezone.utc)
    battles = [
        _battle("candidate-1", "80"),
        _battle("candidate-2", "82"),
        _battle("baseline-1", "100"),
        _battle("baseline-2", "101"),
        _battle("baseline-3", "99"),
    ]
    partials = _host_aggregates(now)
    for aggregate in partials:
        aggregate["sample_count"] = 1

    assessment = assess_emulator_degradation(
        battles,
        partials,
        current_strategy="farm_t18",
        current_run_id="run-1",
        assessed_at=now,
    )

    assert assessment["status"] == "recommend"
    assert assessment["host_evidence"]["status"] == "insufficient"
    assert assessment["host_evidence"]["sampled_coverage_seconds"] == 121.0


def test_completed_recovery_runs_do_not_calibrate_degradation(tmp_path):
    battles_dir = tmp_path / "battles"
    battles_dir.mkdir()

    def write(name: str, captured_at: str, *, recovered: bool = False):
        record = {
            "schema_version": 6,
            "battle_id": name,
            "captured_at": captured_at,
            "strategy": "farm_t18",
            "battle_type": "farm",
            "run_configuration": {"profile": "farm"},
            "more_stats": {
                "sections": [
                    {
                        "key": "battle_report",
                        "rows": [
                            {
                                "key": "coins_per_hour",
                                "value_raw": "1.25Q",
                                "value_decimal": "1250000000000000000",
                            }
                        ],
                    }
                ]
            },
            "derived": {"effective_game_speed": 6.0},
            "runtime": (
                {"emulator_recovery": {"request_id": REQUEST_ID}}
                if recovered
                else {}
            ),
        }
        (battles_dir / f"{name}.json").write_text(
            json.dumps(record),
            encoding="utf-8",
        )

    write("Battle20260809T010000+0000", "2026-08-09T01:00:00+00:00")
    write(
        "Battle20260810T010000+0000",
        "2026-08-10T01:00:00+00:00",
        recovered=True,
    )

    loaded = load_comparable_battles(battles_dir)

    assert [record["battle_id"] for record in loaded] == [
        "Battle20260809T010000+0000"
    ]
    assert loaded[0]["coins_per_hour"] == 1_250_000_000_000_000_000


def _maintenance(state: str = "host_restarted") -> dict:
    return {
        "schema_version": 1,
        "request_id": REQUEST_ID,
        "action": "restart_bluestacks",
        "state": state,
        "reason": "degraded",
        "source": "test",
        "requested_at": "2026-08-10T10:00:00+00:00",
        "updated_at": "2026-08-10T10:00:00+00:00",
        "runtime": RUNTIME,
        "battle_scope": "run-1",
    }


def test_runtime_installs_recovery_hold_and_captures_pre_restart_wave():
    maintenance = _maintenance("requested")
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        emulator_maintenance=maintenance,
        current_exclusive_validation_owner=lambda: dict(RUNTIME),
        control_request_identity={"state_request_id": "state-enable-1"},
        finish_emulator_maintenance=Mock(),
    )
    app._last_wave_value = 1_234
    app._current_run_scope_id = lambda: "run-1"
    app._activation_tracker = lambda: SimpleNamespace(
        intro_sprint_active=False
    )
    app._get_watchdog_mutation_guard = lambda: SimpleNamespace(
        quiescence_boundary=nullcontext
    )
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._emulator_recovery_request_id = None
    app._emulator_maintenance_hold_active = False

    with patch("core.app.stop_blind_gem_tapper") as stop_tapper:
        app._sync_emulator_maintenance_control_boundary(now=1_000.0)

    assert app._emulator_maintenance_hold_active is True
    assert app._emulator_replay_window.high_water_wave == 1_234
    assert app._emulator_replay_window.battle_scope == "run-1"
    assert app._emulator_recovery_ack["state"] == "pending"
    assert app._emulator_recovery_ack["runtime"] == RUNTIME
    stop_tapper.assert_called_once_with()

    app._emulator_recovery_ack["state"] = "host_restart_authorized"
    app._sync_emulator_maintenance_control_boundary(now=1_001.0)
    assert app._emulator_recovery_ack["state"] == "host_restart_authorized"


def test_unacknowledged_host_request_expires_before_any_host_mutation():
    maintenance = _maintenance("requested")
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        emulator_maintenance=maintenance,
        current_exclusive_validation_owner=lambda: dict(RUNTIME),
        control_request_identity={"state_request_id": "state-enable-1"},
    )
    app._current_run_scope_id = lambda: "run-1"
    app._finish_emulator_recovery = Mock(return_value=True)
    expired_at = (
        datetime.fromisoformat(maintenance["requested_at"]).timestamp() + 181
    )

    app._sync_emulator_maintenance_control_boundary(now=expired_at)

    app._finish_emulator_recovery.assert_called_once_with(
        maintenance,
        disposition="host_ack_timeout",
        reason=(
            "Windows did not acknowledge an exact BlueStacks process before "
            "the three-minute pre-mutation timeout"
        ),
        now=expired_at,
    )


def test_request_is_cancelled_if_battle_changes_before_host_acknowledgement():
    maintenance = _maintenance("requested")
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        emulator_maintenance=maintenance,
        current_exclusive_validation_owner=lambda: dict(RUNTIME),
        control_request_identity={"state_request_id": "state-enable-1"},
    )
    app._current_run_scope_id = lambda: "replacement-run"
    app._finish_emulator_recovery = Mock(return_value=True)

    app._sync_emulator_maintenance_control_boundary()

    app._finish_emulator_recovery.assert_called_once_with(
        maintenance,
        disposition="battle_scope_replaced",
        reason=(
            "the active battle scope changed before Windows acknowledged any "
            "host mutation"
        ),
        now=None,
    )


def _recovery_app() -> App:
    maintenance = _maintenance()
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        emulator_maintenance=maintenance,
        control_state="RUNNING",
        current_exclusive_validation_owner=lambda: dict(RUNTIME),
        control_request_identity={"state_request_id": "state-enable-1"},
    )
    app._emulator_maintenance_hold_active = True
    app._emulator_replay_window = RestartReplayWindow(
        REQUEST_ID,
        100,
        battle_scope="run-1",
    )
    app._emulator_recovery_resume_attempts = 0
    app._emulator_recovery_launch_attempts = 0
    app._emulator_recovery_launch_dispatched_at = None
    app._emulator_recovery_home_attempts = 0
    app._emulator_recovery_home_dispatch = None
    app._emulator_recovery_next_action_at = 0.0
    app._emulator_recovery_force_new_battle = False
    app._sync_emulator_maintenance_control_boundary = Mock()
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._capture_frame = Mock()
    app._set_emulator_recovery_ack = Mock()
    app._last_wave_value = 100
    app._last_wave_conf = 90.0
    app._last_wave_ts = 0.0
    return app


def test_landscape_bluestacks_home_dispatches_owned_tower_launch():
    app = _recovery_app()
    app._last_screenshot_capture_result = ScreenshotCaptureResult(
        None,
        ScreenshotFailure.UNSUPPORTED_GEOMETRY,
        "Unsupported emulator resolution 1920x1080",
        adb_target="localhost:5555",
        native_width=1920,
        native_height=1080,
    )
    accepted = SimpleNamespace(
        attempted=True,
        accepted=True,
        uncertain=False,
    )

    with (
        patch("core.app.time.monotonic", return_value=100.0),
        patch(
            "core.app.bring_to_foreground",
            return_value=accepted,
        ) as launcher,
    ):
        assert app._advance_emulator_recovery_from_landscape_launcher()

    launcher.assert_called_once()
    assert launcher.call_args.kwargs["input_reason"] == (
        f"emulator_recovery request_id={REQUEST_ID}"
    )
    assert app._emulator_recovery_launch_attempts == 1
    assert app._emulator_recovery_launch_dispatched_at == 100.0
    assert app._set_emulator_recovery_ack.call_args.kwargs["state"] == (
        "awaiting_welcome_back"
    )


def test_landscape_capture_cannot_launch_without_durable_recovery_hold():
    app = _recovery_app()
    app._emulator_maintenance_hold_active = False
    app._last_screenshot_capture_result = ScreenshotCaptureResult(
        None,
        ScreenshotFailure.UNSUPPORTED_GEOMETRY,
        native_width=1920,
        native_height=1080,
    )

    with patch("core.app.bring_to_foreground") as launcher:
        assert not app._advance_emulator_recovery_from_landscape_launcher()

    launcher.assert_not_called()


def test_landscape_capture_cannot_launch_for_a_different_runtime_target():
    app = _recovery_app()
    app._last_screenshot_capture_result = ScreenshotCaptureResult(
        None,
        ScreenshotFailure.UNSUPPORTED_GEOMETRY,
        adb_target="localhost:5565",
        native_width=1920,
        native_height=1080,
    )

    with patch("core.app.bring_to_foreground") as launcher:
        assert not app._advance_emulator_recovery_from_landscape_launcher()

    launcher.assert_not_called()


def test_runtime_resumes_welcome_back_then_falls_back_to_end_run():
    app = _recovery_app()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    resolved = RecoveryUiDispatchOutcome(
        RecoveryUiDispatchStatus.RESOLVED,
        input_dispatched=True,
        attempts=1,
        final_state="RUNNING",
        reason="cleared",
    )
    with (
        patch("core.app.time.monotonic", return_value=100.0),
        patch(
            "core.app.handle_game_restarted",
            return_value=resolved,
        ) as handler,
    ):
        assert app._advance_emulator_recovery(
            {"state": "GAME_RESTARTED"},
            frame,
        )
    assert handler.call_args.kwargs["action"] is GameRestartedAction.RESUME
    assert app._emulator_replay_window.resume_dispatched is True
    assert app._emulator_recovery_resume_attempts == 1

    app = _recovery_app()
    failed = RecoveryUiDispatchOutcome(
        RecoveryUiDispatchStatus.FAILED,
        input_dispatched=True,
        attempts=2,
        final_state="GAME_RESTARTED",
        reason="persisted",
    )
    with (
        patch("core.app.time.monotonic", return_value=200.0),
        patch(
            "core.app.handle_game_restarted",
            side_effect=(failed, resolved),
        ) as handler,
    ):
        assert app._advance_emulator_recovery(
            {"state": "GAME_RESTARTED"},
            frame,
        )
        assert app._advance_emulator_recovery(
            {"state": "GAME_RESTARTED"},
            frame,
        )
    assert handler.call_args.kwargs["action"] is GameRestartedAction.END_RUN
    assert app._emulator_recovery_force_new_battle is True


def test_runtime_suppresses_replay_frames_until_high_water_is_reached():
    app = _recovery_app()
    app._emulator_replay_window.mark_resume_dispatched()
    app._finish_emulator_recovery = Mock(return_value=True)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with patch(
        "core.app.detect_wave_number_from_image",
        return_value=(95, 92.0),
    ):
        assert app._advance_emulator_recovery({"state": "RUNNING"}, frame)
    app._finish_emulator_recovery.assert_not_called()
    assert app._last_wave_value == 100

    with patch(
        "core.app.detect_wave_number_from_image",
        return_value=(100, 93.0),
    ):
        assert not app._advance_emulator_recovery({"state": "RUNNING"}, frame)
    app._finish_emulator_recovery.assert_called_once()
    assert app._last_wave_value == 100


def test_fallback_recovery_releases_only_after_new_battle_is_running():
    app = _recovery_app()
    app._emulator_recovery_force_new_battle = True
    app._emulator_recovery_home_dispatch = {
        "request_id": REQUEST_ID,
        "control": HomeBattleControl.NEW_BATTLE.value,
        "dispatched_monotonic": 10.0,
    }
    app._finish_emulator_recovery = Mock(return_value=True)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    assert not app._advance_emulator_recovery({"state": "RUNNING"}, frame)

    app._finish_emulator_recovery.assert_called_once_with(
        app._supervisor.emulator_maintenance,
        disposition="fallback_new_battle",
        reason=(
            "the prior battle was not resumable and the configured "
            "replacement battle reached fresh RUNNING evidence"
        ),
        now=None,
    )
    assert app._emulator_recovery_home_dispatch is None


def test_accepted_home_recovery_input_is_not_replayed_without_postcondition():
    app = _recovery_app()
    app._emulator_recovery_home_attempts = 1
    app._emulator_recovery_home_dispatch = {
        "request_id": REQUEST_ID,
        "control": HomeBattleControl.RESUME_BATTLE.value,
        "dispatched_monotonic": 100.0,
        "modal_recovery_completed": False,
    }
    app._runtime_uncertain_mutation_result = Mock()
    app._uncertain_lifecycle_actions = set()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with patch(
        "core.app.time.monotonic",
        return_value=100.0 + EMULATOR_HOME_POSTCONDITION_TIMEOUT_SECONDS,
    ):
        assert app._advance_emulator_recovery(
            {
                "state": "HOME_SCREEN",
                "home_battle_control": HomeBattleControl.RESUME_BATTLE.value,
            },
            frame,
        )

    app._runtime_uncertain_mutation_result.assert_called_once()
    assert REQUEST_ID in app._uncertain_lifecycle_actions
    assert app._emulator_recovery_home_dispatch is not None


def test_free_ticket_clear_requires_two_stable_home_frames_before_retry():
    app = _recovery_app()
    app._emulator_recovery_force_new_battle = True
    app._emulator_recovery_home_attempts = 1
    app._emulator_recovery_home_dispatch = {
        "request_id": REQUEST_ID,
        "control": HomeBattleControl.NEW_BATTLE.value,
        "dispatched_monotonic": 100.0,
        "modal_recovery_completed": True,
    }
    app._control_observation = {"observation_id": "runtime-recovery:1"}
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    detection = {
        "state": "HOME_SCREEN",
        "home_battle_control": HomeBattleControl.NEW_BATTLE.value,
    }

    with patch("core.app.time.monotonic", return_value=101.0):
        assert app._advance_emulator_recovery(detection, frame)
    assert (
        app._emulator_recovery_home_dispatch[
            "retry_home_candidate_observation_id"
        ]
        == "runtime-recovery:1"
    )

    app._control_observation = {"observation_id": "runtime-recovery:2"}
    with patch("core.app.time.monotonic", return_value=102.0):
        assert not app._advance_emulator_recovery(detection, frame)
    assert app._emulator_recovery_home_dispatch is None


def test_restored_source_releases_hold_when_terminal_receipt_is_pending():
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        finish_emulator_maintenance=Mock(return_value=None),
    )
    app._emulator_maintenance_hold_active = True
    app._emulator_recovery_force_new_battle = True
    app._emulator_recovery_action_logged = False
    app._set_emulator_recovery_ack = Mock()
    app._update_action_authority = Mock()
    app._publish_action_authority = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    maintenance = _maintenance("host_restarted")

    assert app._finish_emulator_recovery(
        maintenance,
        disposition="fallback_new_battle",
        reason="fresh RUNNING replacement battle",
    )

    assert app._emulator_maintenance_hold_active is False
    assert app._emulator_recovery_terminal_pending == {
        "request_id": REQUEST_ID,
        "disposition": "fallback_new_battle",
        "reason": "fresh RUNNING replacement battle",
    }
    app._flag_recoverable_runtime_failure.assert_called_once_with(
        RuntimeFailureKind.REPORTING_FAILURE,
        "the BlueStacks recovery source was restored, but its terminal "
        "receipt could not yet be persisted",
    )


def test_recovery_home_authority_accepts_resume_or_forced_new_battle_only():
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        emulator_maintenance=_maintenance(),
        battle_workflow=None,
        manual_control=None,
    )
    app._runtime_action_guard = Mock(return_value=True)
    app._emulator_maintenance_hold_active = True
    app._emulator_recovery_force_new_battle = False
    app._emulator_recovery_home_attempts = 0
    app._emulator_recovery_home_dispatch = None

    assert app._home_launch_authority_matches(
        source="emulator_recovery",
        request_id=REQUEST_ID,
        home_control=HomeBattleControl.RESUME_BATTLE,
    )
    assert not app._home_launch_authority_matches(
        source="emulator_recovery",
        request_id=REQUEST_ID,
        home_control=HomeBattleControl.NEW_BATTLE,
    )
    app._emulator_recovery_force_new_battle = True
    assert app._home_launch_authority_matches(
        source="emulator_recovery",
        request_id=REQUEST_ID,
        home_control=HomeBattleControl.NEW_BATTLE,
    )

    app._emulator_recovery_home_dispatch = {
        "request_id": REQUEST_ID,
        "control": HomeBattleControl.NEW_BATTLE.value,
    }
    assert not app._home_launch_authority_matches(
        source="emulator_recovery",
        request_id=REQUEST_ID,
        home_control=HomeBattleControl.NEW_BATTLE,
    )

    app._emulator_recovery_home_dispatch = None
    app._emulator_recovery_home_attempts = 2
    assert not app._home_launch_authority_matches(
        source="emulator_recovery",
        request_id=REQUEST_ID,
        home_control=HomeBattleControl.NEW_BATTLE,
    )


def test_free_ticket_recovery_requires_exact_new_battle_dispatch_receipt():
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        emulator_maintenance=_maintenance(),
        battle_workflow=None,
        manual_control=None,
    )
    app._emulator_maintenance_hold_active = True
    app._emulator_recovery_force_new_battle = True
    app._emulator_recovery_home_dispatch = None

    assert app._free_ticket_recovery_owner() is None

    app._emulator_recovery_home_dispatch = {
        "request_id": REQUEST_ID,
        "control": HomeBattleControl.NEW_BATTLE.value,
    }
    assert app._free_ticket_recovery_owner() == (
        "emulator_maintenance",
        f"emulator:{REQUEST_ID}",
    )
