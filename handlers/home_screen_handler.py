# handlers/home_screen_handler.py

import time
from utils.logger import log
from core.input import tap_if_visible


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
                log("[HOME] Battle/Resume buttons not visible; leaving handler", "WARN")
        time.sleep(2)
    else:
        log("[HOME] Auto-start disabled — waiting for manual start.", "INFO")
