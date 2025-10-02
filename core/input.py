"""Device input helpers: visible/ blind taps and basic swipes."""

from __future__ import annotations

import time
from typing import Any, Dict, Literal, Optional, Tuple

from utils.logger import log
from core.adb_utils import adb_shell
from core.tap_dispatcher import tap as enqueue_tap
from core.clickmap_access import resolve_dot_path, get_click, get_swipe
from core.label_tapper import get_label_match

DispatchMode = Literal["now", "queue"]


def _compute_offset(entry: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    if not isinstance(entry, dict):
        return None
    offset = entry.get("tap_offset")
    if isinstance(offset, dict):
        try:
            return int(offset.get("x", 0)), int(offset.get("y", 0))
        except Exception:
            return None
    roles = entry.get("roles") or []
    if isinstance(roles, list) and "upgrade_label" in roles:
        return (405, 60)
    return None


def _dispatch_tap(x: int, y: int, *, label: Optional[str], dispatch: DispatchMode) -> None:
    if dispatch == "queue":
        enqueue_tap(x, y, label=label, log_it=False)
    else:
        adb_shell(["input", "tap", str(x), str(y)], check=False)


def _dispatch_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
    adb_shell(
        [
            "input",
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(duration_ms),
        ],
        check=False,
    )


def safe_tap(
    name: str,
    *,
    require_visible: bool = True,
    retries: int = 0,
    retry_delay: float = 0.5,
    dispatch: DispatchMode = "now",
    allow_fallback: bool = False,
    screenshot=None,
    log_label: Optional[str] = None,
) -> bool:
    attempts = max(0, int(retries)) + 1
    entry = resolve_dot_path(name)
    label = log_label or name
    last_err: Optional[Exception] = None

    if dispatch not in ("now", "queue"):
        dispatch = "now"

    if require_visible:
        for attempt in range(attempts):
            try:
                bbox = get_label_match(name, screenshot=screenshot if attempt == 0 else None)
                x, y, w, h = bbox
                offset = _compute_offset(entry or {})
                tap_x = x + (offset[0] if offset else w // 2)
                tap_y = y + (offset[1] if offset else h // 2)
                log(
                    f"TAP_SAFE now={dispatch=='now'} label={label} at ({tap_x},{tap_y}) vis=True attempt={attempt+1}/{attempts}",
                    "ACTION",
                )
                _dispatch_tap(tap_x, tap_y, label=label, dispatch=dispatch)
                return True
            except Exception as exc:
                last_err = exc
                if attempt < attempts - 1:
                    time.sleep(max(0.0, float(retry_delay)))
        if allow_fallback:
            coords = get_click(name)
            if coords:
                log(
                    f"TAP_SAFE now={dispatch=='now'} label={label} at {coords} vis=False fallback=True",
                    "ACTION",
                )
                _dispatch_tap(coords[0], coords[1], label=label, dispatch=dispatch)
                return True
        if last_err is not None:
            log(f"[SKIP] TAP_SAFE failed for {label}: {last_err}", "WARN")
        return False

    coords = get_click(name)
    if not coords:
        log(f"[SKIP] TAP_SAFE blind path has no coords for {label}", "WARN")
        return False
    log(f"TAP_SAFE now={dispatch=='now'} label={label} at {coords} vis=False", "ACTION")
    _dispatch_tap(coords[0], coords[1], label=label, dispatch=dispatch)
    return True


def tap_if_visible(
    name: str,
    *,
    retries: int = 0,
    retry_delay: float = 0.5,
    dispatch: DispatchMode = "now",
    screenshot=None,
) -> bool:
    return safe_tap(
        name,
        require_visible=True,
        retries=retries,
        retry_delay=retry_delay,
        dispatch=dispatch,
        allow_fallback=False,
        screenshot=screenshot,
    )


def tap_blind(name: str, *, dispatch: DispatchMode = "queue") -> bool:
    return safe_tap(name, require_visible=False, dispatch=dispatch)


def tap_now(name: str) -> bool:
    coords = get_click(name)
    if not coords:
        log(f"[INPUT] tap_now: missing coords for '{name}'", "ERROR")
        return False
    _dispatch_tap(coords[0], coords[1], label=name, dispatch="now")
    log(f"TAP_NOW: {name} at {coords}", "ACTION")
    return True


def swipe_now(name: str) -> bool:
    swipe = get_swipe(name)
    if not swipe:
        log(f"[INPUT] swipe_now: missing swipe data for '{name}'", "ERROR")
        return False
    try:
        x1, y1 = int(swipe["x1"]), int(swipe["y1"])
        x2, y2 = int(swipe["x2"]), int(swipe["y2"])
        duration = int(swipe.get("duration_ms", 0))
    except Exception:
        log(f"[INPUT] swipe_now: invalid swipe data for '{name}'", "ERROR")
        return False
    _dispatch_swipe(x1, y1, x2, y2, duration)
    log(f"SWIPE_NOW: {name} ({x1},{y1})→({x2},{y2}) in {duration}ms", "ACTION")
    return True


__all__ = [
    "safe_tap",
    "tap_if_visible",
    "tap_blind",
    "tap_now",
    "swipe_now",
]
