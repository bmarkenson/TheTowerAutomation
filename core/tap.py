"""
core/tap.py

Unified, visibility-aware tap helpers that preserve the dual-path input model:
- Immediate taps (now) via ADB
- Queued taps via tap_dispatcher

APIs:
  safe_tap(name, *, require_visible=True, retries=0, retry_delay=0.5,
           dispatch='now', allow_fallback=False, screenshot=None, log_label=None) -> bool

  tap_if_visible(name, *, retries=0, retry_delay=0.5, dispatch='now', screenshot=None) -> bool
  tap_blind(name, *, dispatch='queue') -> bool

Notes:
- When require_visible=True, matching is done via core.label_tapper.get_label_match().
- When allow_fallback=True and matching fails, falls back to click center from clickmap.
- Offsets: if entry.tap_offset exists (or entry.roles contains 'upgrade_label'),
  offset is applied relative to matched top-left; no offset is applied on blind fallback.
"""

from typing import Optional, Tuple
import time

from utils.logger import log
from core.adb_utils import adb_shell
from core.tap_dispatcher import tap as enqueue_tap
from core.clickmap_access import resolve_dot_path, get_click
from core.label_tapper import get_label_match


def _compute_offset_for_entry(entry: dict) -> Optional[Tuple[int, int]]:
    """Return (dx, dy) from entry.tap_offset or upgrade-label default; else None."""
    if not isinstance(entry, dict):
        return None
    if "tap_offset" in entry and isinstance(entry["tap_offset"], dict):
        off = entry["tap_offset"]
        try:
            return int(off.get("x", 0)), int(off.get("y", 0))
        except Exception:
            return None
    roles = entry.get("roles") or []
    if isinstance(roles, list) and "upgrade_label" in roles:
        return 405, 60
    return None


def _dispatch_tap(x: int, y: int, *, label: Optional[str], dispatch: str) -> None:
    if dispatch == 'queue':
        # Avoid double per-tap logging; safe_tap logs one ACTION line.
        enqueue_tap(x, y, label=label, log_it=False)
    else:
        adb_shell(["input", "tap", str(x), str(y)])


def safe_tap(
    name: str,
    *,
    require_visible: bool = True,
    retries: int = 0,
    retry_delay: float = 0.5,
    dispatch: str = 'now',
    allow_fallback: bool = False,
    screenshot=None,
    log_label: Optional[str] = None,
) -> bool:
    """
    Visibility-aware tap. Returns True on tap sent, else False.

    - When require_visible, match the label each attempt. Retries recapture unless a
      screenshot is provided (in which case it is reused).
    - When allow_fallback and match fails after retries, falls back to get_click(name).
    - dispatch: 'now' (ADB) or 'queue' (tap_dispatcher).
    """
    attempts = max(0, int(retries)) + 1
    entry = resolve_dot_path(name)
    label = log_label or name
    last_err = None

    if dispatch not in ('now', 'queue'):
        dispatch = 'now'

    if require_visible:
        for i in range(attempts):
            try:
                # Fresh screenshot per attempt unless caller provided one.
                bbox = get_label_match(name, screenshot=screenshot if i == 0 else None)
                x, y, w, h = bbox
                dx_dy = _compute_offset_for_entry(entry or {})
                tap_x = x + (dx_dy[0] if dx_dy else w // 2)
                tap_y = y + (dx_dy[1] if dx_dy else h // 2)
                log(f"TAP_SAFE now={dispatch=='now'} label={label} at ({tap_x},{tap_y}) vis=True attempt={i+1}/{attempts}", "ACTION")
                _dispatch_tap(tap_x, tap_y, label=label, dispatch=dispatch)
                return True
            except Exception as e:
                last_err = e
                if i < attempts - 1:
                    time.sleep(max(0.0, float(retry_delay)))
        # All attempts failed; optionally fallback
        if allow_fallback:
            coords = get_click(name)
            if coords:
                log(f"TAP_SAFE now={dispatch=='now'} label={label} at {coords} vis=False fallback=True", "ACTION")
                _dispatch_tap(coords[0], coords[1], label=label, dispatch=dispatch)
                return True
        # Failure
        if last_err is not None:
            log(f"[SKIP] TAP_SAFE failed for {label}: {last_err}", "WARN")
        return False

    # require_visible=False → blind path
    coords = get_click(name)
    if not coords:
        log(f"[SKIP] TAP_SAFE blind path has no coords for {label}", "WARN")
        return False
    log(f"TAP_SAFE now={dispatch=='now'} label={label} at {coords} vis=False", "ACTION")
    _dispatch_tap(coords[0], coords[1], label=label, dispatch=dispatch)
    return True


def tap_if_visible(name: str, *, retries: int = 0, retry_delay: float = 0.5, dispatch: str = 'now', screenshot=None) -> bool:
    return safe_tap(name, require_visible=True, retries=retries, retry_delay=retry_delay, dispatch=dispatch, allow_fallback=False, screenshot=screenshot)


def tap_blind(name: str, *, dispatch: str = 'queue') -> bool:
    return safe_tap(name, require_visible=False, dispatch=dispatch)

