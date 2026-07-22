"""Read-only visual classification of persistent preset selection borders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np


Region = tuple[int, int, int, int]

FARM_PRESET_SLOT: Final[Region] = (12, 185, 210, 98)
CARDS_FARM_PRESET_SLOT: Final[Region] = (12, 371, 210, 98)
BOTS_FARM_PRESET_SLOT: Final[Region] = (18, 496, 347, 98)
TOURNEY_PRESET_SLOT: Final[Region] = (225, 185, 210, 98)
CARDS_TOURNAMENT_PRESET_SLOT: Final[Region] = (225, 371, 210, 98)
BOTS_AMPLIFY_PRESET_SLOT: Final[Region] = (713, 496, 347, 98)
INACTIVE_PRESET_SLOTS: Final[tuple[Region, ...]] = (
    (225, 185, 210, 98),
    (437, 185, 210, 98),
    (648, 185, 210, 98),
    (858, 185, 210, 98),
)

_BORDER_BAND_PX: Final[int] = 14
_GREEN_LOWER: Final[np.ndarray] = np.array((35, 100, 120), dtype=np.uint8)
_GREEN_UPPER: Final[np.ndarray] = np.array((75, 255, 255), dtype=np.uint8)
_CYAN_LOWER: Final[np.ndarray] = np.array((80, 100, 120), dtype=np.uint8)
_CYAN_UPPER: Final[np.ndarray] = np.array((110, 255, 255), dtype=np.uint8)
_MIN_SELECTED_GREEN_PIXELS: Final[int] = 1000
_SELECTED_COLOR_RATIO: Final[int] = 3


@dataclass(frozen=True)
class PresetSlotSelection:
    region: Region
    valid_region: bool
    selected: bool
    green_pixels: int
    cyan_pixels: int


def measure_preset_slot_selection(
    screenshot,
    region: Region = FARM_PRESET_SLOT,
) -> PresetSlotSelection:
    """Classify a preset slot by its green selected or cyan inactive border."""

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
        return PresetSlotSelection(region, False, False, 0, 0)

    slot = screenshot[y : y + h, x : x + w]
    hsv = cv2.cvtColor(slot, cv2.COLOR_BGR2HSV)
    border = np.zeros((h, w), dtype=np.uint8)
    band = min(_BORDER_BAND_PX, max(1, min(w, h) // 2))
    border[:band, :] = 255
    border[-band:, :] = 255
    border[:, :band] = 255
    border[:, -band:] = 255

    green = cv2.bitwise_and(cv2.inRange(hsv, _GREEN_LOWER, _GREEN_UPPER), border)
    cyan = cv2.bitwise_and(cv2.inRange(hsv, _CYAN_LOWER, _CYAN_UPPER), border)
    green_pixels = int(cv2.countNonZero(green))
    cyan_pixels = int(cv2.countNonZero(cyan))
    selected = (
        green_pixels >= _MIN_SELECTED_GREEN_PIXELS
        and green_pixels > _SELECTED_COLOR_RATIO * max(1, cyan_pixels)
    )
    return PresetSlotSelection(region, True, selected, green_pixels, cyan_pixels)


__all__ = [
    "BOTS_AMPLIFY_PRESET_SLOT",
    "BOTS_FARM_PRESET_SLOT",
    "CARDS_TOURNAMENT_PRESET_SLOT",
    "CARDS_FARM_PRESET_SLOT",
    "FARM_PRESET_SLOT",
    "INACTIVE_PRESET_SLOTS",
    "PresetSlotSelection",
    "TOURNEY_PRESET_SLOT",
    "measure_preset_slot_selection",
]
