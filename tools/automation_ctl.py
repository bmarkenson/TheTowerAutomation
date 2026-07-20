#!/usr/bin/env python3
"""
tools/automation_ctl.py

Small CLI to control automation state/mode via a JSON control file
consumed by main.py (default: logs/automation_ctl.json).

Commands:
  - pause                 → state=PAUSED until an explicit resume
  - pause --minutes N     → state=PAUSED until its persisted deadline
  - resume                → state=RUNNING
  - stop                  → state=STOPPED
  - mode <retry|wait|home>→ set ExecMode
  - set state <S>         → explicitly set state (RUNNING|PAUSED|STOPPED)
  - set mode  <M>         → explicitly set mode  (RETRY|WAIT|HOME)
  - status                → print current file contents (or defaults)

Writes atomically (tmp + os.replace) and preserves unspecified fields.
The control file is authoritative. A pause is indefinite unless it has an
explicit ``resume_at`` deadline.
"""

from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.control_directives import (
    ControlDirectiveError,
    ControlDirectiveStore,
    VALID_MODES,
    VALID_STATES,
)


DEFAULT_CTRL_PATH = "logs/automation_ctl.json"


def _set_state(path: str, state: str, *, resume_at: float | None = None) -> None:
    s = state.upper()
    if s not in VALID_STATES:
        raise SystemExit(f"Invalid state: {state}. Use one of {sorted(VALID_STATES)}")
    try:
        ControlDirectiveStore(path).set_state(s, resume_at=resume_at, source="cli")
    except ControlDirectiveError as exc:
        raise SystemExit(f"Unable to update automation control: {exc}") from exc
    print(f"[OK] State set to {s} @ {path}")


def _set_mode(path: str, mode: str) -> None:
    m = mode.upper()
    if m not in VALID_MODES:
        raise SystemExit(f"Invalid mode: {mode}. Use one of {sorted(VALID_MODES)}")
    try:
        ControlDirectiveStore(path).set_mode(m, source="cli")
    except ControlDirectiveError as exc:
        raise SystemExit(f"Unable to update automation control: {exc}") from exc
    print(f"[OK] Mode set to {m} @ {path}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Automation pause/mode controller")
    p.add_argument("command", nargs="+", help="pause | resume | stop | mode <m> | set state <s> | set mode <m> | status")
    p.add_argument(
        "--minutes",
        type=float,
        default=None,
        metavar="N",
        help="Optional positive duration for the pause command",
    )
    p.add_argument("--control-file", dest="ctrl", default=DEFAULT_CTRL_PATH, help=f"Control file path (default: {DEFAULT_CTRL_PATH})")
    args = p.parse_args(argv)

    cmd = args.command
    ctrl = args.ctrl

    if cmd[0] == "pause" and len(cmd) == 1:
        resume_at = None
        if args.minutes is not None:
            if not math.isfinite(args.minutes) or args.minutes <= 0:
                p.error("--minutes must be a positive number")
            resume_at = time.time() + (args.minutes * 60)
        _set_state(ctrl, "PAUSED", resume_at=resume_at)
        return 0
    if args.minutes is not None:
        p.error("--minutes is only valid with the pause command")
    if cmd[0] == "resume" and len(cmd) == 1:
        _set_state(ctrl, "RUNNING")
        return 0
    if cmd[0] == "stop" and len(cmd) == 1:
        _set_state(ctrl, "STOPPED")
        return 0
    if cmd[0] == "mode" and len(cmd) == 2:
        _set_mode(ctrl, cmd[1])
        return 0
    if cmd[0] == "set" and len(cmd) == 3 and cmd[1] == "state":
        _set_state(ctrl, cmd[2])
        return 0
    if cmd[0] == "set" and len(cmd) == 3 and cmd[1] == "mode":
        _set_mode(ctrl, cmd[2])
        return 0
    if cmd[0] == "status" and len(cmd) == 1:
        try:
            status = ControlDirectiveStore(ctrl).status()
        except ControlDirectiveError as exc:
            p.error(str(exc))
        status.pop("exists", None)
        status.pop("updated_by", None)
        status.pop("state_updated_at", None)
        status.pop("mode_updated_at", None)
        status["updated_at"] = status.get("updated_at") or "<never>"
        print(json.dumps(status, indent=2))
        return 0

    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
