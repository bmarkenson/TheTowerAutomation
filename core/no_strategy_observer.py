"""Passive evidence collection for battles run without a strategy profile.

``No Strategy`` deliberately supplies no configured intent.  This module keeps
observed values separate from ``run_configuration`` so a familiar Tier or
loadout can never be mistaken for a profile the operator selected.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import cv2
import numpy as np

from core.auto_pick_perks import measure_auto_pick_perks
from core.battle_classification import dissonance_subtype_from_preset_label
from core.damage_adjuster import read_damage_adjuster
from core.label_tapper import is_visible
from core.module_icon_index import identify_equipped_ancestral_modules
from core.player_save_temporal import (
    PlayerSaveTemporalClass,
    ROUND_INVARIANT_ATTACHMENT_CHECKS,
    RunningAttachmentSaveFact,
    RunningAttachmentSaveObservations,
    attachment_temporal_class,
    canonical_temporal_value,
)
from core.target_priority import detect_target_priority_order
from core.upgrade_box_detector import detect_visible_boxes
from core.gc_preflight import merge_ultimate_weapon_observations
from core.workshop_preset import measure_preset_slot_selection
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
Clock = Callable[[], datetime]

# The modifier badge is fixed immediately to the right of the Tier label.  A
# narrow fixed region prevents purple battle effects elsewhere in the frame
# from becoming identity evidence.  Its white icon identifies which system is
# disabled; purple alone proves only the Dissonance family.
DISSONANCE_BADGE_REGION = (680, 985, 80, 80)
# Compatibility for callers that imported the original sword-only name.
ATTACK_DISSONANCE_BADGE_REGION = DISSONANCE_BADGE_REGION
_DISSONANCE_BADGE_ICON_REGION = (680, 998, 46, 48)
_PURPLE_LOWER = np.array((125, 70, 90), dtype=np.uint8)
_PURPLE_UPPER = np.array((165, 255, 255), dtype=np.uint8)
_MIN_BADGE_PURPLE_PIXELS = 500
_BADGE_WHITE_LOWER = np.array((0, 0, 175), dtype=np.uint8)
_BADGE_WHITE_UPPER = np.array((179, 100, 255), dtype=np.uint8)
_REFERENCE_ICON_LOWER = np.array((0, 10, 80), dtype=np.uint8)
_REFERENCE_ICON_UPPER = np.array((179, 255, 255), dtype=np.uint8)
_MIN_BADGE_ICON_AREA = 100.0
_MAX_BADGE_ICON_SHAPE_DISTANCE = 0.20
_MIN_BADGE_ICON_SHAPE_MARGIN = 0.05
_SUPPORTED_DISSONANCE_SUBTYPES = frozenset({"Attack", "Utility"})
_ROOT = Path(__file__).resolve().parents[1]
_DISSONANCE_ICON_REFERENCES = {
    "Attack": _ROOT / "assets/match_templates/navigation/goto_attack.png",
    "Defense": _ROOT / "assets/match_templates/navigation/goto_defense.png",
    "Utility": _ROOT / "assets/match_templates/navigation/goto_utility.png",
    "Ultimate Weapons": _ROOT / "assets/match_templates/navigation/goto_uw.png",
}

_CARD_SLOTS = tuple((12 + 213 * index, 371, 210, 98) for index in range(5))
_WORKSHOP_SLOTS = tuple((12 + 213 * index, 185, 210, 98) for index in range(5))
_BOT_SLOTS = (
    (18, 496, 347, 98),
    (366, 496, 347, 98),
    (713, 496, 347, 98),
)
_GUARDIAN_SECONDARIES = {
    "GUARDIAN_FETCH_EQUIPPED": "Fetch",
    "GUARDIAN_SUMMON_EQUIPPED": "Summon",
    "GUARDIAN_SCOUT_EQUIPPED": "Scout",
    "GUARDIAN_ATTACK_EQUIPPED": "Attack",
    "GUARDIAN_ALLY_EQUIPPED": "Ally",
}

OBSERVED_FIELDS = (
    "run_identity",
    "cards_deck",
    "workshop_preset",
    "free_upgrade_locks",
    "bots_preset",
    "guardian_chips",
    "modules",
    "target_priority",
    "damage_slider",
    "auto_pick_perks",
    "perk_first_choice",
    "perk_bans",
    "perk_auto_pick_order",
    "ultimate_weapons",
)
_RESOLVED_STATUSES = frozenset(
    {"observed", "evidence_captured", "unavailable"}
)


def _largest_external_contour(mask: Frame) -> Optional[Frame]:
    contours, _hierarchy = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


@lru_cache(maxsize=1)
def _dissonance_icon_reference_contours() -> dict[str, Frame]:
    references: dict[str, Frame] = {}
    for subtype, path in _DISSONANCE_ICON_REFERENCES.items():
        icon = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if icon is None:
            continue
        hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
        contour = _largest_external_contour(
            cv2.inRange(hsv, _REFERENCE_ICON_LOWER, _REFERENCE_ICON_UPPER)
        )
        if (
            contour is not None
            and cv2.contourArea(contour) >= _MIN_BADGE_ICON_AREA
        ):
            references[subtype] = contour
    return references


def detect_dissonance_badge(frame: Optional[Frame]) -> dict[str, Any]:
    """Return localized Dissonance family and subtype evidence without input."""

    x, y, width, height = DISSONANCE_BADGE_REGION
    icon_x, icon_y, icon_width, icon_height = _DISSONANCE_BADGE_ICON_REGION
    if (
        frame is None
        or not isinstance(frame, np.ndarray)
        or frame.ndim != 3
        or y + height > frame.shape[0]
        or x + width > frame.shape[1]
        or icon_y + icon_height > frame.shape[0]
        or icon_x + icon_width > frame.shape[1]
    ):
        return {
            "observed": False,
            "subtype": None,
            "label": None,
            "purple_pixels": 0,
            "region": list(DISSONANCE_BADGE_REGION),
        }
    crop = frame[y : y + height, x : x + width]
    purple_pixels = int(
        cv2.countNonZero(
            cv2.inRange(
                cv2.cvtColor(crop, cv2.COLOR_BGR2HSV),
                _PURPLE_LOWER,
                _PURPLE_UPPER,
            )
        )
    )
    observed = purple_pixels >= _MIN_BADGE_PURPLE_PIXELS
    evidence: dict[str, Any] = {
        "observed": observed,
        "subtype": None,
        "label": "Dissonance" if observed else None,
        "purple_pixels": purple_pixels,
        "minimum_purple_pixels": _MIN_BADGE_PURPLE_PIXELS,
        "region": list(DISSONANCE_BADGE_REGION),
    }
    if not observed:
        return evidence

    icon_crop = frame[
        icon_y : icon_y + icon_height,
        icon_x : icon_x + icon_width,
    ]
    icon_mask = cv2.inRange(
        cv2.cvtColor(icon_crop, cv2.COLOR_BGR2HSV),
        _BADGE_WHITE_LOWER,
        _BADGE_WHITE_UPPER,
    )
    icon_contour = _largest_external_contour(icon_mask)
    icon_area = (
        float(cv2.contourArea(icon_contour))
        if icon_contour is not None
        else 0.0
    )
    evidence["icon_white_pixels"] = int(cv2.countNonZero(icon_mask))
    evidence["icon_contour_area"] = round(icon_area, 3)
    if icon_contour is None or icon_area < _MIN_BADGE_ICON_AREA:
        return evidence

    scores = {
        subtype: float(
            cv2.matchShapes(
                icon_contour,
                reference,
                cv2.CONTOURS_MATCH_I1,
                0.0,
            )
        )
        for subtype, reference in _dissonance_icon_reference_contours().items()
    }
    evidence["icon_shape_scores"] = {
        subtype: round(score, 6) for subtype, score in sorted(scores.items())
    }
    evidence["maximum_shape_distance"] = _MAX_BADGE_ICON_SHAPE_DISTANCE
    evidence["minimum_shape_margin"] = _MIN_BADGE_ICON_SHAPE_MARGIN
    if not scores:
        return evidence
    ranked = sorted(scores.items(), key=lambda item: item[1])
    subtype, best_score = ranked[0]
    margin = ranked[1][1] - best_score if len(ranked) > 1 else float("inf")
    evidence["shape_margin"] = round(margin, 6)
    if (
        subtype in _SUPPORTED_DISSONANCE_SUBTYPES
        and best_score <= _MAX_BADGE_ICON_SHAPE_DISTANCE
        and margin >= _MIN_BADGE_ICON_SHAPE_MARGIN
    ):
        evidence["subtype"] = subtype
        evidence["label"] = f"{subtype} Dissonance"
    return evidence


def detect_attack_dissonance_badge(frame: Optional[Frame]) -> dict[str, Any]:
    """Return evidence only when the localized Dissonance icon is Attack."""

    evidence = detect_dissonance_badge(frame)
    return {
        **evidence,
        "observed": bool(
            evidence.get("observed") and evidence.get("subtype") == "Attack"
        ),
    }


class NoStrategyRunObserver:
    """Accumulate actual-value evidence from frames the runtime already sees."""

    def __init__(self, *, clock: Optional[Clock] = None) -> None:
        self._clock = clock or (lambda: datetime.now().astimezone())
        self.reset()

    def reset(self) -> None:
        self._started_at = self._timestamp()
        self._fields: dict[str, dict[str, Any]] = {
            name: {
                "status": "not_observed",
                "value": None,
                "source": None,
                "phase": None,
                "observed_at": None,
            }
            for name in OBSERVED_FIELDS
        }
        self._round_invariant_claims: dict[str, tuple[str, str]] = {}
        self._round_invariant_conflicts: set[str] = set()
        self._attachment_claim_fingerprint: Optional[str] = None

    def restore_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        """Restore a persisted unfinished observation after a process reload."""

        if snapshot.get("collection_mode") != "no_strategy_observation":
            raise ValueError("not a No Strategy observation snapshot")
        if snapshot.get("finalized") is True:
            raise ValueError("cannot restore a finalized observation snapshot")
        fields = snapshot.get("fields")
        if not isinstance(fields, Mapping):
            raise ValueError("No Strategy observation fields are missing")
        restored: dict[str, dict[str, Any]] = {}
        for name in OBSERVED_FIELDS:
            field = fields.get(name)
            if not isinstance(field, Mapping):
                raise ValueError(f"No Strategy observation field {name!r} is missing")
            status = str(field.get("status") or "")
            if status not in {
                "not_observed",
                "observed",
                "evidence_captured",
                "unavailable",
            }:
                raise ValueError(
                    f"No Strategy observation field {name!r} has invalid status"
                )
            restored[name] = deepcopy(dict(field))
        started_at = str(snapshot.get("started_at") or "").strip()
        if not started_at:
            raise ValueError("No Strategy observation start time is missing")
        self._started_at = started_at
        self._fields = restored
        self._round_invariant_claims = {}
        self._round_invariant_conflicts = set()
        self._attachment_claim_fingerprint = None
        for name in ROUND_INVARIANT_ATTACHMENT_CHECKS:
            field = restored.get(name)
            if not isinstance(field, Mapping):
                continue
            if (
                field.get("status") == "unavailable"
                and field.get("reason") == "same_round_invariant_conflict"
            ):
                self._round_invariant_conflicts.add(name)
                continue
            provenance = field.get("provenance")
            temporal = (
                provenance.get("temporal")
                if isinstance(provenance, Mapping)
                else None
            )
            claim = (
                str(temporal.get("claim_fingerprint") or "").strip()
                if isinstance(temporal, Mapping)
                else ""
            )
            if field.get("status") == "observed" and claim:
                if self._attachment_claim_fingerprint in {None, claim}:
                    self._attachment_claim_fingerprint = claim
                self._round_invariant_claims[name] = (
                    claim,
                    _canonical_invariant_value(name, field.get("value")),
                )

    def observe(
        self,
        frame: Frame,
        detection: Mapping[str, Any],
        *,
        phase: str = "in_battle",
    ) -> None:
        """Inspect one already-captured frame; never navigate or send input."""

        state = str(detection.get("state") or "UNKNOWN")
        menu = str(detection.get("menu") or "")
        secondary = {str(value) for value in detection.get("secondary_states") or ()}

        if state == "RUNNING":
            badge = detect_dissonance_badge(frame)
            if badge["observed"]:
                subtype = str(badge.get("subtype") or "").strip() or None
                label = str(badge.get("label") or "Dissonance")
                source_subtype = (
                    subtype.casefold().replace(" ", "_") if subtype else "unknown"
                )
                current_identity = self._fields["run_identity"].get("value")
                current_subtype = (
                    str(current_identity.get("subtype") or "").strip()
                    if isinstance(current_identity, Mapping)
                    else ""
                )
                subtype_conflicts = bool(
                    subtype and current_subtype and subtype != current_subtype
                )
                if not subtype_conflicts and (
                    subtype is not None or not current_subtype
                ):
                    self._set(
                        "run_identity",
                        {
                            "family": "Dissonance",
                            "subtype": subtype,
                            "label": label,
                            "signals": {"tier_badge": badge},
                        },
                        source=f"tier_{source_subtype}_dissonance_badge",
                        phase=phase,
                        confidence="high" if subtype else "medium",
                    )
                if (
                    subtype == "Attack"
                    and not subtype_conflicts
                    and not self.is_resolved("damage_slider")
                ):
                    self.record_unavailable(
                        "damage_slider",
                        reason="Attack menu disabled by Attack Dissonance",
                        source="attack_dissonance_menu_constraint",
                        phase=phase,
                    )
            if menu == "UW_MENU":
                self._observe_ultimate_weapons(frame, phase)

        if state == "CARDS":
            self._observe_selected_slot("cards_deck", frame, _CARD_SLOTS, phase)
        elif state == "WORKSHOP":
            preset = self._observe_selected_slot(
                "workshop_preset", frame, _WORKSHOP_SLOTS, phase
            )
            preset_label = str((preset or {}).get("label") or "").casefold()
            dissonance_subtype = dissonance_subtype_from_preset_label(preset_label)
            current_identity = self._fields["run_identity"].get("value")
            current_family = (
                str(current_identity.get("family") or "").strip().casefold()
                if isinstance(current_identity, Mapping)
                else ""
            )
            current_subtype = (
                str(current_identity.get("subtype") or "").strip()
                if isinstance(current_identity, Mapping)
                else ""
            )
            if (
                phase == "post_run_home"
                and dissonance_subtype
                and current_family in {"", "dissonance"}
                and not current_subtype
            ):
                self._set(
                    "run_identity",
                    {
                        "family": "Dissonance",
                        "subtype": dissonance_subtype,
                        "label": f"{dissonance_subtype} Dissonance",
                        "signals": {"post_run_workshop_preset": preset},
                    },
                    source="post_run_workshop_preset_selected_border",
                    phase=phase,
                    confidence="high",
                )
        elif state == "EVENT" and "EVENT_BOTS_SCREEN" in secondary:
            self._observe_selected_slot("bots_preset", frame, _BOT_SLOTS, phase)
        elif state == "GUILD" and "GUILD_GUARDIAN_SCREEN" in secondary:
            chips = [
                label
                for signal, label in _GUARDIAN_SECONDARIES.items()
                if signal in secondary
            ]
            if chips:
                self._set(
                    "guardian_chips",
                    chips,
                    source="guardian_equipped_templates",
                    phase=phase,
                    confidence="high",
                )
        elif state == "MODULES":
            self._observe_modules(frame, phase)
        elif state == "TARGET_PRIORITY":
            try:
                order = detect_target_priority_order(frame)
            except (TypeError, ValueError):
                pass
            else:
                self._set(
                    "target_priority",
                    order,
                    source="target_priority_ocr",
                    phase=phase,
                    confidence="high",
                )
        elif state == "DAMAGE_ADJUSTER":
            reading = read_damage_adjuster(frame)
            if reading.visible and reading.mode and reading.percentage:
                self._set(
                    "damage_slider",
                    {
                        "mode": reading.mode,
                        "percentage": reading.percentage,
                        "ocr_confidence": reading.ocr_confidence,
                        "panel_confidence": reading.panel_confidence,
                    },
                    source="damage_adjuster_ocr",
                    phase=phase,
                    confidence="high",
                )
        elif state == "PERKS" and is_visible(
            "indicators.perks_panel", screenshot=frame
        ):
            evidence = measure_auto_pick_perks(frame)
            if evidence.valid_region:
                self._set(
                    "auto_pick_perks",
                    {
                        "enabled": evidence.enabled,
                        "green_pixels": evidence.green_pixels,
                    },
                    source="in_battle_perks_panel",
                    phase=phase,
                    confidence="high" if evidence.enabled else "medium",
                )

    def record_player_save_observations(
        self,
        observations: RunningAttachmentSaveObservations,
    ) -> tuple[str, ...]:
        """Merge facts only after continuity binds their final run scope."""

        if not isinstance(observations, RunningAttachmentSaveObservations):
            raise TypeError("typed active-attachment observations are required")
        binding = observations.binding
        if not binding.final:
            raise ValueError("active-attachment observations lack a final scope")
        claim_fingerprint = binding.claim_fingerprint
        if self._attachment_claim_fingerprint not in {
            None,
            claim_fingerprint,
        }:
            raise ValueError("active-attachment temporal binding changed")

        facts = {fact.check_id: fact for fact in observations.facts}

        def fact_for(check_id: str) -> Optional[RunningAttachmentSaveFact]:
            fact = facts.get(check_id)
            if fact is None:
                return None
            if fact.temporal_class is not attachment_temporal_class(check_id):
                raise ValueError(
                    f"invalid temporal class for save check {check_id!r}"
                )
            return fact

        applied: list[str] = []
        direct_checks = {
            "cards_deck": "cards_deck",
            "workshop_preset": "workshop_preset",
            "free_upgrade_locks": "free_upgrade_locks",
            "bots_preset": "bots_preset",
            "guardian_chips": "guardian_chips",
            "modules": "modules",
            "target_priority": "target_priority",
            "auto_pick_perks": "auto_pick_perks",
            "perk_first_choice": "perk_first_choice",
            "perk_bans": "perk_bans",
            "perk_auto_pick_order": "perk_auto_pick_order",
        }
        for field, check_id in direct_checks.items():
            fact = fact_for(check_id)
            if fact is None:
                continue
            value = fact.copied_value()
            if field in {"cards_deck", "workshop_preset", "bots_preset"}:
                value = {"label": value}
            elif field == "auto_pick_perks":
                value = {"enabled": value}
            self._merge_player_save_fact(
                field=field,
                value=value,
                fact=fact,
                observations=observations,
            )
            applied.append(field)

        ultimate_check_ids = (
            "ultimate_weapon_primaries",
            "poison_swamp_stun",
            "spotlight_missiles",
        )
        ultimate_facts = tuple(
            fact_for(check_id) for check_id in ultimate_check_ids
        )
        if all(fact is not None for fact in ultimate_facts):
            primaries = ultimate_facts[0].copied_value()
            poison_stun = ultimate_facts[1].copied_value()
            spotlight_missiles = ultimate_facts[2].copied_value()
            if isinstance(primaries, Mapping):
                ultimate_weapons = {
                    str(weapon): dict(toggles)
                    for weapon, toggles in primaries.items()
                    if isinstance(toggles, Mapping)
                }
                required_components_present = bool(
                    len(ultimate_weapons) == len(primaries)
                    and "Poison Swamp" in ultimate_weapons
                    and "Spotlight" in ultimate_weapons
                )
                if required_components_present:
                    ultimate_weapons["Poison Swamp"]["stun"] = poison_stun
                    ultimate_weapons["Spotlight"]["missiles"] = (
                        spotlight_missiles
                    )
                    temporal = observations.binding.redacted()
                    temporal["temporal_class"] = (
                        PlayerSaveTemporalClass.CURRENT_CONFIGURATION.value
                    )
                    temporal["save_checks"] = list(ultimate_check_ids)
                    self._set(
                        "ultimate_weapons",
                        ultimate_weapons,
                        source="guarded_active_attachment_player_save",
                        phase="in_battle_attachment_save",
                        confidence="high",
                        observed_at=binding.captured_at,
                        provenance={
                            "mapping_id": binding.mapping_id,
                            "save_checks": list(ultimate_check_ids),
                            "temporal": temporal,
                        },
                    )
                    applied.append("ultimate_weapons")
        if applied:
            self._attachment_claim_fingerprint = claim_fingerprint
        return tuple(applied)

    def _merge_player_save_fact(
        self,
        *,
        field: str,
        value: Any,
        fact: RunningAttachmentSaveFact,
        observations: RunningAttachmentSaveObservations,
    ) -> None:
        binding = observations.binding
        temporal = observations.redacted_provenance(fact)
        provenance = {
            "mapping_id": binding.mapping_id,
            "save_checks": [fact.check_id],
            "temporal": temporal,
        }
        if fact.temporal_class is PlayerSaveTemporalClass.ROUND_INVARIANT:
            claim = binding.claim_fingerprint
            canonical = _canonical_invariant_value(field, value)
            existing = self._round_invariant_claims.get(field)
            if field in self._round_invariant_conflicts:
                return
            if existing is not None and existing[0] != claim:
                raise ValueError("round-invariant temporal binding changed")
            if existing is not None and existing[1] != canonical:
                self._record_round_invariant_conflict(
                    field,
                    temporal=temporal,
                )
                return
            current = self._fields[field]
            if (
                existing is None
                and _authoritative_ui_invariant(field, current)
                and _canonical_invariant_value(field, current.get("value"))
                != canonical
            ):
                self._record_round_invariant_conflict(
                    field,
                    temporal=temporal,
                )
                return
            self._round_invariant_claims[field] = (claim, canonical)

        self._set(
            field,
            value,
            source="guarded_active_attachment_player_save",
            phase="in_battle_attachment_save",
            confidence="high",
            observed_at=binding.captured_at,
            provenance=provenance,
            temporal_merge=True,
        )

    def _record_round_invariant_conflict(
        self,
        field: str,
        *,
        temporal: Mapping[str, Any],
    ) -> None:
        self._round_invariant_conflicts.add(field)
        self._fields[field] = {
            "status": "unavailable",
            "value": None,
            "reason": "same_round_invariant_conflict",
            "source": "temporal_authority",
            "phase": "in_battle_attachment_save",
            "confidence": "high",
            "observed_at": self._timestamp(),
            "provenance": {"temporal": deepcopy(dict(temporal))},
        }

    def is_resolved(self, field: str) -> bool:
        """Return whether one observation has a terminal evidence status."""

        if field not in self._fields:
            raise ValueError(f"unknown No Strategy observation field {field!r}")
        return str(self._fields[field].get("status") or "") in _RESOLVED_STATUSES

    def unresolved_fields(
        self,
        candidates: Optional[set[str] | frozenset[str]] = None,
    ) -> set[str]:
        """Return unresolved fields, optionally restricted to one route."""

        selected = set(OBSERVED_FIELDS if candidates is None else candidates)
        unknown = selected.difference(self._fields)
        if unknown:
            raise ValueError(
                "unknown No Strategy observation fields: "
                + ", ".join(sorted(unknown))
            )
        return {field for field in selected if not self.is_resolved(field)}

    def record_post_run_value(
        self,
        field: str,
        value: Any,
        *,
        source: str,
        confidence: str = "high",
        observed_at: Optional[str] = None,
    ) -> None:
        """Attach a Home-only observation made after the battle completed."""

        if field not in self._fields:
            raise ValueError(f"unknown No Strategy observation field {field!r}")
        self._set(
            field,
            value,
            source=source,
            phase="post_run_home",
            confidence=confidence,
            observed_at=observed_at,
        )

    def record_post_run_evidence(
        self,
        field: str,
        value: Any,
        *,
        source: str,
        confidence: str = "uninterpreted",
        observed_at: Optional[str] = None,
    ) -> None:
        """Attach complete raw evidence when no authoritative parser exists yet."""

        if field not in self._fields:
            raise ValueError(f"unknown No Strategy observation field {field!r}")
        if (
            field in ROUND_INVARIANT_ATTACHMENT_CHECKS
            and (
                field in self._round_invariant_claims
                or field in self._round_invariant_conflicts
            )
        ):
            return
        self._fields[field] = {
            "status": "evidence_captured",
            "value": value,
            "source": source,
            "phase": "post_run_home",
            "confidence": confidence,
            "observed_at": observed_at or self._timestamp(),
        }

    def record_unavailable(
        self,
        field: str,
        *,
        reason: str,
        source: str,
        phase: str,
        observed_at: Optional[str] = None,
    ) -> None:
        """Resolve a field whose control is authoritatively inaccessible."""

        if field not in self._fields:
            raise ValueError(f"unknown No Strategy observation field {field!r}")
        if field in self._round_invariant_conflicts:
            return
        if field in self._round_invariant_claims:
            return
        self._fields[field] = {
            "status": "unavailable",
            "value": None,
            "reason": str(reason),
            "source": source,
            "phase": phase,
            "confidence": "high",
            "observed_at": observed_at or self._timestamp(),
        }

    def snapshot(self, *, finalized: bool = False) -> dict[str, Any]:
        observed = sum(
            field["status"] == "observed" for field in self._fields.values()
        )
        evidence_captured = sum(
            field["status"] == "evidence_captured"
            for field in self._fields.values()
        )
        unavailable = sum(
            field["status"] == "unavailable" for field in self._fields.values()
        )
        return {
            "schema_version": 2,
            "collection_mode": "no_strategy_observation",
            "started_at": self._started_at,
            "finalized_at": self._timestamp() if finalized else None,
            "finalized": bool(finalized),
            "coverage": {
                "observed": observed,
                "evidence_captured": evidence_captured,
                "unavailable": unavailable,
                "total": len(self._fields),
                "complete": (
                    observed + evidence_captured + unavailable == len(self._fields)
                ),
            },
            "fields": {name: dict(value) for name, value in self._fields.items()},
        }

    def _observe_selected_slot(
        self,
        field: str,
        frame: Frame,
        regions: tuple[tuple[int, int, int, int], ...],
        phase: str,
    ) -> Optional[dict[str, Any]]:
        selected = []
        for index, region in enumerate(regions, start=1):
            evidence = measure_preset_slot_selection(frame, region)
            if evidence.selected:
                selected.append((index, region, evidence))
        if len(selected) != 1:
            return None
        index, (x, y, width, height), evidence = selected[0]
        # The luminous selected border confuses Tesseract on otherwise clear
        # short preset names. Keep selection measurement on the full region,
        # but OCR only the stable interior label.
        inset = 15
        label_crop = frame[
            y + inset : y + height - inset,
            x + inset : x + width - inset,
        ]
        label, ocr_confidence = ocr_text_and_conf(label_crop, psm=7)
        normalized = " ".join(str(label or "").split())
        value = {
            "slot": index,
            "label": normalized if ocr_confidence >= 60.0 else None,
            "label_ocr_confidence": ocr_confidence,
            "green_pixels": evidence.green_pixels,
            "cyan_pixels": evidence.cyan_pixels,
        }
        self._set(
            field,
            value,
            source=f"{field}_selected_border",
            phase=phase,
            confidence="high" if normalized and ocr_confidence >= 80.0 else "medium",
        )
        return value

    def _observe_modules(self, frame: Frame, phase: str) -> None:
        try:
            matches = identify_equipped_ancestral_modules(frame)
        except (OSError, TypeError, ValueError):
            return
        if not matches or not any(match.status == "matched" for match in matches):
            return
        self._set(
            "modules",
            [asdict(match) for match in matches],
            source="module_icon_catalog",
            phase=phase,
            confidence=(
                "high" if all(match.status == "matched" for match in matches) else "partial"
            ),
        )

    def _observe_ultimate_weapons(self, frame: Frame, phase: str) -> None:
        try:
            boxes_by_column = detect_visible_boxes(frame, menu="ultimate weapons")
        except (OSError, TypeError, ValueError):
            return
        visible = [box for boxes in boxes_by_column.values() for box in boxes]
        additions = merge_ultimate_weapon_observations(visible)
        if not additions:
            return
        existing = self._fields["ultimate_weapons"].get("value")
        merged = dict(existing) if isinstance(existing, Mapping) else {}
        for label, toggles in additions.items():
            current = dict(merged.get(label) or {})
            current.update(toggles)
            merged[label] = current
        self._set(
            "ultimate_weapons",
            merged,
            source="ultimate_weapon_tiles",
            phase=phase,
            confidence="high",
        )

    def _set(
        self,
        field: str,
        value: Any,
        *,
        source: str,
        phase: str,
        confidence: str,
        observed_at: Optional[str] = None,
        provenance: Optional[Mapping[str, Any]] = None,
        temporal_merge: bool = False,
    ) -> None:
        if field in ROUND_INVARIANT_ATTACHMENT_CHECKS and not temporal_merge:
            if field in self._round_invariant_conflicts:
                return
            if field in self._round_invariant_claims:
                current = self._fields[field]
                if (
                    _authoritative_ui_invariant(
                        field,
                        {
                            "status": "observed",
                            "value": value,
                            "source": source,
                            "confidence": confidence,
                        },
                    )
                    and _canonical_invariant_value(
                        field, current.get("value")
                    )
                    != _canonical_invariant_value(field, value)
                ):
                    provenance_value = current.get("provenance")
                    temporal = (
                        provenance_value.get("temporal")
                        if isinstance(provenance_value, Mapping)
                        else {}
                    )
                    self._record_round_invariant_conflict(
                        field,
                        temporal=(
                            temporal if isinstance(temporal, Mapping) else {}
                        ),
                    )
                return
        observation = {
            "status": "observed",
            "value": value,
            "source": source,
            "phase": phase,
            "confidence": confidence,
            "observed_at": observed_at or self._timestamp(),
        }
        if provenance is not None:
            observation["provenance"] = deepcopy(dict(provenance))
        self._fields[field] = observation

    def _timestamp(self) -> str:
        return self._clock().isoformat(timespec="seconds")


def _authoritative_ui_invariant(
    field: str,
    observation: Mapping[str, Any],
) -> bool:
    """Only complete preset UI evidence may contradict a save invariant."""

    if field not in {"workshop_preset", "bots_preset"}:
        return False
    value = observation.get("value")
    return bool(
        observation.get("status") == "observed"
        and observation.get("source")
        != "guarded_active_attachment_player_save"
        and observation.get("confidence") == "high"
        and isinstance(value, Mapping)
        and str(value.get("label") or "").strip()
    )


def _canonical_invariant_value(field: str, value: Any) -> str:
    if field in {"workshop_preset", "bots_preset"}:
        label = value.get("label") if isinstance(value, Mapping) else value
        return canonical_temporal_value(
            " ".join(str(label or "").split()).casefold()
        )
    if field == "guardian_chips" and isinstance(value, (list, tuple, set)):
        return canonical_temporal_value(
            sorted(str(item) for item in value)
        )
    return canonical_temporal_value(value)


__all__ = [
    "ATTACK_DISSONANCE_BADGE_REGION",
    "DISSONANCE_BADGE_REGION",
    "NoStrategyRunObserver",
    "OBSERVED_FIELDS",
    "detect_attack_dissonance_badge",
    "detect_dissonance_badge",
]
