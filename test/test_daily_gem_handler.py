from unittest.mock import patch

import numpy as np

from handlers.daily_gem_handler import (
    DAILY_GEM_NOT_READY,
    _daily_gem_unavailable,
    _open_store_for_current_screen,
)


def _screenshot():
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def test_home_screen_uses_bottom_store_navigation():
    with (
        patch("handlers.daily_gem_handler.capture_adb_screenshot", return_value=_screenshot()),
        patch(
            "handlers.daily_gem_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch("handlers.daily_gem_handler.safe_tap", return_value=True) as safe_tap,
        patch("handlers.daily_gem_handler.tap_if_visible") as tap_if_visible,
    ):
        assert _open_store_for_current_screen()

    safe_tap.assert_called_once_with(
        "navigation.goto_store_home",
        require_visible=False,
        dispatch="now",
    )
    tap_if_visible.assert_not_called()


def test_running_screen_uses_gold_cart_template():
    screenshot = _screenshot()
    with (
        patch("handlers.daily_gem_handler.capture_adb_screenshot", return_value=screenshot),
        patch(
            "handlers.daily_gem_handler.detect_state_and_overlays",
            return_value={"state": "RUNNING"},
        ),
        patch("handlers.daily_gem_handler.safe_tap") as safe_tap,
        patch("handlers.daily_gem_handler.tap_if_visible", return_value=True) as tap_if_visible,
    ):
        assert _open_store_for_current_screen()

    tap_if_visible.assert_called_once_with(
        "navigation.goto_store",
        screenshot=screenshot,
        retries=1,
    )
    safe_tap.assert_not_called()


def test_unknown_screen_refuses_store_navigation():
    with (
        patch("handlers.daily_gem_handler.capture_adb_screenshot", return_value=_screenshot()),
        patch(
            "handlers.daily_gem_handler.detect_state_and_overlays",
            return_value={"state": "UNKNOWN"},
        ),
        patch("handlers.daily_gem_handler.safe_tap") as safe_tap,
        patch("handlers.daily_gem_handler.tap_if_visible") as tap_if_visible,
    ):
        assert not _open_store_for_current_screen()

    safe_tap.assert_not_called()
    tap_if_visible.assert_not_called()


def test_free_card_ocr_is_a_normal_not_ready_state():
    with patch(
        "handlers.daily_gem_handler.ocr_text_and_conf",
        return_value=("gem x 15 FREE 2h 49m", 80.8),
    ):
        assert _daily_gem_unavailable(_screenshot()) == DAILY_GEM_NOT_READY
