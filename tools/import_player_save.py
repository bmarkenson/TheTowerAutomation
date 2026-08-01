#!/usr/bin/env python3
"""Inspect a local or ADB playerInfo.dat and plan UI fallback checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.player_save import (
    PLAYER_SAVE_DEVICE_PATH,
    PlayerSaveError,
    decode_player_save_bytes,
    pull_player_save_bytes,
    read_player_save_file,
    reconcile_requirements,
)


def _load_requirements(path: Path) -> Mapping[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"requirements file must contain a mapping: {path}")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--file",
        type=Path,
        help="local playerInfo.dat path",
    )
    source.add_argument(
        "--adb-target",
        help="ADB serial/target, for example localhost:5555",
    )
    parser.add_argument(
        "--device-path",
        default=PLAYER_SAVE_DEVICE_PATH,
        help=f"device save path (default: {PLAYER_SAVE_DEVICE_PATH})",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        help="optional YAML run profile or resolved settings to reconcile",
    )
    parser.add_argument(
        "--force-ui-audit",
        action="store_true",
        help="require the existing UI check even for a validated save match",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional destination for the privacy-safe JSON report",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit compact JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.file is not None:
            snapshot = read_player_save_file(args.file)
        else:
            payload = pull_player_save_bytes(
                device_id=args.adb_target,
                device_path=args.device_path,
            )
            snapshot = decode_player_save_bytes(
                payload,
                source_name=Path(args.device_path).name,
            )
        report = {"snapshot": snapshot.as_dict()}
        if args.requirements is not None:
            report["reconciliation"] = reconcile_requirements(
                snapshot,
                _load_requirements(args.requirements),
                force_ui_audit=bool(args.force_ui_audit),
            )
    except (OSError, ValueError, PlayerSaveError, yaml.YAMLError) as exc:
        print(f"player-save import failed: {exc}", file=sys.stderr)
        return 1

    indent = None if args.compact else 2
    rendered = json.dumps(report, indent=indent, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
