#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from tools.strategy_builders.lib import build_strategy_yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate strategy YAML from compact source")
    parser.add_argument("source", type=Path, help="Strategy source YAML")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write expanded strategy YAML (default: source name with .strategy.yaml)",
    )
    return parser.parse_args()


def resolve_output_path(source: Path, explicit: Path | None) -> Path:
    if explicit:
        return explicit
    if source.name.endswith(".source.yaml"):
        return source.with_name(source.name.replace(".source.yaml", ".strategy.yaml"))
    return source.with_suffix(".strategy.yaml")


def main(argv: list[str] | None = None) -> int:
    args = parse_args()

    try:
        source_data = yaml.safe_load(args.source.read_text())
    except FileNotFoundError:
        print(f"Source file not found: {args.source}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Failed to load source: {exc}", file=sys.stderr)
        return 2

    try:
        expanded = build_strategy_yaml(source_data)
    except Exception as exc:
        print(f"Failed to build strategy: {exc}", file=sys.stderr)
        return 3

    output_path = resolve_output_path(args.source, args.output)
    output_path.write_text(yaml.safe_dump(expanded, sort_keys=False))
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
