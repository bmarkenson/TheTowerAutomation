from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.app import App
from core.app_setup import config_from_args, parse_args
from core.player_save import PlayerSaveDecodeError, PlayerSavePullError
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)
from core.player_save_audit import (
    AppendOnlyAuditReceiptWriter,
    AuditRequest,
    AuditSaveObservation,
    DEFAULT_PLAYER_SAVE_AUDIT_MANIFEST_PATH,
    PlayerSaveAuditCollector,
    PlayerSaveAuditStateMachine,
    load_player_save_audit_manifest,
)
from core.runtime_save import (
    ActiveRoundIdentity,
    BattleHistoryTail,
    NormalizedRuntimeSave,
    RuntimePerkCalibration,
    RuntimePerkCalibrationPick,
    RuntimePerkSnapshot,
)


UTC = timezone.utc
START = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)
FORBIDDEN_KEY_PARTS = {
    "account",
    "decoded",
    "device_file",
    "exception",
    "more_stats",
    "ocr",
    "pixel",
    "player_id",
    "playerid",
    "profile",
    "raw",
    "root",
    "screenshot",
    "user_name",
    "username",
}


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _identity(*, tier: int = 22, counter: int = 4, seed: int = 12345):
    return {
        "fingerprint": _sha(f"identity:{tier}:{counter}:{seed}"),
        "game_version": 1073,
        "tier": tier,
        "per_tier_counter": counter,
        "seed": seed,
    }


def _pick(
    sequence: int,
    *,
    wave: int,
    perk_id: int,
    perk_key: str,
    level_after: int = 1,
):
    return {
        "sequence": sequence,
        "saved_wave": wave,
        "perk_id": perk_id,
        "perk_key": perk_key,
        "level_after": level_after,
    }


def _perks(
    picks=(),
    *,
    state: str = "active_round",
    status: str = "observed",
    reason: str = "available",
):
    picks = [dict(pick) for pick in picks]
    return {
        "status": status,
        "reason_code": reason,
        "state": state if status == "observed" else None,
        "picked_count": len(picks) if status == "observed" else None,
        "fingerprint": (
            _sha(json.dumps(picks, sort_keys=True) + state)
            if status == "observed"
            else None
        ),
        "picks": picks if status == "observed" else [],
    }


def _tail(
    label: str,
    *,
    count: int,
    capacity: int = 30,
    killed_by_id: int = 3,
    semantic_status: str = "observed",
    arbitrary: dict | None = None,
):
    fingerprint = _sha(f"tail:{label}") if count else None
    newest = (
        {
            "battle_date": {
                "kind_id": 2,
                "kind": "local",
                "ticks": "638897112000000000",
                "clock_time": "2026-08-02T13:00:00.000000",
                "clock_basis": "local_wall_clock_without_offset",
                "submicrosecond_100ns": 0,
            },
            "tier": 22,
            "wave": 751,
            "game_time_seconds": 4000.0,
            "real_time_seconds": 800.0,
            "killed_by_id": killed_by_id,
            "is_tournament": False,
            **(arbitrary or {}),
        }
        if count
        else None
    )
    return {
        "structural_status": "observed" if count else "empty",
        "structural_reason_code": "available" if count else "battle_history_empty",
        "entry_count": count,
        "capacity": capacity,
        "fingerprint": fingerprint,
        "newest_identity": newest,
        "semantic_completed_entry": {
            "status": semantic_status if count else "not_applicable",
            "reason_code": (
                "available"
                if semantic_status == "observed"
                else "unmapped_killed_by_id"
            ),
            "fingerprint": (
                _sha(f"semantic:{label}")
                if count and semantic_status == "observed"
                else None
            ),
            "more_stats_rows": ["must-not-survive"],
        },
    }


def _observation(
    revision: int,
    *,
    active: bool,
    wave: int,
    identity=None,
    perks=None,
    tail=None,
    source_label: str | None = None,
    target_label: str = "localhost:5555",
    captured_at: datetime | None = None,
):
    captured = captured_at or START + timedelta(seconds=revision)
    return AuditSaveObservation(
        mapping_id="data-9-game-1073",
        audit_matrix_id="data-9-game-1073-runtime-audit-v2",
        game_version=1073,
        captured_at=captured,
        source_fingerprint=_sha(source_label or f"source:{revision}"),
        save_revision=revision,
        round_active=active,
        saved_wave=wave,
        active_identity=(identity if active else None),
        perks=(
            perks
            if perks is not None
            else _perks(state="active_round" if active else "cleared")
        ),
        history_tail=tail or _tail("baseline", count=29),
        target_fingerprint=_sha(target_label),
        acquisition_started_at=captured - timedelta(milliseconds=125),
        acquisition_completed_at=captured + timedelta(milliseconds=25),
    )


def _request(boundary: str | None, *, offset: int = 0, reason: str | None = None):
    observed = START + timedelta(seconds=offset)
    default_reason = {
        "HOME_NEW_BATTLE": "home_new_battle",
        "RUNNING": "first_running_observation",
        "GAME_OVER": "game_over",
        "TOURNAMENT_RESULTS": "tournament_results",
        None: "periodic_interval",
    }[boundary]
    return AuditRequest(
        reasons=(reason or default_reason,),
        requested_at=observed,
        boundary_label=boundary,
        boundary_observed_at=observed if boundary is not None else None,
    )


def _machine(receipts: list[dict], *, runtime="runtime-1", collector="collector-1"):
    machine = PlayerSaveAuditStateMachine(
        load_player_save_audit_manifest(),
        receipt_sink=lambda receipt: receipts.append(dict(receipt)),
        interval_seconds=300,
        runtime_session_id=runtime,
        collector_session_id=collector,
        now_fn=lambda: START,
    )
    machine.start_session()
    return machine


def _records(receipts, record_type):
    return [record for record in receipts if record["record_type"] == record_type]


def _unmapped_runtime(revision: int) -> NormalizedRuntimeSave:
    return NormalizedRuntimeSave(
        mapping_id="data-9-game-1073",
        audit_matrix_id="data-9-game-1073-runtime-audit-v2",
        capture={
            "captured_at": (START + timedelta(seconds=revision)).isoformat(),
            "source_sha256": _sha(f"unmapped-save:{revision}"),
        },
        save_revision=revision,
        round_active=True,
        current_wave=250 + revision,
        active_round_identity=ActiveRoundIdentity(
            game_version=1073,
            current_tier=19,
            rounds_started_this_tier=232,
            round_seed=123456,
            fingerprint=_sha("active-round"),
        ),
        perks_status="unavailable",
        perks_reason="unmapped_perk_id:11",
        perks=None,
        battle_history_tail=BattleHistoryTail(
            structural_status="empty",
            structural_reason="battle_history_empty",
            entry_count=0,
            capacity=30,
            identity=None,
            completed_entry_status="not_applicable",
            completed_entry_reason="battle_history_empty",
            entry=None,
        ),
        perk_calibration=RuntimePerkCalibration(
            state="active_round",
            picked_count=1,
            levels=((11, 1),),
            picks=(RuntimePerkCalibrationPick(1, 200, 11, 1),),
            known_ids=((10, "perk_wave_requirement"),),
            fingerprint=_sha("numeric-perk-calibration"),
        ),
    )


def _mapping_batch(*, family: str = "interest") -> dict:
    return {
        "schema_version": 1,
        "sequence": 1,
        "scheduled_wave": 200,
        "scheduled_waves": [200],
        "boundary_coverage": "complete",
        "selection_model": "simultaneous_batch",
        "observed_at": START.isoformat(),
        "selections": [
            {
                "family": family,
                "confidence_percent": 95.0,
                "change": "added",
            }
        ],
    }


def test_cli_and_environment_opt_in_are_default_disabled_and_bounded(monkeypatch):
    monkeypatch.delenv("THETOWER_PLAYER_SAVE_AUDIT", raising=False)
    monkeypatch.delenv("THETOWER_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS", raising=False)
    config = config_from_args(parse_args([]))
    assert config.player_save_audit_enabled is False
    assert config.player_save_audit_interval_seconds == 300

    config = config_from_args(
        parse_args(
            [
                "--player-save-audit",
                "--player-save-audit-interval-seconds",
                "30",
            ]
        )
    )
    assert config.player_save_audit_enabled is True
    assert config.player_save_audit_interval_seconds == 30

    monkeypatch.setenv("THETOWER_PLAYER_SAVE_AUDIT", "yes")
    monkeypatch.setenv("THETOWER_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS", "600")
    config = config_from_args(parse_args([]))
    assert config.player_save_audit_enabled is True
    assert config.player_save_audit_interval_seconds == 600
    assert (
        config_from_args(
            parse_args(["--no-player-save-audit"])
        ).player_save_audit_enabled
        is False
    )

    with pytest.raises(SystemExit):
        parse_args(["--player-save-audit-interval-seconds", "29"])
    with pytest.raises(SystemExit):
        parse_args(["--player-save-audit-interval-seconds", "3601"])
    monkeypatch.setenv("THETOWER_PLAYER_SAVE_AUDIT", "sometimes")
    with pytest.raises(SystemExit):
        parse_args([])


def test_disabled_collector_performs_zero_acquisition_and_creates_zero_files(
    tmp_path,
):
    pulls = Mock()
    targets = Mock()
    receipt = tmp_path / "audit" / "receipts.jsonl"
    collector = PlayerSaveAuditCollector(
        enabled=False,
        interval_seconds=300,
        target_snapshot_fn=targets,
        receipt_path=receipt,
        pull_fn=pulls,
    )

    collector.observe_screen(
        {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
    )
    collector.request_observation("periodic_interval")
    collector.observe_visual_events([{"ability": "nuke"}])
    collector.observe_perk_mapping_evidence([_mapping_batch()])
    collector.reset_perk_mapping_evidence()
    collector.close(wait=True)

    pulls.assert_not_called()
    targets.assert_not_called()
    assert not receipt.exists()
    assert not receipt.parent.exists()


def test_collector_rejects_out_of_bounds_interval_without_acquisition_or_receipt(
    tmp_path,
    monkeypatch,
):
    pulls = Mock()
    targets = Mock()
    receipt = tmp_path / "audit" / "receipts.jsonl"
    monkeypatch.setattr("core.player_save_audit.log", Mock())

    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=29,
        target_snapshot_fn=targets,
        receipt_path=receipt,
        pull_fn=pulls,
    )
    collector.observe_screen({"state": "RUNNING"})
    collector.close(wait=True)

    assert collector.enabled is False
    pulls.assert_not_called()
    targets.assert_not_called()
    assert not receipt.exists()
    assert not receipt.parent.exists()


def test_enabled_worker_projects_only_normalized_runtime_evidence(tmp_path):
    receipt = tmp_path / "receipts.jsonl"
    raw_payload = b"raw-save-marker-that-must-not-survive"
    runtime = NormalizedRuntimeSave(
        mapping_id="data-9-game-1073",
        audit_matrix_id="data-9-game-1073-runtime-audit-v2",
        capture={
            "captured_at": START.isoformat(),
            "source_sha256": _sha("stable-raw-source"),
        },
        save_revision=7,
        round_active=False,
        current_wave=0,
        active_round_identity=None,
        perks_status="observed",
        perks_reason="available",
        perks=RuntimePerkSnapshot(
            state="cleared",
            picked_count=0,
            levels=(),
            picks=(),
            fingerprint=_sha("cleared-perks"),
        ),
        battle_history_tail=BattleHistoryTail(
            structural_status="empty",
            structural_reason="battle_history_empty",
            entry_count=0,
            capacity=30,
            identity=None,
            completed_entry_status="not_applicable",
            completed_entry_reason="battle_history_empty",
            entry=None,
        ),
    )
    pull = Mock(return_value=raw_payload)

    def decode(payload, *, source_name, captured_at):
        assert payload == raw_payload
        assert source_name == "playerInfo.dat"
        assert captured_at.tzinfo is not None
        return SimpleNamespace(
            runtime_save=runtime,
            mapping_supported=True,
            shape_valid=True,
            game_version=1073,
        )

    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=1,
            owned=True,
        ),
        receipt_path=receipt,
        pull_fn=pull,
        decode_fn=decode,
    )
    collector.observe_screen(
        {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
    )
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)

    pull.assert_called_once_with(
        device_id="localhost:5555",
        attempts=3,
        settle_seconds=0.1,
    )
    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    save = next(
        record for record in records if record["record_type"] == "save_observation"
    )
    assert save["mapping"]["game_version"] == 1073
    assert save["capture"]["save_revision"] == 7
    assert save["history_tail"]["baseline_comparison"]["status"] == (
        "inactive_home_baseline_recorded"
    )
    assert raw_payload.decode() not in json.dumps(records)


def test_external_mode_projects_shared_bundle_without_another_pull(tmp_path):
    receipt = tmp_path / "receipts.jsonl"
    pull = Mock()
    runtime = _unmapped_runtime(1)
    captured = datetime.fromisoformat(str(runtime.capture["captured_at"]))
    snapshot = SimpleNamespace(
        runtime_save=runtime,
        mapping_supported=True,
        shape_valid=True,
        game_version=1073,
        mapping_id=runtime.mapping_id,
    )
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=PlayerSaveTargetBinding("localhost:5555", 1),
        acquisition_started_at=captured - timedelta(milliseconds=100),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=25),
        transport_stable=True,
        snapshot=snapshot,
    )
    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=1,
            owned=True,
        ),
        receipt_path=receipt,
        pull_fn=pull,
        acquire_internally=False,
    )

    collector.observe_screen({"state": "RUNNING"})
    collector.observe_acquisition(
        acquisition,
        reason_code="periodic_interval",
    )
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)

    pull.assert_not_called()
    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    saves = _records(records, "save_observation")
    assert len(saves) == 1
    assert saves[0]["capture"]["save_revision"] == 1
    assert saves[0]["request"]["reason_codes"] == ["periodic_interval"]
    assert datetime.fromisoformat(saves[0]["request"]["requested_at"]) == (
        acquisition.acquisition_started_at
    )


def test_collector_maps_unknown_perk_and_keeps_mapping_across_retry_reset(tmp_path):
    receipt = tmp_path / "receipts.jsonl"
    decode_count = {"value": 0}

    def decode(_payload, **_kwargs):
        decode_count["value"] += 1
        return SimpleNamespace(
            runtime_save=_unmapped_runtime(decode_count["value"]),
            mapping_supported=True,
            shape_valid=True,
            game_version=1073,
        )

    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=1,
            owned=True,
        ),
        receipt_path=receipt,
        pull_fn=lambda **_kwargs: b"stable",
        decode_fn=decode,
    )
    collector.observe_screen({"state": "RUNNING"})
    assert collector.wait_until_idle(2.0)

    collector.observe_perk_mapping_evidence([_mapping_batch()])
    collector.request_observation("mapping_followup")
    assert collector.wait_until_idle(2.0)

    collector.reset_perk_mapping_evidence()
    collector.request_observation("retry_followup")
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)

    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    calibration = [
        record
        for record in records
        if record["record_type"] == "normalized_component"
        and record["component"]["name"] == "perk_id_calibration"
    ]
    assert len(calibration) == 1
    evidence = calibration[0]["component"]["evidence"]
    assert evidence == {
        "status": "resolved",
        "game_version": 1073,
        "perk_id": 11,
        "perk_key": "interest",
        "save_pick_wave": 200,
        "save_level_after": 1,
        "ui_batch_sequence": 1,
        "ui_confidence_percent": 95,
        "evidence_fingerprint": evidence["evidence_fingerprint"],
        "evidence_semantics": "unique_exact_wave_cross_channel",
        "mapping_scope": "collector_session_only",
    }
    assert len(evidence["evidence_fingerprint"]) == 64

    saves = [
        record for record in records if record["record_type"] == "save_observation"
    ]
    assert [save["perks"]["status"] for save in saves] == [
        "unavailable",
        "observed",
        "observed",
    ]
    assert saves[1]["perks"]["progression"]["delta"] == [
        {
            "sequence": 1,
            "saved_wave": 200,
            "perk_id": 11,
            "perk_key": "interest",
            "level_after": 1,
        }
    ]
    assert saves[-1]["perks"]["progression"]["status"] == (
        "complete_unchanged_prefix"
    )
    rendered = json.dumps(records).lower()
    assert "display_text" not in rendered
    assert "private" not in rendered


def test_target_generation_change_discards_learned_perk_mapping(tmp_path):
    receipt = tmp_path / "receipts.jsonl"
    holder = {"generation": 1, "decode_count": 0}

    def target_snapshot():
        return SimpleNamespace(
            target=f"localhost:{5545 + holder['generation'] * 10}",
            generation=holder["generation"],
            owned=True,
        )

    def decode(_payload, **_kwargs):
        holder["decode_count"] += 1
        return SimpleNamespace(
            runtime_save=_unmapped_runtime(holder["decode_count"]),
            mapping_supported=True,
            shape_valid=True,
            game_version=1073,
        )

    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=target_snapshot,
        receipt_path=receipt,
        pull_fn=lambda **_kwargs: b"stable",
        decode_fn=decode,
    )
    collector.observe_screen({"state": "RUNNING"})
    assert collector.wait_until_idle(2.0)
    collector.observe_perk_mapping_evidence([_mapping_batch()])
    collector.request_observation("mapping_followup")
    assert collector.wait_until_idle(2.0)

    holder["generation"] = 2
    collector.request_observation("target_handoff_followup")
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)

    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    saves = [
        record for record in records if record["record_type"] == "save_observation"
    ]
    assert [save["perks"]["status"] for save in saves] == [
        "unavailable",
        "observed",
        "unavailable",
    ]


def test_mapping_receipt_requires_exact_manifest_context(tmp_path):
    receipt = tmp_path / "receipts.jsonl"
    revision = {"value": 0}

    def decode(_payload, **_kwargs):
        revision["value"] += 1
        runtime = replace(
            _unmapped_runtime(revision["value"]),
            mapping_id="different-exact-version",
        )
        return SimpleNamespace(
            runtime_save=runtime,
            mapping_supported=True,
            shape_valid=True,
            game_version=1073,
        )

    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=1,
            owned=True,
        ),
        receipt_path=receipt,
        pull_fn=lambda **_kwargs: b"stable",
        decode_fn=decode,
    )
    collector.observe_screen({"state": "RUNNING"})
    assert collector.wait_until_idle(2.0)
    collector.observe_perk_mapping_evidence([_mapping_batch()])
    collector.request_observation("mapping_followup")
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)

    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert not _records(records, "normalized_component")
    assert any(
        record.get("outcome", {}).get("code") == "malformed_normalized_evidence"
        for record in records
    )


def test_collector_rejects_unallowlisted_perk_mapping_before_queue(tmp_path):
    receipt = tmp_path / "receipts.jsonl"
    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=1,
            owned=True,
        ),
        receipt_path=receipt,
        pull_fn=lambda **_kwargs: (_ for _ in ()).throw(
            PlayerSavePullError("unused")
        ),
    )
    unsafe = _mapping_batch()
    unsafe["selections"][0]["display_text"] = "must not queue"

    collector.observe_perk_mapping_evidence([unsafe])
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)

    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    outcomes = _records(records, "audit_outcome")
    assert outcomes[-1]["outcome"]["code"] == (
        "perk_id_mapping_evidence_rejected"
    )
    assert "must not queue" not in json.dumps(records)


def test_natural_boundary_state_machine_retains_perk_deltas_clear_and_tail_candidate():
    receipts: list[dict] = []
    machine = _machine(receipts)
    identity = _identity()
    first_pick = _pick(1, wave=100, perk_id=10, perk_key="perk_wave_requirement")
    second_pick = _pick(2, wave=150, perk_id=0, perk_key="max_health")
    third_pick = _pick(
        3,
        wave=190,
        perk_id=10,
        perk_key="perk_wave_requirement",
        level_after=2,
    )

    baseline = _observation(
        100,
        active=False,
        wave=0,
        tail=_tail("before", count=29),
    )
    first_active = _observation(
        101,
        active=True,
        wave=5,
        identity=identity,
        perks=_perks(),
        tail=_tail("before", count=29),
    )
    second_active = _observation(
        102,
        active=True,
        wave=125,
        identity=identity,
        perks=_perks([first_pick]),
        tail=_tail("before", count=29),
    )
    final_active = _observation(
        103,
        active=True,
        wave=210,
        identity=identity,
        perks=_perks([first_pick, second_pick, third_pick]),
        tail=_tail("before", count=29),
    )
    terminal = _observation(
        104,
        active=False,
        wave=0,
        perks=_perks(state="cleared"),
        tail=_tail("after", count=30),
    )

    assert machine.observe_save(baseline, _request("HOME_NEW_BATTLE"))
    assert machine.observe_save(first_active, _request("RUNNING", offset=1))
    assert machine.observe_save(second_active, _request(None, offset=2))
    assert machine.observe_save(final_active, _request(None, offset=3))
    assert machine.observe_save(terminal, _request("GAME_OVER", offset=4))

    saves = _records(receipts, "save_observation")
    assert saves[0]["history_tail"]["baseline_comparison"]["status"] == (
        "inactive_home_baseline_recorded"
    )
    assert saves[1]["round"]["identity_status"] == (
        "first_naturally_serialized_identity"
    )
    assert saves[1]["round"]["attachment_status"] == ("not_assessed_observation_only")
    assert saves[2]["round"]["identity_status"] == "same_identity_newer_revision"
    assert saves[2]["perks"]["progression"]["delta"] == [first_pick]
    assert saves[3]["perks"]["progression"]["delta"] == [
        second_pick,
        third_pick,
    ]
    assert saves[4]["perks"]["progression"]["status"] == ("first_cleared_projection")
    assert saves[4]["perks"]["last_complete_same_identity"] == {
        "save_revision": 103,
        "saved_wave": 210,
        "picked_count": 3,
        "fingerprint": final_active.perks["fingerprint"],
    }
    candidate = saves[4]["history_tail"]["baseline_comparison"]
    assert candidate["status"] == "candidate_tail_change"
    assert candidate["candidate_only"] is True
    assert candidate["capacity_rollover"] is False
    assert candidate["semantic_completed_entry_status"] == "observed"
    assert saves[4]["timing"]["semantics"] == (
        "observation_latency_only_not_game_write_time"
    )
    assert all(record["authority"]["dispatch"] is False for record in saves)


def test_duplicate_revision_and_source_are_suppressed_without_rewriting_prior_lines(
    tmp_path,
):
    path = tmp_path / "receipts.jsonl"
    prior = '{"preexisting":"preserved"}\n'
    path.write_text(prior, encoding="utf-8")
    writer = AppendOnlyAuditReceiptWriter(path)
    machine = PlayerSaveAuditStateMachine(
        load_player_save_audit_manifest(),
        receipt_sink=writer.append,
        interval_seconds=300,
        runtime_session_id="runtime-append",
        collector_session_id="collector-append",
        now_fn=lambda: START,
    )
    observation = _observation(
        10,
        active=False,
        wave=0,
        tail=_tail("before", count=2),
    )
    request = _request("HOME_NEW_BATTLE")

    machine.start_session()
    assert machine.observe_save(observation, request)
    assert not machine.observe_save(
        observation,
        _request("GAME_OVER", offset=1),
    )

    rendered = path.read_text(encoding="utf-8")
    assert rendered.startswith(prior)
    lines = rendered.splitlines()
    assert lines[0] == prior.rstrip()
    appended = [json.loads(line) for line in lines[1:]]
    assert [item["record_type"] for item in appended] == [
        "collector_session",
        "save_observation",
    ]


def test_duplicate_terminal_save_can_seed_the_next_exact_home_baseline():
    receipts: list[dict] = []
    machine = _machine(receipts)
    first_identity = _identity(counter=4, seed=111)
    next_identity = _identity(counter=5, seed=222)
    terminal = _observation(
        3,
        active=False,
        wave=0,
        perks=_perks(state="cleared"),
        tail=_tail("first-terminal", count=2),
    )

    assert machine.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=1)),
        _request("HOME_NEW_BATTLE"),
    )
    assert machine.observe_save(
        _observation(
            2,
            active=True,
            wave=50,
            identity=first_identity,
            tail=_tail("before", count=1),
        ),
        _request("RUNNING", offset=1),
    )
    assert machine.observe_save(terminal, _request("GAME_OVER", offset=2))
    assert not machine.observe_save(
        terminal,
        _request("HOME_NEW_BATTLE", offset=3),
    )
    assert machine.observe_save(
        _observation(
            4,
            active=True,
            wave=60,
            identity=next_identity,
            tail=_tail("first-terminal", count=2),
        ),
        _request("RUNNING", offset=4),
    )
    assert machine.observe_save(
        _observation(
            5,
            active=False,
            wave=0,
            perks=_perks(state="cleared"),
            tail=_tail("second-terminal", count=3),
        ),
        _request("GAME_OVER", offset=5),
    )

    saves = _records(receipts, "save_observation")
    assert len(saves) == 5
    assert saves[-2]["round"]["identity_status"] == (
        "first_naturally_serialized_identity"
    )
    comparison = saves[-1]["history_tail"]["baseline_comparison"]
    assert comparison["status"] == "candidate_tail_change"
    assert comparison["baseline_fingerprint"] == _sha("tail:first-terminal")


def test_direct_retry_starts_a_new_round_from_the_prior_terminal_tail():
    receipts: list[dict] = []
    machine = _machine(receipts)
    first_identity = _identity(counter=4, seed=111)
    retry_identity = _identity(counter=5, seed=222)
    first_round_pick = _pick(1, wave=50, perk_id=0, perk_key="max_health")
    retry_first_pick = _pick(1, wave=80, perk_id=41, perk_key="game_speed")
    retry_second_pick = _pick(
        2,
        wave=120,
        perk_id=10,
        perk_key="perk_wave_requirement",
    )
    first_terminal = _observation(
        3,
        active=False,
        wave=0,
        perks=_perks(state="cleared"),
        tail=_tail("first-terminal", count=2),
    )

    assert machine.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=1)),
        _request("HOME_NEW_BATTLE"),
    )
    assert machine.observe_save(
        _observation(
            2,
            active=True,
            wave=100,
            identity=first_identity,
            perks=_perks([first_round_pick]),
            tail=_tail("before", count=1),
        ),
        _request("RUNNING", offset=1),
    )
    assert machine.observe_save(first_terminal, _request("GAME_OVER", offset=2))

    # A Retry can be visible before the save advances. The duplicate terminal
    # projection must not consume the retained baseline.
    assert not machine.observe_save(
        first_terminal,
        _request("RUNNING", offset=3),
    )
    assert machine.observe_save(
        _observation(
            4,
            active=True,
            wave=90,
            identity=retry_identity,
            perks=_perks([retry_first_pick]),
            tail=_tail("first-terminal", count=2),
        ),
        _request("RUNNING", offset=3),
    )
    assert machine.observe_save(
        _observation(
            5,
            active=True,
            wave=150,
            identity=retry_identity,
            perks=_perks([retry_first_pick, retry_second_pick]),
            tail=_tail("first-terminal", count=2),
        ),
        _request(None, offset=4),
    )
    assert machine.observe_save(
        _observation(
            6,
            active=False,
            wave=0,
            perks=_perks(state="cleared"),
            tail=_tail("second-terminal", count=3),
        ),
        _request("GAME_OVER", offset=5),
    )

    saves = _records(receipts, "save_observation")
    retry_first = saves[3]
    assert retry_first["round"]["identity_status"] == (
        "first_naturally_serialized_identity"
    )
    assert retry_first["history_tail"]["baseline_comparison"] == {
        "status": "terminal_retry_baseline_carried",
        "fingerprint": _sha("tail:first-terminal"),
        "entry_count": 2,
        "capacity": 30,
        "terminal_save_revision": 3,
        "terminal_evidence_status": "candidate_tail_change",
        "transition": "passive_game_over_to_running",
    }
    assert retry_first["perks"]["progression"]["status"] == (
        "initial_complete_checkpoint"
    )
    assert retry_first["perks"]["progression"]["delta"] == [retry_first_pick]
    assert saves[4]["round"]["identity_status"] == "same_identity_newer_revision"
    assert saves[4]["perks"]["progression"]["delta"] == [retry_second_pick]
    retry_terminal = saves[5]
    comparison = retry_terminal["history_tail"]["baseline_comparison"]
    assert comparison["status"] == "candidate_tail_change"
    assert comparison["baseline_fingerprint"] == _sha("tail:first-terminal")
    assert retry_terminal["perks"]["last_complete_same_identity"] == {
        "save_revision": 5,
        "saved_wave": 150,
        "picked_count": 2,
        "fingerprint": _perks([retry_first_pick, retry_second_pick])["fingerprint"],
    }


def test_midbattle_collector_start_can_seed_the_next_direct_retry_baseline():
    receipts: list[dict] = []
    machine = _machine(receipts)

    assert machine.observe_save(
        _observation(
            1,
            active=True,
            wave=100,
            identity=_identity(counter=4, seed=111),
            tail=_tail("before", count=1),
        ),
        _request("RUNNING"),
    )
    assert machine.observe_save(
        _observation(
            2,
            active=False,
            wave=0,
            perks=_perks(state="cleared"),
            tail=_tail("first-terminal", count=2),
        ),
        _request("GAME_OVER", offset=1),
    )
    assert machine.observe_save(
        _observation(
            3,
            active=True,
            wave=50,
            identity=_identity(counter=5, seed=222),
            tail=_tail("first-terminal", count=2),
        ),
        _request("RUNNING", offset=2),
    )

    saves = _records(receipts, "save_observation")
    assert saves[1]["history_tail"]["baseline_comparison"]["status"] == (
        "baseline_unavailable"
    )
    retry = saves[2]
    assert retry["round"]["identity_status"] == (
        "first_naturally_serialized_identity"
    )
    assert retry["history_tail"]["baseline_comparison"] == {
        "status": "terminal_retry_baseline_carried",
        "fingerprint": _sha("tail:first-terminal"),
        "entry_count": 2,
        "capacity": 30,
        "terminal_save_revision": 2,
        "terminal_evidence_status": "baseline_unavailable",
        "transition": "passive_game_over_to_running",
    }


def test_direct_retry_after_an_unchanged_terminal_tail_stays_fail_closed():
    receipts: list[dict] = []
    machine = _machine(receipts)

    assert machine.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=1)),
        _request("HOME_NEW_BATTLE"),
    )
    assert machine.observe_save(
        _observation(
            2,
            active=True,
            wave=100,
            identity=_identity(counter=4, seed=111),
            tail=_tail("before", count=1),
        ),
        _request("RUNNING", offset=1),
    )
    assert machine.observe_save(
        _observation(
            3,
            active=False,
            wave=0,
            perks=_perks(state="cleared"),
            tail=_tail("before", count=1),
        ),
        _request("GAME_OVER", offset=2),
    )
    assert machine.observe_save(
        _observation(
            4,
            active=True,
            wave=50,
            identity=_identity(counter=5, seed=222),
            tail=_tail("before", count=1),
        ),
        _request("RUNNING", offset=3),
    )

    saves = _records(receipts, "save_observation")
    assert saves[2]["history_tail"]["baseline_comparison"]["status"] == "unchanged"
    retry = saves[3]
    assert retry["round"]["identity_status"] == ("fail_closed_identity_discontinuity")
    assert retry["history_tail"]["baseline_comparison"] == {"status": "not_evaluated"}
    assert {item["code"] for item in retry["audit_outcomes"]} == {
        "active_identity_discontinuity"
    }


def test_direct_retry_baseline_cannot_cross_an_exact_target_change():
    receipts: list[dict] = []
    machine = _machine(receipts)

    assert machine.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=1)),
        _request("HOME_NEW_BATTLE"),
    )
    assert machine.observe_save(
        _observation(
            2,
            active=True,
            wave=100,
            identity=_identity(counter=4, seed=111),
            tail=_tail("before", count=1),
        ),
        _request("RUNNING", offset=1),
    )
    assert machine.observe_save(
        _observation(
            3,
            active=False,
            wave=0,
            perks=_perks(state="cleared"),
            tail=_tail("first-terminal", count=2),
        ),
        _request("GAME_OVER", offset=2),
    )
    assert machine.observe_save(
        _observation(
            4,
            active=True,
            wave=50,
            identity=_identity(counter=5, seed=222),
            tail=_tail("first-terminal", count=2),
            target_label="localhost:5565",
        ),
        _request("RUNNING", offset=3),
    )

    retry = _records(receipts, "save_observation")[-1]
    assert retry["round"]["identity_status"] == ("fail_closed_identity_discontinuity")
    assert retry["history_tail"]["baseline_comparison"] == {"status": "not_evaluated"}


def test_identity_discontinuity_and_perk_regression_fail_closed_without_merging():
    receipts: list[dict] = []
    machine = _machine(receipts)
    identity = _identity(seed=111)
    other_identity = _identity(seed=222)
    first = _pick(1, wave=50, perk_id=0, perk_key="max_health")
    second = _pick(2, wave=100, perk_id=10, perk_key="perk_wave_requirement")
    changed_second = _pick(2, wave=100, perk_id=41, perk_key="game_speed")

    machine.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=4)),
        _request("HOME_NEW_BATTLE"),
    )
    machine.observe_save(
        _observation(
            2,
            active=True,
            wave=120,
            identity=identity,
            perks=_perks([first, second]),
            tail=_tail("before", count=4),
        ),
        _request("RUNNING", offset=1),
    )
    machine.observe_save(
        _observation(
            3,
            active=True,
            wave=130,
            identity=other_identity,
            perks=_perks([first, second]),
            tail=_tail("before", count=4),
        ),
        _request(None, offset=2),
    )
    machine.observe_save(
        _observation(
            4,
            active=True,
            wave=140,
            identity=identity,
            perks=_perks([first, changed_second]),
            tail=_tail("before", count=4),
        ),
        _request(None, offset=3),
    )
    machine.observe_save(
        _observation(
            5,
            active=False,
            wave=0,
            perks=_perks(state="cleared"),
            tail=_tail("after", count=5),
        ),
        _request("GAME_OVER", offset=4),
    )

    saves = _records(receipts, "save_observation")
    discontinuity = saves[2]
    assert discontinuity["round"]["identity_status"] == (
        "fail_closed_identity_discontinuity"
    )
    assert discontinuity["perks"]["progression"]["delta"] == []
    regression = saves[3]
    assert regression["perks"]["progression"]["status"] == ("fail_closed_non_prefix")
    assert regression["perks"]["progression"]["delta"] == []
    assert {item["code"] for item in regression["audit_outcomes"]} == {
        "perk_progression_non_prefix"
    }
    terminal = saves[4]
    assert terminal["history_tail"]["baseline_comparison"]["status"] == (
        "identity_continuity_failed"
    )
    assert "candidate_tail_change" not in json.dumps(terminal)


def test_capacity_30_rollover_is_only_a_structural_candidate():
    receipts: list[dict] = []
    machine = _machine(receipts)
    identity = _identity()
    machine.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=30)),
        _request("HOME_NEW_BATTLE"),
    )
    machine.observe_save(
        _observation(
            2,
            active=True,
            wave=10,
            identity=identity,
            tail=_tail("before", count=30),
        ),
        _request("RUNNING", offset=1),
    )
    machine.observe_save(
        _observation(
            3,
            active=False,
            wave=0,
            tail=_tail("rolled", count=30),
            perks=_perks(state="cleared"),
        ),
        _request("GAME_OVER", offset=2),
    )

    candidate = _records(receipts, "save_observation")[-1]["history_tail"][
        "baseline_comparison"
    ]
    assert candidate["status"] == "candidate_tail_change"
    assert candidate["capacity_rollover"] is True
    assert candidate["baseline_entry_count"] == 30
    assert candidate["observed_entry_count"] == 30
    assert candidate["candidate_only"] is True


def test_unknown_killed_by_keeps_structural_candidate_and_semantic_unavailable():
    receipts: list[dict] = []
    machine = _machine(receipts)
    identity = _identity()
    machine.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=1)),
        _request("HOME_NEW_BATTLE"),
    )
    machine.observe_save(
        _observation(
            2,
            active=True,
            wave=10,
            identity=identity,
            tail=_tail("before", count=1),
        ),
        _request("RUNNING", offset=1),
    )
    machine.observe_save(
        _observation(
            3,
            active=False,
            wave=0,
            perks=_perks(state="cleared"),
            tail=_tail(
                "unknown",
                count=2,
                killed_by_id=77,
                semantic_status="unavailable",
            ),
        ),
        _request("GAME_OVER", offset=2),
    )

    terminal = _records(receipts, "save_observation")[-1]
    assert terminal["history_tail"]["newest_identity"]["killed_by_id"] == 77
    semantic = terminal["history_tail"]["semantic_completed_entry"]
    assert semantic == {
        "status": "unavailable",
        "reason_code": "unmapped_killed_by_id",
        "fingerprint": None,
    }
    candidate = terminal["history_tail"]["baseline_comparison"]
    assert candidate["status"] == "candidate_tail_change"
    assert candidate["semantic_completed_entry_status"] == "unavailable"


def test_unavailable_history_component_does_not_erase_valid_core_evidence():
    receipts: list[dict] = []
    machine = _machine(receipts)
    unavailable_tail = {
        "structural_status": "unavailable",
        "structural_reason_code": "battle_history_exceeds_capacity",
        "entry_count": 31,
        "capacity": 30,
        "fingerprint": None,
        "newest_identity": None,
        "semantic_completed_entry": {
            "status": "unavailable",
            "reason_code": "structural_tail_unavailable",
            "fingerprint": None,
        },
    }

    assert machine.observe_save(
        _observation(
            1,
            active=True,
            wave=25,
            identity=_identity(),
            perks=_perks(),
            tail=unavailable_tail,
        ),
        _request("RUNNING"),
    )

    save = _records(receipts, "save_observation")[-1]
    assert save["round"]["identity_status"] == ("first_naturally_serialized_identity")
    assert save["perks"]["progression"]["status"] == ("initial_complete_checkpoint")
    assert save["history_tail"]["structural_status"] == "unavailable"
    assert {outcome["code"] for outcome in save["audit_outcomes"]} == {
        "history_tail_component_unavailable",
        "semantic_completed_entry_unavailable",
        "pre_round_baseline_unavailable",
    }


def test_process_restart_cannot_inherit_an_earlier_audit_candidate():
    receipts: list[dict] = []
    first = _machine(receipts, runtime="runtime-old", collector="collector-old")
    identity = _identity()
    first.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=2)),
        _request("HOME_NEW_BATTLE"),
    )
    first.observe_save(
        _observation(
            2,
            active=True,
            wave=50,
            identity=identity,
            tail=_tail("before", count=2),
        ),
        _request("RUNNING", offset=1),
    )
    first.observe_save(
        _observation(
            3,
            active=False,
            wave=0,
            perks=_perks(state="cleared"),
            tail=_tail("after", count=3),
        ),
        _request("GAME_OVER", offset=2),
    )
    assert (
        _records(receipts, "save_observation")[-1]["history_tail"][
            "baseline_comparison"
        ]["status"]
        == "candidate_tail_change"
    )

    restarted = _machine(
        receipts,
        runtime="runtime-new",
        collector="collector-new",
    )
    restarted.observe_save(
        _observation(
            4,
            active=False,
            wave=0,
            perks=_perks(state="cleared"),
            tail=_tail("after", count=3),
        ),
        _request("GAME_OVER", offset=3),
    )

    terminal = _records(receipts, "save_observation")[-1]
    assert terminal["runtime_session_id"] == "runtime-new"
    assert terminal["collector_session_id"] == "collector-new"
    assert terminal["history_tail"]["baseline_comparison"]["status"] == (
        "session_round_unavailable"
    )
    assert terminal["perks"]["progression"]["status"] == ("terminal_revision_rejected")


def test_visual_events_keep_only_whitelisted_approximate_metadata():
    receipts: list[dict] = []
    machine = _machine(receipts)
    event = {
        "ability": "second_wind",
        "sequence": 2,
        "approximate_wave": 4015,
        "exact_activation_wave": 4012,
        "wave_confidence": 97.5,
        "wave_observed_at": "2026-08-02T13:00:00-07:00",
        "detected_at": "2026-08-02T13:00:01-07:00",
        "confirmed_at": "2026-08-02T13:00:02-07:00",
        "detection_source": "active_status_icon",
        "presence_confidence": 0.91,
        "absence_confidence": 0.11,
        "active_icon_confidence": 0.98,
        "confirmation_frames": 2,
        "estimated_rearm_wave": 4415,
        "evidence_image": (
            "screenshots/matches/"
            "SurvivalActivation20260802T130001-0700_second_wind.png"
        ),
        "ocr_text": "secret text",
        "pixels": [[1, 2, 3]],
        "decoded_root": {"playerID": "private"},
    }

    assert machine.record_visual_events([event]) == 1

    receipt = _records(receipts, "visual_event")[0]
    visual = receipt["visual_event"]
    assert visual["wave"] == {
        "approximate_visual_observation": 4015,
        "confidence_percent": 97.5,
        "observed_at": "2026-08-02T20:00:00.000+00:00",
        "semantics": "approximate_not_exact_activation_wave",
    }
    assert visual["evidence_image_reference"].startswith("screenshots/matches/")
    rendered = json.dumps(receipt)
    assert "exact_activation_wave" not in visual
    assert "estimated_rearm_wave" not in rendered
    assert "secret text" not in rendered
    assert "playerID" not in rendered


def test_visual_boundary_is_before_action_consumers_and_is_pause_independent():
    source = inspect.getsource(App.run)
    observer = source.index("self._observe_player_save_audit_screen(detection)")
    mission_consumer = source.index("self._mission_mgr.observe_detection(detection)")
    dispatch_gate = source.index("battle_started = self._mission_mgr.maybe_run_start")

    assert observer < mission_consumer < dispatch_gate


def test_manifest_disabled_survival_evidence_is_rejected_without_erasing_core():
    receipts: list[dict] = []
    machine = _machine(receipts)

    assert not machine.record_normalized_component(
        "survival_checkpoints",
        {
            "raw_timer": 12,
            "exact_activation_wave": 100,
            "account_id": "private",
        },
    )
    assert machine.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=1)),
        _request("HOME_NEW_BATTLE"),
    )

    outcome = _records(receipts, "audit_outcome")[-1]
    assert outcome["outcome"] == {
        "code": "survival_checkpoint_manifest_disabled",
        "component": "survival_checkpoints",
        "disposition": "fail_closed_observation_only",
    }
    assert len(_records(receipts, "save_observation")) == 1
    rendered = json.dumps(receipts)
    assert "raw_timer" not in rendered
    assert "exact_activation_wave" not in rendered
    assert "private" not in rendered


def test_future_optional_component_requires_explicit_manifest_allowlist(tmp_path):
    manifest_payload = json.loads(
        DEFAULT_PLAYER_SAVE_AUDIT_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    survival = manifest_payload["components"]["survival_checkpoints"]
    survival.update(
        {
            "enabled": True,
            "schema_version": 1,
            "fields": {
                "checkpoint_status": {
                    "type": "enum",
                    "values": ["observed"],
                },
                "checkpoint_fingerprint": {"type": "sha256"},
            },
            "unavailable_reason": "not_applicable",
        }
    )
    manifest_path = tmp_path / "future-manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    receipts: list[dict] = []
    machine = PlayerSaveAuditStateMachine(
        load_player_save_audit_manifest(manifest_path),
        receipt_sink=receipts.append,
        interval_seconds=300,
        runtime_session_id="runtime-future-gate",
        collector_session_id="collector-future-gate",
        now_fn=lambda: START,
    )

    assert machine.record_normalized_component(
        "survival_checkpoints",
        {
            "checkpoint_status": "observed",
            "checkpoint_fingerprint": _sha("future-normalized-component"),
        },
    )

    component = _records(receipts, "normalized_component")[0]["component"]
    assert component["name"] == "survival_checkpoints"
    assert component["evidence"] == {
        "checkpoint_status": "observed",
        "checkpoint_fingerprint": _sha("future-normalized-component"),
    }


def test_recursive_privacy_allowlist_excludes_private_and_unbounded_evidence():
    receipts: list[dict] = []
    machine = _machine(receipts)
    identity = {
        **_identity(),
        "playerID": "must-not-leak",
        "profile_summary": {"currency": 999},
        "decoded_root": {"userName": "operator"},
    }
    perks = {
        **_perks([_pick(1, wave=10, perk_id=0, perk_key="max_health")]),
        "perkLevel": [1] * 50,
        "raw_save": b"private-bytes",
    }
    history = _tail(
        "private",
        count=1,
        arbitrary={
            "account_identifier": "private-account",
            "profile_checks": {"cards": "Farm"},
            "arbitrary_history": {"coins": 999},
            "more_stats_rows": [1] * 144,
            "ocr_text": "private OCR",
            "pixels": [[0]],
        },
    )
    observation = _observation(
        1,
        active=True,
        wave=10,
        identity=identity,
        perks=perks,
        tail=history,
    )

    assert machine.observe_save(observation, _request("RUNNING"))

    rendered = json.dumps(receipts)
    for secret in (
        "must-not-leak",
        "operator",
        "private-bytes",
        "private-account",
        "private OCR",
        "perkLevel",
        "more_stats_rows",
        "arbitrary_history",
    ):
        assert secret not in rendered

    def inspect(value, key=None):
        if key is not None:
            lowered = key.lower()
            assert not any(part in lowered for part in FORBIDDEN_KEY_PARTS)
        if isinstance(value, dict):
            for child_key, child in value.items():
                inspect(child, child_key)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(receipts)


def test_slow_failed_acquisition_does_not_block_or_change_app_dispatch(tmp_path):
    started = threading.Event()
    release = threading.Event()
    receipt = tmp_path / "receipts.jsonl"

    def slow_pull(**_kwargs):
        started.set()
        assert release.wait(2.0)
        raise PlayerSavePullError("private transport detail")

    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=1,
            owned=True,
        ),
        receipt_path=receipt,
        pull_fn=slow_pull,
    )
    app = App.__new__(App)
    app._player_save_audit_collector = collector
    dispatch = Mock()

    before = time.monotonic()
    result = app._observe_player_save_audit_screen(
        {"state": "GAME_OVER", "home_battle_control": "UNKNOWN"}
    )
    elapsed = time.monotonic() - before
    dispatch()

    assert result is None
    assert elapsed < 0.1
    dispatch.assert_called_once_with()
    assert started.wait(1.0)
    release.set()
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)
    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    failures = [
        record for record in records if record["record_type"] == "audit_outcome"
    ]
    assert failures[-1]["outcome"]["code"] == "stable_read_unavailable"
    assert "private transport detail" not in json.dumps(records)


def test_exact_target_result_is_discarded_after_handoff(tmp_path, monkeypatch):
    holder = {"generation": 1}
    receipt = tmp_path / "receipts.jsonl"

    def target_snapshot():
        return SimpleNamespace(
            target="localhost:5555" if holder["generation"] == 1 else "localhost:5565",
            generation=holder["generation"],
            owned=True,
        )

    def decode(_payload, **_kwargs):
        holder["generation"] = 2
        return SimpleNamespace(
            runtime_save=object(),
            mapping_supported=True,
            shape_valid=True,
            game_version=1073,
        )

    safe_observation = _observation(
        1,
        active=False,
        wave=0,
        tail=_tail("before", count=1),
    )
    monkeypatch.setattr(
        "core.player_save_audit._audit_observation_from_runtime",
        lambda *_args, **_kwargs: safe_observation,
    )
    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=target_snapshot,
        receipt_path=receipt,
        pull_fn=lambda **_kwargs: b"stable",
        decode_fn=decode,
    )

    collector.observe_screen(
        {"state": "HOME_SCREEN", "home_battle_control": "NEW_BATTLE"}
    )
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)

    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    outcomes = [
        record for record in records if record["record_type"] == "audit_outcome"
    ]
    assert outcomes[-1]["outcome"]["code"] == "target_handoff_discarded"
    assert not any(record["record_type"] == "save_observation" for record in records)
    assert "localhost:5555" not in json.dumps(records)
    assert "localhost:5565" not in json.dumps(records)


def test_single_worker_never_has_more_than_one_poll_in_flight(tmp_path):
    lock = threading.Lock()
    active = 0
    maximum = 0
    calls = 0

    def pull(**_kwargs):
        nonlocal active, maximum, calls
        with lock:
            active += 1
            maximum = max(maximum, active)
            calls += 1
        time.sleep(0.05)
        with lock:
            active -= 1
        raise PlayerSavePullError("unavailable")

    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=1,
            owned=True,
        ),
        receipt_path=tmp_path / "receipts.jsonl",
        pull_fn=pull,
    )
    collector.request_observation("test_request_one")
    collector.request_observation("test_request_two")
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)

    assert calls == 2
    assert maximum == 1


def test_pause_does_not_block_passive_boundary_forwarding():
    app = App.__new__(App)
    collector = Mock()
    app._player_save_audit_collector = collector
    app._supervisor = SimpleNamespace(is_paused=True)
    detection = {"state": "RUNNING"}

    assert app._observe_player_save_audit_screen(detection) is None

    collector.observe_screen.assert_called_once_with(detection)


def test_app_forwards_new_perk_mapping_batches_without_a_dispatch_result():
    app = App.__new__(App)
    collector = Mock(enabled=True)
    observer = Mock()
    batches = (_mapping_batch(),)
    observer.drain_mapping_evidence.return_value = batches
    app._player_save_audit_collector = collector
    app._perk_timeline_observer = observer

    assert app._observe_player_save_audit_perk_mapping_evidence() is None

    observer.drain_mapping_evidence.assert_called_once_with()
    collector.observe_perk_mapping_evidence.assert_called_once_with(batches)


def test_app_does_not_drain_mapping_evidence_when_collector_is_disabled():
    app = App.__new__(App)
    collector = Mock(enabled=False)
    observer = Mock()
    app._player_save_audit_collector = collector
    app._perk_timeline_observer = observer

    assert app._observe_player_save_audit_perk_mapping_evidence() is None

    observer.drain_mapping_evidence.assert_not_called()
    collector.observe_perk_mapping_evidence.assert_not_called()


def test_app_forwards_mapping_window_reset_without_affecting_dispatch():
    app = App.__new__(App)
    collector = Mock(enabled=True)
    app._player_save_audit_collector = collector

    assert app._reset_player_save_audit_perk_mapping_evidence() is None

    collector.reset_perk_mapping_evidence.assert_called_once_with()


def test_perk_timeline_syncs_save_before_passive_observation_without_ui_route():
    source = inspect.getsource(App.run)
    sync = source.index("self._sync_perk_timeline_save_checkpoint()")
    observe = source.index("self._perk_timeline().observe_passive")
    forward = source.index(
        "self._observe_player_save_audit_perk_mapping_evidence()"
    )
    visual_observer = source.index("activation_tracker = self._activation_tracker()")

    assert sync < observe < forward < visual_observer
    assert "perk_timeline_handled" not in source


def test_receipt_write_or_decoder_failure_cannot_escape_into_normal_runtime(
    tmp_path,
):
    receipts = Mock(side_effect=OSError("private filesystem detail"))
    machine = PlayerSaveAuditStateMachine(
        load_player_save_audit_manifest(),
        receipt_sink=receipts,
        interval_seconds=300,
        runtime_session_id="runtime-write-failure",
        collector_session_id="collector-write-failure",
        now_fn=lambda: START,
    )
    machine.start_session()
    assert machine.observe_save(
        _observation(1, active=False, wave=0, tail=_tail("before", count=1)),
        _request("HOME_NEW_BATTLE"),
    )

    receipt_path = tmp_path / "decode-failure.jsonl"
    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=1,
            owned=True,
        ),
        receipt_path=receipt_path,
        pull_fn=lambda **_kwargs: b"stable",
        decode_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PlayerSaveDecodeError("private decoder detail")
        ),
    )
    collector.observe_screen({"state": "RUNNING"})
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)

    records = [json.loads(line) for line in receipt_path.read_text().splitlines()]
    assert any(
        record.get("outcome", {}).get("code") == "decoder_unavailable"
        for record in records
    )
    assert "private decoder detail" not in json.dumps(records)


def test_collector_worker_start_failure_disables_it_without_runtime_failure(
    tmp_path,
    monkeypatch,
):
    receipt = tmp_path / "receipts.jsonl"
    targets = Mock()
    monkeypatch.setattr("core.player_save_audit.log", Mock())

    def fail_start(_thread):
        raise RuntimeError("private thread detail")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=targets,
        receipt_path=receipt,
    )

    assert collector.enabled is False
    collector.observe_screen({"state": "RUNNING"})
    collector.close(wait=True)
    targets.assert_not_called()
    assert not receipt.exists()


def test_tournament_terminal_boundary_requests_an_immediate_observation(tmp_path):
    started = threading.Event()

    def pull(**_kwargs):
        started.set()
        raise PlayerSavePullError("unavailable")

    collector = PlayerSaveAuditCollector(
        enabled=True,
        interval_seconds=300,
        target_snapshot_fn=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=1,
            owned=True,
        ),
        receipt_path=tmp_path / "receipts.jsonl",
        pull_fn=pull,
    )
    collector.observe_screen({"state": "TOURNAMENT_RESULTS"})

    assert started.wait(1.0)
    assert collector.wait_until_idle(2.0)
    collector.close(wait=True, timeout=1.0)
    records = [
        json.loads(line)
        for line in (tmp_path / "receipts.jsonl").read_text().splitlines()
    ]
    boundary = next(
        record for record in records if record["record_type"] == "boundary_observation"
    )
    assert boundary["boundary"]["label"] == "TOURNAMENT_RESULTS"
    assert boundary["boundary"]["reason_code"] == "tournament_results"
