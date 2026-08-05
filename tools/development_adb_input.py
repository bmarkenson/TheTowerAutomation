#!/usr/bin/env python3
"""Send one lease-aware exact-target development tap or swipe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.development_adb_input import (
    ActionLogAudit,
    AdbBoundary,
    AuditBoundary,
    DevelopmentInputRequest,
    EXIT_SUCCESS,
    SubprocessAdbBoundary,
    execute_development_input,
    fetch_control_status,
)


DEVELOPMENT_ENVIRONMENT_CONFIG = (
    PROJECT_ROOT / "requirements" / "development-environment.json"
)


def production_action_log_path() -> Path:
    """Derive the fixed production log from the tracked environment contract."""

    try:
        payload = json.loads(DEVELOPMENT_ENVIRONMENT_CONFIG.read_text(encoding="utf-8"))
        production_environment = Path(str(payload["production_environment"]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "unable to derive the production action log from "
            f"{DEVELOPMENT_ENVIRONMENT_CONFIG}"
        ) from exc
    if not production_environment.is_absolute() or production_environment.name != ".venv":
        raise RuntimeError("configured production environment path is malformed")
    return production_environment.parent / "logs" / "actions.log"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Send exactly one canonical-coordinate ADB tap or swipe after the "
            "production control surface proves the supplied interactive lease."
        ),
        epilog=(
            "Exit status: 0 completed; 2 invalid CLI usage; 3 lease/status "
            "rejection; 4 ADB read/input failure; 5 required audit-log failure. "
            "Uncertain input is never retried."
        ),
    )
    parser.add_argument(
        "--lease-id",
        required=True,
        help="Ordinary 32-hex interactive development lease ID",
    )
    parser.add_argument(
        "--action-log",
        default=str(production_action_log_path()),
        metavar="ABSOLUTE_PATH",
        help=(
            "test-only audit-path override; defaults to the production "
            "logs/actions.log derived from the tracked environment contract"
        ),
    )
    actions = parser.add_subparsers(dest="action", required=True)

    tap = actions.add_parser("tap", help="Tap one canonical coordinate")
    tap.add_argument("x", type=float, help="Canonical x in [0, 1080)")
    tap.add_argument("y", type=float, help="Canonical y in [0, 1920)")

    swipe = actions.add_parser("swipe", help="Swipe once between canonical points")
    swipe.add_argument("x1", type=float, help="Canonical start x in [0, 1080)")
    swipe.add_argument("y1", type=float, help="Canonical start y in [0, 1920)")
    swipe.add_argument("x2", type=float, help="Canonical end x in [0, 1080)")
    swipe.add_argument("y2", type=float, help="Canonical end y in [0, 1920)")
    swipe.add_argument(
        "duration_ms",
        type=int,
        help="Positive swipe duration, at most 5000 milliseconds",
    )
    return parser


def main(
    argv: Optional[list[str]] = None,
    *,
    status_reader: Optional[Callable[[], Mapping[str, Any]]] = None,
    adb: Optional[AdbBoundary] = None,
    audit: Optional[AuditBoundary] = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    selected_log_path = Path(args.action_log)
    if not selected_log_path.is_absolute():
        parser.error("--action-log must be an absolute path")

    request = (
        DevelopmentInputRequest.tap(args.x, args.y)
        if args.action == "tap"
        else DevelopmentInputRequest.swipe(
            args.x1,
            args.y1,
            args.x2,
            args.y2,
            args.duration_ms,
        )
    )
    result = execute_development_input(
        request,
        lease_id=args.lease_id,
        status_reader=status_reader or fetch_control_status,
        adb=adb or SubprocessAdbBoundary(),
        audit=audit or ActionLogAudit(selected_log_path),
    )
    stream = sys.stdout if result.exit_code == EXIT_SUCCESS else sys.stderr
    print(result.message, file=stream)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main", "production_action_log_path"]
