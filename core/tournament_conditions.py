"""Versioned Tournament Battle Condition derivation and record evidence.

The game does not retain the expanded condition list in a post-run Home save.
For an exact supported game version, however, it deterministically generates
that list from the Tournament number.  This module reproduces only the
cross-channel-validated mapping and fails closed for every other version or
league.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional


SCHEMA_VERSION = 1
MAPPING_ID = "data-9-game-1073-tournament-conditions-v1"
SUPPORTED_VERSION = (9, 1073)
LEGEND_LEAGUE_ID = 5

_CONDITIONS: dict[int, tuple[str, str, str]] = {
    0: ("orb_resistance", "OR", "Orb Resistance"),
    1: ("death_ray_resistance", "DR", "Death Ray Resistance"),
    2: ("thorns_resistance", "TR", "Thorns Resistance"),
    3: ("knockback_resistance", "KB", "Knockback Resistance"),
    4: ("enemy_speed", "SPD", "Enemy Speed"),
    5: ("armored_enemies", "AR", "Armored Enemies"),
    6: ("enemy_attack_speed", "EAS", "Enemy Attack Speed"),
    7: ("more_enemies", "ME", "More Enemies"),
    8: ("plasma_cannon_resistance", "PC", "Plasma Cannon Resistance"),
    11: ("more_bosses", "MB", "More Bosses"),
    12: ("energy_shields_down", "ES", "Energy Shields Down"),
    13: ("death_defy_down", "DD", "Death Defy Down"),
    14: ("protectors_ultimate", "PU", "Protector's Ultimate"),
    15: ("tanks_ultimate", "TU", "Tank's Ultimate"),
    17: ("ultimate_weapon_durations", "UWD", "Ultimate Weapon Durations"),
    18: ("bosses_ultimate", "BOU", "Boss's Ultimate"),
    19: ("basics_ultimate", "BU", "Basic's Ultimate"),
    20: ("fasts_ultimate", "FU", "Fast's Ultimate"),
    21: ("ranged_ultimate", "RU", "Ranged Ultimate"),
    26: ("mass_enforcement", "MAE", "Mass Enforcement"),
}

_LEGEND_RANDOM_POOL = (
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    14,
    15,
    17,
    18,
    19,
    20,
    21,
    26,
)

# This order matches the conventional Tournament summary order supplied by the
# operator and makes records stable even though RNG selection order is not
# presentation order.
_SUMMARY_CODE_ORDER = (
    "PC",
    "DR",
    "OR",
    "TR",
    "KB",
    "AR",
    "SPD",
    "EAS",
    "ME",
    "MB",
    "DD",
    "ES",
    "TU",
    "PU",
    "UWD",
    "BU",
    "BOU",
    "FU",
    "MAE",
    "RU",
    "SD",
    "SRM",
)

_FIXED_OVERHEAT = (
    ("enemy_level_skip_decay", "SD", "Enemy Level Skip Decay", None),
    (
        "enemy_level_skip_reduction_multiply",
        "SRM",
        "Enemy Level Skip Reduction - Multiply",
        None,
    ),
    ("damage_decay", None, "Damage Decay", None),
    ("health_decay", None, "Health Decay", None),
    ("more_bosses", "MB", "More Bosses", 11),
    ("more_elites", None, "More Elites", None),
    ("more_fleets", None, "More Fleets", None),
)


class _DotNetRandom:
    """Compatibility implementation of seeded ``System.Random``."""

    _MBIG = 2_147_483_647
    _MSEED = 161_803_398

    def __init__(self, seed: int) -> None:
        subtraction = self._MBIG if seed == -2_147_483_648 else abs(seed)
        mj = self._MSEED - subtraction
        if mj < 0:
            mj += self._MBIG
        self._seed_array = [0] * 56
        self._seed_array[55] = mj
        mk = 1
        for index in range(1, 55):
            target = (21 * index) % 55
            self._seed_array[target] = mk
            mk = mj - mk
            if mk < 0:
                mk += self._MBIG
            mj = self._seed_array[target]
        for _pass in range(4):
            for index in range(1, 56):
                self._seed_array[index] -= self._seed_array[
                    1 + (index + 30) % 55
                ]
                if self._seed_array[index] < 0:
                    self._seed_array[index] += self._MBIG
        self._inext = 0
        self._inextp = 21

    def next(self) -> int:
        self._inext += 1
        if self._inext >= 56:
            self._inext = 1
        self._inextp += 1
        if self._inextp >= 56:
            self._inextp = 1
        value = self._seed_array[self._inext] - self._seed_array[self._inextp]
        if value == self._MBIG:
            value -= 1
        if value < 0:
            value += self._MBIG
        self._seed_array[self._inext] = value
        return value


def _random_range(random: _DotNetRandom, minimum: int, maximum: int) -> int:
    if maximum <= minimum:
        raise ValueError("maximum must be greater than minimum")
    return minimum + random.next() % (maximum - minimum)


def _condition_entry(
    condition_index: int,
    *,
    category: str,
    selection: str,
) -> dict[str, Any]:
    condition_id, code, name = _CONDITIONS[condition_index]
    return {
        "id": condition_id,
        "code": code,
        "name": name,
        "category": category,
        "selection": selection,
        "condition_index": condition_index,
    }


def unavailable_tournament_conditions(
    reason: str,
    *,
    data_version: Optional[int] = None,
    game_version: Optional[int] = None,
    tournament_number: Optional[int] = None,
    league_id: Optional[int] = None,
    source: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Return explicit nonblocking evidence that requires the UI fallback."""

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "unavailable",
        "complete": False,
        "reason": str(reason),
        "mapping_id": (
            MAPPING_ID
            if (data_version, game_version) == SUPPORTED_VERSION
            else None
        ),
        "data_version": data_version,
        "game_version": game_version,
        "tournament_number": tournament_number,
        "league": {
            "id": league_id,
            "name": "Legend League" if league_id == LEGEND_LEAGUE_ID else None,
        },
        "seed": tournament_number,
        "summary_codes": [],
        "heat": [],
        "overheat": [],
        "unknown_conditions": [],
        "source": copy.deepcopy(dict(source or {})),
        "ui_fallback": {
            "preserved": True,
            "required": True,
            "reason": str(reason),
        },
    }


def derive_tournament_conditions(
    tournament_number: int,
    league_id: int,
    *,
    data_version: int,
    game_version: int,
    source: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Derive one exact-version Tournament condition inventory."""

    if isinstance(tournament_number, bool) or tournament_number <= 0:
        return unavailable_tournament_conditions(
            "invalid_tournament_number",
            data_version=data_version,
            game_version=game_version,
            tournament_number=None,
            league_id=league_id,
            source=source,
        )
    if (data_version, game_version) != SUPPORTED_VERSION:
        return unavailable_tournament_conditions(
            "unsupported_game_version",
            data_version=data_version,
            game_version=game_version,
            tournament_number=tournament_number,
            league_id=league_id,
            source=source,
        )
    if league_id != LEGEND_LEAGUE_ID:
        return unavailable_tournament_conditions(
            "league_mapping_not_validated",
            data_version=data_version,
            game_version=game_version,
            tournament_number=tournament_number,
            league_id=league_id,
            source=source,
        )

    random = _DotNetRandom(tournament_number)
    selected_indices = [_random_range(random, 12, 14)]
    selected_pool_indices: list[int] = []
    while len(selected_pool_indices) < 5:
        candidate = _random_range(random, 0, len(_LEGEND_RANDOM_POOL))
        if candidate not in selected_pool_indices:
            selected_pool_indices.append(candidate)
    selected_indices.extend(
        _LEGEND_RANDOM_POOL[index] for index in selected_pool_indices
    )

    order = {code: index for index, code in enumerate(_SUMMARY_CODE_ORDER)}
    heat = [
        _condition_entry(
            condition_index,
            category="heat",
            selection="seeded",
        )
        for condition_index in selected_indices
    ]
    heat.sort(key=lambda item: order[item["code"]])
    overheat = [
        {
            "id": condition_id,
            "code": code,
            "name": name,
            "category": "overheat",
            "selection": "fixed_by_league",
            "condition_index": condition_index,
        }
        for condition_id, code, name, condition_index in _FIXED_OVERHEAT
    ]
    summary_codes = sorted(
        [item["code"] for item in heat]
        + ["MB", "SD", "SRM"],
        key=order.__getitem__,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "complete": True,
        "reason": "",
        "mapping_id": MAPPING_ID,
        "data_version": data_version,
        "game_version": game_version,
        "tournament_number": tournament_number,
        "league": {"id": league_id, "name": "Legend League"},
        "seed": tournament_number,
        "summary_codes": summary_codes,
        "heat": heat,
        "overheat": overheat,
        "unknown_conditions": [],
        "source": copy.deepcopy(dict(source or {})),
        "ui_fallback": {
            "preserved": True,
            "required": False,
            "reason": None,
        },
    }


def _optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dotnet_date(value: Any) -> Optional[date]:
    numeric = _optional_int(value)
    if numeric is None or numeric < 0:
        return None
    ticks = numeric & ((1 << 62) - 1)
    try:
        return (datetime(1, 1, 1) + timedelta(microseconds=ticks // 10)).date()
    except (OverflowError, ValueError):
        return None


def _latest_tournament_record(
    decoded: Mapping[str, Any],
    records_field: str,
    tournament_number: int,
) -> Optional[Mapping[str, Any]]:
    records = decoded.get(records_field)
    if not isinstance(records, (list, tuple)):
        return None
    for record in reversed(records):
        if not isinstance(record, Mapping):
            continue
        if _optional_int(record.get("tournamentNumber")) == tournament_number:
            return record
    return None


def derive_tournament_conditions_from_save(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    """Resolve and cross-check the current event identity in one decoded save."""

    identity = mapping.get("identity") or {}
    data_version = _optional_int(identity.get("data_version"))
    game_version = _optional_int(identity.get("game_version"))
    spec = mapping.get("tournament_conditions") or {}
    if str(spec.get("mapping_id") or "") != MAPPING_ID:
        return unavailable_tournament_conditions(
            "tournament_mapping_config_changed",
            data_version=data_version,
            game_version=game_version,
        )
    fields = {
        "seed": str(spec.get("seed_field") or "tourneyConditionsSeed"),
        "active": str(spec.get("active_number_field") or "tournamentNumber"),
        "checked": str(
            spec.get("checked_number_field") or "tournamentCheckedNumber"
        ),
        "records": str(spec.get("records_field") or "tournamentRecords"),
        "league": str(spec.get("league_field") or "leagueID"),
    }
    seed = _optional_int(decoded.get(fields["seed"])) or 0
    active = _optional_int(decoded.get(fields["active"])) or 0
    checked = _optional_int(decoded.get(fields["checked"])) or 0
    positives = {value for value in (seed, active, checked) if value > 0}
    source = {
        "kind": "player_save",
        "method": "versioned_seed_derivation",
        "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "source_fields": list(fields.values()),
    }
    if not positives:
        return unavailable_tournament_conditions(
            "tournament_identity_missing",
            data_version=data_version,
            game_version=game_version,
            source=source,
        )
    if len(positives) > 1:
        return unavailable_tournament_conditions(
            "tournament_identity_mismatch",
            data_version=data_version,
            game_version=game_version,
            source=source,
        )

    tournament_number = positives.pop()
    seed_source = (
        fields["seed"]
        if seed > 0
        else fields["active"]
        if active > 0
        else fields["checked"]
    )
    record = _latest_tournament_record(
        decoded,
        fields["records"],
        tournament_number,
    )
    league_id = _optional_int(decoded.get(fields["league"]))
    event_date = None
    if record is not None:
        record_league = _optional_int(record.get("leagueID"))
        if league_id is None:
            league_id = record_league
        elif record_league is not None and record_league != league_id:
            return unavailable_tournament_conditions(
                "tournament_league_mismatch",
                data_version=data_version,
                game_version=game_version,
                tournament_number=tournament_number,
                league_id=league_id,
                source=source,
            )
        event_date = _dotnet_date(record.get("date"))

    # Outside an active Tournament battle the game clears both active fields.
    # In that state the checked number is accepted only when the matching
    # registry record binds it to the current terminal-result date.
    if seed <= 0 and active <= 0:
        if record is None or event_date is None:
            return unavailable_tournament_conditions(
                "checked_tournament_not_bound_to_record",
                data_version=data_version,
                game_version=game_version,
                tournament_number=tournament_number,
                league_id=league_id,
                source=source,
            )
        captured_date = captured_at.astimezone(timezone.utc).date()
        if (captured_date - event_date).days not in (0, 1):
            return unavailable_tournament_conditions(
                "checked_tournament_record_is_stale",
                data_version=data_version,
                game_version=game_version,
                tournament_number=tournament_number,
                league_id=league_id,
                source=source,
            )

    source["seed_source"] = seed_source
    source["event_date"] = event_date.isoformat() if event_date else None
    if league_id is None:
        return unavailable_tournament_conditions(
            "tournament_league_missing",
            data_version=data_version,
            game_version=game_version,
            tournament_number=tournament_number,
            source=source,
        )
    return derive_tournament_conditions(
        tournament_number,
        league_id,
        data_version=int(data_version or 0),
        game_version=int(game_version or 0),
        source=source,
    )


def capture_current_tournament_conditions(
    *,
    captured_at: Optional[datetime] = None,
    device_id: Optional[str] = None,
    pull_fn: Optional[Callable[..., bytes]] = None,
    decode_fn: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Capture normalized current-event evidence without changing game state."""

    from core.player_save import (
        PlayerSaveDecodeError,
        PlayerSaveError,
        PlayerSavePullError,
        decode_player_save_bytes,
        pull_player_save_bytes,
    )

    when = captured_at or datetime.now().astimezone()
    pull = pull_fn or pull_player_save_bytes
    decode = decode_fn or decode_player_save_bytes
    try:
        payload = pull(device_id=device_id, attempts=2)
        snapshot = decode(
            payload,
            source_name="playerInfo.dat",
            captured_at=when,
        )
    except PlayerSavePullError:
        return unavailable_tournament_conditions("save_pull_failed")
    except PlayerSaveDecodeError:
        return unavailable_tournament_conditions("save_decode_failed")
    except PlayerSaveError:
        return unavailable_tournament_conditions("save_mapping_failed")
    except (OSError, TypeError, ValueError):
        return unavailable_tournament_conditions("save_capture_failed")

    evidence = snapshot.checks.get("tournament_conditions")
    if evidence is None or evidence.status != "observed" or not evidence.complete:
        return unavailable_tournament_conditions(
            evidence.reason if evidence is not None else "tournament_check_unmapped",
            data_version=snapshot.data_version,
            game_version=snapshot.game_version,
            source={
                "kind": "player_save",
                "method": "versioned_seed_derivation",
                "captured_at": snapshot.captured_at,
                "mapping_id": snapshot.mapping_id,
            },
        )
    if (
        snapshot.mapping_maturity != "validated"
        and "tournament_conditions" not in snapshot.validated_checks
    ):
        return unavailable_tournament_conditions(
            "tournament_mapping_not_validated",
            data_version=snapshot.data_version,
            game_version=snapshot.game_version,
        )

    result = copy.deepcopy(dict(evidence.value))
    source = dict(result.get("source") or {})
    source.update(
        {
            "mapping_id": snapshot.mapping_id,
            "save_revision": snapshot.save_revision,
            "save_sha256": snapshot.source_sha256,
        }
    )
    result["source"] = source
    return result


def tournament_conditions_complete(evidence: Any) -> bool:
    """Return whether a record contains a complete normalized inventory."""

    return bool(
        isinstance(evidence, Mapping)
        and evidence.get("status") == "complete"
        and evidence.get("complete") is True
        and evidence.get("summary_codes")
    )


__all__ = [
    "LEGEND_LEAGUE_ID",
    "MAPPING_ID",
    "SCHEMA_VERSION",
    "SUPPORTED_VERSION",
    "capture_current_tournament_conditions",
    "derive_tournament_conditions",
    "derive_tournament_conditions_from_save",
    "tournament_conditions_complete",
    "unavailable_tournament_conditions",
]
