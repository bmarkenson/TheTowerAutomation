from pathlib import Path
from unittest.mock import patch

import cv2

from core.input import TapVerification
from handlers.dismiss_uw_detail import handle_upgrade_detail_popup


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ui_state_20260714"


def _load(name: str):
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None, f"fixture is unreadable: {name}"
    return image


def test_generic_upgrade_detail_uses_generalized_dismiss_target():
    detail = _load("active_upgrade_detail_health.png")
    cleared = _load("active_wave_info.png")

    with patch("handlers.dismiss_uw_detail.safe_tap", return_value=True) as safe_tap:
        result = handle_upgrade_detail_popup(
            screenshot=detail,
            capture_fn=lambda: cleared,
            sleep_fn=lambda _seconds: None,
        )

    assert result is cleared
    safe_tap.assert_called_once()
    target, = safe_tap.call_args.args
    kwargs = safe_tap.call_args.kwargs
    assert target == "gesture_targets.upgrade_detail_dismiss"
    assert kwargs["retries"] == 1
    assert kwargs["retry_delay"] == 0.2
    assert kwargs["dispatch"] == "now"
    assert isinstance(kwargs["verification"], TapVerification)


def test_non_detail_screen_is_not_tapped():
    screenshot = _load("active_wave_info.png")

    with patch("handlers.dismiss_uw_detail.safe_tap") as safe_tap:
        result = handle_upgrade_detail_popup(
            screenshot=screenshot,
            capture_fn=lambda: screenshot,
            sleep_fn=lambda _seconds: None,
        )

    assert result is screenshot
    safe_tap.assert_not_called()
