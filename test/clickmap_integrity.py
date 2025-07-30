# test/clickmap_integrity.py

import os
import json

CLICKMAP_PATH = "coords/clickmap.json"
TEMPLATE_DIR = "assets/match_templates"


def validate_entry(name, entry):
    errors = []
    if "match_template" in entry:
        path = os.path.join(TEMPLATE_DIR, entry["match_template"])
        if not os.path.exists(path):
            errors.append(f"Missing template image: {entry['match_template']}")

    if "match_region" in entry:
        region = entry["match_region"]
        for key in ["x", "y", "w", "h"]:
            if key not in region:
                errors.append(f"Missing match_region key '{key}'")
            elif not isinstance(region[key], int):
                errors.append(f"match_region.{key} is not an integer")

    if "tap" in entry:
        for k in ["x", "y"]:
            if k not in entry["tap"]:
                errors.append(f"tap.{k} missing")

    if "swipe" in entry:
        for k in ["x1", "y1", "x2", "y2", "duration_ms"]:
            if k not in entry["swipe"]:
                errors.append(f"swipe.{k} missing")

    return errors


def main():
    if not os.path.exists(CLICKMAP_PATH):
        print("[ERROR] clickmap.json not found")
        return

    with open(CLICKMAP_PATH, "r") as f:
        clickmap = json.load(f)

    total_errors = 0
    for name, entry in clickmap.items():
        errs = validate_entry(name, entry)
        if errs:
            total_errors += len(errs)
            print(f"[FAIL] {name}:")
            for e in errs:
                print(f"    - {e}")

    if total_errors == 0:
        print("[PASS] All clickmap entries look valid.")
    else:
        print(f"[SUMMARY] {total_errors} total issues found.")


if __name__ == "__main__":
    main()
