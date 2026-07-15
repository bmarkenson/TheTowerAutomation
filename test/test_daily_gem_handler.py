from unittest.mock import patch

import numpy as np

from core.scrolling import ScrollResult
from handlers.daily_gem_handler import (
    DAILY_GEM_BUTTON,
    DAILY_GEM_NOT_READY,
    DailyGemResult,
    STORE_MENU_INDICATOR,
    _daily_gem_unavailable,
    _open_store_for_current_screen,
    _return_from_store,
    handle_daily_gem,
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
        assert _open_store_for_current_screen() == "HOME_SCREEN"

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
        assert _open_store_for_current_screen() == "RUNNING"

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
        assert _open_store_for_current_screen() is None

    safe_tap.assert_not_called()
    tap_if_visible.assert_not_called()


def test_free_card_ocr_is_a_normal_not_ready_state():
    with patch(
        "handlers.daily_gem_handler.ocr_text_and_conf",
        return_value=("gem x 15 FREE 2h 49m", 80.8),
    ):
        assert _daily_gem_unavailable(_screenshot()) == DAILY_GEM_NOT_READY


def test_free_price_without_countdown_keeps_scrolling_for_claim():
    with patch(
        "handlers.daily_gem_handler.ocr_text_and_conf",
        return_value=("gem x 15 FREE CLAIM", 91.2),
    ):
        assert _daily_gem_unavailable(_screenshot()) is None


def test_unrelated_offer_timer_before_free_price_is_not_a_cooldown():
    with patch(
        "handlers.daily_gem_handler.ocr_text_and_conf",
        return_value=("Time left 9d 23h gem x 15 FREE CLAIM", 86.7),
    ):
        assert _daily_gem_unavailable(_screenshot()) is None


def test_colon_countdown_after_free_price_is_not_ready():
    with patch(
        "handlers.daily_gem_handler.ocr_text_and_conf",
        return_value=("gem x 15 FREE 02:49:10", 88.0),
    ):
        assert _daily_gem_unavailable(_screenshot()) == DAILY_GEM_NOT_READY


def test_visible_claim_at_store_entry_skips_all_scrolling():
    screenshot = _screenshot()

    def visible(label, *, screenshot=None):
        return label in {STORE_MENU_INDICATOR, DAILY_GEM_BUTTON}

    with (
        patch("handlers.daily_gem_handler._make_session_id", return_value="test"),
        patch(
            "handlers.daily_gem_handler._open_store_for_current_screen",
            return_value="RUNNING",
        ),
        patch("handlers.daily_gem_handler._wait_for_label", return_value=True),
        patch("handlers.daily_gem_handler.capture_adb_screenshot", return_value=screenshot),
        patch("handlers.daily_gem_handler.is_visible", side_effect=visible),
        patch("handlers.daily_gem_handler.scroll_to_edge") as scroll_to_edge,
        patch("handlers.daily_gem_handler.scroll_until_visible") as scroll_until_visible,
        patch("handlers.daily_gem_handler.tap_if_visible", return_value=True) as tap,
        patch(
            "handlers.daily_gem_handler._return_from_store",
            return_value=True,
        ) as return_from_store,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem()

    scroll_to_edge.assert_not_called()
    scroll_until_visible.assert_not_called()
    assert result == DailyGemResult.CLAIMED
    assert [call.args[0] for call in tap.call_args_list] == [
        DAILY_GEM_BUTTON,
        "buttons.skip:claim_daily_gems",
    ]
    return_from_store.assert_called_once_with("test", "RUNNING")


def test_confirmed_cooldown_returns_not_ready_result():
    screenshot = _screenshot()

    def visible(label, *, screenshot=None):
        return label == STORE_MENU_INDICATOR

    with (
        patch("handlers.daily_gem_handler._make_session_id", return_value="test"),
        patch(
            "handlers.daily_gem_handler._open_store_for_current_screen",
            return_value="RUNNING",
        ),
        patch("handlers.daily_gem_handler._wait_for_label", return_value=True),
        patch("handlers.daily_gem_handler.capture_adb_screenshot", return_value=screenshot),
        patch("handlers.daily_gem_handler.is_visible", side_effect=visible),
        patch(
            "handlers.daily_gem_handler.scroll_to_edge",
            return_value=ScrollResult(True, screenshot, 1, "edge_reached"),
        ),
        patch(
            "handlers.daily_gem_handler.scroll_until_visible",
            return_value=ScrollResult(False, screenshot, 0, DAILY_GEM_NOT_READY),
        ),
        patch(
            "handlers.daily_gem_handler._return_from_store",
            return_value=True,
        ) as return_from_store,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem()

    assert result == DailyGemResult.NOT_READY
    return_from_store.assert_called_once_with("test", "RUNNING")


def test_confirmed_cooldown_fails_when_store_return_is_unavailable():
    screenshot = _screenshot()

    def visible(label, *, screenshot=None):
        return label == STORE_MENU_INDICATOR

    with (
        patch("handlers.daily_gem_handler._make_session_id", return_value="test"),
        patch(
            "handlers.daily_gem_handler._open_store_for_current_screen",
            return_value="RUNNING",
        ),
        patch("handlers.daily_gem_handler._wait_for_label", return_value=True),
        patch("handlers.daily_gem_handler.capture_adb_screenshot", return_value=screenshot),
        patch("handlers.daily_gem_handler.is_visible", side_effect=visible),
        patch(
            "handlers.daily_gem_handler.scroll_to_edge",
            return_value=ScrollResult(True, screenshot, 1, "edge_reached"),
        ),
        patch(
            "handlers.daily_gem_handler.scroll_until_visible",
            return_value=ScrollResult(False, screenshot, 0, DAILY_GEM_NOT_READY),
        ),
        patch(
            "handlers.daily_gem_handler._return_from_store",
            return_value=False,
        ) as return_from_store,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem()

    assert result == DailyGemResult.FAILED
    return_from_store.assert_called_once_with("test", "RUNNING")


def test_home_origin_returns_through_bottom_navigation_and_verifies_home():
    screenshot = _screenshot()
    with (
        patch("handlers.daily_gem_handler.safe_tap", return_value=True) as safe_tap,
        patch("handlers.daily_gem_handler.tap_if_visible") as tap_if_visible,
        patch("handlers.daily_gem_handler.capture_adb_screenshot", return_value=screenshot),
        patch(
            "handlers.daily_gem_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        assert _return_from_store("test", "HOME_SCREEN")

    safe_tap.assert_called_once_with(
        "navigation.goto_home_store",
        require_visible=False,
        dispatch="now",
    )
    tap_if_visible.assert_not_called()


def test_running_origin_returns_through_in_run_control_and_verifies_running():
    screenshot = _screenshot()
    with (
        patch("handlers.daily_gem_handler.safe_tap") as safe_tap,
        patch("handlers.daily_gem_handler.tap_if_visible", return_value=True) as tap,
        patch("handlers.daily_gem_handler.capture_adb_screenshot", return_value=screenshot),
        patch(
            "handlers.daily_gem_handler.detect_state_and_overlays",
            return_value={"state": "RUNNING"},
        ),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        assert _return_from_store("test", "RUNNING")

    tap.assert_called_once_with("buttons.return_to_game", retries=1)
    safe_tap.assert_not_called()
