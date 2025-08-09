#!/usr/bin/env python3
# tools/run_blind_gem_tapper.py
"""
CLI tool to manually start the blind floating gem tapper for testing.

Example:
  python tools/run_blind_gem_tapper.py --duration 15 --interval 0.5
"""

import argparse
from utils.logger import log
from handlers.ad_gem_handler import start_blind_gem_tapper, stop_blind_gem_tapper


def main():
    parser = argparse.ArgumentParser(description="Manually start the blind floating gem tapper for testing.")
    parser.add_argument("--duration", type=float, default=20.0, help="Seconds to run (default: 20)")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between taps (default: 1.0)")
    args = parser.parse_args()

    log(f"[ACTION] Starting blind gem tapper for {args.duration}s @ {args.interval}s (blocking)", "ACTION")
    try:
        start_blind_gem_tapper(duration=args.duration, interval=args.interval, blocking=True)
    except KeyboardInterrupt:
        log("KeyboardInterrupt — stopping tapper.", "INFO")
    finally:
        stop_blind_gem_tapper()
        log("Tester exited cleanly.", "INFO")


if __name__ == "__main__":
    main()
