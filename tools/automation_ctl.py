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
  - take-manual-control [minimal|full]
                          → Pause and choose manual-Surrender collection
  - return-control        → request paused observation reconciliation
  - capture-setup         → request a fresh save-backed setup preview
  - capture-setup status|cancel
  - capture-setup save-modules <id> [display name]
  - capture-setup review-strategy <id> <tier> [display name]
  - capture-setup save-strategy <id> <tier> [display name]
      --review-fingerprint <sha256>
  - capture-setup draft <id> → reopen a durable captured Strategy source
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
    workflow = (
        model.get("battle_workflow")
        if action in {"start_battle", "attach_battle"}
        else model.get("manual_control")
        if action in {"take_manual_control", "return_control"}
        else {}
    ) or {}
    disposition = str(request.get("disposition") or "requested")
    status = str(workflow.get("status") or "pending")
    collection = (
        f"; manual Surrender collection={workflow.get('surrender_collection')}"
        if action == "take_manual_control"
        and workflow.get("surrender_collection")
        else ""
    )
    print(
        f"[OK] {action}: {disposition}; runtime status={status}"
        f"{collection} @ {path}"
    )


def _capture_base(value: str | None) -> dict[str, object] | None:
    if value is None:
        return None
    identifier, separator, revision_text = value.strip().partition("@")
    if not separator or not identifier or not revision_text.isdigit():
        raise SystemExit("--base must use <base-id>@<revision>")
    return {"id": identifier, "revision": int(revision_text)}


def _print_capture_preview(capture: dict[str, object]) -> None:
    preview = capture.get("preview") or {}
    acquisition_source = str(capture.get("acquisition_source") or "unknown")
    print(f"Evidence source: {acquisition_source}")
    print("Captured values:")
    print(json.dumps(preview.get("settings") or {}, indent=2, sort_keys=True))
    print("Unresolved values:")
    print(json.dumps(preview.get("unresolved") or [], indent=2, sort_keys=True))


def _capture_setup(
    path: str,
    command: list[str],
    *,
    base_ref: str | None = None,
    review_fingerprint: str | None = None,
) -> None:
    """Use only the Linux runtime-issued capture preview and save authority."""

    service = _better_control_service(path)
    try:
        current = service.setup_capture()
        capture = current.get("capture") or {}
        if not command:
            response = service.apply_setup_capture({"operation": "request"})
        elif command == ["status"]:
            print(json.dumps(current, indent=2))
            return
        elif command == ["cancel"]:
            request_id = str(capture.get("request_id") or "")
            if not request_id:
                raise SystemExit("No current setup capture to cancel")
            response = service.apply_setup_capture(
                {"operation": "cancel", "request_id": request_id}
            )
        elif command[0:1] == ["draft"] and len(command) == 2:
            print(
                json.dumps(
                    service.captured_setup_draft(command[1]),
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        elif command[0] == "save-modules" and len(command) >= 2:
            request_id = str(capture.get("request_id") or "")
            fingerprint = str(capture.get("preview_fingerprint") or "")
            if capture.get("status") != "ready" or not fingerprint:
                raise SystemExit("Setup capture is not ready for review and saving")
            identifier = command[1]
            display_name = " ".join(command[2:]).strip() or None
            _print_capture_preview(capture)
            response = service.apply_setup_capture(
                {
                    "operation": "save",
                    "request_id": request_id,
                    "expected_preview_fingerprint": fingerprint,
                    "kind": "module_preset",
                    "id": identifier,
                    "display_name": display_name,
                }
            )
        elif command[0] in {"review-strategy", "save-strategy"} and len(command) >= 3:
            request_id = str(capture.get("request_id") or "")
            fingerprint = str(capture.get("preview_fingerprint") or "")
            if capture.get("status") != "ready" or not fingerprint:
                raise SystemExit("Setup capture is not ready for review and saving")
            try:
                tier = int(command[2])
            except ValueError as exc:
                raise SystemExit("Captured Strategy tier must be an integer") from exc
            identifier = command[1]
            display_name = " ".join(command[3:]).strip() or None
            base = _capture_base(base_ref)
            _print_capture_preview(capture)
            review_request = {
                "operation": "review",
                "request_id": request_id,
                "expected_preview_fingerprint": fingerprint,
                "kind": "strategy_draft",
                "id": identifier,
                "display_name": display_name,
                "tier": tier,
            }
            if base is not None:
                review_request["base"] = base
            if command[0] == "review-strategy":
                reviewed = service.apply_setup_capture(review_request)
                review = reviewed["review"]
                print("Captured-versus-Base review:")
                print(json.dumps(review, indent=2, sort_keys=True))
                response = reviewed
            else:
                reviewed_fingerprint = str(
                    review_fingerprint or ""
                ).strip().lower()
                if (
                    len(reviewed_fingerprint) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in reviewed_fingerprint
                    )
                ):
                    raise SystemExit(
                        "save-strategy requires --review-fingerprint from a "
                        "prior review-strategy command"
                    )
                save_request = {
                    **review_request,
                    "operation": "save",
                    "expected_review_fingerprint": reviewed_fingerprint,
                }
                response = service.apply_setup_capture(save_request)
        else:
            raise SystemExit(
                "Use capture-setup, capture-setup status, capture-setup cancel, "
                "capture-setup save-modules <id> [display name], or "
                "capture-setup review-strategy <id> <tier> [display name], or "
                "capture-setup save-strategy <id> <tier> [display name] "
                "--review-fingerprint <sha256>, or "
                "capture-setup draft <id>"
            )
    except ControlSurfaceRequestError as exc:
        code = f" [{exc.code}]" if exc.code else ""
        raise SystemExit(f"capture-setup unavailable{code}: {exc}") from exc
    request = response.get("request") or {}
    capture = response.get("capture") or {}
    print(
        "[OK] capture-setup: "
        f"{request.get('disposition') or 'requested'}; "
        f"runtime status={capture.get('status') or 'pending'} @ {path}"
    )


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
            "take-manual-control [minimal|full] | return-control | "
            "capture-setup [status|cancel|save-modules|save-strategy ...] | "
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
    p.add_argument(
        "--base",
        default=None,
        metavar="ID@REVISION",
        help="Optional comparison Base for captured Strategy review",
    )
    p.add_argument(
        "--review-fingerprint",
        default=None,
        metavar="SHA256",
        help=(
            "Fingerprint printed by a prior capture-setup review-strategy; "
            "required by capture-setup save-strategy"
        ),
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
    if args.base is not None and cmd[0] != "capture-setup":
        p.error("--base is only valid with capture-setup")
    if args.review_fingerprint is not None and cmd[0] != "capture-setup":
        p.error("--review-fingerprint is only valid with capture-setup")
    if args.review_fingerprint is not None and cmd[:2] != [
        "capture-setup",
        "save-strategy",
    ]:
        p.error(
            "--review-fingerprint is only valid with capture-setup save-strategy"
        )
    if cmd[0] == "enable" and len(cmd) == 1:
        _apply_better_control(ctrl, "enable")
        return 0
    if cmd[0] == "start-battle" and len(cmd) == 1:
        _apply_better_control(ctrl, "start_battle")
        return 0
    if cmd[0] == "attach-battle" and len(cmd) == 1:
        _apply_better_control(ctrl, "attach_battle")
        return 0
    if cmd[0] == "take-manual-control" and len(cmd) in {1, 2}:
        collection = cmd[1].strip().lower() if len(cmd) == 2 else "minimal"
        if collection not in {"minimal", "full"}:
            p.error("take-manual-control collection must be minimal or full")
        _apply_better_control(
            ctrl,
            "take_manual_control",
            manual_surrender_collection=collection,
        )
        return 0
    if cmd[0] == "return-control" and len(cmd) == 1:
        _apply_better_control(ctrl, "return_control")
        return 0
    if cmd[0] == "capture-setup":
        _capture_setup(
            ctrl,
            cmd[1:],
            base_ref=args.base,
            review_fingerprint=args.review_fingerprint,
        )
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
