"""Fail-closed correlation of numeric save Perks with passive UI evidence.

The static exact-version mapping remains authoritative.  This module can add
an observation-only overlay for one collector session when a complete UI
timeline batch and the structurally validated save picks have exactly one
semantic assignment.  It never reads OCR text, sends input, or edits mapping
configuration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any

from core.perk_configuration import PERK_CONFIGURATION_LABELS
from core.runtime_save import (
    NormalizedRuntimeSave,
    RuntimePerkCalibrationPick,
    runtime_with_perk_id_overrides,
)


MIN_MAPPING_CONFIDENCE_PERCENT = 80.0
MAX_MAPPING_BATCH_SELECTIONS = 32
_SAFE_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,95}")
_EXACT_SELECTION_MODELS = {
    "simultaneous_batch",
    "singleton_after_pwr_max",
    "singleton_after_pwr_max_reconstructed",
}
_FAMILY_ALIASES = {
    "all_coins_bonuses": "coins_bonus",
    "boss_health_speed_tradeoff": "boss_health_tradeoff",
    "cash_wave_tradeoff": "cash_tradeoff",
    "inner_mines": "inner_land_mines",
    "max_game_speed": "game_speed",
    "spotlight_damage_bonus": "spotlight_damage",
    "tower_health_regen_tradeoff": "health_regen_tradeoff",
    "wave_on_death": "death_wave_quantity",
}
_MAPPABLE_PERK_KEYS = frozenset(PERK_CONFIGURATION_LABELS)


@dataclass(frozen=True)
class PerkIdMappingEvidence:
    """One unique cross-channel assignment suitable for an audit receipt."""

    perk_id: int
    perk_key: str
    save_pick_wave: int
    save_level_after: int
    ui_batch_sequence: int
    ui_confidence_percent: int
    evidence_fingerprint: str

    def component_payload(self, *, game_version: int) -> dict[str, Any]:
        return {
            "status": "resolved",
            "game_version": game_version,
            "perk_id": self.perk_id,
            "perk_key": self.perk_key,
            "save_pick_wave": self.save_pick_wave,
            "save_level_after": self.save_level_after,
            "ui_batch_sequence": self.ui_batch_sequence,
            "ui_confidence_percent": self.ui_confidence_percent,
            "evidence_fingerprint": self.evidence_fingerprint,
            "evidence_semantics": "unique_exact_wave_cross_channel",
            "mapping_scope": "collector_session_only",
        }


@dataclass(frozen=True)
class PerkIdResolution:
    """Result of one pure correlation attempt."""

    runtime: NormalizedRuntimeSave
    overrides: Mapping[int, str]
    learned: tuple[PerkIdMappingEvidence, ...]
    conflicts: tuple[int, ...]
    unresolved_ids: tuple[int, ...]


@dataclass(frozen=True)
class _UiSelection:
    perk_key: str
    confidence_percent: float
    change: str


@dataclass(frozen=True)
class _UiBatch:
    sequence: int
    wave: int
    observed_at: datetime
    selections: tuple[_UiSelection, ...]


def resolve_runtime_perk_ids(
    runtime: NormalizedRuntimeSave,
    timeline_batches: Sequence[Mapping[str, Any]],
    overrides: Mapping[int, str],
) -> PerkIdResolution:
    """Apply only uniquely proven session mappings to ``runtime``."""

    calibration = runtime.perk_calibration
    if calibration is None:
        return PerkIdResolution(runtime, dict(overrides), (), (), ())

    static = dict(calibration.known_ids)
    unresolved_without_overlay = tuple(
        sorted(
            {pick.perk_id for pick in calibration.picks if pick.perk_id not in static}
        )
    )
    if len(static) != len(calibration.known_ids) or len(set(static.values())) != len(
        static
    ):
        return PerkIdResolution(
            runtime,
            {},
            (),
            (),
            unresolved_without_overlay,
        )

    learned_map: dict[int, str] = {}
    used_override_keys = set(static.values())
    for perk_id, perk_key in overrides.items():
        if (
            type(perk_id) is not int
            or perk_id < 0
            or not isinstance(perk_key, str)
            or _SAFE_KEY_RE.fullmatch(perk_key) is None
            or perk_id in static
            or perk_key in used_override_keys
        ):
            continue
        learned_map[perk_id] = perk_key
        used_override_keys.add(perk_key)
    effective = {**static, **learned_map}
    assigned_keys = {key: perk_id for perk_id, key in effective.items()}
    constraints: dict[int, set[str]] = {}
    support: dict[
        tuple[int, str], tuple[RuntimePerkCalibrationPick, _UiBatch, float]
    ] = {}
    conflicts: set[int] = set()

    picks_by_wave: dict[int, list[RuntimePerkCalibrationPick]] = {}
    for pick in calibration.picks:
        picks_by_wave.setdefault(pick.wave, []).append(pick)

    for raw_batch in timeline_batches:
        batch = _normalize_ui_batch(raw_batch)
        if batch is None:
            continue
        save_picks = picks_by_wave.get(batch.wave)
        if not save_picks or len(save_picks) != len(batch.selections):
            continue
        if len({pick.perk_id for pick in save_picks}) != len(save_picks):
            continue
        remaining = list(batch.selections)
        unresolved: list[RuntimePerkCalibrationPick] = []
        batch_invalid = False
        for pick in save_picks:
            perk_key = effective.get(pick.perk_id)
            if perk_key is None:
                unresolved.append(pick)
                continue
            match_index = next(
                (
                    index
                    for index, selection in enumerate(remaining)
                    if selection.perk_key == perk_key
                ),
                None,
            )
            if match_index is None:
                if pick.perk_id in learned_map:
                    conflicts.add(pick.perk_id)
                batch_invalid = True
                break
            remaining.pop(match_index)
        if batch_invalid or len(unresolved) != len(remaining) or not unresolved:
            continue
        candidate_keys = {
            selection.perk_key
            for selection in remaining
            if selection.perk_key not in assigned_keys
        }
        if len(candidate_keys) != len(remaining):
            continue
        for pick in unresolved:
            prior = constraints.get(pick.perk_id)
            constraints[pick.perk_id] = (
                set(candidate_keys)
                if prior is None
                else prior.intersection(candidate_keys)
            )
            for selection in remaining:
                support[(pick.perk_id, selection.perk_key)] = (
                    pick,
                    batch,
                    selection.confidence_percent,
                )

    for perk_id in conflicts:
        learned_map.pop(perk_id, None)
    effective = {**static, **learned_map}
    used_keys = set(effective.values())
    pending = {
        perk_id: {key for key in keys if key not in used_keys}
        for perk_id, keys in constraints.items()
        if perk_id not in effective and keys
    }
    learned: list[PerkIdMappingEvidence] = []
    while True:
        singleton_pairs = [
            (perk_id, next(iter(keys)))
            for perk_id, keys in sorted(pending.items())
            if len(keys) == 1
        ]
        key_counts: dict[str, int] = {}
        for _perk_id, key in singleton_pairs:
            key_counts[key] = key_counts.get(key, 0) + 1
        accepted = [
            (perk_id, key)
            for perk_id, key in singleton_pairs
            if key_counts[key] == 1 and key not in used_keys
        ]
        if not accepted:
            break
        for perk_id, perk_key in accepted:
            evidence = support.get((perk_id, perk_key))
            if evidence is None:
                continue
            pick, batch, confidence = evidence
            learned_map[perk_id] = perk_key
            effective[perk_id] = perk_key
            used_keys.add(perk_key)
            learned.append(
                _mapping_evidence(
                    perk_id,
                    perk_key,
                    pick,
                    batch,
                    confidence,
                )
            )
            pending.pop(perk_id, None)
        for keys in pending.values():
            keys.difference_update(used_keys)

    resolved_runtime = runtime_with_perk_id_overrides(runtime, learned_map)
    unresolved = sorted(
        {
            pick.perk_id
            for pick in calibration.picks
            if pick.perk_id not in static and pick.perk_id not in learned_map
        }
    )
    return PerkIdResolution(
        resolved_runtime,
        dict(sorted(learned_map.items())),
        tuple(learned),
        tuple(sorted(conflicts)),
        tuple(unresolved),
    )


def timeline_family_to_perk_key(value: Any) -> str | None:
    """Translate an accepted timeline family to save/config vocabulary."""

    family = str(value or "").strip().lower()
    family = _FAMILY_ALIASES.get(family, family)
    if family not in _MAPPABLE_PERK_KEYS or _SAFE_KEY_RE.fullmatch(family) is None:
        return None
    return family


def normalize_timeline_mapping_batch(value: Any) -> dict[str, Any] | None:
    """Return the strict, privacy-safe batch allowed onto the worker queue."""

    batch = _normalize_ui_batch(value)
    if batch is None:
        return None
    return {
        "schema_version": 1,
        "sequence": batch.sequence,
        "scheduled_wave": batch.wave,
        "scheduled_waves": [batch.wave],
        "boundary_coverage": "complete",
        "selection_model": str(value["selection_model"]),
        "observed_at": batch.observed_at.isoformat(),
        "selections": [
            {
                "family": selection.perk_key,
                "confidence_percent": selection.confidence_percent,
                "change": selection.change,
            }
            for selection in batch.selections
        ],
    }


def _normalize_ui_batch(value: Any) -> _UiBatch | None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "sequence",
        "scheduled_wave",
        "scheduled_waves",
        "boundary_coverage",
        "selection_model",
        "observed_at",
        "selections",
    }:
        return None
    if value.get("schema_version") != 1:
        return None
    sequence = value.get("sequence")
    wave = value.get("scheduled_wave")
    if type(sequence) is not int or sequence < 1 or type(wave) is not int or wave < 1:
        return None
    scheduled_waves = value.get("scheduled_waves")
    if (
        not isinstance(scheduled_waves, Sequence)
        or isinstance(
            scheduled_waves,
            (str, bytes, bytearray),
        )
        or list(scheduled_waves) != [wave]
    ):
        return None
    if value.get("boundary_coverage") != "complete":
        return None
    if value.get("selection_model") not in _EXACT_SELECTION_MODELS:
        return None
    observed_at = _aware_datetime(value.get("observed_at"))
    if observed_at is None:
        return None
    raw_selections = value.get("selections")
    if (
        not isinstance(raw_selections, Sequence)
        or isinstance(
            raw_selections,
            (str, bytes, bytearray),
        )
        or not (1 <= len(raw_selections) <= MAX_MAPPING_BATCH_SELECTIONS)
    ):
        return None
    selections: list[_UiSelection] = []
    for raw in raw_selections:
        if not isinstance(raw, Mapping) or set(raw) != {
            "family",
            "confidence_percent",
            "change",
        }:
            return None
        if raw.get("change") not in {"added", "level_changed"}:
            return None
        perk_key = timeline_family_to_perk_key(raw.get("family"))
        confidence = raw.get("confidence_percent")
        if (
            perk_key is None
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not (MIN_MAPPING_CONFIDENCE_PERCENT <= float(confidence) <= 100.0)
        ):
            return None
        selections.append(
            _UiSelection(
                perk_key,
                float(confidence),
                str(raw["change"]),
            )
        )
    if len({selection.perk_key for selection in selections}) != len(selections):
        return None
    return _UiBatch(sequence, wave, observed_at, tuple(selections))


def _aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _mapping_evidence(
    perk_id: int,
    perk_key: str,
    pick: RuntimePerkCalibrationPick,
    batch: _UiBatch,
    confidence: float,
) -> PerkIdMappingEvidence:
    projection = {
        "perk_id": perk_id,
        "perk_key": perk_key,
        "save_pick_wave": pick.wave,
        "save_level_after": pick.level_after,
        "ui_batch_sequence": batch.sequence,
        "ui_observed_at": batch.observed_at.isoformat(),
        "ui_confidence_percent": round(confidence),
        "semantics": "unique_exact_wave_cross_channel",
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return PerkIdMappingEvidence(
        perk_id=perk_id,
        perk_key=perk_key,
        save_pick_wave=pick.wave,
        save_level_after=pick.level_after,
        ui_batch_sequence=batch.sequence,
        ui_confidence_percent=round(confidence),
        evidence_fingerprint=fingerprint,
    )


__all__ = [
    "MIN_MAPPING_CONFIDENCE_PERCENT",
    "PerkIdMappingEvidence",
    "PerkIdResolution",
    "normalize_timeline_mapping_batch",
    "resolve_runtime_perk_ids",
    "timeline_family_to_perk_key",
]
