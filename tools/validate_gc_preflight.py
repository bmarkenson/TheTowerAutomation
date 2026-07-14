#!/usr/bin/env python3
"""Validate captured Cards, Workshop, Bots, and Guardian screens for GC."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import cv2


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.gc_preflight import validate_gc_preflight_screens


def _load(path: Path):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"could not read screenshot: {path}")
    return image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards-image", required=True, type=Path)
    parser.add_argument("--workshop-image", required=True, type=Path)
    parser.add_argument("--bots-image", required=True, type=Path)
    parser.add_argument("--guardians-image", required=True, type=Path)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        evidence = validate_gc_preflight_screens(
            cards_screen=_load(args.cards_image),
            workshop_screen=_load(args.workshop_image),
            bots_screen=_load(args.bots_image),
            guardians_screen=_load(args.guardians_image),
        )
    except ValueError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(evidence.as_dict(), indent=2))
    return 0 if evidence.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
