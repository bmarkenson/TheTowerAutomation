from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
from unittest.mock import Mock, patch

import nrbf
import pytest

from core.adb_utils import read_device_file
from core.app import App
from core.control_model import validate_setup_capture_preview
from core.player_save import (
    PLAYER_SAVE_DEVICE_PATH,
    PlayerSaveError,
    PlayerSavePullError,
    _module_loadout_evidence,
    _raw_field_manifest_names,
    _raw_field_name_sha256,
    _validate_raw_field_manifest,
    _validate_revision_compatibility,
    decode_player_save_bytes,
    pull_player_save_bytes,
    reconcile_requirements,
)
from core.player_save_confirmed_local_mapping import ConfirmedLocalMappingStore
from core.player_save_mapping_candidates import (
    build_mapping_candidate_record,
    build_mapping_candidate_ui_evidence,
    fingerprint_json,
    resolve_mapping_candidates,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)
from core.player_save_setup_capture import project_forced_save_setup
from core.profile_progression import (
    ProfileProgressionError,
    diff_profile_progression,
    normalize_profile_progression,
)
from core.runtime_save import runtime_with_perk_id_overrides


CAPTURED_AT = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
VERSION_MAPPING = json.loads(
    (ROOT / "config/player_save_versions/data_9_game_1073.json").read_text(
        encoding="utf-8"
    )
)
VERSION_1101_MAPPING = json.loads(
    (ROOT / "config/player_save_versions/data_9_game_1101.json").read_text(
        encoding="utf-8"
    )
)
CLIPBOARD_REPORT_PATH = ROOT / "test/fixtures/battle_report_clipboard.txt"
PERK_ID_CALIBRATION_PATH = (
    ROOT / "test/fixtures/player_save_perk_id_calibration_v1073.json"
)


def test_default_device_path_matches_operator_adb_pull():
    assert PLAYER_SAVE_DEVICE_PATH == (
        "/sdcard/Android/data/"
        "com.TechTreeGames.TheTower/files/playerInfo.dat"
    )


def test_raw_field_manifest_is_complete_disjoint_and_canonical():
    manifest = VERSION_MAPPING["raw_field_manifest"]
    names = _raw_field_manifest_names(VERSION_MAPPING)

    _validate_raw_field_manifest(VERSION_MAPPING, source="test mapping")

    assert manifest["schema_version"] == 1
    assert manifest["audit_id"] == "V1073-RAW-001"
    assert manifest["root_class"] == "SaveLoad+PlayerData"
    assert manifest["field_count"] == len(names) == 739
    assert manifest["field_name_sha256"] == _raw_field_name_sha256(names)
    assert len(names) == len(set(names))

    progression_sources = {
        spec["source"]
        for fields in VERSION_MAPPING["profile_progression"][
            "components"
        ].values()
        for spec in fields.values()
    }
    assert progression_sources <= set(names)


def test_v1101_raw_field_manifest_is_complete_compatible_extension():
    manifest = VERSION_1101_MAPPING["raw_field_manifest"]
    old_names = set(_raw_field_manifest_names(VERSION_MAPPING))
    names = _raw_field_manifest_names(VERSION_1101_MAPPING)

    _validate_raw_field_manifest(VERSION_1101_MAPPING, source="v1101 test mapping")

    assert VERSION_1101_MAPPING["mapping_id"] == "data-9-game-1101"
    assert VERSION_1101_MAPPING["maturity"] == "candidate"
    assert VERSION_1101_MAPPING["validated_checks"] == []
    assert "runtime_save" not in VERSION_1101_MAPPING
    assert VERSION_1101_MAPPING["revision_compatibility"] == {
        "schema_version": 1,
        "authority_mapping_id": "data-9-game-1073",
        "validated_checks": VERSION_MAPPING["validated_checks"][:-1],
        "runtime_save": True,
        "allow_forward_game_versions": True,
    }
    assert VERSION_1101_MAPPING["identity"] == {
        "data_version": 9,
        "game_version": 1101,
        "root_class": "SaveLoad+PlayerData",
    }
    assert manifest["audit_id"] == "V1101-RAW-001"
    assert manifest["field_count"] == len(names) == 741
    assert manifest["field_name_sha256"] == _raw_field_name_sha256(names)
    assert set(names) - old_names == {
        "enemiesKilledThisWave",
        "enemiesSpawnedThisWave",
    }
    assert old_names <= set(names)
    assert {
        "enemiesKilledThisWave",
        "enemiesSpawnedThisWave",
    } <= set(manifest["dispositions"]["unknown"])

    unchanged_semantic_sections = {
        "auto_pick_order",
        "card_recharge_modes",
        "cards",
        "free_upgrade_lock_fields",
        "guardian_chip_ids",
        "guardian_chips",
        "module_info_indices",
        "module_loadout",
        "perk_bans",
        "perk_ids",
        "presets",
        "required_array_lengths",
        "required_fields",
        "target_priority_ids",
        "ultimate_weapon_names",
        "unmapped_checks",
        "validated_free_upgrade_lock_set",
    }
    assert all(
        VERSION_1101_MAPPING[key] == VERSION_MAPPING[key]
        for key in unchanged_semantic_sections
    )


def test_revision_compatibility_rejects_published_additions():
    mapping = copy.deepcopy(VERSION_1101_MAPPING)
    dispositions = mapping["raw_field_manifest"]["dispositions"]
    dispositions["unknown"].remove("enemiesKilledThisWave")
    dispositions["automation_gating"].append("enemiesKilledThisWave")
    dispositions["automation_gating"].sort()

    with pytest.raises(
        PlayerSaveError,
        match="additions must remain unknown and unpublished",
    ):
        _validate_revision_compatibility(
            mapping,
            mappings_by_id={
                VERSION_MAPPING["mapping_id"]: VERSION_MAPPING,
                mapping["mapping_id"]: mapping,
            },
            source="test mapping",
        )


def test_revision_compatibility_rejects_unvalidated_authority_check():
    mapping = copy.deepcopy(VERSION_1101_MAPPING)
    mapping["revision_compatibility"]["validated_checks"].append(
        "damage_slider"
    )

    with pytest.raises(
        PlayerSaveError,
        match="validated checks are invalid",
    ):
        _validate_revision_compatibility(
            mapping,
            mappings_by_id={
                VERSION_MAPPING["mapping_id"]: VERSION_MAPPING,
                mapping["mapping_id"]: mapping,
            },
            source="test mapping",
        )


def test_revision_compatibility_rejects_changed_authority_array():
    mapping = copy.deepcopy(VERSION_1101_MAPPING)
    mapping["required_array_lengths"]["perkLevel"] = 51

    with pytest.raises(
        PlayerSaveError,
        match="changed an authority array length",
    ):
        _validate_revision_compatibility(
            mapping,
            mappings_by_id={
                VERSION_MAPPING["mapping_id"]: VERSION_MAPPING,
                mapping["mapping_id"]: mapping,
            },
            source="test mapping",
        )


def test_raw_field_manifest_rejects_invalid_categories():
    mapping = copy.deepcopy(VERSION_MAPPING)
    dispositions = mapping["raw_field_manifest"]["dispositions"]
    dispositions["unsupported"] = dispositions.pop("unknown")

    with pytest.raises(
        PlayerSaveError,
        match="raw field disposition categories are invalid",
    ):
        _validate_raw_field_manifest(mapping, source="test mapping")


def test_raw_field_manifest_rejects_duplicate_dispositions():
    mapping = copy.deepcopy(VERSION_MAPPING)
    dispositions = mapping["raw_field_manifest"]["dispositions"]
    duplicate = dispositions["unknown"][0]
    dispositions["structural"].append(duplicate)
    dispositions["structural"].sort()

    with pytest.raises(PlayerSaveError, match="duplicate dispositions"):
        _validate_raw_field_manifest(mapping, source="test mapping")


def test_raw_field_manifest_requires_ignored_field_reasons():
    mapping = copy.deepcopy(VERSION_MAPPING)
    mapping["raw_field_manifest"]["dispositions"][
        "ignored_with_reason"
    ][0]["reason"] = ""

    with pytest.raises(PlayerSaveError, match="has no reason"):
        _validate_raw_field_manifest(mapping, source="test mapping")


def _decoded_save() -> dict:
    payload = {
        field_name: None
        for field_name in _raw_field_manifest_names(VERSION_MAPPING)
    }
    payload.update(
        {
            "__class__": "SaveLoad+PlayerData",
            "dataVersion": 9,
            "versionNumber": 1073,
            "saveRevision": 1234,
            "roundActiveBool": True,
            "currentTier": 19,
            "currentWave": 450,
            "roundSeed": 123456789,
            "roundsStartedThisTier": [0] * 40,
            "perkLevel": [0] * 50,
            "perksPickedCount": 4,
            "perksPicked": [
                {"wave": 200, "perk": 10, "__class__": "PerkPick"},
                {"wave": 290, "perk": 41, "__class__": "PerkPick"},
                {"wave": 430, "perk": 10, "__class__": "PerkPick"},
                {"wave": 430, "perk": 0, "__class__": "PerkPick"},
            ],
            "battleHistory": [_synthetic_battle_history_entry()],
            "presetName": ["Farm", "Tournament", "Other", "Fourth", "Fifth"],
            "workshopPresetName": ["Farm", "Tourney", "D1", "D2", "D3"],
            "botPresetName": ["Farm", "Flame", "Amplify"],
            "currentPreset": 0,
            "currentWorkshopPreset": 0,
            "currentBotPreset": 0,
            "demonModeAutomateToggle": True,
            "nukeAutomateToggle": False,
            "slotsUnlocked": 22,
            "autoPickPerk": True,
            "bannedPerksIndex": [49, 43, 14, 5, 6, 13, -1, -1],
            "autoPickOrder": [
                10,
                12,
                41,
                24,
                27,
                22,
                3,
                8,
                7,
                42,
                40,
                45,
                48,
                44,
                25,
                23,
                1,
                28,
                0,
                2,
                4,
                5,
                6,
                9,
                13,
                14,
                20,
                21,
                26,
                43,
                46,
                47,
                49,
                11,
            ],
            "firstPerkIndex": 10,
            "targetPriorityList": [2, 7, 9, 5, 8, 6, 3, 0, 4, 1],
            "ultimateWeaponUnlocked": [True] * 9,
            "ultimateWeaponOn": [True] * 9,
            "ultimateWeaponLevel": [0] * 27,
            "poisonSwampStunOff": True,
            "spotlightSmartMissilesOff": False,
            "guardianChipSlot": [6, 7, 8],
            "guardianSlotsUnlocked": 2,
            "guardianChipUnlocked": [False] * 10,
            "guardianChipLevel": [0] * 30,
            "guardianUnlocked": True,
            "tourneyConditionsSeed": 287,
            "tournamentNumber": 287,
            "tournamentCheckedNumber": 287,
            "tournamentRecords": [
                {
                    "tournamentNumber": 287,
                    "date": 0,
                    "leagueID": 5,
                    "wave": 1,
                    "rank": 0,
                }
            ],
            "leagueID": 5,
            "researchLevel": [0] * 250,
            "upgradeWorkshopLevel": [0] * 20,
            "upgradeWorkshopDefenseLevel": [0] * 20,
            "upgradeWorkshopUtilityLevel": [0] * 20,
            "enhancementLevel": [0] * 20,
            "enhancementDefenseLevel": [0] * 20,
            "enhancementUtilityLevel": [0] * 20,
            "enhancementTierUnlocked": [False] * 20,
            "enhancementDefenseTierUnlocked": [False] * 20,
            "enhancementUtilityTierUnlocked": [False] * 20,
            "cardLevel": [0] * 40,
            "cardUnlocked": [True] * 31 + [False] * 9,
            "cardMasteryUnlocked": [True] * 8 + [False] * 32,
            "slotPresetCardInt": [0] * 140,
            "slotPresetCardAssignedBool": [True] * 28 + [False] * 112,
            "labLevel": [1] * 5,
            "labsUnlocked": 5,
            "profileRelics": [19, 18, 20, 21, 6],
            "relicsUnlocked": [2] * 276 + [0] * 29,
            "towerUnlocked": [True] * 74 + [False] * 26,
            "backgroundUnlocked": [True] * 54 + [False] * 46,
            "menuUnlocked": [True] * 11 + [False] * 89,
            "diceTowers": [True] * 50 + [False] * 50,
            "diceBackgrounds": [True] * 50 + [False] * 50,
            "totalSkinsBought": 144,
            "disableAdsUnlockedBool": True,
            "starterPackUnlockedBool": True,
            "epicPackUnlockedBool": True,
            "eventBoostsBoughtTotal": 3,
            "ultimateWeaponPlusLevel": [0] * 9,
            "ultimateWeaponPlusOn": [True] * 9,
            "ultimateWeaponPlusUnlocked": [False] * 9,
            "flameBotLevelCooldownSelected": 25,
            "thunderBotLevelCooldownSelected": 14,
            "goldenBotLevelCooldownSelected": 25,
            "amplifyBotLevelCooldownSelected": 20,
            "botBotLevelCooldownSelected": 25,
            "flameBotPresets": [_bot_preset() for _ in range(4)],
            "thunderBotPresets": [_bot_preset() for _ in range(4)],
            "goldenBotPresets": [_bot_preset() for _ in range(4)],
            "amplifyBotPresets": [_bot_preset() for _ in range(4)],
            "botBotPresets": [_bot_preset() for _ in range(4)],
            "harmonyNodesUnlocked": [True] * 41 + [False] * 7,
            "powerNodesLevel": [0] * 46,
            "powerNodesMaxLevel": [0] * 46,
            "powerNodesUnlocked": [True] * 10 + [False] * 36,
            "synchronicityLevel": 0,
            "synchronicityUnlocked": False,
            "moduleEquipped": [
                _module_item(45),
                _module_item(46),
                _module_item(27),
                _module_item(37),
            ],
            "assistModuleSlots": [
                _assist_module_slot(0, 9),
                _assist_module_slot(1, 20),
                _assist_module_slot(2, 30),
                _assist_module_slot(3, 38),
            ],
            "upgradesLockedFreeUpgrades": [False] * 20,
            "upgradesDefenseLockedFreeUpgrades": [False] * 20,
            "upgradesUtilityLockedFreeUpgrades": [False] * 20,
            "playfabID": "must-not-leak-player-id",
            "userName": "must-not-leak-user-name",
        }
    )
    payload["upgradesLockedFreeUpgrades"][11] = True
    payload["upgradesLockedFreeUpgrades"][12] = True
    payload["upgradesDefenseLockedFreeUpgrades"][10] = True
    payload["roundsStartedThisTier"][19] = 12
    payload["perkLevel"][0] = 1
    payload["perkLevel"][10] = 2
    payload["perkLevel"][41] = 1
    payload["goldenBotPresets"][0] = _bot_preset(
        levels=[30, 20, 15, 30],
        plus_level=2,
    )
    payload["amplifyBotPresets"][0] = _bot_preset(
        levels=[3, 3, 0, 0],
        plus_level=1,
    )
    payload["botBotPresets"][0] = _bot_preset(
        levels=[11, 15, 15, 11],
        plus_level=9,
    )
    return payload


def _decoded_save_v1101() -> dict:
    previous = _decoded_save()
    payload = {
        field_name: previous.get(field_name)
        for field_name in _raw_field_manifest_names(VERSION_1101_MAPPING)
    }
    payload.update(
        {
            "versionNumber": 1101,
            "enemiesKilledThisWave": 17,
            "enemiesSpawnedThisWave": 19,
        }
    )
    return payload


def _bot_preset(
    *,
    levels: list[int] | None = None,
    plus_level: int = 0,
) -> dict:
    values = list(levels or [0, 0, 0, 0])
    return {
        "__class__": "UserBotData",
        "unlocked": True,
        "active": True,
        "levels": values,
        "selectedLevels": list(values),
        "plusUnlocked": True,
        "plusLevel": plus_level,
    }


def _module_item(info_index: int) -> dict:
    return {
        "__class__": "ModuleItem",
        "infoIndex": info_index,
        "guid": f"must-not-leak-module-guid-{info_index}",
        "currentRarity": 15,
        "level": 201,
        "effects": [18, 71, 12, 6, 67, 35, 54, 0],
        "effectLocked": [True] * 7 + [False],
        "privateEffectDetail": "must-not-leak-module-effect",
        "inventoryRecord": {"private": "must-not-leak-module-inventory"},
    }


def _assist_module_slot(slot_type: int, info_index: int) -> dict:
    return {
        "__class__": "AssistModuleSlot",
        "type": slot_type,
        "unlocked": True,
        "module": _module_item(info_index),
        "uniqueEffectEfficiencyLevel": 2,
        "mainEffectEfficiencyLevel": 23,
        "substatEfficiencyLevel": 24,
    }


def _synthetic_battle_history_entry(
    *,
    day: int = 1,
    when: datetime | None = None,
    date_kind: int = 2,
    killed_by: int = 8,
) -> dict:
    history_spec = VERSION_MAPPING["runtime_save"]["battle_history"]
    fields = set(history_spec["non_report_fields"])
    for section in history_spec["more_stats_sections"]:
        for _label, row_spec in section["rows"]:
            if isinstance(row_spec, str):
                fields.add(row_spec)
            else:
                for key in ("source", "amount", "seconds", "active_percent_of"):
                    field = row_spec.get(key)
                    if field:
                        fields.add(field)

    integer_fields = set(history_spec["integer_fields"])
    boolean_fields = set(history_spec["boolean_fields"])
    entry = {"__class__": history_spec["entry_class"]}
    for field in fields:
        if field in boolean_fields:
            entry[field] = False
        elif field in integer_fields:
            entry[field] = 1
        else:
            entry[field] = 1.0

    when = when or datetime(2026, 1, day)
    delta = when - datetime(1, 1, 1)
    ticks = (
        (delta.days * 86400 + delta.seconds) * 10_000_000
        + delta.microseconds * 10
    )
    entry.update(
        {
            "battleDate": ticks | (date_kind << 62),
            "tier": 19,
            "wave": 2558 + day,
            "gameTime": 20599.0 + day,
            "realTime": 4244.0 + day,
            "killedBy": killed_by,
            "coinsEarned": 872_380_000_000_000_000.0,
            "cellsEarned": 204_600.0,
            "totalEnemies": 160_757.0,
            "adGemsThisRound": 30,
        }
    )
    return entry


def _snapshot(monkeypatch, decoded: dict | None = None):
    monkeypatch.setattr(nrbf, "loads", lambda _raw: decoded or _decoded_save())
    return decode_player_save_bytes(
        gzip.compress(b"synthetic-nrbf"),
        source_name="/private/path/playerInfo.dat",
        captured_at=CAPTURED_AT,
    )


def _snapshot_v1101(monkeypatch, decoded: dict | None = None):
    monkeypatch.setattr(
        nrbf,
        "loads",
        lambda _raw: decoded or _decoded_save_v1101(),
    )
    return decode_player_save_bytes(
        gzip.compress(b"synthetic-nrbf-v1101"),
        source_name="/private/path/playerInfo.dat",
        captured_at=CAPTURED_AT,
    )


def test_v1101_decode_reuses_compatible_mappings_and_keeps_tournament_ui(
    monkeypatch,
):
    snapshot = _snapshot_v1101(monkeypatch)

    assert snapshot.mapping_id == "data-9-game-1101"
    assert snapshot.mapping_maturity == "candidate"
    assert snapshot.mapping_resolution == "compatible_exact_revision"
    assert snapshot.mapping_authority_id == "data-9-game-1073"
    assert snapshot.mapping_structural_id == "data-9-game-1101"
    assert snapshot.validated_checks == tuple(
        VERSION_1101_MAPPING["revision_compatibility"]["validated_checks"]
    )
    assert snapshot.shape_valid
    assert snapshot.runtime_save is not None
    assert snapshot.runtime_save.mapping_id == "data-9-game-1101"
    assert (
        snapshot.runtime_save.audit_matrix_id
        == "data-9-game-1073-runtime-audit-v2"
    )
    assert snapshot.runtime_save.active_round_identity is not None
    assert snapshot.runtime_save.active_round_identity.game_version == 1101
    assert snapshot.profile_progression["status"] == "complete"
    assert snapshot.profile_progression["identity"] == {
        "data_version": 9,
        "game_version": 1101,
        "save_revision": 1234,
        "mapping_id": "data-9-game-1101",
        "audit_matrix_id": "data-9-game-1101-profile-progression-v1",
    }
    assert snapshot.checks["cards_deck"].value == "Farm"
    assert snapshot.checks["perk_auto_pick_order"].complete
    assert snapshot.checks["tournament_conditions"].status == "unmapped"
    assert snapshot.checks["tournament_conditions"].reason == (
        "unsupported_game_version"
    )
    assert any(
        "passed its declared additive revision-compatibility gate" in warning
        for warning in snapshot.warnings
    )

    plan = reconcile_requirements(
        snapshot,
        {"cards_deck": "Farm"},
        freshness_verified=True,
    )
    assert plan["checks"]["cards_deck"]["disposition"] == "save_match"
    assert plan["checks"]["cards_deck"]["reason"] == (
        "compatible_revision_save_match"
    )
    assert plan["checks"]["cards_deck"]["save_evidence_authoritative"]

    decoded = _decoded_save_v1101()
    decoded["enemiesKilledThisWave"] = "must-not-publish-v1101-wave-counter"
    redacted = _snapshot_v1101(monkeypatch, decoded)
    assert "must-not-publish-v1101-wave-counter" not in json.dumps(
        redacted.as_dict()
    )


def test_v1101_compatible_snapshot_projects_setup_capture_allowlist(monkeypatch):
    snapshot = _snapshot_v1101(monkeypatch)
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="captured",
        binding=PlayerSaveTargetBinding("localhost:5555", 7),
        acquisition_started_at=CAPTURED_AT - timedelta(milliseconds=1),
        captured_at=CAPTURED_AT,
        acquisition_completed_at=CAPTURED_AT + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=snapshot,
    )

    preview = project_forced_save_setup(acquisition)

    assert preview["mapping_id"] == "data-9-game-1101"
    assert preview["mapping_maturity"] == "candidate"
    assert preview["settings"]["cards_deck"] == "Farm"
    assert set(preview["captured_check_ids"]) == (
        set(snapshot.validated_checks) - {"perk_first_choice"}
    )
    assert "tournament_conditions" not in preview["captured_check_ids"]
    assert any(
        item["setting_id"] == "perk_first_choice"
        and item["status"] == "observed_not_authorable"
        for item in preview["unresolved"]
    )
    assert preview["saving_activates_strategy"] is False
    assert preview["publication_activates_strategy"] is False

    workflow_binding, binding_status, binding_reason = (
        App._setup_capture_workflow_binding(
            acquisition,
            {
                "game_state": "active_battle",
                "runtime_id": "runtime-v1101",
                "activity_scope_run_id": "scope-v1101",
            },
        )
    )
    assert workflow_binding is not None
    assert binding_status is None
    assert binding_reason is None
    assert workflow_binding["active_round_identity_fingerprint"] == (
        snapshot.runtime_save.active_round_identity.fingerprint
    )
    preview["workflow_binding"] = workflow_binding
    preview["capture_origin"] = {
        "schema_version": 1,
        "acquisition_source": "new_setup_capture_refresh",
        "source_manual_control_fingerprint": None,
    }
    validated_preview = validate_setup_capture_preview(preview)
    assert validated_preview is not None
    assert validated_preview["mapping_id"] == "data-9-game-1101"
    assert validated_preview["workflow_binding"] == workflow_binding


def test_unknown_additive_version_uses_latest_compatible_mapping(monkeypatch):
    decoded = _decoded_save_v1101()
    decoded.update(
        {
            "versionNumber": 1102,
            "saveRevision": 1235,
            "futureAdditiveCounter": "must-not-publish-future-counter",
        }
    )

    snapshot = _snapshot_v1101(monkeypatch, decoded)

    assert snapshot.mapping_id == "data-9-game-1102-compatible-via-1101"
    assert snapshot.mapping_resolution == "compatible_forward_revision"
    assert snapshot.mapping_authority_id == "data-9-game-1073"
    assert snapshot.mapping_structural_id == "data-9-game-1101"
    assert snapshot.shape_valid
    assert snapshot.field_count == 742
    assert snapshot.runtime_save is not None
    assert snapshot.runtime_save.mapping_id == snapshot.mapping_id
    assert snapshot.runtime_save.active_round_identity is not None
    assert snapshot.runtime_save.active_round_identity.game_version == 1102
    assert snapshot.profile_progression["status"] == "unavailable"
    assert snapshot.profile_progression["reason"] == (
        "exact_version_progression_mapping_unavailable"
    )
    assert snapshot.checks["tournament_conditions"].reason == (
        "unsupported_game_version"
    )
    assert snapshot.as_dict()["mapping"] == {
        "supported": True,
        "id": "data-9-game-1102-compatible-via-1101",
        "maturity": "candidate",
        "validated_checks": list(snapshot.validated_checks),
        "shape_valid": True,
        "resolution": "compatible_forward_revision",
        "authority_id": "data-9-game-1073",
        "structural_id": "data-9-game-1101",
        "semantic_fingerprint": snapshot.mapping_semantic_fingerprint,
        "canonical_fingerprint": snapshot.canonical_mapping_fingerprint,
        "effective_fingerprint": snapshot.effective_mapping_fingerprint,
        "confirmed_local": dict(snapshot.confirmed_local_mappings),
    }
    assert "must-not-publish-future-counter" not in json.dumps(
        snapshot.as_dict()
    )

    plan = reconcile_requirements(
        snapshot,
        {
            "cards_deck": "Farm",
            "tournament_conditions": {},
        },
        freshness_verified=True,
    )
    assert plan["checks"]["cards_deck"]["disposition"] == "save_match"
    assert plan["checks"]["cards_deck"]["reason"] == (
        "compatible_revision_save_match"
    )
    assert plan["checks"]["tournament_conditions"]["disposition"] == (
        "ui_required"
    )


@pytest.mark.parametrize(
    "mutation",
    ("missing_field", "changed_array", "changed_data_version"),
)
def test_unknown_incompatible_version_falls_back_to_ui(monkeypatch, mutation):
    decoded = _decoded_save_v1101()
    decoded["versionNumber"] = 1102
    if mutation == "missing_field":
        decoded.pop("autoPickPerk")
    elif mutation == "changed_array":
        decoded["perkLevel"] = [0] * 51
    else:
        decoded["dataVersion"] = 10

    snapshot = _snapshot_v1101(monkeypatch, decoded)
    plan = reconcile_requirements(
        snapshot,
        {"cards_deck": "Farm"},
        freshness_verified=True,
    )

    assert not snapshot.mapping_supported
    assert not snapshot.shape_valid
    assert snapshot.runtime_save is None
    assert snapshot.checks == {}
    assert snapshot.mapping_resolution in {
        "incompatible_revision",
        "unsupported",
    }
    assert plan["checks"]["cards_deck"]["disposition"] == "ui_required"
    assert plan["checks"]["cards_deck"]["reason"] == (
        "unsupported_save_version"
    )
    assert plan["checks"]["cards_deck"]["fallback"] == "existing_ui_check"


def test_exact_version_decode_builds_redacted_candidate_snapshot(monkeypatch):
    snapshot = _snapshot(monkeypatch)

    assert snapshot.as_dict()["schema_version"] == 6
    assert snapshot.mapping_id == "data-9-game-1073"
    assert snapshot.mapping_maturity == "candidate"
    assert snapshot.validated_checks == (
        "cards_deck",
        "card_recharge_modes",
        "workshop_preset",
        "bots_preset",
        "perk_first_choice",
        "perk_bans",
        "perk_auto_pick_order",
        "free_upgrade_locks",
        "guardian_chips",
        "modules",
        "auto_pick_perks",
        "target_priority",
        "ultimate_weapon_primaries",
        "poison_swamp_stun",
        "spotlight_missiles",
        "tournament_conditions",
    )
    assert snapshot.shape_valid
    assert snapshot.source_name == "playerInfo.dat"
    assert snapshot.profile_summary["cards"] == {
        "base_slots": 22,
        "effective_slots": 28,
        "preset_count": 5,
        "assigned_counts": [28, 0, 0, 0, 0],
    }
    assert snapshot.checks["cards_deck"].value == "Farm"
    assert snapshot.checks["card_recharge_modes"].value == {
        "Demon Mode": "auto_reactivate",
        "Nuke": "ready_after_recharge",
    }
    assert snapshot.checks["guardian_chips"].value == [
        "Fetch",
        "Summon",
        "Scout",
    ]
    assert snapshot.checks["perk_auto_pick_order"].value[-1] == (
        "spotlight_damage"
    )
    assert snapshot.checks["perk_auto_pick_order"].complete
    assert snapshot.checks["tournament_conditions"].value["summary_codes"] == [
        "DR",
        "SPD",
        "MB",
        "DD",
        "UWD",
        "BU",
        "FU",
        "SD",
        "SRM",
    ]
    assert snapshot.checks["modules"].status == "observed"
    assert snapshot.checks["modules"].value == {
        "cannon_primary": "Amplifying Strike",
        "armor_primary": "Orbital Augment",
        "generator_primary": "Black Hole Digestor",
        "core_primary": "Multiverse Nexus",
        "cannon_assist": "Being Annihilator",
        "armor_assist": "Anti-Cube Portal",
        "generator_assist": "Singularity Harness",
        "core_assist": "Dimension Core",
    }
    progression = snapshot.profile_progression
    assert progression["status"] == "complete"
    assert progression["identity"]["save_revision"] == 1234
    assert progression["components"]["bots"]["validation"] == {
        "audit_id": "V1073-PROFILE-005",
        "evidence_level": "structural",
        "provenance": (
            "Exact version-1073 source fields and component shapes; "
            "unmapped indices, formulas, caps, and effective values remain "
            "unpublished."
        ),
    }
    assert progression["components"]["themes"]["summary"] == {
        "background_dice": {"length": 100, "true_count": 50},
        "background_unlocked": {"length": 100, "true_count": 54},
        "menu_unlocked": {"length": 100, "true_count": 11},
        "tower_dice": {"length": 100, "true_count": 50},
        "tower_unlocked": {"length": 100, "true_count": 74},
    }
    assert progression["components"]["bots"]["values"]["bot_bot_presets"][0][
        "plus_level"
    ] == 9
    assert progression["components"]["modules"]["values"]["primary_slots"][0][
        "level"
    ] == 201

    runtime = snapshot.runtime_save
    assert runtime is not None
    assert runtime.audit_matrix_id == "data-9-game-1073-runtime-audit-v2"
    assert runtime.save_revision == 1234
    assert runtime.round_active
    assert runtime.current_wave == 450
    assert runtime.active_round_identity is not None
    assert runtime.active_round_identity.as_tuple() == (1073, 19, 12, 123456789)
    assert runtime.perks_status == "observed"
    assert runtime.perks is not None
    assert runtime.perks.picked_count == 4
    assert runtime.perks.levels[0] == (0, "max_health", 1)
    assert runtime.perks.picks[-1].perk_key == "max_health"
    assert runtime.battle_history_tail.structural_status == "observed"
    assert runtime.battle_history_tail.identity is not None
    assert runtime.battle_history_tail.completed_entry_status == "observed"
    assert runtime.battle_history_tail.entry is not None
    assert runtime.battle_history_tail.entry.row_count == 144
    assert len(runtime.battle_history_tail.entry.sections) == 16
    assert len(_synthetic_battle_history_entry()) == 148

    rendered = json.dumps(snapshot.as_dict())
    assert "must-not-leak-player-id" not in rendered
    assert "must-not-leak-user-name" not in rendered
    assert "must-not-leak-module-guid" not in rendered
    assert "must-not-leak-module-effect" not in rendered
    assert "must-not-leak-module-inventory" not in rendered
    assert "/private/path" not in rendered


def test_raw_field_manifest_rejects_an_unclassified_decoded_field(monkeypatch):
    decoded = _decoded_save()
    decoded["futureVersion1073Field"] = 1

    snapshot = _snapshot(monkeypatch, decoded)

    assert not snapshot.shape_valid
    assert snapshot.checks == {}
    assert snapshot.runtime_save is None
    assert any(
        "1 unclassified field(s) were decoded: futureVersion1073Field" in warning
        for warning in snapshot.warnings
    )


def test_raw_field_manifest_rejects_a_missing_classified_field(monkeypatch):
    decoded = _decoded_save()
    del decoded["adGemsClaimedToday"]

    snapshot = _snapshot(monkeypatch, decoded)

    assert not snapshot.shape_valid
    assert snapshot.checks == {}
    assert snapshot.runtime_save is None
    assert any(
        "1 classified field(s) are missing: adGemsClaimedToday" in warning
        for warning in snapshot.warnings
    )


def test_private_ignored_and_unknown_root_values_remain_unpublished(monkeypatch):
    decoded = _decoded_save()
    decoded["playfabID"] = "must-not-publish-private-root"
    decoded["musicMutedBool"] = "must-not-publish-ignored-root"
    decoded["adGemsClaimedToday"] = "must-not-publish-unknown-root"

    snapshot = _snapshot(monkeypatch, decoded)
    rendered = json.dumps(snapshot.as_dict())

    assert snapshot.shape_valid
    assert "must-not-publish-private-root" not in rendered
    assert "must-not-publish-ignored-root" not in rendered
    assert "must-not-publish-unknown-root" not in rendered


def test_profile_component_validation_requires_exact_component_coverage():
    mapping = copy.deepcopy(VERSION_MAPPING)
    del mapping["profile_progression"]["component_validation"]["bots"]

    with pytest.raises(
        ProfileProgressionError,
        match="component validation coverage changed",
    ):
        normalize_profile_progression(
            _decoded_save(),
            mapping,
            capture={
                "captured_at": CAPTURED_AT.isoformat(),
                "source_sha256": "0" * 64,
            },
        )


def test_profile_progression_diff_reports_exact_source_indices(monkeypatch):
    before_decoded = _decoded_save()
    after_decoded = copy.deepcopy(before_decoded)
    after_decoded["saveRevision"] += 1
    after_decoded["menuUnlocked"][11] = True
    after_decoded["researchLevel"][231] = 9
    after_decoded["upgradeWorkshopUtilityLevel"][2] = 150
    after_decoded["botBotPresets"][0]["plusLevel"] = 10

    before = _snapshot(monkeypatch, before_decoded).profile_progression
    after = _snapshot(monkeypatch, after_decoded).profile_progression
    delta = diff_profile_progression(before, after)

    assert delta["status"] == "changed"
    assert delta["changed_components"] == [
        "bots",
        "research",
        "themes",
        "workshop",
    ]
    assert {change["path"] for change in delta["changes"]} == {
        "bots.bot_bot_presets[0].plus_level",
        "research.levels[231]",
        "themes.menu_unlocked[11]",
        "workshop.utility_levels[2]",
    }


def test_profile_progression_shape_change_isolated_to_its_component(monkeypatch):
    decoded = _decoded_save()
    decoded["menuUnlocked"] = [True]

    snapshot = _snapshot(monkeypatch, decoded)
    progression = snapshot.profile_progression

    assert snapshot.shape_valid
    assert snapshot.runtime_save is not None
    assert progression["status"] == "partial"
    assert progression["components"]["themes"]["status"] == "partial"
    assert progression["components"]["workshop"]["status"] == "structural"
    assert progression["components"]["themes"]["reasons"] == [
        "menuUnlocked:length_changed:expected=100:actual=1"
    ]
    assert "menu_unlocked" not in progression["components"]["themes"]["values"]


def test_runtime_capture_and_history_projection_are_privacy_safe(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    runtime = snapshot.runtime_save
    assert runtime is not None

    payload = runtime.as_dict()
    assert payload["schema_version"] == 2
    assert payload["capture"] == {
        "captured_at": CAPTURED_AT.isoformat(),
        "source_name": "playerInfo.dat",
        "source_sha256": snapshot.source_sha256,
        "source_size": snapshot.source_size,
        "container": "gzip+nrbf",
        "decompressed_size": len(b"synthetic-nrbf"),
    }
    history = payload["battle_history_tail"]
    structure = history["structure"]
    completed = history["completed_entry"]
    assert structure["fingerprint"]
    assert len(structure["fingerprint"]) == 64
    assert structure["identity"]["killed_by_id"] == 8
    assert completed["status"] == "observed"
    assert completed["projection"]["more_stats"]["row_count"] == 144
    assert "raw_text" not in json.dumps(history)
    assert "playfabID" not in json.dumps(history)
    assert "damageTakenWhileBerserked" not in json.dumps(history)

    rows = {
        (section["key"], row["key"]): row
        for section in completed["projection"]["more_stats"]["sections"]
        for row in section["rows"]
    }
    assert rows[("battle_report", "killed_by")]["value"] == "Scatter"
    assert rows[("battle_report", "coins_per_hour")]["derivation"] == (
        "amount_per_real_hour"
    )
    assert rows[("currencies", "ad_gems")]["value"] == 30
    assert rows[("currencies", "ad_gems")]["source_fields"] == [
        "adGemsThisRound"
    ]
    assert rows[("killed_with_effect_active", "golden_tower")][
        "active_percent_decimal"
    ]


def test_history_projection_covers_the_ordered_144_row_more_stats_report(
    monkeypatch,
):
    runtime = _snapshot(monkeypatch).runtime_save
    assert runtime is not None
    entry = runtime.battle_history_tail.entry
    assert entry is not None

    expected_rows = []
    section_name = None
    for line in CLIPBOARD_REPORT_PATH.read_text(encoding="utf-8").splitlines():
        if "\t" not in line:
            section_name = line
            continue
        label, _value = line.split("\t", 1)
        if label == "Battle Date":
            continue
        expected_rows.append((section_name, label))
    projected_rows = [
        (section.name, row.label)
        for section in entry.sections
        for row in section.rows
    ]

    assert len(expected_rows) == 144
    assert projected_rows == expected_rows


def test_runtime_fingerprints_separate_tail_identity_from_semantics(monkeypatch):
    first_decoded = _decoded_save()
    second_decoded = _decoded_save()
    second_decoded["saveRevision"] += 1
    second_decoded["playfabID"] = "different-private-id"

    first = _snapshot(monkeypatch, first_decoded).runtime_save
    second = _snapshot(monkeypatch, second_decoded).runtime_save
    assert first is not None and second is not None
    assert first.active_round_identity is not None
    assert second.active_round_identity is not None
    assert first.active_round_identity.fingerprint == (
        second.active_round_identity.fingerprint
    )
    assert first.perks is not None and second.perks is not None
    assert first.perks.fingerprint == second.perks.fingerprint
    assert first.battle_history_tail.structural_fingerprint == (
        second.battle_history_tail.structural_fingerprint
    )
    assert first.battle_history_tail.completed_entry_fingerprint == (
        second.battle_history_tail.completed_entry_fingerprint
    )

    changed_decoded = _decoded_save()
    changed_decoded["battleHistory"][-1]["coinsEarned"] *= 1.01
    changed = _snapshot(monkeypatch, changed_decoded).runtime_save
    assert changed is not None
    assert changed.battle_history_tail.structural_fingerprint == (
        first.battle_history_tail.structural_fingerprint
    )
    assert changed.battle_history_tail.completed_entry_fingerprint != (
        first.battle_history_tail.completed_entry_fingerprint
    )


def test_inactive_round_exposes_cleared_perks_without_active_identity(monkeypatch):
    decoded = _decoded_save()
    decoded.update(
        {
            "roundActiveBool": False,
            "currentWave": 0,
            "roundSeed": 0,
            "perkLevel": [0] * 50,
            "perksPickedCount": 0,
            "perksPicked": None,
        }
    )

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    assert runtime.active_round_identity is None
    assert runtime.perks_status == "observed"
    assert runtime.perks is not None
    assert runtime.perks.state == "cleared"
    assert runtime.perks.picks == ()


def test_live_cross_channel_perk_ids_are_mapped(monkeypatch):
    decoded = _decoded_save()
    calibration = json.loads(PERK_ID_CALIBRATION_PATH.read_text(encoding="utf-8"))
    expected = [
        (mapping["perk_id"], mapping["perk_key"]) for mapping in calibration["mappings"]
    ]
    decoded["currentWave"] = max(
        wave for mapping in calibration["mappings"] for wave in mapping["pick_waves"]
    )
    decoded["perksPicked"] = [
        {"wave": wave, "perk": mapping["perk_id"], "__class__": "PerkPick"}
        for mapping in calibration["mappings"]
        for wave in mapping["pick_waves"]
    ]
    decoded["perksPicked"].sort(key=lambda pick: pick["wave"])
    decoded["perksPickedCount"] = len(decoded["perksPicked"])
    decoded["perkLevel"] = [0] * 50
    for mapping in calibration["mappings"]:
        decoded["perkLevel"][mapping["perk_id"]] = mapping["final_level"]

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    assert runtime.perks_status == "observed"
    assert runtime.perks is not None
    assert {(pick.perk_id, pick.perk_key) for pick in runtime.perks.picks} == set(
        expected
    )
    assert calibration["evidence"] == {
        "method": "stable_two_identical_read_cross_channel_same_round_ui_timeline",
        "raw_save_retained": False,
        "decoded_root_retained": False,
        "account_identifiers_retained": False,
    }


def test_unknown_perk_id_fails_only_the_perk_component(monkeypatch):
    decoded = _decoded_save()
    unmapped_id = next(
        perk_id
        for perk_id in range(50)
        if str(perk_id) not in VERSION_MAPPING["perk_ids"]
    )
    decoded["perksPicked"].append(
        {"wave": 440, "perk": unmapped_id, "__class__": "PerkPick"}
    )
    decoded["perksPickedCount"] = 5
    decoded["perkLevel"][unmapped_id] = 1

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    assert runtime.perks is None
    assert runtime.perks_status == "unavailable"
    assert runtime.perks_reason == f"unmapped_perk_id:{unmapped_id}"
    assert runtime.perk_calibration is not None
    assert runtime.perk_calibration.picks[-1].perk_id == unmapped_id
    assert "perk_calibration" not in runtime.as_dict()
    assert runtime.active_round_identity is not None
    assert runtime.battle_history_tail.structural_status == "observed"

    resolved = runtime_with_perk_id_overrides(
        runtime,
        {unmapped_id: "new_observed_perk"},
    )
    assert resolved.perks_status == "observed"
    assert resolved.perks is not None
    assert resolved.perks.picks[-1].perk_key == "new_observed_perk"


def test_perk_id_overlay_cannot_replace_static_or_duplicate_semantics(monkeypatch):
    decoded = _decoded_save()
    unmapped_id = next(
        perk_id
        for perk_id in range(50)
        if str(perk_id) not in VERSION_MAPPING["perk_ids"]
    )
    decoded["perksPicked"].append(
        {"wave": 440, "perk": unmapped_id, "__class__": "PerkPick"}
    )
    decoded["perksPickedCount"] = 5
    decoded["perkLevel"][unmapped_id] = 1
    runtime = _snapshot(monkeypatch, decoded).runtime_save
    assert runtime is not None

    static_id = 10
    replaced = runtime_with_perk_id_overrides(
        runtime,
        {static_id: "different_semantics", unmapped_id: "new_observed_perk"},
    )
    duplicated = runtime_with_perk_id_overrides(
        runtime,
        {unmapped_id: VERSION_MAPPING["perk_ids"][str(static_id)]},
    )

    assert replaced.perks is None
    assert replaced.perks_reason == f"perk_override_static_conflict:{static_id}"
    assert duplicated.perks is None
    assert duplicated.perks_reason == f"perk_override_key_conflict:{unmapped_id}"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        (
            lambda decoded: decoded.update(perksPickedCount=3),
            "perk_count_list_mismatch",
        ),
        (
            lambda decoded: decoded["perkLevel"].__setitem__(10, 1),
            "perk_count_levels_mismatch",
        ),
        (
            lambda decoded: (
                decoded["perkLevel"].__setitem__(10, 1),
                decoded["perkLevel"].__setitem__(41, 2),
            ),
            "perk_list_level_mismatch:10",
        ),
        (
            lambda decoded: decoded["perksPicked"][0].update(extra=True),
            "perk_pick_changed_shape:0",
        ),
    ),
)
def test_inconsistent_perk_shapes_fail_closed(monkeypatch, mutation, reason):
    decoded = _decoded_save()
    mutation(decoded)

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    assert runtime.perks is None
    assert runtime.perks_reason == reason


def test_unknown_killed_by_keeps_structural_tail_identity(monkeypatch):
    decoded = _decoded_save()
    decoded["battleHistory"][-1]["killedBy"] = 77

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    tail = runtime.battle_history_tail
    assert tail.structural_status == "observed"
    assert tail.identity is not None
    assert tail.identity.killed_by_id == 77
    assert tail.structural_fingerprint is not None
    assert tail.completed_entry_status == "unavailable"
    assert tail.completed_entry_reason == "unmapped_killed_by_id:77"
    assert tail.completed_entry_fingerprint is None
    assert tail.entry is None
    rendered_tail = tail.as_dict()
    assert rendered_tail["structure"]["identity"]["killed_by_id"] == 77
    assert rendered_tail["completed_entry"]["projection"] is None
    assert rendered_tail["completed_entry"]["fallback"] == (
        "existing_ui_game_stats_perks_more_stats"
    )
    assert runtime.perks_status == "observed"
    evidence = _snapshot(monkeypatch, decoded).checks[
        "battle_history_killed_by"
    ]
    assert evidence.status == "unmapped"
    assert evidence.diagnostics["mapping_candidates"] == [
        {
            "value_kind": "battle_history_killed_by_id",
            "raw_discriminator": {"kind": "integer_id", "value": 77},
            "pairing_method": "exact_locator",
            "locator": "killed_by",
            "expected_observation_count": 1,
            "observation_count_policy": "exact",
            "minimum_evidence_count": 1,
            "known_semantic_values": list(
                VERSION_MAPPING["runtime_save"]["battle_history"][
                    "killed_by_ids"
                ].values()
            ),
            "known_raw_semantic_value": None,
            "peer_semantic_values": [],
            "peer_locator_values": {},
            "scope": {},
        }
    ]


def test_unknown_tournament_league_exposes_review_candidate(monkeypatch):
    decoded = _decoded_save()
    decoded["leagueID"] = 4
    decoded["tournamentRecords"][-1]["leagueID"] = 4

    snapshot = _snapshot(monkeypatch, decoded)
    evidence = snapshot.checks["tournament_league"]

    assert snapshot.checks["tournament_conditions"].reason == (
        "league_mapping_not_validated"
    )
    assert evidence.status == "unmapped"
    candidate = evidence.diagnostics["mapping_candidates"][0]
    assert candidate["value_kind"] == "tournament_league_id"
    assert candidate["raw_discriminator"]["value"] == 4


@pytest.mark.parametrize(
    ("killed_by_id", "label"),
    ((3, "Boss"), (6, "Vampire"), (99, "Surrender")),
)
def test_cross_channel_killed_by_ids_are_semantically_mapped(
    monkeypatch,
    killed_by_id,
    label,
):
    decoded = _decoded_save()
    decoded["battleHistory"][-1]["killedBy"] = killed_by_id

    tail = _snapshot(monkeypatch, decoded).runtime_save.battle_history_tail

    assert tail.identity is not None
    assert tail.identity.killed_by_id == killed_by_id
    assert tail.completed_entry_status == "observed"
    assert tail.entry is not None
    assert tail.entry.killed_by == label


@pytest.mark.parametrize(
    "mutation",
    (
        lambda entry: entry.pop("coinsEarned"),
        lambda entry: entry.update(coinsEarned=1),
        lambda entry: entry.update(unexpectedField=1.0),
    ),
)
def test_malformed_history_entry_never_publishes_partial_projection(
    monkeypatch,
    mutation,
):
    decoded = _decoded_save()
    mutation(decoded["battleHistory"][-1])

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    tail = runtime.battle_history_tail
    assert tail.structural_status == "unavailable"
    assert tail.structural_fingerprint is None
    assert tail.identity is None
    assert tail.completed_entry_status == "unavailable"
    assert tail.entry is None


def test_mixed_datetime_kinds_do_not_use_cross_kind_tick_ordering(monkeypatch):
    decoded = _decoded_save()
    decoded["battleHistory"] = [
        _synthetic_battle_history_entry(
            when=datetime(2026, 1, 2, 5),
            date_kind=1,
        ),
        _synthetic_battle_history_entry(
            when=datetime(2026, 1, 1, 22, 30),
            date_kind=2,
        ),
    ]

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    tail = runtime.battle_history_tail
    assert tail.structural_status == "observed"
    assert tail.identity is not None
    assert tail.identity.battle_date["kind"] == "local"
    assert tail.identity.battle_date["clock_basis"] == (
        "local_wall_clock_without_offset"
    )
    assert tail.completed_entry_status == "observed"


def test_utc_tail_datetime_retains_its_clock_basis(monkeypatch):
    decoded = _decoded_save()
    decoded["battleHistory"] = [
        _synthetic_battle_history_entry(date_kind=1),
    ]

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    identity = runtime.battle_history_tail.identity
    assert identity is not None
    assert identity.battle_date["kind"] == "utc"
    assert identity.battle_date["clock_basis"] == "utc"
    assert identity.battle_date["clock_time"].endswith("+00:00")


def test_capped_history_rollover_changes_only_the_newest_tail_identity(
    monkeypatch,
):
    start = datetime(2026, 1, 1)
    decoded = _decoded_save()
    decoded["battleHistory"] = [
        _synthetic_battle_history_entry(when=start + timedelta(days=offset))
        for offset in range(30)
    ]
    before = _snapshot(monkeypatch, decoded).runtime_save

    rolled = _decoded_save()
    rolled["battleHistory"] = [
        _synthetic_battle_history_entry(when=start + timedelta(days=offset))
        for offset in range(1, 31)
    ]
    after = _snapshot(monkeypatch, rolled).runtime_save

    assert before is not None and after is not None
    before_tail = before.battle_history_tail
    after_tail = after.battle_history_tail
    assert before_tail.entry_count == after_tail.entry_count == 30
    assert before_tail.as_dict()["structure"]["at_capacity"]
    assert after_tail.as_dict()["structure"]["at_capacity"]
    assert before_tail.structural_status == after_tail.structural_status == (
        "observed"
    )
    assert before_tail.structural_fingerprint != (
        after_tail.structural_fingerprint
    )
    assert before_tail.identity is not None and after_tail.identity is not None
    assert before_tail.identity.battle_date["ticks"] != (
        after_tail.identity.battle_date["ticks"]
    )


def test_live_calibrated_card_recharge_boolean_polarity(monkeypatch):
    decoded = _decoded_save()
    decoded["demonModeAutomateToggle"] = False
    decoded["nukeAutomateToggle"] = True

    snapshot = _snapshot(monkeypatch, decoded)

    evidence = snapshot.checks["card_recharge_modes"]
    assert evidence.status == "observed"
    assert evidence.complete
    assert evidence.value == {
        "Demon Mode": "ready_after_recharge",
        "Nuke": "auto_reactivate",
    }
    assert evidence.source_fields == (
        "demonModeAutomateToggle",
        "nukeAutomateToggle",
    )


def test_card_recharge_changed_field_type_fails_closed(monkeypatch):
    decoded = _decoded_save()
    decoded["demonModeAutomateToggle"] = 1

    snapshot = _snapshot(monkeypatch, decoded)

    evidence = snapshot.checks["card_recharge_modes"]
    assert evidence.status == "unmapped"
    assert not evidence.complete
    assert evidence.value is None
    assert "demonModeAutomateToggle" in evidence.reason


def test_live_calibrated_swamp_radius_perk_id_is_mapped(monkeypatch):
    decoded = _decoded_save()
    decoded["bannedPerksIndex"] = [21, -1, -1, -1, -1, -1, -1, -1]

    snapshot = _snapshot(monkeypatch, decoded)

    assert snapshot.checks["perk_bans"].status == "observed"
    assert snapshot.checks["perk_bans"].value == ["swamp_radius"]


def test_live_calibrated_auto_pick_ids_follow_the_ui_rank_order(monkeypatch):
    decoded = _decoded_save()
    decoded["autoPickOrder"] = [
        10,
        12,
        41,
        24,
        27,
        22,
        3,
        8,
        7,
        42,
        40,
        45,
        48,
        44,
        25,
        23,
        28,
        1,
        0,
        2,
        4,
        5,
        6,
        9,
        13,
        14,
        20,
        21,
        26,
        43,
        46,
        47,
        49,
        11,
    ]

    snapshot = _snapshot(monkeypatch, decoded)

    assert snapshot.checks["perk_auto_pick_order"].value == [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
        "golden_tower_bonus",
        "black_hole_duration",
        "death_wave_quantity",
        "coins_bonus",
        "free_upgrade_chance",
        "orbs",
        "enemy_health_tradeoff",
        "tower_damage_boss_health_tradeoff",
        "enemy_speed_tradeoff",
        "boss_health_tradeoff",
        "ranged_distance_tradeoff",
        "chain_lightning_damage",
        "inner_land_mines",
        "spotlight_damage",
        "damage",
    ]
    assert snapshot.checks["perk_auto_pick_order"].complete is True
    assert snapshot.checks["perk_auto_pick_order"].reason == ""
    assert len(decoded["autoPickOrder"]) == 34
    assert len(set(decoded["autoPickOrder"])) == 34
    assert set(decoded["autoPickOrder"]) == {
        int(perk_id) for perk_id in VERSION_MAPPING["perk_ids"]
    }
    assert VERSION_MAPPING["perk_ids"]["11"] == (
        "unlock_random_ultimate_weapon"
    )


def test_statically_mapped_random_ultimate_weapon_needs_no_dynamic_override(
    monkeypatch,
):
    decoded = _decoded_save()
    decoded["perksPicked"].append(
        {"wave": 440, "perk": 11, "__class__": "PerkPick"}
    )
    decoded["perksPickedCount"] += 1
    decoded["perkLevel"][11] = 1

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    assert runtime.perks_status == "observed"
    assert runtime.perks is not None
    assert runtime.perks.picks[-1].perk_key == (
        "unlock_random_ultimate_weapon"
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda values: values[:-1], "shape changed"),
        (
            lambda values: [*values[:18], values[0], *values[19:]],
            "duplicate",
        ),
        (lambda values: [999, *values[1:]], "membership changed"),
        (lambda values: [True, *values[1:]], "exact integer"),
    ],
)
def test_auto_pick_structural_uncertainty_fails_closed(
    monkeypatch,
    mutation,
    reason,
):
    decoded = _decoded_save()
    decoded["autoPickOrder"] = mutation(decoded["autoPickOrder"])

    evidence = _snapshot(monkeypatch, decoded).checks["perk_auto_pick_order"]

    assert evidence.status == "unmapped"
    assert not evidence.complete
    assert reason.casefold() in evidence.reason.casefold()


def test_free_upgrade_locks_accept_required_subset_with_unmanaged_health(
    monkeypatch,
):
    decoded = _decoded_save()
    decoded["upgradesDefenseLockedFreeUpgrades"][0] = True
    snapshot = _snapshot(monkeypatch, decoded)

    evidence = snapshot.checks["free_upgrade_locks"]
    assert evidence.status == "observed"
    assert set(evidence.value) == {
        "Shockwave Size",
        "Bounce Shot Targets",
        "Bounce Shot Range",
        "Health",
    }
    matching = reconcile_requirements(
        snapshot,
        {
            "free_upgrade_locks": [
                "Shockwave Size",
                "Bounce Shot Targets",
                "Bounce Shot Range",
            ]
        },
        freshness_verified=True,
    )
    narrower = reconcile_requirements(
        snapshot,
        {"free_upgrade_locks": ["Shockwave Size"]},
        freshness_verified=True,
    )
    duplicate = reconcile_requirements(
        snapshot,
        {"free_upgrade_locks": [*evidence.value, evidence.value[0]]},
        freshness_verified=True,
    )
    assert matching["checks"]["free_upgrade_locks"]["disposition"] == "save_match"
    assert matching["checks"]["free_upgrade_locks"]["diagnostics"] == {
        "unmanaged_locks": ["Health"],
        "unmapped_locked_slot_count": 0,
    }
    assert narrower["checks"]["free_upgrade_locks"]["disposition"] == (
        "save_match"
    )
    assert narrower["checks"]["free_upgrade_locks"]["diagnostics"][
        "unmanaged_locks"
    ] == ["Bounce Shot Range", "Bounce Shot Targets", "Health"]
    assert duplicate["checks"]["free_upgrade_locks"]["reason"] == (
        "save_requirement_outside_validated_scope"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("upgradesLockedFreeUpgrades", 1),
        ("upgradesUtilityLockedFreeUpgrades", None),
    ],
)
def test_free_upgrade_lock_non_boolean_value_fails_closed(
    monkeypatch,
    field,
    value,
):
    decoded = _decoded_save()
    index = 0
    decoded[field][index] = value

    evidence = _snapshot(monkeypatch, decoded).checks["free_upgrade_locks"]

    assert evidence.status == "unmapped"
    assert not evidence.complete


def test_free_upgrade_lock_shape_failure_is_component_local(monkeypatch):
    decoded = _decoded_save()
    decoded["upgradesDefenseLockedFreeUpgrades"].pop()

    snapshot = _snapshot(monkeypatch, decoded)
    evidence = snapshot.checks["free_upgrade_locks"]

    assert snapshot.shape_valid
    assert snapshot.checks["cards_deck"].status == "observed"
    assert evidence.status == "unmapped"
    assert not evidence.complete
    assert "shape" in evidence.reason.casefold()


def test_missing_required_free_upgrade_lock_retains_ui_fallback(monkeypatch):
    decoded = _decoded_save()
    decoded["upgradesLockedFreeUpgrades"][11] = False
    snapshot = _snapshot(monkeypatch, decoded)

    plan = reconcile_requirements(
        snapshot,
        {
            "free_upgrade_locks": [
                "Shockwave Size",
                "Bounce Shot Targets",
                "Bounce Shot Range",
            ]
        },
        freshness_verified=True,
    )

    decision = plan["checks"]["free_upgrade_locks"]
    assert snapshot.checks["free_upgrade_locks"].status == "observed"
    assert decision["disposition"] == "save_mismatch"
    assert decision["reason"] == "save_mismatch"
    assert decision["ui_requirement_kind"] == "trusted_mismatch"
    assert decision["repair_queued"] is True
    assert decision["fallback"] == "existing_ui_check"


def test_calibrated_target_priority_permutation_matches_farm(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    expected = [
        "Fast",
        "Protector",
        "Fleets",
        "Boss",
        "Elites",
        "In Spotlight",
        "Tank",
        "Closest (Default)",
        "Ranged",
        "Basic",
    ]

    evidence = snapshot.checks["target_priority"]
    plan = reconcile_requirements(
        snapshot,
        {"target_priority": expected},
        freshness_verified=True,
    )

    assert evidence.value == expected
    assert plan["checks"]["target_priority"]["disposition"] == "save_match"


def test_target_priority_order_mismatch_retains_ui_fallback(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    expected = list(snapshot.checks["target_priority"].value)
    expected[0], expected[1] = expected[1], expected[0]

    decision = reconcile_requirements(
        snapshot,
        {"target_priority": expected},
        freshness_verified=True,
    )["checks"]["target_priority"]

    assert decision["disposition"] == "save_mismatch"
    assert decision["reason"] == "save_mismatch"


@pytest.mark.parametrize(
    "priority",
    [
        [0, 2, 9, 5, 8, 7, 6, 3, 4],
        [0, 2, 9, 5, 8, 7, 6, 3, 4, 4],
        [0, 2, 9, 5, 8, 7, 6, 3, 4, 99],
        [0, 2, 9, 5, 8, 7, 6, 3, 4, True],
        [0, 2, 9, 5, 8, 7, 6, 3, 4, 1, 5],
    ],
)
def test_target_priority_requires_exact_complete_ten_id_order(
    monkeypatch,
    priority,
):
    decoded = _decoded_save()
    decoded["targetPriorityList"] = priority

    evidence = _snapshot(monkeypatch, decoded).checks["target_priority"]

    assert evidence.status == "unmapped"
    assert not evidence.complete


FARM_MODULES = {
    "cannon_primary": "Amplifying Strike",
    "armor_primary": "Orbital Augment",
    "generator_primary": "Black Hole Digestor",
    "core_primary": "Multiverse Nexus",
    "cannon_assist": "Being Annihilator",
    "armor_assist": "Anti-Cube Portal",
    "generator_assist": "Singularity Harness",
    "core_assist": "Dimension Core",
}

TOURNAMENT_MODULES = {
    "cannon_primary": "Amplifying Strike",
    "armor_primary": "Orbital Augment",
    "generator_primary": "Project Funding",
    "core_primary": "Dimension Core",
    "cannon_assist": "Being Annihilator",
    "armor_assist": "Anti-Cube Portal",
    "generator_assist": "Singularity Harness",
    "core_assist": "Harmony Conductor",
}

CURRENT_TOURNAMENT_MODULES = {
    **TOURNAMENT_MODULES,
    "armor_primary": "Anti-Cube Portal",
    "armor_assist": "Space Displacer",
}

MODULE_INFO_INDICES = {
    "7": {"name": "Havoc Bringer", "family": "cannon"},
    "8": {"name": "Death Penalty", "family": "cannon"},
    "9": {"name": "Being Annihilator", "family": "cannon"},
    "10": {"name": "Astral Deliverance", "family": "cannon"},
    "17": {"name": "Wormhole Redirector", "family": "armor"},
    "18": {"name": "Negative Mass Projector", "family": "armor"},
    "19": {"name": "Space Displacer", "family": "armor"},
    "20": {"name": "Anti-Cube Portal", "family": "armor"},
    "27": {"name": "Black Hole Digestor", "family": "generator"},
    "28": {"name": "Pulsar Harvester", "family": "generator"},
    "29": {"name": "Galaxy Compressor", "family": "generator"},
    "30": {"name": "Singularity Harness", "family": "generator"},
    "37": {"name": "Multiverse Nexus", "family": "core"},
    "38": {"name": "Dimension Core", "family": "core"},
    "39": {"name": "Harmony Conductor", "family": "core"},
    "40": {"name": "Om Chip", "family": "core"},
    "41": {"name": "Shrink Ray", "family": "cannon"},
    "42": {"name": "Sharp Fortitude", "family": "armor"},
    "43": {"name": "Project Funding", "family": "generator"},
    "44": {"name": "Magnetic Hook", "family": "core"},
    "45": {"name": "Amplifying Strike", "family": "cannon"},
    "46": {"name": "Orbital Augment", "family": "armor"},
    "47": {"name": "Restorative Bonus", "family": "generator"},
    "48": {"name": "Primordial Collapse", "family": "core"},
}


def _set_module_loadout(
    decoded: dict,
    *,
    primary: tuple[int, int, int, int],
    assist: tuple[int, int, int, int],
) -> None:
    decoded["moduleEquipped"] = [_module_item(value) for value in primary]
    decoded["assistModuleSlots"] = [
        _assist_module_slot(slot_type, value)
        for slot_type, value in enumerate(assist)
    ]


def test_exact_farm_module_loadout_matches_from_one_redacted_snapshot(
    monkeypatch,
):
    snapshot = _snapshot(monkeypatch)

    decision = reconcile_requirements(
        snapshot,
        {"modules": FARM_MODULES},
        freshness_verified=True,
    )["checks"]["modules"]

    assert snapshot.checks["modules"].value == FARM_MODULES
    assert decision["disposition"] == "save_match"
    assert decision["save_requirement_supported"] is True
    rendered = json.dumps(decision)
    for private_marker in (
        "must-not-leak-module-guid",
        "must-not-leak-module-effect",
        "must-not-leak-module-inventory",
        '"level": 201',
    ):
        assert private_marker not in rendered


@pytest.mark.parametrize("mapping", (VERSION_MAPPING, VERSION_1101_MAPPING))
def test_all_current_module_info_indices_are_globally_mapped(mapping):
    assert mapping["module_info_indices"] == MODULE_INFO_INDICES
    assert len({item["name"] for item in MODULE_INFO_INDICES.values()}) == 24
    assert {
        family: sum(
            item["family"] == family for item in MODULE_INFO_INDICES.values()
        )
        for family in ("cannon", "armor", "generator", "core")
    } == {"cannon": 6, "armor": 6, "generator": 6, "core": 6}


@pytest.mark.parametrize(
    "mutate",
    (
        lambda mapping: mapping.__setitem__("module_info_indices", []),
        lambda mapping: mapping.__setitem__("module_info_indices", {}),
        lambda mapping: mapping["module_info_indices"].__setitem__(
            "045",
            mapping["module_info_indices"].pop("45"),
        ),
        lambda mapping: mapping["module_info_indices"].__setitem__(
            "45",
            {"name": " Amplifying Strike", "family": "cannon"},
        ),
        lambda mapping: mapping["module_info_indices"].__setitem__(
            "45",
            {"name": "Amplifying Strike", "family": "unknown"},
        ),
        lambda mapping: mapping["module_info_indices"].__setitem__(
            "46",
            {"name": "Amplifying Strike", "family": "armor"},
        ),
        lambda mapping: mapping["module_info_indices"].__setitem__(
            "45",
            {"name": "Amplifying Strike", "family": "armor"},
        ),
    ),
)
def test_malformed_or_conflicting_global_module_identity_map_fails_closed(
    mutate,
):
    mapping = copy.deepcopy(VERSION_MAPPING)
    mutate(mapping)

    evidence = _module_loadout_evidence(_decoded_save(), mapping)

    assert evidence.status == "unmapped"
    assert evidence.value is None
    assert evidence.reason == "module infoIndex mapping changed"


def test_exact_tournament_reference_is_a_save_backed_observation(monkeypatch):
    decoded = _decoded_save()
    _set_module_loadout(
        decoded,
        primary=(45, 46, 43, 38),
        assist=(9, 20, 30, 39),
    )
    snapshot = _snapshot(monkeypatch, decoded)

    decision = reconcile_requirements(
        snapshot,
        {
            "modules": TOURNAMENT_MODULES,
            "loadout_policies": {"modules": "observe"},
        },
        freshness_verified=True,
    )

    modules = decision["checks"]["modules"]
    assert snapshot.checks["modules"].value == TOURNAMENT_MODULES
    assert modules["disposition"] == "save_observation"
    assert modules["reason"] == "exact_version_save_observation"
    assert modules["matches"] is True
    assert modules["policy"] == "observe"
    assert modules["save_requirement_supported"] is True
    assert modules["ui_required"] is False
    assert decision["summary"]["save_observations"] == 1


def test_tournament_variation_is_reported_without_enforcement(monkeypatch):
    decoded = _decoded_save()
    _set_module_loadout(
        decoded,
        primary=(45, 20, 43, 38),
        assist=(9, 19, 30, 39),
    )
    snapshot = _snapshot(monkeypatch, decoded)

    observed = reconcile_requirements(
        snapshot,
        {
            "modules": TOURNAMENT_MODULES,
            "loadout_policies": {"modules": "observe"},
        },
        freshness_verified=True,
    )["checks"]["modules"]
    enforced = reconcile_requirements(
        snapshot,
        {"modules": TOURNAMENT_MODULES},
        freshness_verified=True,
    )["checks"]["modules"]

    assert snapshot.checks["modules"].value == CURRENT_TOURNAMENT_MODULES
    assert observed["disposition"] == "save_observation"
    assert observed["matches"] is False
    assert observed["observed"] == CURRENT_TOURNAMENT_MODULES
    assert observed["ui_required"] is False
    assert enforced["disposition"] == "save_mismatch"
    assert enforced["reason"] == "save_mismatch"
    rendered = json.dumps(snapshot.checks["modules"].as_dict())
    for private_marker in (
        "must-not-leak-module-guid",
        "must-not-leak-module-effect",
        "must-not-leak-module-inventory",
        '"level": 201',
        '"infoIndex"',
    ):
        assert private_marker not in rendered


def test_tournament_module_observation_audit_still_requires_ui(monkeypatch):
    decoded = _decoded_save()
    _set_module_loadout(
        decoded,
        primary=(45, 46, 43, 38),
        assist=(9, 20, 30, 39),
    )
    snapshot = _snapshot(monkeypatch, decoded)

    decision = reconcile_requirements(
        snapshot,
        {
            "modules": TOURNAMENT_MODULES,
            "loadout_policies": {"modules": "observe"},
        },
        freshness_verified=True,
        force_ui_audit=True,
    )["checks"]["modules"]

    assert decision["disposition"] == "ui_required"
    assert decision["reason"] == "scheduled_ui_audit"


def test_unmapped_tournament_module_name_retains_complete_ui_path(monkeypatch):
    decoded = _decoded_save()
    _set_module_loadout(
        decoded,
        primary=(45, 46, 43, 38),
        assist=(9, 20, 30, 39),
    )
    snapshot = _snapshot(monkeypatch, decoded)
    requested = {**TOURNAMENT_MODULES, "core_assist": "Magnetic Hook"}

    decision = reconcile_requirements(
        snapshot,
        {
            "modules": requested,
            "loadout_policies": {"modules": "observe"},
        },
        freshness_verified=True,
    )["checks"]["modules"]

    assert decision["disposition"] == "ui_required"
    assert decision["reason"] == "save_requirement_outside_validated_scope"
    assert decision["fallback"] == "existing_ui_check"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda decoded: decoded["moduleEquipped"].__setitem__(0, None),
            "Primary module entry",
        ),
        (
            lambda decoded: decoded["moduleEquipped"][0].__setitem__(
                "infoIndex", 999
            ),
            "unsupported primary module infoIndex",
        ),
        (
            lambda decoded: decoded["assistModuleSlots"][0].__setitem__(
                "unlocked", False
            ),
            "Assist module slot is locked",
        ),
        (
            lambda decoded: decoded["assistModuleSlots"][0].__setitem__(
                "__class__", "ChangedAssistSlot"
            ),
            "Assist module slot changed type",
        ),
        (
            lambda decoded: decoded["assistModuleSlots"][0].__setitem__(
                "module", None
            ),
            "exactly one ModuleItem",
        ),
        (
            lambda decoded: decoded.__setitem__(
                "moduleEquipped", decoded["moduleEquipped"][:-1]
            ),
            "Primary module structure changed",
        ),
        (
            lambda decoded: decoded.__setitem__(
                "assistModuleSlots", decoded["assistModuleSlots"][:-1]
            ),
            "Assist module structure changed",
        ),
    ],
)
def test_malformed_or_unknown_module_structure_fails_closed(
    monkeypatch,
    mutation,
    reason,
):
    decoded = _decoded_save()
    mutation(decoded)

    snapshot = _snapshot(monkeypatch, decoded)
    evidence = snapshot.checks["modules"]
    decision = reconcile_requirements(
        snapshot,
        {"modules": FARM_MODULES},
        freshness_verified=True,
    )["checks"]["modules"]

    assert snapshot.shape_valid
    assert snapshot.checks["cards_deck"].status == "observed"
    assert evidence.status == "unmapped"
    assert reason.casefold() in evidence.reason.casefold()
    assert decision["disposition"] == "ui_required"


def test_fresh_decode_effective_fingerprint_tracks_local_mapping_generation(
    monkeypatch,
    tmp_path,
):
    store = ConfirmedLocalMappingStore(tmp_path / "local")
    monkeypatch.setattr(
        "core.player_save.ConfirmedLocalMappingStore",
        lambda: store,
    )
    clean = _snapshot(monkeypatch, _decoded_save())
    decoded = _decoded_save()
    decoded["assistModuleSlots"][3]["module"]["infoIndex"] = 777
    before = _snapshot(monkeypatch, decoded)
    before_projection = copy.deepcopy(before.as_dict())
    pending = before.checks["modules"].diagnostics["mapping_candidates"]
    locator_values = dict(clean.checks["modules"].value)
    locator_values["core_assist"] = "Future Module"
    locator_scopes = {
        f"{family}_{role}": {
            "slot_key": f"{family}_{role}",
            "family": family,
            "role": role,
        }
        for family in ("cannon", "armor", "generator", "core")
        for role in ("primary", "assist")
    }
    ui = build_mapping_candidate_ui_evidence(
        "modules",
        canonical_values=list(locator_values.values()),
        locator_values=locator_values,
        locator_scopes=locator_scopes,
        observed_at=CAPTURED_AT,
    )
    resolved = resolve_mapping_candidates("modules", pending, ui)
    candidate = next(
        item
        for item in resolved
        if item["raw_discriminator"]["value"] == 777
    )
    record = build_mapping_candidate_record(
        mapping={
            "mapping_id": before.mapping_id,
            "data_version": before.data_version,
            "game_version": before.game_version,
            "root_class": before.root_class,
            "resolution": before.mapping_resolution,
            "authority_mapping_id": before.mapping_authority_id,
            "structural_mapping_id": before.mapping_structural_id,
            "canonical_dependency_fingerprint": (
                before.mapping_semantic_fingerprint
            ),
        },
        check_id="modules",
        candidate=candidate,
        snapshot_fingerprint=fingerprint_json(before_projection),
        ui_evidence_fingerprint=fingerprint_json(ui),
        source_observation_fingerprint=ui[
            "source_observation_fingerprint"
        ],
        workflow_provenance={
            "capture_request_id": "capture-effective-generation",
            "inspection_request_id": "inspect-effective-generation",
            "runtime_session_fingerprint": "1" * 64,
            "pid": 4242,
            "target_generation_fingerprint": "2" * 64,
            "activity_scope_fingerprint": "3" * 64,
            "game_state": "home_new_battle",
            "active_round_identity_fingerprint": None,
            "boundary_fingerprint": "4" * 64,
        },
        observed_at=CAPTURED_AT,
    )
    accepted = store.accept_candidate(record)

    after = _snapshot(monkeypatch, decoded)

    assert accepted["generation"] == 1
    assert before.effective_mapping_fingerprint != (
        after.effective_mapping_fingerprint
    )
    assert after.confirmed_local_mappings["generation"] == 1
    assert after.confirmed_local_mappings["applied_event_ids"] == [
        accepted["event_id"]
    ]
    evidence = after.checks["modules"]
    assert evidence.status == "unmapped"
    assert evidence.value is None
    assert "unsupported assist module value" in evidence.reason
    assert evidence.diagnostics == {
        "slots": [
            *before.checks["modules"].diagnostics["slots"][:-1],
            {
                "slot_key": "core_assist",
                "family": "core",
                "role": "assist",
                "name": "Future Module",
                "mapping_status": "mapped_identity_unsupported_scope",
            },
        ]
    }
    assert before.as_dict() == before_projection


def test_known_global_module_pair_in_a_new_scope_is_diagnostic_only(
    monkeypatch,
):
    decoded = _decoded_save()
    decoded["moduleEquipped"][3]["infoIndex"] = 39

    snapshot = _snapshot(monkeypatch, decoded)
    evidence = snapshot.checks["modules"]

    assert evidence.status == "unmapped"
    assert "unsupported primary module value" in evidence.reason
    assert "mapping_candidates" not in evidence.diagnostics
    assert evidence.diagnostics["slots"][3] == {
        "slot_key": "core_primary",
        "family": "core",
        "role": "primary",
        "name": "Harmony Conductor",
        "mapping_status": "mapped_identity_unsupported_scope",
    }


def test_known_unsupported_module_does_not_hide_later_unknown_candidate(
    monkeypatch,
):
    decoded = _decoded_save()
    decoded["moduleEquipped"][3]["infoIndex"] = 39
    decoded["assistModuleSlots"][3]["module"]["infoIndex"] = 777

    evidence = _snapshot(monkeypatch, decoded).checks["modules"]

    assert evidence.status == "unmapped"
    candidates = evidence.diagnostics["mapping_candidates"]
    assert [
        candidate["raw_discriminator"]["value"] for candidate in candidates
    ] == [777]
    statuses = {
        item["slot_key"]: item.get("mapping_status")
        for item in evidence.diagnostics["slots"]
    }
    assert statuses["core_primary"] == "mapped_identity_unsupported_scope"
    assert statuses["core_assist"] == "unmapped"

    ui_values = dict(FARM_MODULES)
    ui_values["core_primary"] = "Harmony Conductor"
    ui_values["core_assist"] = "Future Module"
    ui = build_mapping_candidate_ui_evidence(
        "modules",
        canonical_values=list(ui_values.values()),
        locator_values=ui_values,
        locator_scopes={
            slot_key: {
                "slot_key": slot_key,
                "family": slot_key.rsplit("_", 1)[0],
                "role": slot_key.rsplit("_", 1)[1],
            }
            for slot_key in ui_values
        },
        observed_at=CAPTURED_AT,
    )

    resolved = resolve_mapping_candidates("modules", candidates, ui)

    assert len(resolved) == 1
    assert resolved[0]["status"] == "ready_for_review"
    assert resolved[0]["semantic_value"] == "Future Module"


def test_ultimate_weapon_components_have_independent_value_scope(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    requirements = {
        "ultimate_weapons": {
            name: {"primary": "on"}
            for name in VERSION_MAPPING["ultimate_weapon_names"]
        }
    }
    requirements["ultimate_weapons"]["Poison Swamp"]["stun"] = "off"
    requirements["ultimate_weapons"]["Spotlight"]["missiles"] = "on"

    plan = reconcile_requirements(
        snapshot,
        requirements,
        freshness_verified=True,
    )

    assert plan["checks"]["ultimate_weapon_primaries"]["disposition"] == (
        "save_match"
    )
    assert plan["checks"]["poison_swamp_stun"]["disposition"] == "save_match"
    assert plan["checks"]["spotlight_missiles"]["disposition"] == "save_match"

    requirements["ultimate_weapons"]["Chain Lightning"]["primary"] = "off"
    requirements["ultimate_weapons"]["Spotlight"]["missiles"] = "off"
    unsupported = reconcile_requirements(
        snapshot,
        requirements,
        freshness_verified=True,
    )
    assert unsupported["checks"]["ultimate_weapon_primaries"]["reason"] == (
        "save_requirement_outside_validated_scope"
    )
    assert unsupported["checks"]["spotlight_missiles"]["reason"] == (
        "save_requirement_outside_validated_scope"
    )
    assert unsupported["checks"]["poison_swamp_stun"]["disposition"] == (
        "save_match"
    )


@pytest.mark.parametrize(("raw", "required"), [(False, "on"), (True, "off")])
def test_poison_swamp_stun_supports_both_exact_boolean_polarities(
    monkeypatch,
    raw,
    required,
):
    decoded = _decoded_save()
    decoded["poisonSwampStunOff"] = raw
    snapshot = _snapshot(monkeypatch, decoded)

    assert snapshot.checks["poison_swamp_stun"].value == required
    plan = reconcile_requirements(
        snapshot,
        {
            "ultimate_weapons": {
                "Poison Swamp": {"stun": required},
            }
        },
        freshness_verified=True,
    )
    assert plan["checks"]["poison_swamp_stun"]["disposition"] == "save_match"


def test_malformed_boolean_components_fail_closed_without_truthiness(monkeypatch):
    decoded = _decoded_save()
    decoded["autoPickPerk"] = 1
    decoded["poisonSwampStunOff"] = 0
    decoded["spotlightSmartMissilesOff"] = 0
    decoded["ultimateWeaponOn"][0] = 1

    checks = _snapshot(monkeypatch, decoded).checks

    for check_id in (
        "auto_pick_perks",
        "poison_swamp_stun",
        "spotlight_missiles",
        "ultimate_weapon_primaries",
    ):
        assert checks[check_id].status == "unmapped"
        assert not checks[check_id].complete


def test_unknown_game_version_decodes_metadata_but_requires_ui(monkeypatch):
    decoded = _decoded_save()
    decoded["versionNumber"] = 1074
    snapshot = _snapshot(monkeypatch, decoded)

    assert not snapshot.mapping_supported
    assert not snapshot.shape_valid
    assert snapshot.checks == {}
    assert snapshot.runtime_save is None
    assert "No exact or structurally compatible player-save mapping" in (
        snapshot.warnings[0]
    )

    plan = reconcile_requirements(snapshot, {"cards_deck": "Farm"})
    decision = plan["checks"]["cards_deck"]
    assert decision["reason"] == "unsupported_save_version"
    assert decision["fallback"] == "existing_ui_check"
    assert decision["ui_required"]


def test_exact_version_with_changed_shape_fails_closed(monkeypatch):
    decoded = _decoded_save()
    decoded["researchLevel"] = [0] * 251
    snapshot = _snapshot(monkeypatch, decoded)

    assert snapshot.mapping_supported
    assert not snapshot.shape_valid
    assert snapshot.checks == {}
    assert any("researchLevel length changed" in item for item in snapshot.warnings)


def test_runtime_array_shape_change_fails_the_exact_mapping(monkeypatch):
    decoded = _decoded_save()
    decoded["perkLevel"] = [0] * 49

    snapshot = _snapshot(monkeypatch, decoded)

    assert not snapshot.shape_valid
    assert snapshot.runtime_save is None
    assert any("perkLevel length changed" in item for item in snapshot.warnings)


def test_runtime_round_counter_type_change_publishes_no_runtime_model(monkeypatch):
    decoded = _decoded_save()
    decoded["roundsStartedThisTier"][0] = False

    snapshot = _snapshot(monkeypatch, decoded)

    assert snapshot.shape_valid
    assert snapshot.runtime_save is None
    assert any(
        "runtime projection failed closed" in item for item in snapshot.warnings
    )


def test_candidate_mapping_keeps_ui_for_matching_checks(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    plan = reconcile_requirements(
        snapshot,
        {
            "invariants": {
                "cards_deck": "Farm",
                "auto_pick_perks": True,
                "guardian_chips": ["Fetch", "Summon", "Scout"],
            }
        },
    )

    assert plan["ui_backup_preserved"]
    assert plan["summary"]["matching_observations"] == 3
    assert plan["summary"]["save_matches"] == 0
    assert plan["summary"]["ui_required"] == 3
    assert plan["checks"]["cards_deck"]["reason"] == (
        "save_freshness_unverified"
    )
    assert plan["checks"]["auto_pick_perks"]["reason"] == (
        "save_freshness_unverified"
    )
    assert plan["checks"]["guardian_chips"]["reason"] == (
        "save_freshness_unverified"
    )


def test_candidate_mapping_can_use_only_validated_complete_fresh_checks(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    plan = reconcile_requirements(
        snapshot,
        {
            "cards_deck": "Farm",
            "auto_pick_perks": True,
            "perk_auto_pick_order": [
                "perk_wave_requirement",
                "game_speed",
            ],
        },
        freshness_verified=True,
    )

    assert plan["checks"]["cards_deck"]["disposition"] == "save_match"
    assert plan["checks"]["cards_deck"]["save_check_validated"]
    assert plan["checks"]["auto_pick_perks"]["disposition"] == "save_match"
    assert plan["checks"]["perk_auto_pick_order"]["disposition"] == "save_match"
    assert plan["summary"]["save_matches"] == 3


def test_mismatch_requires_global_trust_and_complete_validated_evidence(monkeypatch):
    snapshot = _snapshot(monkeypatch)

    unverified_freshness = reconcile_requirements(
        snapshot,
        {"cards_deck": "Tournament"},
    )["checks"]["cards_deck"]
    assert unverified_freshness["disposition"] == "ui_required"
    assert unverified_freshness["reason"] == "save_freshness_unverified"
    assert unverified_freshness["snapshot_trusted"] is False

    incomplete_cards = replace(
        snapshot.checks["cards_deck"],
        complete=False,
    )
    incomplete_snapshot = replace(
        snapshot,
        checks={**snapshot.checks, "cards_deck": incomplete_cards},
    )
    incomplete = reconcile_requirements(
        incomplete_snapshot,
        {"cards_deck": "Tournament"},
        freshness_verified=True,
    )["checks"]["cards_deck"]
    assert incomplete["disposition"] == "ui_required"
    assert incomplete["reason"] == "save_evidence_incomplete"
    assert incomplete["save_evidence_authoritative"] is False


def test_auto_pick_enabled_requires_an_exact_boolean_true_requirement(monkeypatch):
    snapshot = _snapshot(monkeypatch)

    plan = reconcile_requirements(
        snapshot,
        {"auto_pick_perks": "on"},
        freshness_verified=True,
    )

    assert plan["checks"]["auto_pick_perks"]["reason"] == (
        "save_requirement_outside_validated_scope"
    )
    assert plan["checks"]["auto_pick_perks"]["ui_required"] is True


def test_validated_card_recharge_match_skips_ui_but_mismatch_does_not(
    monkeypatch,
):
    snapshot = _snapshot(monkeypatch)
    expected = {
        "Demon Mode": "auto_reactivate",
        "Nuke": "ready_after_recharge",
    }

    matching = reconcile_requirements(
        snapshot,
        {"card_recharge_modes": expected},
        freshness_verified=True,
    )
    mismatching = reconcile_requirements(
        snapshot,
        {
            "card_recharge_modes": {
                **expected,
                "Demon Mode": "ready_after_recharge",
            }
        },
        freshness_verified=True,
    )

    assert matching["checks"]["card_recharge_modes"]["disposition"] == (
        "save_match"
    )
    assert matching["checks"]["card_recharge_modes"][
        "save_check_validated"
    ]
    assert mismatching["checks"]["card_recharge_modes"]["reason"] == (
        "save_mismatch"
    )
    assert mismatching["checks"]["card_recharge_modes"]["ui_required"]


def test_validated_mapping_can_skip_only_exact_complete_matches(monkeypatch):
    snapshot = replace(_snapshot(monkeypatch), mapping_maturity="validated")
    plan = reconcile_requirements(
        snapshot,
        {
            "cards_deck": "Farm",
            "perk_auto_pick_order": [
                "perk_wave_requirement",
                "game_speed",
                "coin_tradeoff",
            ],
            "ultimate_weapons": {
                "Chain Lightning": {"primary": True},
                "Poison Swamp": {"primary": True, "stun": False},
            },
            "modules": {"cannon_primary": "Amplifying Strike"},
        },
        freshness_verified=True,
    )

    assert plan["checks"]["cards_deck"]["disposition"] == "save_match"
    assert plan["checks"]["perk_auto_pick_order"]["disposition"] == "save_match"
    assert plan["checks"]["ultimate_weapon_primaries"]["reason"] == (
        "save_requirement_outside_validated_scope"
    )
    assert plan["checks"]["poison_swamp_stun"]["disposition"] == "save_match"
    assert plan["checks"]["modules"]["disposition"] == "ui_required"
    assert plan["checks"]["modules"]["fallback"] == "existing_ui_check"


def test_mismatch_audit_and_staleness_each_restore_ui(monkeypatch):
    snapshot = replace(_snapshot(monkeypatch), mapping_maturity="validated")

    mismatch = reconcile_requirements(
        snapshot,
        {"cards_deck": "Tournament"},
        freshness_verified=True,
    )
    assert mismatch["checks"]["cards_deck"]["disposition"] == "save_mismatch"
    assert mismatch["checks"]["cards_deck"]["reason"] == "save_mismatch"

    audit = reconcile_requirements(
        snapshot,
        {"cards_deck": "Farm"},
        force_ui_audit=True,
    )
    assert audit["checks"]["cards_deck"]["reason"] == "scheduled_ui_audit"

    stale = reconcile_requirements(
        snapshot,
        {"cards_deck": "Farm"},
        max_snapshot_age_s=60,
        now=CAPTURED_AT + timedelta(seconds=61),
    )
    assert stale["checks"]["cards_deck"]["reason"] == "save_snapshot_stale"


def test_stable_adb_pull_requires_two_identical_reads():
    payload = gzip.compress(b"stable")
    read = Mock(side_effect=[payload, payload])

    assert pull_player_save_bytes(
        device_id="localhost:5555",
        attempts=1,
        settle_seconds=0,
        read_fn=read,
    ) == payload
    assert read.call_count == 2


def test_changing_adb_pull_fails_without_publishing_last_read():
    read = Mock(side_effect=[b"one", b"two", b"three", b"four"])

    with pytest.raises(PlayerSavePullError, match="changed between"):
        pull_player_save_bytes(
            attempts=2,
            settle_seconds=0,
            read_fn=read,
        )


def test_device_file_reader_uses_argument_vector_without_shell():
    completed = Mock(stdout=b"save bytes")
    with patch("core.adb_utils.subprocess.run", return_value=completed) as run:
        payload = read_device_file(
            "/storage/emulated/0/playerInfo.dat",
            device_id="localhost:5555",
        )

    assert payload == b"save bytes"
    assert run.call_args.args[0] == [
        "adb",
        "-s",
        "localhost:5555",
        "exec-out",
        "cat",
        "/storage/emulated/0/playerInfo.dat",
    ]
    assert "shell" not in run.call_args.kwargs


@pytest.mark.parametrize(
    "path",
    (
        "relative.dat",
        "/bad\npath",
        "/storage/emulated/0/player;rm",
        "/storage/emulated/../private.dat",
        "",
    ),
)
def test_device_file_reader_rejects_unsafe_paths(path):
    with pytest.raises(ValueError, match="absolute and shell-inert"):
        read_device_file(path)
