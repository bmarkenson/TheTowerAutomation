#!/usr/bin/env python3
# automation/run_demon_nuke.py

import sys
import os
import time

# Add project root to sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

from handlers.mission_nuke import run_nuke_strategy
from utils.logger import log


def main():
    """Entrypoint: persistent Demon-Nuke mission loop until interrupted."""
    log("[MISSION] Starting persistent Demon-Nuke loop. Ctrl+C to stop.", "INFO")
    while True:
        try:
            run_nuke_strategy()
            time.sleep(2)
        except KeyboardInterrupt:
            log("[MISSION] Stopping loop due to user interrupt.", "INFO")
            break
        except Exception as e:
            log(f"[MISSION] Unhandled error during mission: {e}", "FAIL")
            time.sleep(2)


if __name__ == "__main__":
    main()
