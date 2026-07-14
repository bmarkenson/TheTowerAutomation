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
import os
import sys
import time
from datetime import datetime


DEFAULT_CTRL_PATH = "logs/automation_ctl.json"


VALID_STATES = {"RUNNING", "PAUSED", "STOPPED"}
VALID_MODES = {"RETRY", "WAIT", "HOME"}


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[WARN] Failed to read {path}: {e}", file=sys.stderr)
        return {}


def _atomic_write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _set_state(path: str, state: str, *, resume_at: float | None = None) -> None:
    s = state.upper()
    if s not in VALID_STATES:
        raise SystemExit(f"Invalid state: {state}. Use one of {sorted(VALID_STATES)}")
    data = _read_json(path)
    data["state"] = s
    data.pop("resume_at", None)
    if s == "PAUSED" and resume_at is not None:
        data["resume_at"] = resume_at
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_write_json(path, data)
    print(f"[OK] State set to {s} @ {path}")


def _set_mode(path: str, mode: str) -> None:
    m = mode.upper()
    if m not in VALID_MODES:
        raise SystemExit(f"Invalid mode: {mode}. Use one of {sorted(VALID_MODES)}")
    data = _read_json(path)
    data["mode"] = m
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_write_json(path, data)
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
        data = _read_json(ctrl)
        # Defaults if file missing/empty
        st = (data.get("state") or "RUNNING").upper()
        md = (data.get("mode") or "RETRY").upper()
        upd = data.get("updated_at") or "<never>"
        resume_at = data.get("resume_at")
        remaining_seconds = None
        if isinstance(resume_at, (int, float)) and math.isfinite(resume_at):
            remaining_seconds = max(0, round(resume_at - time.time()))
        print(json.dumps({
            "state": st,
            "mode": md,
            "resume_at": resume_at,
            "remaining_seconds": remaining_seconds,
            "updated_at": upd,
            "path": ctrl,
        }, indent=2))
        return 0

    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
