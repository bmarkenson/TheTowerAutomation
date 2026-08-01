from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.player_save import PlayerSavePullError, SaveCheckEvidence
from core.tournament_conditions import (
    capture_current_tournament_conditions,
    derive_tournament_conditions,
    derive_tournament_conditions_from_save,
)


ROOT = Path(__file__).resolve().parents[1]
MAPPING = json.loads(
    (ROOT / "config/player_save_versions/data_9_game_1073.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize(
    ("tournament_number", "expected"),
    (
        (271, "PC / OR / TR / EAS / MB / DD / BU / SD / SRM"),
        (272, "PC / TR / KB / SPD / MB / ES / TU / SD / SRM"),
        (273, "PC / EAS / MB / ES / TU / BOU / MAE / SD / SRM"),
        (274, "OR / TR / MB / DD / PU / BU / FU / SD / SRM"),
        (275, "DR / OR / TR / KB / ME / MB / DD / SD / SRM"),
        (276, "OR / SPD / EAS / MB / ES / PU / MAE / SD / SRM"),
        (277, "OR / AR / MB / ES / PU / UWD / RU / SD / SRM"),
        (278, "OR / TR / MB / DD / BU / BOU / FU / SD / SRM"),
        (279, "PC / TR / EAS / MB / DD / PU / RU / SD / SRM"),
        (280, "PC / DR / AR / SPD / MB / DD / PU / SD / SRM"),
        (281, "SPD / MB / ES / TU / PU / UWD / RU / SD / SRM"),
        (282, "PC / OR / KB / MB / ES / PU / RU / SD / SRM"),
        (283, "DR / TR / AR / MB / DD / PU / MAE / SD / SRM"),
        (284, "DR / SPD / ME / MB / DD / UWD / BU / SD / SRM"),
        (285, "PC / DR / OR / ME / MB / ES / RU / SD / SRM"),
        (286, "DR / OR / TR / MB / ES / TU / FU / SD / SRM"),
        (287, "DR / SPD / MB / DD / UWD / BU / FU / SD / SRM"),
    ),
)
def test_version_1073_generator_matches_seventeen_observed_tournaments(
    tournament_number,
    expected,
):
    evidence = derive_tournament_conditions(
        tournament_number,
        5,
        data_version=9,
        game_version=1073,
    )

    assert " / ".join(evidence["summary_codes"]) == expected
    assert evidence["status"] == "complete"
    assert evidence["complete"]


def test_complete_inventory_distinguishes_heat_and_fixed_overheat():
    evidence = derive_tournament_conditions(
        287,
        5,
        data_version=9,
        game_version=1073,
    )

    assert [item["name"] for item in evidence["heat"]] == [
        "Death Ray Resistance",
        "Enemy Speed",
        "Death Defy Down",
        "Ultimate Weapon Durations",
        "Basic's Ultimate",
        "Fast's Ultimate",
    ]
    assert [item["name"] for item in evidence["overheat"]] == [
        "Enemy Level Skip Decay",
        "Enemy Level Skip Reduction - Multiply",
        "Damage Decay",
        "Health Decay",
        "More Bosses",
        "More Elites",
        "More Fleets",
    ]
    assert evidence["ui_fallback"] == {
        "preserved": True,
        "required": False,
        "reason": None,
    }


@pytest.mark.parametrize(
    ("data_version", "game_version", "league_id", "reason"),
    (
        (9, 1074, 5, "unsupported_game_version"),
        (9, 1073, 4, "league_mapping_not_validated"),
    ),
)
def test_unknown_version_or_unvalidated_league_fails_to_ui(
    data_version,
    game_version,
    league_id,
    reason,
):
    evidence = derive_tournament_conditions(
        287,
        league_id,
        data_version=data_version,
        game_version=game_version,
    )

    assert evidence["status"] == "unavailable"
    assert evidence["reason"] == reason
    assert evidence["ui_fallback"]["required"]


def _dotnet_date_binary(value: date) -> int:
    ticks = (value.toordinal() - 1) * 24 * 60 * 60 * 10_000_000
    return ticks | (1 << 62)


def test_post_run_save_binds_checked_number_to_current_registry_record():
    decoded = {
        "tourneyConditionsSeed": 0,
        "tournamentNumber": 0,
        "tournamentCheckedNumber": 287,
        "leagueID": 5,
        "tournamentRecords": [
            {
                "tournamentNumber": 287,
                "date": _dotnet_date_binary(date(2026, 8, 1)),
                "leagueID": 5,
            }
        ],
    }

    evidence = derive_tournament_conditions_from_save(
        decoded,
        MAPPING,
        captured_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
    )

    assert evidence["status"] == "complete"
    assert evidence["tournament_number"] == 287
    assert evidence["source"]["seed_source"] == "tournamentCheckedNumber"
    assert evidence["source"]["event_date"] == "2026-08-01"


def test_post_run_checked_number_fails_closed_when_registry_record_is_stale():
    decoded = {
        "tourneyConditionsSeed": 0,
        "tournamentNumber": 0,
        "tournamentCheckedNumber": 286,
        "leagueID": 5,
        "tournamentRecords": [
            {
                "tournamentNumber": 286,
                "date": _dotnet_date_binary(date(2026, 7, 29)),
                "leagueID": 5,
            }
        ],
    }

    evidence = derive_tournament_conditions_from_save(
        decoded,
        MAPPING,
        captured_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
    )

    assert evidence["status"] == "unavailable"
    assert evidence["reason"] == "checked_tournament_record_is_stale"


def test_capture_enriches_evidence_without_retaining_raw_save_values():
    value = derive_tournament_conditions(
        287,
        5,
        data_version=9,
        game_version=1073,
        source={"kind": "player_save", "method": "versioned_seed_derivation"},
    )
    snapshot = SimpleNamespace(
        checks={
            "tournament_conditions": SaveCheckEvidence(
                check_id="tournament_conditions",
                status="observed",
                value=value,
                source_fields=("tournamentCheckedNumber",),
            )
        },
        mapping_maturity="candidate",
        validated_checks=("tournament_conditions",),
        mapping_id="data-9-game-1073",
        data_version=9,
        game_version=1073,
        captured_at="2026-08-01T12:00:00+00:00",
        save_revision=45969,
        source_sha256="abc123",
    )

    evidence = capture_current_tournament_conditions(
        captured_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        pull_fn=lambda **_kwargs: b"opaque-save",
        decode_fn=lambda *_args, **_kwargs: snapshot,
    )

    assert evidence["status"] == "complete"
    assert evidence["source"]["save_revision"] == 45969
    assert evidence["source"]["save_sha256"] == "abc123"
    assert "opaque-save" not in repr(evidence)


def test_capture_failure_is_explicit_and_nonblocking():
    def fail_pull(**_kwargs):
        raise PlayerSavePullError("unavailable")

    evidence = capture_current_tournament_conditions(pull_fn=fail_pull)

    assert evidence["status"] == "unavailable"
    assert evidence["reason"] == "save_pull_failed"
    assert evidence["ui_fallback"]["required"]
