#!/usr/bin/env python3
# Combine all *.py.md files under a root into one file.
# Each section is written verbatim, separated by a single line '---------------'.
# Optional: include SPEC_LEGEND.md first if present.

import argparse
from pathlib import Path

DELIM = "---------------"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Directory containing *.py.md files")
    ap.add_argument("--out", required=True, help="Output combined file")
    ap.add_argument("--include-legend", action="store_true", help="If SPEC_LEGEND.md exists, include it as the first section")
    args = ap.parse_args()

    root = Path(args.root)
    out = Path(args.out)

    parts = []

    # Legend first (verbatim)
    legend = root / "SPEC_LEGEND.md"
    if args.include_legend and legend.exists():
        parts.append(legend.read_text(encoding="utf-8").rstrip() + "\n")
        parts.append(DELIM + "\n")

    # Deterministic order
    files = sorted(root.rglob("*.py.md"))

    for md in files:
        if md.name == "SPEC_LEGEND.md":
            continue
        body = md.read_text(encoding="utf-8").rstrip() + "\n"
        parts.append(body)
        parts.append(DELIM + "\n")

    # Remove trailing delimiter
    if parts and parts[-1].strip() == DELIM:
        parts.pop()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    print(f"Wrote combined file: {out} ({len(files)} module sections{' + legend' if args.include_legend and legend.exists() else ''})")

if __name__ == "__main__":
    main()
