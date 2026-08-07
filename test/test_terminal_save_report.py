from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
    terminal_save_report_complete,
    terminal_save_report_from_acquisition,
)


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
        "identity_schema_version": 1,
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
        captured_at="2026-08-06T11:00:00+00:00",
        mapping_id=MAPPING_ID,
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
    assert mismatched["reason"] == "terminal_history_kind_mismatch"
