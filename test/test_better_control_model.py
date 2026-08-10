from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from automation.missions.manager import MissionManager
from core.action_authority import (
    AuthorityHold,
    AuthorityHoldState,
    AuxiliaryCollector,
    RuntimeActionAuthority,
    RuntimeActionAuthorityPublisher,
    RuntimeActionClass,
)
from core.app import App
from core.automation_supervisor import AutomationSupervisor
from core.battle_lifecycle import HomeBattleControl
from core.control_directives import ControlDirectiveStore
from core.control_model import (
    build_home_ui_reconciliation_receipt,
    build_running_ui_reconciliation_receipt,
    build_running_save_reconciliation_receipt,
    build_terminal_ui_reconciliation_receipt,
    build_terminal_return_reconciliation_receipt,
    intent_matches_evidence,
    observed_game_state,
    ui_reconciliation_receipt_matches_evidence,
    validate_save_reconciliation_receipt,
    validate_workflow_evidence,
)
from core.gc_no_battle_setup import (
    GcNoBattleSetupResult,
    GcNoBattleSetupStatus,
)
from core.player_save import PlayerSaveSnapshot, SaveCheckEvidence
from core.player_save_preflight import CarriedEvidenceState
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
)
from core.player_save_temporal import (
    PlayerSaveTemporalClass,
    RunningAttachmentSaveFact,
    RunningAttachmentSaveObservations,
    RunningAttachmentTemporalBinding,
)
from core.player_save_history import PlayerSaveAttachmentContext
from core.player_save_serialization import GuardedSerializationStatus
from core.strategy_authoring import FARM_SETTING_REGISTRY
from core.control_surface import ControlSurfaceRequestError, ControlSurfaceService
from core.run_state import AUTOMATION, ExecMode, RunState
from handlers.game_over_handler import GameOverHandlingOutcome
from tools import automation_ctl


@pytest.fixture(autouse=True)
def _restore_automation_control():
    prior_state = AUTOMATION.state
    prior_mode = AUTOMATION.mode
    try:
        yield
    finally:
        AUTOMATION.state = prior_state
        AUTOMATION.mode = prior_mode


def _timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _evidence(
    *,
    game_state: str = "home_new_battle",
    observation_id: str = "runtime-1:1",
    runtime_id: str = "runtime-1",
    scope: str = "scope-1",
) -> dict[str, object]:
    primary_state = {
        "home_new_battle": "HOME_SCREEN",
        "home_resume_battle": "HOME_SCREEN",
        "active_battle": "RUNNING",
        "game_over": "GAME_OVER",
        "tournament_results": "TOURNAMENT_RESULTS",
    }.get(game_state, "UNKNOWN")
    home_control = {
        "home_new_battle": "NEW_BATTLE",
        "home_resume_battle": "RESUME_BATTLE",
    }.get(game_state, "UNKNOWN")
    return {
        "schema_version": 1,
        "runtime_id": runtime_id,
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "observation_id": observation_id,
        "observed_at": _timestamp(),
        "primary_state": primary_state,
        "home_battle_control": home_control,
        "game_state": game_state,
        "active_battle": game_state
        in {"home_resume_battle", "active_battle"},
        "activity_scope_run_id": scope,
        "target_generation": 7,
    }


@pytest.mark.parametrize("request_change", ("policy_cycle", "authority_cycle"))
def test_terminal_home_continuation_requires_unchanged_exact_requests(
    tmp_path,
    monkeypatch,
    request_change,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    store.set_mode("NEXT_BATTLE", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    terminal = _evidence(
        game_state="game_over",
        runtime_id=str(owner["runtime_id"]),
    )
    terminal["pid"] = owner["pid"]
    app = App.__new__(App)
    app._supervisor = supervisor
    app._operator_battle_intent_required = True
    app._control_observation = {
        key: value
        for key, value in terminal.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    claim = app._build_terminal_home_continuation_claim(
        source="no_strategy_post_run"
    )

    assert claim is not None
    assert claim["state_request_id"] == (
        supervisor.control_request_identity["state_request_id"]
    )
    assert claim["mode_request_id"] == (
        supervisor.control_request_identity["mode_request_id"]
    )
    assert app._commit_terminal_home_continuation(claim) is True
    home = _evidence(
        game_state="home_new_battle",
        observation_id="runtime-1:home",
        runtime_id=str(owner["runtime_id"]),
    )
    home["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in home.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    assert app._terminal_home_continuation_ready(
        home_control=HomeBattleControl.NEW_BATTLE
    ) is True

    if request_change == "policy_cycle":
        store.set_mode("HOME", source="test")
        supervisor.apply_control()
        store.set_mode("NEXT_BATTLE", source="test")
    else:
        store.set_state("PAUSED", source="test")
        supervisor.apply_control()
        store.set_state("RUNNING", source="test")
    supervisor.apply_control()

    assert AUTOMATION.mode is ExecMode.NEXT_BATTLE
    assert app._terminal_home_continuation_ready(
        home_control=HomeBattleControl.NEW_BATTLE
    ) is False
    assert app._terminal_home_continuation is None


def test_terminal_home_continuation_never_authorizes_resume_battle():
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        control_state="RUNNING",
        control_request_identity={
            "state_request_id": "state-1",
            "mode_request_id": "mode-1",
        },
    )
    binding = {
        "runtime_id": "runtime-1",
        "pid": 123,
        "adb_target": "localhost:5555",
        "target_generation": 7,
        "activity_scope_run_id": "scope-1",
    }
    app._terminal_home_continuation = {
        "schema_version": 1,
        "source": "no_strategy_post_run",
        "state_request_id": "state-1",
        "mode_request_id": "mode-1",
        "binding": binding,
    }
    app._current_control_workflow_evidence = MagicMock(
        return_value={
            **binding,
            "game_state": "home_resume_battle",
        }
    )
    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.NEXT_BATTLE

    try:
        ready = app._terminal_home_continuation_ready(
            home_control=HomeBattleControl.RESUME_BATTLE
        )
    finally:
        AUTOMATION.mode = original_mode

    assert ready is False
    assert app._terminal_home_continuation is None
    app._current_control_workflow_evidence.assert_not_called()


@pytest.mark.parametrize(
    "workflow",
    [
        {
            "request_id": "start-2",
            "intent": "start_battle",
            "status": "acknowledged",
        },
        {
            "request_id": "start-1",
            "intent": "attach_battle",
            "status": "validating_save",
        },
        *[
            {
                "request_id": "start-1",
                "intent": "start_battle",
                "status": status,
            }
            for status in (
                "awaiting_enable",
                "action_dispatched",
                "completed",
                "rejected",
                "interrupted",
                "failed",
                "cancelled",
            )
        ],
    ],
    ids=(
        "replacement-request",
        "replacement-intent",
        "awaiting-enable",
        "action-dispatched",
        "completed",
        "rejected",
        "interrupted",
        "failed",
        "cancelled",
    ),
)
def test_home_launch_barrier_rejects_changed_start_workflow(workflow):
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        battle_workflow=workflow,
        manual_control=None,
    )
    app._runtime_action_guard = MagicMock(return_value=True)
    app._awaiting_initial_battle_intent = MagicMock(return_value=False)

    assert app._home_launch_authority_matches(
        source="start_battle",
        request_id="start-1",
        home_control=HomeBattleControl.NEW_BATTLE,
    ) is False


def test_home_launch_barrier_accepts_only_current_exact_workflows():
    app = App.__new__(App)
    app._runtime_action_guard = MagicMock(return_value=True)
    app._awaiting_initial_battle_intent = MagicMock(return_value=False)
    app._supervisor = SimpleNamespace(
        battle_workflow={
            "request_id": "start-1",
            "intent": "start_battle",
            "status": "acknowledged",
        },
        manual_control=None,
    )

    assert app._home_launch_authority_matches(
        source="start_battle",
        request_id="start-1",
        home_control=HomeBattleControl.NEW_BATTLE,
    ) is True
    app._awaiting_initial_battle_intent.return_value = True
    assert app._home_launch_authority_matches(
        source="start_battle",
        request_id="start-1",
        home_control=HomeBattleControl.NEW_BATTLE,
    ) is False
    app._awaiting_initial_battle_intent.return_value = False

    app._supervisor.battle_workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    assert app._home_launch_authority_matches(
        source="attach_battle",
        request_id="attach-1",
        home_control=HomeBattleControl.RESUME_BATTLE,
    ) is True
    assert app._home_launch_authority_matches(
        source="attach_battle",
        request_id="attach-2",
        home_control=HomeBattleControl.RESUME_BATTLE,
    ) is False

    app._supervisor.battle_workflow = None
    app._supervisor.manual_control = {
        "manual_control_id": "manual-1",
        "status": "reconciling",
    }
    assert app._home_launch_authority_matches(
        source="manual_return",
        request_id="manual-1",
        home_control=HomeBattleControl.RESUME_BATTLE,
    ) is True
    assert app._home_launch_authority_matches(
        source="manual_return",
        request_id="manual-2",
        home_control=HomeBattleControl.RESUME_BATTLE,
    ) is False
    app._supervisor.manual_control["status"] = "completed"
    assert app._home_launch_authority_matches(
        source="manual_return",
        request_id="manual-1",
        home_control=HomeBattleControl.RESUME_BATTLE,
    ) is False


def test_home_launch_barrier_requires_current_lifecycle_authority():
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        battle_workflow={
            "request_id": "start-1",
            "intent": "start_battle",
            "status": "acknowledged",
        },
        manual_control=None,
    )
    app._runtime_action_guard = MagicMock(return_value=False)

    assert app._home_launch_authority_matches(
        source="start_battle",
        request_id="start-1",
        home_control=HomeBattleControl.NEW_BATTLE,
    ) is False


@pytest.mark.parametrize(
    ("workflow", "manual"),
    (
        (
            {
                "request_id": "start-1",
                "intent": "start_battle",
                "status": "acknowledged",
            },
            None,
        ),
        (
            None,
            {
                "manual_control_id": "manual-1",
                "status": "reconciling",
            },
        ),
    ),
    ids=("battle-workflow", "manual-control"),
)
def test_terminal_home_launch_barrier_yields_to_new_operator_workflow(
    workflow,
    manual,
):
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        battle_workflow=workflow,
        manual_control=manual,
    )
    app._runtime_action_guard = MagicMock(return_value=True)
    app._clear_terminal_home_continuation = MagicMock()
    app._terminal_home_continuation_ready = MagicMock(return_value=True)

    assert app._home_launch_authority_matches(
        source="terminal_continuation",
        request_id="",
        home_control=HomeBattleControl.NEW_BATTLE,
    ) is False
    app._clear_terminal_home_continuation.assert_called_once()
    app._terminal_home_continuation_ready.assert_not_called()


def test_terminal_home_launch_barrier_clears_manual_claim_when_guard_denies():
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        battle_workflow=None,
        manual_control={
            "manual_control_id": "manual-1",
            "status": "reconciling",
        },
    )
    app._runtime_action_guard = MagicMock(return_value=False)
    app._clear_terminal_home_continuation = MagicMock()
    app._terminal_home_continuation_ready = MagicMock(return_value=True)

    assert app._home_launch_authority_matches(
        source="terminal_continuation",
        request_id="",
        home_control=HomeBattleControl.NEW_BATTLE,
    ) is False
    app._clear_terminal_home_continuation.assert_called_once()
    app._terminal_home_continuation_ready.assert_not_called()


def _save_receipt(
    workflow_id: str,
    evidence: dict[str, object],
    *,
    kind: str = "running_attachment_reconciliation",
) -> dict[str, object]:
    receipt, _acquisition, _temporal, _context = _running_save_claim(
        workflow_id,
        evidence,
        kind=kind,
    )
    return receipt


def _running_save_claim(
    workflow_id: str,
    evidence: dict[str, object],
    *,
    kind: str = "running_attachment_reconciliation",
    final_scope: str | None = None,
) -> tuple[
    dict[str, object],
    PlayerSaveAcquisitionBundle,
    RunningAttachmentTemporalBinding,
    PlayerSaveAttachmentContext,
]:
    acquisition, temporal, context = _running_reconciliation_objects(
        evidence,
        final_scope=final_scope,
    )
    receipt = build_running_save_reconciliation_receipt(
        kind=kind,
        workflow_id=workflow_id,
        observation_id=str(evidence["observation_id"]),
        acquisition=acquisition,
        temporal_binding=temporal,
        disposition="same_battle",
    )
    return receipt, acquisition, temporal, context


def _retain_running_save_claim(
    app: App,
    workflow_id: str,
    evidence: dict[str, object],
    claim: tuple[
        dict[str, object],
        PlayerSaveAcquisitionBundle,
        RunningAttachmentTemporalBinding,
        PlayerSaveAttachmentContext,
    ],
) -> None:
    receipt, acquisition, temporal, context = claim
    app._current_player_save_attachment_context = lambda **_kwargs: context
    app._retain_running_reconciliation_claim(
        workflow_id,
        receipt=receipt,
        acquisition=acquisition,
        temporal_binding=temporal,
        context=context,
        evidence=evidence,
    )


def _running_reconciliation_objects(
    evidence: dict[str, object],
    *,
    snapshot: object | None = None,
    final_scope: str | None = None,
) -> tuple[
    PlayerSaveAcquisitionBundle,
    RunningAttachmentTemporalBinding,
    PlayerSaveAttachmentContext,
]:
    started = datetime.now(timezone.utc)
    captured = started + timedelta(milliseconds=1)
    binding = PlayerSaveTargetBinding(
        str(evidence["adb_target"]),
        int(evidence["target_generation"]),
    )
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=binding,
        acquisition_started_at=started,
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=snapshot if snapshot is not None else SimpleNamespace(),
    )
    resolved_scope = str(final_scope or evidence["activity_scope_run_id"])
    temporal = RunningAttachmentTemporalBinding(
        runtime_session_id="save-runtime-1",
        source_activity_scope_id=str(evidence["activity_scope_run_id"]),
        target_binding=binding,
        mapping_id="data-9-game-1073",
        effective_mapping_fingerprint="9" * 64,
        active_round_identity_fingerprint="b" * 64,
        captured_at=captured.isoformat(),
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
    ).bind_final_scope(resolved_scope)
    context = PlayerSaveAttachmentContext(
        runtime_session_id="save-runtime-1",
        activity_scope_id=resolved_scope,
        target=str(evidence["adb_target"]),
        target_generation=int(evidence["target_generation"]),
        active_battle_observed=True,
    )
    return acquisition, temporal, context


def _player_save_snapshot(
    check_id: str,
    value: object,
    *,
    complete: bool = True,
    runtime_save: object | None = None,
) -> PlayerSaveSnapshot:
    evidence = SaveCheckEvidence(
        check_id=check_id,
        status="observed",
        value=value,
        source_fields=("field",),
        complete=complete,
        authority={
            "kind": "allowed_values",
            "values": ["Farm", "Tourney"],
        },
    )
    return PlayerSaveSnapshot(
        captured_at=datetime.now(timezone.utc).isoformat(),
        source_name="playerInfo.dat",
        source_sha256="a" * 64,
        source_size=1,
        container="raw",
        decompressed_size=1,
        root_class="PlayerInfo",
        field_count=1,
        data_version=9,
        game_version=1073,
        save_revision=1,
        mapping_id="data-9-game-1073",
        mapping_maturity="validated",
        validated_checks=(check_id,),
        shape_valid=True,
        warnings=(),
        profile_summary={},
        checks={check_id: evidence},
        runtime_save=runtime_save,
    )


def _running_return_fixture(
    tmp_path,
    *,
    snapshot: PlayerSaveSnapshot,
    observed_value: object,
):
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "awaiting_enable",
    )
    store.enable_after_return_control(
        manual["manual_control_id"],
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    supervisor.apply_control()

    manager = MagicMock()
    manager.strategy = SimpleNamespace(
        name="active-farm",
        session_preflight_requirements=lambda: {
            "workshop_preset": "Farm"
        },
    )
    acquisition, temporal, context = _running_reconciliation_objects(
        evidence,
        snapshot=snapshot,
    )
    observations = RunningAttachmentSaveObservations(
        binding=temporal,
        facts=(
            RunningAttachmentSaveFact(
                check_id="workshop_preset",
                temporal_class=PlayerSaveTemporalClass.ROUND_INVARIANT,
                value=observed_value,
                source_fields=("field",),
            ),
        ),
    )
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._current_player_save_attachment_context = lambda: context
    return (
        app,
        supervisor,
        manager,
        evidence,
        acquisition,
        temporal,
        observations,
        context,
    )


def _natural_terminal_acquisition(
    evidence: dict[str, object],
) -> PlayerSaveAcquisitionBundle:
    started = datetime.now(timezone.utc)
    captured = started + timedelta(milliseconds=1)
    return PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.NATURAL_BOUNDARY,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=PlayerSaveTargetBinding(
            str(evidence["adb_target"]),
            int(evidence["target_generation"]),
        ),
        acquisition_started_at=started,
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=SimpleNamespace(),
        boundary=PlayerSaveNaturalBoundary(
            kind=PlayerSaveBoundaryKind.GAME_OVER,
            observed_at=started,
            runtime_session_id=str(evidence["runtime_id"]),
            activity_scope_id=str(evidence["activity_scope_run_id"]),
        ),
    )


def _publish_runtime_observation(
    service: ControlSurfaceService,
    evidence: dict[str, object],
    *,
    published_at: float | None = None,
    paused: bool = True,
    active_battle_adopted: bool = False,
    active_strategy: str | None = None,
    explicit_home_intent_required: bool = False,
    terminal_home_continuation: dict[str, object] | None = None,
) -> None:
    owner = {
        "runtime_id": evidence["runtime_id"],
        "pid": evidence["pid"],
        "adb_target": evidence["adb_target"],
    }
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=paused,
        active_battle=bool(evidence["active_battle"]),
        battle_scope=evidence["activity_scope_run_id"],
        primary_state=str(evidence["primary_state"]),
    )
    publisher = RuntimeActionAuthorityPublisher(
        service.strategy_action_gate_path,
        owner=owner,
    )
    publisher.publish(
        authority.snapshot(),
        owner=owner,
        now=published_at,
        control_model={
            "schema_version": 1,
            "observation": {
                key: value
                for key, value in evidence.items()
                if key not in {"runtime_id", "pid", "adb_target"}
            },
            "battle_lifecycle": {
                "awaiting_initial_intent": not active_battle_adopted,
                "active_battle_adopted": active_battle_adopted,
                "explicit_home_intent_required": (
                    explicit_home_intent_required
                ),
                "terminal_home_continuation": (
                    terminal_home_continuation or {"pending": False}
                ),
            },
            "strategy_scope": {
                "active_battle": active_strategy,
                "observation_only": bool(
                    active_battle_adopted and active_strategy == "none"
                ),
            },
        },
    )
    service._runtime_evidence = lambda: {
        "active": True,
        "instances": [
            {
                "active": True,
                "pid": evidence["pid"],
                "target": evidence["adb_target"],
            }
        ],
    }


def test_attachment_context_allows_one_expected_scope_rebind(monkeypatch):
    app = App.__new__(App)
    session = MagicMock()
    session.snapshot.return_value = SimpleNamespace(
        owned=True,
        target="localhost:5555",
        generation=7,
    )
    app._adb_target_session = session
    app._mission_mgr = MagicMock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._player_save_runtime_session_id = "save-runtime-1"
    app._current_control_workflow_evidence = lambda: _evidence(
        game_state="active_battle",
        scope="scope-before-continuity",
    )
    app._running_save_reconciliation_owner = (
        lambda: AuthorityHold.OPERATOR_WORKFLOW
    )
    monkeypatch.setattr(
        "core.app.get_activity_scope",
        lambda: {"run_id": "scope-after-continuity"},
    )

    with pytest.raises(RuntimeError, match="workflow binding changed"):
        app._current_player_save_attachment_context()
    with pytest.raises(RuntimeError, match="workflow binding changed"):
        app._current_player_save_attachment_context(
            transition_source_activity_scope_id="unrelated-scope"
        )

    context = app._current_player_save_attachment_context(
        transition_source_activity_scope_id="scope-before-continuity"
    )

    assert context == PlayerSaveAttachmentContext(
        runtime_session_id="save-runtime-1",
        activity_scope_id="scope-after-continuity",
        target="localhost:5555",
        target_generation=7,
        active_battle_observed=True,
    )


def test_attachment_scope_transition_requires_exact_typed_source_and_target():
    app = App.__new__(App)
    requested = _evidence(game_state="active_battle", scope="scope-before")
    expected = _evidence(game_state="active_battle", scope="scope-after")
    unexpected = _evidence(game_state="active_battle", scope="scope-other")

    allowed, _ = app._workflow_evidence_matches_runtime(
        requested,
        expected,
        intent="attach_battle",
        allowed_activity_scope_transition=("scope-before", "scope-after"),
    )
    rejected, reason = app._workflow_evidence_matches_runtime(
        requested,
        unexpected,
        intent="attach_battle",
        allowed_activity_scope_transition=("scope-before", "scope-after"),
    )

    assert allowed is True
    assert rejected is False
    assert reason == "battle activity scope changed"


def test_observed_game_dimensions_do_not_infer_a_workflow():
    assert (
        observed_game_state(
            "HOME_SCREEN", "NEW_BATTLE", active_battle=False
        )
        == "home_new_battle"
    )
    assert (
        observed_game_state(
            "HOME_SCREEN", "RESUME_BATTLE", active_battle=True
        )
        == "home_resume_battle"
    )
    assert observed_game_state("RUNNING", "UNKNOWN", active_battle=True) == (
        "active_battle"
    )
    assert intent_matches_evidence("start_battle", _evidence())
    assert not intent_matches_evidence("attach_battle", _evidence())


def test_workflow_evidence_requires_exact_aware_owner_and_observation():
    evidence = _evidence()
    assert validate_workflow_evidence(evidence) == evidence

    naive = {**evidence, "observed_at": "2026-08-07T12:00:00"}
    assert validate_workflow_evidence(naive) is None
    unknown_target = {**evidence, "adb_target": "unknown"}
    assert validate_workflow_evidence(unknown_target) is None
    mismatched_classification = {**evidence, "game_state": "active_battle"}
    assert validate_workflow_evidence(mismatched_classification) is None


def test_ui_fallback_receipts_bind_running_home_and_terminal_workflows():
    running = _evidence(game_state="active_battle")
    running_receipt = build_running_ui_reconciliation_receipt(
        kind="running_attachment_reconciliation",
        workflow_id="attach-1",
        observation_id=str(running["observation_id"]),
        evidence=running,
        disposition="attachment_baseline",
        reason="unsupported_save_version",
        fallback_complete=True,
    )
    assert validate_save_reconciliation_receipt(running_receipt) == (
        running_receipt
    )
    assert ui_reconciliation_receipt_matches_evidence(
        running_receipt,
        running,
    )

    home = _evidence(game_state="home_new_battle")
    home_receipt = build_home_ui_reconciliation_receipt(
        workflow_id="return-home-1",
        observation_id=str(home["observation_id"]),
        evidence=home,
        reason="save_mapping_unavailable",
        resolved_check_ids=("workshop_preset",),
    )
    assert home_receipt["ui_fallback"]["source"] == (
        "home_configuration_ui"
    )
    assert ui_reconciliation_receipt_matches_evidence(home_receipt, home)

    terminal = _evidence(game_state="game_over")
    terminal_receipt = build_terminal_ui_reconciliation_receipt(
        workflow_id="return-terminal-1",
        observation_id=str(terminal["observation_id"]),
        evidence=terminal,
        killed_by="Boss",
        reason="terminal_save_report_unavailable",
    )
    assert terminal_receipt["terminal"]["collection"] == "full"
    assert ui_reconciliation_receipt_matches_evidence(
        terminal_receipt,
        terminal,
    )

    refreshed = {
        **running,
        "observation_id": "runtime-1:next-heartbeat",
        "observed_at": "2026-08-07T12:00:01+00:00",
    }
    assert ui_reconciliation_receipt_matches_evidence(
        running_receipt,
        refreshed,
    )

    changed = dict(running)
    changed["target_generation"] = 8
    assert not ui_reconciliation_receipt_matches_evidence(
        running_receipt,
        changed,
    )


def test_directive_store_rejects_mismatched_intent_and_serializes_workflows(
    tmp_path,
):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    evidence = _evidence()

    with pytest.raises(ValueError, match="does not match"):
        store.request_battle_workflow(
            "attach_battle",
            evidence=evidence,
        )

    first = store.request_battle_workflow(
        "start_battle",
        evidence=evidence,
        source="test",
    )
    with pytest.raises(ValueError, match="already in progress"):
        store.request_battle_workflow(
            "start_battle",
            evidence=evidence,
        )

    acknowledged = store.transition_battle_workflow(
        first["request_id"],
        "awaiting_enable",
        reason="Pause still owns input",
    )
    assert acknowledged is not None
    assert acknowledged["status"] == "awaiting_enable"
    assert store.transition_battle_workflow(
        "not-current", "interrupted"
    ) is None
    with pytest.raises(ValueError, match="cannot transition"):
        store.transition_battle_workflow(first["request_id"], "completed")


def test_take_manual_control_atomically_requests_indefinite_pause(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    store.set_state("RUNNING", source="test")
    workflow = store.request_battle_workflow(
        "start_battle",
        evidence=_evidence(),
        source="test",
    )

    manual = store.request_manual_control(
        evidence=_evidence(observation_id="runtime-1:2"),
        source="test",
    )
    current = store.status()

    assert manual["status"] == "pause_requested"
    assert current["state"] == "PAUSED"
    assert current["resume_at"] is None
    assert current["battle_workflow"]["request_id"] == workflow["request_id"]
    assert current["battle_workflow"]["status"] == "interrupted"

    active = store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=_evidence(observation_id="runtime-1:3"),
    )
    assert active is not None
    returned = store.request_return_control(
        manual["manual_control_id"],
        evidence=_evidence(observation_id="runtime-1:4"),
    )
    assert returned["status"] == "return_requested"
    assert store.status()["state"] == "PAUSED"


def test_control_surface_exposes_separate_dimensions_and_exact_actions(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence()
    _publish_runtime_observation(service, evidence)

    status = service.status()
    model = status["control_model"]
    assert model["process"]["state"] == "live"
    assert model["action_authority"]["effective"] == "paused"
    assert model["observation"]["game_state"] == "home_new_battle"
    assert model["strategy_scope"]["startup_default"]
    assert model["when_battle_ends"]["compatibility_value"] == "NEXT_BATTLE"
    assert model["actions"]["start_battle"]["available"] is True
    assert model["actions"]["attach_battle"]["available"] is False

    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.apply_control({"action": "attach_battle"})
    assert exc_info.value.code == "intent_mismatch"

    response = service.apply_control({"action": "start_battle"})
    assert response["request"]["disposition"] == "requested"
    assert response["control_model"]["battle_workflow"]["intent"] == (
        "start_battle"
    )
    with pytest.raises(ControlSurfaceRequestError) as busy:
        service.apply_control({"action": "start_battle"})
    assert busy.value.code == "workflow_busy"


def test_control_surface_reports_terminal_home_entitlement_separately(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    _publish_runtime_observation(
        service,
        _evidence(),
        explicit_home_intent_required=True,
        terminal_home_continuation={
            "pending": True,
            "source": "no_strategy_post_run",
            "terminal_observation_id": "runtime-1:terminal",
        },
    )

    home = service.status()["control_model"]["home_behavior"]

    assert home["explicit_intent_required"] is True
    assert home["terminal_continuation"] == {
        "pending": True,
        "source": "no_strategy_post_run",
        "terminal_observation_id": "runtime-1:terminal",
    }
    assert "matching explicit battle intent" in home["meaning"]


@pytest.mark.parametrize(
    ("game_state", "start_available", "attach_available"),
    [
        ("home_new_battle", True, False),
        ("home_resume_battle", False, True),
        ("active_battle", False, True),
        ("game_over", False, False),
        ("tournament_results", False, False),
        ("unknown", False, False),
    ],
)
@pytest.mark.parametrize("paused", [True, False])
def test_server_intent_availability_matrix_is_state_exact(
    tmp_path,
    game_state,
    start_available,
    attach_available,
    paused,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state=game_state)
    _publish_runtime_observation(service, evidence, paused=paused)

    model = service.status()["control_model"]

    assert model["process"]["state"] == "live"
    assert model["action_authority"]["effective"] == (
        "paused" if paused else "pending"
    )
    assert model["observation"]["game_state"] == game_state
    assert model["actions"]["start_battle"]["available"] is start_available
    assert model["actions"]["attach_battle"]["available"] is attach_available


def test_stopped_process_rejects_action_authority_and_battle_workflows(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    service._runtime_evidence = lambda: {"active": False, "instances": []}

    model = service.status()["control_model"]

    assert model["process"]["state"] == "stopped"
    for action in ("pause", "enable", "start_battle", "attach_battle"):
        assert model["actions"][action]["available"] is False
        assert model["actions"][action]["code"] == "process_stopped"
        with pytest.raises(ControlSurfaceRequestError) as stopped:
            service.apply_control({"action": action})
        assert stopped.value.code == "process_stopped"


def test_adopted_active_battle_makes_redundant_attach_unavailable(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    _publish_runtime_observation(
        service,
        _evidence(game_state="active_battle"),
        active_battle_adopted=True,
    )

    attach = service.status()["control_model"]["actions"]["attach_battle"]

    assert attach["available"] is False
    assert attach["code"] == "battle_already_adopted"


def test_strategy_scope_keeps_active_and_pending_values_separate(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    service.control_store.set_strategy("farm_t19", source="test")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    service.action_log.write_text(
        "[INFO "
        + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        + "] [CTRL] Strategy set to farm_t18 via control file\n",
        encoding="utf-8",
    )
    _publish_runtime_observation(
        service,
        _evidence(game_state="active_battle"),
        active_battle_adopted=True,
        active_strategy="farm_t18",
    )

    scope = service.status()["control_model"]["strategy_scope"]

    assert scope["startup_default"] == "farm_t19"
    assert scope["active_battle"] == "farm_t18"
    assert scope["pending_next_boundary"] == "farm_t19"


def test_start_battle_atomically_rearms_normal_tournament_gates(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    selected = service.control_store.set_strategy(
        "tournament",
        source="test",
    )
    old_request_id = selected["exclusive_validation"]["current_request_id"]
    old_strategy_request_id = selected["strategy_request_id"]
    service.control_store.finish_exclusive_validation(
        old_request_id,
        outcome="cancelled",
        reason="completed prior request",
        allowed_statuses=("pending",),
    )
    _publish_runtime_observation(service, _evidence())

    response = service.apply_control({"action": "start_battle"})

    control = response["control"]
    ledger = control["exclusive_validation"]
    new_request_id = ledger["current_request_id"]
    assert response["control_model"]["battle_workflow"]["status"] == (
        "requested"
    )
    assert control["strategy_request_id"] != old_strategy_request_id
    assert new_request_id != old_request_id
    assert ledger["receipts"][new_request_id]["status"] == "pending"
    assert ledger["receipts"][new_request_id]["strategy_request_id"] == (
        control["strategy_request_id"]
    )


def test_control_surface_rejects_stale_runtime_observation(tmp_path):
    service = ControlSurfaceService(
        repository_root=tmp_path,
        stale_after_seconds=1,
    )
    _publish_runtime_observation(
        service,
        _evidence(),
        published_at=1.0,
    )

    model = service.status()["control_model"]
    assert model["observation"]["freshness"] == "stale"
    assert model["actions"]["start_battle"] == {
        "available": False,
        "code": "fresh_observation_unavailable",
        "reason": "fresh runtime-owned game observation is required",
    }
    with pytest.raises(ControlSurfaceRequestError) as stale:
        service.apply_control({"action": "start_battle"})
    assert stale.value.code == "fresh_observation_unavailable"


def test_fresh_authority_heartbeat_cannot_refresh_an_old_game_observation(
    tmp_path,
):
    now = datetime.now(timezone.utc).timestamp()
    service = ControlSurfaceService(
        repository_root=tmp_path,
        stale_after_seconds=1,
    )
    evidence = _evidence()
    evidence["observed_at"] = datetime.fromtimestamp(
        now - 10,
        tz=timezone.utc,
    ).astimezone().isoformat(timespec="seconds")
    _publish_runtime_observation(service, evidence, published_at=now)

    model = service.status(now=now)["control_model"]

    assert model["observation"]["freshness"] == "stale"
    assert model["observation"]["age_seconds"] >= 9
    assert "authority heartbeat is current" in model["observation"]["reason"]
    assert model["workflow_evidence"] is None
    assert model["actions"]["start_battle"]["code"] == (
        "fresh_observation_unavailable"
    )


def test_control_surface_take_and_return_control_require_pause_ack(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="active_battle")
    _publish_runtime_observation(service, evidence)

    taken = service.apply_control({"action": "take_manual_control"})
    manual = taken["control_model"]["manual_control"]
    assert manual["status"] == "pause_requested"
    repeated = service.apply_control({"action": "take_manual_control"})
    assert repeated["request"]["disposition"] == "no_op"
    with pytest.raises(ControlSurfaceRequestError) as unacknowledged:
        service.apply_control({"action": "return_control"})
    assert unacknowledged.value.code == "manual_control_not_acknowledged"

    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    returned = service.apply_control({"action": "return_control"})
    assert returned["control_model"]["manual_control"]["status"] == (
        "return_requested"
    )
    assert returned["control"]["state"] == "PAUSED"


def test_operator_startup_waits_for_matching_intent_and_enable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("PAUSED", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(
        None,
        None,
        await_initial_battle_intent=True,
    )
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(runtime_id=str(owner["runtime_id"]))
    evidence["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=evidence)
    supervisor.apply_control()

    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "awaiting_enable"
    assert manager.awaiting_initial_battle_intent() is True

    store.set_state("RUNNING", source="test")
    supervisor.apply_control()
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "acknowledged"
    assert manager.awaiting_initial_battle_intent() is False
    assert manager.maybe_run_start(
        {
            "state": "HOME_SCREEN",
            "home_battle_control": "NEW_BATTLE",
        }
    ) is False
    assert manager.maybe_run_start({"state": "RUNNING"}) is True


@pytest.mark.parametrize("active_battle", (False, True))
def test_idle_home_intent_hold_exposes_only_home_ad_gem(active_battle):
    app = App.__new__(App)
    app._supervisor = SimpleNamespace(
        manual_control_error=False,
        battle_workflow_error=False,
        setup_capture_error=False,
        setup_capture=None,
        manual_control=None,
        battle_workflow=None,
    )
    app._awaiting_initial_battle_intent = MagicMock(return_value=True)

    hold = app._operator_workflow_authority_hold()

    assert isinstance(hold, AuthorityHoldState)
    assert hold.hold is AuthorityHold.OPERATOR_WORKFLOW
    assert hold.allowed_auxiliary_collectors == (
        AuxiliaryCollector.HOME_AD_GEM,
    )
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=active_battle,
        battle_scope="run-1",
        primary_state="HOME_SCREEN",
        holds=(hold,),
    )
    assert authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.HOME_AD_GEM,
    ).allowed is True
    assert authority.decision(
        RuntimeActionClass.AUXILIARY_COLLECTION,
        collector=AuxiliaryCollector.IN_BATTLE_AD_GEM,
    ).allowed is False


def test_start_dispatch_survives_preflight_scope_change_and_completes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH", str(tmp_path / "actions.log")
    )
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    requested = _evidence(runtime_id=str(owner["runtime_id"]))
    requested["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=requested)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in requested.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "acknowledged"
    workflow_hold = app._operator_workflow_authority_hold()
    assert workflow_hold is not None
    assert workflow_hold.hold is AuthorityHold.OPERATOR_WORKFLOW
    assert workflow_hold.allowed_auxiliary_collectors == ()
    app._update_action_authority(holds=(workflow_hold,))
    assert app._action_decision(
        RuntimeActionClass.STRATEGY_ACTION
    ).allowed is False
    assert app._action_decision(
        RuntimeActionClass.LIFECYCLE_ACTION,
        owner=AuthorityHold.OPERATOR_WORKFLOW,
    ).allowed is True

    preflight = _evidence(
        observation_id="runtime-1:2",
        runtime_id=str(owner["runtime_id"]),
        scope="scope-preflight",
    )
    preflight["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in preflight.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "acknowledged"
    assert manager.maybe_run_start(
        {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
    ) is False
    assert app._mark_operator_battle_action_dispatched(True) is True
    assert supervisor.battle_workflow["status"] == "action_dispatched"
    assert app._operator_workflow_authority_hold() is not None

    running = _evidence(
        game_state="active_battle",
        observation_id="runtime-1:3",
        runtime_id=str(owner["runtime_id"]),
        scope="scope-preflight",
    )
    running["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in running.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert supervisor.battle_workflow["status"] == "action_dispatched"
    battle_started = manager.maybe_run_start({"state": "RUNNING"})
    assert battle_started is True
    start_workflow_completed = app._complete_started_battle_workflow(
        battle_started
    )
    assert start_workflow_completed is True
    assert supervisor.battle_workflow["status"] == "completed"

    # The authority snapshot still contains the retired launch hold until the
    # main loop publishes the first RUNNING-frame owners. Evidence binding is
    # observational, so that stale action hold and terminal policy are not
    # allowed to destroy the exact launch transition.
    assert app._runtime_action_guard(
        action_class=RuntimeActionClass.LIFECYCLE_ACTION
    ) is False
    coordinator = MagicMock()
    coordinator.bind_running.return_value = True
    app._player_save_preflight_coordinator = coordinator
    AUTOMATION.state = RunState.RUNNING
    for mode in (ExecMode.NEXT_BATTLE, ExecMode.HOME, ExecMode.WAIT):
        coordinator.reset_mock()
        coordinator.bind_running.return_value = True
        AUTOMATION.mode = mode
        assert app._bind_started_battle_player_save_preflight(
            battle_started=True,
            stable_running=True,
        ) is True
        coordinator.bind_running.assert_called_once_with(
            battle_started=True,
            stable_running=True,
            continuity_verified=True,
        )
        assert (
            manager.ctx.data["player_save_preflight_coordinator"]
            is coordinator
        )

    coordinator.reset_mock()
    AUTOMATION.mode = ExecMode.NEXT_BATTLE
    store.request_battle_workflow("attach_battle", evidence=running)
    supervisor.apply_control()
    assert app._bind_started_battle_player_save_preflight(
        battle_started=True,
        stable_running=True,
    ) is False
    coordinator.bind_running.assert_not_called()
    coordinator.discard_carry.assert_called_once_with(
        "competing_workflow_at_running_boundary"
    )
    assert supervisor.battle_workflow["intent"] == "attach_battle"
    assert supervisor.battle_workflow["status"] == "requested"


def test_verified_retry_scope_stages_terminal_save_for_current_strategy():
    app = App.__new__(App)
    coordinator = MagicMock()
    carry = object()
    result = SimpleNamespace(carry=carry)
    coordinator.stage_direct_retry.return_value = result
    strategy = MagicMock()
    strategy.session_preflight_requirements.return_value = {
        "auto_pick_perks": True
    }
    manager = MagicMock()
    manager.strategy = strategy
    manager.ctx.data = {"player_save_preflight_coordinator": "old"}
    app._mission_mgr = manager
    app._player_save_preflight_coordinator = coordinator
    app._player_save_preflight_session_id = ""
    app._runtime_policy = lambda: {"player_save_preflight": "save_first"}
    acquisition = MagicMock(spec=PlayerSaveAcquisitionBundle)

    assert app._stage_direct_retry_player_save_preflight(
        acquisition,
        source_activity_scope_id="source-scope",
        retry_scope={"reason": "game_over_retry", "run_id": "retry-scope"},
    )

    assert app._player_save_preflight_session_id
    assert app._player_save_preflight_result is result
    assert app._player_save_preflight_activity_scope_id == "retry-scope"
    coordinator.stage_direct_retry.assert_called_once_with(
        acquisition,
        {"auto_pick_perks": True},
        source_activity_scope_id="source-scope",
        mode="save_first",
    )


def test_retry_save_staging_failure_preserves_retry_and_uses_ui_fallback():
    app = App.__new__(App)
    coordinator = MagicMock()
    coordinator.stage_direct_retry.side_effect = RuntimeError("projection bug")
    strategy = MagicMock()
    strategy.session_preflight_requirements.return_value = {
        "auto_pick_perks": True
    }
    manager = MagicMock()
    manager.strategy = strategy
    manager.ctx.data = {"player_save_preflight_coordinator": coordinator}
    app._mission_mgr = manager
    app._player_save_preflight_coordinator = coordinator
    app._player_save_preflight_session_id = ""
    app._runtime_policy = lambda: {"player_save_preflight": "save_first"}

    with patch("core.app.log") as emit:
        assert not app._stage_direct_retry_player_save_preflight(
            MagicMock(spec=PlayerSaveAcquisitionBundle),
            source_activity_scope_id="source-scope",
            retry_scope={"reason": "game_over_retry", "run_id": "retry-scope"},
        )

    coordinator.discard_carry.assert_called_once_with(
        "direct_retry_save_staging_failed"
    )
    assert "player_save_preflight_coordinator" not in manager.ctx.data
    assert "projection bug" not in emit.call_args.args[0]


@pytest.mark.parametrize(
    ("state", "method", "reason"),
    [
        (
            RunState.PAUSED,
            "suspend_carry",
            "pause_requires_fresh_running_evidence",
        ),
        (
            RunState.STOPPED,
            "discard_carry",
            "automation_stopped_before_running_bind",
        ),
    ],
)
def test_running_boundary_pause_or_stop_never_consumes_carried_save(
    state,
    method,
    reason,
):
    app = App.__new__(App)
    coordinator = MagicMock()
    coordinator.carry = SimpleNamespace(
        state=CarriedEvidenceState.LAUNCH_DISPATCHED
    )
    app._player_save_preflight_coordinator = coordinator
    app._operator_workflow_authority_hold = lambda: None
    AUTOMATION.state = state

    assert not app._bind_started_battle_player_save_preflight(
        battle_started=True,
        stable_running=True,
    )

    getattr(coordinator, method).assert_called_once_with(reason)
    coordinator.bind_running.assert_not_called()


@pytest.mark.parametrize("changed_state", ["home_resume_battle", "game_over"])
def test_dispatched_start_interrupts_on_a_definitive_wrong_boundary(
    tmp_path,
    monkeypatch,
    changed_state,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    requested = _evidence(runtime_id=str(owner["runtime_id"]))
    requested["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=requested)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in requested.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert app._mark_operator_battle_action_dispatched(True) is True

    changed = _evidence(
        game_state=changed_state,
        observation_id="runtime-1:changed-after-dispatch",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows(
        {"state": str(changed["primary_state"])}
    )

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True


def test_dispatched_start_fails_closed_after_bounded_home_timeout(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(runtime_id=str(owner["runtime_id"]))
    evidence["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=evidence)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert app._mark_operator_battle_action_dispatched(True) is True
    dispatched_at = datetime.fromisoformat(
        str(supervisor.battle_workflow["updated_at"])
    )
    app._control_observation = {
        **app._control_observation,
        "observation_id": "runtime-1:timeout",
        "observed_at": (
            dispatched_at + timedelta(seconds=21)
        ).isoformat(timespec="seconds"),
    }

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    assert supervisor.battle_workflow["status"] == "failed"
    assert "within 20 seconds" in supervisor.battle_workflow["reason"]


def test_dispatched_resumable_attach_completes_after_same_battle_adoption(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_resume_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(
        "attach_battle",
        evidence=evidence,
    )
    for status in ("acknowledged", "validating_save"):
        store.transition_battle_workflow(
            workflow["request_id"],
            status,
            acknowledgement=evidence,
        )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert app._mark_operator_battle_action_dispatched(True) is True

    active = _evidence(
        game_state="active_battle",
        observation_id="runtime-1:active",
        runtime_id=str(owner["runtime_id"]),
        scope=str(evidence["activity_scope_run_id"]),
    )
    active["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in active.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    claim = _running_save_claim(workflow["request_id"], active)
    store.transition_battle_workflow(
        workflow["request_id"],
        "ready",
        acknowledgement=active,
        save_receipt=claim[0],
    )
    _retain_running_save_claim(
        app,
        workflow["request_id"],
        active,
        claim,
    )
    supervisor.apply_control()
    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert manager.maybe_run_start({"state": "RUNNING"}) is False

    assert app._complete_ready_attachment_after_adoption() is True
    assert supervisor.battle_workflow["status"] == "completed"


def test_unusable_attach_save_releases_enabled_ui_monitoring(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(
        "attach_battle",
        evidence=evidence,
        source="test",
    )
    for status in ("acknowledged", "validating_save"):
        store.transition_battle_workflow(
            workflow["request_id"],
            status,
            acknowledgement=evidence,
        )
    supervisor.apply_control()
    manager = MagicMock()
    manager.strategy = None
    manager.active_battle_observed.return_value = True
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._apply_activity_continuity_outcome(
        SimpleNamespace(
            ui_monitoring_fallback=True,
            ui_fallback_complete=True,
            ui_fallback_reason="unsupported_save_version",
            confirmed_same_battle_scope_id="scope-1",
            confirmed_later_battle_scope_id=None,
        )
    )

    ready = supervisor.battle_workflow
    assert ready["status"] == "ready"
    assert ready["save_receipt"]["ui_fallback"]["source"] == (
        "battle_history_ui"
    )
    assert app._complete_ready_attachment_after_adoption() is True
    assert supervisor.battle_workflow["status"] == "completed"
    assert supervisor.is_paused is False


def test_return_control_stays_input_blocked_during_reconciliation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert supervisor.manual_control["status"] == "active"
    store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    supervisor.apply_control()
    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert supervisor.manual_control["status"] == "awaiting_enable"

    store.enable_after_return_control(
        manual["manual_control_id"],
        source="test",
    )
    supervisor.apply_control()
    app._sync_operator_control_workflows({"state": "RUNNING"})

    assert supervisor.manual_control["status"] == "reconciling"
    hold = app._operator_workflow_authority_hold()
    assert hold is not None
    assert hold.hold.value == "manual_control_return"


@pytest.mark.parametrize(
    ("status", "reason", "background_dispatched", "expected_status"),
    (
        ("blocked", "action_not_authorized", False, "failed"),
        (
            "blocked",
            "restored_target_or_new_battle_boundary_unverified",
            True,
            "interrupted",
        ),
        ("ready", "stable_save_unavailable", True, "failed"),
    ),
)
def test_home_return_refresh_failure_pauses_terminally_without_repeating_input(
    tmp_path,
    monkeypatch,
    status,
    reason,
    background_dispatched,
    expected_status,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_new_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    store.enable_after_return_control(
        manual["manual_control_id"],
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)
    app._adb_target_session = None
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    result = SimpleNamespace(
        status=SimpleNamespace(value=status),
        reason=reason,
        ready=status == "ready",
        provenance={
            "serialization": (
                "verified_android_home_boundary"
                if background_dispatched and status == "ready"
                else "background_dispatched"
                if background_dispatched
                else "not_attempted"
            ),
            "background_dispatched": background_dispatched,
        },
        acquisition=None,
        context=None,
    )
    acquisitions = []
    app._acquire_player_save_home_preflight = (
        lambda *_args, **_kwargs: acquisitions.append(result) or result
    )
    app._run_home_setup_attempts = lambda *_args, **_kwargs: pytest.fail(
        "configuration UI must not run without an exact save receipt"
    )

    assert app._handle_home_return_reconciliation(screenshot=object()) is True
    assert app._handle_home_return_reconciliation(screenshot=object()) is False

    terminal = supervisor.manual_control
    assert terminal["status"] == expected_status
    assert "Automation remains Paused" in terminal["detail"]
    assert supervisor.is_paused is True
    assert acquisitions == [result]


def test_home_return_uses_ui_when_restored_save_is_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_new_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    store.enable_after_return_control(
        manual["manual_control_id"],
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MagicMock()
    app._mission_mgr.strategy = SimpleNamespace(
        session_preflight_requirements=lambda: {
            "workshop_preset": "Farm"
        }
    )
    app._startup_gate_waivers = {}
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    context = object()
    app._current_player_save_preflight_context = lambda: context
    result = SimpleNamespace(
        ready=True,
        reason="stable_save_unavailable",
        safe_ui_fallback=True,
        acquisition=None,
        context=context,
        decisions={
            "workshop_preset": {
                "disposition": "ui_required",
                "reason": "stable_save_unavailable",
            }
        },
        as_dict=lambda: {"safe_ui_fallback": True},
    )
    app._run_home_setup_attempts = MagicMock(
        return_value=GcNoBattleSetupResult(
            GcNoBattleSetupStatus.COMPLETE,
            "verified through UI",
            evidence={"workshop_preset": "Farm"},
        )
    )

    assert app._complete_home_return_reconciliation(
        result,
        screenshot=object(),
    ) is True

    completed = supervisor.manual_control
    assert completed["status"] == "completed"
    assert completed["refresh_status"] == (
        "home_ui_fallback_reconciliation_complete"
    )
    assert completed["save_receipt"]["ui_fallback"]["source"] == (
        "home_configuration_ui"
    )
    assert supervisor.is_paused is False
    app._run_home_setup_attempts.assert_called_once()


def test_home_return_reports_nonretryable_setup_for_manual_correction(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_new_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MagicMock()
    app._mission_mgr.strategy = SimpleNamespace(
        session_preflight_requirements=lambda: {
            "perk_auto_pick_order": ["chrono_field_duration"]
        }
    )
    app._startup_gate_waivers = {}
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    context = object()
    app._current_player_save_preflight_context = lambda: context
    captured = datetime.now(timezone.utc)
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="captured",
        binding=PlayerSaveTargetBinding("localhost:5555", 7),
        acquisition_started_at=captured - timedelta(milliseconds=1),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=SimpleNamespace(),
    )
    result = SimpleNamespace(
        ready=True,
        acquisition=acquisition,
        context=context,
        decisions={
            "perk_auto_pick_order": {
                "disposition": "ui_required",
                "reason": "current order needs UI validation",
            }
        },
    )
    setup = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.FAILED,
        "Auto Pick repair made no stable progress",
        failed_check="perk_auto_pick_order",
        retryable_from_home=False,
    )
    app._run_home_setup_attempts = MagicMock(return_value=setup)

    assert app._complete_home_return_reconciliation(
        result,
        screenshot=object(),
    ) is True

    blocked = supervisor.manual_control
    assert blocked["status"] == "awaiting_manual_correction"
    assert blocked["refresh_status"] == "manual_correction_required"
    assert blocked["configuration"]["failed_check"] == (
        "perk_auto_pick_order"
    )
    assert blocked["configuration"]["retryable_from_home"] is False
    assert "made no stable progress" in blocked["detail"]
    assert blocked["save_receipt"]["acquisition"]["type"] == (
        "forced_serialization"
    )
    assert supervisor.is_paused is True
    app._run_home_setup_attempts.assert_called_once()
    assert app._complete_home_return_reconciliation(
        result,
        screenshot=object(),
    ) is False
    app._run_home_setup_attempts.assert_called_once()


def test_post_serialization_interruption_terminates_attach_and_pauses(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(
        "attach_battle",
        evidence=evidence,
        source="test",
    )
    store.transition_battle_workflow(
        workflow["request_id"],
        "acknowledged",
        acknowledgement=evidence,
    )
    store.transition_battle_workflow(
        workflow["request_id"],
        "validating_save",
        acknowledgement=evidence,
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._apply_activity_continuity_outcome(
        SimpleNamespace(
            operator_workflow_interruption_reason=(
                "active_attachment_restored_source_boundary_unverified"
            )
        )
    )

    assert supervisor.is_paused is True
    assert supervisor.battle_workflow["status"] == "interrupted"
    assert "Automation remains Paused" in supervisor.battle_workflow["reason"]


def test_identity_projection_failure_terminates_return_and_discards_claim(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH",
        str(tmp_path / "logs" / "actions.log"),
    )
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._manual_return_reconciliation_claims = {
        manual["manual_control_id"]: {"private": "typed claim"}
    }

    app._apply_activity_continuity_outcome(
        SimpleNamespace(
            operator_workflow_interruption_reason=(
                "active_attachment_temporal_projection_unavailable"
            )
        )
    )

    assert supervisor.is_paused is True
    assert supervisor.manual_control["status"] == "failed"
    assert supervisor.manual_control["refresh_status"] == (
        "save_restoration_interrupted"
    )
    assert app._manual_return_reconciliation_claims == {}


def test_running_return_trusted_save_mismatch_pauses_without_ui_fallback(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    (
        app,
        supervisor,
        manager,
        _evidence_value,
        acquisition,
        temporal,
        observations,
        context,
    ) = _running_return_fixture(
        tmp_path,
        snapshot=_player_save_snapshot("workshop_preset", "Tourney"),
        observed_value="Tourney",
    )

    completed = app._complete_save_backed_operator_reconciliation(
        outcome=SimpleNamespace(
            confirmed_same_battle_scope_id="scope-1",
            confirmed_later_battle_scope_id=None,
        ),
        acquisition=acquisition,
        temporal_binding=temporal,
        observations=observations,
        context=context,
    )

    assert completed is True
    manual = supervisor.manual_control
    assert manual["status"] == "awaiting_configuration"
    assert manual["refresh_status"] == "trusted_mismatch_paused"
    assert manual["configuration"]["trusted_mismatch_check_ids"] == [
        "workshop_preset"
    ]
    assert manual["configuration"]["ui_required_check_ids"] == []
    assert supervisor.is_paused is True
    manager.begin_manual_return_reconciliation.assert_not_called()


def test_unusable_running_return_save_starts_supported_ui_reconciliation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    (
        app,
        supervisor,
        manager,
        _evidence_value,
        _acquisition,
        _temporal,
        _observations,
        _context,
    ) = _running_return_fixture(
        tmp_path,
        snapshot=_player_save_snapshot("workshop_preset", "Farm"),
        observed_value="Farm",
    )
    manager.begin_manual_return_reconciliation.return_value = True

    app._apply_activity_continuity_outcome(
        SimpleNamespace(
            ui_monitoring_fallback=True,
            ui_fallback_complete=False,
            ui_fallback_reason="save_mapping_unavailable",
            confirmed_same_battle_scope_id=None,
            confirmed_later_battle_scope_id=None,
        )
    )

    pending = supervisor.manual_control
    assert pending["status"] == "awaiting_configuration"
    assert pending["refresh_status"] == "configuration_validation_pending"
    assert pending["save_receipt"]["ui_fallback"]["status"] == "degraded"
    assert pending["configuration"]["ui_required_check_ids"] == [
        "workshop_preset"
    ]
    assert supervisor.is_paused is False
    manager.begin_manual_return_reconciliation.assert_called_once_with()


def test_capture_reviews_manual_changes_from_exact_retained_return_save(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    runtime_save = SimpleNamespace(
        round_active=True,
        active_round_identity=SimpleNamespace(fingerprint="a" * 64),
    )
    (
        app,
        supervisor,
        _manager,
        evidence,
        acquisition,
        temporal,
        observations,
        context,
    ) = _running_return_fixture(
        tmp_path,
        snapshot=_player_save_snapshot(
            "workshop_preset",
            "Tourney",
            runtime_save=runtime_save,
        ),
        observed_value="Tourney",
    )
    assert app._complete_save_backed_operator_reconciliation(
        outcome=SimpleNamespace(
            confirmed_same_battle_scope_id="scope-1",
            confirmed_later_battle_scope_id=None,
        ),
        acquisition=acquisition,
        temporal_binding=temporal,
        observations=observations,
        context=context,
    ) is True
    manual = supervisor.manual_control
    assert manual["status"] == "awaiting_configuration"
    assert supervisor.is_paused is True

    service = ControlSurfaceService(
        repository_root=tmp_path,
        control_file=supervisor.control_file,
    )
    _publish_runtime_observation(
        service,
        evidence,
        paused=True,
        active_battle_adopted=True,
        active_strategy="active-farm",
    )
    availability = service.status()["control_model"]["actions"][
        "capture_current_setup"
    ]
    assert availability["available"] is True
    assert availability["code"] == "available_from_return_control"

    requested = service.apply_setup_capture({"operation": "request"})
    capture = requested["capture"]
    assert capture["acquisition_source"] == (
        "retained_return_control_refresh"
    )
    assert capture["source_manual_control_id"] == manual[
        "manual_control_id"
    ]
    supervisor.apply_control()

    monkeypatch.setattr(
        "core.app.GuardedPlayerSaveSerializer",
        lambda **_kwargs: pytest.fail(
            "retained Return Control evidence must not request another refresh"
        ),
    )
    monkeypatch.setattr(
        "core.app.project_forced_save_setup",
        lambda bundle: (
            _capture_preview(evidence=evidence, acquisition=bundle)
            if bundle is acquisition
            else pytest.fail("capture used a different acquisition")
        ),
    )
    app._setup_capture_source_refreshed = False
    app._log_operator_workflow_result = lambda *_args, **_kwargs: None

    app._sync_operator_control_workflows({"state": "RUNNING"})

    ready = supervisor.setup_capture
    assert ready["status"] == "ready"
    assert "without new device input" in ready["reason"]
    assert ready["preview"]["capture_origin"]["acquisition_source"] == (
        "retained_return_control_refresh"
    )
    assert len(
        ready["preview"]["capture_origin"][
            "source_manual_control_fingerprint"
        ]
    ) == 64
    assert app._setup_capture_source_refreshed is False
    assert supervisor.manual_control["status"] == "awaiting_configuration"
    assert supervisor.is_paused is True


@pytest.mark.parametrize(
    "pending_status",
    ["awaiting_configuration", "awaiting_manual_correction"],
)
def test_api_enable_retries_configuration_with_fresh_save_boundary(
    tmp_path,
    pending_status,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="active_battle")
    manual = service.control_store.request_manual_control(
        evidence=evidence,
        source="test",
    )
    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    service.control_store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "awaiting_configuration",
        refresh_status="trusted_mismatch_paused",
    )
    if pending_status == "awaiting_manual_correction":
        service.control_store.transition_manual_control(
            manual["manual_control_id"],
            "awaiting_manual_correction",
            detail="make the reported manual correction",
            refresh_status="manual_correction_required",
        )
    service.control_store.set_state("PAUSED", source="test")
    _publish_runtime_observation(service, evidence, paused=True)

    availability = service.status()["control_model"]["actions"]["enable"]
    response = service.apply_control({"action": "enable"})

    retried = service.control_store.status()["manual_control"]
    assert availability["available"] is True
    if pending_status == "awaiting_manual_correction":
        assert "manual correction" in availability["reason"]
    assert response["request"]["accepted"] is True
    assert retried["status"] == pending_status
    assert retried["refresh_status"] == "configuration_retry_after_enable"
    assert service.control_store.status()["state"] == "RUNNING"


def test_manual_correction_enable_discards_prior_claim_before_new_home_save(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_new_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "awaiting_configuration",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "awaiting_manual_correction",
        detail="make the reported manual correction",
        refresh_status="manual_correction_required",
    )
    store.set_state("PAUSED", source="test")
    store.enable_after_return_control(
        manual["manual_control_id"],
        source="test",
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MagicMock()
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._manual_return_reconciliation_claims = {
        manual["manual_control_id"]: {"stale": "typed claim"}
    }
    app._manual_return_configuration_authorized_id = None
    app._log_operator_workflow_result = lambda *_args, **_kwargs: None

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    assert supervisor.manual_control["status"] == "reconciling"
    assert app._manual_return_reconciliation_claims == {}
    assert app._manual_return_configuration_authorized_id == (
        manual["manual_control_id"]
    )
    acquisition = MagicMock(return_value=None)
    app._acquire_player_save_home_preflight = acquisition
    app._run_home_setup_attempts = lambda *_args, **_kwargs: pytest.fail(
        "UI cannot run before the new save acquisition"
    )

    assert app._handle_home_return_reconciliation(screenshot=object()) is True
    acquisition.assert_called_once()
    assert supervisor.manual_control["status"] == "failed"


def test_running_return_save_match_completes_without_using_queued_strategy(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    (
        app,
        supervisor,
        manager,
        _evidence_value,
        acquisition,
        temporal,
        observations,
        context,
    ) = _running_return_fixture(
        tmp_path,
        snapshot=_player_save_snapshot("workshop_preset", "Farm"),
        observed_value="Farm",
    )
    app._pending_strategy_request = (
        "tournament",
        "queued-request",
        "next_boundary",
    )

    completed = app._complete_save_backed_operator_reconciliation(
        outcome=SimpleNamespace(
            confirmed_same_battle_scope_id="scope-1",
            confirmed_later_battle_scope_id=None,
        ),
        acquisition=acquisition,
        temporal_binding=temporal,
        observations=observations,
        context=context,
    )

    assert completed is True
    assert supervisor.manual_control["status"] == "completed"
    assert supervisor.manual_control["configuration"]["status"] == "complete"
    assert supervisor.is_paused is False
    manager.begin_manual_return_reconciliation.assert_not_called()


def test_running_return_persists_forced_save_before_ui_fallback_is_armed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    (
        app,
        supervisor,
        manager,
        _evidence_value,
        acquisition,
        temporal,
        observations,
        context,
    ) = _running_return_fixture(
        tmp_path,
        snapshot=_player_save_snapshot(
            "workshop_preset",
            "Farm",
            complete=False,
        ),
        observed_value="Farm",
    )

    def _assert_save_was_persisted_first():
        manual = supervisor.manual_control
        assert manual["status"] == "awaiting_configuration"
        assert manual["save_receipt"]["acquisition"]["status"] == "complete"
        assert manual["configuration"]["ui_required_check_ids"] == [
            "workshop_preset"
        ]
        return True

    manager.begin_manual_return_reconciliation.side_effect = (
        _assert_save_was_persisted_first
    )

    completed = app._complete_save_backed_operator_reconciliation(
        outcome=SimpleNamespace(
            confirmed_same_battle_scope_id="scope-1",
            confirmed_later_battle_scope_id=None,
        ),
        acquisition=acquisition,
        temporal_binding=temporal,
        observations=observations,
        context=context,
    )

    assert completed is True
    assert supervisor.manual_control["status"] == "awaiting_configuration"
    assert supervisor.is_paused is False
    manager.begin_manual_return_reconciliation.assert_called_once_with()


def test_terminal_return_write_retry_does_not_repeat_save_or_ui_work(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    active = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    active["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=active, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=active,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=active,
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "awaiting_enable",
    )
    store.enable_after_return_control(
        manual["manual_control_id"],
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    supervisor.apply_control()
    terminal = _evidence(
        game_state="game_over",
        observation_id="runtime-1:terminal",
        runtime_id=str(owner["runtime_id"]),
    )
    terminal["pid"] = owner["pid"]
    acquisition = _natural_terminal_acquisition(terminal)
    receipt = build_terminal_return_reconciliation_receipt(
        workflow_id=manual["manual_control_id"],
        observation_id=str(terminal["observation_id"]),
        activity_scope_id=str(terminal["activity_scope_run_id"]),
        acquisition=acquisition,
        runtime_session_id=str(terminal["runtime_id"]),
        expected_binding=acquisition.binding,
        killed_by="Surrender",
        collection="minimal",
    )
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MagicMock()
    app._terminal_battle_bundle = MagicMock()
    app._manual_terminal_save_claims = {
        manual["manual_control_id"]: {
            "receipt": receipt,
            "acquisition": acquisition,
            "context": {},
            "evidence": terminal,
            "pending_completion": {
                "detail": "terminal disposition complete",
                "refresh_status": "terminal_reconciliation_complete",
                "save_receipt": receipt,
                "configuration": {
                    "schema_version": 1,
                    "terminal_status": "confirmed_surrender",
                    "collection": "minimal",
                },
            },
        }
    }

    completed = app._retry_pending_manual_terminal_completion(
        supervisor.manual_control,
        terminal,
    )

    assert completed is not None
    assert completed["status"] == "completed"
    app._terminal_battle_bundle.assert_not_called()
    assert app._manual_terminal_claims() == {}


def test_terminal_ui_fallback_completion_retry_preserves_enabled_authority(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    active = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    active["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=active, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=active,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=active,
        source="test",
    )
    store.enable_after_return_control(
        manual["manual_control_id"],
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    supervisor.apply_control()
    terminal = _evidence(
        game_state="game_over",
        observation_id="runtime-1:terminal-ui",
        runtime_id=str(owner["runtime_id"]),
    )
    terminal["pid"] = owner["pid"]
    receipt = build_terminal_ui_reconciliation_receipt(
        workflow_id=manual["manual_control_id"],
        observation_id=str(terminal["observation_id"]),
        evidence=terminal,
        killed_by="Boss",
        reason="terminal_save_report_unavailable",
    )
    app = App.__new__(App)
    app._supervisor = supervisor
    app._manual_terminal_save_claims = {
        manual["manual_control_id"]: {
            "receipt": receipt,
            "evidence": terminal,
            "ui_fallback": True,
            "pending_completion": {
                "detail": "terminal UI fallback complete",
                "refresh_status": (
                    "terminal_ui_fallback_reconciliation_complete"
                ),
                "save_receipt": receipt,
                "configuration": {
                    "schema_version": 1,
                    "terminal_status": "confirmed_other",
                    "collection": "full_ui_fallback",
                },
            },
        }
    }

    completed = app._retry_pending_manual_terminal_completion(
        supervisor.manual_control,
        terminal,
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["save_receipt"]["ui_fallback"]["source"] == (
        "terminal_stats_ui"
    )
    assert supervisor.is_paused is False
    assert app._manual_terminal_claims() == {}


def test_terminal_return_uses_supported_ui_when_save_is_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    active = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    active["pid"] = owner["pid"]
    manual = store.request_manual_control(evidence=active, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=active,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=active,
        source="test",
    )
    store.enable_after_return_control(
        manual["manual_control_id"],
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    supervisor.apply_control()
    terminal = _evidence(
        game_state="game_over",
        observation_id="runtime-1:terminal-fallback",
        runtime_id=str(owner["runtime_id"]),
    )
    terminal["pid"] = owner["pid"]
    supervisor.record_manual_terminal_evidence(
        manual["manual_control_id"],
        {
            "schema_version": 1,
            "status": "unavailable",
            "observation_id": terminal["observation_id"],
            "activity_scope_fingerprint": hashlib.sha256(
                str(terminal["activity_scope_run_id"]).encode("utf-8")
            ).hexdigest(),
            "reason": "terminal_save_report_unavailable",
        },
    )
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MagicMock()
    app._mission_mgr.strategy = SimpleNamespace(name="farm")
    app._mission_mgr.session_preflight_repair_in_progress.return_value = False
    app._status_reporter = MagicMock()
    app._fast_game_over = False
    app._pending_game_over_route = None
    app._manual_terminal_save_claims = {}
    app._current_control_workflow_evidence = lambda: terminal
    app._advance_pending_game_over_route_recovery = lambda *_args: False
    app._handler_enabled = lambda name: name == "game_over"
    app._handle_exclusive_validation_game_over = lambda: False
    app._terminal_battle_bundle = MagicMock(
        return_value=(
            {
                "terminal_save_report": {
                    "status": "unavailable",
                    "reason": "terminal_save_report_unavailable",
                }
            },
            None,
            None,
        )
    )
    app._runtime_action_guard = lambda **_kwargs: True
    app._apply_pending_strategy = MagicMock()
    app._strategy_boundary_confirmed = False
    record = {
        "battle_id": "BattleUiFallback",
        "game_stats": {
            "fields": {"killed_by": {"value": "Boss", "raw": "Boss"}}
        },
    }
    game_over = MagicMock(
        return_value=GameOverHandlingOutcome(
            True,
            "home",
            record,
            "saved",
        )
    )
    monkeypatch.setattr("core.app.handle_game_over", game_over)

    app._handle_primary_states(
        "GAME_OVER",
        set(),
        object(),
        operator_workflow_only=True,
    )

    completed = supervisor.manual_control
    assert completed["status"] == "completed"
    assert completed["refresh_status"] == (
        "terminal_ui_fallback_reconciliation_complete"
    )
    assert completed["save_receipt"]["ui_fallback"]["source"] == (
        "terminal_stats_ui"
    )
    assert completed["configuration"]["collection"] == "full_ui_fallback"
    assert supervisor.is_paused is False
    assert game_over.call_args.kwargs["capture_stats"] is True


def test_attach_stays_pending_before_battle_adoption(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("PAUSED", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    store.request_battle_workflow("attach_battle", evidence=evidence)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert supervisor.battle_workflow["status"] == "awaiting_enable"
    store.set_state("RUNNING", source="test")
    supervisor.apply_control()
    app._sync_operator_control_workflows({"state": "RUNNING"})

    assert supervisor.battle_workflow["status"] == "validating_save"
    assert manager.awaiting_initial_battle_intent() is True
    assert manager.maybe_run_start({"state": "RUNNING"}) is False
    hold = app._operator_workflow_authority_hold()
    assert hold is not None
    assert hold.hold.value == "operator_workflow"
    assert hold.allowed_auxiliary_collectors == ()


def test_enabled_attach_begins_validation_on_first_runtime_sync(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    store.request_battle_workflow("attach_battle", evidence=evidence)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "RUNNING"})

    workflow = supervisor.battle_workflow
    assert workflow["status"] == "validating_save"
    assert workflow["acknowledgement"] == evidence
    assert "acknowledged_at" in workflow


def test_validated_attach_completes_only_after_lifecycle_adoption(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(
        "attach_battle",
        evidence=evidence,
    )
    store.transition_battle_workflow(
        workflow["request_id"],
        "acknowledged",
        acknowledgement=evidence,
    )
    store.transition_battle_workflow(
        workflow["request_id"],
        "validating_save",
        acknowledgement=evidence,
    )
    final_scope = "scope-after-continuity"
    claim = _running_save_claim(
        workflow["request_id"],
        evidence,
        final_scope=final_scope,
    )
    store.transition_battle_workflow(
        workflow["request_id"],
        "ready",
        save_receipt=claim[0],
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    session = MagicMock()
    session.snapshot.return_value = SimpleNamespace(
        owned=True,
        target="localhost:5555",
        generation=7,
    )
    app._adb_target_session = session
    app._player_save_runtime_session_id = "save-runtime-1"
    monkeypatch.setattr(
        "core.app.get_activity_scope",
        lambda: {"run_id": final_scope},
    )
    current = _evidence(
        game_state="active_battle",
        observation_id="runtime-1:after-continuity",
        runtime_id=str(owner["runtime_id"]),
        scope=final_scope,
    )
    current["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in current.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    receipt, acquisition, temporal, context = claim
    app._retain_running_reconciliation_claim(
        workflow["request_id"],
        receipt=receipt,
        acquisition=acquisition,
        temporal_binding=temporal,
        context=context,
        evidence=evidence,
    )

    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert manager.awaiting_initial_battle_intent() is False
    assert supervisor.battle_workflow["status"] == "ready"
    assert app._complete_ready_attachment_after_adoption() is False

    assert manager.maybe_run_start({"state": "RUNNING"}) is False
    assert app._complete_ready_attachment_after_adoption() is True
    assert supervisor.battle_workflow["status"] == "completed"


@pytest.mark.parametrize("changed_state", ["home_new_battle", "game_over"])
def test_ready_attach_interrupts_if_boundary_changes_before_adoption(
    tmp_path,
    monkeypatch,
    changed_state,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_resume_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(
        "attach_battle", evidence=evidence
    )
    for status in ("acknowledged", "validating_save"):
        store.transition_battle_workflow(
            workflow["request_id"],
            status,
            acknowledgement=evidence,
        )
    active = _evidence(
        game_state="active_battle",
        observation_id="runtime-1:active-before-change",
        runtime_id=str(owner["runtime_id"]),
        scope=str(evidence["activity_scope_run_id"]),
    )
    active["pid"] = owner["pid"]
    claim = _running_save_claim(workflow["request_id"], active)
    store.transition_battle_workflow(
        workflow["request_id"],
        "ready",
        acknowledgement=active,
        save_receipt=claim[0],
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in active.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    _retain_running_save_claim(
        app,
        workflow["request_id"],
        active,
        claim,
    )
    app._sync_operator_control_workflows({"state": "RUNNING"})
    assert manager.awaiting_initial_battle_intent() is False

    changed = _evidence(
        game_state=changed_state,
        observation_id="runtime-1:changed",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows(
        {"state": str(changed["primary_state"])}
    )

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True


def test_runtime_rejects_changed_boundary_before_first_acknowledgement(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("PAUSED", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    requested = _evidence(runtime_id=str(owner["runtime_id"]))
    requested["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=requested)
    supervisor.apply_control()
    changed = _evidence(
        game_state="home_resume_battle",
        observation_id="runtime-1:2",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    assert supervisor.battle_workflow["status"] == "rejected"
    assert manager.awaiting_initial_battle_intent() is True


def test_runtime_interrupts_and_revokes_start_if_boundary_changes_after_ack(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    requested = _evidence(runtime_id=str(owner["runtime_id"]))
    requested["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=requested)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in requested.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})
    assert supervisor.battle_workflow["status"] == "acknowledged"
    assert manager.awaiting_initial_battle_intent() is False

    changed = _evidence(
        game_state="home_resume_battle",
        observation_id="runtime-1:2",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True


@pytest.mark.parametrize(
    ("intent", "game_state", "ready"),
    [
        ("start_battle", "home_new_battle", False),
        ("attach_battle", "active_battle", True),
    ],
)
def test_manual_control_interrupt_revokes_unadopted_battle_authorization(
    tmp_path,
    monkeypatch,
    intent,
    game_state,
    ready,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH", str(tmp_path / "actions.log")
    )
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state=game_state,
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    workflow = store.request_battle_workflow(intent, evidence=evidence)
    claim = None
    if ready:
        for status in ("acknowledged", "validating_save"):
            store.transition_battle_workflow(
                workflow["request_id"],
                status,
                acknowledgement=evidence,
            )
        claim = _running_save_claim(workflow["request_id"], evidence)
        store.transition_battle_workflow(
            workflow["request_id"],
            "ready",
            acknowledgement=evidence,
            save_receipt=claim[0],
        )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    if claim is not None:
        _retain_running_save_claim(
            app,
            workflow["request_id"],
            evidence,
            claim,
        )
    app._sync_operator_control_workflows(
        {"state": str(evidence["primary_state"])}
    )
    assert manager.awaiting_initial_battle_intent() is False

    store.request_manual_control(evidence=evidence, source="test")
    supervisor.apply_control()
    app._sync_operator_control_workflows(
        {"state": str(evidence["primary_state"])}
    )

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True
    assert app._operator_workflow_authority_hold() is not None


def test_battle_intent_authorization_can_change_after_a_natural_boundary(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "TOWER_ACTION_LOG_PATH", str(tmp_path / "actions.log")
    )
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()

    assert manager.authorize_initial_battle_intent("attach_battle") is True
    assert manager.maybe_run_start({"state": "RUNNING"}) is False
    assert manager.active_battle_observed() is True
    manager.maybe_run_start({"state": "GAME_OVER"})
    assert manager.active_battle_observed() is False

    assert manager.authorize_initial_battle_intent("start_battle") is True
    manager.maybe_run_start(
        {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
    )
    assert manager.maybe_run_start({"state": "RUNNING"}) is True


def test_operator_workflow_is_interrupted_when_boundary_changes_before_enable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("PAUSED", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    manager = MissionManager(None, None, await_initial_battle_intent=True)
    manager.start()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(runtime_id=str(owner["runtime_id"]))
    evidence["pid"] = owner["pid"]
    store.request_battle_workflow("start_battle", evidence=evidence)
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = manager
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    changed = _evidence(
        game_state="home_resume_battle",
        observation_id="runtime-1:2",
        runtime_id=str(owner["runtime_id"]),
    )
    changed["pid"] = owner["pid"]
    app._control_observation = {
        key: value
        for key, value in changed.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    assert supervisor.battle_workflow["status"] == "interrupted"
    assert manager.awaiting_initial_battle_intent() is True


def test_unexpected_manual_activity_yields_with_an_indefinite_pause(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    current = _evidence(
        game_state="home_resume_battle",
        observation_id="manual-runtime:2",
        runtime_id=str(owner["runtime_id"]),
    )
    current["pid"] = owner["pid"]

    app = App.__new__(App)
    app._supervisor = supervisor
    app._prior_control_observation = {
        **{
            key: value
            for key, value in current.items()
            if key not in {"runtime_id", "pid", "adb_target"}
        },
        "observation_id": "manual-runtime:1",
        "primary_state": "RUNNING",
        "home_battle_control": "UNKNOWN",
        "game_state": "active_battle",
        "active_battle": True,
    }
    app._control_observation = {
        key: value
        for key, value in current.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    assert app._yield_on_unexpected_manual_activity() is True
    status = store.status()
    assert status["state"] == "PAUSED"
    assert status["resume_at"] is None
    assert status["manual_control"]["reason"] == "unexpected_manual_activity"
    assert status["manual_control"]["status"] == "pause_requested"


def test_malformed_manual_control_fails_closed_without_overwrite(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    service = ControlSurfaceService(repository_root=tmp_path)
    raw_manual = {"schema_version": 999, "status": "active"}
    service.control_store.replace(
        {
            "state": "RUNNING",
            "mode": "NEXT_BATTLE",
            "manual_control": raw_manual,
        }
    )
    _publish_runtime_observation(
        service,
        _evidence(),
        paused=False,
    )

    model = service.status()["control_model"]
    for action in ("start_battle", "enable"):
        assert model["actions"][action]["available"] is False
        assert model["actions"][action]["code"] == "manual_control_invalid"
        with pytest.raises(ControlSurfaceRequestError) as invalid:
            service.apply_control({"action": action})
        assert invalid.value.code == "manual_control_invalid"
    assert service.control_store.read()["manual_control"] == raw_manual

    supervisor = AutomationSupervisor(
        control_file=str(service.control_store.path)
    )
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)
    hold = app._operator_workflow_authority_hold()
    assert hold is not None
    assert hold.hold.value == "manual_control_return"


def test_malformed_whole_control_file_disables_every_model_action(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    _publish_runtime_observation(service, _evidence())
    service.control_store.path.parent.mkdir(parents=True, exist_ok=True)
    service.control_store.path.write_text("{malformed", encoding="utf-8")

    status = service.status()

    assert status["control"]["error"]
    for action, availability in status["control_model"]["actions"].items():
        assert availability["available"] is False, action
        assert availability["code"] == "control_invalid", action
    for action in ("start_battle", "attach_battle", "pause", "enable"):
        with pytest.raises(ControlSurfaceRequestError) as invalid:
            service.apply_control({"action": action})
        assert invalid.value.code == "control_invalid"


def test_manual_control_cannot_cross_a_runtime_owner_boundary(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    first_supervisor = AutomationSupervisor(control_file=str(path))
    first_owner = first_supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(first_owner["runtime_id"]),
    )
    evidence["pid"] = first_owner["pid"]
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )

    replacement_supervisor = AutomationSupervisor(control_file=str(path))
    replacement_supervisor.apply_control()
    replacement_owner = (
        replacement_supervisor.current_exclusive_validation_owner()
    )
    replacement_evidence = _evidence(
        game_state="active_battle",
        observation_id="replacement-runtime:1",
        runtime_id=str(replacement_owner["runtime_id"]),
    )
    replacement_evidence["pid"] = replacement_owner["pid"]
    app = App.__new__(App)
    app._supervisor = replacement_supervisor
    app._mission_mgr = MissionManager(None, None)
    app._control_observation = {
        key: value
        for key, value in replacement_evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }

    app._sync_operator_control_workflows({"state": "RUNNING"})

    assert replacement_supervisor.manual_control["status"] == "interrupted"
    assert replacement_supervisor.is_paused is True
    assert "runtime evidence changed" in (
        replacement_supervisor.manual_control["detail"]
    )


def test_manual_handoff_hold_survives_concurrent_running_directive(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    evidence = _evidence()
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    store.set_state("RUNNING", source="concurrent-legacy-writer")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)

    hold = app._operator_workflow_authority_hold()

    assert supervisor.is_paused is False
    assert hold is not None
    assert hold.hold.value == "manual_control_return"


def test_repeated_return_enable_is_pending_and_keeps_request_identity(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="active_battle")
    _publish_runtime_observation(service, evidence)
    manual = service.control_store.request_manual_control(
        evidence=evidence,
        source="test",
    )
    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    service.control_store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )

    first = service.apply_control({"action": "enable"})
    request_id = first["control"]["state_request_id"]
    updated_at = first["control_model"]["manual_control"]["updated_at"]
    second = service.apply_control({"action": "enable"})

    assert first["request"]["disposition"] == "requested"
    assert second["request"]["disposition"] == "pending"
    assert second["control"]["state_request_id"] == request_id
    assert second["control_model"]["manual_control"]["updated_at"] == updated_at


def test_same_value_state_ack_requires_the_exact_request_identity(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    first = service.control_store.set_state("PAUSED", source="first")
    second = service.control_store.set_state("PAUSED", source="second")
    timestamp = datetime.fromisoformat(
        str(second["state_updated_at"])
    ).strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    service.action_log.write_text(
        f"[INFO {timestamp}] [CTRL] State set to PAUSED via control file "
        f"request_id={first['state_request_id']}\n",
        encoding="utf-8",
    )

    stale_ack = service.status()["acknowledgements"]["state"]
    assert stale_ack["request_id"] == first["state_request_id"]
    assert stale_ack["acknowledges_current"] is False

    with service.action_log.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[INFO {timestamp}] [CTRL] State set to PAUSED via control file "
            f"request_id={second['state_request_id']}\n"
        )
    current_ack = service.status()["acknowledgements"]["state"]
    assert current_ack["request_id"] == second["state_request_id"]
    assert current_ack["acknowledges_current"] is True


def test_same_value_terminal_policy_ack_requires_exact_request_identity(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    first = service.control_store.set_mode("WAIT", source="first")
    second = service.control_store.set_mode("WAIT", source="second")
    timestamp = datetime.fromisoformat(
        str(second["mode_updated_at"])
    ).strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    service.action_log.write_text(
        f"[INFO {timestamp}] [CTRL] Mode set to WAIT via control file "
        f"request_id={first['mode_request_id']}\n",
        encoding="utf-8",
    )

    stale_ack = service.status()["acknowledgements"]["mode"]
    assert stale_ack["request_id"] == first["mode_request_id"]
    assert stale_ack["acknowledges_current"] is False

    with service.action_log.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[INFO {timestamp}] [CTRL] Mode set to WAIT via control file "
            f"request_id={second['mode_request_id']}\n"
        )
    current_ack = service.status()["acknowledgements"]["mode"]
    assert current_ack["request_id"] == second["mode_request_id"]
    assert current_ack["acknowledges_current"] is True


def test_terminal_policy_requests_have_stable_pending_and_noop_identity(
    tmp_path,
):
    stopped = ControlSurfaceService(repository_root=tmp_path / "stopped")
    first = stopped.apply_control(
        {"action": "terminal_policy", "policy": "continue_automatically"}
    )
    first_id = first["control"]["mode_request_id"]
    repeated = stopped.apply_control(
        {"action": "terminal_policy", "policy": "continue_automatically"}
    )
    assert first["request"]["disposition"] == "requested"
    assert first["control"]["exists"] is True
    assert repeated["request"]["disposition"] == "no_op"
    assert repeated["control"]["mode_request_id"] == first_id

    live = ControlSurfaceService(repository_root=tmp_path / "live")
    selected = live.control_store.set_mode("WAIT", source="test")
    _publish_runtime_observation(live, _evidence())
    pending = live.apply_control(
        {"action": "terminal_policy", "policy": "wait"}
    )
    assert pending["request"]["disposition"] == "pending"
    assert pending["control"]["mode_request_id"] == (
        selected["mode_request_id"]
    )


def test_return_control_is_unavailable_if_indefinite_pause_was_superseded(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="active_battle")
    manual = service.control_store.request_manual_control(
        evidence=evidence,
        source="test",
    )
    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    service.control_store.set_state("RUNNING", source="concurrent-writer")
    _publish_runtime_observation(service, evidence, paused=False)

    availability = service.status()["control_model"]["actions"][
        "return_control"
    ]

    assert availability["available"] is False
    assert availability["code"] == "pause_not_acknowledged"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("target_generation", None),
        ("target_generation", 0),
        ("activity_scope_run_id", ""),
    ),
)
def test_return_control_requires_an_exact_current_binding(
    tmp_path,
    field,
    value,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    starting = _evidence(game_state="active_battle")
    manual = service.control_store.request_manual_control(
        evidence=starting,
        source="test",
    )
    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=starting,
    )
    current = _evidence(game_state="active_battle")
    current[field] = value
    _publish_runtime_observation(service, current, paused=True)

    availability = service.status()["control_model"]["actions"][
        "return_control"
    ]

    assert availability["available"] is False
    assert availability["code"] == "exact_return_binding_unavailable"
    with pytest.raises(ControlSurfaceRequestError) as unavailable:
        service.apply_control({"action": "return_control"})
    assert unavailable.value.code == "exact_return_binding_unavailable"


@pytest.mark.parametrize("game_state", ("tournament_results", "unknown"))
def test_return_control_rejects_boundaries_without_save_reconciliation(
    tmp_path,
    game_state,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    starting = _evidence(game_state="active_battle")
    manual = service.control_store.request_manual_control(
        evidence=starting,
        source="test",
    )
    service.control_store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=starting,
    )
    _publish_runtime_observation(
        service,
        _evidence(game_state=game_state),
        paused=True,
    )

    availability = service.status()["control_model"]["actions"][
        "return_control"
    ]

    assert availability["available"] is False
    assert availability["code"] == "return_boundary_unavailable"


def test_tournament_results_expose_verified_home_policy_separately(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    service.control_store.set_mode("HOME", source="test")
    _publish_runtime_observation(
        service,
        _evidence(game_state="tournament_results"),
    )

    policy = service.status()["control_model"]["when_battle_ends"]

    assert policy["compatibility_value"] == "HOME"
    assert policy["status"] == "selected"
    assert "verified OK-to-Home" in policy["reason"]


def test_cli_does_not_expose_raw_authority_aliases(tmp_path, capsys):
    control_path = tmp_path / "logs" / "automation_ctl.json"

    assert automation_ctl.main(
        ["resume", "--control-file", str(control_path)]
    ) == 2
    assert automation_ctl.main(
        ["set", "state", "RUNNING", "--control-file", str(control_path)]
    ) == 2
    assert not control_path.exists()
    capsys.readouterr()


def test_cli_uses_same_exact_intent_validation(tmp_path, monkeypatch, capsys):
    control_path = tmp_path / "logs" / "automation_ctl.json"
    service = ControlSurfaceService(
        repository_root=tmp_path,
        control_file=control_path,
    )
    _publish_runtime_observation(service, _evidence())
    monkeypatch.setattr(
        automation_ctl,
        "_better_control_service",
        lambda _path: service,
    )

    assert automation_ctl.main(
        ["start-battle", "--control-file", str(control_path)]
    ) == 0
    assert service.control_store.status()["battle_workflow"]["intent"] == (
        "start_battle"
    )
    assert "runtime status=requested" in capsys.readouterr().out


def test_cli_labels_terminal_policy_as_future_behavior(tmp_path, capsys):
    control_path = tmp_path / "logs" / "automation_ctl.json"

    assert automation_ctl.main(
        ["when-battle-ends", "wait", "--control-file", str(control_path)]
    ) == 0
    assert ControlDirectiveStore(control_path).status()["mode"] == "WAIT"
    assert "terminal_policy" in capsys.readouterr().out


def _capture_preview(
    *,
    modules_local: bool = False,
    evidence: dict[str, object] | None = None,
    acquisition: PlayerSaveAcquisitionBundle | None = None,
) -> dict[str, object]:
    evidence = evidence or _evidence()
    if acquisition is None:
        captured = datetime.now(timezone.utc)
        acquisition = PlayerSaveAcquisitionBundle(
            acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
            status=PlayerSaveAcquisitionStatus.COMPLETE,
            reason="captured",
            binding=PlayerSaveTargetBinding(
                str(evidence["adb_target"]),
                int(evidence["target_generation"]),
            ),
            acquisition_started_at=captured - timedelta(milliseconds=1),
            captured_at=captured,
            acquisition_completed_at=captured + timedelta(milliseconds=1),
            transport_stable=True,
            snapshot=SimpleNamespace(),
        )
    settings = {
        setting_id: definition.normalizer(definition.initial_value_factory())
        for setting_id, definition in FARM_SETTING_REGISTRY.items()
        if setting_id not in {"damage_slider", "orb_distance"}
    }
    if modules_local:
        settings["modules"] = {
            "local": {
                "cannon_primary": "Amplifying Strike",
                "armor_primary": "Orbital Augment",
                "generator_primary": "Black Hole Digestor",
                "core_primary": "Multiverse Nexus",
                "cannon_assist": "Being Annihilator",
                "armor_assist": "Anti-Cube Portal",
                "generator_assist": "Singularity Harness",
                "core_assist": "Dimension Core",
            }
        }
    return {
        "schema_version": 1,
        "status": "partial",
        "mapping_id": "data-9-game-1073",
        "mapping_maturity": "candidate",
        "effective_mapping_fingerprint": "9" * 64,
        "captured_at": acquisition.captured_at.isoformat(),
        "acquisition": acquisition.redacted_provenance(),
        "settings": settings,
        "captured_check_ids": sorted(settings),
        "unresolved": [
            {
                "setting_id": "damage_slider",
                "display_name": "Damage Slider",
                "source_check_ids": ["damage_slider"],
                "status": "unresolved",
                "reason": "no validated save observation",
            },
            {
                "setting_id": "orb_distance",
                "display_name": "Orb Distance",
                "source_check_ids": ["orb_distance"],
                "status": "unresolved",
                "reason": "no validated save observation",
            },
        ],
        "saving_activates_strategy": False,
        "publication_activates_strategy": False,
        "workflow_binding": {
            "schema_version": 1,
            "game_state": evidence["game_state"],
            "runtime_session_fingerprint": hashlib.sha256(
                (
                    "thetower-setup-capture-runtime-v1\0"
                    f"{evidence['runtime_id']}"
                ).encode("utf-8")
            ).hexdigest(),
            "activity_scope_fingerprint": hashlib.sha256(
                (
                    "thetower-setup-capture-scope-v1\0"
                    f"{evidence['activity_scope_run_id']}"
                ).encode("utf-8")
            ).hexdigest(),
            "target_generation_fingerprint": acquisition.binding.fingerprint,
            "active_round_identity_fingerprint": (
                "a" * 64
                if evidence["game_state"]
                in {"active_battle", "home_resume_battle"}
                else None
            ),
        },
        "capture_origin": {
            "schema_version": 1,
            "acquisition_source": "new_setup_capture_refresh",
            "source_manual_control_fingerprint": None,
        },
    }


def _ready_capture(
    store: ControlDirectiveStore,
    capture: dict[str, object],
    preview: dict[str, object],
) -> dict[str, object]:
    request_id = str(capture["request_id"])
    store.transition_setup_capture(request_id, "acknowledged")
    store.transition_setup_capture(request_id, "capturing")
    ready = store.transition_setup_capture(
        request_id,
        "ready",
        preview=preview,
    )
    assert ready is not None
    return ready


def test_process_boundary_retires_legacy_authority_records(tmp_path):
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    evidence = _evidence(game_state="active_battle")
    workflow = store.request_battle_workflow(
        "attach_battle",
        evidence=evidence,
    )
    for status in ("acknowledged", "validating_save"):
        store.transition_battle_workflow(
            workflow["request_id"],
            status,
            acknowledgement=evidence,
        )
    receipt = _save_receipt(str(workflow["request_id"]), evidence)
    store.transition_battle_workflow(
        workflow["request_id"],
        "ready",
        save_receipt=receipt,
    )
    store.transition_battle_workflow(
        workflow["request_id"],
        "completed",
        acknowledgement=evidence,
    )
    manual = store.request_manual_control(evidence=evidence, source="test")
    store.transition_manual_control(
        manual["manual_control_id"],
        "active",
        pause_acknowledgement=evidence,
    )
    store.request_return_control(
        manual["manual_control_id"],
        evidence=evidence,
        source="test",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "reconciling",
    )
    manual_receipt = _save_receipt(
        str(manual["manual_control_id"]),
        evidence,
        kind="return_control_reconciliation",
    )
    store.transition_manual_control(
        manual["manual_control_id"],
        "completed",
        save_receipt=manual_receipt,
    )
    capture = store.request_setup_capture(evidence=evidence, source="test")
    _ready_capture(
        store,
        capture,
        _capture_preview(evidence=evidence),
    )

    def remove_new_mapping_provenance(data):
        data["battle_workflow"]["save_receipt"]["temporal"].pop(
            "effective_mapping_fingerprint"
        )
        data["manual_control"]["save_receipt"]["temporal"].pop(
            "effective_mapping_fingerprint"
        )
        data["setup_capture"]["preview"].pop(
            "effective_mapping_fingerprint"
        )
        return data

    store.update(remove_new_mapping_provenance)
    incompatible = store.status()
    assert incompatible["battle_workflow_error"]
    assert incompatible["manual_control_error"]
    assert incompatible["setup_capture_error"]

    store.interrupt_operator_workflows(
        "a new automation process boundary started",
        source="test-process-start",
    )

    retired = store.status()
    assert retired["battle_workflow_error"] is None
    assert retired["battle_workflow"]["status"] == "interrupted"
    assert "save_receipt" not in retired["battle_workflow"]
    assert retired["manual_control_error"] is None
    assert retired["manual_control"]["status"] == "interrupted"
    assert "save_receipt" not in retired["manual_control"]
    assert retired["setup_capture_error"] is None
    assert retired["setup_capture"]["status"] == "interrupted"
    assert "preview" not in retired["setup_capture"]
    replacement = store.request_battle_workflow(
        "attach_battle",
        evidence={
            **evidence,
            "observation_id": "runtime-2:1",
            "runtime_id": "runtime-2",
        },
    )
    assert replacement["status"] == "requested"
    assert replacement["request_id"] != workflow["request_id"]


def test_cli_capture_reviews_saves_and_reopens_a_durable_strategy_draft(
    tmp_path,
    monkeypatch,
    capsys,
):
    control_path = tmp_path / "logs" / "automation_ctl.json"
    service = ControlSurfaceService(
        repository_root=tmp_path,
        control_file=control_path,
    )
    evidence = _evidence(game_state="home_new_battle")
    capture = service.control_store.request_setup_capture(
        evidence=evidence,
        source="test",
    )
    _ready_capture(
        service.control_store,
        capture,
        _capture_preview(evidence=evidence),
    )
    monkeypatch.setattr(
        automation_ctl,
        "_better_control_service",
        lambda _path: service,
    )

    assert automation_ctl.main(
        [
            "capture-setup",
            "review-strategy",
            "captured_cli",
            "19",
            "Captured CLI",
            "--control-file",
            str(control_path),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "Evidence source: new_setup_capture_refresh" in output
    assert "Captured-versus-Base review:" in output
    fingerprint_match = re.search(
        r'"review_fingerprint": "([0-9a-f]{64})"',
        output,
    )
    assert fingerprint_match is not None
    with pytest.raises(SystemExit, match="prior review-strategy"):
        automation_ctl.main(
            [
                "capture-setup",
                "save-strategy",
                "captured_cli",
                "19",
                "Captured CLI",
                "--control-file",
                str(control_path),
            ]
        )
    assert automation_ctl.main(
        [
            "capture-setup",
            "save-strategy",
            "captured_cli",
            "19",
            "Captured CLI",
            "--review-fingerprint",
            fingerprint_match.group(1),
            "--control-file",
            str(control_path),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "runtime status=saved" in output
    assert "captured_cli" not in service.profile_store.strategy_ids()

    assert automation_ctl.main(
        [
            "capture-setup",
            "draft",
            "captured_cli",
            "--control-file",
            str(control_path),
        ]
    ) == 0
    reopened = capsys.readouterr().out
    assert '"id": "captured_cli"' in reopened
    assert '"kind": "strategy"' in reopened


def test_setup_capture_directive_has_exact_transitions_and_conflicts(tmp_path):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    evidence = _evidence(game_state="active_battle")

    capture = store.request_setup_capture(evidence=evidence, source="test")

    assert capture["status"] == "requested"
    with pytest.raises(ValueError, match="Save or cancel"):
        store.request_setup_capture(evidence=evidence, source="test")
    with pytest.raises(ValueError, match="Setup capture currently owns"):
        store.request_manual_control(evidence=evidence, source="test")
    with pytest.raises(ValueError, match="Setup capture currently owns"):
        store.request_battle_workflow("attach_battle", evidence=evidence)

    ready = _ready_capture(
        store,
        capture,
        _capture_preview(evidence=evidence),
    )
    assert ready["status"] == "ready"
    assert len(str(ready["preview_fingerprint"])) == 64
    saved = store.transition_setup_capture(
        str(capture["request_id"]),
        "saved",
        saved_result={
            "kind": "strategy_draft",
            "id": "captured_farm",
            "selected": False,
        },
    )
    assert saved is not None
    assert saved["status"] == "saved"
    assert saved["saved_result"]["selected"] is False


def test_setup_capture_directive_rejects_a_preview_bound_to_another_runtime(
    tmp_path,
):
    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    evidence = _evidence(game_state="active_battle")
    capture = store.request_setup_capture(evidence=evidence, source="test")
    store.transition_setup_capture(str(capture["request_id"]), "acknowledged")
    store.transition_setup_capture(str(capture["request_id"]), "capturing")
    preview = _capture_preview(evidence=evidence)
    preview["workflow_binding"]["runtime_session_fingerprint"] = "0" * 64

    with pytest.raises(
        ValueError,
        match="exact forced-save workflow evidence",
    ):
        store.transition_setup_capture(
            str(capture["request_id"]),
            "ready",
            preview=preview,
        )

    assert store.status()["setup_capture"]["status"] == "capturing"


def test_setup_capture_api_reports_pause_and_saves_without_control_mutation(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="active_battle")
    service.control_store.set_state("PAUSED", source="test")
    _publish_runtime_observation(service, evidence, paused=True)

    with pytest.raises(ControlSurfaceRequestError) as paused:
        service.apply_setup_capture({"operation": "request"})
    assert paused.value.code == "automation_paused"

    enabled = service.control_store.set_state("RUNNING", source="test")
    _publish_runtime_observation(service, evidence, paused=False)
    timestamp = datetime.fromisoformat(
        str(enabled["state_updated_at"])
    ).strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    service.action_log.write_text(
        f"[INFO {timestamp}] [CTRL] State set to RUNNING via control file "
        f"request_id={enabled['state_request_id']}\n",
        encoding="utf-8",
    )
    requested = service.apply_setup_capture({"operation": "request"})
    capture = requested["capture"]
    ready = _ready_capture(
        service.control_store,
        capture,
        _capture_preview(modules_local=True, evidence=evidence),
    )
    before = service.control_store.status()

    saved = service.apply_setup_capture(
        {
            "operation": "save",
            "request_id": capture["request_id"],
            "expected_preview_fingerprint": ready["preview_fingerprint"],
            "kind": "module_preset",
            "id": "captured_modules",
            "display_name": "Captured Modules",
        }
    )

    assert saved["capture"]["status"] == "saved"
    assert saved["request"]["saved_result"]["selected"] is False
    after = service.control_store.status()
    for field in ("state", "mode", "strategy", "strategy_apply_mode"):
        assert after[field] == before[field]
    assert "captured_modules" in {
        item["id"] for item in saved["module_presets"]["items"]
    }


def test_setup_capture_api_saves_normal_strategy_draft_without_publication(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="home_new_battle")
    capture = service.control_store.request_setup_capture(
        evidence=evidence,
        source="test",
    )
    ready = _ready_capture(
        service.control_store,
        capture,
        _capture_preview(evidence=evidence),
    )

    reviewed = service.apply_setup_capture(
        {
            "operation": "review",
            "request_id": capture["request_id"],
            "expected_preview_fingerprint": ready["preview_fingerprint"],
            "kind": "strategy_draft",
            "id": "captured_farm",
            "display_name": "Captured Farm",
            "tier": 19,
        }
    )

    saved = service.apply_setup_capture(
        {
            "operation": "save",
            "request_id": capture["request_id"],
            "expected_preview_fingerprint": ready["preview_fingerprint"],
            "expected_review_fingerprint": reviewed["review"][
                "review_fingerprint"
            ],
            "kind": "strategy_draft",
            "id": "captured_farm",
            "display_name": "Captured Farm",
            "tier": 19,
        }
    )

    assert saved["capture"]["status"] == "saved"
    result = saved["request"]["saved_result"]
    assert result["kind"] == "strategy_draft"
    assert result["published"] is False
    assert result["selected"] is False
    assert "captured_farm" not in service.profile_store.strategy_ids()
    draft = service.profile_store.captured_strategy_draft("captured_farm")
    assert draft["review"]["unresolved"] == _capture_preview()["unresolved"]
    detail = service.captured_setup_draft("captured_farm")
    assert detail["capability"] == "save_backed_setup_capture_v2"
    assert detail["draft"]["source"] == draft["source"]


def test_setup_capture_module_collision_is_not_mistaken_for_receipt_recovery(
    tmp_path,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="home_new_battle")

    def save_once():
        capture = service.control_store.request_setup_capture(
            evidence=evidence,
            source="test",
        )
        ready = _ready_capture(
            service.control_store,
            capture,
            _capture_preview(modules_local=True, evidence=evidence),
        )
        return service.apply_setup_capture(
            {
                "operation": "save",
                "request_id": capture["request_id"],
                "expected_preview_fingerprint": ready[
                    "preview_fingerprint"
                ],
                "kind": "module_preset",
                "id": "captured_modules",
                "display_name": "Captured Modules",
            }
        )

    first = save_once()
    assert first["request"]["saved_result"]["artifact_disposition"] == (
        "created"
    )
    with pytest.raises(ControlSurfaceRequestError) as conflict:
        save_once()
    assert conflict.value.status == 409


def test_exact_strategy_capture_recovers_only_after_receipt_write_failure(
    tmp_path,
    monkeypatch,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="home_new_battle")
    capture = service.control_store.request_setup_capture(
        evidence=evidence,
        source="test",
    )
    ready = _ready_capture(
        service.control_store,
        capture,
        _capture_preview(evidence=evidence),
    )
    review_request = {
        "operation": "review",
        "request_id": capture["request_id"],
        "expected_preview_fingerprint": ready["preview_fingerprint"],
        "kind": "strategy_draft",
        "id": "captured_farm",
        "display_name": "Captured Farm",
        "tier": 19,
    }
    reviewed = service.apply_setup_capture(review_request)
    save_request = {
        **review_request,
        "operation": "save",
        "expected_review_fingerprint": reviewed["review"][
            "review_fingerprint"
        ],
    }
    transition = service.control_store.transition_setup_capture

    def fail_saved_receipt(request_id, status, **kwargs):
        if status == "saved":
            raise ValueError("simulated ledger write failure")
        return transition(request_id, status, **kwargs)

    monkeypatch.setattr(
        service.control_store,
        "transition_setup_capture",
        fail_saved_receipt,
    )
    with pytest.raises(ControlSurfaceRequestError) as failed_receipt:
        service.apply_setup_capture(save_request)
    assert failed_receipt.value.code == "capture_receipt_write_failed"
    assert service.profile_store.captured_strategy_draft("captured_farm")
    assert service.control_store.status()["setup_capture"]["status"] == (
        "ready"
    )

    monkeypatch.setattr(
        service.control_store,
        "transition_setup_capture",
        transition,
    )
    recovered = service.apply_setup_capture(save_request)

    assert recovered["capture"]["status"] == "saved"
    assert recovered["request"]["saved_result"][
        "artifact_disposition"
    ] == "recovered_existing"


def test_runtime_setup_capture_forces_save_before_publishing_preview(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    capture = store.request_setup_capture(evidence=evidence, source="test")
    supervisor.apply_control()
    captured = datetime.now(timezone.utc)
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="captured",
        binding=PlayerSaveTargetBinding("localhost:5555", 7),
        acquisition_started_at=captured - timedelta(milliseconds=1),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=SimpleNamespace(
            runtime_save=SimpleNamespace(
                round_active=True,
                active_round_identity=SimpleNamespace(fingerprint="a" * 64),
            )
        ),
    )
    constructor = {}

    class FakeSerializer:
        def __init__(self, **kwargs):
            constructor.update(kwargs)

        def acquire(self, **_kwargs):
            assert constructor["context_guard_fn"]() is True
            assert constructor["action_guard_fn"]() is True
            return SimpleNamespace(
                status=GuardedSerializationStatus.COMPLETE,
                background_dispatched=True,
                acquisition=acquisition,
            )

    projected = []
    monkeypatch.setattr("core.app.GuardedPlayerSaveSerializer", FakeSerializer)
    monkeypatch.setattr(
        "core.app.project_forced_save_setup",
        lambda bundle: projected.append(bundle)
        or _capture_preview(evidence=evidence, acquisition=acquisition),
    )
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._adb_target_session = SimpleNamespace(snapshot=lambda: None)
    app._player_save_acquirer = object()
    app._runtime_action_guard = lambda **_kwargs: True
    app._log_operator_workflow_result = lambda *_args, **_kwargs: None
    app._get_action_authority().activate_strategy_gate(
        strategy="none",
        battle_scope=str(evidence["activity_scope_run_id"]),
        source="setup_capture",
        phase="running_battle",
        failed_check_ids=("setup_capture_battle_identity",),
        reason="earlier capture contradiction",
    )

    app._sync_operator_control_workflows({"state": "RUNNING"})

    ready = supervisor.setup_capture
    assert ready["status"] == "ready"
    assert ready["preview"]["workflow_binding"]["game_state"] == (
        "active_battle"
    )
    assert projected == [acquisition]
    assert app._setup_capture_source_refreshed is True
    assert ready["request_id"] == capture["request_id"]
    assert app._get_action_authority().strategy_gate is None


def test_setup_capture_gate_is_not_cleared_by_unrelated_session_preflight_success():
    strategy = SimpleNamespace(
        requires_session_preflight=lambda: True,
        is_session_preflight_complete=lambda _context: True,
    )
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(
        strategy=strategy,
        ctx=SimpleNamespace(
            data={
                "mission_vars": {
                    "gc_session_preflight_last_reason": "different mismatch",
                }
            }
        ),
        session_preflight_failure_checks=lambda: ["cards_deck"],
    )
    gate = app._get_action_authority().activate_strategy_gate(
        strategy="farm",
        battle_scope="scope-1",
        source="setup_capture",
        phase="running_battle",
        failed_check_ids=("setup_capture_battle_identity",),
        reason="fresh save contradicted the observed battle boundary",
    )

    app._sync_strategy_action_gate(terminally_blocked=False)

    assert app._get_action_authority().strategy_gate == gate
    app._sync_strategy_action_gate(terminally_blocked=True)
    assert app._get_action_authority().strategy_gate == gate


def test_setup_capture_request_and_runtime_share_one_action_result_pair(
    tmp_path,
    monkeypatch,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    evidence = _evidence(game_state="home_new_battle")
    enabled = service.control_store.set_state("RUNNING", source="test")
    _publish_runtime_observation(service, evidence, paused=False)
    timestamp = datetime.fromisoformat(
        str(enabled["state_updated_at"])
    ).strftime("%Y-%m-%d %H:%M:%S")
    service.action_log.parent.mkdir(parents=True, exist_ok=True)
    service.action_log.write_text(
        f"[INFO {timestamp}] [CTRL] State set to RUNNING via control file "
        f"request_id={enabled['state_request_id']}\n",
        encoding="utf-8",
    )

    requested = service.apply_setup_capture({"operation": "request"})

    request_id = requested["capture"]["request_id"]
    audit_text = service.action_log.read_text(encoding="utf-8")
    assert "[INFO " in audit_text
    assert "[ACTION " not in audit_text
    events = []
    monkeypatch.setattr(
        "core.app.log_action_intent",
        lambda *_args, **kwargs: events.append(
            ("ACTION", kwargs.get("operation_id"))
        ),
    )
    monkeypatch.setattr(
        "core.app.log_result",
        lambda *_args, **kwargs: events.append(
            ("RESULT", kwargs.get("operation_id"))
        ),
    )
    app = App.__new__(App)
    app._log_operator_workflow_intent(
        request_id,
        purpose="Capturing the current setup",
        reason="force one exact save",
    )
    app._log_operator_workflow_result(
        request_id,
        purpose="Capturing the current setup",
        reason="force one exact save",
        result="Setup capture ready",
    )
    app._log_operator_workflow_result(
        request_id,
        purpose="Capturing the current setup",
        reason="force one exact save",
        result="Setup capture ready",
    )

    assert events == [("ACTION", request_id), ("RESULT", request_id)]


def test_runtime_setup_capture_ready_write_retry_never_serializes_twice(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    store.request_setup_capture(evidence=evidence, source="test")
    supervisor.apply_control()
    captured = datetime.now(timezone.utc)
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="captured",
        binding=PlayerSaveTargetBinding("localhost:5555", 7),
        acquisition_started_at=captured - timedelta(milliseconds=1),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=SimpleNamespace(
            runtime_save=SimpleNamespace(
                round_active=True,
                active_round_identity=SimpleNamespace(fingerprint="a" * 64),
            )
        ),
    )
    serializer_calls = []

    class FakeSerializer:
        def __init__(self, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            serializer_calls.append("serialize")
            return SimpleNamespace(
                status=GuardedSerializationStatus.COMPLETE,
                background_dispatched=True,
                acquisition=acquisition,
            )

    monkeypatch.setattr("core.app.GuardedPlayerSaveSerializer", FakeSerializer)
    monkeypatch.setattr(
        "core.app.project_forced_save_setup",
        lambda bundle: _capture_preview(
            evidence=evidence,
            acquisition=bundle,
        ),
    )
    original_transition = supervisor.transition_setup_capture
    ready_attempts = []

    def fail_first_ready(request_id, status, **details):
        if status == "ready" and not ready_attempts:
            ready_attempts.append(request_id)
            return None
        return original_transition(request_id, status, **details)

    monkeypatch.setattr(
        supervisor,
        "transition_setup_capture",
        fail_first_ready,
    )
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._adb_target_session = SimpleNamespace(snapshot=lambda: None)
    app._player_save_acquirer = object()
    app._runtime_action_guard = lambda **_kwargs: True
    app._log_operator_workflow_intent = lambda *_args, **_kwargs: None
    app._log_operator_workflow_result = lambda *_args, **_kwargs: None
    app._setup_capture_source_refreshed = False

    assert app._sync_setup_capture(object()) is True
    assert supervisor.setup_capture["status"] == "capturing"
    assert supervisor.is_paused is False
    app._setup_capture_source_refreshed = False
    assert app._sync_setup_capture(object()) is True

    assert supervisor.setup_capture["status"] == "ready"
    assert supervisor.setup_capture["authority_outcome"] == "preserved"
    assert supervisor.is_paused is False
    assert serializer_calls == ["serialize"]
    assert app._pending_setup_capture_claims() == {}


@pytest.mark.parametrize(
    (
        "game_state",
        "round_active",
        "active_fingerprint",
        "acquisition_present",
        "snapshot_kind",
        "expected_status",
        "reason_fragment",
        "expected_authority",
        "paused",
    ),
    (
        (
            "active_battle",
            False,
            None,
            True,
            "runtime",
            "failed",
            "round identity contradicts",
            "continuity_gated",
            False,
        ),
        (
            "home_new_battle",
            True,
            "a" * 64,
            True,
            "runtime",
            "failed",
            "round identity contradicts",
            "paused_for_safety",
            True,
        ),
        (
            "active_battle",
            True,
            None,
            True,
            "runtime",
            "unavailable",
            "did not prove an active battle identity",
            "preserved",
            False,
        ),
        (
            "home_new_battle",
            None,
            None,
            True,
            "runtime",
            "unavailable",
            "did not prove an inactive round",
            "preserved",
            False,
        ),
        (
            "home_new_battle",
            False,
            None,
            True,
            "unsupported",
            "unavailable",
            "save version is unsupported",
            "preserved",
            False,
        ),
        (
            "home_new_battle",
            False,
            None,
            True,
            "incompatible",
            "unavailable",
            "structurally incompatible",
            "preserved",
            False,
        ),
        (
            "home_new_battle",
            False,
            None,
            True,
            "runtime_projection_unavailable",
            "unavailable",
            "no usable runtime projection",
            "preserved",
            False,
        ),
        (
            "active_battle",
            True,
            "a" * 64,
            False,
            "runtime",
            "unavailable",
            "no stable current save",
            "preserved",
            False,
        ),
    ),
)
def test_runtime_setup_capture_evidence_failure_preserves_enabled_after_restoration(
    tmp_path,
    monkeypatch,
    game_state,
    round_active,
    active_fingerprint,
    acquisition_present,
    snapshot_kind,
    expected_status,
    reason_fragment,
    expected_authority,
    paused,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state=game_state,
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    store.request_setup_capture(evidence=evidence, source="test")
    supervisor.apply_control()
    captured = datetime.now(timezone.utc)
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="captured",
        binding=PlayerSaveTargetBinding("localhost:5555", 7),
        acquisition_started_at=captured - timedelta(milliseconds=1),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=(
            SimpleNamespace(
                runtime_save=SimpleNamespace(
                    round_active=round_active,
                    active_round_identity=(
                        SimpleNamespace(fingerprint=active_fingerprint)
                        if active_fingerprint
                        else None
                    ),
                )
            )
            if snapshot_kind == "runtime"
            else SimpleNamespace(
                runtime_save=None,
                mapping_id=(
                    "data-9-game-audit-only"
                    if snapshot_kind == "runtime_projection_unavailable"
                    else None
                ),
                mapping_resolution=(
                    "incompatible_revision"
                    if snapshot_kind == "incompatible"
                    else "exact"
                    if snapshot_kind == "runtime_projection_unavailable"
                    else "unsupported"
                ),
                shape_valid=(
                    snapshot_kind == "runtime_projection_unavailable"
                ),
            )
        ),
    )
    serializer_calls = []

    class FakeSerializer:
        def __init__(self, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            serializer_calls.append("serialize")
            return SimpleNamespace(
                status=GuardedSerializationStatus.COMPLETE,
                source_restored=True,
                lifecycle_input_attempted=True,
                background_dispatched=True,
                acquisition=acquisition if acquisition_present else None,
            )

    monkeypatch.setattr("core.app.GuardedPlayerSaveSerializer", FakeSerializer)
    project = MagicMock(
        side_effect=AssertionError(
            "failure evidence must not reach setup authoring projection"
        )
    )
    monkeypatch.setattr("core.app.project_forced_save_setup", project)
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._adb_target_session = SimpleNamespace(snapshot=lambda: None)
    app._player_save_acquirer = object()
    app._runtime_action_guard = lambda **_kwargs: True
    app._log_operator_workflow_intent = lambda *_args, **_kwargs: None
    app._log_operator_workflow_result = lambda *_args, **_kwargs: None

    assert app._sync_setup_capture(object()) is True

    result = supervisor.setup_capture
    assert result["status"] == expected_status
    assert reason_fragment in result["reason"]
    assert result["authority_outcome"] == expected_authority
    assert supervisor.is_paused is paused
    if expected_authority == "preserved":
        assert "did not change automation authority" in result["reason"]
    elif expected_authority == "continuity_gated":
        assert app._get_action_authority().strategy_gate is not None
    assert serializer_calls == ["serialize"]
    project.assert_not_called()


@pytest.mark.parametrize(
    ("lifecycle_input_attempted", "expected_status", "paused"),
    (
        (False, "unavailable", False),
        (True, "failed", True),
    ),
)
def test_runtime_setup_capture_pauses_only_when_attempted_source_is_unrestored(
    tmp_path,
    monkeypatch,
    lifecycle_input_attempted,
    expected_status,
    paused,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="active_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    store.request_setup_capture(evidence=evidence, source="test")
    supervisor.apply_control()

    class FakeSerializer:
        def __init__(self, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return SimpleNamespace(
                status=GuardedSerializationStatus.BLOCKED,
                reason="source boundary unavailable",
                source_restored=False,
                lifecycle_input_attempted=lifecycle_input_attempted,
                # A transport failure can report no accepted dispatch even
                # though KEYCODE_HOME was already attempted.
                background_dispatched=False,
                acquisition=None,
            )

    monkeypatch.setattr("core.app.GuardedPlayerSaveSerializer", FakeSerializer)
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._adb_target_session = SimpleNamespace(snapshot=lambda: None)
    app._player_save_acquirer = object()
    app._runtime_action_guard = lambda **_kwargs: True
    app._log_operator_workflow_intent = lambda *_args, **_kwargs: None
    app._log_operator_workflow_result = lambda *_args, **_kwargs: None
    app._setup_capture_source_refreshed = False

    assert app._sync_setup_capture(object()) is True

    result = supervisor.setup_capture
    assert result["status"] == expected_status
    assert supervisor.is_paused is paused
    assert result["authority_outcome"] == (
        "paused_for_safety" if paused else "preserved"
    )
    assert app._setup_capture_source_refreshed is lifecycle_input_attempted


def test_runtime_setup_capture_terminal_write_retry_never_serializes_twice(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("RUNNING", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_new_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    store.request_setup_capture(evidence=evidence, source="test")
    supervisor.apply_control()
    captured = datetime.now(timezone.utc)
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="captured",
        binding=PlayerSaveTargetBinding("localhost:5555", 7),
        acquisition_started_at=captured - timedelta(milliseconds=1),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=SimpleNamespace(
            runtime_save=None,
            mapping_id=None,
            mapping_resolution="unsupported",
            shape_valid=False,
        ),
    )
    serializer_calls = []

    class FakeSerializer:
        def __init__(self, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            serializer_calls.append("serialize")
            return SimpleNamespace(
                status=GuardedSerializationStatus.COMPLETE,
                source_restored=True,
                lifecycle_input_attempted=True,
                background_dispatched=True,
                acquisition=acquisition,
            )

    monkeypatch.setattr("core.app.GuardedPlayerSaveSerializer", FakeSerializer)
    original_transition = supervisor.transition_setup_capture
    failures = []

    def fail_first_terminal(request_id, status, **details):
        if status == "unavailable" and not failures:
            failures.append(request_id)
            return None
        return original_transition(request_id, status, **details)

    monkeypatch.setattr(
        supervisor,
        "transition_setup_capture",
        fail_first_terminal,
    )
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._adb_target_session = SimpleNamespace(snapshot=lambda: None)
    app._player_save_acquirer = object()
    app._runtime_action_guard = lambda **_kwargs: True
    app._log_operator_workflow_intent = lambda *_args, **_kwargs: None
    app._log_operator_workflow_result = lambda *_args, **_kwargs: None

    assert app._sync_setup_capture(object()) is True
    assert supervisor.setup_capture["status"] == "capturing"
    assert supervisor.is_paused is False
    assert app._sync_setup_capture(object()) is True

    terminal = supervisor.setup_capture
    assert terminal["status"] == "unavailable"
    assert terminal["authority_outcome"] == "preserved"
    assert supervisor.is_paused is False
    assert serializer_calls == ["serialize"]
    assert app._pending_setup_capture_claims() == {}


def test_runtime_setup_capture_reports_pause_without_using_cached_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ADB_DEVICE", "localhost:5555")
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    store.set_state("PAUSED", source="test")
    supervisor = AutomationSupervisor(control_file=str(path))
    supervisor.apply_control()
    owner = supervisor.current_exclusive_validation_owner()
    evidence = _evidence(
        game_state="home_new_battle",
        runtime_id=str(owner["runtime_id"]),
    )
    evidence["pid"] = owner["pid"]
    store.request_setup_capture(evidence=evidence, source="test")
    supervisor.apply_control()
    app = App.__new__(App)
    app._supervisor = supervisor
    app._mission_mgr = MissionManager(None, None)
    app._control_observation = {
        key: value
        for key, value in evidence.items()
        if key not in {"runtime_id", "pid", "adb_target"}
    }
    app._log_operator_workflow_result = lambda *_args, **_kwargs: None

    app._sync_operator_control_workflows({"state": "HOME_SCREEN"})

    result = supervisor.setup_capture
    assert result["status"] == "unavailable"
    assert "no cached save was used" in result["reason"]
    assert "preview" not in result
