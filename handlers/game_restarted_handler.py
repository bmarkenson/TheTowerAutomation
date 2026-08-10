"""Guarded inputs for The Tower's post-process-restart Welcome Back modal."""

from __future__ import annotations

from enum import Enum
from typing import Optional

import numpy as np

from core.input import ActionGuard, safe_tap
from core.state_detector import detect_state_and_overlays
from utils.logger import log


Frame = np.ndarray


class GameRestartedAction(str, Enum):
    NONE = "none"
    RESUME = "resume"
    END_RUN = "end_run"


def handle_game_restarted(
    screenshot: Optional[Frame],
    *,
    action: GameRestartedAction,
    action_guard_fn: ActionGuard = None,
) -> bool:
    """Dispatch exactly one button from a freshly proven Welcome Back modal."""

    if screenshot is None:
        return False
    detection = detect_state_and_overlays(screenshot)
    if detection["state"] != "GAME_RESTARTED":
        log(
            "[EMULATOR_RECOVERY] Refusing Welcome Back input from "
            f"state={detection['state']!r}",
            "WARN",
        )
        return False
    if action is GameRestartedAction.NONE:
        return False
    key = (
        "buttons.resume_game:game_restarted"
        if action is GameRestartedAction.RESUME
        else "buttons.end_run:game_restarted"
    )
    return safe_tap(
        key,
        screenshot=screenshot,
        dispatch="now",
        retries=0,
        failure_log_level="DEBUG",
        action_guard_fn=action_guard_fn,
    )


__all__ = ["GameRestartedAction", "handle_game_restarted"]
