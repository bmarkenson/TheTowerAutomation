#!/usr/bin/env python3
# automation/run_mission.py
"""
Generic standalone mission runner.

This script provides a common entrypoint for running any handler-based mission
in a persistent loop. It replaces the duplicated logic from the older
run_demon_mode.py, run_nuke.py, etc. scripts.

Usage:
  python automation/run_mission.py demon_mode --delay 5
  python automation/run_mission.py nuke
  python automation/run_mission.py demon_nuke --once
"""

import sys
import os
import time
import argparse

# Add project root to sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

from handlers.mission_demon_mode import run_demon_mode
from handlers.mission_demon_nuke import run_demon_nuke_strategy
from handlers.mission_nuke import run_nuke_strategy
from utils.logger import log

MISSION_HANDLERS = {
    "demon_mode": lambda: run_demon_mode(),
    "demon_nuke": run_demon_nuke_strategy,
    "nuke": run_nuke_strategy,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a standalone mission loop.")
    parser.add_argument("mission", choices=MISSION_HANDLERS.keys(), help="The mission to run.")
    parser.add_argument("--delay", type=int, default=2, help="Seconds to sleep between iterations (default: 2)")
    parser.add_argument("--once", action="store_true", help="Run a single iteration then exit")
    args = parser.parse_args()

    handler = MISSION_HANDLERS[args.mission]
    log(f"[MISSION] Starting '{args.mission}' loop. Ctrl+C to stop.", "INFO")

    while True:
        try:
            handler()
            if args.once:
                log(f"[MISSION] Completed single iteration of '{args.mission}' (--once). Exiting.", "INFO")
                break
            time.sleep(args.delay)
        except KeyboardInterrupt:
            log("[MISSION] Stopping loop due to user interrupt.", "INFO")
            break
        except Exception as e:
            log(f"[MISSION] Unhandled error during '{args.mission}' mission: {e}", "FAIL")
            time.sleep(args.delay)