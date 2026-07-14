"""Pytest-wide safeguards for TheTower tests."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


_TEST_LOG_DIR = Path(tempfile.mkdtemp(prefix="thetower-pytest-"))
_TEST_ACTION_LOG = _TEST_LOG_DIR / "actions.log"

# Set this during conftest import, before pytest imports test modules and their
# runtime dependencies. Synthetic log events must never enter the live log.
os.environ["TOWER_ACTION_LOG_PATH"] = str(_TEST_ACTION_LOG)


def pytest_report_header() -> str:
    return f"isolated action log: {_TEST_ACTION_LOG}"
