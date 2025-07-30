# main.py

import threading
import time
from datetime import datetime
import os
import cv2
from core.watchdog import watchdog_process_check
from core.ss_capture import capture_adb_screenshot
from core.automation_state import AUTOMATION
from core.state_detector import detect_state
from handlers.game_over_handler import handle_game_over
from handlers.home_screen_handler import handle_home_screen
from utils.logger import log
import argparse

SCREENSHOT_PATH = "screenshots/latest.png"

parser = argparse.ArgumentParser()
parser.add_argument("--no-restart", action="store_true", help="Disable auto restart on home screen")
args = parser.parse_args()
AUTO_START_ENABLED = not args.no_restart
log(f"AUTO_START_ENABLED = {AUTO_START_ENABLED}", "DEBUG")

AUTO_START_ENABLED = not args.no_restart

def main():
    log("Starting main heartbeat loop.", level="INFO")
    threading.Thread(target=watchdog_process_check, daemon=True).start()

    while True:
        img = capture_adb_screenshot()
        if img is None:
            log("Failed to capture screenshot.", level="FAIL")
            time.sleep(2)
            continue

        log(f"Captured screenshot: shape={img.shape}", level="DEBUG")
        try:
            os.makedirs(os.path.dirname(SCREENSHOT_PATH), exist_ok=True)
            cv2.imwrite(SCREENSHOT_PATH, img)
            log(f"Saved screenshot to {SCREENSHOT_PATH}", level="DEBUG")
        except Exception as e:
            log(f"Error saving screenshot: {e}", level="FAIL")

        # Detect current state from image
        new_state = detect_state(img)
        old_state = AUTOMATION.get_state()
        if new_state != old_state:
            log(f"State change: {old_state} → {new_state}", "STATE")
            AUTOMATION.set_state(new_state)

        # Handle known states
        if new_state == "GAME_OVER":
            log("Detected GAME OVER. Executing handler.", "INFO")
            handle_game_over()
        elif new_state == "HOME_SCREEN":
            log("Detected HOME_SCREEN. Executing handler.", "INFO")
            handle_home_screen(restart_enabled=AUTO_START_ENABLED)

        time.sleep(5)

if __name__ == "__main__":
    main()


