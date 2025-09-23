from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from typing import Literal

import cv2  # type: ignore
import numpy as np  # type: ignore

from core.adb_utils import adb_shell
from core.clickmap_access import resolve_dot_path
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.tap import safe_tap
from core.upgrade_box_detector import UpgradeBox, detect_visible_boxes
from core.upgrade_buy_quantity import (
    BuyQuantity,
    ensure_buy_quantity,
    detect_current_buy_quantity,
)
from utils.logger import log

# Default timing constants
_SCROLL_SETTLE_SEC = 0.45
_MAX_SCROLL_ATTEMPTS = 12

SwipeSpan = Literal["micro", "short", "medium", "long", "extended"]

_SWIPE_PROFILES: Dict[SwipeSpan, Dict[str, Tuple[float, float]]] = {
    "micro": {
        "towards_top": (0.46, 0.54),
        "towards_bottom": (0.54, 0.46),
    },
    "short": {
        "towards_top": (0.42, 0.58),
        "towards_bottom": (0.58, 0.42),
    },
    "medium": {
        "towards_top": (0.36, 0.64),
        "towards_bottom": (0.64, 0.36),
    },
    "long": {
        "towards_top": (0.32, 0.68),
        "towards_bottom": (0.68, 0.32),
    },
    "extended": {
        "towards_top": (0.28, 0.72),
        "towards_bottom": (0.72, 0.28),
    },
}


@dataclass
class UpgradeSearchResult:
    menu: str
    column: str
    index: int
    label: str
    box: UpgradeBox
    screenshot: np.ndarray
    purchase_attempted: bool = False
    purchase_sent: bool = False
    purchase_reason: Optional[str] = None
    buy_quantity: Optional[BuyQuantity] = None


_MENU_ALIAS = {
    "attack_menu": "attack",
    "attack": "attack",
    "defense_menu": "defense",
    "defense": "defense",
    "utility_menu": "utility",
    "utility": "utility",
    "uw_menu": "ultimate weapons",
    "ultimate weapons": "ultimate weapons",
    "ultimate": "ultimate weapons",
    "ultimate_weapon": "ultimate weapons",
}

_MENU_NAV = {
    "attack": {
        "nav": "navigation.goto_attack",
        "indicator": "indicators.menu_attack",
    },
    "defense": {
        "nav": "navigation.goto_defense",
        "indicator": "indicators.menu_defense",
    },
    "utility": {
        "nav": "navigation.goto_utility",
        "indicator": "indicators.menu_utility",
    },
    "ultimate weapons": {
        "nav": "navigation.goto_uw",
        "indicator": "indicators.menu_uw",
    },
}


def _normalize_menu(menu: Optional[str]) -> Optional[str]:
    if not menu:
        return None
    return _MENU_ALIAS.get(menu.lower(), menu.lower())


@lru_cache(maxsize=1)
def _load_manifest() -> Dict[str, Dict[str, List[str]]]:
    from pathlib import Path
    import json

    manifest_path = Path("config/upgrade_manifest.json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    upgrades = data.get("upgrades", {})
    normalized: Dict[str, Dict[str, List[str]]] = {}
    for menu, columns in upgrades.items():
        normalized[_normalize_menu(menu) or menu] = {
            col.lower(): list(entries)
            for col, entries in columns.items()
        }
    return normalized


def _manifest_entry(menu: Optional[str], label: str) -> Tuple[str, int, str]:
    manifest = _load_manifest()
    label_norm = label.strip().lower()
    candidate_menus: Sequence[str]

    if menu is not None:
        menu_key = _normalize_menu(menu)
        if not menu_key or menu_key not in manifest:
            raise ValueError(f"Unknown menu '{menu}'")
        candidate_menus = [menu_key]
    else:
        candidate_menus = manifest.keys()

    for menu_candidate in candidate_menus:
        columns = manifest[menu_candidate]
        for column, entries in columns.items():
            for idx, entry in enumerate(entries):
                if entry.lower() == label_norm:
                    return column, idx, menu_candidate

    if menu is None:
        raise ValueError(f"Label '{label}' not found in any manifest menu")
    raise ValueError(f"Label '{label}' not found in manifest menu '{menu}'")


def _perform_swipe(direction: str, span: SwipeSpan = "short") -> None:
    entry = resolve_dot_path("_shared_match_regions.upgrade_menu_area")
    if not entry or "match_region" not in entry:
        raise RuntimeError("upgrade_menu_area region missing from clickmap")
    region = entry["match_region"]
    x = int(region["x"])
    y = int(region["y"])
    w = int(region["w"])
    h = int(region["h"])

    profile = _SWIPE_PROFILES.get(span)
    if profile is None or direction not in profile:
        raise ValueError(f"Unsupported swipe configuration: direction={direction}, span={span}")

    start_ratio, end_ratio = profile[direction]

    if direction not in ("towards_top", "towards_bottom"):
        raise ValueError(f"Unknown scroll direction '{direction}'")

    start_x = x + w // 2
    start_y = y + int(h * start_ratio)
    end_y = y + int(h * end_ratio)

    adb_shell([
        "input",
        "swipe",
        str(start_x),
        str(start_y),
        str(start_x),
        str(end_y),
        "350",
    ])


def _ensure_menu(menu: str, *, capture_fn: Callable[[], Optional[np.ndarray]], max_attempts: int = 4) -> Optional[np.ndarray]:
    menu_key = _normalize_menu(menu)
    if menu_key is None or menu_key not in _MENU_NAV:
        raise ValueError(f"Unsupported menu '{menu}'")

    for attempt in range(max_attempts):
        screenshot = capture_fn()
        if screenshot is None:
            continue
        detection = detect_state_and_overlays(screenshot)
        current = _normalize_menu(detection.get("menu"))
        if current == menu_key:
            return screenshot

        nav_entry = _MENU_NAV[menu_key]["nav"]
        log(f"[UPGRADE_NAV] Attempting to switch to {menu_key} (attempt {attempt + 1})", "MATCH")
        safe_tap(nav_entry, require_visible=False, dispatch='now')
        time.sleep(0.5)

    # final capture after attempts
    screenshot = capture_fn()
    return screenshot


def _purchase_tap_coords(rect: Tuple[int, int, int, int]) -> Tuple[int, int]:
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        raise ValueError("Invalid upgrade box dimensions for tap")

    tap_x = x + min(w - 12, max(28, int(w * 0.78)))
    tap_y = y + min(h - 12, max(24, int(h * 0.68)))
    return tap_x, tap_y


def _verify_target_box(
    *,
    menu: str,
    column: str,
    target_text: str,
    reference_rect: Tuple[int, int, int, int],
    capture_fn: Callable[[], Optional[np.ndarray]],
    fallback_screenshot: np.ndarray,
) -> Tuple[UpgradeBox, np.ndarray]:
    """Reconfirm the target upgrade is still visible before tapping."""
    attempts: List[Optional[np.ndarray]] = [capture_fn(), fallback_screenshot]

    for shot in attempts:
        if shot is None:
            continue
        boxes_by_col = detect_visible_boxes(shot, menu=menu)
        for candidate in boxes_by_col.get(column, []):
            if (candidate.text or "").lower() != target_text:
                continue
            cx, cy, cw, ch = candidate.rect
            rx, ry, rw, rh = reference_rect
            if abs(cx - rx) <= 20 and abs(cy - ry) <= 20:
                return candidate, shot

    raise RuntimeError("Unable to confirm target upgrade before purchase tap")


def _validate_cost_crop(image: np.ndarray, tap_x: int, tap_y: int, rect: Tuple[int, int, int, int]) -> None:
    crop_w = max(60, min(rect[2] // 2, 160))
    crop_h = max(50, min(rect[3] // 2, 120))
    crop_x = max(0, tap_x - crop_w // 2)
    crop_y = max(0, tap_y - crop_h // 2)
    crop = image[crop_y : crop_y + crop_h, crop_x : crop_x + crop_w]

    if crop.size == 0:
        raise RuntimeError("Purchase tap validation failed: empty crop")

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    val = hsv[:, :, 2]
    if float(val.mean()) < 40:
        raise RuntimeError("Purchase tap validation failed: area too dark")


def _tap_purchase_area(
    box: UpgradeBox,
    *,
    menu: str,
    column: str,
    target_text: str,
    capture_fn: Callable[[], Optional[np.ndarray]],
    fallback_screenshot: np.ndarray,
) -> None:
    confirmed_box, image = _verify_target_box(
        menu=menu,
        column=column,
        target_text=target_text,
        reference_rect=box.rect,
        capture_fn=capture_fn,
        fallback_screenshot=fallback_screenshot,
    )

    tap_x, tap_y = _purchase_tap_coords(confirmed_box.rect)
    _validate_cost_crop(image, tap_x, tap_y, confirmed_box.rect)

    log(
        f"[UPGRADE_NAV] Tapping purchase area at ({tap_x},{tap_y}) for '{confirmed_box.text or 'unknown'}'",
        "ACTION",
    )

    adb_shell(["input", "tap", str(tap_x), str(tap_y)])


def _select_span(
    direction: str,
    visible_indices: List[int],
    target_index: int,
) -> SwipeSpan:
    if not visible_indices:
        return "long"

    if direction == "towards_top":
        distance = visible_indices[0] - target_index
    else:
        distance = target_index - visible_indices[-1]

    distance = max(distance, 0)

    if distance <= 1:
        return "micro"
    if distance <= 2:
        return "short"
    if distance <= 4:
        return "medium"
    return "long"


def apply_menu_buy_quantities(
    menu_quantities: Dict[str, BuyQuantity],
    *,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, BuyQuantity]:
    if not menu_quantities:
        return {}

    results: Dict[str, BuyQuantity] = {}

    for menu_name, quantity in menu_quantities.items():
        menu_key = _normalize_menu(menu_name)
        if menu_key is None or menu_key not in _MENU_NAV:
            raise ValueError(f"Unsupported menu '{menu_name}'")

        screenshot = _ensure_menu(menu_key, capture_fn=capture_fn)
        if screenshot is None:
            screenshot = capture_fn()
        if screenshot is None:
            raise RuntimeError(f"Unable to capture screenshot for menu '{menu_key}'")

        try:
            screenshot = ensure_buy_quantity(
                quantity,
                screenshot=screenshot,
                capture_fn=capture_fn,
                sleep_fn=sleep_fn,
            )
        except Exception as exc:
            log(
                f"[UPGRADE_NAV] Failed to set buy quantity '{quantity}' on menu '{menu_key}': {exc}",
                "WARN",
            )
            raise

        confirmed = detect_current_buy_quantity(screenshot=screenshot)
        if confirmed is None:
            log(
                f"[UPGRADE_NAV] Unable to confirm buy quantity visually; assuming '{quantity}'",
                "WARN",
            )
            confirmed = quantity
        results[menu_key] = confirmed
        sleep_fn(0.15)

    return results


def find_upgrade(
    menu: Optional[str],
    label: str,
    *,
    max_scrolls: int = _MAX_SCROLL_ATTEMPTS,
    capture_fn: Callable[[], Optional[np.ndarray]] = capture_adb_screenshot,
    sleep_fn: Callable[[float], None] = time.sleep,
    swipe_fn: Callable[[str, SwipeSpan], None] = _perform_swipe,
    ensure_menu: bool = True,
    attempt_purchase: bool = False,
    menu_buy_quantities: Optional[Dict[str, BuyQuantity]] = None,
    purchase_quantity: Optional[BuyQuantity] = None,
) -> Optional[UpgradeSearchResult]:
    """Locate ``label`` within ``menu`` and optionally purchase it.

    The search captures live screenshots, scrolls the upgrade pane, and returns
    metadata about the first match.  When ``purchase_quantity`` is supplied the
    function temporarily switches the selector to that quantity, restores the
    previous value before returning, and records the value used in the result.
    """
    column, target_index, resolved_menu = _manifest_entry(menu, label)

    menu_key = _normalize_menu(resolved_menu)
    if menu_key is None:
        raise ValueError(f"Unable to resolve menu for label '{label}'")

    manifest = _load_manifest()[menu_key]
    target_label_norm = label.strip().lower()
    screenshot = None
    if ensure_menu and menu_key is not None:
        screenshot = _ensure_menu(menu_key, capture_fn=capture_fn)
    if screenshot is None:
        screenshot = capture_fn()
    if screenshot is None:
        raise RuntimeError("Unable to capture screenshot for upgrade navigation")

    last_state: Optional[Tuple[int, ...]] = None
    last_direction: Optional[str] = None

    repeat_count = 0

    desired_quantity: Optional[BuyQuantity] = None
    restore_quantity: Optional[BuyQuantity] = None
    if purchase_quantity:
        desired_quantity = purchase_quantity
    elif menu_buy_quantities and menu_key in menu_buy_quantities:
        desired_quantity = menu_buy_quantities[menu_key]

    if desired_quantity:
        original_quantity = detect_current_buy_quantity(screenshot=screenshot)
        if purchase_quantity and original_quantity and original_quantity != desired_quantity:
            restore_quantity = original_quantity

        try:
            screenshot = ensure_buy_quantity(
                desired_quantity,
                screenshot=screenshot,
                capture_fn=capture_fn,
                sleep_fn=sleep_fn,
            )
        except Exception as exc:
            log(f"[UPGRADE_NAV] Failed to set buy quantity '{desired_quantity}': {exc}", "WARN")

    try:
        for attempt in range(max_scrolls + 1):
            boxes_by_col = detect_visible_boxes(screenshot, menu=menu_key)
            visible_boxes = boxes_by_col.get(column, [])
            visible_indices: List[int] = []
            match_box: Optional[UpgradeBox] = None

            for box in visible_boxes:
                if not box.text:
                    continue
                try:
                    idx = manifest[column].index(box.text)
                except ValueError:
                    continue
                visible_indices.append(idx)
                if box.text.lower() == target_label_norm:
                    match_box = box

            if match_box is not None:
                purchase_attempted = False
                purchase_sent = False
                purchase_reason: Optional[str] = None

                if attempt_purchase:
                    purchase_attempted = True
                    if menu_key == "ultimate weapons":
                        purchase_reason = "menu_has_toggles"
                    else:
                        status = match_box.affordability or "unknown"
                        if status == "affordable":
                            try:
                                _tap_purchase_area(
                                    match_box,
                                    menu=menu_key,
                                    column=column,
                                    target_text=target_label_norm,
                                    capture_fn=capture_fn,
                                    fallback_screenshot=screenshot,
                                )
                                purchase_sent = True
                                purchase_reason = "tapped_cost_panel"
                            except Exception as exc:
                                purchase_reason = f"tap_failed:{exc}"
                                log(
                                    f"[UPGRADE_NAV] Purchase tap failed for '{match_box.text}': {exc}",
                                    "WARN",
                                )
                        else:
                            purchase_reason = f"status={status}"
                            log(
                                f"[UPGRADE_NAV] Skipping purchase for '{match_box.text}' (status={status})",
                                "INFO",
                            )

                return UpgradeSearchResult(
                    menu=menu_key,
                    column=column,
                    index=target_index,
                    label=manifest[column][target_index],
                    box=match_box,
                    screenshot=screenshot,
                    purchase_attempted=purchase_attempted,
                    purchase_sent=purchase_sent,
                    purchase_reason=purchase_reason,
                    buy_quantity=desired_quantity,
                )

            if attempt >= max_scrolls:
                break

            if not visible_indices:
                direction = "towards_bottom"
            elif target_index < visible_indices[0]:
                if visible_indices[0] == 0:
                    break
                direction = "towards_top"
            elif target_index > visible_indices[-1]:
                if visible_indices[-1] == len(manifest[column]) - 1:
                    break
                direction = "towards_bottom"
            else:
                mid = visible_indices[0] + (visible_indices[-1] - visible_indices[0]) // 2
                direction = "towards_top" if target_index <= mid else "towards_bottom"

            state_key = tuple(visible_indices)
            if state_key and last_state == state_key and last_direction == direction:
                repeat_count += 1

                at_bottom = (
                    direction == "towards_bottom"
                    and visible_indices
                    and visible_indices[-1] == len(manifest[column]) - 1
                )
                at_top = (
                    direction == "towards_top"
                    and visible_indices
                    and visible_indices[0] == 0
                )

                if not (at_bottom or at_top) and repeat_count <= 3:
                    swipe_fn(direction, "extended")
                    sleep_fn(_SCROLL_SETTLE_SEC)
                    screenshot = capture_fn()
                    if screenshot is None:
                        screenshot = capture_fn()
                        if screenshot is None:
                            raise RuntimeError("Failed to capture screenshot after scrolling")
                    last_state = state_key
                    last_direction = direction
                    continue

                log("[UPGRADE_NAV] No progress after scrolling; aborting", "WARN")
                break
            else:
                repeat_count = 0

            # If we're trying to move down and already have the bottom-most entries, stop.
            if (
                direction == "towards_bottom"
                and visible_indices
                and visible_indices[-1] == len(manifest[column]) - 1
            ):
                break

            # Likewise for moving up at the very top.
            if direction == "towards_top" and visible_indices and visible_indices[0] == 0:
                break

            last_state = state_key if state_key else None
            last_direction = direction

            span = _select_span(direction, visible_indices, target_index)
            swipe_fn(direction, span)
            sleep_fn(_SCROLL_SETTLE_SEC)
            screenshot = capture_fn()
            if screenshot is None:
                screenshot = capture_fn()
                if screenshot is None:
                    raise RuntimeError("Failed to capture screenshot after scrolling")
    finally:
        if restore_quantity:
            try:
                ensure_buy_quantity(
                    restore_quantity,
                    capture_fn=capture_fn,
                    sleep_fn=sleep_fn,
                )
            except Exception as exc:
                log(
                    f"[UPGRADE_NAV] Failed to restore buy quantity '{restore_quantity}': {exc}",
                    "WARN",
                )

    return None


__all__ = ["UpgradeSearchResult", "find_upgrade", "apply_menu_buy_quantities"]
