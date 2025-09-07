#!/usr/bin/env python3
# Split a combined specs file into individual .py.md files.
# Sections are separated by a line that is exactly '---------------'.
# For each section, the FIRST NON-EMPTY LINE is the module path like 'core/foo.py'.
# We write the whole section verbatim to '<out>/<core/foo.py>.md'.

import argparse, sys
from pathlib import Path

DELIM = "---------------"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True, help="Combined specs file")
    ap.add_argument("--out", dest="outdir", required=True, help="Output directory")
    args = ap.parse_args()

    combined = Path(args.infile).read_text(encoding="utf-8").splitlines()
    outroot = Path(args.outdir)

    # Split by delimiter lines (exact match after strip)
    blocks, cur = [], []
    for line in combined:
        if line.strip() == DELIM:
            if cur:
                blocks.append(cur); cur = []
            continue
        cur.append(line)
    if any(s.strip() for s in cur):
        blocks.append(cur)

    if not blocks:
        print("No sections found. Ensure sections are separated by a line '---------------'.", file=sys.stderr)
        sys.exit(1)

    outroot.mkdir(parents=True, exist_ok=True)
    written = 0
    for block in blocks:
        # Trim leading/trailing blank lines in the block (but keep internal spacing)
        while block and block[0].strip() == "":
            block.pop(0)
        while block and block[-1].strip() == "":
            block.pop()

        if not block:
            continue

        # First non-empty line is the module path (e.g., 'core/foo.py')
        module_line = block[0].strip()
        if not module_line.endswith(".py"):
            print(f"Skipping block (first line not a .py path): {module_line}", file=sys.stderr)
            continue

        out_path = outroot / (module_line + ".md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(block).rstrip() + "\n"
        out_path.write_text(text, encoding="utf-8")
        print(f"Wrote {out_path}")
        written += 1

    print(f"Done. Sections written: {written}")

if __name__ == "__main__":
    main()
