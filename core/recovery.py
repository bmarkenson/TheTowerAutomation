from __future__ import annotations

"""Shared recovery helpers for unexpected UI states."""

import time
from typing import Optional

from utils.logger import log
from core.input import tap_if_visible, safe_tap


_last_unknown_recover = 0.0
_unknown_visible_since_ts: Optional[float] = None


def update_unknown_state(is_unknown: bool) -> None:
    """Track when UNKNOWN has been continuously observed."""

    global _unknown_visible_since_ts, _last_unknown_recover
    if is_unknown:
        if _unknown_visible_since_ts is None:
            _unknown_visible_since_ts = time.monotonic()
            log("[RECOVERY] Unknown state detected; starting close timer", "INFO")
    else:
        _unknown_visible_since_ts = None
        _last_unknown_recover = 0.0


def handle_unknown_state(
    screen,
    *,
    cooldown_s: float = 2.0,
    trigger_after_s: float = 900.0,
) -> None:
    """Attempt to dismiss dialogs once UNKNOWN persists beyond trigger_after_s."""

    global _last_unknown_recover, _unknown_visible_since_ts

    now = time.monotonic()
    if trigger_after_s <= 0:
        trigger_after_s = 900.0

    if _unknown_visible_since_ts is None:
        _unknown_visible_since_ts = now
        return

    if (now - _unknown_visible_since_ts) < trigger_after_s:
        return

    if now - _last_unknown_recover < cooldown_s:
        return

    tapped = False
    try:
        if tap_if_visible("buttons.close_generic", retries=1, screenshot=screen):
            tapped = True
        elif safe_tap("buttons.close_generic", dispatch="now"):
            tapped = True
        if tapped:
            log("[RECOVERY] Unknown state detected; tapped close_generic", "WARN")
            _unknown_visible_since_ts = None
    except Exception as exc:
        log(f"[RECOVERY] close_generic attempt failed: {exc}", "ERROR")
    finally:
        _last_unknown_recover = now
