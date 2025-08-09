#!/usr/bin/env python3
# main.py

import threading
import time
from datetime import datetime
import os
import cv2
import argparse

from core.watchdog import watchdog_process_check
from core.ss_capture import capture_and_save_screenshot
from core.automation_state import AUTOMATION
from core.state_detector import detect_state_and_overlays
from handlers.game_over_handler import handle_game_over
from handlers.home_screen_handler import handle_home_screen
from handlers.ad_gem_handler import handle_ad_gem, stop_blind_gem_tapper
from utils.logger import log

SCREENSHOT_PATH = "screenshots/latest.png"

parser = argparse.ArgumentParser()
parser.add_argument("--no-restart", action="store_true", help="Disable auto restart on home screen")
args = parser.parse_args()
AUTO_START_ENABLED = not args.no_restart
log(f"AUTO_START_ENABLED = {AUTO_START_ENABLED}", "DEBUG")


def main():
    log("Starting main heartbeat loop.", level="INFO")
    threading.Thread(target=watchdog_process_check, daemon=True).start()

    last_ui_state = None
    try:
        while True:
            img = capture_and_save_screenshot()
            if img is None:
                log("Failed to capture screenshot.", level="FAIL")
                time.sleep(2)
                continue

            # Detect current state from image
            detection = detect_state_and_overlays(img)
            new_state = detection["state"]           # e.g., "GAME_OVER", "HOME_SCREEN"
            overlays = detection["overlays"]

            if new_state != last_ui_state:
                log(f"UI state change: {last_ui_state} → {new_state}", "STATE")
                last_ui_state = new_state

            # Handle known states
            if new_state == "GAME_OVER":
                log("Detected GAME_OVER. Executing handler.", "INFO")
                handle_game_over()
            elif new_state == "HOME_SCREEN":
                log("Detected HOME_SCREEN. Executing handler.", "INFO")
                handle_home_screen(restart_enabled=AUTO_START_ENABLED)

            if "AD_GEMS_AVAILABLE" in overlays:
                handle_ad_gem()

            time.sleep(5)  # Ctrl+C interrupts here immediately
    except KeyboardInterrupt:
        log("KeyboardInterrupt — shutting down.", "INFO")
    finally:
        stop_blind_gem_tapper()
        log("Exited cleanly.", "INFO")


if __name__ == "__main__":
    main()
