"""Guarded controls for leaving or ending a live battle."""

from __future__ import annotations

import re
import time
from typing import Callable, Final, Optional

import numpy as np

from core.input import safe_tap, tap_if_visible
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.ss_capture import capture_adb_screenshot
from core.state_detector import StateDetectionResult, detect_state_and_overlays
from utils.logger import log
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
_RETRY_DELAY: Final[float] = 0.6
_EXIT_DIALOG_REGION: Final[tuple[int, int, int, int]] = (130, 690, 820, 540)


def _input_guard_kwargs(
    action_guard: Optional[Callable[[], bool]],
) -> dict[str, object]:
    return (
        {"action_guard_fn": action_guard}
        if action_guard is not None
        else {}
    )


def ensure_menu_open(
    timeout_s: float = 5.0,
    *,
    action_guard: Optional[Callable[[], bool]] = None,
    running_guard: Optional[Callable[[StateDetectionResult], bool]] = None,
) -> bool:
    """Open the in-run menu without touching any action inside the menu."""

    deadline = time.monotonic() + max(0.0, timeout_s)
    while time.monotonic() < deadline:
        screenshot = capture_adb_screenshot()
        if screenshot is None:
            time.sleep(0.4)
            continue
        detection: StateDetectionResult = detect_state_and_overlays(screenshot)
        overlays = set(detection["overlays"])
        if detection["state"] != "RUNNING":
            log(
                f"[RUN_CONTROL] Refusing to open battle menu from "
                f"state={detection['state']!r}",
                "WARN",
            )
            return False
        if running_guard is not None and not running_guard(detection):
            log("[RUN_CONTROL] Battle identity guard rejected menu access", "WARN")
            return False
        if "MENU_OPEN" in overlays:
            return True
        if "MENU_CLOSED" not in overlays:
            log("[RUN_CONTROL] Menu state is neither open nor closed", "WARN")
            return False
        if action_guard is not None and not action_guard():
            log("[RUN_CONTROL] Action ownership changed before menu tap", "WARN")
            return False
        if not tap_if_visible(
            "navigation.menu_open_button",
            screenshot=screenshot,
            retries=1,
            **_input_guard_kwargs(action_guard),
        ):
            log("[RUN_CONTROL] Verified menu-open button did not match", "WARN")
            return False
        time.sleep(_RETRY_DELAY)
    log("[RUN_CONTROL] Timed out waiting for the in-run menu", "WARN")
    return False


def _exit_battle_dialog_visible(screenshot: Optional[Frame]) -> bool:
    if screenshot is None:
        return False
    x, y, w, h = _EXIT_DIALOG_REGION
    crop = screenshot[y:y + h, x:x + w]
    if crop.size == 0:
        return False
    text, confidence = ocr_text_and_conf(crop, psm=6)
    normalized = re.sub(r"[^A-Z]+", " ", text.upper()).strip()
    visible = "EXIT BATTLE" in normalized and "WHAT WOULD YOU LIKE TO DO" in normalized
    if visible:
        log(
            f"[RUN_CONTROL] Verified Exit Battle dialog "
            f"(OCR confidence={confidence:.1f})",
            "DEBUG",
        )
    return visible


def _wait_for_screen(
    predicate: Callable[[Frame], bool],
    *,
    timeout_s: float,
    poll_s: float = 0.3,
) -> Optional[Frame]:
    deadline = time.monotonic() + max(0.0, timeout_s)
    while time.monotonic() < deadline:
        screenshot = capture_adb_screenshot()
        if screenshot is not None and predicate(screenshot):
            return screenshot
        time.sleep(max(0.05, poll_s))
    return None


def _open_exit_battle_dialog(
    timeout_s: float,
    *,
    action_guard: Optional[Callable[[], bool]] = None,
    running_guard: Optional[Callable[[StateDetectionResult], bool]] = None,
) -> bool:
    if not ensure_menu_open(
        timeout_s=max(1.0, timeout_s / 2),
        action_guard=action_guard,
        running_guard=running_guard,
    ):
        return False
    screenshot = capture_adb_screenshot()
    if screenshot is None:
        return False
    detection = detect_state_and_overlays(screenshot)
    if detection["state"] != "RUNNING" or "MENU_OPEN" not in detection["overlays"]:
        log("[RUN_CONTROL] Run/menu guard failed before Exit Battle tap", "WARN")
        return False
    if running_guard is not None and not running_guard(detection):
        log("[RUN_CONTROL] Battle identity changed before Exit Battle tap", "WARN")
        return False
    if action_guard is not None and not action_guard():
        log("[RUN_CONTROL] Action ownership changed before Exit Battle tap", "WARN")
        return False
    if not safe_tap(
        "buttons.exit_battle",
        dispatch="now",
        **_input_guard_kwargs(action_guard),
    ):
        return False
    dialog = _wait_for_screen(
        _exit_battle_dialog_visible,
        timeout_s=max(1.0, timeout_s / 2),
    )
    if dialog is None:
        log("[RUN_CONTROL] Exit Battle dialog was not verified", "WARN")
        return False
    return True


def _choose_exit_battle_action(
    button_key: str,
    *,
    expected_state: str,
    timeout_s: float,
    action_guard: Optional[Callable[[], bool]] = None,
) -> bool:
    screenshot = capture_adb_screenshot()
    if not _exit_battle_dialog_visible(screenshot):
        log(f"[RUN_CONTROL] Refusing '{button_key}': Exit Battle dialog missing", "WARN")
        return False
    if action_guard is not None and not action_guard():
        log("[RUN_CONTROL] Action ownership changed before terminal action", "WARN")
        return False
    if not safe_tap(
        button_key,
        dispatch="now",
        **_input_guard_kwargs(action_guard),
    ):
        return False

    def reached_expected_state(frame: Frame) -> bool:
        return detect_state_and_overlays(frame)["state"] == expected_state

    result = _wait_for_screen(reached_expected_state, timeout_s=max(1.0, timeout_s))
    if result is None:
        log(
            f"[RUN_CONTROL] '{button_key}' did not reach state={expected_state}",
            "WARN",
        )
        return False
    return True


def surrender_run(
    timeout_s: float = 12.0,
    *,
    action_guard: Optional[Callable[[], bool]] = None,
    running_guard: Optional[Callable[[StateDetectionResult], bool]] = None,
) -> bool:
    """End the current run through Exit Battle -> Surrender."""

    if not _open_exit_battle_dialog(
        timeout_s,
        action_guard=action_guard,
        running_guard=running_guard,
    ):
        return False
    return _choose_exit_battle_action(
        "buttons.surrender:exit_battle",
        expected_state="GAME_OVER",
        timeout_s=timeout_s / 2,
        action_guard=action_guard,
    )


def go_home_from_run(
    timeout_s: float = 12.0,
    *,
    action_guard: Optional[Callable[[], bool]] = None,
) -> bool:
    """Leave the battle view without ending the current run."""

    if not _open_exit_battle_dialog(
        timeout_s,
        action_guard=action_guard,
    ):
        return False
    return _choose_exit_battle_action(
        "buttons.go_home:exit_battle",
        expected_state="HOME_SCREEN",
        timeout_s=timeout_s / 2,
        action_guard=action_guard,
    )


def return_home_from_game_over(
    timeout_s: float = 8.0,
    *,
    action_guard: Optional[Callable[[], bool]] = None,
) -> bool:
    """Leave a completed owned battle through its verified Home control."""

    screenshot = capture_adb_screenshot()
    if screenshot is None:
        return False
    detection = detect_state_and_overlays(screenshot)
    if detection["state"] != "GAME_OVER":
        log(
            "[RUN_CONTROL] Refusing Game Over Home action from "
            f"state={detection['state']!r}",
            "WARN",
        )
        return False
    if action_guard is not None and not action_guard():
        log("[RUN_CONTROL] Action ownership changed before Game Over Home", "WARN")
        return False
    if not tap_if_visible(
        "buttons.home:game_over",
        screenshot=screenshot,
        retries=1,
        **_input_guard_kwargs(action_guard),
    ):
        return False

    def reached_new_battle_home(frame: Frame) -> bool:
        current = detect_state_and_overlays(frame)
        return bool(
            current["state"] == "HOME_SCREEN"
            and detect_home_battle_control(frame).control
            is HomeBattleControl.NEW_BATTLE
        )

    result = _wait_for_screen(
        reached_new_battle_home,
        timeout_s=max(1.0, timeout_s),
    )
    if result is None:
        log(
            "[RUN_CONTROL] Game Over Home action did not reach verified NEW_BATTLE",
            "WARN",
        )
        return False
    return True


def restart_run(
    timeout_s: float = 12.0,
    *,
    action_guard: Optional[Callable[[], bool]] = None,
) -> bool:
    """Compatibility action: end the current run so Game Over can retry it."""

    if action_guard is None:
        return surrender_run(timeout_s=timeout_s)
    return surrender_run(timeout_s=timeout_s, action_guard=action_guard)


__all__ = [
    "ensure_menu_open",
    "go_home_from_run",
    "return_home_from_game_over",
    "restart_run",
    "surrender_run",
]
