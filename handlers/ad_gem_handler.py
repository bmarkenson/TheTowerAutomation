# handlers/ad_gem_handler.py

import threading
import time
from core.tap_dispatcher import tap
from core.clickmap_access import get_click
from core.label_tapper import tap_label_now
from utils.logger import log

def _blind_floating_gem_tapper(duration=20, interval=1):
    """Blindly tap in floating gem region for up to `duration` seconds."""
    coords = get_click("gesture_targets.floating_gem_blind_tap")
    if not coords:
        log("[WARN] No blind tap location defined for floating gem", "WARN")
        return

    x, y = coords
    label = "floating_gem_blind_tap"

    end_time = time.time() + duration
    while time.time() < end_time:
        tap(x, y, label=label)
        time.sleep(interval)

def handle_ad_gem():
    log("Handling AD_GEMS_AVAILABLE overlay", "ACTION")

    # Kick off blind tapping for floating gem every time the ad gem becomes available
    threading.Thread(target=_blind_floating_gem_tapper, daemon=True).start()

    # Tap the ad gem to collect
    tap_label_now("overlays.ad_gem")

    time.sleep(1)

