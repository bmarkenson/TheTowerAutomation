"""Schema and structured OCR for Home Perks configuration."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any, Callable, Mapping, Optional, Sequence

import cv2
import numpy as np

from core.battle_perks import ocr_perk_configuration_rows, ocr_perk_rows


Frame = np.ndarray
RowTextFn = Callable[[Frame], tuple[str, float]]
RankingBoundaryFn = Callable[[Frame], int | None]

# In the configuration panel selected tiles use the same hue as available
# tiles but a deliberately darker fill.  The retained First Perk fixture reads
# median V=83 for selected and V=137 for available rows.
MAX_SELECTED_BACKGROUND_VALUE = 110.0
DEFAULT_CONFIDENCE_THRESHOLD = 70.0
PERK_BAN_CAPACITY = 6
AUTO_PICK_DIVIDER_SCAN_TOP = 400
AUTO_PICK_DIVIDER_SCAN_BOTTOM = 1700
AUTO_PICK_DIVIDER_LEFT_X1 = 120
AUTO_PICK_DIVIDER_LEFT_X2 = 310
AUTO_PICK_DIVIDER_RIGHT_X1 = 770
AUTO_PICK_DIVIDER_RIGHT_X2 = 960
AUTO_PICK_DIVIDER_MIN_WHITE_PIXELS = 130

FARM_PERK_BANS = (
    "lifesteal_knockback_tradeoff",
    "enemies_damage_tradeoff",
    "defense_absolute",
    "interest",
    "land_mine_damage",
    "cash_bonus",
)

FARM_AUTO_PICK_ORDER = (
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
    "damage",
)

PERK_CONFIGURATION_LABELS = {
    "cash_tradeoff": "Cash Trade-Off",
    "enemies_damage_tradeoff": "Enemies Damage / Tower Damage Trade-Off",
    "lifesteal_knockback_tradeoff": "Lifesteal / Knockback Trade-Off",
    "interest": "Interest",
    "defense_absolute": "Defense Absolute",
    "land_mine_damage": "Land Mine Damage",
    "cash_bonus": "Cash Bonus",
    "perk_wave_requirement": "Perk Wave Requirement",
    "game_speed": "Game Speed",
    "coin_tradeoff": "Coin Trade-Off",
    "golden_tower_bonus": "Golden Tower Bonus",
    "black_hole_duration": "Black Hole Duration",
    "death_wave_quantity": "Death Wave Quantity",
    "coins_bonus": "Coins Bonus",
    "free_upgrade_chance": "Free Upgrade Chance",
    "enemy_health_tradeoff": (
        "Enemy Health / Tower Regen and Lifesteal Trade-Off"
    ),
    "boss_health_tradeoff": "Boss Health / Boss Speed Trade-Off",
    "tower_damage_boss_health_tradeoff": (
        "Tower Damage / Boss Health Trade-Off"
    ),
    "defense_percent": "Defense Percent",
    "max_health": "Max Health",
    "health_regen": "Health Regen",
    "health_regen_tradeoff": "Health Regen / Max Health Trade-Off",
    "enemy_speed_tradeoff": "Enemy Speed / Enemy Damage Trade-Off",
    "ranged_distance_tradeoff": "Ranged Distance / Ranged Damage Trade-Off",
    "orbs": "Orbs",
    "bounce_shot": "Bounce Shot",
    "chain_lightning_damage": "Chain Lightning Damage",
    "inner_land_mines": "Inner Land Mines",
    "smart_missiles": "Smart Missiles",
    "spotlight_damage": "Spotlight Damage",
    "damage": "Damage",
}

_SUPPORTED_PERK_KEYS = frozenset(PERK_CONFIGURATION_LABELS)

ORDER_SEMANTICS = {
    "perk_first_choice": "single_choice",
    "perk_bans": "display_order",
    "perk_auto_pick_order": "top_to_bottom_priority",
}


def normalize_perk_configuration_requirements(
    requirements: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """Validate the profile's semantic Ban and Auto Pick lists."""

    bans = _normalize_perk_key_list(
        requirements.get("perk_bans"),
        field="perk_bans",
        allow_empty=True,
    )
    auto_pick = _normalize_perk_key_list(
        requirements.get("perk_auto_pick_order"),
        field="perk_auto_pick_order",
    )
    if len(bans) > PERK_BAN_CAPACITY:
        raise ValueError(
            f"perk_bans supports at most {PERK_BAN_CAPACITY} configured perks"
        )
    return bans, auto_pick


def classify_perk_configuration_text(text: str) -> str | None:
    """Map OCR display text to a value-independent semantic perk key."""

    normalized = _comparison_text(text)
    tokens = set(normalized.split())
    if "empty slot" in normalized:
        return "empty_slot"
    if "cash per wave" in normalized and "give cash" in normalized:
        return "cash_tradeoff"
    if "lifesteal" in tokens and any(
        token.startswith("knockb") for token in tokens
    ):
        return "lifesteal_knockback_tradeoff"
    if "enemies damage" in normalized and "tower damage" in normalized:
        return "enemies_damage_tradeoff"
    if "perk wave requirement" in normalized:
        return "perk_wave_requirement"
    if "increase max game speed" in normalized:
        return "game_speed"
    if (
        "coins" in tokens
        and "tower max health" in normalized
        and "but" in tokens
    ):
        return "coin_tradeoff"
    if "golden tower bonus" in normalized:
        return "golden_tower_bonus"
    if "black hole duration" in normalized:
        return "black_hole_duration"
    if "wave on death wave" in normalized:
        return "death_wave_quantity"
    if "all coins bonuses" in normalized:
        return "coins_bonus"
    if "free upgrade chance" in normalized:
        return "free_upgrade_chance"
    if "cash bonus" in normalized:
        return "cash_bonus"
    if (
        "enemies have" in normalized
        and "health" in tokens
        and "tower health regen" in normalized
        and "lifesteal" in tokens
    ):
        return "enemy_health_tradeoff"
    if "boss health" in normalized and "boss speed" in normalized:
        return "boss_health_tradeoff"
    if (
        "tower damage" in normalized
        and "bosses have" in normalized
        and "health" in tokens
    ):
        return "tower_damage_boss_health_tradeoff"
    if normalized.startswith("defense percent"):
        return "defense_percent"
    if (
        "tower health regen" in normalized
        and "tower max health" in normalized
    ):
        return "health_regen_tradeoff"
    if "enemies speed" in normalized and "enemies damage" in normalized:
        return "enemy_speed_tradeoff"
    if (
        "ranged enemies attack distance reduced" in normalized
        and "ranged enemies damage" in normalized
    ):
        return "ranged_distance_tradeoff"
    if "max health" in normalized and "but" not in tokens:
        return "max_health"
    if "health regen" in normalized and "but" not in tokens:
        return "health_regen"
    if normalized.startswith("orb") or normalized.startswith("orbs"):
        return "orbs"
    if normalized.startswith("bounce shot"):
        return "bounce_shot"
    if "chain lightning damage" in normalized:
        return "chain_lightning_damage"
    if "extra set of inner mines" in normalized:
        return "inner_land_mines"
    if "more smart missiles" in normalized:
        return "smart_missiles"
    if "spotlight damage" in normalized:
        return "spotlight_damage"
    if "land mine damage" in normalized:
        return "land_mine_damage"
    if "defense absolute" in normalized:
        return "defense_absolute"
    if normalized.startswith("interest"):
        return "interest"
    if re.fullmatch(r"x?[\d.]+\s+damage", normalized):
        return "damage"
    return None


def extract_configured_perk_bans(
    frame: Frame,
    *,
    capacity: int = PERK_BAN_CAPACITY,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    row_fn: Callable[[Frame], list[dict[str, Any]]] = (
        ocr_perk_configuration_rows
    ),
) -> dict[str, Any]:
    """Read the fixed Selected Perks block at the top of the Ban tab."""

    rows = list(row_fn(frame))
    selected: list[dict[str, Any]] = []
    empty_slot_seen = False
    for row in rows[: max(1, int(capacity))]:
        entry = _semantic_entry(row)
        if entry["key"] == "empty_slot":
            empty_slot_seen = True
            break
        selected.append(entry)

    low_confidence = [
        str(entry["display_text"])
        for entry in selected
        if _semantic_entry_is_low_confidence(
            entry,
            confidence_threshold=confidence_threshold,
        )
    ]
    warnings = []
    if len(rows) < capacity:
        warnings.append(
            f"Ban Perks selected block exposed {len(rows)} of {capacity} slots"
        )
    if len(selected) < capacity and not empty_slot_seen:
        warnings.append("Ban Perks empty-slot boundary was not recognized")
    if low_confidence:
        warnings.append(
            "Low-confidence banned perks: " + ", ".join(low_confidence)
        )
    return {
        "order_semantics": "display_order",
        "selected": selected,
        "quality": {
            "valid": not warnings,
            "selected_count": len(selected),
            "capacity": int(capacity),
            "empty_slot_seen": empty_slot_seen,
            "low_confidence": low_confidence,
            "warnings": warnings,
        },
        "raw_rows": rows,
    }


def extract_ranked_auto_pick_order(
    frames: Sequence[Frame],
    *,
    ranking_count: int,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    row_fn: Callable[[Frame], list[dict[str, Any]]] = (
        ocr_perk_configuration_rows
    ),
    ranking_boundary_fn: RankingBoundaryFn = (
        lambda frame: detect_auto_pick_ranking_boundary(frame)
    ),
) -> dict[str, Any]:
    """Read the first profile-declared number of Auto Pick priority rows."""

    if ranking_count <= 0:
        raise ValueError("ranking_count must be positive")
    ordered: list[dict[str, Any]] = []
    raw_pages = []
    ranking_boundary_seen = False
    for page, frame in enumerate(frames, start=1):
        rows = list(row_fn(frame))
        ranking_boundary_y = ranking_boundary_fn(frame)
        if ranking_boundary_y is not None:
            ranking_boundary_seen = True
            ranked_rows = [
                row
                for row in rows
                if int(row.get("bottom") or 0) < ranking_boundary_y
            ]
        else:
            ranked_rows = rows
        raw_pages.append(
            {
                "page": page,
                "rows": rows,
                "ranking_boundary_y": ranking_boundary_y,
            }
        )
        for row in ranked_rows:
            entry = _semantic_entry(row)
            if any(
                perk_entries_match(existing, entry)
                for existing in ordered
            ):
                continue
            entry["rank"] = len(ordered) + 1
            entry["page"] = page
            ordered.append(entry)
            if len(ordered) >= ranking_count:
                break
        if len(ordered) >= ranking_count or ranking_boundary_seen:
            break

    selected = ordered[:ranking_count]
    low_confidence = [
        str(entry["display_text"])
        for entry in selected
        if _semantic_entry_is_low_confidence(
            entry,
            confidence_threshold=confidence_threshold,
        )
    ]
    warnings = []
    if len(selected) != ranking_count:
        suffix = (
            " before the ranking boundary"
            if ranking_boundary_seen
            else ""
        )
        warnings.append(
            f"Auto Pick exposed {len(selected)} of {ranking_count} ranked "
            f"perks{suffix}"
        )
    if low_confidence:
        warnings.append(
            "Low-confidence Auto Pick perks: " + ", ".join(low_confidence)
        )
    return {
        "order_semantics": "top_to_bottom_priority",
        "selected": selected,
        "quality": {
            "valid": not warnings,
            "selected_count": len(selected),
            "ranking_count": ranking_count,
            "ranking_boundary_seen": ranking_boundary_seen,
            "low_confidence": low_confidence,
            "warnings": warnings,
        },
        "raw_pages": raw_pages,
    }


def detect_auto_pick_ranking_boundary(frame: Frame) -> int | None:
    """Locate the paired white rules surrounding ``Rankings Unlocked``."""

    if (
        not isinstance(frame, np.ndarray)
        or frame.ndim != 3
        or frame.shape[0] < AUTO_PICK_DIVIDER_SCAN_BOTTOM
        or frame.shape[1] < AUTO_PICK_DIVIDER_RIGHT_X2
    ):
        return None
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    white = (hsv[:, :, 1] < 60) & (hsv[:, :, 2] >= 180)
    left_counts = white[
        AUTO_PICK_DIVIDER_SCAN_TOP:AUTO_PICK_DIVIDER_SCAN_BOTTOM,
        AUTO_PICK_DIVIDER_LEFT_X1:AUTO_PICK_DIVIDER_LEFT_X2,
    ].sum(axis=1)
    right_counts = white[
        AUTO_PICK_DIVIDER_SCAN_TOP:AUTO_PICK_DIVIDER_SCAN_BOTTOM,
        AUTO_PICK_DIVIDER_RIGHT_X1:AUTO_PICK_DIVIDER_RIGHT_X2,
    ].sum(axis=1)
    paired_counts = np.minimum(left_counts, right_counts)
    candidates = np.flatnonzero(
        paired_counts >= AUTO_PICK_DIVIDER_MIN_WHITE_PIXELS
    )
    if candidates.size == 0:
        return None

    groups = np.split(
        candidates,
        np.flatnonzero(np.diff(candidates) > 1) + 1,
    )
    strongest = max(
        groups,
        key=lambda group: (
            int(paired_counts[group].max()),
            int(group.size),
        ),
    )
    return int(
        AUTO_PICK_DIVIDER_SCAN_TOP
        + round(float(np.median(strongest)))
    )


def evaluate_profile_perk_configuration(
    requirements: Mapping[str, Any],
    *,
    bans_frame: Frame,
    auto_pick_frames: Sequence[Frame],
    row_fn: Callable[[Frame], list[dict[str, Any]]] = (
        ocr_perk_configuration_rows
    ),
) -> dict[str, Any]:
    """Compare captured Home configuration to the semantic profile lists."""

    required_bans, required_auto_pick = (
        normalize_perk_configuration_requirements(requirements)
    )
    bans = extract_configured_perk_bans(bans_frame, row_fn=row_fn)
    auto_pick = extract_ranked_auto_pick_order(
        auto_pick_frames,
        ranking_count=len(required_auto_pick),
        row_fn=row_fn,
    )
    checks = {
        "perk_bans": _compare_configuration_field(
            required_bans,
            bans,
            ordered=False,
        ),
        "perk_auto_pick_order": _compare_configuration_field(
            required_auto_pick,
            auto_pick,
            ordered=True,
        ),
    }
    failed = [
        check_id
        for check_id, evidence in checks.items()
        if evidence["valid"] is not True
    ]
    return {
        "boundary": "NEW_BATTLE",
        "checked": True,
        "valid": not failed,
        "failed_checks": failed,
        **checks,
    }


def semantic_perk_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return one OCR row with its strategy-facing semantic identity."""

    return _semantic_entry(row)


def perk_configuration_label(key: str | None, display_text: str = "") -> str:
    if key in PERK_CONFIGURATION_LABELS:
        return PERK_CONFIGURATION_LABELS[str(key)]
    return str(display_text or key or "Unknown perk")


def perk_entries_match(
    expected: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> bool:
    """Match known semantic keys, falling back to conservative OCR similarity."""

    expected_key = expected.get("key")
    candidate_key = candidate.get("key")
    if expected_key or candidate_key:
        return bool(expected_key and expected_key == candidate_key)
    left = _comparison_text(str(expected.get("display_text") or ""))
    right = _comparison_text(str(candidate.get("display_text") or ""))
    return bool(left and right and SequenceMatcher(None, left, right).ratio() >= 0.88)


def parse_perk_configuration_selection(
    frames: Sequence[Frame],
    *,
    field: str,
    source_complete: bool,
    source_reason: str,
    evidence_images: Sequence[str] = (),
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    text_fn: Optional[RowTextFn] = None,
) -> dict[str, Any]:
    """Extract the dark selected rows from overlapping configuration pages."""

    if field not in ORDER_SEMANTICS:
        raise ValueError(f"unknown Perks configuration field {field!r}")
    selected: list[dict[str, Any]] = []
    raw_pages = []
    for page, frame in enumerate(frames, start=1):
        rows = ocr_perk_rows(frame, text_fn=text_fn)
        raw_pages.append(
            {
                "page": page,
                "rows": rows,
            }
        )
        for row in rows:
            if float(row["background_value_median"]) > MAX_SELECTED_BACKGROUND_VALUE:
                continue
            existing = _find_duplicate(selected, str(row["display_text"]))
            observation = {
                "page": page,
                "top": row["top"],
                "confidence": row["confidence"],
            }
            if existing is not None:
                existing["observations"].append(observation)
                if float(row["confidence"]) > float(existing["confidence"]):
                    existing.update(
                        text_raw=row["text_raw"],
                        display_text=row["display_text"],
                        key=row["key"],
                        confidence=row["confidence"],
                    )
                continue
            selected.append(
                {
                    "rank": len(selected) + 1,
                    "display_text": row["display_text"],
                    "text_raw": row["text_raw"],
                    "key": row["key"],
                    "confidence": row["confidence"],
                    "observations": [observation],
                }
            )

    low_confidence = [
        item["key"]
        for item in selected
        if float(item["confidence"]) < float(confidence_threshold)
    ]
    warnings = []
    if not source_complete:
        warnings.append(f"Perks configuration capture was incomplete: {source_reason}")
    if low_confidence:
        warnings.append("Low-confidence configured perks: " + ", ".join(low_confidence))
    if field == "perk_first_choice" and len(selected) != 1:
        warnings.append(
            f"First Perk capture contained {len(selected)} selected rows instead of one"
        )
    if field == "perk_auto_pick_order" and not selected:
        warnings.append("Auto Pick capture contained no selected priority rows")
    valid = bool(source_complete and not low_confidence and not warnings)
    return {
        "source_method": "scrolling_screenshot_ocr",
        "page_count": len(frames),
        "order_semantics": ORDER_SEMANTICS[field],
        "selected": selected,
        "evidence_images": list(evidence_images),
        "raw_pages": raw_pages,
        "quality": {
            "valid": valid,
            "source_complete": bool(source_complete),
            "source_reason": source_reason,
            "confidence_threshold": float(confidence_threshold),
            "selected_count": len(selected),
            "low_confidence": low_confidence,
            "warnings": warnings,
        },
    }


def _find_duplicate(
    selected: Sequence[Mapping[str, Any]],
    display_text: str,
) -> Optional[dict[str, Any]]:
    normalized = _comparison_text(display_text)
    for item in selected:
        existing = _comparison_text(str(item.get("display_text") or ""))
        if SequenceMatcher(None, existing, normalized).ratio() >= 0.88:
            return item  # type: ignore[return-value]
    return None


def _normalize_perk_key_list(
    raw: Any,
    *,
    field: str,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(raw, list) or (not raw and not allow_empty):
        requirement = "a list" if allow_empty else "a non-empty list"
        raise ValueError(f"{field} must be {requirement}")
    normalized = [str(item or "").strip().lower() for item in raw]
    if any(not item for item in normalized):
        raise ValueError(f"{field} cannot contain empty perk keys")
    unknown = sorted(set(normalized) - _SUPPORTED_PERK_KEYS)
    if unknown:
        raise ValueError(f"{field} contains unsupported perks: {unknown}")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} cannot repeat a perk")
    return normalized


def _semantic_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    raw_candidates = row.get("text_candidates")
    source_candidates = (
        [dict(item) for item in raw_candidates if isinstance(item, Mapping)]
        if isinstance(raw_candidates, Sequence)
        else []
    )
    candidates = list(source_candidates)
    candidates.append(
        {
            "display_text": str(
                row.get("display_text") or row.get("text_raw") or ""
            ).strip(),
            "text_raw": str(row.get("text_raw") or ""),
            "confidence": float(row.get("confidence") or -1.0),
        }
    )
    recognized = [
        (
            classify_perk_configuration_text(
                str(item.get("display_text") or item.get("text_raw") or "")
            ),
            item,
        )
        for item in candidates
    ]
    known = [
        (key, item)
        for key, item in recognized
        if key is not None
    ]
    if known:
        semantic_key, best = max(
            known,
            key=lambda pair: float(pair[1].get("confidence") or -1.0),
        )
    else:
        best = max(
            candidates,
            key=lambda item: float(item.get("confidence") or -1.0),
        )
        semantic_key = None
    display_text = str(
        best.get("display_text") or best.get("text_raw") or ""
    ).strip()
    semantic_agreement = sum(
        1
        for item in source_candidates
        if classify_perk_configuration_text(
            str(item.get("display_text") or item.get("text_raw") or "")
        )
        == semantic_key
    )
    if not source_candidates and semantic_key is not None:
        semantic_agreement = 1
    return {
        "key": semantic_key,
        "display_text": display_text,
        "text_raw": str(best.get("text_raw") or display_text),
        "confidence": float(best.get("confidence") or -1.0),
        "semantic_agreement": semantic_agreement,
        "top": int(row.get("top") or 0),
        "bottom": int(row.get("bottom") or 0),
        "background_value_median": float(
            row.get("background_value_median") or 0.0
        ),
    }


def _semantic_entry_is_low_confidence(
    entry: Mapping[str, Any],
    *,
    confidence_threshold: float,
) -> bool:
    if float(entry.get("confidence") or -1.0) >= float(confidence_threshold):
        return False
    return int(entry.get("semantic_agreement") or 0) < 2


def _compare_configuration_field(
    expected: Sequence[str],
    captured: Mapping[str, Any],
    *,
    ordered: bool,
) -> dict[str, Any]:
    selected = [
        dict(item)
        for item in captured.get("selected") or ()
        if isinstance(item, Mapping)
    ]
    observed = [item.get("key") for item in selected]
    quality = captured.get("quality") or {}
    quality_valid = bool(
        isinstance(quality, Mapping) and quality.get("valid") is True
    )
    recognized = all(key is not None for key in observed)
    if ordered:
        matches = observed == list(expected)
    else:
        matches = (
            len(observed) == len(expected)
            and set(observed) == set(expected)
        )
    valid = bool(quality_valid and recognized and matches)
    reason = "matched"
    if not quality_valid:
        warnings = quality.get("warnings") if isinstance(quality, Mapping) else None
        reason = "; ".join(str(item) for item in warnings or ()) or "capture invalid"
    elif not recognized:
        reason = "one or more configured perks were not recognized"
    elif not matches:
        reason = "configured perks did not match the strategy"
    return {
        "boundary": "NEW_BATTLE",
        "checked": True,
        "valid": valid,
        "ordered": bool(ordered),
        "expected": list(expected),
        "expected_labels": [
            perk_configuration_label(key) for key in expected
        ],
        "observed": observed,
        "observed_labels": [
            perk_configuration_label(
                item.get("key"),
                str(item.get("display_text") or ""),
            )
            for item in selected
        ],
        "reason": reason,
        "capture": dict(captured),
    }


def _comparison_text(text: str) -> str:
    return re.sub(r"[^a-z0-9.%+-]+", " ", str(text or "").casefold()).strip()


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "FARM_AUTO_PICK_ORDER",
    "FARM_PERK_BANS",
    "MAX_SELECTED_BACKGROUND_VALUE",
    "ORDER_SEMANTICS",
    "PERK_BAN_CAPACITY",
    "PERK_CONFIGURATION_LABELS",
    "classify_perk_configuration_text",
    "detect_auto_pick_ranking_boundary",
    "evaluate_profile_perk_configuration",
    "extract_configured_perk_bans",
    "extract_ranked_auto_pick_order",
    "normalize_perk_configuration_requirements",
    "parse_perk_configuration_selection",
    "perk_configuration_label",
    "perk_entries_match",
    "semantic_perk_entry",
]
