#!/usr/bin/env python3
"""Attach versioned Battle Conditions to Tournament records by explicit UTC date."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.tournament_results import backfill_tournament_conditions


def _event(value: str) -> tuple[str, int]:
    raw_date, separator, raw_number = str(value).partition("=")
    if not separator:
        raise argparse.ArgumentTypeError("event must use YYYY-MM-DD=NUMBER")
    try:
        event_date = date.fromisoformat(raw_date).isoformat()
        tournament_number = int(raw_number)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "event must use a valid YYYY-MM-DD and positive integer"
        ) from exc
    if tournament_number <= 0:
        raise argparse.ArgumentTypeError("Tournament number must be positive")
    return event_date, tournament_number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--records-dir",
        type=Path,
        default=Path("logs/tournaments"),
        help="Tournament JSON/Markdown directory",
    )
    parser.add_argument(
        "--event",
        action="append",
        type=_event,
        required=True,
        metavar="YYYY-MM-DD=NUMBER",
        help="repeatable, explicit UTC Tournament date mapping",
    )
    parser.add_argument("--data-version", type=int, default=9)
    parser.add_argument("--game-version", type=int, default=1073)
    parser.add_argument("--league-id", type=int, default=5)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write atomically; without this flag the command is a dry run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events = dict(args.event)
    if len(events) != len(args.event):
        print("duplicate UTC event date", file=sys.stderr)
        return 2
    report = backfill_tournament_conditions(
        events,
        records_dir=args.records_dir,
        data_version=args.data_version,
        game_version=args.game_version,
        league_id=args.league_id,
        write=bool(args.apply),
    )
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 1 if report["summary"]["skipped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
