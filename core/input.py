"""Device input helpers: verified taps and basic swipes."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Dict, Literal, Optional, Tuple, Sequence, Union

from utils.logger import log, log_action
from core.adb_utils import input_swipe, input_tap
from core.tap_dispatcher import tap as enqueue_tap
from core.clickmap_access import get_click, get_explicit_tap, get_swipe, resolve_dot_path
from core.label_tapper import get_label_match
from core.ss_capture import is_complete_screenshot

DispatchMode = Literal["now", "queue"]
Coord = Union[Sequence[int], Sequence[float]]
Region = Tuple[int, int, int, int]


@dataclass(frozen=True)
class TapVerification:
    """Fresh target-specific evidence authorizing one non-template tap.

    Static coordinates and dynamically calculated points are not action
    authority by themselves.  Their caller must provide the frame used to
    identify the target, a bounded target region containing the tap point, and
    a predicate which rechecks the target-specific evidence on that frame.
    ``reuse_authority`` is reserved for bounded, explicitly urgent sequences
    whose contract accepts that one verified frame remains authoritative for
    multiple taps.
    """

    screenshot: Any
    target_region: Region
    description: str
    verifier: Callable[[Any], bool]
    reuse_authority: bool = False
    _cached_result: Optional[bool] = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def authorizes(self, point: Tuple[int, int]) -> bool:
        if not is_complete_screenshot(self.screenshot):
            return False
        x, y, width, height = (int(value) for value in self.target_region)
        tap_x, tap_y = point
        if width <= 0 or height <= 0:
            return False
        if not (x <= tap_x < x + width and y <= tap_y < y + height):
            return False
        if self.reuse_authority and self._cached_result is not None:
            return self._cached_result
        result = bool(self.verifier(self.screenshot))
        if self.reuse_authority:
            object.__setattr__(self, "_cached_result", result)
        return result


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
        input_tap(x, y, check=False)


def _dispatch_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
    input_swipe(x1, y1, x2, y2, duration_ms, check=False)


def _operator_label(label: str) -> str:
    path, separator, context = label.partition(":")
    display = path.rsplit(".", 1)[-1].replace("_", " ")
    if display.startswith("goto "):
        display = f"go to {display[len('goto '):]}"
    display = display[:1].upper() + display[1:]
    if separator and context:
        display += f" ({context.replace('_', ' ')})"
    return display


def _tap_summary(label: str, dispatch: DispatchMode) -> str:
    verb = "queued" if dispatch == "queue" else "requested"
    return f"Tap {verb}: {_operator_label(label)}"


def safe_tap(
    name: Union[str, Coord],
    *,
    retries: int = 0,
    retry_delay: float = 0.5,
    dispatch: DispatchMode = "now",
    screenshot=None,
    log_label: Optional[str] = None,
    verification: Optional[TapVerification] = None,
) -> bool:
    """Tap only a freshly matched or explicitly reverified target.

    Template-backed names are always matched immediately before dispatch.
    Coordinate and static named targets require :class:`TapVerification`;
    otherwise the tap fails closed.
    """

    if dispatch not in ("now", "queue"):
        dispatch = "now"
    entry = resolve_dot_path(name) if isinstance(name, str) else None
    has_template = isinstance(entry, dict) and bool(entry.get("match_template"))
    label = log_label or (name if isinstance(name, str) else None)

    if has_template:
        attempts = max(0, int(retries)) + 1
        last_err: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                bbox = get_label_match(
                    name,
                    screenshot=screenshot if attempt == 0 else None,
                )
                x, y, width, height = bbox
                offset = _compute_offset(entry)
                tap_x = x + (offset[0] if offset else width // 2)
                tap_y = y + (offset[1] if offset else height // 2)
                log_action(
                    _tap_summary(str(label), dispatch),
                    detail=(
                        f"TAP_SAFE now={dispatch=='now'} label={label} "
                        f"at ({tap_x},{tap_y}) verified=template "
                        f"attempt={attempt+1}/{attempts}"
                    ),
                )
                _dispatch_tap(tap_x, tap_y, label=str(label), dispatch=dispatch)
                return True
            except Exception as exc:
                last_err = exc
                if attempt < attempts - 1:
                    time.sleep(max(0.0, float(retry_delay)))
        if last_err is not None:
            log(f"[SKIP] TAP_SAFE failed for {label}: {last_err}", "WARN")
        return False

    if isinstance(name, (tuple, list)):
        try:
            tap_x = int(name[0])
            tap_y = int(name[1])
        except (IndexError, TypeError, ValueError):
            log(f"[SKIP] TAP_SAFE invalid coordinate target: {name!r}", "WARN")
            return False
        label = log_label or f"tap@{tap_x},{tap_y}"
        summary_label = log_label or "screen target"
    else:
        coords = get_explicit_tap(name)
        if not coords:
            log(
                f"[SKIP] TAP_SAFE static path has no explicit coords for {label}",
                "WARN",
            )
            return False
        tap_x, tap_y = coords
        summary_label = str(label)

    if verification is None:
        log(
            f"[SKIP] TAP_SAFE refused unverified target {label} "
            f"at ({tap_x},{tap_y})",
            "WARN",
        )
        return False
    try:
        authorized = verification.authorizes((tap_x, tap_y))
    except Exception as exc:
        log(
            f"[SKIP] TAP_SAFE verification failed for {label}: {exc}",
            "WARN",
        )
        return False
    if not authorized:
        log(
            f"[SKIP] TAP_SAFE target check rejected {label}: "
            f"{verification.description}",
            "WARN",
        )
        return False

    log_action(
        _tap_summary(str(summary_label), dispatch),
        detail=(
            f"TAP_SAFE now={dispatch=='now'} label={label} "
            f"at ({tap_x},{tap_y}) verified={verification.description}"
        ),
    )
    _dispatch_tap(tap_x, tap_y, label=str(label), dispatch=dispatch)
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
        retries=retries,
        retry_delay=retry_delay,
        dispatch=dispatch,
        screenshot=screenshot,
    )


def tap_unchecked_for_tooling(name: str, *, reason: str) -> bool:
    """Issue an explicit static tap from an operator-invoked development tool."""

    if not str(reason or "").strip():
        log(f"[INPUT] unchecked tooling tap lacks a reason for '{name}'", "ERROR")
        return False
    coords = get_click(name)
    if not coords:
        log(f"[INPUT] tooling tap: missing coords for '{name}'", "ERROR")
        return False
    _dispatch_tap(coords[0], coords[1], label=name, dispatch="now")
    log_action(
        f"Unchecked tooling tap requested: {_operator_label(name)}",
        detail=f"TAP_TOOLING: {name} at {coords} reason={reason}",
    )
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
    log_action(
        f"Swipe requested: {_operator_label(name)}",
        detail=f"SWIPE_NOW: {name} ({x1},{y1})→({x2},{y2}) in {duration}ms",
    )
    return True


__all__ = [
    "TapVerification",
    "safe_tap",
    "tap_if_visible",
    "tap_unchecked_for_tooling",
    "swipe_now",
]
