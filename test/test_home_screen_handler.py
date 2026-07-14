from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from core.matcher import get_match
from handlers.home_screen_handler import _tap_verified_home_battle_control


ROOT = Path(__file__).resolve().parents[1]
NEW_DAY_HOME_FIXTURE = (
    ROOT / "test" / "fixtures" / "home_screen_new_day_store_badge_20260713.png"
)


def _screenshot():
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def test_verified_home_battle_ocr_fallback_taps_configured_control():
    with (
        patch("handlers.home_screen_handler.capture_adb_screenshot", return_value=_screenshot()),
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.ocr_text_and_conf",
            return_value=("BATTLE", 96.0),
        ),
        patch("handlers.home_screen_handler.safe_tap", return_value=True) as tap,
    ):
        assert _tap_verified_home_battle_control()

    tap.assert_called_once_with(
        "buttons.battle_control:home",
        require_visible=False,
        dispatch="now",
    )


def test_home_battle_fallback_refuses_unknown_screen():
    with (
        patch("handlers.home_screen_handler.capture_adb_screenshot", return_value=_screenshot()),
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "UNKNOWN"},
        ),
        patch("handlers.home_screen_handler.safe_tap") as tap,
    ):
        assert not _tap_verified_home_battle_control()

    tap.assert_not_called()


def test_resume_battle_ocr_tolerates_button_border_artifacts():
    with (
        patch("handlers.home_screen_handler.capture_adb_screenshot", return_value=_screenshot()),
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.ocr_text_and_conf",
            return_value=("L RESUME BATTLE J", 93.75),
        ),
        patch("handlers.home_screen_handler.safe_tap", return_value=True),
    ):
        assert _tap_verified_home_battle_control()


def test_live_new_day_home_fixture_matches_battle_and_store_badge():
    screenshot = cv2.imread(str(NEW_DAY_HOME_FIXTURE))
    assert screenshot is not None

    battle_point, battle_confidence = get_match(
        "buttons.battle:home",
        screenshot=screenshot,
    )
    badge_point, badge_confidence = get_match(
        "overlays.daily_free_gems_badge_home",
        screenshot=screenshot,
    )

    assert battle_point is not None
    assert battle_confidence >= 0.9
    assert badge_point is not None
    assert badge_confidence >= 0.9
