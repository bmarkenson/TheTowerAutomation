from pathlib import Path

import cv2

from core.matcher import get_match
from core.state_detector import detect_state_and_overlays


ROOT = Path(__file__).resolve().parents[1]
GC_ACTIVE_FIXTURE = ROOT / "test" / "fixtures" / "cards_gc_active_20260713.png"
HOME_NEGATIVE_FIXTURE = (
    ROOT / "test" / "fixtures" / "home_screen_new_day_store_badge_20260713.png"
)


def _load(path: Path):
    image = cv2.imread(str(path))
    assert image is not None, f"fixture is unreadable: {path}"
    return image


def test_live_gc_cards_fixture_identifies_active_gc_preset():
    screen = _load(GC_ACTIVE_FIXTURE)

    point, confidence = get_match(
        "indicators.cards:gc_active",
        screenshot=screen,
    )
    detection = detect_state_and_overlays(screen)

    assert point == (118, 420)
    assert confidence >= 0.99
    assert detection["state"] == "CARDS"
    assert detection["secondary_states"] == ["CARDS_GC_ACTIVE"]


def test_gc_active_preset_does_not_match_home_screen():
    point, confidence = get_match(
        "indicators.cards:gc_active",
        screenshot=_load(HOME_NEGATIVE_FIXTURE),
    )

    assert point is None
    assert confidence < 0.9
