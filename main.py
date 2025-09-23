#!/usr/bin/env python3
"""CLI entrypoint for the App-based automation runtime."""

from __future__ import annotations

from utils.logger import log
from utils.wave_detector import set_wave_hint
from core.app_setup import parse_args, config_from_args
from core.app import App


def main(argv=None) -> None:
    args = parse_args(argv)
    config = config_from_args(args)

    log(f"AUTO_START_ENABLED = {config.auto_start_enabled}", "DEBUG")

    if config.reset_wave_hint:
        set_wave_hint(None)
        log("[WAVE] Reset wave hint at startup", "DEBUG")

    app = App(config)
    app.run()


if __name__ == "__main__":
    main()
