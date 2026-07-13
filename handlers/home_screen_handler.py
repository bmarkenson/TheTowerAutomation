# handlers/home_screen_handler.py

import time
from utils.logger import log
from core.input import safe_tap, tap_if_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.ocr_utils import ocr_text_and_conf


HOME_BATTLE_CONTROL_REGION = (270, 1450, 540, 210)


def _tap_verified_home_battle_control() -> bool:
    """OCR and tap Battle/Resume only on a verified home screen."""

    screenshot = capture_adb_screenshot()
    if screenshot is None:
        return False
    detection = detect_state_and_overlays(screenshot)
    if detection["state"] != "HOME_SCREEN":
        log(
            f"[HOME] Refusing Battle fallback from state={detection['state']!r}",
            "WARN",
        )
        return False
    x, y, w, h = HOME_BATTLE_CONTROL_REGION
    text, confidence = ocr_text_and_conf(screenshot[y:y + h, x:x + w], psm=7)
    normalized = " ".join(text.upper().split())
    if "RESUME BATTLE" in normalized:
        action_text = "RESUME BATTLE"
    elif normalized == "BATTLE":
        action_text = "BATTLE"
    else:
        log(
            f"[HOME] Battle fallback OCR was {text!r} "
            f"(confidence={confidence:.1f}); refusing tap",
            "WARN",
        )
        return False
    log(
        f"[HOME] Verified {action_text!r} by OCR (confidence={confidence:.1f})",
        "DEBUG",
    )
    return safe_tap(
        "buttons.battle_control:home",
        require_visible=False,
        dispatch="now",
    )


def handle_home_screen(restart_enabled=True):
    """
    Handle the HOME_SCREEN state by optionally starting a battle.

    Args:
        restart_enabled (bool, optional):
            When True (default), taps the 'Battle' button to auto-start gameplay.
            When False, does nothing beyond logging (awaits manual start).

    Returns:
        None — handler effects only.

    Side effects:
        [tap] Taps the Battle button when restart_enabled=True.
        [log] Emits INFO logs.
        (Also sleeps ≈2s after tapping to allow UI to transition.)

    Defaults:
        restart_enabled=True; adds a ~2s pause after tapping when enabled.

    Errors:
        None material; tap failures (if any) are not explicitly handled here.
    """
    log("[HOME] Handling HOME_SCREEN state", "INFO")

    if restart_enabled:
        log("[HOME] Auto-start enabled — tapping 'Battle' button", "INFO")
        if not tap_if_visible("buttons.battle:home", retries=1):
            if not tap_if_visible("buttons.resume_battle:home", retries=1):
                if not _tap_verified_home_battle_control():
                    log("[HOME] Battle/Resume controls not verified; leaving handler", "WARN")
        time.sleep(2)
    else:
        log("[HOME] Auto-start disabled — waiting for manual start.", "INFO")
