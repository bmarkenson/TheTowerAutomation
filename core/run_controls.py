from __future__ import annotations

import time
from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.tap import tap_if_visible, safe_tap
from core.label_tapper import is_visible


def ensure_menu_open(timeout_s: float = 5.0) -> bool:
    """Best-effort: open the in-game menu so End Round is tappable.

    Tries tapping the End Round entry directly; if not visible, nudges UI via
    a bottom nav tap (attack) and retries briefly.
    """
    end_time = time.time() + max(0.0, timeout_s)
    while time.time() < end_time:
        img = capture_adb_screenshot()
        if img is None:
            time.sleep(0.4)
            continue
        det = detect_state_and_overlays(img)
        if "MENU_OPEN" in (det.get("overlays") or []):
            return True
        # Try an explicit menu toggle first (restored in clickmap under navigation)
        if tap_if_visible("navigation.toggle_menu", retries=1):
            time.sleep(0.6)
            # recheck next loop
            continue
        # Fallback to dot-path direct tap if label match fails
        if safe_tap("navigation.toggle_menu", require_visible=False, dispatch='now'):
            time.sleep(0.6)
            continue
        # Try tapping End Round label directly; some UIs accept it even if not open
        if tap_if_visible("overlays.end_round", retries=1):
            time.sleep(0.6)
            return True
        # Nudge UI: tap a bottom nav (attack) to stabilize, then retry
        safe_tap("navigation.goto_attack", require_visible=False, dispatch='now')
        time.sleep(0.6)
    return False


def restart_run(timeout_s: float = 12.0) -> bool:
    """End the current run and confirm Yes. Returns True if sequence was issued.

    Steps:
      1) Ensure menu is open (best-effort).
      2) Tap End Round.
      3) Tap Yes to confirm.
    """
    if not ensure_menu_open(timeout_s=timeout_s / 2):
        log("[RESTART] Failed to open menu; proceeding to attempt End Round", "WARN")
    # Tap End Round and confirm
    ok = tap_if_visible("overlays.end_round", retries=1)
    time.sleep(0.6)
    ok_yes = tap_if_visible("buttons.yes:end_round", retries=1)
    if not ok and not ok_yes:
        log("[RESTART] End Round/Yes taps may not have been accepted", "WARN")
    return True
