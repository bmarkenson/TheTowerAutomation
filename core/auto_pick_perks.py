"""Read-only classification of the in-run Auto Pick Perks checkbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np


Region = tuple[int, int, int, int]

AUTO_PICK_CHECK_REGION: Final[Region] = (255, 220, 100, 90)
_GREEN_LOWER: Final[np.ndarray] = np.array((35, 100, 100), dtype=np.uint8)
_GREEN_UPPER: Final[np.ndarray] = np.array((85, 255, 255), dtype=np.uint8)
_MIN_ENABLED_GREEN_PIXELS: Final[int] = 800


@dataclass(frozen=True)
class AutoPickPerksEvidence:
    region: Region
    valid_region: bool
    enabled: bool
    green_pixels: int


def measure_auto_pick_perks(
    screenshot,
    region: Region = AUTO_PICK_CHECK_REGION,
) -> AutoPickPerksEvidence:
    """Return positive evidence for the green enabled checkmark."""

    x, y, w, h = region
    if (
        screenshot is None
        or not getattr(screenshot, "size", 0)
        or x < 0
        or y < 0
        or w <= 0
        or h <= 0
        or y + h > screenshot.shape[0]
        or x + w > screenshot.shape[1]
    ):
        return AutoPickPerksEvidence(region, False, False, 0)

    crop = screenshot[y : y + h, x : x + w]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, _GREEN_LOWER, _GREEN_UPPER)
    green_pixels = int(cv2.countNonZero(green))
    return AutoPickPerksEvidence(
        region=region,
        valid_region=True,
        enabled=green_pixels >= _MIN_ENABLED_GREEN_PIXELS,
        green_pixels=green_pixels,
    )


__all__ = [
    "AUTO_PICK_CHECK_REGION",
    "AutoPickPerksEvidence",
    "measure_auto_pick_perks",
]
