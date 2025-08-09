#!/usr/bin/env python3
# test/test_game_over_handler.py

import time
from handlers.game_over_handler import handle_game_over
from core.automation_state import AUTOMATION
from utils.logger import log

def run_test():
    log("[TEST] Starting Game Over handler test", "INFO")

    # Set mode to WAIT for safety
    AUTOMATION.set_mode("WAIT")
    log(f"[TEST] Automation mode set to: {AUTOMATION.get_mode()}", "INFO")

    try:
        handle_game_over()
    except Exception as e:
        log(f"[TEST] Exception raised during handler: {e}", "ERROR")

    log(f"[TEST] Final automation mode: {AUTOMATION.get_mode()}", "INFO")
    log("[TEST] Game Over handler test complete", "INFO")

if __name__ == "__main__":
    run_test()


