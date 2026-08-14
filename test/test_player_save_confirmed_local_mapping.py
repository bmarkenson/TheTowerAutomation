from __future__ import annotations

from copy import deepcopy
import stat

import pytest

from core.player_save import (
    _apply_confirmed_local_mappings,
    _mapping_by_id,
    _mapping_semantic_fingerprint,
    confirmed_local_mapping_status,
)
from core.player_save_confirmed_local_mapping import (
    ConfirmedLocalMappingError,
    ConfirmedLocalMappingStore,
    active_confirmations,
)
from core.player_save_mapping_candidates import (
    build_mapping_candidate_record,
    fingerprint_json,
    pending_mapping_candidate,
    resolve_mapping_candidates,
)


OBSERVED_AT = "2026-08-09T12:00:00+00:00"
MODULES = {
    "cannon_primary": "Amplifying Strike",
    "armor_primary": "Orbital Augment",
    "generator_primary": "Project Funding",
    "core_primary": "Dimension Core",
    "cannon_assist": "Being Annihilator",
    "armor_assist": "Space Displacer",
    "generator_assist": "Singularity Harness",
    "core_assist": "Future Module",
}


def _workflow() -> dict:
    return {
        "capture_request_id": "capture-1",
        "inspection_request_id": "inspection-1",
        "runtime_session_fingerprint": "1" * 64,
        "pid": 4242,
        "target_generation_fingerprint": "2" * 64,
        "activity_scope_fingerprint": "3" * 64,
        "game_state": "home_new_battle",
        "active_round_identity_fingerprint": None,
        "boundary_fingerprint": "4" * 64,
    }


def _record(
    *,
    semantic: str = "Future Module",
    raw_value: int = 777,
    resolution: str = "exact",
    dependency: str = "8" * 64,
    snapshot_fingerprint: str = "6" * 64,
    slot_key: str = "core_assist",
    known_raw_semantic_value: str | None = None,
) -> dict:
    values = {**MODULES, slot_key: semantic}
    peers = {
        locator: value
        for locator, value in values.items()
        if locator != slot_key
    }
    family, role = slot_key.rsplit("_", 1)
    pending = pending_mapping_candidate(
        value_kind="module_info_index",
        raw_value=raw_value,
        pairing_method="exact_locator",
        locator=slot_key,
        expected_observation_count=8,
        known_semantic_values=tuple(peers.values()),
        known_raw_semantic_value=known_raw_semantic_value,
        peer_locator_values=peers,
        scope={
            "slot_key": slot_key,
            "family": family,
            "role": role,
        },
    )
    ui = {
        "canonical_values": list(values.values()),
        "locator_values": values,
        "locator_scopes": {
            slot_key: {
                "slot_key": slot_key,
                "family": slot_key.rsplit("_", 1)[0],
                "role": slot_key.rsplit("_", 1)[1],
            }
            for slot_key in values
        },
        "complete": True,
        "pre_mutation": True,
        "observed_at": OBSERVED_AT,
        "source_observation_fingerprint": "5" * 64,
    }
    resolved = resolve_mapping_candidates("modules", [pending], ui)[0]
    mapping = {
        "mapping_id": "data-9-game-1073",
        "data_version": 9,
        "game_version": 1073,
        "root_class": "SaveLoad+PlayerData",
        "resolution": resolution,
        "authority_mapping_id": "data-9-game-1073",
        "structural_mapping_id": "data-9-game-1073",
        "canonical_dependency_fingerprint": dependency,
    }
    if resolution == "compatible_exact_revision":
        mapping.update(
            mapping_id="data-9-game-1101",
            game_version=1101,
            structural_mapping_id="data-9-game-1101",
        )
    return build_mapping_candidate_record(
        mapping=mapping,
        check_id="modules",
        candidate=resolved,
        snapshot_fingerprint=snapshot_fingerprint,
        ui_evidence_fingerprint="7" * 64,
        source_observation_fingerprint="5" * 64,
        workflow_provenance=_workflow(),
        observed_at=OBSERVED_AT,
        recorded_at="2026-08-09T12:00:01+00:00",
    )


def test_accept_is_atomic_private_idempotent_and_revoke_is_append_only(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    record = _record()
    first = store.accept_candidate(
        record,
        recorded_at="2026-08-09T12:00:02+00:00",
    )
    second = store.accept_candidate(
        record,
        recorded_at="2026-08-09T12:00:03+00:00",
    )

    assert first["changed"] is True
    assert second["changed"] is False
    assert first["event_id"] == second["event_id"]
    path = store.path_for(9, 1073)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    document = store.load(9, 1073)
    assert document is not None
    assert document["generation"] == 1
    assert len(active_confirmations(document)) == 1

    revoked = store.revoke(
        data_version=9,
        game_version=1073,
        target_event_id=first["event_id"],
        reason="canonical investigation",
        expected_generation=document["generation"],
        expected_document_fingerprint=fingerprint_json(document),
        recorded_at="2026-08-09T12:00:04+00:00",
    )
    assert revoked["changed"] is True
    document = store.load(9, 1073)
    assert document is not None
    assert document["generation"] == 2
    assert active_confirmations(document) == []


def test_conflicting_active_semantic_is_rejected_and_document_is_preserved(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    store.accept_candidate(_record())
    path = store.path_for(9, 1073)
    before = path.read_bytes()

    with pytest.raises(
        ConfirmedLocalMappingError,
        match="active_raw_conflict",
    ):
        store.accept_candidate(
            _record(semantic="Different Future Module"),
        )

    assert path.read_bytes() == before


def test_identical_global_module_pair_can_be_confirmed_in_another_scope(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    first = store.accept_candidate(_record())

    second = store.accept_candidate(
        _record(
            slot_key="core_primary",
            known_raw_semantic_value="Future Module",
            snapshot_fingerprint="a" * 64,
        )
    )

    assert first["changed"] is True
    assert second["changed"] is True
    active = active_confirmations(store.load(9, 1073))
    assert {
        (event["scope"]["slot_key"], event["raw_value"], event["semantic_value"])
        for event in active
    } == {
        ("core_assist", 777, "Future Module"),
        ("core_primary", 777, "Future Module"),
    }


def test_global_module_name_cannot_be_confirmed_with_a_different_raw_value(
    tmp_path,
):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    store.accept_candidate(_record())
    path = store.path_for(9, 1073)
    before = path.read_bytes()

    with pytest.raises(
        ConfirmedLocalMappingError,
        match="active_semantic_conflict",
    ):
        store.accept_candidate(
            _record(
                raw_value=778,
                slot_key="core_primary",
                known_raw_semantic_value="Future Module",
                snapshot_fingerprint="a" * 64,
            )
        )

    assert path.read_bytes() == before


def test_malformed_document_is_not_replaced(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    path = store.path_for(9, 1073)
    path.parent.mkdir(parents=True)
    path.write_text("{malformed", encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ConfirmedLocalMappingError, match="document_invalid"):
        store.accept_candidate(
            _record(),
        )

    assert path.read_bytes() == before


def test_compatible_exact_revision_is_eligible_for_local_confirmation(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    result = store.accept_candidate(
        _record(resolution="compatible_exact_revision"),
    )
    assert result["changed"] is True
    assert store.load(9, 1101)["identity"]["mapping_id"] == (
        "data-9-game-1101"
    )


def test_fresh_decode_projection_applies_gap_but_dependency_drift_blocks(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    mapping = deepcopy(_mapping_by_id("data-9-game-1073"))
    dependency = _mapping_semantic_fingerprint(
        mapping,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
    )
    accepted = store.accept_candidate(
        _record(dependency=dependency),
    )

    projected, provenance, warnings = _apply_confirmed_local_mappings(
        deepcopy(mapping),
        data_version=9,
        game_version=1073,
        root_class="SaveLoad+PlayerData",
        mapping_resolution="exact",
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
        dependency_fingerprint=dependency,
        store=store,
    )
    core_assist = next(
        slot
        for slot in projected["module_loadout"]["assist"]
        if slot["slot_key"] == "core_assist"
    )
    assert {"info_index": 777, "name": "Future Module"} in (
        core_assist["values"]
    )
    assert provenance["applied_event_ids"] == [accepted["event_id"]]
    assert provenance["blocked_checks"] == []
    assert warnings

    _projected, blocked, _warnings = _apply_confirmed_local_mappings(
        deepcopy(mapping),
        data_version=9,
        game_version=1073,
        root_class="SaveLoad+PlayerData",
        mapping_resolution="exact",
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
        dependency_fingerprint="9" * 64,
        store=store,
    )
    assert blocked["applied_event_ids"] == []
    assert blocked["blocked_checks"] == []
    assert blocked["items"][0]["state"] == "reconfirmation_required"


def test_canonical_dependency_tracks_authority_policy_but_not_unrelated_fields(
    monkeypatch,
):
    import core.player_save as module

    mapping = deepcopy(_mapping_by_id("data-9-game-1073"))
    baseline = _mapping_semantic_fingerprint(
        mapping,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
    )

    validation_changed = deepcopy(mapping)
    validation_changed["validated_checks"] = [
        *validation_changed["validated_checks"],
        "future_check",
    ]
    maturity_changed = deepcopy(mapping)
    maturity_changed["maturity"] = "validated"
    unrelated_changed = deepcopy(mapping)
    unrelated_changed["profile_progression"] = {"unrelated": True}

    assert _mapping_semantic_fingerprint(
        validation_changed,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
    ) != baseline
    assert _mapping_semantic_fingerprint(
        maturity_changed,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
    ) != baseline
    assert _mapping_semantic_fingerprint(
        unrelated_changed,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
    ) == baseline

    structural = deepcopy(mapping)
    structural["revision_compatibility"] = {
        "schema_version": 1,
        "authority_mapping_id": "data-9-game-1073",
        "validated_checks": ["modules"],
        "runtime_save": True,
        "allow_forward_game_versions": True,
    }
    monkeypatch.setattr(module, "_mapping_by_id", lambda _mapping_id: structural)
    compatible = _mapping_semantic_fingerprint(
        mapping,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1101",
    )
    structural["revision_compatibility"]["validated_checks"].append(
        "perk_first_choice"
    )
    changed_compatibility = _mapping_semantic_fingerprint(
        mapping,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1101",
    )

    assert changed_compatibility != compatible


def test_status_is_nonblocking_and_reports_pending_integration(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    mapping = deepcopy(_mapping_by_id("data-9-game-1073"))
    dependency = _mapping_semantic_fingerprint(
        mapping,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
    )
    store.accept_candidate(
        _record(dependency=dependency),
    )

    status = confirmed_local_mapping_status(store=store)

    assert status["available"] is True
    assert status["blocks_startup"] is False
    assert status["counts"] == {"active_local": 1}
    assert status["items"][0]["state"] == "active_local"


@pytest.mark.parametrize(
    "integration_state",
    [
        "integration_recovery_required",
        "integration_unconfirmed",
        "production_validation_pending",
        "promotion_pending",
        "restaging_required",
    ],
)
def test_fast_lane_lifecycle_takes_precedence_over_local_confirmation(
    tmp_path,
    integration_state,
):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    mapping = deepcopy(_mapping_by_id("data-9-game-1073"))
    dependency = _mapping_semantic_fingerprint(
        mapping,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
    )
    record = _record(dependency=dependency)
    store.accept_candidate(record)
    candidate_status = {
        "available": True,
        "reason": "",
        "items": [
            {
                "candidate_record_id": record["record_id"],
                "state": integration_state,
                "reason": "fast-lane checkpoint remains pending",
            }
        ],
        "counts": {integration_state: 1},
    }

    status = confirmed_local_mapping_status(
        store=store,
        candidate_status=candidate_status,
    )

    assert status["counts"] == {integration_state: 1}
    assert status["items"] == candidate_status["items"]


def test_status_and_runtime_agree_when_dependency_requires_reconfirmation(
    tmp_path,
):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    store.accept_candidate(_record())

    status = confirmed_local_mapping_status(store=store)

    assert status["counts"] == {"reconfirmation_required": 1}
    assert status["items"][0]["state"] == "reconfirmation_required"


def test_fresh_confirmation_supersedes_stale_dependency_atomically(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    first = store.accept_candidate(
        _record(),
    )

    second = store.accept_candidate(
        _record(
            dependency="9" * 64,
            snapshot_fingerprint="a" * 64,
        ),
        recorded_at="2026-08-09T12:01:00+00:00",
    )

    assert second["changed"] is True
    assert second["event_id"] != first["event_id"]
    document = store.load(9, 1073)
    assert document is not None
    assert document["generation"] == 3
    active = active_confirmations(document)
    assert len(active) == 1
    assert active[0]["event_id"] == second["event_id"]
    assert active[0]["dependency_fingerprint"] == "9" * 64


def test_dependency_change_rejects_reused_candidate_evidence(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    store.accept_candidate(_record())
    stale_record = _record()
    stale_record["mapping"]["canonical_dependency_fingerprint"] = "9" * 64
    stale_record["record_id"] = fingerprint_json(
        {
            "schema_version": stale_record["schema_version"],
            "schema_id": stale_record["schema_id"],
            "record_type": stale_record["record_type"],
            "mapping": stale_record["mapping"],
            "candidate": stale_record["candidate"],
            "evidence": stale_record["evidence"],
            "authority": stale_record["authority"],
        }
    )

    # The record id changes because authority is now part of the receipt, but
    # its UI/save observation fingerprints still describe the old boundary.
    with pytest.raises(
        ConfirmedLocalMappingError,
        match="reconfirmation_requires_fresh_evidence",
    ):
        store.accept_candidate(stale_record)


def test_directory_fsync_failure_restores_prior_authority(
    monkeypatch,
    tmp_path,
):
    import core.player_save_confirmed_local_mapping as module

    store = ConfirmedLocalMappingStore(tmp_path / "local")
    store.accept_candidate(_record())
    path = store.path_for(9, 1073)
    before = path.read_bytes()
    monkeypatch.setattr(
        module,
        "_fsync_directory",
        lambda _path: (_ for _ in ()).throw(OSError("directory fsync failed")),
    )

    with pytest.raises(
        ConfirmedLocalMappingError,
        match="commit_state_uncertain",
    ):
        store.accept_candidate(
            _record(
                dependency="9" * 64,
                snapshot_fingerprint="a" * 64,
            ),
        )

    assert path.read_bytes() == before
    assert active_confirmations(store.load(9, 1073))[0][
        "dependency_fingerprint"
    ] == "8" * 64


def test_read_side_filesystem_failure_is_targeted_and_nonblocking(
    monkeypatch,
    tmp_path,
):
    import core.player_save_confirmed_local_mapping as module

    store = ConfirmedLocalMappingStore(tmp_path / "local")
    mapping = deepcopy(_mapping_by_id("data-9-game-1073"))
    dependency = _mapping_semantic_fingerprint(
        mapping,
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
    )
    store.accept_candidate(
        _record(dependency=dependency),
    )
    monkeypatch.setattr(
        module,
        "_open_lock",
        lambda _path: (_ for _ in ()).throw(PermissionError("read denied")),
    )

    _projected, provenance, warnings = _apply_confirmed_local_mappings(
        mapping,
        data_version=9,
        game_version=1073,
        root_class="SaveLoad+PlayerData",
        mapping_resolution="exact",
        authority_mapping_id="data-9-game-1073",
        structural_mapping_id="data-9-game-1073",
        dependency_fingerprint=dependency,
        store=store,
    )
    status = confirmed_local_mapping_status(store=store)

    assert provenance["available"] is False
    assert provenance["blocked_checks"] == []
    assert warnings
    assert status["available"] is False
    assert status["blocks_startup"] is False


def test_list_rejects_filename_identity_mismatch(tmp_path):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    store.accept_candidate(_record())
    path = store.path_for(9, 1073)
    path.rename(store.path_for(8, 1073))

    with pytest.raises(ConfirmedLocalMappingError, match="filename_identity"):
        store.list_documents()


def test_revoke_rejects_stale_compare_and_swap_without_changing_authority(
    tmp_path,
):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    accepted = store.accept_candidate(_record())
    path = store.path_for(9, 1073)
    before = path.read_bytes()

    with pytest.raises(
        ConfirmedLocalMappingError,
        match="revoke_compare_and_swap_failed",
    ):
        store.revoke(
            data_version=9,
            game_version=1073,
            target_event_id=accepted["event_id"],
            reason="stale operator view",
            expected_generation=0,
            expected_document_fingerprint="f" * 64,
        )

    assert path.read_bytes() == before


def test_accept_reserves_capacity_to_revoke_every_active_confirmation(
    monkeypatch,
    tmp_path,
):
    import core.player_save_confirmed_local_mapping as module

    monkeypatch.setattr(module, "MAX_CONFIRMED_LOCAL_MAPPING_EVENTS", 4)
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    store.accept_candidate(_record())
    store.accept_candidate(
        _record(
            semantic="Second Future Module",
            raw_value=778,
            slot_key="cannon_assist",
            snapshot_fingerprint="a" * 64,
        )
    )

    with pytest.raises(
        ConfirmedLocalMappingError,
        match="revocation_capacity_exhausted",
    ):
        store.accept_candidate(
            _record(
                semantic="Third Future Module",
                raw_value=779,
                slot_key="armor_assist",
                snapshot_fingerprint="b" * 64,
            )
        )

    document = store.load(9, 1073)
    assert document is not None
    assert len(active_confirmations(document)) == 2
