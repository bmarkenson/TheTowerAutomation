from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.adb_target_session import AdbTargetSnapshot
from core.app import App
from core.player_save_acquisition import StablePlayerSaveAcquirer
from core.player_save_serialization import quiet_player_save_read


class _StableSession:
    def __init__(self, *snapshots):
        self._snapshots = list(snapshots)

    def snapshot(self):
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        return self._snapshots[0]


def _normalized_progression():
    return {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "identity": {
            "data_version": 9,
            "game_version": 1073,
            "save_revision": 47316,
            "mapping_id": "data-9-game-1073",
            "audit_matrix_id": "data-9-game-1073-profile-progression-v1",
        },
        "source": {
            "captured_at": "2026-08-06T04:00:00+00:00",
            "sha256": "source-fingerprint",
        },
        "fingerprint": "profile-fingerprint",
        "components": {},
        "warnings": [],
    }


def _install_shared_acquirer(app, decoded):
    pull = Mock(return_value=b"save")
    parse = Mock(return_value=decoded)
    app._player_save_acquirer = StablePlayerSaveAcquirer(
        target_snapshot_fn=app._adb_target_session.snapshot,
        pull_fn=pull,
        parser=SimpleNamespace(parse_bytes=parse),
        pull_options={
            "attempts": 3,
            "settle_seconds": 0.1,
            "read_fn": quiet_player_save_read,
        },
    )
    return pull, parse


def test_terminal_progression_uses_stable_exact_target_and_marks_acquisition():
    target = AdbTargetSnapshot("localhost:5555", 4, True)
    app = App.__new__(App)
    app._adb_target_session = _StableSession(target, target)
    decoded = SimpleNamespace(
        profile_progression=_normalized_progression(),
        mapping_id="data-9-game-1073",
        save_revision=47316,
    )
    pull, parse = _install_shared_acquirer(app, decoded)

    result = app._capture_terminal_profile_progression()

    assert result["status"] == "complete"
    assert result["source"]["acquisition"]["type"] == "passive_stable_read"
    pull.assert_called_once_with(
        device_id="localhost:5555",
        attempts=3,
        settle_seconds=0.1,
        read_fn=quiet_player_save_read,
    )
    assert parse.call_args.args == (b"save",)
    assert parse.call_args.kwargs["source_name"] == "playerInfo.dat"


def test_terminal_progression_discards_snapshot_across_target_generation_change():
    app = App.__new__(App)
    app._adb_target_session = _StableSession(
        AdbTargetSnapshot("localhost:5555", 4, True),
        AdbTargetSnapshot("localhost:5555", 5, True),
    )
    decoded = SimpleNamespace(
        profile_progression=_normalized_progression(),
        mapping_id="data-9-game-1073",
        save_revision=47316,
    )
    _install_shared_acquirer(app, decoded)

    result = app._capture_terminal_profile_progression()

    assert result["status"] == "unavailable"
    assert result["reason"] == "adb_target_changed_during_terminal_capture"
    assert result["identity"]["mapping_id"] is None


def test_terminal_progression_is_nonblocking_without_an_owned_target():
    app = App.__new__(App)

    result = app._capture_terminal_profile_progression()

    assert result["status"] == "unavailable"
    assert result["reason"] == "adb_target_session_unavailable"


def test_terminal_save_capture_reuses_one_snapshot_for_progression_and_report():
    target = AdbTargetSnapshot("localhost:5555", 4, True)
    app = App.__new__(App)
    app._adb_target_session = _StableSession(target, target)
    app._player_save_runtime_session_id = "runtime-1"
    app._perk_save_monitor = Mock()
    app._active_run_metric_monitor = Mock()
    app._player_save_audit_collector = Mock()
    monitor_context = object()
    app._current_player_save_observation_context = Mock(
        return_value=monitor_context
    )
    decoded = SimpleNamespace(
        profile_progression=_normalized_progression(),
        mapping_id="data-9-game-1073",
        save_revision=47316,
        checks={},
    )
    pull, _parse = _install_shared_acquirer(app, decoded)
    report = {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "completed_entry": {"schema_version": 1},
        "ui_fallback": {"required": False},
    }
    binding = {
        "schema_version": 1,
        "status": "bound",
        "activity_scope_run_id": "scope-1",
    }
    scope = {"schema_version": 1, "run_id": "scope-1"}

    with (
        patch("core.app.get_activity_scope", return_value=scope),
        patch(
            "core.app.terminal_history_transition_from_acquisition",
            return_value={
                "schema_version": 1,
                "status": "unavailable",
                "complete": False,
                "reason": "test_transition_unavailable",
            },
        ) as transition_from_acquisition,
        patch(
            "core.app.terminal_save_report_from_acquisition",
            return_value=report,
        ) as report_from_acquisition,
    ):
        result = app._capture_terminal_player_save(
            "GAME_OVER",
            run_binding=binding,
        )

    assert result["profile_progression"]["status"] == "complete"
    assert result["terminal_save_report"] is report
    pull.assert_called_once()
    transition_from_acquisition.assert_called_once()
    report_from_acquisition.assert_called_once()
    call = report_from_acquisition.call_args
    assert call.args[0].snapshot is decoded
    assert call.args[0].acquisition_type.value == "natural_boundary"
    assert call.kwargs == {
        "terminal_state": "GAME_OVER",
        "run_binding": binding,
        "activity_scope": scope,
        "history_transition": {
            "schema_version": 1,
            "status": "unavailable",
            "complete": False,
            "reason": "test_transition_unavailable",
        },
    }
    monitor_call = app._perk_save_monitor.observe_bundle.call_args
    assert monitor_call.args[0] is call.args[0]
    assert monitor_call.kwargs == {"context": monitor_context}
    metric_call = app._active_run_metric_monitor.observe_bundle.call_args
    assert metric_call.args[0] is call.args[0]
    assert metric_call.kwargs == {"context": monitor_context}
    audit_call = app._player_save_audit_collector.observe_acquisition.call_args
    assert audit_call.args == (call.args[0],)
    assert audit_call.kwargs == {"reason_code": "game_over"}


def test_terminal_bundle_fans_out_to_all_tournament_projectors_without_reread():
    target = AdbTargetSnapshot("localhost:5555", 4, True)
    app = App.__new__(App)
    app._adb_target_session = _StableSession(target, target)
    app._player_save_runtime_session_id = "runtime-1"
    app._activity_continuity = SimpleNamespace(
        publish_terminal_history_handoff=Mock()
    )
    app._perk_save_monitor = Mock()
    app._active_run_metric_monitor = Mock()
    monitor_context = object()
    app._current_player_save_observation_context = Mock(
        return_value=monitor_context
    )
    decoded = SimpleNamespace(
        profile_progression=_normalized_progression(),
        mapping_id="data-9-game-1073",
        save_revision=47316,
    )
    pull, parse = _install_shared_acquirer(app, decoded)
    report = {
        "schema_version": 1,
        "status": "unavailable",
        "complete": False,
        "reason": "semantic_projection_unavailable",
        "completed_entry": None,
        "ui_fallback": {"required": True},
    }
    conditions = {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "summary_codes": ["OR"],
    }
    binding = {
        "schema_version": 1,
        "status": "bound",
        "activity_scope_run_id": "scope-1",
    }
    scope = {"schema_version": 1, "run_id": "scope-1"}
    transition = {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "reason": "",
        "handoff": {"schema_version": 1, "status": "ready"},
    }

    with (
        patch("core.app.get_activity_scope", return_value=scope),
        patch(
            "core.app.terminal_history_transition_from_acquisition",
            return_value=transition,
        ),
        patch(
            "core.app.terminal_save_report_from_acquisition",
            return_value=report,
        ) as report_projector,
        patch(
            "core.app.tournament_conditions_from_acquisition",
            return_value=conditions,
        ) as conditions_projector,
    ):
        result = app._capture_terminal_player_save(
            "TOURNAMENT_RESULTS",
            run_binding=binding,
        )

    pull.assert_called_once()
    parse.assert_called_once()
    report_projector.assert_called_once()
    conditions_projector.assert_called_once()
    report_bundle = report_projector.call_args.args[0]
    conditions_bundle = conditions_projector.call_args.args[0]
    assert report_bundle is conditions_bundle
    assert report_bundle.snapshot is decoded
    assert result["battle_conditions"] == conditions
    app._perk_save_monitor.observe_bundle.assert_not_called()
    app._active_run_metric_monitor.observe_bundle.assert_called_once_with(
        report_bundle,
        context=monitor_context,
    )
    app._activity_continuity.publish_terminal_history_handoff.assert_called_once_with(
        transition
    )


@pytest.mark.parametrize("failing_consumer", ("perk", "metric"))
def test_shared_observation_fanout_isolates_consumer_exceptions(
    failing_consumer,
):
    app = App.__new__(App)
    app._perk_save_monitor = Mock()
    app._active_run_metric_monitor = Mock()
    app._player_save_audit_collector = Mock()
    app._retain_perk_timeline_save_checkpoint = Mock()
    app._log_active_run_metric_summary = Mock()
    context = object()
    acquisition = object()
    if failing_consumer == "perk":
        app._perk_save_monitor.observe_bundle.side_effect = RuntimeError(
            "perk projection failed"
        )
    else:
        app._active_run_metric_monitor.observe_bundle.side_effect = RuntimeError(
            "metric projection failed"
        )

    app._publish_player_save_observation(
        acquisition,
        context=context,
        reason_code="passive_interval",
    )

    app._perk_save_monitor.observe_bundle.assert_called_once_with(
        acquisition,
        context=context,
    )
    app._active_run_metric_monitor.observe_bundle.assert_called_once_with(
        acquisition,
        context=context,
    )
    app._player_save_audit_collector.observe_acquisition.assert_called_once_with(
        acquisition,
        reason_code="passive_interval",
    )
