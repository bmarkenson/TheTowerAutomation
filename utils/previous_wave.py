#!/usr/bin/env python3
# utils/previous_wave.py

import os
import glob
import json
from typing import Optional

from core.battle_stats import included_in_default_history

# ---------- File selection ----------


def _latest_battle_record(records_dir: str = "logs/battles") -> Optional[str]:
    candidates = sorted(
        glob.glob(os.path.join(records_dir, "Battle*.json")),
        key=os.path.basename,
        reverse=True,
    )
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and included_in_default_history(record):
            return path
    return None


# ---------- Public API ----------

def get_previous_run_wave(
    records_dir: str = "logs/battles",
) -> Optional[int]:
    """Read the latest structured battle record and return its final wave."""
    record_path = _latest_battle_record(records_dir)
    if record_path:
        try:
            with open(record_path, "r", encoding="utf-8") as handle:
                record = json.load(handle)
            for section in record.get("more_stats", {}).get("sections", []):
                if section.get("key") != "battle_report":
                    continue
                for row in section.get("rows", []):
                    if row.get("key") == "wave" and isinstance(row.get("value"), int):
                        return int(row["value"])
            value = (
                record.get("game_stats", {})
                .get("fields", {})
                .get("wave", {})
                .get("value")
            )
            if isinstance(value, int):
                return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return None


def main():
    """CLI: print the previous run's wave from structured battle records."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--records-dir",
        default="logs/battles",
        help="Directory containing Battle*.json records",
    )
    args = parser.parse_args()

    val = get_previous_run_wave(records_dir=args.records_dir)
    print("Previous run wave:", "<none>" if val is None else val)


if __name__ == "__main__":
    main()
