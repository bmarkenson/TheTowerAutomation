from __future__ import annotations

import time

from typing import Final

from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.state_detector import StateDetectionResult, detect_state_and_overlays
from core.input import tap_if_visible, safe_tap

_RETRY_DELAY: Final[float] = 0.6


def ensure_menu_open(timeout_s: float = 5.0) -> bool:
    """Best-effort: open the in-game menu so End Round is tappable.

    Tries tapping the End Round entry directly; if not visible, nudges UI via
    a bottom nav tap (attack) and retries briefly.
    """
    timeout = max(0.0, timeout_s)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        img = capture_adb_screenshot()
        if img is None:
            time.sleep(0.4)
            continue
        det: StateDetectionResult = detect_state_and_overlays(img)
        if "MENU_OPEN" in det["overlays"]:
            return True
        # Try an explicit menu toggle first (restored in clickmap under navigation)
        if tap_if_visible("navigation.toggle_menu", retries=1):
            time.sleep(_RETRY_DELAY)
            # recheck next loop
            continue
        # Fallback to dot-path direct tap if label match fails
        if safe_tap("navigation.toggle_menu", require_visible=False, dispatch='now'):
            time.sleep(_RETRY_DELAY)
            continue
        # Try tapping End Round label directly; some UIs accept it even if not open
        if tap_if_visible("overlays.end_round", retries=1):
            time.sleep(_RETRY_DELAY)
            return True
        # Nudge UI: tap a bottom nav (attack) to stabilize, then retry
        safe_tap("navigation.goto_attack", require_visible=False, dispatch='now')
        time.sleep(_RETRY_DELAY)
    return False


def restart_run(timeout_s: float = 12.0) -> bool:
    """End the current run and confirm Yes. Returns True if sequence was issued.

    Steps:
      1) Ensure menu is open (best-effort).
      2) Tap End Round.
      3) Tap Yes to confirm.
    """
    menu_open = ensure_menu_open(timeout_s=timeout_s / 2)
    if not menu_open:
        log("[RESTART] Failed to open menu; proceeding to attempt End Round", "WARN")
        if safe_tap("navigation.toggle_menu", require_visible=False, dispatch="now"):
            # Give the UI a moment to render the menu if the toggle succeeds
            time.sleep(_RETRY_DELAY)
    # Tap End Round and confirm (fallback to blind taps when template match fails)
    ok = tap_if_visible("overlays.end_round", retries=1)
    if not ok:
        ok = safe_tap("overlays.end_round", require_visible=False)
    time.sleep(_RETRY_DELAY)
    yes_ready = tap_if_visible("buttons.yes:end_round", retries=1)
    if not yes_ready:
        log("[RESTART] Yes button missing; aborting confirmation", "WARN")
        return False
    ok_yes = safe_tap("buttons.yes:end_round", require_visible=False)
    if not ok and not ok_yes:
        log("[RESTART] End Round/Yes taps may not have been accepted", "WARN")
    return True
