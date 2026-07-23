"""Passive evidence collection for battles run without a strategy profile.

``No Strategy`` deliberately supplies no configured intent.  This module keeps
observed values separate from ``run_configuration`` so a familiar Tier or
loadout can never be mistaken for a profile the operator selected.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

import cv2
import numpy as np

from core.auto_pick_perks import measure_auto_pick_perks
from core.damage_adjuster import read_damage_adjuster
from core.label_tapper import is_visible
from core.module_icon_index import identify_equipped_ancestral_modules
from core.target_priority import detect_target_priority_order
from core.upgrade_box_detector import detect_visible_boxes
from core.gc_preflight import merge_ultimate_weapon_observations
from core.workshop_preset import measure_preset_slot_selection
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
Clock = Callable[[], datetime]

# The modifier badge is fixed immediately to the right of the Tier label.  A
# narrow fixed region prevents purple battle effects elsewhere in the frame
# from becoming identity evidence.
ATTACK_DISSONANCE_BADGE_REGION = (680, 985, 80, 80)
_PURPLE_LOWER = np.array((125, 70, 90), dtype=np.uint8)
_PURPLE_UPPER = np.array((165, 255, 255), dtype=np.uint8)
_MIN_BADGE_PURPLE_PIXELS = 500

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


def detect_attack_dissonance_badge(frame: Optional[Frame]) -> dict[str, Any]:
    """Return localized purple-sword badge evidence without taking action."""

    x, y, width, height = ATTACK_DISSONANCE_BADGE_REGION
    if (
        frame is None
        or not isinstance(frame, np.ndarray)
        or frame.ndim != 3
        or y + height > frame.shape[0]
        or x + width > frame.shape[1]
    ):
        return {
            "observed": False,
            "purple_pixels": 0,
            "region": list(ATTACK_DISSONANCE_BADGE_REGION),
        }
    crop = frame[y : y + height, x : x + width]
    purple_pixels = int(
        cv2.countNonZero(
            cv2.inRange(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), _PURPLE_LOWER, _PURPLE_UPPER)
        )
    )
    return {
        "observed": purple_pixels >= _MIN_BADGE_PURPLE_PIXELS,
        "purple_pixels": purple_pixels,
        "minimum_purple_pixels": _MIN_BADGE_PURPLE_PIXELS,
        "region": list(ATTACK_DISSONANCE_BADGE_REGION),
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
            badge = detect_attack_dissonance_badge(frame)
            if badge["observed"]:
                self._set(
                    "run_identity",
                    {
                        "family": "Dissonance",
                        "subtype": "Attack",
                        "label": "Attack Dissonance",
                        "signals": {"tier_badge": badge},
                    },
                    source="tier_attack_dissonance_badge",
                    phase=phase,
                    confidence="high",
                )
            if menu == "UW_MENU":
                self._observe_ultimate_weapons(frame, phase)

        if state == "CARDS":
            self._observe_selected_slot("cards_deck", frame, _CARD_SLOTS, phase)
        elif state == "WORKSHOP":
            self._observe_selected_slot(
                "workshop_preset", frame, _WORKSHOP_SLOTS, phase
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
    ) -> None:
        selected = []
        for index, region in enumerate(regions, start=1):
            evidence = measure_preset_slot_selection(frame, region)
            if evidence.selected:
                selected.append((index, region, evidence))
        if len(selected) != 1:
            return
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
        self._set(
            field,
            {
                "slot": index,
                "label": normalized if ocr_confidence >= 60.0 else None,
                "label_ocr_confidence": ocr_confidence,
                "green_pixels": evidence.green_pixels,
                "cyan_pixels": evidence.cyan_pixels,
            },
            source=f"{field}_selected_border",
            phase=phase,
            confidence="high" if normalized and ocr_confidence >= 80.0 else "medium",
        )

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
    ) -> None:
        self._fields[field] = {
            "status": "observed",
            "value": value,
            "source": source,
            "phase": phase,
            "confidence": confidence,
            "observed_at": observed_at or self._timestamp(),
        }

    def _timestamp(self) -> str:
        return self._clock().isoformat(timespec="seconds")


__all__ = [
    "ATTACK_DISSONANCE_BADGE_REGION",
    "NoStrategyRunObserver",
    "OBSERVED_FIELDS",
    "detect_attack_dissonance_badge",
]
