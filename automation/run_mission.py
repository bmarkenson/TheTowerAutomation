#!/usr/bin/env python3
"""Mission-focused CLI entrypoint that boots the App orchestrator."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence


# Add project root to sys.path for direct script execution
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.app import App
from core.app_setup import build_arg_parser, config_from_args
from utils.logger import log


def _build_parser() -> argparse.ArgumentParser:
    parser = build_arg_parser()
    parser.prog = "python automation/run_mission.py"
    parser.description = (
        "Launch the automation App with a specified mission. "
        "This is a convenience wrapper around main.py for mission-centric runs."
    )
    parser.add_argument(
        "mission_name",
        nargs="?",
        help="Positional alias for --mission (e.g. 'demon_mode').",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    positional = getattr(args, "mission_name", None)
    if positional:
        args.mission = positional

    mission_value = (getattr(args, "mission", "") or "").strip().lower()
    has_yaml_config = bool(getattr(args, "mission_config", None))
    if mission_value in {"", "none"} and not has_yaml_config:
        parser.error("Mission required. Provide MISSION positional, --mission, or --mission-config.")

    config = config_from_args(args)
    log(
        f"[MISSION] Starting App runtime — mission='{config.mission_name}' "
        f"strategy='{config.strategy_name}' mission_config={config.mission_config_path or 'None'}",
        "INFO",
    )
    App(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
