# handlers/ad_gem_handler.py

from core.clickmap_access import tap_now
from utils.logger import log
import time

def handle_ad_gem():
    log("Handling AD_GEMS_AVAILABLE overlay", "ACTION")
    tap_now("overlays.claim_ad_gem")
    time.sleep(1)  # optional: let UI stabilize

