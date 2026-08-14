from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
)
from core.terminal_save_report import (
    terminal_history_transition_from_acquisition,
    terminal_mapping_workflow_provenance,
    terminal_save_report_complete,
    terminal_save_report_from_acquisition,
    terminal_save_report_structural_complete,
    validate_terminal_history_handoff,
)
from core.player_save_history import history_metadata_from_acquisition


MAPPING_ID = "data-9-game-1073"
SCOPE_ID = "current-run"


class _Entry:
    def __init__(self, *, is_tournament: bool = False):
        self.is_tournament = is_tournament

    def as_dict(self):
        return {
            "schema_version": 1,
            "mapping_id": MAPPING_ID,
            "identity": {
                "tier": 19,
                "wave": 5000,
                "is_tournament": self.is_tournament,
            },
            "more_stats": {
                "source_method": "player_save_battle_history",
                "source_complete": True,
                "row_count": 144,
                "sections": [],
            },
            "fingerprint": "c" * 64,
        }


def _metadata(*, fingerprint: str, count: int, capacity: int = 30):
    return {
        "schema_version": 2,
        "source": "player_save",
        "mapping_id": MAPPING_ID,
        "effective_mapping_fingerprint": "9" * 64,
        "identity_schema_version": 2,
        "fingerprint": fingerprint,
        "tier": 19,
        "wave": 5000,
        "battle_date": {
            "kind_id": 2,
            "kind": "local",
            "clock_basis": "local_wall_clock_without_offset",
            "clock_time": "2026-08-06T04:00:00",
            "ticks": "639000000000000000",
            "submicrosecond_100ns": 0,
        },
        "entry_count": count,
        "capacity": capacity,
    }


def _snapshot(
    *,
    fingerprint: str = "b" * 64,
    count: int = 30,
    is_tournament: bool = False,
    semantic_status: str = "observed",
):
    identity = SimpleNamespace(
        mapping_id=MAPPING_ID,
        fingerprint=fingerprint,
        tier=19,
        wave=5000,
        is_tournament=is_tournament,
        battle_date=_metadata(fingerprint=fingerprint, count=count)["battle_date"],
    )
    tail = SimpleNamespace(
        structural_status="observed",
        structural_reason="",
        capacity=30,
        entry_count=count,
        identity=identity,
        completed_entry_status=semantic_status,
        completed_entry_reason=(
            "unmapped_killed_by_id:42" if semantic_status != "observed" else ""
        ),
        entry=(
            _Entry(is_tournament=is_tournament)
            if semantic_status == "observed"
            else None
        ),
    )
    return SimpleNamespace(
        captured_at="2026-08-06T11:00:00.001000+00:00",
        mapping_id=MAPPING_ID,
        mapping_resolution="exact",
        mapping_semantic_fingerprint="8" * 64,
        effective_mapping_fingerprint="9" * 64,
        save_revision=48000,
        source_sha256="d" * 64,
        runtime_save=SimpleNamespace(
            mapping_id=MAPPING_ID,
            round_active=False,
            battle_history_tail=tail,
        ),
    )


def _binding():
    return {
        "schema_version": 1,
        "status": "bound",
        "activity_scope_run_id": SCOPE_ID,
    }


def _acquisition(snapshot, *, terminal_state="GAME_OVER"):
    started = datetime(2026, 8, 6, 11, 0, tzinfo=timezone.utc)
    captured = started + timedelta(milliseconds=1)
    return PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.NATURAL_BOUNDARY,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=PlayerSaveTargetBinding("private-target", 3),
        acquisition_started_at=started,
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=snapshot,
        boundary=PlayerSaveNaturalBoundary(
            kind=PlayerSaveBoundaryKind(terminal_state),
            observed_at=started,
            runtime_session_id="runtime-1",
            activity_scope_id=SCOPE_ID,
        ),
    )


def _scope(baseline):
    return {
        "schema_version": 1,
        "run_id": SCOPE_ID,
        "latest_completed_battle": baseline,
    }


def test_terminal_save_report_accepts_one_bound_append():
    report = terminal_save_report_from_acquisition(
        _acquisition(_snapshot(count=30)),
        terminal_state="GAME_OVER",
        run_binding=_binding(),
        activity_scope=_scope(
            _metadata(fingerprint="a" * 64, count=29)
        ),
    )

    assert terminal_save_report_complete(report)
    assert report["history_transition"] == {
        "status": "append",
        "baseline_fingerprint": "a" * 64,
        "observed_fingerprint": "b" * 64,
        "baseline_entry_count": 29,
        "observed_entry_count": 30,
        "capacity": 30,
    }
    assert report["completed_entry"]["fingerprint"] == "c" * 64


def test_terminal_mapping_workflow_binds_semantic_neutral_tail():
    acquisition = _acquisition(
        _snapshot(count=30, semantic_status="unavailable")
    )
    scope = _scope(_metadata(fingerprint="a" * 64, count=29))
    transition = terminal_history_transition_from_acquisition(
        acquisition,
        terminal_state="GAME_OVER",
        run_binding=_binding(),
        activity_scope=scope,
    )

    workflow = terminal_mapping_workflow_provenance(
        acquisition,
        terminal_state="GAME_OVER",
        run_binding=_binding(),
        activity_scope=scope,
        history_transition=transition,
        pid=4242,
    )

    assert workflow is not None
    assert workflow["game_state"] == "terminal_game_over"
    assert workflow["active_round_identity_fingerprint"] == "b" * 64


@pytest.mark.parametrize(
    "mutation",
    (
        "scope_changed",
        "target_changed",
        "effective_authority_changed",
        "terminal_kind_changed",
        "non_exact_mapping",
    ),
)
def test_terminal_mapping_workflow_rejects_lost_boundary_authority(mutation):
    snapshot = _snapshot(count=30, semantic_status="unavailable")
    acquisition = _acquisition(snapshot)
    scope = _scope(_metadata(fingerprint="a" * 64, count=29))
    transition = terminal_history_transition_from_acquisition(
        acquisition,
        terminal_state="GAME_OVER",
        run_binding=_binding(),
        activity_scope=scope,
    )
    binding = _binding()
    terminal = "GAME_OVER"
    if mutation == "scope_changed":
        scope = {**scope, "run_id": "replacement-run"}
    elif mutation == "target_changed":
        transition["handoff"]["source"][
            "target_generation_fingerprint"
        ] = "f" * 64
    elif mutation == "effective_authority_changed":
        transition["handoff"]["source"][
            "effective_mapping_fingerprint"
        ] = "e" * 64
    elif mutation == "terminal_kind_changed":
        terminal = "TOURNAMENT_RESULTS"
    else:
        snapshot.mapping_resolution = "compatible_forward_revision"

    workflow = terminal_mapping_workflow_provenance(
        acquisition,
        terminal_state=terminal,
        run_binding=binding,
        activity_scope=scope,
        history_transition=transition,
        pid=4242,
    )

    assert workflow is None


def test_terminal_save_report_accepts_capacity_rollover_and_tournament_kind():
    report = terminal_save_report_from_acquisition(
        _acquisition(
            _snapshot(count=30, is_tournament=True),
            terminal_state="TOURNAMENT_RESULTS",
        ),
        terminal_state="TOURNAMENT_RESULTS",
        run_binding=_binding(),
        activity_scope=_scope(
            _metadata(fingerprint="a" * 64, count=30)
        ),
    )

    assert terminal_save_report_complete(report)
    assert report["history_transition"]["status"] == "capacity_rollover"


@pytest.mark.parametrize(
    ("binding", "baseline", "reason"),
    (
        ({"status": "unbound"}, _metadata(fingerprint="a" * 64, count=29), "terminal_run_unbound"),
        (_binding(), None, "pre_terminal_history_baseline_unavailable"),
        (_binding(), _metadata(fingerprint="b" * 64, count=30), "terminal_history_tail_unchanged"),
        (_binding(), _metadata(fingerprint="a" * 64, count=28), "terminal_history_tail_transition_invalid"),
    ),
)
def test_terminal_save_report_fails_closed_without_causal_tail_proof(
    binding,
    baseline,
    reason,
):
    scope = {"schema_version": 1, "run_id": SCOPE_ID}
    if baseline is not None:
        scope["latest_completed_battle"] = baseline

    report = terminal_save_report_from_acquisition(
        _acquisition(_snapshot(count=30)),
        terminal_state="GAME_OVER",
        run_binding=binding,
        activity_scope=scope,
    )

    assert not terminal_save_report_complete(report)
    assert report["reason"] == reason
    assert report["ui_fallback"]["required"]


def test_semantic_forward_report_retains_only_monitor_bound_terminal_claims():
    snapshot = _snapshot(count=30)
    snapshot.mapping_resolution = "semantic_forward_revision"
    tail = snapshot.runtime_save.battle_history_tail
    tail.structural_status = "unavailable"
    tail.structural_reason = "legacy_history_capability_not_declared"
    tail.identity = None
    tail.terminal_identity = SimpleNamespace(is_tournament=False)
    tail.terminal_metric_claims = {
        "status": "observed",
        "reason": "",
        "capability_id": "thetower.player_save.active_run_tallies.v1",
        "claims": {"coinsEarned": {"status": "observed"}},
        "unavailable": {},
    }
    acquisition = _acquisition(snapshot)

    report = terminal_save_report_from_acquisition(
        acquisition,
        terminal_state="GAME_OVER",
        run_binding=_binding(),
        activity_scope=_scope(_metadata(fingerprint="a" * 64, count=29)),
        history_transition={
            "schema_version": 1,
            "status": "unavailable",
            "complete": False,
            "reason": "legacy_history_capability_not_declared",
        },
    )

    assert report["status"] == "unavailable"
    assert report["ui_fallback"]["required"] is True
    assert report["terminal_metric_claims"] == tail.terminal_metric_claims
    assert report["capture"]["source_fingerprint"] == snapshot.source_sha256
    assert report["capture"]["acquisition"] == (
        acquisition.redacted_provenance()
    )
    assert report["active_tally_terminal_relation"] == {
        "status": "monitor_baseline_required",
        "reason": "legacy_history_capability_not_declared",
    }


def test_terminal_save_report_requires_semantic_entry_and_matching_kind():
    baseline = _metadata(fingerprint="a" * 64, count=29)

    unavailable = terminal_save_report_from_acquisition(
        _acquisition(_snapshot(count=30, semantic_status="unavailable")),
        terminal_state="GAME_OVER",
        run_binding=_binding(),
        activity_scope=_scope(baseline),
    )
    mismatched = terminal_save_report_from_acquisition(
        _acquisition(_snapshot(count=30, is_tournament=True)),
        terminal_state="GAME_OVER",
        run_binding=_binding(),
        activity_scope=_scope(baseline),
    )

    assert unavailable["reason"] == "unmapped_killed_by_id:42"
    assert terminal_save_report_structural_complete(unavailable)
    assert not terminal_save_report_complete(unavailable)
    assert mismatched["reason"] == "terminal_history_kind_mismatch"
    assert not terminal_save_report_structural_complete(mismatched)
    assert mismatched["terminal_metric_claims"]["status"] == "unavailable"
    assert mismatched["terminal_metric_claims"]["reason"] == (
        "terminal_history_kind_mismatch"
    )
    assert mismatched["terminal_metric_claims"]["claims"] == {}


@pytest.mark.parametrize(
    ("terminal_state", "is_tournament"),
    (("GAME_OVER", True), ("TOURNAMENT_RESULTS", False)),
)
def test_structural_terminal_transition_rejects_battle_kind_mismatch(
    terminal_state,
    is_tournament,
):
    transition = terminal_history_transition_from_acquisition(
        _acquisition(
            _snapshot(count=30, is_tournament=is_tournament),
            terminal_state=terminal_state,
        ),
        terminal_state=terminal_state,
        run_binding=_binding(),
        activity_scope=_scope(
            _metadata(fingerprint="a" * 64, count=29)
        ),
    )

    assert transition["status"] == "unavailable"
    assert transition["reason"] == "terminal_history_kind_mismatch"
    assert transition["handoff"] is None


def test_structural_handoff_survives_unknown_semantic_killed_by_without_reprojection():
    acquisition = _acquisition(
        _snapshot(count=30, semantic_status="unavailable")
    )
    baseline = _metadata(fingerprint="a" * 64, count=29)

    with patch(
        "core.terminal_save_report.history_metadata_from_acquisition",
        wraps=history_metadata_from_acquisition,
    ) as structural_projector:
        transition = terminal_history_transition_from_acquisition(
            acquisition,
            terminal_state="GAME_OVER",
            run_binding=_binding(),
            activity_scope=_scope(baseline),
        )
        report = terminal_save_report_from_acquisition(
            acquisition,
            terminal_state="GAME_OVER",
            run_binding=_binding(),
            activity_scope=_scope(baseline),
            history_transition=transition,
        )

    structural_projector.assert_called_once_with(acquisition)
    assert transition["complete"] is True
    assert transition["history_transition"]["status"] == "append"
    assert report["reason"] == "unmapped_killed_by_id:42"
    assert report["structural_history"]["status"] == "complete"
    assert report["history_transition"] == transition["history_transition"]
    retained = json.dumps(transition["handoff"], sort_keys=True)
    assert "private-target" not in retained
    assert "runtime-1" not in retained
    assert SCOPE_ID not in retained


def test_terminal_handoff_rejects_process_and_target_generation_changes():
    acquisition = _acquisition(_snapshot(count=30))
    transition = terminal_history_transition_from_acquisition(
        acquisition,
        terminal_state="GAME_OVER",
        run_binding=_binding(),
        activity_scope=_scope(
            _metadata(fingerprint="a" * 64, count=29)
        ),
    )
    handoff = transition["handoff"]

    accepted, reason = validate_terminal_history_handoff(
        handoff,
        runtime_session_id="runtime-1",
        target_binding=acquisition.binding,
        destination_reason="new_battle_preflight",
    )
    restarted, restarted_reason = validate_terminal_history_handoff(
        handoff,
        runtime_session_id="runtime-2",
        target_binding=acquisition.binding,
        destination_reason="new_battle_preflight",
    )
    retargeted, retargeted_reason = validate_terminal_history_handoff(
        handoff,
        runtime_session_id="runtime-1",
        target_binding=PlayerSaveTargetBinding("private-target", 4),
        destination_reason="new_battle_preflight",
    )
    wrong_kind_handoff = json.loads(json.dumps(handoff))
    wrong_kind_handoff["latest_completed_battle"]["is_tournament"] = True
    wrong_kind, wrong_kind_reason = validate_terminal_history_handoff(
        wrong_kind_handoff,
        runtime_session_id="runtime-1",
        target_binding=acquisition.binding,
        destination_reason="new_battle_preflight",
    )

    assert accepted is not None
    assert reason == "terminal_history_handoff_accepted"
    assert restarted is None
    assert restarted_reason == "terminal_history_handoff_process_changed"
    assert retargeted is None
    assert retargeted_reason == "terminal_history_handoff_target_changed"
    assert wrong_kind is None
    assert wrong_kind_reason == "terminal_history_handoff_identity_invalid"
