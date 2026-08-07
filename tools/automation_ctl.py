#!/usr/bin/env python3
"""
tools/automation_ctl.py

Small CLI to control automation state/mode via a JSON control file
consumed by main.py (default: logs/automation_ctl.json).

Commands:
  - pause                 → state=PAUSED until an explicit Enable
  - pause --minutes N     → state=PAUSED until its persisted deadline
  - enable                → explicitly permit guarded automation actions
  - start-battle          → request only a verified new-run Home workflow
  - attach-battle         → request only a verified active/resumable workflow
  - take-manual-control   → request an acknowledged indefinite Pause
  - return-control        → request paused observation reconciliation
  - when-battle-ends <continue|wait|home> → set future terminal policy
  - game-speed <target>   → enforce x0.0..x6.0, or x6.3/max available
  - gate                  → prompt for a pending startup-gate decision
  - gate <choice>         → resolve it non-interactively by choice id
  - force-continue        → legacy alias for gate bypass_once
  - configure-run         → interactively stage one-run check skips
  - configure-run skip <check>
  - configure-run default <check>
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.control_directives import (
    ControlDirectiveError,
    ControlDirectiveStore,
    normalize_game_speed_target,
)
from core.gate_decisions import (
    prompt_for_gate_decision,
    startup_gate_context_for_strategy,
)
from core.control_surface import ControlSurfaceRequestError, ControlSurfaceService


DEFAULT_CTRL_PATH = "logs/automation_ctl.json"


def _set_game_speed_target(path: str, target: str) -> None:
    raw_target = "6.3" if target.strip().lower() == "max" else target
    if raw_target.lower().startswith("x"):
        raw_target = raw_target[1:]
    try:
        normalized = normalize_game_speed_target(raw_target)
        ControlDirectiveStore(path).set_game_speed_target(
            normalized,
            source="cli",
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid game-speed target: {exc}") from exc
    except ControlDirectiveError as exc:
        raise SystemExit(f"Unable to update automation control: {exc}") from exc
    print(f"[OK] Game speed target set to x{normalized:.1f} @ {path}")


def _better_control_service(path: str) -> ControlSurfaceService:
    """Use the same fresh-owner validation as browser and native clients."""

    control_path = Path(path)
    parent = control_path.parent
    return ControlSurfaceService(
        repository_root=PROJECT_ROOT,
        control_file=control_path,
        action_log=parent / "actions.log",
        strategy_action_gate_file=parent / "strategy_action_gate.json",
    )


def _apply_better_control(path: str, action: str, **values: object) -> None:
    try:
        response = _better_control_service(path).apply_control(
            {"action": action, **values}
        )
    except ControlSurfaceRequestError as exc:
        code = f" [{exc.code}]" if exc.code else ""
        raise SystemExit(f"{action} unavailable{code}: {exc}") from exc
    request = response.get("request") or {}
    model = response.get("control_model") or {}
    workflow = model.get("battle_workflow") or model.get("manual_control") or {}
    disposition = str(request.get("disposition") or "requested")
    status = str(workflow.get("status") or "pending")
    print(f"[OK] {action}: {disposition}; runtime status={status} @ {path}")


def _request_force_continue(path: str) -> None:
    """Resolve an existing blocked gate with the scoped bypass choice."""

    _resolve_gate(path, "bypass_once")


def _resolve_gate(path: str, decision_id: str | None = None) -> None:
    """Prompt for or explicitly resolve the current pending gate."""

    try:
        store = ControlDirectiveStore(path)
        directive = store.status().get("gate_decision")
    except ControlDirectiveError as exc:
        raise SystemExit(f"Unable to read automation control: {exc}") from exc
    if not directive or directive.get("status") != "pending":
        raise SystemExit("No pending startup-gate decision")
    selected = decision_id or prompt_for_gate_decision(directive)
    if not selected:
        print("[PENDING] Startup-gate decision left unresolved")
        return
    try:
        resolved = store.resolve_gate_decision(
            str(directive["request_id"]),
            selected,
            source="cli",
        )
    except (ControlDirectiveError, ValueError) as exc:
        raise SystemExit(f"Unable to resolve startup gate: {exc}") from exc
    if resolved is None:
        raise SystemExit("Startup-gate decision changed before it was resolved")
    print(
        f"[OK] Resolved {resolved['check_id']} with "
        f"{resolved['decision_id']} @ {path}"
    )


def _configure_run(path: str, command: list[str]) -> None:
    """Interactively or explicitly configure one-run requirement skips."""

    store = ControlDirectiveStore(path)
    try:
        status = store.status()
        context = startup_gate_context_for_strategy(
            str(status.get("strategy") or "farm")
        )
    except (ControlDirectiveError, OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"Unable to load run configuration: {exc}") from exc
    checks = context.get("checks") or []
    checks_by_id = {check["id"]: check for check in checks}
    strategy = str(context.get("strategy") or "none")

    def stage(check_id: str) -> None:
        if check_id not in checks_by_id:
            raise SystemExit(
                f"{check_id!r} is not a configurable check for {strategy}"
            )
        directive = store.request_startup_gate_waiver(
            check_id,
            strategy=strategy,
            source="cli",
        )
        print(f"[OK] {directive['label']} will be skipped for one {strategy} run")

    def restore_default(check_id: str) -> None:
        removed = store.cancel_startup_gate_waiver(check_id, source="cli")
        if removed is None:
            raise SystemExit(f"No staged skip exists for {check_id!r}")
        print(f"[OK] {removed['label']} restored to the strategy default")

    if command:
        if len(command) != 2 or command[0] not in {"skip", "default"}:
            raise SystemExit(
                "Use configure-run, configure-run skip <check>, or "
                "configure-run default <check>"
            )
        if command[0] == "skip":
            stage(command[1].strip().lower())
        else:
            restore_default(command[1].strip().lower())
        return

    if not checks:
        print(f"[OK] {strategy} has no configurable startup checks")
        return
    while True:
        pending = store.status().get("startup_gate_waivers") or {}
        print(f"\nConfigure next {strategy} run (Enter accepts these settings)")
        for index, check in enumerate(checks, start=1):
            marker = "SKIP ONCE" if check["id"] in pending else "default"
            print(f"  {index}) [{marker}] {check['label']} ({check['id']})")
        try:
            raw = input(f"Toggle 1-{len(checks)}, or press Enter when done: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return
        if not raw:
            return
        try:
            selected = int(raw)
        except ValueError:
            print("Enter the number of one available check.")
            continue
        if not 1 <= selected <= len(checks):
            print("That check is outside the available range.")
            continue
        check_id = checks[selected - 1]["id"]
        if check_id in pending:
            restore_default(check_id)
        else:
            stage(check_id)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Better Control Model authority and workflow controller"
    )
    p.add_argument(
        "command",
        nargs="+",
        help=(
            "pause | enable | start-battle | attach-battle | "
            "take-manual-control | return-control | "
            "when-battle-ends <continue|wait|home> | gate [choice] | "
            "game-speed <0.0..6.0|6.3|max> | "
            "force-continue | configure-run [skip|default <check>] | "
            "status"
        ),
    )
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
        if args.minutes is not None:
            if not math.isfinite(args.minutes) or args.minutes <= 0:
                p.error("--minutes must be a positive number")
            _apply_better_control(ctrl, "pause", minutes=args.minutes)
        else:
            _apply_better_control(ctrl, "pause")
        return 0
    if args.minutes is not None:
        p.error("--minutes is only valid with the pause command")
    if cmd[0] == "enable" and len(cmd) == 1:
        _apply_better_control(ctrl, "enable")
        return 0
    if cmd[0] == "start-battle" and len(cmd) == 1:
        _apply_better_control(ctrl, "start_battle")
        return 0
    if cmd[0] == "attach-battle" and len(cmd) == 1:
        _apply_better_control(ctrl, "attach_battle")
        return 0
    if cmd[0] == "take-manual-control" and len(cmd) == 1:
        _apply_better_control(ctrl, "take_manual_control")
        return 0
    if cmd[0] == "return-control" and len(cmd) == 1:
        _apply_better_control(ctrl, "return_control")
        return 0
    if cmd[0] == "when-battle-ends" and len(cmd) == 2:
        aliases = {
            "continue": "NEXT_BATTLE",
            "continue-automatically": "NEXT_BATTLE",
            "wait": "WAIT",
            "home": "HOME",
        }
        policy = aliases.get(cmd[1].strip().lower(), cmd[1])
        _apply_better_control(ctrl, "terminal_policy", policy=policy)
        return 0
    if cmd[0] == "game-speed" and len(cmd) == 2:
        _set_game_speed_target(ctrl, cmd[1])
        return 0
    if cmd[0] == "force-continue" and len(cmd) == 1:
        _request_force_continue(ctrl)
        return 0
    if cmd[0] == "gate" and len(cmd) in {1, 2}:
        _resolve_gate(ctrl, cmd[1] if len(cmd) == 2 else None)
        return 0
    if cmd[0] == "configure-run":
        _configure_run(ctrl, cmd[1:])
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
        try:
            status["startup_gate_context"] = startup_gate_context_for_strategy(
                str(status.get("strategy") or "farm")
            )
        except (OSError, TypeError, ValueError):
            status["startup_gate_context"] = None
        status["updated_at"] = status.get("updated_at") or "<never>"
        print(json.dumps(status, indent=2))
        return 0

    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
