"""Pytest-wide safeguards for TheTower tests."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest


_TEST_LOG_DIR = Path(tempfile.mkdtemp(prefix="thetower-pytest-"))
_TEST_ACTION_LOG = _TEST_LOG_DIR / "actions.log"

# Set this during conftest import, before pytest imports test modules and their
# runtime dependencies. Synthetic log events must never enter the live log.
os.environ["TOWER_ACTION_LOG_PATH"] = str(_TEST_ACTION_LOG)
os.environ["THETOWER_DEVELOPMENT_LOG_DIR"] = str(_TEST_LOG_DIR)


@pytest.fixture(autouse=True)
def reset_process_automation_singleton():
    """Prevent one App.run shutdown latch from leaking into another test."""

    from core.run_state import AUTOMATION

    AUTOMATION._reset_for_testing()
    try:
        yield
    finally:
        AUTOMATION._reset_for_testing()


def pytest_report_header() -> str:
    return f"isolated action log: {_TEST_ACTION_LOG}"
