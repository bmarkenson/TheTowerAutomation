"""OCR and tap helpers for the upgrade buy-quantity selector."""

from __future__ import annotations

import difflib
import time
from typing import Callable, Dict, Optional, Tuple
from typing import Literal, cast

import cv2  # type: ignore
import numpy as np  # type: ignore

from core.input import TapVerification, safe_tap
from core.clickmap_access import resolve_dot_path
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log
from utils.ocr_utils import preprocess_binary, ocr_text_and_conf
from pathlib import Path

BuyQuantity = Literal["max", "x100", "x10", "x5", "x1"]


_BUY_ALLOWED = {"max", "x100", "x10", "x5", "x1"}
_WHITELIST_BY_QUANTITY = {
    "max": "MAX",
    "x100": "x100",
    "x10": "x10",
    "x5": "x5",
    "x1": "x1",
}
_BUTTON_CENTER_X_RATIOS: Dict[str, Tuple[float, ...]] = {
    "max": (0.2559, 0.9227),
    "x100": (0.3886,),
    "x10": (0.5213,),
    "x5": (0.6540,),
    "x1": (0.7867,),
}
_BUTTON_ROW_CENTER_Y_RATIO = 0.075
_COLLAPSED_CENTER_X_RATIO = 0.9227
_COLLAPSED_CENTER_Y_RATIO = 0.073
_BUY_COLLAPSED_TOP_RATIO = 0.02
_BUY_COLLAPSED_BOTTOM_RATIO = 0.13
_BUY_COLLAPSED_RIGHT_RATIO = 0.97
_BUY_COLLAPSED_WIDTH_RATIO = 0.22
_OCR_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
_DEBUG_DIR = Path("debug/buy_quantity")


def _save_debug(image: np.ndarray, name: str) -> None:
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(_DEBUG_DIR / name), image)
    except Exception:
        pass


def _upgrade_area_rect() -> Tuple[int, int, int, int]:
    entry = resolve_dot_path("_shared_match_regions.upgrade_menu_area")
    if not entry or "match_region" not in entry:
        raise RuntimeError("upgrade_menu_area region missing from clickmap")
    region = entry["match_region"]
    return int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])


def _normalize_quantity(value: str) -> str:
    if not value:
        raise ValueError("Empty quantity value")
    cleaned = value.strip().lower().replace("×", "x")
    allowed_chars = []
    for ch in cleaned:
        if ch.isalnum() or ch == "x":
            allowed_chars.append(ch)
    cleaned = "".join(allowed_chars)

    substitutions = {
        "mx": "max",
        "ma": "max",
        "maxx": "max",
        "maax": "max",
        "x1o": "x10",
    }
    if cleaned in substitutions:
        cleaned = substitutions[cleaned]

    cleaned = (
        cleaned.replace("l", "1")
        .replace("i", "1")
        .replace("t", "1")
        .replace("o", "0")
        .replace("q", "0")
        .replace("d", "0")
        .replace("s", "5")
    )
    if cleaned == "x":
        cleaned = "x1"
    if cleaned == "mx":
        cleaned = "max"
    if cleaned not in _BUY_ALLOWED:
        # try small corrections
        cleaned = cleaned.replace("l", "1").replace("o", "0")
        if cleaned not in _BUY_ALLOWED:
            best = max(
                _BUY_ALLOWED,
                key=lambda candidate: difflib.SequenceMatcher(None, candidate, cleaned).ratio(),
            )
            cleaned = best
    return cast(BuyQuantity, cleaned)


def _collapsed_center(rect: Tuple[int, int, int, int]) -> Tuple[int, int]:
    x, y, w, h = rect
    return (
        x + int(w * _COLLAPSED_CENTER_X_RATIO),
        y + int(h * _COLLAPSED_CENTER_Y_RATIO),
    )


def _expanded_centers(rect: Tuple[int, int, int, int], quantity: BuyQuantity) -> Tuple[Tuple[int, int], ...]:
    ratios = _BUTTON_CENTER_X_RATIOS.get(quantity)
    if ratios is None:
        raise ValueError(f"Unsupported quantity '{quantity}'")
    x, y, w, h = rect
    centers = []
    for ratio in ratios:
        centers.append(
            (
                x + int(w * ratio),
                y + int(h * _BUTTON_ROW_CENTER_Y_RATIO),
            )
        )
    return tuple(centers)


def _tap_point(
    point: Tuple[int, int],
    *,
    screenshot: np.ndarray,
    target_region: Tuple[int, int, int, int],
    description: str,
    verifier: Callable[[np.ndarray], bool],
) -> bool:
    x, y = point
    return safe_tap(
        (int(x), int(y)),
        dispatch="now",
        log_label="buy_quantity",
        verification=TapVerification(
            screenshot=screenshot,
            target_region=target_region,
            description=description,
            verifier=verifier,
        ),
    )


def get_buy_quantity_regions(image: np.ndarray) -> Dict[str, object]:
    """Return the key rectangles and tap targets for the selector.

    Parameters
    ----------
    image:
        Screenshot of the current game frame in BGR order.

    Returns
    -------
    dict
        Mapping containing the full area rect, the collapsed badge rect,
        the tap centre for toggling the selector, and the tap centres for
        each quantity option.
    """
    x, y, w, h = _upgrade_area_rect()
    left = x + int(w * _BUY_COLLAPSED_RIGHT_RATIO) - int(w * _BUY_COLLAPSED_WIDTH_RATIO)
    right = x + int(w * _BUY_COLLAPSED_RIGHT_RATIO)
    top = y + int(h * _BUY_COLLAPSED_TOP_RATIO)
    bottom = y + int(h * _BUY_COLLAPSED_BOTTOM_RATIO)

    collapsed_rect = (left, top, right - left, bottom - top)
    collapsed_center = _collapsed_center((x, y, w, h))

    button_centers: Dict[str, Tuple[Tuple[int, int], ...]] = {}
    for quantity in ("max", "x100", "x10", "x5", "x1"):
        button_centers[quantity] = _expanded_centers((x, y, w, h), quantity)  # type: ignore[arg-type]

    return {
        "area_rect": (x, y, w, h),
        "collapsed_rect": collapsed_rect,
        "collapsed_center": collapsed_center,
        "button_centers": button_centers,
    }


def is_buy_quantity_expanded(image: np.ndarray) -> bool:
    """Return True when the selector options row is visible."""

    try:
        return "BUY_QUANTITY_MENU_EXPANDED" in set(
            detect_state_and_overlays(image).get("overlays") or ()
        )
    except Exception:
        return False


def collapse_buy_quantity(
    *,
    screenshot: Optional[np.ndarray] = None,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_attempts: int = 3,
) -> Optional[np.ndarray]:
    """Best-effort collapse of the expanded selector.

    Returns the latest screenshot (collapsed when successful) so callers can
    continue working with a fresh frame.
    """

    current = screenshot if screenshot is not None else capture_fn()
    if current is None:
        return None

    try:
        area_rect = _upgrade_area_rect()
    except RuntimeError:
        return current

    collapsed_point = _collapsed_center(area_rect)

    attempts = 0
    while attempts < max_attempts:
        if not is_buy_quantity_expanded(current):
            return current

        if not _tap_point(
            collapsed_point,
            screenshot=current,
            target_region=area_rect,
            description="buy_quantity:expanded_selector",
            verifier=is_buy_quantity_expanded,
        ):
            return current
        sleep_fn(0.35)

        current = capture_fn()
        if current is None:
            attempts += 1
            continue

        attempts += 1

    return current


def _read_collapsed_quantity(
    image: np.ndarray,
    *,
    expected: Optional[BuyQuantity] = None,
) -> Optional[BuyQuantity]:
    """OCR the currently selected quantity from ``image``.

    The function first performs a broad OCR pass that accepts any quantity. If
    the result differs from ``expected`` (when provided) it retries with a
    narrow whitelist so that callers requesting a specific value do not accept
    stale text rendered in the region.
    """
    x, y, w, h = _upgrade_area_rect()
    top = y + int(h * _BUY_COLLAPSED_TOP_RATIO)
    bottom = y + int(h * _BUY_COLLAPSED_BOTTOM_RATIO)
    right = x + int(w * _BUY_COLLAPSED_RIGHT_RATIO)
    left = right - int(w * _BUY_COLLAPSED_WIDTH_RATIO)
    if bottom <= top or right <= left:
        return None

    crop = image[top:bottom, left:right]
    if crop.size == 0:
        return None

    scale = 3.0
    enlarged = cv2.resize(crop, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(enlarged, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 200), (180, 70, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _OCR_KERNEL, iterations=1)

    # Convert to BGR for reuse with existing OCR helper
    mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    bin_img = preprocess_binary(mask_bgr, alpha=1.0, block=15, C=2, close=(3, 3), choose_best=False)

    def _run_ocr(whitelist: str) -> Optional[BuyQuantity]:
        config_extra = f"--oem 3 -c tessedit_char_whitelist={whitelist}"

        text_mask, _ = ocr_text_and_conf(bin_img, psm=7, config_extra=config_extra)
        cleaned = text_mask.replace("\n", "").strip()
        if not cleaned or len(cleaned) < 2:
            text_enlarged, _ = ocr_text_and_conf(enlarged, psm=7, config_extra=config_extra)
            cleaned = text_enlarged.replace("\n", "").strip()
        if not cleaned or len(cleaned) < 2:
            text_crop, _ = ocr_text_and_conf(crop, psm=7, config_extra=config_extra)
            cleaned = text_crop.replace("\n", "").strip()
        if cleaned and len(cleaned) >= 1:
            normalized = _normalize_quantity(cleaned)
            if normalized in _BUY_ALLOWED:
                return cast(BuyQuantity, normalized)
        return None

    general = _run_ocr("MAXx105")

    if expected is not None:
        if general == expected:
            return expected

        whitelist = _WHITELIST_BY_QUANTITY.get(expected)
        if whitelist:
            candidate = _run_ocr(whitelist)
            if candidate == expected:
                return candidate

        # Signal to callers that the read did not match the expected value.
        return None

    return general


def read_buy_quantity_from_image(
    image: np.ndarray,
    *,
    expected: Optional[BuyQuantity] = None,
) -> Optional[BuyQuantity]:
    """Public wrapper around :func:`_read_collapsed_quantity`."""
    return _read_collapsed_quantity(image, expected=expected)


def detect_current_buy_quantity(
    screenshot: Optional[np.ndarray] = None,
    *,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
) -> Optional[BuyQuantity]:
    """Detect the active buy quantity, capturing a screenshot if needed."""
    image = screenshot if screenshot is not None else capture_fn()
    if image is None:
        return None
    return _read_collapsed_quantity(image)


def ensure_buy_quantity(
    quantity: str,
    *,
    screenshot: Optional[np.ndarray] = None,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_attempts: int = 5,
) -> np.ndarray:
    """Ensure the selector is set to ``quantity``.

    Parameters
    ----------
    quantity:
        Target quantity literal (case-insensitive).
    screenshot:
        Optional pre-captured frame to reuse on the first attempt.
    capture_fn / sleep_fn:
        Hooks injected by callers to control capture/timing in tests.

    Returns
    -------
    np.ndarray
        A screenshot captured after the selector has been adjusted to the
        desired value.
    """
    target = _normalize_quantity(quantity)

    log(f"[UPGRADE_BUY] Requested quantity '{target}'", "MATCH")

    current = screenshot if screenshot is not None else capture_fn()
    if current is None:
        raise RuntimeError("Unable to capture screen for buy quantity adjustment")

    area_rect = _upgrade_area_rect()
    collapsed_point = _collapsed_center(area_rect)

    last_capture = current

    for attempt in range(max_attempts):
        current_quantity = _read_collapsed_quantity(current)
        if current_quantity == target:
            log(
                f"[UPGRADE_BUY] Quantity already '{target}', no adjustment needed",
                "INFO",
            )
            collapsed = collapse_buy_quantity(
                screenshot=current,
                capture_fn=capture_fn,
                sleep_fn=sleep_fn,
                max_attempts=2,
            )
            return collapsed if collapsed is not None else current

        log(
            f"[UPGRADE_BUY] Adjusting quantity to '{target}' (attempt {attempt + 1})",
            "MATCH",
        )

        if current_quantity is None:
            current = capture_fn()
            if current is None:
                raise RuntimeError(
                    "Buy quantity control was not identified before tapping"
                )
            last_capture = current
            continue
        collapsed_rect = get_buy_quantity_regions(current)["collapsed_rect"]
        if not _tap_point(
            collapsed_point,
            screenshot=current,
            target_region=collapsed_rect,
            description=f"buy_quantity:collapsed:{current_quantity}",
            verifier=lambda frame, expected=current_quantity: (
                _read_collapsed_quantity(frame) == expected
            ),
        ):
            raise RuntimeError("Buy quantity selector was not reverified")
        sleep_fn(0.35)

        expanded = capture_fn()
        if expanded is None or not is_buy_quantity_expanded(expanded):
            raise RuntimeError("Buy quantity options did not become visible")
        current = expanded
        last_capture = current

        centers = _expanded_centers(area_rect, target)
        for idx, target_point in enumerate(centers):
            if not _tap_point(
                target_point,
                screenshot=current,
                target_region=area_rect,
                description=f"buy_quantity:expanded:{target}",
                verifier=is_buy_quantity_expanded,
            ):
                raise RuntimeError(
                    f"Buy quantity option {target!r} was not reverified"
                )
            sleep_fn(0.4)

            for verify in range(3):
                current = capture_fn()
                if current is None:
                    current = capture_fn()
                    if current is None:
                        raise RuntimeError("Failed to capture screenshot after selecting quantity")
                last_capture = current

                final_quantity = _read_collapsed_quantity(current, expected=target)
                if final_quantity == target:
                    collapsed = collapse_buy_quantity(
                        screenshot=current,
                        capture_fn=capture_fn,
                        sleep_fn=sleep_fn,
                        max_attempts=2,
                    )
                    log(
                        f"[UPGRADE_BUY] Quantity set to '{target}' (attempt {attempt + 1})",
                        "INFO",
                    )
                    return collapsed if collapsed is not None else current

                if final_quantity not in (None, target):
                    _save_debug(
                        current,
                        f"mismatch_{target}_attempt{attempt+1}_point{idx}_verify{verify}.png",
                    )
                    break

                sleep_fn(0.15)

            else:
                # verification loop exhausted without explicit mismatch; continue retries
                continue

            if idx < len(centers) - 1:
                observed_quantity = _read_collapsed_quantity(current)
                if observed_quantity is None:
                    raise RuntimeError(
                        "Buy quantity control was not identified before retrying"
                    )
                collapsed_rect = get_buy_quantity_regions(current)["collapsed_rect"]
                if not _tap_point(
                    collapsed_point,
                    screenshot=current,
                    target_region=collapsed_rect,
                    description=f"buy_quantity:collapsed:{observed_quantity}",
                    verifier=lambda frame, expected=observed_quantity: (
                        _read_collapsed_quantity(frame) == expected
                    ),
                ):
                    raise RuntimeError(
                        "Buy quantity selector was not reverified before retrying"
                    )
                sleep_fn(0.35)
                expanded = capture_fn()
                if expanded is None or not is_buy_quantity_expanded(expanded):
                    raise RuntimeError(
                        "Buy quantity options did not reopen for retry"
                    )
                current = expanded
                last_capture = current
        else:
            _save_debug(last_capture, f"failure_{target}_attempt{attempt+1}.png")

    log(
        f"[UPGRADE_BUY] Unable to confirm quantity '{target}' after {max_attempts} attempts",
        "WARN",
    )
    collapsed = collapse_buy_quantity(
        screenshot=last_capture,
        capture_fn=capture_fn,
        sleep_fn=sleep_fn,
        max_attempts=1,
    )
    return collapsed if collapsed is not None else last_capture


__all__ = [
    "BuyQuantity",
    "collapse_buy_quantity",
    "detect_current_buy_quantity",
    "ensure_buy_quantity",
    "get_buy_quantity_regions",
    "is_buy_quantity_expanded",
    "read_buy_quantity_from_image",
]
