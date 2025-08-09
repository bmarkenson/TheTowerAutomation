#!/usr/bin/env python3
# automation/run_demon_mode.py

import sys
import os
import time
import argparse
from utils.logger import log

# Add project root to sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

from handlers.mission_demon_mode import run_demon_mode


def main(delay: int = 2, once: bool = False):
    """
    Run the Demon-Mode mission loop.
    - delay: seconds to sleep between iterations
    - once: if True, run a single iteration and exit
    """
    log("[MISSION] Starting Demon-Mode loop. Ctrl+C to stop.", "INFO")
    while True:
        try:
            run_demon_mode()
            if once:
                log("[MISSION] Completed single iteration (--once). Exiting.", "INFO")
                break
            time.sleep(delay)
        except KeyboardInterrupt:
            log("[MISSION] Stopping loop due to user interrupt.", "INFO")
            break
        except Exception as e:
            log(f"[MISSION] Unhandled error during mission: {e}", "FAIL")
            time.sleep(delay)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Demon-Mode mission loop.")
    parser.add_argument("--delay", type=int, default=2, help="Seconds to sleep between iterations (default: 2)")
    parser.add_argument("--once", action="store_true", help="Run a single iteration then exit")
    args = parser.parse_args()

    main(delay=args.delay, once=args.once)


