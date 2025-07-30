# handlers/home_screen_handler.py

import time
from utils.logger import log
from core.input_named import tap_named, load_clickmap


def handle_home_screen(restart_enabled=True):
    clickmap = load_clickmap()
    log("[HOME] Handling HOME_SCREEN state", "INFO")

    if restart_enabled:
        log("[HOME] Auto-start enabled — tapping 'Battle' button", "INFO")
        tap_named(clickmap, "Battle")
        time.sleep(2)
    else:
        log("[HOME] Auto-start disabled — waiting for manual start.", "INFO")
