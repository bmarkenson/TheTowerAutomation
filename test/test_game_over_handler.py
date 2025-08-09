#!/usr/bin/env python3
# test/test_game_over_handler.py

import traceback
from handlers.game_over_handler import handle_game_over
from core.automation_state import AUTOMATION, ExecMode
from utils.logger import log


def run_test():
    """
    Exercise the Game Over handler safely.

    Behavior:
      - Saves current AUTOMATION.mode, switches to WAIT for safety, runs handle_game_over(),
        logs any exception with traceback, then restores the original mode.

    Returns:
      Action result (logs lifecycle; no explicit return).

    Side effects:
      Temporarily mutates global automation mode; invokes handler which may perform ADB I/O,
      template matching, taps/swipes, and filesystem writes.

    Errors:
      Exceptions from the handler are caught and logged; original mode is always restored.
    """
    log("[TEST] Starting Game Over handler test", "INFO")

    original_mode = AUTOMATION.mode
    AUTOMATION.mode = ExecMode.WAIT
    log(f"[TEST] Automation mode set to: {AUTOMATION.mode.value}", "INFO")

    try:
        handle_game_over()
    except Exception as e:
        log(f"[TEST] Exception raised during handler: {e}", "ERROR")
        log(traceback.format_exc(), "ERROR")
    finally:
        AUTOMATION.mode = original_mode
        log(f"[TEST] Automation mode restored to: {AUTOMATION.mode.value}", "INFO")

    log("[TEST] Game Over handler test complete", "INFO")


if __name__ == "__main__":
    run_test()
