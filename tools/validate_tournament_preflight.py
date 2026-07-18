#!/usr/bin/env python3
"""Capture or validate the persistent setup used by an active Tournament."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

import cv2


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.gc_preflight_navigation import (
    GcPreflightNavigationStatus,
    run_read_only_gc_preflight,
)
from core.ss_capture import capture_adb_screenshot, is_complete_screenshot
from core.state_detector import detect_state_and_overlays
from core.tournament_preflight import (
    load_tournament_requirements,
    validate_tournament_session_preflight_screens,
)


DEFAULT_CONTROL_PATH = ROOT / "logs" / "automation_ctl.json"
CAPTURE_KEYS = (
    "cards_screen",
    "workshop_screen",
    "bots_screen",
    "guardians_screen",
    "modules_screen",
    "perks_screen",
)


@dataclass(frozen=True)
class _CaptureEvidence:
    valid: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "capture_only": True}


def _capture_callback(output_dir: Path):
    def capture(**screens):
        output_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for key in CAPTURE_KEYS:
            frame = screens.get(key)
            if frame is None:
                continue
            destination = output_dir / f"{key.removesuffix('_screen')}.png"
            if not cv2.imwrite(str(destination), frame):
                raise OSError(f"could not write {destination}")
            written.append(destination.name)
        observations = screens.get("ultimate_observations") or {}
        (output_dir / "ultimate_weapons.json").write_text(
            json.dumps(observations, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Captured Tournament preflight evidence in {output_dir}")
        print("Screens: " + ", ".join(written))
        return _CaptureEvidence()

    return capture


def require_paused_control(path: Path) -> None:
    """Refuse live navigation unless persistent operator intent is PAUSED."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read automation control file {path}: {exc}") from exc
    state = str(payload.get("state") or "").strip().upper()
    if state != "PAUSED":
        raise ValueError(
            f"Tournament validation requires PAUSED control; found {state or 'missing'}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="capture every guarded preflight screen without evaluating it",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/thetower_tournament_preflight"),
        help="capture-only evidence directory",
    )
    parser.add_argument(
        "--control-file",
        type=Path,
        default=DEFAULT_CONTROL_PATH,
        help="persistent automation control file (must declare PAUSED)",
    )
    args = parser.parse_args()

    try:
        require_paused_control(args.control_file)
    except ValueError as exc:
        print(f"Tournament validation refused: {exc}")
        return 2

    initial = capture_adb_screenshot()
    if not is_complete_screenshot(initial):
        print("Tournament validation refused: initial screenshot was incomplete")
        return 2
    detection = detect_state_and_overlays(initial)
    secondaries = set(detection.get("secondary_states") or ())
    if detection.get("state") != "RUNNING" or "TOURNAMENT" not in secondaries:
        print(
            "Tournament validation requires an active Tournament; "
            f"detected state={detection.get('state')} secondary={sorted(secondaries)}"
        )
        return 2

    requirements = load_tournament_requirements()
    result = run_read_only_gc_preflight(
        requirements,
        validate_fn=(
            _capture_callback(args.output_dir)
            if args.capture_only
            else validate_tournament_session_preflight_screens
        ),
    )
    operation = "capture" if args.capture_only else "validation"
    print(f"Tournament {operation} status: {result.status.value} ({result.reason})")
    if not args.capture_only and result.evidence is not None:
        print(json.dumps(result.evidence.as_dict(), indent=2))
    return 0 if result.status is GcPreflightNavigationStatus.COMPLETE else 1


if __name__ == "__main__":
    raise SystemExit(main())
