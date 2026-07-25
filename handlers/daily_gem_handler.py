from enum import Enum
import os
import re
import time

import cv2

from core.input import safe_tap, tap_if_visible
from core.label_tapper import is_visible
from core.scrolling import scroll_to_edge, scroll_until_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log, log_action_intent
from utils.ocr_utils import ocr_text_and_conf

STORE_MENU_INDICATOR = "indicators.menu_store"
DAILY_GEM_BUTTON = "buttons.claim_daily_gems"
STORE_CONTENT_REGION = (0, 170, 1080, 1580)
DAILY_GEM_CARD_COLUMN = (100, 180, 440, 1570)
DAILY_GEM_NOT_READY = "daily_gem_not_ready"


class DailyGemResult(str, Enum):
    CLAIMED = "claimed"
    NOT_READY = "not_ready"
    FAILED = "failed"


def _wait_for_label(label: str, *, timeout: float = 5.0, poll: float = 0.3) -> bool:
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        if is_visible(label):
            return True
        time.sleep(max(0.05, poll))
    return False


def _daily_gem_unavailable(screenshot) -> str | None:
    """Return a stop reason when the left daily-gem card is on cooldown."""

    if screenshot is None:
        return None
    x, y, w, h = DAILY_GEM_CARD_COLUMN
    crop = screenshot[y:y + h, x:x + w]
    if crop.size == 0:
        return None
    text, confidence = ocr_text_and_conf(crop, psm=6)
    normalized = " ".join(text.upper().split())
    free_match = re.search(r"\bFREE\b", normalized)
    # FREE is also the price shown on an active card. Only treat it as a
    # cooldown when a duration follows that card's FREE label; offer timers
    # elsewhere in the Store commonly appear before the card.
    after_free = normalized[free_match.end():] if free_match else ""
    has_countdown = bool(
        re.search(r"\b\d+\s*[DHMS]\b", after_free)
        or re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", after_free)
    )
    if free_match and has_countdown:
        log(
            f"[DAILY_GEM] Free-gem cooldown detected "
            f"(OCR confidence={confidence:.1f})",
            "INFO",
        )
        return DAILY_GEM_NOT_READY
    return None


def _open_store_for_current_screen() -> str | None:
    """Open Store and return the verified source state on success."""

    screenshot = capture_adb_screenshot()
    if screenshot is None:
        log("[DAILY_GEM] Cannot identify source screen before opening Store", "WARN")
        return False
    detection = detect_state_and_overlays(screenshot)
    state = detection.get("state")
    if state == "HOME_SCREEN":
        log("[DAILY_GEM] Opening Store from the home-screen bottom navigation", "DEBUG")
        opened = safe_tap(
            "navigation.goto_store_home",
            dispatch="now",
        )
        return state if opened else None
    if state == "RUNNING":
        log("[DAILY_GEM] Opening Store from the in-run gold cart", "DEBUG")
        opened = tap_if_visible("navigation.goto_store", screenshot=screenshot, retries=1)
        return state if opened else None
    log(f"[DAILY_GEM] Refusing to open Store from state={state!r}", "WARN")
    return None


def handle_daily_gem() -> DailyGemResult:
    session_id = _make_session_id()
    log_action_intent(
        "Checking Daily Gem availability",
        reason="the daily Store reward may be claimable after rollover",
    )
    log(f"Handling DAILY AD GEM — Session: {session_id}", "INFO")

    # Tap into Store
    source_state = _open_store_for_current_screen()
    if source_state is None:
        return _abort_handler("Goto Store", session_id)
    time.sleep(1.2)
    if not _wait_for_label(STORE_MENU_INDICATOR, timeout=4.0):
        return _abort_handler("Store indicator not detected", session_id)

    store_entry = capture_adb_screenshot()
    if store_entry is None or not is_visible(STORE_MENU_INDICATOR, screenshot=store_entry):
        return _abort_handler("Store entry capture not verified", session_id)

    if _daily_gem_unavailable(store_entry) == DAILY_GEM_NOT_READY:
        log("[DAILY_GEM] Cooldown visible at Store entry; skipping scroll", "DEBUG")
        if not _return_from_store(session_id, source_state):
            return DailyGemResult.FAILED
        return DailyGemResult.NOT_READY
    if is_visible(DAILY_GEM_BUTTON, screenshot=store_entry):
        log("[DAILY_GEM] Claim is already visible at Store entry; skipping scroll", "DEBUG")
        claim_screenshot = store_entry
    else:
        # Establish a deterministic starting edge only when the current Store
        # position does not already expose the actionable card.
        top = scroll_to_edge(
            "gesture_targets.goto_top:store",
            source_label=STORE_MENU_INDICATOR,
            screenshot=store_entry,
            progress_region=STORE_CONTENT_REGION,
            max_swipes=8,
            settle_s=1.0,
        )
        if not top.success or top.screenshot is None:
            return _abort_handler(f"Goto top of Store ({top.reason})", session_id)

        save_image(top.screenshot, f"{session_id}_store_top")

        # Scroll only while Store remains verified, stopping when the claim
        # button itself is visible rather than assuming one gesture reaches it.
        claim = scroll_until_visible(
            "gesture_targets.goto_claim_daily_gems:store",
            source_label=STORE_MENU_INDICATOR,
            target_label=DAILY_GEM_BUTTON,
            screenshot=top.screenshot,
            progress_region=STORE_CONTENT_REGION,
            max_swipes=10,
            settle_s=1.0,
            stop_fn=_daily_gem_unavailable,
        )
        if claim.reason == DAILY_GEM_NOT_READY:
            log("[DAILY_GEM] No claim available; leaving Store.", "INFO")
            if not _return_from_store(session_id, source_state):
                return DailyGemResult.FAILED
            return DailyGemResult.NOT_READY
        if not claim.success or claim.screenshot is None:
            return _abort_handler(f"Find Claim Daily Gems ({claim.reason})", session_id)
        claim_screenshot = claim.screenshot

    save_image(claim_screenshot, f"{session_id}_claim_daily_gems")

    # Claim Daily Gem
    if not tap_if_visible(DAILY_GEM_BUTTON, retries=1):
        return _abort_handler("Claim_daily_gems", session_id)
    time.sleep(1.2)

    # Skip
    if not tap_if_visible("buttons.skip_reward_reveal", retries=1):
        return _abort_handler("Skip Claim_daily_gems", session_id)
    time.sleep(1.2)

    if not _return_from_store(session_id, source_state):
        return DailyGemResult.FAILED
    return DailyGemResult.CLAIMED


def _make_session_id():
    return "Game" + time.strftime("%Y%m%d_%H%M")


def save_image(img, tag):
    if img is None:
        log(f"[CAPTURE] Skipping save for '{tag}' (no image)", "WARN")
        return
    path = os.path.join("screenshots", "matches", f"{tag}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)
    log(f"[CAPTURE] Saved screenshot: {path}", "INFO")


def _return_from_store(session_id: str, source_state: str) -> bool:
    """Leave Store through the route appropriate to its verified source."""

    if source_state == "RUNNING":
        returned = tap_if_visible("buttons.return_to_game", retries=1)
        step = "Return to Game"
    elif source_state == "HOME_SCREEN":
        returned = safe_tap(
            "navigation.goto_home_store",
            dispatch="now",
        )
        step = "Return Home"
    else:
        returned = False
        step = f"Return from Store ({source_state})"

    if not returned:
        _abort_handler(step, session_id)
        return False
    time.sleep(1.2)

    screenshot = capture_adb_screenshot()
    state = (
        detect_state_and_overlays(screenshot).get("state")
        if screenshot is not None
        else None
    )
    if state != source_state:
        log(
            f"[DAILY_GEM] Store return reached state={state!r}; "
            f"expected {source_state!r}",
            "WARN",
        )
        _abort_handler(f"Verify {step}", session_id)
        return False
    return True


def _abort_handler(step, session_id):
    """
    Logs error, saves screenshot, and aborts handler.
    """
    log(f"[ABORT]  Daily Gem handler failed at: {step}", "ERROR")
    debug_img = capture_adb_screenshot()
    save_image(debug_img, f"{session_id}_ABORT_{step.replace(' ', '_')}")
    return DailyGemResult.FAILED
