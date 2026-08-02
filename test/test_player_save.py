from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
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
ROOT = Path(__file__).resolve().parents[1]
VERSION_MAPPING = json.loads(
    (ROOT / "config/player_save_versions/data_9_game_1073.json").read_text(
        encoding="utf-8"
    )
)
CLIPBOARD_REPORT_PATH = ROOT / "test/fixtures/battle_report_clipboard.txt"


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
            4,
        ],
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
    payload["roundsStartedThisTier"][19] = 12
    payload["perkLevel"][0] = 1
    payload["perkLevel"][10] = 2
    payload["perkLevel"][41] = 1
    return payload


def _synthetic_battle_history_entry(*, day: int = 1) -> dict:
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

    when = datetime(2026, 1, day)
    delta = when - datetime(1, 1, 1)
    ticks = (
        (delta.days * 86400 + delta.seconds) * 10_000_000
        + delta.microseconds * 10
    )
    entry.update(
        {
            "battleDate": ticks | (2 << 62),
            "tier": 19,
            "wave": 2558 + day,
            "gameTime": 20599.0 + day,
            "realTime": 4244.0 + day,
            "killedBy": 8,
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


def test_exact_version_decode_builds_redacted_candidate_snapshot(monkeypatch):
    snapshot = _snapshot(monkeypatch)

    assert snapshot.as_dict()["schema_version"] == 2
    assert snapshot.mapping_id == "data-9-game-1073"
    assert snapshot.mapping_maturity == "candidate"
    assert snapshot.validated_checks == (
        "cards_deck",
        "card_recharge_modes",
        "workshop_preset",
        "bots_preset",
        "perk_first_choice",
        "perk_bans",
        "guardian_chips",
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
    assert snapshot.checks["perk_auto_pick_order"].value[-1] == "damage"
    assert not snapshot.checks["perk_auto_pick_order"].complete
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
    assert snapshot.checks["modules"].status == "unmapped"

    runtime = snapshot.runtime_save
    assert runtime is not None
    assert runtime.audit_matrix_id == "data-9-game-1073-runtime-audit-v1"
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
    assert runtime.battle_history_tail.status == "observed"
    assert runtime.battle_history_tail.entry is not None
    assert runtime.battle_history_tail.entry.row_count == 144
    assert len(runtime.battle_history_tail.entry.sections) == 16
    assert len(_synthetic_battle_history_entry()) == 148

    rendered = json.dumps(snapshot.as_dict())
    assert "must-not-leak-player-id" not in rendered
    assert "must-not-leak-user-name" not in rendered
    assert "/private/path" not in rendered


def test_runtime_capture_and_history_projection_are_privacy_safe(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    runtime = snapshot.runtime_save
    assert runtime is not None

    payload = runtime.as_dict()
    assert payload["capture"] == {
        "captured_at": CAPTURED_AT.isoformat(),
        "source_name": "playerInfo.dat",
        "source_sha256": snapshot.source_sha256,
        "source_size": snapshot.source_size,
        "container": "gzip+nrbf",
        "decompressed_size": len(b"synthetic-nrbf"),
    }
    history = payload["battle_history_tail"]
    assert history["fingerprint"]
    assert len(history["fingerprint"]) == 64
    assert history["projection"]["more_stats"]["row_count"] == 144
    assert "raw_text" not in json.dumps(history)
    assert "playerID" not in json.dumps(history)
    assert "damageTakenWhileBerserked" not in json.dumps(history)

    rows = {
        (section["key"], row["key"]): row
        for section in history["projection"]["more_stats"]["sections"]
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


def test_runtime_fingerprints_are_stable_and_projection_sensitive(monkeypatch):
    first_decoded = _decoded_save()
    second_decoded = _decoded_save()
    second_decoded["saveRevision"] += 1
    second_decoded["playerID"] = "different-private-id"

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
    assert first.battle_history_tail.fingerprint == (
        second.battle_history_tail.fingerprint
    )

    changed_decoded = _decoded_save()
    changed_decoded["battleHistory"][-1]["coinsEarned"] *= 1.01
    changed = _snapshot(monkeypatch, changed_decoded).runtime_save
    assert changed is not None
    assert changed.battle_history_tail.fingerprint != (
        first.battle_history_tail.fingerprint
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


def test_unknown_perk_id_fails_only_the_perk_component(monkeypatch):
    decoded = _decoded_save()
    decoded["perksPicked"].append(
        {"wave": 440, "perk": 46, "__class__": "PerkPick"}
    )
    decoded["perksPickedCount"] = 5
    decoded["perkLevel"][46] = 1

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    assert runtime.perks is None
    assert runtime.perks_status == "unavailable"
    assert runtime.perks_reason == "unmapped_perk_id:46"
    assert runtime.active_round_identity is not None
    assert runtime.battle_history_tail.status == "observed"


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


def test_unknown_killed_by_id_fails_only_the_history_component(monkeypatch):
    decoded = _decoded_save()
    decoded["battleHistory"][-1]["killedBy"] = 99

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    assert runtime.battle_history_tail.status == "unavailable"
    assert runtime.battle_history_tail.reason == "unmapped_killed_by_id:99"
    assert runtime.battle_history_tail.fingerprint is None
    assert runtime.battle_history_tail.entry is None
    assert runtime.perks_status == "observed"


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
    assert runtime.battle_history_tail.status == "unavailable"
    assert runtime.battle_history_tail.fingerprint is None
    assert runtime.battle_history_tail.entry is None


def test_nonchronological_history_fails_closed(monkeypatch):
    decoded = _decoded_save()
    decoded["battleHistory"] = [
        _synthetic_battle_history_entry(day=2),
        _synthetic_battle_history_entry(day=1),
    ]

    runtime = _snapshot(monkeypatch, decoded).runtime_save

    assert runtime is not None
    assert runtime.battle_history_tail.status == "unavailable"
    assert runtime.battle_history_tail.reason == (
        "battle_history_is_not_chronological"
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
        4,
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
    assert snapshot.checks["perk_auto_pick_order"].complete is False


def test_unknown_game_version_decodes_metadata_but_requires_ui(monkeypatch):
    decoded = _decoded_save()
    decoded["versionNumber"] = 1074
    snapshot = _snapshot(monkeypatch, decoded)

    assert not snapshot.mapping_supported
    assert not snapshot.shape_valid
    assert snapshot.checks == {}
    assert snapshot.runtime_save is None
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
        "mapping_candidate_audit"
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
    assert plan["checks"]["auto_pick_perks"]["reason"] == (
        "mapping_candidate_audit"
    )
    assert plan["checks"]["perk_auto_pick_order"]["reason"] == (
        "save_evidence_incomplete"
    )
    assert plan["summary"]["save_matches"] == 1


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
    assert plan["checks"]["perk_auto_pick_order"]["reason"] == (
        "save_evidence_incomplete"
    )
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
