from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import stat

import pytest

from core.cell_balance_tracker import CellBalanceTracker
from core.player_save import (
    PlayerSaveCapabilityEvidence,
    PlayerSaveSnapshot,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)
from core.runtime_save import (
    BattleHistoryTail,
    CellBalanceSnapshot,
    NormalizedRuntimeSave,
)


CAPABILITY_ID = "thetower.player_save.cell_balance.v1"
SEMANTIC_FINGERPRINT = "a" * 64
BINDING_FINGERPRINT = "b" * 64
START = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _bundle(
    balance: int | float,
    captured_at: datetime,
    *,
    target: str = "localhost:5555",
    generation: int = 1,
    save_revision: int | None = 1,
    source_sha256: str | None = None,
    capability_status: str = "observed",
) -> PlayerSaveAcquisitionBundle:
    balance_snapshot = CellBalanceSnapshot(
        status="observed",
        reason="",
        capability_id=CAPABILITY_ID,
        semantic_fingerprint=SEMANTIC_FINGERPRINT,
        binding_fingerprint=BINDING_FINGERPRINT,
        forward_policy="exact_version_only",
        audit_id="V1101-RUNTIME-021",
        evidence_level="structural_observation",
        value_decimal=str(balance),
        source_fields=("cells",),
    )
    history = BattleHistoryTail(
        structural_status="observed",
        structural_reason="",
        entry_count=0,
        capacity=30,
        identity=None,
        completed_entry_status="unavailable",
        completed_entry_reason="battle_history_empty",
        entry=None,
    )
    runtime = NormalizedRuntimeSave(
        mapping_id="data-9-game-1101",
        audit_matrix_id="data-9-game-1073-runtime-audit-v2",
        capture={},
        save_revision=save_revision,
        round_active=False,
        current_wave=0,
        active_round_identity=None,
        perks_status="not_applicable",
        perks_reason="inactive_round",
        perks=None,
        battle_history_tail=history,
        cell_balance_status="observed",
        cell_balance_reason="",
        cell_balance=balance_snapshot,
    )
    source_sha256 = source_sha256 or hashlib.sha256(
        f"{balance}\0{captured_at.isoformat()}\0{save_revision}".encode(
            "utf-8"
        )
    ).hexdigest()
    snapshot = PlayerSaveSnapshot(
        captured_at=captured_at.isoformat(),
        source_name="playerInfo.dat",
        source_sha256=source_sha256,
        source_size=100,
        container="gzip+nrbf",
        decompressed_size=200,
        root_class="SaveLoad+PlayerData",
        field_count=741,
        data_version=9,
        game_version=1101,
        save_revision=save_revision,
        mapping_id="data-9-game-1101",
        mapping_maturity="candidate",
        validated_checks=(),
        shape_valid=True,
        warnings=(),
        profile_summary={},
        checks={},
        runtime_save=runtime,
        capabilities={
            CAPABILITY_ID: PlayerSaveCapabilityEvidence(
                capability_id=CAPABILITY_ID,
                status=capability_status,
                reason="" if capability_status == "observed" else "unavailable",
                semantic_fingerprint=SEMANTIC_FINGERPRINT,
                binding_fingerprint=BINDING_FINGERPRINT,
                authority_id="V1101-RUNTIME-021",
                provider_mapping_id="data-9-game-1101",
                resolution="compatible_exact_revision",
                forward_policy="exact_version_only",
            )
        },
    )
    return PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="captured",
        binding=PlayerSaveTargetBinding(target, generation),
        acquisition_started_at=captured_at - timedelta(milliseconds=1),
        captured_at=captured_at,
        acquisition_completed_at=captured_at + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=snapshot,
    )


def test_tracker_persists_falling_trend_and_buffer_estimate(tmp_path: Path):
    path = tmp_path / "cell_balance.sqlite3"
    tracker = CellBalanceTracker(path, buffer_floor=1_000)

    assert tracker.observe_bundle(_bundle(2_000, START, save_revision=10)) == (
        "accepted_observation"
    )
    assert tracker.observe_bundle(
        _bundle(1_800, START + timedelta(hours=12), save_revision=11)
    ) == "accepted_observation"
    assert tracker.observe_bundle(
        _bundle(1_500, START + timedelta(hours=25), save_revision=12)
    ) == "accepted_observation"

    status = tracker.status()
    assert status["status"] == "observed"
    assert status["balance_decimal"] == "1500"
    assert status["trend"] == {
        "direction": "falling",
        "basis": "24h_window",
        "change_decimal": "-500",
        "elapsed_hours_decimal": "25",
        "net_per_hour_decimal": "-20",
    }
    assert status["previous"]["change_decimal"] == "-300"
    assert status["buffer"] == {
        "status": "above",
        "floor_decimal": "1000",
        "headroom_decimal": "500",
        "estimated_hours_to_floor_decimal": "25",
        "automatic_reduction_enabled": False,
    }
    assert status["history"]["sample_count"] == 3
    assert status["history"]["comparable_sample_count"] == 3
    assert status["ui_action_authority"] is False
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    reloaded = CellBalanceTracker(path, buffer_floor=1_000).status()
    assert reloaded["balance_decimal"] == "1500"
    assert reloaded["trend"] == status["trend"]


def test_unchanged_save_at_a_later_capture_remains_a_flat_sample(tmp_path: Path):
    tracker = CellBalanceTracker(tmp_path / "cell_balance.sqlite3")
    unchanged_source = "c" * 64

    first = _bundle(2_000, START, source_sha256=unchanged_source)
    later = _bundle(
        2_000,
        START + timedelta(hours=6),
        source_sha256=unchanged_source,
    )
    assert tracker.observe_bundle(first) == "accepted_observation"
    assert tracker.observe_bundle(first) == "ignored_duplicate"
    assert tracker.observe_bundle(later) == "accepted_observation"

    status = tracker.status()
    assert status["trend"]["direction"] == "flat"
    assert status["trend"]["change_decimal"] == "0"
    assert status["history"]["comparable_sample_count"] == 2


def test_reconnect_continues_target_trend_but_target_switch_does_not(
    tmp_path: Path,
):
    tracker = CellBalanceTracker(tmp_path / "cell_balance.sqlite3")

    tracker.observe_bundle(_bundle(2_000, START, generation=1))
    tracker.observe_bundle(
        _bundle(2_100, START + timedelta(hours=1), generation=2)
    )
    continued = tracker.status()
    assert continued["trend"]["direction"] == "rising"
    assert continued["history"]["comparable_sample_count"] == 2

    tracker.observe_bundle(
        _bundle(
            500,
            START + timedelta(hours=2),
            target="localhost:5565",
        )
    )
    switched = tracker.status()
    assert switched["balance_decimal"] == "500"
    assert switched["trend"]["direction"] == "unknown"
    assert switched["history"]["sample_count"] == 3
    assert switched["history"]["comparable_sample_count"] == 1


def test_save_revision_rollback_starts_a_new_comparison_epoch(tmp_path: Path):
    tracker = CellBalanceTracker(tmp_path / "cell_balance.sqlite3")

    tracker.observe_bundle(_bundle(3_000, START, save_revision=20))
    tracker.observe_bundle(
        _bundle(1_000, START + timedelta(hours=1), save_revision=19)
    )
    reset = tracker.status()
    assert reset["trend"]["direction"] == "unknown"
    assert reset["history"]["comparable_sample_count"] == 1

    tracker.observe_bundle(
        _bundle(1_100, START + timedelta(hours=2), save_revision=20)
    )
    resumed = tracker.status()
    assert resumed["trend"]["direction"] == "rising"
    assert resumed["trend"]["change_decimal"] == "100"
    assert resumed["history"]["comparable_sample_count"] == 2


def test_out_of_order_and_untrusted_capability_are_rejected(tmp_path: Path):
    tracker = CellBalanceTracker(tmp_path / "cell_balance.sqlite3")
    later = _bundle(2_000, START + timedelta(hours=2))

    assert tracker.observe_bundle(later) == "accepted_observation"
    assert tracker.observe_bundle(_bundle(1_900, START)) == (
        "ignored_out_of_order_observation"
    )
    assert tracker.observe_bundle(
        _bundle(
            2_100,
            START + timedelta(hours=3),
            capability_status="unavailable",
        )
    ) == "rejected_cell_balance_capability_unavailable"
    assert tracker.status()["history"]["sample_count"] == 1


def test_buffer_breach_warns_without_enabling_automatic_reduction(
    tmp_path: Path,
):
    tracker = CellBalanceTracker(
        tmp_path / "cell_balance.sqlite3",
        buffer_floor=1_000,
    )
    tracker.observe_bundle(_bundle(900, START))

    buffer = tracker.status()["buffer"]
    assert buffer == {
        "status": "below",
        "floor_decimal": "1000",
        "headroom_decimal": "-100",
        "estimated_hours_to_floor_decimal": None,
        "automatic_reduction_enabled": False,
    }


def test_buffer_floor_can_be_changed_live_without_rewriting_history(tmp_path: Path):
    tracker = CellBalanceTracker(tmp_path / "cell_balance.sqlite3")
    tracker.observe_bundle(_bundle(900, START))

    assert tracker.set_buffer_floor("1000") is True
    assert tracker.status()["buffer"]["status"] == "below"
    assert tracker.set_buffer_floor("1000") is False
    assert tracker.set_buffer_floor(None) is True
    assert tracker.status()["buffer"]["status"] == "not_configured"
    with pytest.raises(ValueError, match="nonnegative integer"):
        tracker.set_buffer_floor("1.5")


def test_history_is_bounded_by_capacity_and_retention(tmp_path: Path):
    tracker = CellBalanceTracker(
        tmp_path / "cell_balance.sqlite3",
        retention_days=1,
        max_samples=2,
    )
    tracker.observe_bundle(_bundle(1_000, START, save_revision=1))
    tracker.observe_bundle(
        _bundle(1_100, START + timedelta(hours=12), save_revision=2)
    )
    tracker.observe_bundle(
        _bundle(1_200, START + timedelta(hours=18), save_revision=3)
    )
    assert tracker.status()["history"]["sample_count"] == 2

    tracker.observe_bundle(
        _bundle(1_300, START + timedelta(days=2), save_revision=4)
    )
    status = tracker.status()
    assert status["history"]["sample_count"] == 1
    assert status["history"]["comparable_sample_count"] == 1


def test_unknown_store_schema_fails_closed_without_rewriting_it(tmp_path: Path):
    path = tmp_path / "cell_balance.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    tracker = CellBalanceTracker(path)

    assert tracker.status()["reason"] == "cell_balance_storage_unavailable"
    assert tracker.observe_bundle(_bundle(1_000, START)) == (
        "rejected_storage_unavailable"
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    assert tables == []


def test_corrupt_store_is_nonfatal_and_preserved(tmp_path: Path):
    path = tmp_path / "cell_balance.sqlite3"
    damaged = b"not a sqlite database"
    path.write_bytes(damaged)

    tracker = CellBalanceTracker(path)

    assert tracker.status()["reason"] == "cell_balance_storage_unavailable"
    assert tracker.observe_bundle(_bundle(1_000, START)) == (
        "rejected_storage_unavailable"
    )
    assert path.read_bytes() == damaged
