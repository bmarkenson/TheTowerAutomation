"""Color evidence for reward badges in the in-battle and Home interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple

import cv2
import numpy as np

from core.matcher import get_match


Region = Tuple[int, int, int, int]

MENU_REWARD_ALERT_REGION: Region = (945, 5, 65, 55)
MENU_REWARD_BADGE_REGIONS: Mapping[str, Region] = {
    "daily_missions": (852, 118, 50, 55),
    "event_missions": (852, 438, 50, 55),
}
GUILD_BADGE_OFFSET_REGION: Region = (-58, -46, 50, 55)
HOME_REWARD_BADGE_REGIONS: Mapping[str, Region] = {
    "daily_missions": (895, 220, 70, 80),
    "event_missions": (140, 600, 80, 100),
}
BADGE_PIXEL_THRESHOLD = 250


@dataclass(frozen=True)
class MenuRewardBadges:
    """Relevant badge presence measured from a verified open side menu."""

    daily_missions: bool
    event_missions: bool
    guild_chests: bool

    @property
    def any(self) -> bool:
        return self.daily_missions or self.event_missions or self.guild_chests


def menu_reward_alert_visible(screenshot) -> bool:
    """Detect a red or purple attention dot on the closed in-battle menu."""

    hsv = _hsv_crop(screenshot, MENU_REWARD_ALERT_REGION)
    if hsv is None:
        return False
    return (_red_pixels(hsv) + _purple_pixels(hsv)) >= BADGE_PIXEL_THRESHOLD


def measure_menu_reward_badges(screenshot) -> MenuRewardBadges:
    """Measure Daily, Event, and Guild badges without reading their digits."""

    values = {}
    for name, region in MENU_REWARD_BADGE_REGIONS.items():
        values[name] = _menu_badge_visible(screenshot, region)
    guild_point = None
    if (
        screenshot is not None
        and hasattr(screenshot, "shape")
        and len(screenshot.shape) >= 2
    ):
        guild_point, _confidence = get_match(
            "navigation.menu_guild",
            screenshot=screenshot,
        )
    values["guild_chests"] = bool(
        guild_point is not None
        and _menu_badge_visible(
            screenshot,
            _offset_region(guild_point, GUILD_BADGE_OFFSET_REGION),
        )
    )
    return MenuRewardBadges(**values)


def measure_home_reward_badges(screenshot) -> MenuRewardBadges:
    """Measure Daily and Event badges on a verified Home screen.

    Guild is deliberately excluded until positive Home badge evidence exists;
    its red static icon would make a broad color region unsafe.
    """

    values = {}
    for name, region in HOME_REWARD_BADGE_REGIONS.items():
        hsv = _hsv_crop(screenshot, region)
        values[name] = bool(
            hsv is not None and _red_pixels(hsv) >= BADGE_PIXEL_THRESHOLD
        )
    return MenuRewardBadges(
        daily_missions=values["daily_missions"],
        event_missions=values["event_missions"],
        guild_chests=False,
    )


def _hsv_crop(screenshot, region: Region):
    if screenshot is None or not hasattr(screenshot, "shape") or len(screenshot.shape) < 2:
        return None
    x, y, w, h = region
    screen_h, screen_w = screenshot.shape[:2]
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > screen_w or y + h > screen_h:
        return None
    crop = screenshot[y:y + h, x:x + w]
    if crop.size == 0:
        return None
    return cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)


def _menu_badge_visible(screenshot, region: Region) -> bool:
    hsv = _hsv_crop(screenshot, region)
    return bool(
        hsv is not None
        and (_red_pixels(hsv) + _purple_pixels(hsv)) >= BADGE_PIXEL_THRESHOLD
    )


def _offset_region(point: tuple[int, int], offset: Region) -> Region:
    x, y = point
    dx, dy, width, height = offset
    return x + dx, y + dy, width, height


def _red_pixels(hsv) -> int:
    hue, saturation, value = cv2.split(hsv)
    mask = (
        ((hue < 12) | (hue > 170))
        & (saturation > 120)
        & (value > 120)
    )
    return int(np.count_nonzero(mask))


def _purple_pixels(hsv) -> int:
    hue, saturation, value = cv2.split(hsv)
    mask = (
        (hue >= 115)
        & (hue <= 165)
        & (saturation > 80)
        & (value > 100)
    )
    return int(np.count_nonzero(mask))


__all__ = [
    "MenuRewardBadges",
    "measure_home_reward_badges",
    "measure_menu_reward_badges",
    "menu_reward_alert_visible",
]
