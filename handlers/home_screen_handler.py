# handlers/home_screen_handler.py

import time
from utils.logger import log
from core.clickmap_access import tap_now, swipe_now

def handle_home_screen(restart_enabled=True):
    log("[HOME] Handling HOME_SCREEN state", "INFO")

    if restart_enabled:
        log("[HOME] Auto-start enabled — tapping 'Battle' button", "INFO")
        tap_now("Battle")
        time.sleep(2)
    else:
        log("[HOME] Auto-start disabled — waiting for manual start.", "INFO")
