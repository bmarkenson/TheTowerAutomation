from unittest.mock import patch

import cv2
import numpy as np

from core.run_controls import _exit_battle_dialog_visible, ensure_menu_open, restart_run


def _frame():
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def test_exit_dialog_ocr_requires_title_and_question():
    with patch(
        "core.run_controls.ocr_text_and_conf",
        return_value=("EXIT BATTLE What would you like to do?", 85.0),
    ):
        assert _exit_battle_dialog_visible(_frame())

    with patch(
        "core.run_controls.ocr_text_and_conf",
        return_value=("EXIT BATTLE", 90.0),
    ):
        assert not _exit_battle_dialog_visible(_frame())


def test_live_exit_dialog_fixture_is_recognized():
    screenshot = cv2.imread("/tmp/thetower_exit_confirm.png")
    if screenshot is not None:
        assert _exit_battle_dialog_visible(screenshot)


def test_ensure_menu_open_only_uses_verified_toggle():
    screenshot = _frame()
    detections = iter(
        (
            {"state": "RUNNING", "overlays": ["MENU_CLOSED"]},
            {"state": "RUNNING", "overlays": ["MENU_OPEN"]},
        )
    )
    with (
        patch("core.run_controls.capture_adb_screenshot", return_value=screenshot),
        patch("core.run_controls.detect_state_and_overlays", side_effect=detections),
        patch("core.run_controls.tap_if_visible", return_value=True) as tap,
        patch("core.run_controls.time.sleep"),
    ):
        assert ensure_menu_open(timeout_s=2.0)

    tap.assert_called_once_with(
        "navigation.menu_open_button",
        screenshot=screenshot,
        retries=1,
    )


def test_restart_run_is_the_surrender_compatibility_action():
    with patch("core.run_controls.surrender_run", return_value=True) as surrender:
        assert restart_run(timeout_s=7.0)
    surrender.assert_called_once_with(timeout_s=7.0)
