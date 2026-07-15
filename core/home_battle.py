"""Read-only classification of the Home screen's Battle/Resume control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.battle_lifecycle import HomeBattleControl
from core.matcher import MatchResult, get_match_result
from utils.ocr_utils import ocr_text_and_conf


HOME_BATTLE_CONTROL_REGION = (270, 1450, 540, 210)


@dataclass(frozen=True)
class HomeBattleEvidence:
    control: HomeBattleControl
    source: str
    confidence: float = -1.0
    raw_text: str = ""


def _template_result(key: str, screenshot) -> Optional[MatchResult]:
    try:
        return get_match_result(key, screenshot=screenshot)
    except Exception:
        return None


def detect_home_battle_control(screenshot) -> HomeBattleEvidence:
    """Classify Home as offering a new battle, a resume, or unknown.

    OCR is authoritative when it reads one of the two semantic labels. Template
    matching is the fallback. If both templates match and OCR cannot
    disambiguate them, the result remains UNKNOWN rather than inventing a run
    boundary.
    """

    if screenshot is None or not hasattr(screenshot, "shape") or len(screenshot.shape) < 2:
        return HomeBattleEvidence(HomeBattleControl.UNKNOWN, "invalid_screenshot")

    x, y, w, h = HOME_BATTLE_CONTROL_REGION
    screen_h, screen_w = screenshot.shape[:2]
    if x < 0 or y < 0 or x + w > screen_w or y + h > screen_h:
        return HomeBattleEvidence(HomeBattleControl.UNKNOWN, "region_out_of_bounds")

    raw_text = ""
    ocr_confidence = -1.0
    try:
        raw_text, ocr_confidence = ocr_text_and_conf(
            screenshot[y : y + h, x : x + w],
            psm=7,
        )
    except Exception:
        pass

    normalized = " ".join(raw_text.upper().split())
    if "RESUME BATTLE" in normalized:
        return HomeBattleEvidence(
            HomeBattleControl.RESUME_BATTLE,
            "ocr",
            ocr_confidence,
            raw_text,
        )
    if normalized == "BATTLE":
        return HomeBattleEvidence(
            HomeBattleControl.NEW_BATTLE,
            "ocr",
            ocr_confidence,
            raw_text,
        )

    battle = _template_result("buttons.battle:home", screenshot)
    resume = _template_result("buttons.resume_battle:home", screenshot)
    battle_matched = bool(battle and battle.matched)
    resume_matched = bool(resume and resume.matched)

    if battle_matched and not resume_matched:
        return HomeBattleEvidence(
            HomeBattleControl.NEW_BATTLE,
            "template",
            battle.confidence,
            raw_text,
        )
    if resume_matched and not battle_matched:
        return HomeBattleEvidence(
            HomeBattleControl.RESUME_BATTLE,
            "template",
            resume.confidence,
            raw_text,
        )

    return HomeBattleEvidence(
        HomeBattleControl.UNKNOWN,
        "ambiguous_templates" if battle_matched and resume_matched else "no_match",
        max(
            battle.confidence if battle else -1.0,
            resume.confidence if resume else -1.0,
        ),
        raw_text,
    )


__all__ = [
    "HOME_BATTLE_CONTROL_REGION",
    "HomeBattleEvidence",
    "detect_home_battle_control",
]
