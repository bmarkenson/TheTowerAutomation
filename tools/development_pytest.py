"""Pytest policy for the development checkpoint's excluded host tools."""

from __future__ import annotations

import os
from typing import NoReturn

import pytest


EXCLUDE_HOST_TOOLS_ENV = "THETOWER_CHECKPOINT_EXCLUDE_HOST_TOOLS"


@pytest.fixture(autouse=True)
def exclude_tesseract_host_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip only tests that cross into the excluded Tesseract executable."""

    if os.environ.get(EXCLUDE_HOST_TOOLS_ENV) != "1":
        return
    import pytesseract.pytesseract as pytesseract_engine

    def unavailable(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.skip("requires excluded host prerequisite: tesseract")

    monkeypatch.setattr(pytesseract_engine, "get_tesseract_version", unavailable)
    monkeypatch.setattr(pytesseract_engine, "run_tesseract", unavailable)
