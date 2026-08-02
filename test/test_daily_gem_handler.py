from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from core.label_tapper import get_label_match
from core.scrolling import ScrollResult
from handlers.daily_gem_handler import (
    DAILY_GEM_BUTTON,
    DAILY_GEM_NOT_READY,
    DailyGemCleanupResult,
    DailyGemResult,
    STORE_MENU_INDICATOR,
    _daily_gem_unavailable,
    _open_store_for_current_screen,
    _return_from_store,
    handle_daily_gem,
    resume_daily_gem_cleanup,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _screenshot():
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def test_authority_loss_before_claim_stops_input_and_retains_store_cleanup():
    screenshot = _screenshot()
    guard = Mock(side_effect=[True, False])
    route_state = Mock()
    with (
        patch(
            "handlers.daily_gem_handler._open_store_for_current_screen",
            return_value="RUNNING",
        ),
        patch("handlers.daily_gem_handler._wait_for_label", return_value=True),
        patch(
            "handlers.daily_gem_handler.capture_adb_screenshot",
            return_value=screenshot,
        ),
        patch(
            "handlers.daily_gem_handler.is_visible",
            side_effect=lambda label, **_kwargs: label
            in {STORE_MENU_INDICATOR, DAILY_GEM_BUTTON},
        ),
        patch("handlers.daily_gem_handler._daily_gem_unavailable", return_value=None),
        patch("handlers.daily_gem_handler.tap_if_visible") as tap,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem(
            action_guard_fn=guard,
            route_state_callback=route_state,
        )

    assert result is DailyGemResult.INTERRUPTED
    tap.assert_not_called()
    route_state.assert_any_call("STORE", True, None)
    route_state.assert_any_call(
        "STORE",
        True,
        "auxiliary authority was lost before claim",
    )


def test_daily_cleanup_abandons_unexpected_boundary_without_input():
    with (
        patch(
            "handlers.daily_gem_handler.capture_adb_screenshot",
            return_value=_screenshot(),
        ),
        patch(
            "handlers.daily_gem_handler.detect_state_and_overlays",
            return_value={"state": "GAME_OVER"},
        ),
        patch("handlers.daily_gem_handler.is_visible", return_value=False),
        patch("handlers.daily_gem_handler.tap_if_visible") as tap,
        patch("handlers.daily_gem_handler.safe_tap") as safe_tap,
    ):
        result = resume_daily_gem_cleanup(
            "RUNNING",
            action_guard_fn=lambda: True,
        )

    assert result is DailyGemCleanupResult.ABANDONED
    tap.assert_not_called()
    safe_tap.assert_not_called()


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


def test_drifted_live_claim_stays_inside_authoritative_match_region():
    screenshot = cv2.imread(
        str(FIXTURES / "store_daily_gem_claim_drifted_20260728.png")
    )
    assert screenshot is not None

    match = get_label_match(
        DAILY_GEM_BUTTON,
        screenshot=screenshot,
        return_meta=True,
    )

    assert match["y"] == 1112
    assert match["match_score"] >= 0.99


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
        patch("handlers.daily_gem_handler.log_result") as result_log,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem()

    scroll_to_edge.assert_not_called()
    scroll_until_visible.assert_not_called()
    assert result == DailyGemResult.CLAIMED
    assert [call.args[0] for call in tap.call_args_list] == [
        DAILY_GEM_BUTTON,
        "buttons.skip_reward_reveal",
    ]
    return_from_store.assert_called_once_with("test", "RUNNING")
    result_log.assert_called_once_with(
        "Daily Gem check complete — reward claimed",
        detail=(
            "[DAILY_GEM] result=claimed session=test "
            "reason=the reward was claimed and the source screen was restored"
        ),
    )


def test_failed_claim_restores_running_source_before_terminal_result():
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
        patch(
            "handlers.daily_gem_handler.capture_adb_screenshot",
            return_value=screenshot,
        ),
        patch("handlers.daily_gem_handler.is_visible", side_effect=visible),
        patch(
            "handlers.daily_gem_handler.tap_if_visible",
            return_value=False,
        ) as tap,
        patch(
            "handlers.daily_gem_handler._return_from_store",
            return_value=True,
        ) as return_from_store,
        patch("handlers.daily_gem_handler.log_result") as result_log,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem()

    assert result == DailyGemResult.FAILED
    tap.assert_called_once_with(
        DAILY_GEM_BUTTON,
        screenshot=screenshot,
        retries=1,
    )
    return_from_store.assert_called_once_with("test", "RUNNING")
    result_log.assert_called_once_with(
        "Daily Gem check failed — the verified Daily Gem control could not be tapped",
        detail=(
            "[DAILY_GEM] result=failed session=test "
            "reason=the verified Daily Gem control could not be tapped"
        ),
    )


def test_failed_claim_reports_failed_source_cleanup():
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
        patch(
            "handlers.daily_gem_handler.capture_adb_screenshot",
            return_value=screenshot,
        ),
        patch("handlers.daily_gem_handler.is_visible", side_effect=visible),
        patch(
            "handlers.daily_gem_handler.tap_if_visible",
            return_value=False,
        ),
        patch(
            "handlers.daily_gem_handler._return_from_store",
            return_value=False,
        ),
        patch("handlers.daily_gem_handler.log_result") as result_log,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem()

    assert result == DailyGemResult.FAILED
    result_log.assert_called_once_with(
        "Daily Gem check failed — the verified Daily Gem control could not be tapped; "
        "failure cleanup could not return to RUNNING",
        detail=(
            "[DAILY_GEM] result=failed session=test "
            "reason=the verified Daily Gem control could not be tapped; "
            "failure cleanup could not return to RUNNING"
        ),
    )


def test_cooldown_at_store_entry_skips_all_scrolling():
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
            "handlers.daily_gem_handler._daily_gem_unavailable",
            return_value=DAILY_GEM_NOT_READY,
        ),
        patch("handlers.daily_gem_handler.scroll_to_edge") as scroll_to_edge,
        patch("handlers.daily_gem_handler.scroll_until_visible") as scroll_until_visible,
        patch(
            "handlers.daily_gem_handler._return_from_store",
            return_value=True,
        ) as return_from_store,
        patch("handlers.daily_gem_handler.log_result") as result_log,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem()

    assert result == DailyGemResult.NOT_READY
    scroll_to_edge.assert_not_called()
    scroll_until_visible.assert_not_called()
    return_from_store.assert_called_once_with("test", "RUNNING")
    result_log.assert_called_once_with(
        "Daily Gem check complete — reward not ready",
        detail=(
            "[DAILY_GEM] result=not_ready session=test "
            "reason=a cooldown was visible at Store entry"
        ),
    )


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
        patch("handlers.daily_gem_handler.log_result") as result_log,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem()

    assert result == DailyGemResult.NOT_READY
    return_from_store.assert_called_once_with("test", "RUNNING")
    result_log.assert_called_once_with(
        "Daily Gem check complete — reward not ready",
        detail=(
            "[DAILY_GEM] result=not_ready session=test "
            "reason=a cooldown was found while searching the Store"
        ),
    )


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
        patch("handlers.daily_gem_handler.log_result") as result_log,
        patch("handlers.daily_gem_handler.save_image"),
        patch("handlers.daily_gem_handler.time.sleep"),
    ):
        result = handle_daily_gem()

    assert result == DailyGemResult.FAILED
    return_from_store.assert_called_once_with("test", "RUNNING")
    result_log.assert_called_once_with(
        "Daily Gem check failed — the automation could not return to RUNNING",
        detail=(
            "[DAILY_GEM] result=failed session=test "
            "reason=the automation could not return to RUNNING"
        ),
    )


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
