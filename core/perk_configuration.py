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
CLOSEST_MATCH_MIN_SCORE = 0.84
CLOSEST_MATCH_MIN_MARGIN = 0.12
CLOSEST_MATCH_RETRY_SCORE = 0.70
CLOSEST_MATCH_RETRY_MARGIN = 0.08
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
    "unlock_random_ultimate_weapon": "Unlock a random ultimate weapon",
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
    "swamp_radius": "Swamp Radius",
    "damage": "Damage",
}

# Canonical value-bearing row text is deliberately separate from the concise
# operator labels above.  It gives OCR recovery a comparable text shape after
# volatile numbers are removed, without allowing the active profile's expected
# values to narrow (and therefore bias) the candidate set.
PERK_CONFIGURATION_OCR_EXEMPLARS = {
    "cash_tradeoff": (
        "x13.20 cash per wave, but enemy kills don't give cash"
    ),
    "enemies_damage_tradeoff": (
        "Enemies damage -55.0%, but tower damage -50%"
    ),
    "lifesteal_knockback_tradeoff": (
        "Lifesteal x2.75, but knockback force -70%"
    ),
    "interest": "Interest x1.88",
    "defense_absolute": "x1.44 Defense Absolute",
    "land_mine_damage": "Land Mine Damage x4.38",
    "cash_bonus": "x1.44 Cash Bonus",
    "perk_wave_requirement": "Perk wave requirement -25.00%",
    "unlock_random_ultimate_weapon": "Unlock a random ultimate weapon",
    "game_speed": "Increase max game speed by +1.25",
    "coin_tradeoff": "x1.98 coins, but tower max health -70.0%",
    "golden_tower_bonus": "Golden tower bonus x1.5",
    "black_hole_duration": "Black Hole duration +12.0s",
    "death_wave_quantity": "+1 wave on death wave",
    "coins_bonus": "x1.44 all coins bonuses",
    "free_upgrade_chance": "Free upgrade chance for all +6.25%",
    "enemy_health_tradeoff": (
        "Enemies have -55.0% health, but tower health regen and "
        "lifesteal -90%"
    ),
    "boss_health_tradeoff": (
        "Boss health -73.5%, but boss speed +50%"
    ),
    "tower_damage_boss_health_tradeoff": (
        "x1.65 tower damage, but bosses have x8 health"
    ),
    "defense_percent": "Defense percent +5.00%",
    "max_health": "x1.25 max health",
    "health_regen": "x2.19 Health Regen",
    "health_regen_tradeoff": (
        "tower health regen x8.80, but tower max health -60%"
    ),
    "enemy_speed_tradeoff": (
        "Enemies speed -44.0%, but enemies damage x2.5"
    ),
    "ranged_distance_tradeoff": (
        "Ranged enemies attack distance reduced, but ranged enemies "
        "damage x3"
    ),
    "orbs": "Orbs +1",
    "bounce_shot": "Bounce Shot +2",
    "chain_lightning_damage": "Chain lightning damage x2",
    "inner_land_mines": "Extra set of inner mines",
    "smart_missiles": "4 more smart missiles",
    "spotlight_damage": "Spotlight damage bonus x1.5",
    "swamp_radius": "Swamp radius x1.5",
    "damage": "x1.44 Damage",
    "empty_slot": "Empty Slot",
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


def normalize_perk_first_choice_requirement(requirements: Mapping[str, Any]) -> str:
    """Validate the independent strategy-owned First Perk choice."""

    raw = requirements.get("perk_first_choice")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("perk_first_choice must be one supported perk key")
    normalized = raw.strip().lower()
    if normalized not in _SUPPORTED_PERK_KEYS:
        raise ValueError(
            f"perk_first_choice contains unsupported perk: {normalized!r}"
        )
    return normalized


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
    if "unlock a random ultimate weapon" in normalized:
        return "unlock_random_ultimate_weapon"
    if "increase max game speed" in normalized:
        return "game_speed"
    if (
        "coins" in tokens
        # The narrow Home row crop can truncate ``health`` to ``h`` while
        # retaining the two phrases that uniquely identify Coin Trade-Off.
        and "tower max h" in normalized
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
        # A row clipped at the bottom of the viewport can retain only the
        # ``tower health`` prefix; the overlapping page supplies the complete
        # regen/lifesteal suffix and both observations must share one identity.
        and "tower health" in normalized
        and "but" in tokens
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
    if (
        # A row clipped at the viewport edge has been observed with the
        # leading ``E`` misread as ``c``.  The unchanged damage clause keeps
        # this alias specific to the Enemy Speed trade-off.
        (
            "enemies speed" in normalized
            or "cnemies speed" in normalized
        )
        and "enemies damage" in normalized
    ):
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
    if normalized.startswith("swamp radius"):
        return "swamp_radius"
    if "land mine damage" in normalized:
        return "land_mine_damage"
    if "defense absolute" in normalized:
        return "defense_absolute"
    if normalized.startswith("interest"):
        return "interest"
    if re.fullmatch(r"x?[\d.]+\s+damage", normalized):
        return "damage"
    return None


def closest_perk_configuration_match(text: str) -> dict[str, Any]:
    """Return a scored, margin-gated semantic candidate for failed OCR.

    This is recovery evidence, not a single-read authority grant.  Callers may
    accept an ``accepted`` candidate only after independent OCR crops agree.
    A weaker but uniquely separated candidate requests a fresh local capture.
    """

    normalized = _semantic_comparison_text(text)
    if len(normalized) < 6:
        return {
            "key": None,
            "label": None,
            "score": 0.0,
            "margin": 0.0,
            "accepted": False,
            "retry_recommended": False,
            "normalized_text": normalized,
        }
    scored = sorted(
        (
            (
                SequenceMatcher(
                    None,
                    normalized,
                    _semantic_comparison_text(exemplar),
                ).ratio(),
                key,
            )
            for key, exemplar in PERK_CONFIGURATION_OCR_EXEMPLARS.items()
        ),
        reverse=True,
    )
    best_score, best_key = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    margin = max(0.0, best_score - second_score)
    accepted = bool(
        best_score >= CLOSEST_MATCH_MIN_SCORE
        and margin >= CLOSEST_MATCH_MIN_MARGIN
    )
    retry_recommended = bool(
        accepted
        or (
            best_score >= CLOSEST_MATCH_RETRY_SCORE
            and margin >= CLOSEST_MATCH_RETRY_MARGIN
        )
    )
    return {
        "key": best_key,
        "label": perk_configuration_label(
            best_key,
            PERK_CONFIGURATION_OCR_EXEMPLARS[best_key],
        ),
        "score": round(float(best_score), 4),
        "margin": round(float(margin), 4),
        "accepted": accepted,
        "retry_recommended": retry_recommended,
        "normalized_text": normalized,
    }


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
    unrecognized = [
        str(entry["display_text"])
        for entry in selected
        if entry.get("key") is None
    ]
    semantic_conflicts = [
        str(entry["display_text"])
        for entry in selected
        if entry.get("semantic_conflict") is True
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
    if unrecognized:
        warnings.append(
            "Unrecognized banned perks: " + ", ".join(unrecognized)
        )
    if semantic_conflicts:
        warnings.append(
            "Conflicting Ban Perks OCR: " + ", ".join(semantic_conflicts)
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
            "unrecognized": unrecognized,
            "semantic_conflicts": semantic_conflicts,
            "closest_matches": _ocr_recovery_candidates(selected),
            "ocr_retry_recommended": bool(warnings),
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
    unrecognized = [
        str(entry["display_text"])
        for entry in selected
        if entry.get("key") is None
    ]
    semantic_conflicts = [
        str(entry["display_text"])
        for entry in selected
        if entry.get("semantic_conflict") is True
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
    if unrecognized:
        warnings.append(
            "Unrecognized Auto Pick perks: " + ", ".join(unrecognized)
        )
    if semantic_conflicts:
        warnings.append(
            "Conflicting Auto Pick OCR: " + ", ".join(semantic_conflicts)
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
            "unrecognized": unrecognized,
            "semantic_conflicts": semantic_conflicts,
            "closest_matches": _ocr_recovery_candidates(selected),
            "ocr_retry_recommended": bool(warnings),
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
    captured_bans: Mapping[str, Any] | None = None,
    captured_auto_pick: Mapping[str, Any] | None = None,
    row_fn: Callable[[Frame], list[dict[str, Any]]] = (
        ocr_perk_configuration_rows
    ),
) -> dict[str, Any]:
    """Compare captured Home configuration to the semantic profile lists."""

    required_bans, required_auto_pick = (
        normalize_perk_configuration_requirements(requirements)
    )
    bans = (
        dict(captured_bans)
        if isinstance(captured_bans, Mapping)
        else extract_configured_perk_bans(bans_frame, row_fn=row_fn)
    )
    auto_pick = (
        dict(captured_auto_pick)
        if isinstance(captured_auto_pick, Mapping)
        else extract_ranked_auto_pick_order(
            auto_pick_frames,
            ranking_count=len(required_auto_pick),
            row_fn=row_fn,
        )
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
    if field == "perk_bans":
        return _parse_observed_ban_selection(
            frames,
            source_complete=source_complete,
            source_reason=source_reason,
            evidence_images=evidence_images,
            confidence_threshold=confidence_threshold,
            text_fn=text_fn,
        )
    if field == "perk_auto_pick_order":
        return _parse_observed_auto_pick_order(
            frames,
            source_complete=source_complete,
            source_reason=source_reason,
            evidence_images=evidence_images,
            confidence_threshold=confidence_threshold,
            text_fn=text_fn,
        )
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


def _parse_observed_ban_selection(
    frames: Sequence[Frame],
    *,
    source_complete: bool,
    source_reason: str,
    evidence_images: Sequence[str],
    confidence_threshold: float,
    text_fn: Optional[RowTextFn],
) -> dict[str, Any]:
    """Read outlined rows from the Ban tab's Selected Perks section."""

    selected: list[dict[str, Any]] = []
    raw_pages = []
    for page, frame in enumerate(frames, start=1):
        rows = ocr_perk_configuration_rows(frame, text_fn=text_fn)
        raw_pages.append({"page": page, "rows": rows})
        for row in rows:
            if row.get("selected_outline") is not True:
                continue
            entry = _semantic_entry(row)
            existing = next(
                (
                    item
                    for item in selected
                    if perk_entries_match(item, entry)
                ),
                None,
            )
            observation = {
                "page": page,
                "top": entry["top"],
                "confidence": entry["confidence"],
            }
            if existing is not None:
                existing["observations"].append(observation)
                if entry["confidence"] > existing["confidence"]:
                    observations = existing["observations"]
                    existing.update(entry)
                    existing["observations"] = observations
                continue
            entry.update(
                rank=len(selected) + 1,
                page=page,
                observations=[observation],
            )
            selected.append(entry)
    return _observed_configuration_result(
        field="perk_bans",
        selected=selected,
        raw_pages=raw_pages,
        source_complete=source_complete,
        source_reason=source_reason,
        evidence_images=evidence_images,
        confidence_threshold=confidence_threshold,
        boundary_seen=True,
    )


def _parse_observed_auto_pick_order(
    frames: Sequence[Frame],
    *,
    source_complete: bool,
    source_reason: str,
    evidence_images: Sequence[str],
    confidence_threshold: float,
    text_fn: Optional[RowTextFn],
) -> dict[str, Any]:
    """Read every ranked Auto Pick row above the Rankings Unlocked divider."""

    selected: list[dict[str, Any]] = []
    raw_pages = []
    boundary_seen = False
    for page, frame in enumerate(frames, start=1):
        rows = ocr_perk_configuration_rows(frame, text_fn=text_fn)
        boundary_y = detect_auto_pick_ranking_boundary(frame)
        ranked_rows = (
            [row for row in rows if int(row.get("bottom") or 0) < boundary_y]
            if boundary_y is not None
            else rows
        )
        raw_pages.append(
            {
                "page": page,
                "rows": rows,
                "ranking_boundary_y": boundary_y,
            }
        )
        for row in ranked_rows:
            entry = _semantic_entry(row)
            existing = next(
                (
                    item
                    for item in selected
                    if perk_entries_match(item, entry)
                ),
                None,
            )
            observation = {
                "page": page,
                "top": entry["top"],
                "confidence": entry["confidence"],
            }
            if existing is not None:
                existing["observations"].append(observation)
                if entry["confidence"] > existing["confidence"]:
                    observations = existing["observations"]
                    existing.update(entry)
                    existing["observations"] = observations
                continue
            entry.update(
                rank=len(selected) + 1,
                page=page,
                observations=[observation],
            )
            selected.append(entry)
        if boundary_y is not None:
            boundary_seen = True
            break
    return _observed_configuration_result(
        field="perk_auto_pick_order",
        selected=selected,
        raw_pages=raw_pages,
        source_complete=source_complete,
        source_reason=source_reason,
        evidence_images=evidence_images,
        confidence_threshold=confidence_threshold,
        boundary_seen=boundary_seen,
    )


def _observed_configuration_result(
    *,
    field: str,
    selected: list[dict[str, Any]],
    raw_pages: list[dict[str, Any]],
    source_complete: bool,
    source_reason: str,
    evidence_images: Sequence[str],
    confidence_threshold: float,
    boundary_seen: bool,
) -> dict[str, Any]:
    low_confidence = [
        str(item.get("key") or item.get("display_text") or "unknown")
        for item in selected
        if _semantic_entry_is_low_confidence(
            item,
            confidence_threshold=confidence_threshold,
        )
    ]
    unrecognized = [
        str(item.get("display_text") or "unknown")
        for item in selected
        if item.get("key") is None
    ]
    semantic_conflicts = [
        str(item.get("display_text") or "unknown")
        for item in selected
        if item.get("semantic_conflict") is True
    ]
    warnings = []
    if not source_complete:
        warnings.append(f"Perks configuration capture was incomplete: {source_reason}")
    if field == "perk_auto_pick_order" and not boundary_seen:
        warnings.append("Auto Pick ranking boundary was not recognized")
    if field == "perk_auto_pick_order" and not selected:
        warnings.append("Auto Pick capture contained no ranked priority rows")
    if low_confidence:
        warnings.append(
            "Low-confidence configured perks: " + ", ".join(low_confidence)
        )
    if unrecognized:
        warnings.append(
            "Unrecognized configured perks: " + ", ".join(unrecognized)
        )
    if semantic_conflicts:
        warnings.append(
            "Conflicting configured Perk OCR: "
            + ", ".join(semantic_conflicts)
        )
    return {
        "source_method": "scrolling_screenshot_semantic_ocr",
        "page_count": len(raw_pages),
        "order_semantics": ORDER_SEMANTICS[field],
        "selected": selected,
        "evidence_images": list(evidence_images),
        "raw_pages": raw_pages,
        "quality": {
            "valid": not warnings,
            "source_complete": bool(source_complete),
            "source_reason": source_reason,
            "confidence_threshold": float(confidence_threshold),
            "selected_count": len(selected),
            "ranking_boundary_seen": (
                boundary_seen if field == "perk_auto_pick_order" else None
            ),
            "low_confidence": low_confidence,
            "unrecognized": unrecognized,
            "semantic_conflicts": semantic_conflicts,
            "closest_matches": _ocr_recovery_candidates(selected),
            "ocr_retry_recommended": bool(warnings),
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


def _ocr_recovery_candidates(
    entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    recovered = []
    for entry in entries:
        closest = entry.get("closest_match")
        if not isinstance(closest, Mapping) or closest.get("key") is None:
            continue
        recovered.append(
            {
                "display_text": str(entry.get("display_text") or ""),
                "semantic_key": entry.get("key"),
                "match_method": str(
                    entry.get("match_method") or "unrecognized"
                ),
                "suggested_key": str(closest["key"]),
                "suggested_label": str(closest.get("label") or ""),
                "score": float(closest.get("score") or 0.0),
                "margin": float(closest.get("margin") or 0.0),
                "accepted": closest.get("accepted") is True,
                "retry_recommended": (
                    entry.get("ocr_retry_recommended") is True
                ),
            }
        )
    return recovered


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
    resolutions = []
    for item in candidates:
        text = str(
            item.get("display_text") or item.get("text_raw") or ""
        )
        exact_key = classify_perk_configuration_text(text)
        closest = closest_perk_configuration_match(text)
        proposed_key = exact_key
        if proposed_key is None and closest["accepted"] is True:
            proposed_key = str(closest["key"])
        resolutions.append(
            {
                "item": item,
                "exact_key": exact_key,
                "closest": closest,
                "proposed_key": proposed_key,
            }
        )

    source_resolutions = resolutions[: len(source_candidates)]
    source_proposals = {
        str(resolution["proposed_key"])
        for resolution in source_resolutions
        if resolution["proposed_key"] is not None
    }
    semantic_conflict = len(source_proposals) > 1
    exact_known = [
        resolution
        for resolution in resolutions
        if resolution["exact_key"] is not None
    ]
    semantic_key: str | None = None
    match_method = "unrecognized"
    if exact_known and not semantic_conflict:
        best_resolution = max(
            exact_known,
            key=lambda resolution: float(
                resolution["item"].get("confidence") or -1.0
            ),
        )
        semantic_key = str(best_resolution["exact_key"])
        match_method = "exact"
    else:
        fuzzy_sources: dict[str, set[int]] = {}
        for resolution in source_resolutions:
            if resolution["exact_key"] is not None:
                continue
            closest = resolution["closest"]
            text_x1 = resolution["item"].get("text_x1")
            if (
                closest["accepted"] is not True
                or closest["key"] is None
                or not isinstance(text_x1, (int, float))
            ):
                continue
            fuzzy_sources.setdefault(str(closest["key"]), set()).add(
                int(text_x1)
            )
        agreed_fuzzy = [
            key for key, crop_starts in fuzzy_sources.items()
            if len(crop_starts) >= 2
        ]
        if len(agreed_fuzzy) == 1 and not semantic_conflict:
            semantic_key = agreed_fuzzy[0]
            match_method = "closest_agreement"

    matching_resolutions = [
        resolution
        for resolution in resolutions
        if semantic_key is not None
        and resolution["proposed_key"] == semantic_key
    ]
    best_resolution = max(
        matching_resolutions or resolutions,
        key=lambda resolution: float(
            resolution["item"].get("confidence") or -1.0
        ),
    )
    best = best_resolution["item"]
    closest_resolution = max(
        resolutions,
        key=lambda resolution: (
            float(resolution["closest"].get("score") or 0.0),
            float(resolution["closest"].get("margin") or 0.0),
            float(resolution["item"].get("confidence") or -1.0),
        ),
    )
    closest_match = dict(closest_resolution["closest"])
    display_text = str(
        best.get("display_text") or best.get("text_raw") or ""
    ).strip()
    semantic_agreement = len(
        {
            int(resolution["item"]["text_x1"])
            for resolution in source_resolutions
            if semantic_key is not None
            and resolution["proposed_key"] == semantic_key
            and isinstance(resolution["item"].get("text_x1"), (int, float))
        }
    )
    if semantic_key is not None and source_candidates and not semantic_agreement:
        semantic_agreement = sum(
            1
            for resolution in source_resolutions
            if resolution["proposed_key"] == semantic_key
        )
    if not source_candidates and semantic_key is not None:
        semantic_agreement = 1
    ocr_retry_recommended = bool(
        semantic_conflict
        or (
            semantic_key is None
            and closest_match.get("retry_recommended") is True
        )
    )
    return {
        "key": semantic_key,
        "display_text": display_text,
        "text_raw": str(best.get("text_raw") or display_text),
        "confidence": float(best.get("confidence") or -1.0),
        "semantic_agreement": semantic_agreement,
        "match_method": match_method,
        "semantic_conflict": semantic_conflict,
        "closest_match": closest_match,
        "ocr_retry_recommended": ocr_retry_recommended,
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
    if entry.get("semantic_conflict") is True:
        return True
    if (
        entry.get("match_method") == "closest_agreement"
        and int(entry.get("semantic_agreement") or 0) < 2
    ):
        return True
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


def _semantic_comparison_text(text: str) -> str:
    """Normalize OCR for semantic similarity while discarding perk values."""

    tokens = []
    for token in _comparison_text(text).split():
        compact = token.strip(".%+-")
        if (
            not compact
            or compact in {"x", "u"}
            or any(character.isdigit() for character in compact)
        ):
            continue
        tokens.append(compact)
    return " ".join(tokens)


__all__ = [
    "CLOSEST_MATCH_MIN_MARGIN",
    "CLOSEST_MATCH_MIN_SCORE",
    "CLOSEST_MATCH_RETRY_MARGIN",
    "CLOSEST_MATCH_RETRY_SCORE",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "FARM_AUTO_PICK_ORDER",
    "FARM_PERK_BANS",
    "MAX_SELECTED_BACKGROUND_VALUE",
    "ORDER_SEMANTICS",
    "PERK_BAN_CAPACITY",
    "PERK_CONFIGURATION_LABELS",
    "PERK_CONFIGURATION_OCR_EXEMPLARS",
    "classify_perk_configuration_text",
    "closest_perk_configuration_match",
    "detect_auto_pick_ranking_boundary",
    "evaluate_profile_perk_configuration",
    "extract_configured_perk_bans",
    "extract_ranked_auto_pick_order",
    "normalize_perk_configuration_requirements",
    "normalize_perk_first_choice_requirement",
    "parse_perk_configuration_selection",
    "perk_configuration_label",
    "perk_entries_match",
    "semantic_perk_entry",
]
