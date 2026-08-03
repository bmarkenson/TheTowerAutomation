"""Privacy-safe exact-version projections for active and completed rounds.

This module never publishes an arbitrary decoded save mapping.  It accepts only
the fields declared by an exact player-save version mapping and returns
component-level evidence.  A malformed or semantically incomplete component is
unavailable in full so callers can retain the existing UI route.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import math
import re
from typing import Any, Optional


RUNTIME_SAVE_SCHEMA_VERSION = 2
HISTORY_ENTRY_SCHEMA_VERSION = 1
HISTORY_TAIL_IDENTITY_SCHEMA_VERSION = 1
DOTNET_TICKS_MASK = 0x3FFFFFFFFFFFFFFF
DOTNET_KIND_SHIFT = 62
DOTNET_KIND_NAMES = {
    0: "unspecified",
    1: "utc",
    2: "local",
    3: "local_ambiguous",
}


class RuntimeSaveNormalizationError(ValueError):
    """The exact-version runtime root cannot be normalized safely."""


class _ComponentUnavailable(ValueError):
    """One optional runtime component must fall back without partial output."""


@dataclass(frozen=True)
class ActiveRoundIdentity:
    """The guarded identity tuple for one active round."""

    game_version: int
    current_tier: int
    rounds_started_this_tier: int
    round_seed: int
    fingerprint: str

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (
            self.game_version,
            self.current_tier,
            self.rounds_started_this_tier,
            self.round_seed,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_version": self.game_version,
            "current_tier": self.current_tier,
            "rounds_started_this_tier": self.rounds_started_this_tier,
            "round_seed": self.round_seed,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RuntimePerkPick:
    """One ordered, mapped in-battle Perk selection."""

    sequence: int
    wave: int
    perk_id: int
    perk_key: str
    level_after: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "wave": self.wave,
            "perk_id": self.perk_id,
            "perk_key": self.perk_key,
            "level_after": self.level_after,
        }


@dataclass(frozen=True)
class RuntimePerkSnapshot:
    """A list/count/level-consistent Perk inventory from one save revision."""

    state: str
    picked_count: int
    levels: tuple[tuple[int, str, int], ...]
    picks: tuple[RuntimePerkPick, ...]
    fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "picked_count": self.picked_count,
            "levels": [
                {
                    "perk_id": perk_id,
                    "perk_key": perk_key,
                    "level": level,
                }
                for perk_id, perk_key, level in self.levels
            ],
            "order_semantics": "oldest_selected_first",
            "picks": [pick.as_dict() for pick in self.picks],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RuntimePerkCalibrationPick:
    """One structurally valid numeric pick retained for semantic calibration."""

    sequence: int
    wave: int
    perk_id: int
    level_after: int


@dataclass(frozen=True)
class RuntimePerkCalibration:
    """Private numeric Perk evidence that never claims a semantic mapping."""

    state: str
    picked_count: int
    levels: tuple[tuple[int, int], ...]
    picks: tuple[RuntimePerkCalibrationPick, ...]
    known_ids: tuple[tuple[int, str], ...]
    fingerprint: str


@dataclass(frozen=True)
class MoreStatsRow:
    """One normalized More Stats row derived from allowlisted save fields."""

    section: str
    section_key: str
    label: str
    key: str
    value_type: str
    value: Any
    value_decimal: Optional[str]
    source_fields: tuple[str, ...]
    derivation: str
    enum_id: Optional[int] = None
    active_percent_decimal: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "section": self.section,
            "section_key": self.section_key,
            "label": self.label,
            "key": self.key,
            "value_type": self.value_type,
            "source": "player_save_battle_history",
            "source_fields": list(self.source_fields),
            "derivation": self.derivation,
        }
        if self.value is not None:
            payload["value"] = self.value
        if self.value_decimal is not None:
            payload["value_decimal"] = self.value_decimal
        if self.enum_id is not None:
            payload["enum_id"] = self.enum_id
        if self.active_percent_decimal is not None:
            payload["active_percent_decimal"] = self.active_percent_decimal
        return payload


@dataclass(frozen=True)
class MoreStatsSection:
    """An ordered section in the normalized More Stats projection."""

    name: str
    key: str
    rows: tuple[MoreStatsRow, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "key": self.key,
            "rows": [row.as_dict() for row in self.rows],
        }


@dataclass(frozen=True)
class CompletedBattleHistoryEntry:
    """A complete versioned projection of one save BattleHistory entry."""

    mapping_id: str
    battle_date: Mapping[str, Any]
    tier: int
    wave: int
    game_time_seconds: float
    real_time_seconds: float
    killed_by_id: int
    killed_by: str
    is_tournament: bool
    sections: tuple[MoreStatsSection, ...]
    fingerprint: str

    @property
    def row_count(self) -> int:
        return sum(len(section.rows) for section in self.sections)

    def projection_dict(self) -> dict[str, Any]:
        return {
            "schema_version": HISTORY_ENTRY_SCHEMA_VERSION,
            "mapping_id": self.mapping_id,
            "identity": {
                "battle_date": dict(self.battle_date),
                "tier": self.tier,
                "wave": self.wave,
                "game_time_seconds": self.game_time_seconds,
                "real_time_seconds": self.real_time_seconds,
                "killed_by_id": self.killed_by_id,
                "killed_by": self.killed_by,
                "is_tournament": self.is_tournament,
            },
            "more_stats": {
                "source_method": "player_save_battle_history",
                "source_complete": True,
                "row_count": self.row_count,
                "sections": [section.as_dict() for section in self.sections],
            },
        }

    def as_dict(self) -> dict[str, Any]:
        payload = self.projection_dict()
        payload["fingerprint"] = self.fingerprint
        return payload


@dataclass(frozen=True)
class BattleHistoryTailIdentity:
    """Semantic-neutral identity for the newest source-ordered history entry."""

    mapping_id: str
    battle_date: Mapping[str, Any]
    tier: int
    wave: int
    game_time_seconds: float
    real_time_seconds: float
    killed_by_id: int
    is_tournament: bool
    fingerprint: str

    def projection_dict(self) -> dict[str, Any]:
        return {
            "schema_version": HISTORY_TAIL_IDENTITY_SCHEMA_VERSION,
            "mapping_id": self.mapping_id,
            "battle_date": dict(self.battle_date),
            "tier": self.tier,
            "wave": self.wave,
            "game_time_seconds": self.game_time_seconds,
            "real_time_seconds": self.real_time_seconds,
            "killed_by_id": self.killed_by_id,
            "is_tournament": self.is_tournament,
        }

    def as_dict(self) -> dict[str, Any]:
        payload = self.projection_dict()
        payload["fingerprint"] = self.fingerprint
        return payload


@dataclass(frozen=True)
class BattleHistoryTail:
    """Structural newest-entry evidence plus an optional semantic projection."""

    structural_status: str
    structural_reason: str
    entry_count: int
    capacity: int
    identity: Optional[BattleHistoryTailIdentity]
    completed_entry_status: str
    completed_entry_reason: str
    entry: Optional[CompletedBattleHistoryEntry]

    @property
    def structural_fingerprint(self) -> Optional[str]:
        return self.identity.fingerprint if self.identity is not None else None

    @property
    def completed_entry_fingerprint(self) -> Optional[str]:
        return self.entry.fingerprint if self.entry is not None else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "structure": {
                "status": self.structural_status,
                "reason": self.structural_reason,
                "entry_count": self.entry_count,
                "capacity": self.capacity,
                "at_capacity": self.entry_count >= self.capacity,
                "order_semantics": "source_order_oldest_first",
                "chronology_validation": (
                    "source_order_only_no_cross_kind_tick_comparison"
                ),
                "fingerprint": self.structural_fingerprint,
                "identity": (
                    self.identity.as_dict() if self.identity is not None else None
                ),
            },
            "completed_entry": {
                "status": self.completed_entry_status,
                "reason": self.completed_entry_reason,
                "fallback": "existing_ui_game_stats_perks_more_stats",
                "fingerprint": self.completed_entry_fingerprint,
                "projection": (
                    self.entry.as_dict() if self.entry is not None else None
                ),
            },
        }


@dataclass(frozen=True)
class NormalizedRuntimeSave:
    """Privacy-safe runtime fields from one exact-version decoded save."""

    mapping_id: str
    audit_matrix_id: str
    capture: Mapping[str, Any]
    save_revision: int
    round_active: bool
    current_wave: int
    active_round_identity: Optional[ActiveRoundIdentity]
    perks_status: str
    perks_reason: str
    perks: Optional[RuntimePerkSnapshot]
    battle_history_tail: BattleHistoryTail
    perk_calibration: Optional[RuntimePerkCalibration] = field(
        default=None,
        repr=False,
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_SAVE_SCHEMA_VERSION,
            "mapping_id": self.mapping_id,
            "audit_matrix_id": self.audit_matrix_id,
            "capture": dict(self.capture),
            "save_revision": self.save_revision,
            "round_active": self.round_active,
            "current_wave": self.current_wave,
            "active_round_identity": (
                self.active_round_identity.as_dict()
                if self.active_round_identity is not None
                else None
            ),
            "perks": {
                "status": self.perks_status,
                "reason": self.perks_reason,
                "fallback": "existing_ui_perks_evidence",
                "snapshot": self.perks.as_dict() if self.perks is not None else None,
            },
            "battle_history_tail": self.battle_history_tail.as_dict(),
            "ui_action_authority": False,
        }


def normalize_runtime_save(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    capture: Mapping[str, Any],
) -> NormalizedRuntimeSave:
    """Normalize only the exact fields declared by ``mapping.runtime_save``."""

    runtime_spec = mapping.get("runtime_save")
    if not isinstance(runtime_spec, Mapping):
        raise RuntimeSaveNormalizationError("runtime mapping is unavailable")
    if runtime_spec.get("schema_version") != 1:
        raise RuntimeSaveNormalizationError("runtime mapping schema changed")
    audit_matrix_id = str(runtime_spec.get("audit_matrix_id") or "")
    if not audit_matrix_id:
        raise RuntimeSaveNormalizationError("runtime audit matrix is unavailable")

    identity = mapping.get("identity")
    if not isinstance(identity, Mapping):
        raise RuntimeSaveNormalizationError("mapping identity is unavailable")
    game_version = _required_int(decoded, "versionNumber")
    if game_version != identity.get("game_version"):
        raise RuntimeSaveNormalizationError("runtime game version mismatch")

    save_revision = _required_nonnegative_int(decoded, "saveRevision")
    round_active = _required_bool(decoded, "roundActiveBool")
    current_tier = _required_nonnegative_int(decoded, "currentTier")
    current_wave = _required_nonnegative_int(decoded, "currentWave")
    round_seed = _required_nonnegative_int(decoded, "roundSeed")
    round_counters = _required_sequence(decoded, "roundsStartedThisTier")
    expected_counter_count = _required_positive_mapping_int(
        runtime_spec,
        "rounds_started_tier_count",
    )
    if len(round_counters) != expected_counter_count:
        raise RuntimeSaveNormalizationError(
            "roundsStartedThisTier changed length"
        )
    normalized_counters = [
        _exact_nonnegative_int(
            value,
            f"roundsStartedThisTier[{index}]",
            RuntimeSaveNormalizationError,
        )
        for index, value in enumerate(round_counters)
    ]
    if current_tier >= len(round_counters):
        raise RuntimeSaveNormalizationError("currentTier is outside round counters")
    counter = normalized_counters[current_tier]

    active_identity: Optional[ActiveRoundIdentity] = None
    if round_active:
        if round_seed == 0:
            raise RuntimeSaveNormalizationError("active roundSeed is zero")
        identity_projection = {
            "game_version": game_version,
            "current_tier": current_tier,
            "rounds_started_this_tier": counter,
            "round_seed": round_seed,
        }
        active_identity = ActiveRoundIdentity(
            game_version=game_version,
            current_tier=current_tier,
            rounds_started_this_tier=counter,
            round_seed=round_seed,
            fingerprint=_fingerprint(identity_projection),
        )

    perk_calibration: Optional[RuntimePerkCalibration]
    try:
        perk_calibration = _normalize_perk_calibration(
            decoded,
            mapping,
            runtime_spec,
            round_active=round_active,
            current_wave=current_wave,
        )
    except _ComponentUnavailable as exc:
        perk_calibration = None
        perks = None
        perks_status = "unavailable"
        perks_reason = str(exc)
    else:
        try:
            perks = _map_perk_calibration(perk_calibration, {})
            perks_status = "observed"
            perks_reason = ""
        except _ComponentUnavailable as exc:
            perks = None
            perks_status = "unavailable"
            perks_reason = str(exc)

    try:
        history_tail = _normalize_history_tail(
            decoded,
            mapping,
            runtime_spec,
        )
    except _ComponentUnavailable as exc:
        raw_history = decoded.get("battleHistory")
        history_count = len(raw_history) if _is_sequence(raw_history) else 0
        history_spec = runtime_spec.get("battle_history")
        capacity = (
            int(history_spec.get("capacity") or 0)
            if isinstance(history_spec, Mapping)
            else 0
        )
        history_tail = BattleHistoryTail(
            structural_status="unavailable",
            structural_reason=str(exc),
            entry_count=history_count,
            capacity=capacity,
            identity=None,
            completed_entry_status="unavailable",
            completed_entry_reason="structural_tail_unavailable",
            entry=None,
        )

    return NormalizedRuntimeSave(
        mapping_id=str(mapping.get("mapping_id") or ""),
        audit_matrix_id=audit_matrix_id,
        capture=_normalized_capture(capture),
        save_revision=save_revision,
        round_active=round_active,
        current_wave=current_wave,
        active_round_identity=active_identity,
        perks_status=perks_status,
        perks_reason=perks_reason,
        perks=perks,
        battle_history_tail=history_tail,
        perk_calibration=perk_calibration,
    )


def _normalize_perk_calibration(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
    runtime_spec: Mapping[str, Any],
    *,
    round_active: bool,
    current_wave: int,
) -> RuntimePerkSnapshot:
    perk_spec = runtime_spec.get("perks")
    if not isinstance(perk_spec, Mapping):
        raise RuntimeSaveNormalizationError("runtime Perk mapping is unavailable")
    level_count = _required_positive_mapping_int(perk_spec, "level_count")
    levels_raw = decoded.get("perkLevel")
    if not _is_sequence(levels_raw) or len(levels_raw) != level_count:
        raise _ComponentUnavailable("perk_levels_changed_shape")
    levels = [
        _exact_nonnegative_int(
            value,
            f"perkLevel[{index}]",
            _ComponentUnavailable,
        )
        for index, value in enumerate(levels_raw)
    ]

    picked_count = _component_nonnegative_int(
        decoded.get("perksPickedCount"),
        "perksPickedCount",
    )
    raw_picks = decoded.get("perksPicked")
    if raw_picks is None:
        raw_picks = []
    if not _is_sequence(raw_picks):
        raise _ComponentUnavailable("perks_picked_changed_shape")
    if picked_count != len(raw_picks):
        raise _ComponentUnavailable("perk_count_list_mismatch")

    perk_ids = mapping.get("perk_ids")
    if not isinstance(perk_ids, Mapping):
        raise RuntimeSaveNormalizationError("versioned Perk IDs are unavailable")
    entry_class = str(perk_spec.get("entry_class") or "")
    expected_entry_fields = {"__class__", "wave", "perk"}
    seen_levels: dict[int, int] = {}
    picks: list[RuntimePerkCalibrationPick] = []
    previous_wave = -1
    for index, raw_pick in enumerate(raw_picks):
        if not isinstance(raw_pick, Mapping):
            raise _ComponentUnavailable(f"malformed_perk_pick:{index}")
        if set(raw_pick) != expected_entry_fields:
            raise _ComponentUnavailable(f"perk_pick_changed_shape:{index}")
        if raw_pick.get("__class__") != entry_class:
            raise _ComponentUnavailable(f"perk_pick_class_changed:{index}")
        wave = _component_nonnegative_int(
            raw_pick.get("wave"),
            f"perk_pick_wave:{index}",
        )
        perk_id = _component_nonnegative_int(
            raw_pick.get("perk"),
            f"perk_pick_id:{index}",
        )
        if wave < previous_wave:
            raise _ComponentUnavailable("perk_pick_order_is_not_monotonic")
        if round_active and wave > current_wave:
            raise _ComponentUnavailable("perk_pick_wave_exceeds_current_wave")
        previous_wave = wave
        if perk_id >= len(levels):
            raise _ComponentUnavailable(f"perk_id_outside_levels:{perk_id}")
        level_after = seen_levels.get(perk_id, 0) + 1
        seen_levels[perk_id] = level_after
        picks.append(
            RuntimePerkCalibrationPick(
                sequence=index + 1,
                wave=wave,
                perk_id=perk_id,
                level_after=level_after,
            )
        )

    if sum(levels) != picked_count:
        raise _ComponentUnavailable("perk_count_levels_mismatch")
    normalized_levels: list[tuple[int, int]] = []
    for perk_id, level in enumerate(levels):
        if level == 0:
            continue
        if seen_levels.get(perk_id, 0) != level:
            raise _ComponentUnavailable(f"perk_list_level_mismatch:{perk_id}")
        normalized_levels.append((perk_id, level))
    if round_active:
        state = "active_round"
    elif picked_count:
        state = "post_round_retained"
    else:
        state = "cleared"
    known_ids: list[tuple[int, str]] = []
    seen_known_ids: set[int] = set()
    seen_known_keys: set[str] = set()
    for raw_id, raw_key in perk_ids.items():
        try:
            perk_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeSaveNormalizationError(
                "versioned Perk ID changed shape"
            ) from exc
        if (
            perk_id < 0
            or perk_id >= len(levels)
            or not isinstance(raw_key, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", raw_key) is None
            or perk_id in seen_known_ids
            or raw_key in seen_known_keys
        ):
            raise RuntimeSaveNormalizationError(
                "versioned Perk ID changed shape"
            )
        known_ids.append((perk_id, raw_key))
        seen_known_ids.add(perk_id)
        seen_known_keys.add(raw_key)
    known_ids.sort()
    structural_projection = {
        "state": state,
        "picked_count": picked_count,
        "levels": [list(item) for item in normalized_levels],
        "picks": [
            [pick.sequence, pick.wave, pick.perk_id, pick.level_after]
            for pick in picks
        ],
    }
    return RuntimePerkCalibration(
        state=state,
        picked_count=picked_count,
        levels=tuple(normalized_levels),
        picks=tuple(picks),
        known_ids=tuple(known_ids),
        fingerprint=_fingerprint(structural_projection),
    )


def runtime_with_perk_id_overrides(
    runtime: NormalizedRuntimeSave,
    overrides: Mapping[int, str],
) -> NormalizedRuntimeSave:
    """Return ``runtime`` with a complete session-only semantic Perk overlay.

    Static exact-version mappings cannot be replaced.  The overlay is useful
    only when every picked/nonzero ID becomes known; partial or conflicting
    evidence leaves the caller on the existing fail-closed path.
    """

    calibration = runtime.perk_calibration
    if calibration is None:
        return runtime
    try:
        perks = _map_perk_calibration(calibration, overrides)
    except _ComponentUnavailable as exc:
        return replace(
            runtime,
            perks_status="unavailable",
            perks_reason=str(exc),
            perks=None,
        )
    return replace(
        runtime,
        perks_status="observed",
        perks_reason="",
        perks=perks,
    )


def _map_perk_calibration(
    calibration: RuntimePerkCalibration,
    overrides: Mapping[int, str],
) -> RuntimePerkSnapshot:
    known = dict(calibration.known_ids)
    keys_to_ids = {key: perk_id for perk_id, key in known.items()}
    for raw_id, raw_key in overrides.items():
        if type(raw_id) is not int or raw_id < 0:
            raise _ComponentUnavailable("perk_override_id_invalid")
        if not isinstance(raw_key, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,95}", raw_key
        ):
            raise _ComponentUnavailable("perk_override_key_invalid")
        if raw_id in known and known[raw_id] != raw_key:
            raise _ComponentUnavailable(f"perk_override_static_conflict:{raw_id}")
        prior_id = keys_to_ids.get(raw_key)
        if prior_id is not None and prior_id != raw_id:
            raise _ComponentUnavailable(f"perk_override_key_conflict:{raw_id}")
        known[raw_id] = raw_key
        keys_to_ids[raw_key] = raw_id

    picks: list[RuntimePerkPick] = []
    for pick in calibration.picks:
        perk_key = known.get(pick.perk_id)
        if perk_key is None:
            raise _ComponentUnavailable(f"unmapped_perk_id:{pick.perk_id}")
        picks.append(
            RuntimePerkPick(
                sequence=pick.sequence,
                wave=pick.wave,
                perk_id=pick.perk_id,
                perk_key=perk_key,
                level_after=pick.level_after,
            )
        )
    normalized_levels: list[tuple[int, str, int]] = []
    for perk_id, level in calibration.levels:
        perk_key = known.get(perk_id)
        if perk_key is None:
            raise _ComponentUnavailable(f"unmapped_perk_level_id:{perk_id}")
        normalized_levels.append((perk_id, perk_key, level))
    projection = {
        "state": calibration.state,
        "picked_count": calibration.picked_count,
        "levels": [list(item) for item in normalized_levels],
        "picks": [pick.as_dict() for pick in picks],
    }
    return RuntimePerkSnapshot(
        state=calibration.state,
        picked_count=calibration.picked_count,
        levels=tuple(normalized_levels),
        picks=tuple(picks),
        fingerprint=_fingerprint(projection),
    )


def _normalize_history_tail(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
    runtime_spec: Mapping[str, Any],
) -> BattleHistoryTail:
    history_spec = runtime_spec.get("battle_history")
    if not isinstance(history_spec, Mapping):
        raise RuntimeSaveNormalizationError("battleHistory mapping is unavailable")
    capacity = _required_positive_mapping_int(history_spec, "capacity")
    raw_history = decoded.get("battleHistory")
    if not _is_sequence(raw_history):
        raise _ComponentUnavailable("battle_history_changed_shape")
    if len(raw_history) > capacity:
        raise _ComponentUnavailable("battle_history_exceeds_capacity")
    if not raw_history:
        return BattleHistoryTail(
            structural_status="empty",
            structural_reason="battle_history_empty",
            entry_count=0,
            capacity=capacity,
            identity=None,
            completed_entry_status="not_applicable",
            completed_entry_reason="battle_history_empty",
            entry=None,
        )

    latest_index = len(raw_history) - 1
    latest = _validate_history_entry_shape(
        raw_history[-1],
        history_spec,
        index=latest_index,
    )
    identity = _build_history_tail_identity(latest, mapping)
    try:
        completed = _build_completed_history_entry(
            latest,
            mapping,
            history_spec,
        )
        completed_status = "observed"
        completed_reason = ""
    except _ComponentUnavailable as exc:
        completed = None
        completed_status = "unavailable"
        completed_reason = str(exc)
    return BattleHistoryTail(
        structural_status="observed",
        structural_reason="",
        entry_count=len(raw_history),
        capacity=capacity,
        identity=identity,
        completed_entry_status=completed_status,
        completed_entry_reason=completed_reason,
        entry=completed,
    )


def _build_history_tail_identity(
    entry: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> BattleHistoryTailIdentity:
    battle_date = _normalized_dotnet_datetime(entry["battleDate"])
    projection = {
        "schema_version": HISTORY_TAIL_IDENTITY_SCHEMA_VERSION,
        "mapping_id": str(mapping.get("mapping_id") or ""),
        "battle_date": battle_date,
        "tier": entry["tier"],
        "wave": entry["wave"],
        "game_time_seconds": entry["gameTime"],
        "real_time_seconds": entry["realTime"],
        "killed_by_id": entry["killedBy"],
        "is_tournament": entry["isTournament"],
    }
    return BattleHistoryTailIdentity(
        mapping_id=str(mapping.get("mapping_id") or ""),
        battle_date=battle_date,
        tier=int(entry["tier"]),
        wave=int(entry["wave"]),
        game_time_seconds=float(entry["gameTime"]),
        real_time_seconds=float(entry["realTime"]),
        killed_by_id=int(entry["killedBy"]),
        is_tournament=bool(entry["isTournament"]),
        fingerprint=_fingerprint(projection),
    )


def _validate_history_entry_shape(
    raw_entry: Any,
    history_spec: Mapping[str, Any],
    *,
    index: int,
) -> Mapping[str, Any]:
    if not isinstance(raw_entry, Mapping):
        raise _ComponentUnavailable(f"malformed_history_entry:{index}")
    expected_class = str(history_spec.get("entry_class") or "")
    expected_fields = _history_expected_fields(history_spec)
    if set(raw_entry) != expected_fields:
        raise _ComponentUnavailable(f"history_entry_changed_shape:{index}")
    if raw_entry.get("__class__") != expected_class:
        raise _ComponentUnavailable(f"history_entry_class_changed:{index}")

    integer_fields = _string_set(history_spec.get("integer_fields"))
    boolean_fields = _string_set(history_spec.get("boolean_fields"))
    for field in expected_fields - {"__class__"}:
        value = raw_entry.get(field)
        if field in boolean_fields:
            if type(value) is not bool:
                raise _ComponentUnavailable(
                    f"history_entry_field_type_changed:{index}:{field}"
                )
            continue
        if field in integer_fields:
            if type(value) is not int:
                raise _ComponentUnavailable(
                    f"history_entry_field_type_changed:{index}:{field}"
                )
        elif type(value) is not float:
            raise _ComponentUnavailable(
                f"history_entry_field_type_changed:{index}:{field}"
            )
        if not math.isfinite(float(value)) or value < 0:
            raise _ComponentUnavailable(
                f"malformed_history_entry_value:{index}:{field}"
            )
    for positive_field in ("battleDate", "tier", "wave", "gameTime", "realTime"):
        if raw_entry.get(positive_field, 0) <= 0:
            raise _ComponentUnavailable(
                f"malformed_history_entry_value:{index}:{positive_field}"
            )
    return raw_entry


def _history_expected_fields(history_spec: Mapping[str, Any]) -> set[str]:
    fields = {"__class__"}
    sections = history_spec.get("more_stats_sections")
    if not _is_sequence(sections):
        raise RuntimeSaveNormalizationError("More Stats section mapping changed")
    for section in sections:
        if not isinstance(section, Mapping) or not _is_sequence(
            section.get("rows")
        ):
            raise RuntimeSaveNormalizationError("More Stats row mapping changed")
        for raw_row in section["rows"]:
            if not _is_sequence(raw_row) or len(raw_row) != 2:
                raise RuntimeSaveNormalizationError("More Stats row mapping changed")
            row_spec = raw_row[1]
            if isinstance(row_spec, str):
                fields.add(row_spec)
            elif isinstance(row_spec, Mapping):
                for key in ("source", "amount", "seconds", "active_percent_of"):
                    source_field = row_spec.get(key)
                    if isinstance(source_field, str) and source_field:
                        fields.add(source_field)
            else:
                raise RuntimeSaveNormalizationError(
                    "More Stats source mapping changed"
                )
    extras = history_spec.get("non_report_fields")
    if not _is_sequence(extras):
        raise RuntimeSaveNormalizationError("battleHistory non-report mapping changed")
    extra_fields = {str(field) for field in extras}
    reasons = history_spec.get("non_report_field_reasons")
    if (
        not isinstance(reasons, Mapping)
        or set(reasons) != extra_fields
        or any(not str(reason).strip() for reason in reasons.values())
    ):
        raise RuntimeSaveNormalizationError(
            "battleHistory non-report reasons changed"
        )
    fields.update(extra_fields)
    integer_fields = _string_set(history_spec.get("integer_fields"))
    boolean_fields = _string_set(history_spec.get("boolean_fields"))
    typed = integer_fields | boolean_fields
    if not typed <= fields:
        raise RuntimeSaveNormalizationError(
            "battleHistory type mapping has unknown fields"
        )
    return fields


def _build_completed_history_entry(
    entry: Mapping[str, Any],
    mapping: Mapping[str, Any],
    history_spec: Mapping[str, Any],
) -> CompletedBattleHistoryEntry:
    killed_by_id = int(entry["killedBy"])
    killed_by_ids = history_spec.get("killed_by_ids")
    killed_by = (
        killed_by_ids.get(str(killed_by_id))
        if isinstance(killed_by_ids, Mapping)
        else None
    )
    if not isinstance(killed_by, str) or not killed_by:
        raise _ComponentUnavailable(f"unmapped_killed_by_id:{killed_by_id}")

    sections: list[MoreStatsSection] = []
    raw_sections = history_spec.get("more_stats_sections") or []
    for raw_section in raw_sections:
        name = str(raw_section.get("name") or "")
        section_key = _slug(name)
        rows = tuple(
            _build_more_stats_row(
                entry,
                section_name=name,
                section_key=section_key,
                raw_row=raw_row,
                killed_by_ids=killed_by_ids,
            )
            for raw_row in raw_section.get("rows") or []
        )
        sections.append(MoreStatsSection(name=name, key=section_key, rows=rows))

    expected_row_count = _required_positive_mapping_int(
        history_spec,
        "more_stats_row_count",
    )
    row_count = sum(len(section.rows) for section in sections)
    if row_count != expected_row_count:
        raise RuntimeSaveNormalizationError(
            f"More Stats mapping has {row_count} rows, expected {expected_row_count}"
        )

    battle_date = _normalized_dotnet_datetime(entry["battleDate"])
    projection = {
        "schema_version": HISTORY_ENTRY_SCHEMA_VERSION,
        "mapping_id": str(mapping.get("mapping_id") or ""),
        "identity": {
            "battle_date": battle_date,
            "tier": entry["tier"],
            "wave": entry["wave"],
            "game_time_seconds": entry["gameTime"],
            "real_time_seconds": entry["realTime"],
            "killed_by_id": killed_by_id,
            "killed_by": killed_by,
            "is_tournament": entry["isTournament"],
        },
        "more_stats": {
            "source_method": "player_save_battle_history",
            "source_complete": True,
            "row_count": row_count,
            "sections": [section.as_dict() for section in sections],
        },
    }
    return CompletedBattleHistoryEntry(
        mapping_id=str(mapping.get("mapping_id") or ""),
        battle_date=battle_date,
        tier=int(entry["tier"]),
        wave=int(entry["wave"]),
        game_time_seconds=float(entry["gameTime"]),
        real_time_seconds=float(entry["realTime"]),
        killed_by_id=killed_by_id,
        killed_by=killed_by,
        is_tournament=bool(entry["isTournament"]),
        sections=tuple(sections),
        fingerprint=_fingerprint(projection),
    )


def _build_more_stats_row(
    entry: Mapping[str, Any],
    *,
    section_name: str,
    section_key: str,
    raw_row: Sequence[Any],
    killed_by_ids: Any,
) -> MoreStatsRow:
    label = str(raw_row[0])
    row_spec = raw_row[1]
    key = _slug(label)
    if isinstance(row_spec, str):
        source = row_spec
        value = entry[source]
        return MoreStatsRow(
            section=section_name,
            section_key=section_key,
            label=label,
            key=key,
            value_type="number",
            value=value,
            value_decimal=_decimal_text(value),
            source_fields=(source,),
            derivation="direct",
        )
    if not isinstance(row_spec, Mapping):
        raise RuntimeSaveNormalizationError("More Stats row mapping changed")

    source = row_spec.get("source")
    kind = str(row_spec.get("kind") or "number")
    if isinstance(source, str):
        value = entry[source]
        if kind == "killed_by_enum":
            enum_id = int(value)
            enum_value = (
                killed_by_ids.get(str(enum_id))
                if isinstance(killed_by_ids, Mapping)
                else None
            )
            if not isinstance(enum_value, str) or not enum_value:
                raise _ComponentUnavailable(f"unmapped_killed_by_id:{enum_id}")
            return MoreStatsRow(
                section=section_name,
                section_key=section_key,
                label=label,
                key=key,
                value_type="text",
                value=enum_value,
                value_decimal=None,
                source_fields=(source,),
                derivation="versioned_enum",
                enum_id=enum_id,
            )
        active_field = row_spec.get("active_percent_of")
        active_percent = None
        source_fields = [source]
        if isinstance(active_field, str):
            source_fields.append(active_field)
            active_percent = _percentage_text(value, entry[active_field])
        return MoreStatsRow(
            section=section_name,
            section_key=section_key,
            label=label,
            key=key,
            value_type=kind,
            value=value,
            value_decimal=_decimal_text(value),
            source_fields=tuple(source_fields),
            derivation=(
                "direct_with_active_percent"
                if active_percent is not None
                else "direct"
            ),
            active_percent_decimal=active_percent,
        )

    if row_spec.get("derive") == "per_real_hour":
        amount_field = str(row_spec.get("amount") or "")
        seconds_field = str(row_spec.get("seconds") or "")
        seconds = Decimal(str(entry[seconds_field]))
        if seconds <= 0:
            raise _ComponentUnavailable("history_real_time_is_not_positive")
        with localcontext() as context:
            context.prec = 50
            rate = Decimal(str(entry[amount_field])) * Decimal(3600) / seconds
        return MoreStatsRow(
            section=section_name,
            section_key=section_key,
            label=label,
            key=key,
            value_type="rate_per_real_hour_decimal",
            value=None,
            value_decimal=_decimal_text(rate),
            source_fields=(amount_field, seconds_field),
            derivation="amount_per_real_hour",
        )
    raise RuntimeSaveNormalizationError("unsupported More Stats derivation")


def _normalized_dotnet_datetime(value: Any) -> dict[str, Any]:
    binary = _exact_nonnegative_int(
        value,
        "battleDate",
        _ComponentUnavailable,
    )
    ticks = binary & DOTNET_TICKS_MASK
    kind_id = binary >> DOTNET_KIND_SHIFT
    if kind_id not in DOTNET_KIND_NAMES:
        raise _ComponentUnavailable(f"unsupported_battle_date_kind:{kind_id}")
    try:
        clock_time = datetime(1, 1, 1) + timedelta(microseconds=ticks // 10)
    except (OverflowError, ValueError) as exc:
        raise _ComponentUnavailable("malformed_battle_date") from exc
    if kind_id == 1:
        clock_time = clock_time.replace(tzinfo=timezone.utc)
        clock_basis = "utc"
    elif kind_id == 0:
        clock_basis = "unspecified"
    else:
        clock_basis = "local_wall_clock_without_offset"
    return {
        "kind_id": kind_id,
        "kind": DOTNET_KIND_NAMES[kind_id],
        "ticks": str(ticks),
        "clock_time": clock_time.isoformat(timespec="microseconds"),
        "clock_basis": clock_basis,
        "submicrosecond_100ns": ticks % 10,
    }


def _percentage_text(value: Any, total: Any) -> str:
    numerator = Decimal(str(value))
    denominator = Decimal(str(total))
    if denominator == 0:
        if numerator == 0:
            return "0"
        raise _ComponentUnavailable("effect_count_exceeds_zero_total")
    with localcontext() as context:
        context.prec = 50
        percent = numerator * Decimal(100) / denominator
    return _decimal_text(percent)


def _decimal_text(value: Any) -> str:
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    text = format(decimal, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _fingerprint(value: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _normalized_capture(capture: Mapping[str, Any]) -> dict[str, Any]:
    captured_at = capture.get("captured_at")
    if not isinstance(captured_at, str):
        raise RuntimeSaveNormalizationError("capture time is unavailable")
    try:
        parsed_capture = datetime.fromisoformat(captured_at)
    except ValueError as exc:
        raise RuntimeSaveNormalizationError("capture time is malformed") from exc
    if parsed_capture.tzinfo is None:
        raise RuntimeSaveNormalizationError("capture time lacks a timezone")

    source_name = str(capture.get("source_name") or "")
    source_name = re.split(r"[/\\]+", source_name)[-1]
    if not source_name:
        raise RuntimeSaveNormalizationError("capture source name is unavailable")
    source_sha256 = str(capture.get("source_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
        raise RuntimeSaveNormalizationError("capture source fingerprint is malformed")
    source_size = _exact_nonnegative_int(
        capture.get("source_size"),
        "capture source size",
        RuntimeSaveNormalizationError,
    )
    decompressed_size = _exact_nonnegative_int(
        capture.get("decompressed_size"),
        "capture decompressed size",
        RuntimeSaveNormalizationError,
    )
    container = str(capture.get("container") or "")
    if container not in {"nrbf", "gzip+nrbf"}:
        raise RuntimeSaveNormalizationError("capture container is unsupported")
    return {
        "captured_at": captured_at,
        "source_name": source_name,
        "source_sha256": source_sha256,
        "source_size": source_size,
        "container": container,
        "decompressed_size": decompressed_size,
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")


def _required_int(decoded: Mapping[str, Any], field: str) -> int:
    return _exact_int(decoded.get(field), field, RuntimeSaveNormalizationError)


def _required_nonnegative_int(decoded: Mapping[str, Any], field: str) -> int:
    return _exact_nonnegative_int(
        decoded.get(field),
        field,
        RuntimeSaveNormalizationError,
    )


def _required_bool(decoded: Mapping[str, Any], field: str) -> bool:
    value = decoded.get(field)
    if type(value) is not bool:
        raise RuntimeSaveNormalizationError(f"{field} changed type")
    return value


def _required_sequence(decoded: Mapping[str, Any], field: str) -> Sequence[Any]:
    value = decoded.get(field)
    if not _is_sequence(value):
        raise RuntimeSaveNormalizationError(f"{field} changed shape")
    return value


def _required_positive_mapping_int(mapping: Mapping[str, Any], field: str) -> int:
    value = _exact_int(mapping.get(field), field, RuntimeSaveNormalizationError)
    if value <= 0:
        raise RuntimeSaveNormalizationError(f"{field} must be positive")
    return value


def _component_nonnegative_int(value: Any, field: str) -> int:
    return _exact_nonnegative_int(value, field, _ComponentUnavailable)


def _exact_nonnegative_int(
    value: Any,
    field: str,
    error_type: type[ValueError],
) -> int:
    numeric = _exact_int(value, field, error_type)
    if numeric < 0:
        raise error_type(f"{field} is negative")
    return numeric


def _exact_int(value: Any, field: str, error_type: type[ValueError]) -> int:
    if type(value) is not int:
        raise error_type(f"{field} changed type")
    return value


def _string_set(value: Any) -> set[str]:
    if not _is_sequence(value) or any(not isinstance(item, str) for item in value):
        raise RuntimeSaveNormalizationError("runtime field-type mapping changed")
    return set(value)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


__all__ = [
    "ActiveRoundIdentity",
    "BattleHistoryTail",
    "BattleHistoryTailIdentity",
    "CompletedBattleHistoryEntry",
    "NormalizedRuntimeSave",
    "RuntimePerkCalibration",
    "RuntimePerkCalibrationPick",
    "RuntimePerkPick",
    "RuntimePerkSnapshot",
    "RuntimeSaveNormalizationError",
    "normalize_runtime_save",
    "runtime_with_perk_id_overrides",
]
