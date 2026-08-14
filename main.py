#!/usr/bin/env python3
"""CLI entrypoint for the App-based automation runtime."""

from __future__ import annotations

import os
from typing import Sequence

from utils.logger import log
from core.app_setup import parse_args, config_from_args
from core.app import App
from core.single_instance import InstanceAlreadyRunning
from core.adb_target_session import AdbTargetSession


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, build config, and run the app."""
    args = parse_args(argv)
    config = config_from_args(args)

    os.environ["ADB_DEVICE"] = f"localhost:{config.adb_port}"
    log(f"ADB target = {os.environ['ADB_DEVICE']}", "DEBUG")
    log(f"ADB connection owner = {config.adb_connection_owner}", "DEBUG")

    try:
        with AdbTargetSession(os.environ["ADB_DEVICE"]) as target_session:
            log(f"AUTO_START_ENABLED = {config.auto_start_enabled}", "DEBUG")

            app = App(config, adb_target_session=target_session)
            app.run()
            return 0
    except InstanceAlreadyRunning as exc:
        log(str(exc), "ERROR", console=True)
        return 2
    except KeyboardInterrupt:
        log("Interrupted by user (Ctrl+C)", "INFO")
        return 130
    except Exception as exc:  # pragma: no cover - fatal logging only
        log(f"Fatal error: {exc}", "ERROR")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
