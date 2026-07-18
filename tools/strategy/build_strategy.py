#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "strategy_builders" / "build_strategy.py"

if not SCRIPT.exists():
    print(f"Underlying builder not found: {SCRIPT}", file=sys.stderr)
    sys.exit(2)

os.execv(
    sys.executable,
    [sys.executable, str(SCRIPT), *sys.argv[1:]],
)
