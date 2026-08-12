from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import stat

import pytest

import core.player_save_mapping_candidates as candidates_module
from core.player_save_mapping_candidates import (
    AppendOnlyMappingCandidateStore,
    PlayerSaveMappingCandidateError,
    build_mapping_candidate_context,
    build_mapping_candidate_record,
    mapping_candidate_review_status,
    pending_mapping_candidate,
    proposed_mapping_patch,
    reconcile_mapping_candidate_resolutions,
    resolve_mapping_candidates,
    validate_mapping_candidate_context,
    validate_mapping_candidate_record,
    validate_mapping_candidate_result,
)


ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = "2026-08-08T12:00:00+00:00"
SNAPSHOT_FINGERPRINT = "a" * 64
UI_FINGERPRINT = "b" * 64
SOURCE_OBSERVATION_FINGERPRINT = "1" * 64


def _mapping(*, resolution: str = "exact") -> dict:
    return {
        "mapping_id": "data-9-game-1073",
        "data_version": 9,
        "game_version": 1073,
        "root_class": "SaveLoad+PlayerData",
        "resolution": resolution,
        "authority_mapping_id": "data-9-game-1073",
        "structural_mapping_id": "data-9-game-1073",
        "canonical_dependency_fingerprint": "9" * 64,
    }


def _workflow() -> dict:
    return {
        "capture_request_id": "capture-1",
        "inspection_request_id": "inspection-1",
        "runtime_session_fingerprint": "c" * 64,
        "pid": 4242,
        "target_generation_fingerprint": "d" * 64,
        "activity_scope_fingerprint": "e" * 64,
        "game_state": "home_new_battle",
        "active_round_identity_fingerprint": None,
        "boundary_fingerprint": "f" * 64,
    }


def _terminal_workflow(game_state: str) -> dict:
    return {
        **_workflow(),
        "game_state": game_state,
        "active_round_identity_fingerprint": "7" * 64,
    }


def _ui(value: str, *, locator: str = "selected") -> dict:
    return {
        "canonical_values": [value],
        "locator_values": {locator: value},
        "locator_scopes": {},
        "complete": True,
        "pre_mutation": True,
        "observed_at": OBSERVED_AT,
        "source_observation_fingerprint": SOURCE_OBSERVATION_FINGERPRINT,
    }


def _pending(
    *,
    known: tuple[str, ...] = (),
    minimum_evidence_count: int = 1,
) -> dict:
    return pending_mapping_candidate(
        value_kind="perk_id",
        raw_value=99999,
        pairing_method="exact_locator",
        locator="selected",
        expected_observation_count=1,
        known_semantic_values=known,
        minimum_evidence_count=minimum_evidence_count,
    )


def _resolved(
    value: str = "future_perk",
    *,
    pending: dict | None = None,
) -> dict:
    return resolve_mapping_candidates(
        "perk_first_choice",
        [pending or _pending()],
        _ui(value),
    )[0]


def _record(
    *,
    mapping: dict | None = None,
    resolved: dict | None = None,
) -> dict:
    return build_mapping_candidate_record(
        mapping=mapping or _mapping(),
        check_id="perk_first_choice",
        candidate=resolved or _resolved(),
        snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        ui_evidence_fingerprint=UI_FINGERPRINT,
        source_observation_fingerprint=SOURCE_OBSERVATION_FINGERPRINT,
        workflow_provenance=_workflow(),
        observed_at=OBSERVED_AT,
        recorded_at="2026-08-08T12:00:01+00:00",
    )


def _compatible_module_record() -> dict:
    mapping = {
        **_mapping(resolution="compatible_exact_revision"),
        "mapping_id": "data-9-game-1101",
        "game_version": 1101,
        "authority_mapping_id": "data-9-game-1073",
        "structural_mapping_id": "data-9-game-1101",
    }
    return _record(mapping=mapping)


def _module_info_record(
    *,
    raw_value: int,
    semantic: str,
    known_raw_semantic_value: str | None = None,
) -> dict:
    slots = {
        "cannon_primary": "Amplifying Strike",
        "armor_primary": "Orbital Augment",
        "generator_primary": "Project Funding",
        "core_primary": semantic,
        "cannon_assist": "Being Annihilator",
        "armor_assist": "Space Displacer",
        "generator_assist": "Singularity Harness",
        "core_assist": "Harmony Conductor",
    }
    peers = {
        locator: value
        for locator, value in slots.items()
        if locator != "core_primary"
    }
    pending = pending_mapping_candidate(
        value_kind="module_info_index",
        raw_value=raw_value,
        pairing_method="exact_locator",
        locator="core_primary",
        expected_observation_count=8,
        known_semantic_values=(),
        known_raw_semantic_value=known_raw_semantic_value,
        peer_locator_values=peers,
        scope={
            "slot_key": "core_primary",
            "family": "core",
            "role": "primary",
        },
    )
    ui = {
        "canonical_values": list(slots.values()),
        "locator_values": slots,
        "locator_scopes": {
            locator: {
                "slot_key": locator,
                "family": locator.rsplit("_", 1)[0],
                "role": locator.rsplit("_", 1)[1],
            }
            for locator in slots
        },
        "complete": True,
        "pre_mutation": True,
        "observed_at": OBSERVED_AT,
        "source_observation_fingerprint": SOURCE_OBSERVATION_FINGERPRINT,
    }
    resolved = resolve_mapping_candidates("modules", [pending], ui)[0]
    return build_mapping_candidate_record(
        mapping=_mapping(),
        check_id="modules",
        candidate=resolved,
        snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        ui_evidence_fingerprint=UI_FINGERPRINT,
        source_observation_fingerprint=SOURCE_OBSERVATION_FINGERPRINT,
        workflow_provenance=_workflow(),
        observed_at=OBSERVED_AT,
        recorded_at="2026-08-08T12:00:01+00:00",
    )


def test_candidate_resolution_distinguishes_all_review_dispositions():
    ready = _resolved()
    conflict = _resolved(
        "already_owned",
        pending=_pending(known=("already_owned",)),
    )
    supporting = _resolved(
        pending=_pending(minimum_evidence_count=2),
    )
    ambiguous = resolve_mapping_candidates(
        "perk_first_choice",
        [_pending()],
        {
            **_ui("future_perk"),
            "locator_values": {},
        },
    )[0]

    assert (ready["status"], ready["evidence_strength"]) == (
        "ready_for_review",
        "deterministic",
    )
    assert (conflict["status"], conflict["evidence_strength"]) == (
        "conflicts_existing_mapping",
        "conflicting",
    )
    assert (supporting["status"], supporting["evidence_strength"]) == (
        "needs_more_evidence",
        "supporting",
    )
    assert (ambiguous["status"], ambiguous["evidence_strength"]) == (
        "ambiguous",
        "insufficient",
    )
    assert ambiguous["semantic_value"] is None


def test_context_is_exact_shape_and_rejects_raw_save_contents():
    pending = _pending()
    context = build_mapping_candidate_context(
        mapping_id="data-9-game-1073",
        data_version=9,
        game_version=1073,
        mapping_resolution="exact",
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
        snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        candidates={"perk_first_choice": [pending]},
    )

    assert validate_mapping_candidate_context(context) == context
    assert context["candidates"]["perk_first_choice"][0][
        "raw_discriminator"
    ] == {"kind": "integer_id", "value": 99999}

    changed = deepcopy(context)
    changed["candidates"]["perk_first_choice"][0]["raw_save"] = {
        "playerInfo": "forbidden"
    }
    assert validate_mapping_candidate_context(changed) is None
    with pytest.raises(PlayerSaveMappingCandidateError):
        pending_mapping_candidate(
            value_kind="perk_id",
            raw_value={"raw": "save"},
            pairing_method="exact_locator",
            locator="selected",
            expected_observation_count=1,
        )


def test_terminal_killed_by_candidate_has_a_canonical_patch_owner():
    pending = pending_mapping_candidate(
        value_kind="battle_history_killed_by_id",
        raw_value=42,
        pairing_method="exact_locator",
        locator="killed_by",
        expected_observation_count=1,
        known_semantic_values=("Boss",),
    )
    resolved = resolve_mapping_candidates(
        "battle_history_killed_by",
        [pending],
        _ui("Future Enemy", locator="killed_by"),
    )[0]
    record = build_mapping_candidate_record(
        mapping=_mapping(),
        check_id="battle_history_killed_by",
        candidate=resolved,
        snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        ui_evidence_fingerprint=UI_FINGERPRINT,
        source_observation_fingerprint=SOURCE_OBSERVATION_FINGERPRINT,
        workflow_provenance=_terminal_workflow("terminal_game_over"),
        observed_at=OBSERVED_AT,
        recorded_at="2026-08-08T12:00:01+00:00",
    )

    proposal = proposed_mapping_patch(record, repository_root=ROOT)

    assert proposal["operations"] == [
        {
            "op": "add",
            "path": "/runtime_save/battle_history/killed_by_ids/42",
            "value": "Future Enemy",
        }
    ]


def test_tournament_league_candidate_is_durable_review_only():
    pending = pending_mapping_candidate(
        value_kind="tournament_league_id",
        raw_value=4,
        pairing_method="exact_locator",
        locator="league",
        expected_observation_count=1,
        known_semantic_values=("Legend League",),
    )
    resolved = resolve_mapping_candidates(
        "tournament_league",
        [pending],
        _ui("Champion League", locator="league"),
    )[0]
    record = build_mapping_candidate_record(
        mapping=_mapping(),
        check_id="tournament_league",
        candidate=resolved,
        snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        ui_evidence_fingerprint=UI_FINGERPRINT,
        source_observation_fingerprint=SOURCE_OBSERVATION_FINGERPRINT,
        workflow_provenance=_terminal_workflow(
            "terminal_tournament_results"
        ),
        observed_at=OBSERVED_AT,
        recorded_at="2026-08-08T12:00:01+00:00",
    )

    assert validate_mapping_candidate_record(record) == record
    with pytest.raises(
        PlayerSaveMappingCandidateError,
        match="no_authoritative_patch_owner",
    ):
        proposed_mapping_patch(record, repository_root=ROOT)


def test_terminal_candidate_requires_completed_tail_identity():
    pending = pending_mapping_candidate(
        value_kind="battle_history_killed_by_id",
        raw_value=42,
        pairing_method="exact_locator",
        locator="killed_by",
        expected_observation_count=1,
    )
    resolved = resolve_mapping_candidates(
        "battle_history_killed_by",
        [pending],
        _ui("Vampire", locator="killed_by"),
    )[0]
    workflow = _terminal_workflow("terminal_game_over")
    workflow["active_round_identity_fingerprint"] = None

    with pytest.raises(
        PlayerSaveMappingCandidateError,
        match="active_round_identity_fingerprint",
    ):
        build_mapping_candidate_record(
            mapping=_mapping(),
            check_id="battle_history_killed_by",
            candidate=resolved,
            snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            ui_evidence_fingerprint=UI_FINGERPRINT,
            source_observation_fingerprint=SOURCE_OBSERVATION_FINGERPRINT,
            workflow_provenance=workflow,
            observed_at=OBSERVED_AT,
        )


def test_check_kind_mismatch_is_rejected_before_pairing():
    wrong_kind = pending_mapping_candidate(
        value_kind="target_priority_id",
        raw_value=77,
        pairing_method="exact_locator",
        locator="selected",
        expected_observation_count=1,
    )

    with pytest.raises(
        PlayerSaveMappingCandidateError,
        match="check_value_kind_mismatch",
    ):
        resolve_mapping_candidates(
            "perk_first_choice",
            [wrong_kind],
            _ui("future_perk"),
        )


def test_record_identity_is_recomputed_and_authority_is_candidate_only():
    record = _record()

    assert validate_mapping_candidate_record(record) == record
    assert record["evidence"]["observed_at"] == OBSERVED_AT
    assert record["evidence"]["source_observation_fingerprint"] == (
        SOURCE_OBSERVATION_FINGERPRINT
    )
    assert record["mapping"]["structural_mapping_id"] == (
        "data-9-game-1073"
    )
    assert record["authority"] == {
        "disposition": "candidate_only",
        "mapping_promotion": "explicit_reviewed_repository_change",
        "runtime_reads_receipt": False,
        "authorizes_ui_suppression": False,
        "authorizes_input": False,
        "authorizes_repair": False,
        "changes_configuration": False,
        "changes_strategy": False,
        "self_promotes": False,
    }

    changed = deepcopy(record)
    changed["candidate"]["semantic_value"] = "different_perk"
    changed["candidate"]["observed_semantic_values"] = ["different_perk"]
    with pytest.raises(
        PlayerSaveMappingCandidateError,
        match="record_id_mismatch",
    ):
        validate_mapping_candidate_record(changed)


@pytest.mark.parametrize("invalid_recorded_at", ["", 0, False])
def test_explicit_invalid_recorded_at_is_not_replaced_with_current_time(
    invalid_recorded_at,
):
    with pytest.raises(
        PlayerSaveMappingCandidateError,
        match="recorded_at",
    ):
        build_mapping_candidate_record(
            mapping=_mapping(),
            check_id="perk_first_choice",
            candidate=_resolved(),
            snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            ui_evidence_fingerprint=UI_FINGERPRINT,
            source_observation_fingerprint=SOURCE_OBSERVATION_FINGERPRINT,
            workflow_provenance=_workflow(),
            observed_at=OBSERVED_AT,
            recorded_at=invalid_recorded_at,
        )


def test_compatible_mapping_can_only_retain_supporting_evidence():
    record = _record(mapping=_mapping(resolution="compatible"))

    assert record["candidate"]["status"] == "needs_more_evidence"
    assert record["evidence"]["evidence_strength"] == "supporting"
    with pytest.raises(
        PlayerSaveMappingCandidateError,
        match="requires_exact_mapping",
    ):
        proposed_mapping_patch(record, repository_root=ROOT)


def test_append_once_is_private_idempotent_and_preserves_complete_records(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "private" / "receipts.jsonl"
    store = AppendOnlyMappingCandidateStore(path)
    record = _record()

    assert store.append_once(record) is True
    assert store.append_once(record) is False
    assert store.list_records() == [record]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    original = path.read_bytes()
    second = _record(resolved=_resolved("another_future_perk"))
    real_write = candidates_module.os.write

    def partial_write(descriptor, payload):
        return real_write(descriptor, payload[: max(1, len(payload) // 2)])

    monkeypatch.setattr(candidates_module.os, "write", partial_write)
    with pytest.raises(OSError, match="partial"):
        store.append_once(second)
    assert path.read_bytes() == original


def test_partial_receipt_tail_is_recovered_without_losing_complete_records(
    tmp_path,
):
    path = tmp_path / "receipts.jsonl"
    store = AppendOnlyMappingCandidateStore(path)
    record = _record()
    store.append_once(record)
    path.write_bytes(path.read_bytes() + b'{"partial":true}')

    assert store.list_records() == [record]
    assert path.read_bytes().endswith(b"\n")


def test_review_proposal_is_non_mutating_and_requires_repository_validation():
    target = ROOT / "config/player_save_versions/data_9_game_1073.json"
    before = target.read_bytes()
    record = _record()

    proposal = proposed_mapping_patch(record, repository_root=ROOT)

    assert proposal["applies_changes"] is False
    assert proposal["promotes_mapping"] is False
    assert proposal["review_required"] is True
    assert proposal["target"]["expected_sha256"] == hashlib.sha256(
        before
    ).hexdigest()
    assert proposal["operations"] == [
        {
            "op": "add",
            "path": "/perk_ids/99999",
            "value": "future_perk",
        }
    ]
    assert target.read_bytes() == before


def test_compatible_exact_revision_proposal_updates_owner_and_mirror_atomically():
    proposal = proposed_mapping_patch(
        _compatible_module_record(),
        repository_root=ROOT,
    )

    assert proposal["schema_version"] == 2
    assert proposal["capability"] == (
        "player_save_mapping_candidate_review_v2"
    )
    assert proposal["atomic_group"] is True
    assert [target["mapping_id"] for target in proposal["targets"]] == [
        "data-9-game-1073",
        "data-9-game-1101",
    ]
    assert all(target["state"] == "pending" for target in proposal["targets"])
    assert all(target["operations"] for target in proposal["targets"])


def test_compatible_proposal_only_emits_the_missing_mirror_operation(tmp_path):
    mapping_dir = tmp_path / "config" / "player_save_versions"
    mapping_dir.mkdir(parents=True)
    for name in ("data_9_game_1073.json", "data_9_game_1101.json"):
        shutil.copyfile(
            ROOT / "config/player_save_versions" / name,
            mapping_dir / name,
        )
    authority_path = mapping_dir / "data_9_game_1073.json"
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["perk_ids"]["99999"] = "future_perk"
    authority_path.write_text(json.dumps(authority), encoding="utf-8")

    proposal = proposed_mapping_patch(
        _compatible_module_record(),
        repository_root=tmp_path,
    )

    assert proposal["targets"][0]["state"] == "integrated"
    assert proposal["targets"][0]["operations"] == []
    assert proposal["targets"][1]["state"] == "pending"
    assert proposal["targets"][1]["operations"] == [
        {
            "op": "add",
            "path": "/perk_ids/99999",
            "value": "future_perk",
        }
    ]


@pytest.mark.parametrize(
    ("authority_value", "mirror_value", "expected_state"),
    [
        (None, None, "review_required"),
        ("future_perk", None, "mirror_pending"),
        (None, "future_perk", "authority_pending"),
        ("future_perk", "future_perk", "integrated"),
        ("different_perk", None, "canonical_conflict"),
    ],
)
def test_compatible_review_status_tracks_both_canonical_targets(
    tmp_path,
    authority_value,
    mirror_value,
    expected_state,
):
    mapping_dir = tmp_path / "config" / "player_save_versions"
    mapping_dir.mkdir(parents=True)
    values = {
        "data_9_game_1073.json": authority_value,
        "data_9_game_1101.json": mirror_value,
    }
    for name, value in values.items():
        mapping = json.loads(
            (ROOT / "config/player_save_versions" / name).read_text(
                encoding="utf-8"
            )
        )
        if value is not None:
            mapping["perk_ids"]["99999"] = value
        (mapping_dir / name).write_text(json.dumps(mapping), encoding="utf-8")
    store = AppendOnlyMappingCandidateStore(tmp_path / "receipts.jsonl")
    store.append_once(_compatible_module_record())

    status = mapping_candidate_review_status(
        store=store,
        repository_root=tmp_path,
    )

    assert status["counts"] == {expected_state: 1}
    assert status["items"][0]["state"] == expected_state


def test_candidate_review_status_persists_until_canonical_integration(tmp_path):
    mapping_dir = tmp_path / "config" / "player_save_versions"
    mapping_dir.mkdir(parents=True)
    source = ROOT / "config/player_save_versions/data_9_game_1073.json"
    target = mapping_dir / source.name
    shutil.copyfile(source, target)
    store = AppendOnlyMappingCandidateStore(tmp_path / "receipts.jsonl")
    record = _record()
    store.append_once(record)

    pending = mapping_candidate_review_status(
        store=store,
        repository_root=tmp_path,
    )

    assert pending["counts"] == {"review_required": 1}
    assert pending["items"][0]["record_id"] == record["record_id"]

    mapping = json.loads(target.read_text(encoding="utf-8"))
    mapping["perk_ids"]["99999"] = "future_perk"
    target.write_text(json.dumps(mapping), encoding="utf-8")

    integrated = mapping_candidate_review_status(
        store=store,
        repository_root=tmp_path,
    )

    assert integrated["counts"] == {"integrated": 1}
    assert integrated["items"][0]["state"] == "integrated"


def test_candidate_receipts_and_proposals_do_not_mutate_authoritative_mappings(
    tmp_path,
):
    mapping_paths = sorted((ROOT / "config/player_save_versions").glob("*.json"))
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in mapping_paths
    }
    store = AppendOnlyMappingCandidateStore(tmp_path / "receipts.jsonl")
    record = _record()

    store.append_once(record)
    assert store.list_records() == [record]
    proposed_mapping_patch(record, repository_root=ROOT)

    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in mapping_paths
    }
    assert after == before
    assert all(not item["authority"]["runtime_reads_receipt"] for item in store.list_records())


def test_minimum_count_policy_accepts_a_complete_visible_rank_prefix():
    pending = pending_mapping_candidate(
        value_kind="perk_id",
        raw_value=99999,
        pairing_method="exact_locator",
        locator="rank:1",
        expected_observation_count=2,
        observation_count_policy="minimum",
        peer_locator_values={"rank:0": "damage"},
    )
    evidence = {
        **_ui("future_perk", locator="rank:1"),
        "canonical_values": ["damage", "future_perk", "coins"],
        "locator_values": {
            "rank:0": "damage",
            "rank:1": "future_perk",
            "rank:2": "coins",
        },
    }

    resolved = resolve_mapping_candidates(
        "perk_auto_pick_order",
        [pending],
        evidence,
    )[0]

    assert resolved["status"] == "ready_for_review"
    assert resolved["semantic_value"] == "future_perk"


def test_pairing_kind_scope_and_exact_mapping_invariants_fail_closed():
    wrong_pairing = pending_mapping_candidate(
        value_kind="perk_id",
        raw_value=99999,
        pairing_method="singleton_remainder",
        locator="selected",
        expected_observation_count=1,
    )
    with pytest.raises(PlayerSaveMappingCandidateError, match="pairing_kind"):
        build_mapping_candidate_context(
            mapping_id="data-9-game-1073",
            data_version=9,
            game_version=1073,
            mapping_resolution="exact",
            authority_mapping_id="data-9-game-1073",
            structural_mapping_id="data-9-game-1073",
            snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            candidates={"perk_first_choice": [wrong_pairing]},
        )

    with pytest.raises(PlayerSaveMappingCandidateError, match="authority_mismatch"):
        build_mapping_candidate_context(
            mapping_id="data-9-game-1073",
            data_version=9,
            game_version=1073,
            mapping_resolution="exact",
            authority_mapping_id="data-9-game-1073-old",
            structural_mapping_id="data-9-game-1073",
            snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            candidates={"perk_first_choice": [_pending()]},
        )


def test_orb_calibration_retains_duplicate_values_as_supporting_evidence():
    pending = pending_mapping_candidate(
        value_kind="orb_distance_calibration",
        raw_value=1.25,
        pairing_method="calibration_sample",
        locator="innerOrbDistance",
        expected_observation_count=3,
        minimum_evidence_count=2,
        scope={"field": "innerOrbDistance"},
    )
    evidence = {
        "canonical_values": ["60.00m", "60.00m", "70.00m"],
        "locator_values": {
            "innerOrbDistance": "60.00m",
            "savedWorkshopOrbDistance": "60.00m",
            "workshopOrbDistance": "70.00m",
        },
        "locator_scopes": {
            field: {"field": field}
            for field in (
                "innerOrbDistance",
                "savedWorkshopOrbDistance",
                "workshopOrbDistance",
            )
        },
        "complete": True,
        "pre_mutation": True,
        "observed_at": OBSERVED_AT,
        "source_observation_fingerprint": SOURCE_OBSERVATION_FINGERPRINT,
    }

    resolved = resolve_mapping_candidates("orb_distance", [pending], evidence)[0]
    record = build_mapping_candidate_record(
        mapping=_mapping(),
        check_id="orb_distance",
        candidate=resolved,
        snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        ui_evidence_fingerprint=UI_FINGERPRINT,
        source_observation_fingerprint=SOURCE_OBSERVATION_FINGERPRINT,
        workflow_provenance=_workflow(),
        observed_at=OBSERVED_AT,
        recorded_at="2026-08-08T12:00:01+00:00",
    )

    assert resolved["status"] == "needs_more_evidence"
    assert resolved["observed_semantic_values"].count("60.00m") == 2
    assert validate_mapping_candidate_record(record) == record


def test_module_assist_pairing_persists_family_scope_and_proposes_replace():
    pending = pending_mapping_candidate(
        value_kind="module_assist_type",
        raw_value=99,
        pairing_method="singleton_remainder",
        locator="assist_type",
        expected_observation_count=4,
        known_semantic_values=("cannon", "armor", "generator", "core"),
        peer_semantic_values=("armor", "generator", "core"),
        scope={"role": "assist"},
    )
    slots = {
        "cannon_primary": ("Amplifying Strike", "cannon", "primary"),
        "armor_primary": ("Orbital Augment", "armor", "primary"),
        "generator_primary": ("Project Funding", "generator", "primary"),
        "core_primary": ("Dimension Core", "core", "primary"),
        "cannon_assist": ("Being Annihilator", "cannon", "assist"),
        "armor_assist": ("Space Displacer", "armor", "assist"),
        "generator_assist": ("Singularity Harness", "generator", "assist"),
        "core_assist": ("Harmony Conductor", "core", "assist"),
    }
    evidence = {
        "canonical_values": [value[0] for value in slots.values()],
        "locator_values": {key: value[0] for key, value in slots.items()},
        "locator_scopes": {
            key: {"slot_key": key, "family": value[1], "role": value[2]}
            for key, value in slots.items()
        },
        "complete": True,
        "pre_mutation": True,
        "observed_at": OBSERVED_AT,
        "source_observation_fingerprint": SOURCE_OBSERVATION_FINGERPRINT,
    }

    resolved = resolve_mapping_candidates("modules", [pending], evidence)[0]
    record = build_mapping_candidate_record(
        mapping=_mapping(),
        check_id="modules",
        candidate=resolved,
        snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        ui_evidence_fingerprint=UI_FINGERPRINT,
        source_observation_fingerprint=SOURCE_OBSERVATION_FINGERPRINT,
        workflow_provenance=_workflow(),
        observed_at=OBSERVED_AT,
        recorded_at="2026-08-08T12:00:01+00:00",
    )
    proposal = proposed_mapping_patch(record, repository_root=ROOT)

    assert resolved["semantic_value"] == "cannon"
    assert resolved["scope"] == {
        "slot_key": "cannon_assist",
        "family": "cannon",
        "role": "assist",
    }
    assert resolved["observed_semantic_values"] == [
        "armor",
        "cannon",
        "core",
        "generator",
    ]
    assert proposal["operations"] == [
        {
            "op": "replace",
            "path": "/module_loadout/assist/0/type",
            "value": 99,
        }
    ]


def test_module_proposal_allows_an_identical_global_pair_in_a_new_scope():
    record = _module_info_record(
        raw_value=39,
        semantic="Harmony Conductor",
        known_raw_semantic_value="Harmony Conductor",
    )
    proposal = proposed_mapping_patch(
        record,
        repository_root=ROOT,
    )

    assert proposal["operations"] == [
        {
            "op": "add",
            "path": "/module_loadout/primary/3/values/-",
            "value": {
                "info_index": 39,
                "name": "Harmony Conductor",
            },
        }
    ]

    mapping = json.loads(
        (ROOT / "config/player_save_versions/data_9_game_1073.json").read_text(
            encoding="utf-8"
        )
    )
    mapping["module_loadout"]["primary"][3]["values"].append(
        {"info_index": 39, "name": "Harmony Conductor"}
    )
    validate_mapping_candidate_result(record, mapping)

    mapping["module_loadout"]["assist"][0]["values"].append(
        {"info_index": 39, "name": "Conflicting Module"}
    )
    with pytest.raises(
        PlayerSaveMappingCandidateError,
        match="proposal_result_conflict",
    ):
        validate_mapping_candidate_result(record, mapping)


@pytest.mark.parametrize(
    ("raw_value", "semantic"),
    (
        (39, "Future Module"),
        (777, "Harmony Conductor"),
    ),
)
def test_module_proposal_rejects_both_global_bijection_conflicts(
    raw_value,
    semantic,
):
    with pytest.raises(
        PlayerSaveMappingCandidateError,
        match="conflicts_current_file",
    ):
        proposed_mapping_patch(
            _module_info_record(
                raw_value=raw_value,
                semantic=semantic,
            ),
            repository_root=ROOT,
        )


def test_same_save_discriminator_with_different_ui_semantics_is_ambiguous():
    first = _resolved("future_perk")
    auto_pending = pending_mapping_candidate(
        value_kind="perk_id",
        raw_value=99999,
        pairing_method="exact_locator",
        locator="rank:0",
        expected_observation_count=1,
        observation_count_policy="minimum",
    )
    auto = resolve_mapping_candidates(
        "perk_auto_pick_order",
        [auto_pending],
        _ui("different_perk", locator="rank:0"),
    )[0]

    reconciled = reconcile_mapping_candidate_resolutions(
        [
            {"check_id": "perk_first_choice", "candidate": first},
            {"check_id": "perk_auto_pick_order", "candidate": auto},
        ]
    )

    assert {item["candidate"]["status"] for item in reconciled} == {
        "ambiguous"
    }
    assert all(
        item["candidate"]["semantic_value"] is None for item in reconciled
    )


def test_recomputed_compatible_ready_receipt_is_rejected():
    changed = deepcopy(_record())
    changed["mapping"]["resolution"] = "compatible"
    changed["record_id"] = candidates_module._candidate_record_id(
        changed["mapping"],
        changed["candidate"],
        changed["evidence"],
        changed["authority"],
    )

    with pytest.raises(PlayerSaveMappingCandidateError, match="cannot_be_ready"):
        validate_mapping_candidate_record(changed)


def test_directory_fsync_is_retried_without_duplicate_append(tmp_path, monkeypatch):
    store = AppendOnlyMappingCandidateStore(tmp_path / "receipts.jsonl")
    record = _record()
    calls = 0
    real_fsync = candidates_module._fsync_directory

    def fail_once(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("directory fsync failed")
        real_fsync(path)

    monkeypatch.setattr(candidates_module, "_fsync_directory", fail_once)
    with pytest.raises(OSError, match="directory fsync failed"):
        store.append_once(record)

    assert store.append_once(record) is False
    assert calls == 2
    assert store.list_records() == [record]


def test_complete_invalid_json_line_is_not_silently_repaired(tmp_path):
    path = tmp_path / "receipts.jsonl"
    path.write_bytes(b"not-json\n")

    with pytest.raises(PlayerSaveMappingCandidateError, match="invalid_json"):
        AppendOnlyMappingCandidateStore(path).list_records()


def test_proposal_rechecks_current_semantic_collisions(tmp_path):
    target = ROOT / "config/player_save_versions/data_9_game_1073.json"
    mapping = json.loads(target.read_text(encoding="utf-8"))
    mapping["perk_ids"]["123456"] = "future_perk"
    copied = tmp_path / "config/player_save_versions/data_9_game_1073.json"
    copied.parent.mkdir(parents=True)
    copied.write_text(json.dumps(mapping), encoding="utf-8")

    with pytest.raises(PlayerSaveMappingCandidateError, match="conflicts_current"):
        proposed_mapping_patch(_record(), repository_root=tmp_path)
