"""Verified navigation for one operator-authorized Tournament launch."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable, Optional

import numpy as np

from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.input import TapVerification, safe_tap
from core.ss_capture import capture_adb_screenshot, is_complete_screenshot
from core.state_detector import detect_state_and_overlays
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
CaptureFn = Callable[[], Optional[Frame]]
ActionGuard = Callable[[], bool]

TOURNAMENT_HOME_OPEN_REGION = (10, 500, 225, 110)
TOURNAMENT_BATTLE_REGION = (285, 1455, 510, 190)
_CONTROL_CONFIDENCE_FLOOR = 55.0


@dataclass(frozen=True)
class TournamentLaunchDispatch:
    dispatched: bool
    reason: str


def _region(frame: Frame, bounds: tuple[int, int, int, int]) -> Frame:
    x, y, width, height = bounds
    return frame[y : y + height, x : x + width]


def _word_visible(
    frame: Frame,
    bounds: tuple[int, int, int, int],
    word: str,
) -> bool:
    text, confidence = ocr_text_and_conf(_region(frame, bounds), psm=7)
    return bool(
        confidence >= _CONTROL_CONFIDENCE_FLOOR
        and re.search(rf"\b{re.escape(word)}\b", text.upper())
    )


def tournament_open_control_visible(screenshot: Optional[Frame]) -> bool:
    """Require verified ordinary Home plus the active Tournament OPEN control."""

    if not is_complete_screenshot(screenshot):
        return False
    detection = detect_state_and_overlays(screenshot)
    return bool(
        detection.get("state") == "HOME_SCREEN"
        and detect_home_battle_control(screenshot).control
        is HomeBattleControl.NEW_BATTLE
        and _word_visible(screenshot, TOURNAMENT_HOME_OPEN_REGION, "OPEN")
    )


def tournament_battle_control_visible(screenshot: Optional[Frame]) -> bool:
    """Require the Tournament entry screen plus its BATTLE control."""

    if not is_complete_screenshot(screenshot):
        return False
    detection = detect_state_and_overlays(screenshot)
    return bool(
        detection.get("state") == "TOURNAMENT_SCREEN"
        and _word_visible(screenshot, TOURNAMENT_BATTLE_REGION, "BATTLE")
    )


def tap_verified_tournament_open(screenshot: Frame) -> bool:
    """Enter Tournament only from fresh verified ordinary Home evidence."""

    return safe_tap(
        "buttons.tournament_open:home",
        dispatch="now",
        verification=TapVerification(
            screenshot=screenshot,
            target_region=TOURNAMENT_HOME_OPEN_REGION,
            description="tournament_open:home_new_battle",
            verifier=tournament_open_control_visible,
        ),
    )


def tap_verified_tournament_battle(screenshot: Frame) -> bool:
    """Tap only the freshly verified Tournament BATTLE control."""

    return safe_tap(
        "buttons.battle:tournament",
        dispatch="now",
        verification=TapVerification(
            screenshot=screenshot,
            target_region=TOURNAMENT_BATTLE_REGION,
            description="tournament_battle:tournament_screen",
            verifier=tournament_battle_control_visible,
        ),
    )


def dispatch_tournament_launch(
    screenshot: Optional[Frame],
    *,
    action_guard: ActionGuard,
    capture_fn: CaptureFn = capture_adb_screenshot,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    navigation_timeout_seconds: float = 8.0,
) -> TournamentLaunchDispatch:
    """Dispatch one verified Tournament BATTLE tap from Home or entry."""

    if not is_complete_screenshot(screenshot):
        return TournamentLaunchDispatch(False, "current screenshot is incomplete")
    state = str(detect_state_and_overlays(screenshot).get("state") or "")
    tournament_screen = screenshot
    if state == "HOME_SCREEN":
        if not tournament_open_control_visible(screenshot):
            return TournamentLaunchDispatch(
                False,
                "Home is not at verified NEW_BATTLE with Tournament OPEN",
            )
        if not action_guard():
            return TournamentLaunchDispatch(
                False,
                "launch authority was withdrawn before Tournament navigation",
            )
        if not tap_verified_tournament_open(screenshot):
            return TournamentLaunchDispatch(
                False,
                "verified Tournament OPEN could not be tapped",
            )
        deadline = monotonic_fn() + max(
            1.0,
            float(navigation_timeout_seconds),
        )
        tournament_screen = None
        while monotonic_fn() < deadline:
            if not action_guard():
                return TournamentLaunchDispatch(
                    False,
                    "launch authority was withdrawn before Tournament BATTLE",
                )
            sleep_fn(0.4)
            candidate = capture_fn()
            if tournament_battle_control_visible(candidate):
                tournament_screen = candidate
                break
        if tournament_screen is None:
            return TournamentLaunchDispatch(
                False,
                "Tournament entry did not expose a verified BATTLE control",
            )
    elif state != "TOURNAMENT_SCREEN":
        return TournamentLaunchDispatch(
            False,
            f"launch requires Home or Tournament entry; observed {state or 'UNKNOWN'}",
        )

    if not tournament_battle_control_visible(tournament_screen):
        return TournamentLaunchDispatch(
            False,
            "Tournament BATTLE control is not verified",
        )
    if not action_guard():
        return TournamentLaunchDispatch(
            False,
            "launch authority was withdrawn before Tournament BATTLE",
        )
    if not tap_verified_tournament_battle(tournament_screen):
        return TournamentLaunchDispatch(
            False,
            "verified Tournament BATTLE could not be tapped",
        )
    return TournamentLaunchDispatch(
        True,
        "verified Tournament BATTLE was dispatched",
    )


__all__ = [
    "TOURNAMENT_BATTLE_REGION",
    "TOURNAMENT_HOME_OPEN_REGION",
    "TournamentLaunchDispatch",
    "dispatch_tournament_launch",
    "tap_verified_tournament_battle",
    "tap_verified_tournament_open",
    "tournament_battle_control_visible",
    "tournament_open_control_visible",
]
