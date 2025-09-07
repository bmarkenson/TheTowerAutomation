# handlers/home_screen_handler.py

import time
from utils.logger import log
from core.clickmap_access import tap_now
from core.label_tapper import tap_label_now


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
        if not tap_label_now("buttons.battle:home"):
            tap_label_now("buttons.resume_battle:home")
        time.sleep(2)
    else:
        log("[HOME] Auto-start disabled — waiting for manual start.", "INFO")
