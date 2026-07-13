#!/usr/bin/env python3
"""CLI entrypoint for the App-based automation runtime."""

from __future__ import annotations

import os
import sys
from typing import Sequence

from utils.logger import log
from utils.wave_detector import set_wave_hint
from core.app_setup import parse_args, config_from_args
from core.app import App


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, build config, and run the app."""
    args = parse_args(argv)
    config = config_from_args(args)

    os.environ["ADB_DEVICE"] = f"localhost:{config.adb_port}"
    log(f"ADB target = {os.environ['ADB_DEVICE']}", "DEBUG")

    log(f"AUTO_START_ENABLED = {config.auto_start_enabled}", "DEBUG")

    if config.reset_wave_hint:
        set_wave_hint(None)
        log("[WAVE] Reset wave hint at startup", "DEBUG")

    try:
        app = App(config)
        app.run()
        return 0
    except KeyboardInterrupt:
        log("Interrupted by user (Ctrl+C)", "INFO")
        return 130
    except Exception as exc:  # pragma: no cover - fatal logging only
        log(f"Fatal error: {exc}", "ERROR")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
