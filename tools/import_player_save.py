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
    PlayerSaveParser,
    reconcile_requirements,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionType,
    StablePlayerSaveAcquirer,
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
        "--freshness-verified",
        action="store_true",
        help=(
            "assert that the game completed a known serialization boundary "
            "before this pull; capture time alone is not sufficient"
        ),
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
        parser_api = PlayerSaveParser()
        if args.file is not None:
            snapshot = parser_api.parse_file(args.file)
        else:
            acquirer = StablePlayerSaveAcquirer(
                fixed_target=args.adb_target,
                parser=parser_api,
                source_name=Path(args.device_path).name,
                pull_options={"device_path": args.device_path},
            )
            acquisition = acquirer.acquire(
                PlayerSaveAcquisitionType.PASSIVE_STABLE_READ
            )
            if not acquisition.complete or acquisition.snapshot is None:
                raise PlayerSaveError(acquisition.reason)
            snapshot = acquisition.snapshot
        report = {"snapshot": snapshot.as_dict()}
        if args.requirements is not None:
            report["reconciliation"] = reconcile_requirements(
                snapshot,
                _load_requirements(args.requirements),
                force_ui_audit=bool(args.force_ui_audit),
                freshness_verified=bool(args.freshness_verified),
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
