import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.adb_target_session import AdbTargetSnapshot
from core.app import App
from core.battle_activation_tracker import BattleActivationTracker
from core.battle_identity import (
    ActiveBattleIdentityRecord,
    ActiveBattleTerminalContinuity,
    durable_terminal_report_evidence_from_record,
)
from core.player_save_acquisition import (
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
)
from core.player_save_serialization import quiet_player_save_read
from core.runtime_save import ActiveRoundIdentity


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


def _durable_strategy_snapshot(
    identity_fingerprint: str,
) -> dict[str, object]:
    run_configuration = {"profile": "farm", "tier": 19}

    def fingerprint(value):
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(rendered).hexdigest()

    snapshot = {
        "schema_version": 1,
        "identity_fingerprint": identity_fingerprint,
        "strategy": "farm_t19",
        "strategy_definition_fingerprint": "c" * 64,
        "session_preflight_configuration_fingerprint": "e" * 64,
        "run_configuration": run_configuration,
        "run_configuration_fingerprint": fingerprint(run_configuration),
        "recorded_at": "2026-08-16T06:33:30+00:00",
    }
    snapshot["fingerprint"] = fingerprint(snapshot)
    return snapshot


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


def test_terminal_progression_wrapper_never_requests_a_runtime_save():
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

    assert result["status"] == "unavailable"
    assert result["reason"] == "unsupported_terminal_state"
    pull.assert_not_called()
    parse.assert_not_called()


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


def test_restarted_game_over_uses_retained_counter_proof_for_save_report():
    target = AdbTargetSnapshot("localhost:5555", 4, True)
    target_binding = PlayerSaveTargetBinding("localhost:5555", 4)
    identity = ActiveRoundIdentity(
        game_version=1102,
        current_tier=19,
        rounds_started_this_tier=319,
        round_seed=1721080409,
        fingerprint="a" * 64,
    )
    activation_tracker = BattleActivationTracker()
    assert activation_tracker.bind_round_identity(identity.fingerprint)
    activation_tracker._record_visual_event(
        {
            "ability": "nuke",
            "sequence": 1,
            "approximate_wave": 4_991,
            "detection_source": "button_disappearance",
        }
    )
    activation_checkpoint = activation_tracker.checkpoint()
    assert activation_checkpoint is not None
    strategy_snapshot = _durable_strategy_snapshot(identity.fingerprint)
    retained = ActiveBattleIdentityRecord(
        identity=identity,
        bound_at="2026-08-16T06:33:00+00:00",
        reason="automation_stopped",
        operation_id="stop-1",
        acquisition={"binding_fingerprint": target_binding.fingerprint},
        session_preflight={
            "schema_version": 1,
            "identity_fingerprint": identity.fingerprint,
            "strategy": "farm_t19",
            "configuration_fingerprint": "e" * 64,
            "completed_at": "2026-08-16T06:34:00+00:00",
            "evidence": {"valid": True, "failed_checks": []},
        },
        strategy_snapshot=strategy_snapshot,
        survival_activation_checkpoint={
            "schema_version": 1,
            "identity_fingerprint": identity.fingerprint,
            "tracker_configuration_fingerprint": (
                activation_checkpoint[
                    "tracker_configuration_fingerprint"
                ]
            ),
            "recorded_at": "2026-08-16T06:35:00+00:00",
            "checkpoint": activation_checkpoint,
        },
        terminal_continuity=ActiveBattleTerminalContinuity(
            round_counter_vector_fingerprint="b" * 64,
            round_counter_tier_count=40,
            save_revision=50670,
            target_binding_fingerprint=target_binding.fingerprint,
        ),
    )
    app = App.__new__(App)
    app._adb_target_session = _StableSession(target, target)
    app._player_save_runtime_session_id = "runtime-2"
    app._process_restart_reattachment_enabled = True
    app._retained_battle_identity_record = retained
    app._mission_mgr = SimpleNamespace(
        awaiting_initial_battle_intent=lambda: True,
    )
    app._supervisor = SimpleNamespace(
        process_restart_handoff={
            "status": "failed",
            "workflow_id": None,
            "expected_active_round_identity_fingerprint": identity.fingerprint,
            "source_evidence": {
                "game_state": "active_battle",
                "active_round_identity_fingerprint": identity.fingerprint,
                "adb_target": target.target,
                "target_generation": target.generation,
            },
        }
    )
    current = {
        "game_state": "game_over",
        "adb_target": target.target,
        "target_generation": target.generation,
    }
    app._current_control_workflow_evidence = Mock(return_value=current)
    app._perk_save_monitor = Mock()
    app._active_run_metric_monitor = Mock()
    decoded = SimpleNamespace(
        profile_progression=_normalized_progression(),
        mapping_id="data-9-game-1073",
        save_revision=50677,
        checks={},
    )
    _install_shared_acquirer(app, decoded)
    process_binding = {
        "schema_version": 1,
        "status": "unbound",
        "reason": "terminal_without_forced_active_battle",
        "activity_scope_run_id": "scope-1",
        "active_round_identity_fingerprint": None,
    }
    recovered_binding = {
        "schema_version": 1,
        "status": "bound",
        "reason": "retained_round_counter_vector_matches_terminal_save",
        "binding_source": "durable_full_round_counter_vector",
        "activity_scope_run_id": "scope-1",
        "active_round_identity_fingerprint": identity.fingerprint,
    }
    transition = {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
    }
    report = {"status": "complete", "complete": True}
    scope = {"schema_version": 1, "run_id": "scope-1"}

    with (
        patch("core.app.get_activity_scope", return_value=scope),
        patch(
            "core.app.terminal_run_binding_from_round_counters",
            return_value=recovered_binding,
        ) as recover,
        patch(
            "core.app.terminal_history_transition_from_acquisition",
            return_value=transition,
        ) as history,
        patch(
            "core.app.terminal_save_report_from_acquisition",
            return_value=report,
        ) as project_report,
    ):
        result = app._capture_terminal_player_save(
            "GAME_OVER",
            run_binding=process_binding,
        )

    acquisition = recover.call_args.args[1]
    assert acquisition.boundary.active_round_identity_fingerprint == (
        identity.fingerprint
    )
    assert recover.call_args.kwargs == {
        "expected_identity_fingerprint": identity.fingerprint,
        "activity_scope_run_id": "scope-1",
    }
    assert history.call_args.kwargs["run_binding"] == recovered_binding
    assert project_report.call_args.kwargs["run_binding"] == recovered_binding
    assert result["_report_run_binding"] == recovered_binding
    assert result["_mapping_observer"] is None
    durable = result["_durable_terminal_evidence_context"]
    assert durable["strategy"] == "farm_t19"
    assert durable["run_configuration"] == {"profile": "farm", "tier": 19}
    assert durable["session_preflight_evidence"] == {
        "valid": True,
        "failed_checks": [],
    }
    assert durable["survival_ability_activations"]["nuke_activations"][0][
        "approximate_wave"
    ] == 4_991
    assert durable["survival_ability_activations"]["durable_restoration"] == {
        "schema_version": 1,
        "status": "observed_events_through_checkpoint",
        "complete_history": False,
        "checkpoint_recorded_at": "2026-08-16T06:35:00+00:00",
        "last_save_revision": None,
        "last_saved_wave": None,
    }
    assert durable["durable_terminal_evidence"]["components"] == [
        "strategy_snapshot",
        "session_preflight_evidence",
        "survival_ability_activations",
    ]
    app._perk_save_monitor.observe_bundle.assert_not_called()
    app._active_run_metric_monitor.observe_bundle.assert_not_called()


def test_restarted_legacy_game_over_uses_operator_attestation_binding():
    target = AdbTargetSnapshot("localhost:5555", 4, True)
    identity = ActiveRoundIdentity(
        game_version=1102,
        current_tier=19,
        rounds_started_this_tier=319,
        round_seed=1721080409,
        fingerprint="a" * 64,
    )
    retained = ActiveBattleIdentityRecord(
        identity=identity,
        bound_at="2026-08-16T06:33:00+00:00",
        reason="battle_started",
        operation_id="launch-1",
        acquisition={},
        operator_terminal_attestation={"runtime": {"runtime_id": "runtime-2"}},
    )
    app = App.__new__(App)
    app._adb_target_session = _StableSession(target, target)
    app._player_save_runtime_session_id = "runtime-2"
    app._perk_save_monitor = Mock()
    app._active_run_metric_monitor = Mock()
    app._player_save_audit_collector = Mock()
    app._retained_game_over_binding_candidate = Mock(return_value=retained)
    decoded = SimpleNamespace(
        profile_progression=_normalized_progression(),
        mapping_id="data-9-game-1073",
        save_revision=50677,
        checks={},
    )
    _install_shared_acquirer(app, decoded)
    process_binding = {
        "schema_version": 1,
        "status": "unbound",
        "reason": "terminal_without_forced_active_battle",
    }
    recovered_binding = {
        "schema_version": 1,
        "status": "bound",
        "binding_source": "operator_terminal_attestation",
        "active_round_identity_fingerprint": identity.fingerprint,
    }
    durable = {
        "strategy": "farm_t19",
        "run_configuration": {"profile": "farm", "tier": 19},
    }
    scope = {"schema_version": 1, "run_id": "scope-1"}

    with (
        patch("core.app.get_activity_scope", return_value=scope),
        patch(
            "core.app.terminal_run_binding_from_operator_attestation",
            return_value=recovered_binding,
        ) as operator_binding,
        patch(
            "core.app.terminal_run_binding_from_round_counters"
        ) as counter_binding,
        patch(
            "core.app.durable_terminal_report_evidence_from_record",
            return_value=durable,
        ),
        patch(
            "core.app.terminal_history_transition_from_acquisition",
            return_value={"status": "complete", "complete": True},
        ) as history,
        patch(
            "core.app.terminal_save_report_from_acquisition",
            return_value={"status": "complete", "complete": True},
        ) as report,
    ):
        result = app._capture_terminal_player_save(
            "GAME_OVER",
            run_binding=process_binding,
        )

    acquisition = operator_binding.call_args.args[1]
    assert acquisition.boundary.active_round_identity_fingerprint == (
        identity.fingerprint
    )
    counter_binding.assert_not_called()
    assert history.call_args.kwargs["run_binding"] == recovered_binding
    assert report.call_args.kwargs["run_binding"] == recovered_binding
    assert result["_durable_terminal_evidence_context"] == durable


def test_durable_terminal_components_fail_closed_independently():
    identity = ActiveRoundIdentity(
        game_version=1102,
        current_tier=19,
        rounds_started_this_tier=319,
        round_seed=1721080409,
        fingerprint="a" * 64,
    )
    tracker = BattleActivationTracker()
    assert tracker.bind_round_identity(identity.fingerprint)
    checkpoint = tracker.checkpoint()
    assert checkpoint is not None
    strategy_snapshot = _durable_strategy_snapshot(identity.fingerprint)
    record = ActiveBattleIdentityRecord(
        identity=identity,
        bound_at="2026-08-16T06:33:00+00:00",
        reason="automation_stopped",
        operation_id="stop-1",
        acquisition={},
        session_preflight={
            "schema_version": 1,
            "identity_fingerprint": identity.fingerprint,
            "strategy": "farm_t19",
            "configuration_fingerprint": "wrong",
            "completed_at": "2026-08-16T06:34:00+00:00",
            "evidence": {"valid": True, "failed_checks": []},
        },
        strategy_snapshot=strategy_snapshot,
        survival_activation_checkpoint={
            "schema_version": 1,
            "identity_fingerprint": identity.fingerprint,
            "tracker_configuration_fingerprint": "f" * 64,
            "recorded_at": "2026-08-16T06:35:00+00:00",
            "checkpoint": checkpoint,
        },
    )

    assert durable_terminal_report_evidence_from_record(
        record,
        terminal_binding={"status": "unbound"},
    ) == {}

    durable = durable_terminal_report_evidence_from_record(
        record,
        terminal_binding={
            "schema_version": 1,
            "status": "bound",
            "binding_source": "durable_full_round_counter_vector",
            "active_round_identity_fingerprint": identity.fingerprint,
        },
    )

    assert durable["strategy"] == "farm_t19"
    assert "session_preflight_evidence" not in durable
    assert "survival_ability_activations" not in durable
    assert durable["durable_terminal_evidence"]["components"] == [
        "strategy_snapshot"
    ]


def test_durable_activation_checkpoint_does_not_require_a_strategy_snapshot():
    identity = ActiveRoundIdentity(
        game_version=1102,
        current_tier=19,
        rounds_started_this_tier=319,
        round_seed=1721080409,
        fingerprint="a" * 64,
    )
    tracker = BattleActivationTracker()
    assert tracker.bind_round_identity(identity.fingerprint)
    tracker._record_visual_event(
        {
            "ability": "nuke",
            "sequence": 1,
            "approximate_wave": 4_991,
            "detection_source": "button_disappearance",
        }
    )
    checkpoint = tracker.checkpoint()
    assert checkpoint is not None
    record = ActiveBattleIdentityRecord(
        identity=identity,
        bound_at="2026-08-16T06:33:00+00:00",
        reason="automation_stopped",
        operation_id="stop-1",
        acquisition={},
        survival_activation_checkpoint={
            "schema_version": 1,
            "identity_fingerprint": identity.fingerprint,
            "tracker_configuration_fingerprint": checkpoint[
                "tracker_configuration_fingerprint"
            ],
            "recorded_at": "2026-08-16T06:35:00+00:00",
            "checkpoint": checkpoint,
        },
    )

    durable = durable_terminal_report_evidence_from_record(
        record,
        terminal_binding={
            "schema_version": 1,
            "status": "bound",
            "binding_source": "durable_full_round_counter_vector",
            "active_round_identity_fingerprint": identity.fingerprint,
        },
    )

    assert "strategy" not in durable
    assert durable["survival_ability_activations"]["nuke_activations"][0][
        "approximate_wave"
    ] == 4_991
    assert durable["durable_terminal_evidence"]["components"] == [
        "survival_ability_activations"
    ]


def test_terminal_bundle_applies_durable_evidence_without_process_state():
    app = App.__new__(App)
    process_binding = {
        "schema_version": 1,
        "status": "unbound",
        "reason": "terminal_without_forced_active_battle",
        "activity_scope_run_id": "scope-2",
        "active_round_identity_fingerprint": None,
    }
    durable = {
        "strategy": "farm_t19",
        "run_configuration": {"profile": "farm", "tier": 19},
        "strategy_definition_fingerprint": "c" * 64,
        "session_preflight_evidence": {
            "valid": True,
            "failed_checks": [],
        },
        "survival_ability_activations": {
            "schema_version": 5,
            "source": "player_save_refresh_timer",
            "second_wind_activations": [],
            "demon_mode_first_activation": None,
            "demon_mode_activations": [],
            "nuke_activations": [],
        },
        "durable_terminal_evidence": {
            "schema_version": 1,
            "status": "restored",
            "binding_source": "durable_full_round_counter_vector",
            "active_round_identity_fingerprint": "a" * 64,
            "components": [
                "strategy_snapshot",
                "session_preflight_evidence",
                "survival_ability_activations",
            ],
            "component_fingerprints": {
                "strategy_snapshot": "d" * 64,
                "session_preflight_evidence": "e" * 64,
                "survival_ability_activations": "f" * 64,
            },
        },
    }
    app._terminal_run_binding = Mock(return_value=process_binding)
    app._capture_terminal_player_save = Mock(
        return_value={
            "profile_progression": _normalized_progression(),
            "terminal_save_report": {"status": "complete"},
            "_durable_terminal_evidence_context": durable,
        }
    )
    app._sync_perk_timeline_save_checkpoint = Mock()
    app._perk_timeline_observer = Mock()
    app._battle_activation_tracker = Mock()
    app._reset_player_save_audit_perk_mapping_evidence = Mock()
    app._last_unbound_terminal_signature = None

    context, acquisition, mapping_observer = app._terminal_battle_bundle(
        "GAME_OVER"
    )

    assert acquisition is None
    assert mapping_observer is None
    assert context["run_binding"] == process_binding
    assert context["strategy"] == "farm_t19"
    assert context["session_preflight_evidence"]["valid"] is True
    assert "coin_rate_samples" not in context
    assert "perk_selection_timeline" not in context
    app._perk_timeline_observer.reset.assert_called_once_with(
        fresh_battle=False
    )
    app._battle_activation_tracker.reset.assert_called_once_with()


def test_terminal_bundle_fans_out_to_all_tournament_projectors_without_reread():
    target = AdbTargetSnapshot("localhost:5555", 4, True)
    app = App.__new__(App)
    app._adb_target_session = _StableSession(target, target)
    app._player_save_runtime_session_id = "runtime-1"
    app._activity_history_reporter = SimpleNamespace(
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
    app._activity_history_reporter.publish_terminal_history_handoff.assert_called_once_with(
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


def test_verified_same_battle_handoff_rebinds_metrics_before_projection():
    app = App.__new__(App)
    app._perk_save_monitor = None
    app._active_run_metric_monitor = Mock()
    app._active_run_metric_monitor.continue_same_battle_at_target.return_value = (
        True
    )
    app._active_run_metric_monitor.observe_bundle.return_value = (
        "accepted_checkpoint"
    )
    app._player_save_audit_collector = None
    app._battle_identity_store = None
    app._bind_survival_activation_tracker = Mock(return_value=False)
    app._log_active_run_metric_summary = Mock()
    context = object()
    acquisition = object()

    app._publish_player_save_observation(
        acquisition,
        context=context,
        reason_code="forced_battle_identity",
        verified_same_battle_target_handoff=True,
    )

    metric_monitor = app._active_run_metric_monitor
    metric_monitor.continue_same_battle_at_target.assert_called_once_with(
        context,
        acquisition=acquisition,
    )
    metric_monitor.bind_context.assert_not_called()
    metric_monitor.observe_bundle.assert_called_once_with(
        acquisition,
        context=context,
    )
    app._log_active_run_metric_summary.assert_called_once_with(
        metric_monitor,
        context,
    )
