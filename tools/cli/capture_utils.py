#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from core.ss_capture import capture_and_save_screenshot

if TYPE_CHECKING:
    import numpy as np


def prepare_capture_recorder(
    save_dir: Optional[str],
) -> Optional[Callable[[], Optional["np.ndarray"]]]:
    if not save_dir:
        return None

    directory = Path(save_dir)
    directory.mkdir(parents=True, exist_ok=True)

    counter = {"value": 0}

    def _record_capture() -> Optional["np.ndarray"]:
        counter["value"] += 1
        path = directory / f"capture_{counter['value']:03d}.png"
        return capture_and_save_screenshot(str(path), log_capture=False)

    return _record_capture


__all__ = ["prepare_capture_recorder"]
