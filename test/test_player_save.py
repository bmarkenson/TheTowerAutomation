from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gzip
import json
from unittest.mock import Mock, patch

import nrbf
import pytest

from core.adb_utils import read_device_file
from core.player_save import (
    PLAYER_SAVE_DEVICE_PATH,
    PlayerSavePullError,
    decode_player_save_bytes,
    pull_player_save_bytes,
    reconcile_requirements,
)


CAPTURED_AT = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)


def test_default_device_path_matches_operator_adb_pull():
    assert PLAYER_SAVE_DEVICE_PATH == (
        "/sdcard/Android/data/"
        "com.TechTreeGames.TheTower/files/playerInfo.dat"
    )


def _decoded_save() -> dict:
    payload = {
        "__class__": "SaveLoad+PlayerData",
        "dataVersion": 9,
        "versionNumber": 1073,
        "saveRevision": 1234,
        "presetName": ["Farm", "Tournament", "Other", "Fourth", "Fifth"],
        "workshopPresetName": ["Farm", "Tourney", "D1", "D2", "D3"],
        "botPresetName": ["Farm", "Flame", "Amplify"],
        "currentPreset": 0,
        "currentWorkshopPreset": 0,
        "currentBotPreset": 0,
        "slotsUnlocked": 22,
        "autoPickPerk": True,
        "bannedPerksIndex": [49, 43, 14, 5, 6, 13, -1, -1],
        "autoPickOrder": [10, 12, 41, 24, 27, 22, 3, 8, 7, 1, 42, 48, 45, 44, 40, 25, 23, 28],
        "firstPerkIndex": 10,
        "targetPriorityList": [0, 2, 9, 5, 8, 7, 6, 3, 4, 1],
        "ultimateWeaponUnlocked": [True] * 9,
        "ultimateWeaponOn": [True] * 9,
        "ultimateWeaponLevel": [0] * 27,
        "poisonSwampStunOff": True,
        "spotlightSmartMissilesOff": False,
        "guardianChipSlot": [6, 7, 8],
        "guardianSlotsUnlocked": 2,
        "guardianChipUnlocked": [False] * 10,
        "guardianChipLevel": [0] * 30,
        "researchLevel": [0] * 250,
        "upgradeWorkshopLevel": [0] * 20,
        "upgradeWorkshopDefenseLevel": [0] * 20,
        "upgradeWorkshopUtilityLevel": [0] * 20,
        "enhancementLevel": [0] * 20,
        "enhancementDefenseLevel": [0] * 20,
        "enhancementUtilityLevel": [0] * 20,
        "cardLevel": [0] * 40,
        "cardUnlocked": [True] * 31 + [False] * 9,
        "slotPresetCardInt": [0] * 140,
        "slotPresetCardAssignedBool": [True] * 28 + [False] * 112,
        "moduleEquipped": [None] * 4,
        "upgradesLockedFreeUpgrades": [False] * 20,
        "upgradesDefenseLockedFreeUpgrades": [False] * 20,
        "upgradesUtilityLockedFreeUpgrades": [False] * 20,
        "playerID": "must-not-leak-player-id",
        "userName": "must-not-leak-user-name",
    }
    payload["upgradesLockedFreeUpgrades"][11] = True
    payload["upgradesLockedFreeUpgrades"][12] = True
    payload["upgradesDefenseLockedFreeUpgrades"][10] = True
    return payload


def _snapshot(monkeypatch, decoded: dict | None = None):
    monkeypatch.setattr(nrbf, "loads", lambda _raw: decoded or _decoded_save())
    return decode_player_save_bytes(
        gzip.compress(b"synthetic-nrbf"),
        source_name="/private/path/playerInfo.dat",
        captured_at=CAPTURED_AT,
    )


def test_exact_version_decode_builds_redacted_candidate_snapshot(monkeypatch):
    snapshot = _snapshot(monkeypatch)

    assert snapshot.mapping_id == "data-9-game-1073"
    assert snapshot.mapping_maturity == "candidate"
    assert snapshot.shape_valid
    assert snapshot.source_name == "playerInfo.dat"
    assert snapshot.profile_summary["cards"] == {
        "base_slots": 22,
        "effective_slots": 28,
        "preset_count": 5,
        "assigned_counts": [28, 0, 0, 0, 0],
    }
    assert snapshot.checks["cards_deck"].value == "Farm"
    assert snapshot.checks["guardian_chips"].value == [
        "Fetch",
        "Summon",
        "Scout",
    ]
    assert snapshot.checks["perk_auto_pick_order"].value[-1] == "damage"
    assert not snapshot.checks["perk_auto_pick_order"].complete
    assert snapshot.checks["modules"].status == "unmapped"

    rendered = json.dumps(snapshot.as_dict())
    assert "must-not-leak-player-id" not in rendered
    assert "must-not-leak-user-name" not in rendered
    assert "/private/path" not in rendered


def test_unknown_game_version_decodes_metadata_but_requires_ui(monkeypatch):
    decoded = _decoded_save()
    decoded["versionNumber"] = 1074
    snapshot = _snapshot(monkeypatch, decoded)

    assert not snapshot.mapping_supported
    assert not snapshot.shape_valid
    assert snapshot.checks == {}
    assert "No exact player-save mapping" in snapshot.warnings[0]

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
    assert {
        decision["reason"] for decision in plan["checks"].values()
    } == {"mapping_candidate_audit"}


def test_validated_mapping_can_skip_only_exact_matches(monkeypatch):
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
    )

    assert plan["checks"]["cards_deck"]["disposition"] == "save_match"
    assert plan["checks"]["perk_auto_pick_order"]["disposition"] == "save_match"
    assert plan["checks"]["ultimate_weapons"]["disposition"] == "save_match"
    assert plan["checks"]["modules"]["disposition"] == "ui_required"
    assert plan["checks"]["modules"]["fallback"] == "existing_ui_check"


def test_mismatch_audit_and_staleness_each_restore_ui(monkeypatch):
    snapshot = replace(_snapshot(monkeypatch), mapping_maturity="validated")

    mismatch = reconcile_requirements(snapshot, {"cards_deck": "Tournament"})
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
