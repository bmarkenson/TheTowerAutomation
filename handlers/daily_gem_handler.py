from enum import Enum
import os
import re
import time
from typing import Callable, Optional

import cv2

from core.input import safe_tap, tap_if_visible
from core.label_tapper import is_visible
from core.scrolling import scroll_to_edge, scroll_until_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log, log_action_intent, log_result
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
    INTERRUPTED = "interrupted"


class DailyGemCleanupResult(str, Enum):
    COMPLETE = "complete"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"
    FAILED = "failed"


ActionGuard = Optional[Callable[[], bool]]
RouteStateCallback = Optional[
    Callable[[str, bool, Optional[str]], None]
]


def _action_allowed(action_guard_fn: ActionGuard) -> bool:
    if action_guard_fn is None:
        return True
    try:
        return bool(action_guard_fn())
    except Exception as exc:
        log(f"[DAILY_GEM] Auxiliary authority check failed: {exc}", "ERROR")
        return False


def _input_guard_kwargs(action_guard_fn: ActionGuard) -> dict[str, object]:
    """Preserve legacy call shapes while enabling final-dispatch guards."""

    return (
        {"action_guard_fn": action_guard_fn}
        if action_guard_fn is not None
        else {}
    )


def _note_route(
    callback: RouteStateCallback,
    expected_state: str,
    cleanup_pending: bool,
    reason: Optional[str] = None,
) -> None:
    if callback is not None:
        callback(expected_state, cleanup_pending, reason)


def _guarded_store_return(
    session_id: str,
    source_state: str,
    action_guard_fn: ActionGuard,
) -> bool:
    if action_guard_fn is None:
        return _return_from_store(session_id, source_state)
    return _return_from_store(
        session_id,
        source_state,
        action_guard_fn=action_guard_fn,
    )


def _finish_daily_gem_check(
    result: DailyGemResult,
    *,
    session_id: str,
    reason: str,
) -> DailyGemResult:
    """Emit the terminal operator result for one Daily Gem check."""

    if result == DailyGemResult.CLAIMED:
        summary = "Daily Gem check complete — reward claimed"
    elif result == DailyGemResult.NOT_READY:
        summary = "Daily Gem check complete — reward not ready"
    elif result == DailyGemResult.INTERRUPTED:
        summary = f"Daily Gem check interrupted — {reason}"
    else:
        summary = f"Daily Gem check failed — {reason}"
    log_result(
        summary,
        detail=(
            f"[DAILY_GEM] result={result.value} session={session_id} "
            f"reason={reason}"
        ),
    )
    return result


def _finish_store_failure(
    step: str,
    session_id: str,
    source_state: str,
    *,
    reason: str,
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
) -> DailyGemResult:
    """Retain failure evidence and restore the verified source when possible."""

    _abort_handler(step, session_id)
    if not _action_allowed(action_guard_fn):
        _note_route(
            route_state_callback,
            "STORE",
            True,
            "auxiliary authority was lost before cleanup",
        )
        return _finish_daily_gem_check(
            DailyGemResult.INTERRUPTED,
            session_id=session_id,
            reason="auxiliary authority was lost; verified cleanup remains pending",
        )
    returned = _guarded_store_return(
        session_id,
        source_state,
        action_guard_fn,
    )
    log(
        f"[DAILY_GEM] Failure cleanup "
        f"{'returned to' if returned else 'could not return to'} "
        f"{source_state}",
        "DEBUG",
    )
    final_reason = reason
    if not returned:
        final_reason = (
            f"{reason}; failure cleanup could not return to {source_state}"
        )
    else:
        _note_route(route_state_callback, source_state, False)
    return _finish_daily_gem_check(
        DailyGemResult.FAILED,
        session_id=session_id,
        reason=final_reason,
    )


def _wait_for_label(
    label: str,
    *,
    timeout: float = 5.0,
    poll: float = 0.3,
    action_guard_fn: ActionGuard = None,
) -> bool:
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        if not _action_allowed(action_guard_fn):
            return False
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
            "DEBUG",
        )
        return DAILY_GEM_NOT_READY
    return None


def _open_store_for_current_screen(
    *,
    action_guard_fn: ActionGuard = None,
) -> str | None:
    """Open Store and return the verified source state on success."""

    screenshot = capture_adb_screenshot()
    if screenshot is None:
        log("[DAILY_GEM] Cannot identify source screen before opening Store", "WARN")
        return None
    detection = detect_state_and_overlays(screenshot)
    state = detection.get("state")
    if state == "HOME_SCREEN":
        log("[DAILY_GEM] Opening Store from the home-screen bottom navigation", "DEBUG")
        if not _action_allowed(action_guard_fn):
            return None
        opened = safe_tap(
            "navigation.goto_store_home",
            dispatch="now",
            **_input_guard_kwargs(action_guard_fn),
        )
        return state if opened else None
    if state == "RUNNING":
        log("[DAILY_GEM] Opening Store from the in-run gold cart", "DEBUG")
        if not _action_allowed(action_guard_fn):
            return None
        opened = tap_if_visible(
            "navigation.goto_store",
            screenshot=screenshot,
            retries=1,
            **_input_guard_kwargs(action_guard_fn),
        )
        return state if opened else None
    log(f"[DAILY_GEM] Refusing to open Store from state={state!r}", "WARN")
    return None


def handle_daily_gem(
    *,
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
) -> DailyGemResult:
    session_id = _make_session_id()
    log_action_intent(
        "Checking Daily Gem availability",
        reason="the daily Store reward may be claimable after rollover",
        detail=f"[DAILY_GEM] session={session_id}",
    )

    if not _action_allowed(action_guard_fn):
        return _finish_daily_gem_check(
            DailyGemResult.INTERRUPTED,
            session_id=session_id,
            reason="auxiliary authority was unavailable before the first input",
        )

    # Tap into Store only after the caller has claimed route ownership.
    source_state = _open_store_for_current_screen(
        action_guard_fn=action_guard_fn
    )
    if source_state is None:
        if not _action_allowed(action_guard_fn):
            return _finish_daily_gem_check(
                DailyGemResult.INTERRUPTED,
                session_id=session_id,
                reason="auxiliary authority was lost before Store entry",
            )
        return _finish_daily_gem_check(
            _abort_handler("Goto Store", session_id),
            session_id=session_id,
            reason="the Store could not be opened from a verified source screen",
        )
    _note_route(route_state_callback, "STORE", True)
    time.sleep(1.2)
    if not _wait_for_label(
        STORE_MENU_INDICATOR,
        timeout=4.0,
        action_guard_fn=action_guard_fn,
    ):
        return _finish_store_failure(
            "Store indicator not detected",
            session_id,
            source_state,
            reason="the Store indicator was not detected",
            action_guard_fn=action_guard_fn,
            route_state_callback=route_state_callback,
        )

    store_entry = capture_adb_screenshot()
    if store_entry is None or not is_visible(STORE_MENU_INDICATOR, screenshot=store_entry):
        return _finish_store_failure(
            "Store entry capture not verified",
            session_id,
            source_state,
            reason="the Store entry could not be verified",
            action_guard_fn=action_guard_fn,
            route_state_callback=route_state_callback,
        )

    if _daily_gem_unavailable(store_entry) == DAILY_GEM_NOT_READY:
        log("[DAILY_GEM] Cooldown visible at Store entry; skipping scroll", "DEBUG")
        if not _guarded_store_return(
            session_id,
            source_state,
            action_guard_fn,
        ):
            if not _action_allowed(action_guard_fn):
                _note_route(
                    route_state_callback,
                    "STORE",
                    True,
                    "auxiliary authority was lost before Store return",
                )
                return _finish_daily_gem_check(
                    DailyGemResult.INTERRUPTED,
                    session_id=session_id,
                    reason="auxiliary authority was lost; verified cleanup remains pending",
                )
            return _finish_daily_gem_check(
                DailyGemResult.FAILED,
                session_id=session_id,
                reason=f"the automation could not return to {source_state}",
            )
        _note_route(route_state_callback, source_state, False)
        return _finish_daily_gem_check(
            DailyGemResult.NOT_READY,
            session_id=session_id,
            reason="a cooldown was visible at Store entry",
        )
    if is_visible(DAILY_GEM_BUTTON, screenshot=store_entry):
        log("[DAILY_GEM] Claim is already visible at Store entry; skipping scroll", "DEBUG")
        claim_screenshot = store_entry
    else:
        # Establish a deterministic starting edge only when the current Store
        # position does not already expose the actionable card.
        scroll_guard = (
            {"action_guard_fn": action_guard_fn}
            if action_guard_fn is not None
            else {}
        )
        top = scroll_to_edge(
            "gesture_targets.goto_top:store",
            source_label=STORE_MENU_INDICATOR,
            screenshot=store_entry,
            progress_region=STORE_CONTENT_REGION,
            max_swipes=8,
            settle_s=1.0,
            **scroll_guard,
        )
        if not top.success or top.screenshot is None:
            return _finish_store_failure(
                f"Goto top of Store ({top.reason})",
                session_id,
                source_state,
                reason=f"the Store top could not be reached ({top.reason})",
                action_guard_fn=action_guard_fn,
                route_state_callback=route_state_callback,
            )

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
            **scroll_guard,
        )
        if claim.reason == DAILY_GEM_NOT_READY:
            log("[DAILY_GEM] No claim available; leaving Store", "DEBUG")
            if not _guarded_store_return(
                session_id,
                source_state,
                action_guard_fn,
            ):
                if not _action_allowed(action_guard_fn):
                    _note_route(
                        route_state_callback,
                        "STORE",
                        True,
                        "auxiliary authority was lost before Store return",
                    )
                    return _finish_daily_gem_check(
                        DailyGemResult.INTERRUPTED,
                        session_id=session_id,
                        reason="auxiliary authority was lost; verified cleanup remains pending",
                    )
                return _finish_daily_gem_check(
                    DailyGemResult.FAILED,
                    session_id=session_id,
                    reason=f"the automation could not return to {source_state}",
                )
            _note_route(route_state_callback, source_state, False)
            return _finish_daily_gem_check(
                DailyGemResult.NOT_READY,
                session_id=session_id,
                reason="a cooldown was found while searching the Store",
            )
        if not claim.success or claim.screenshot is None:
            return _finish_store_failure(
                f"Find Claim Daily Gems ({claim.reason})",
                session_id,
                source_state,
                reason=f"the Daily Gem card could not be found ({claim.reason})",
                action_guard_fn=action_guard_fn,
                route_state_callback=route_state_callback,
            )
        claim_screenshot = claim.screenshot

    save_image(claim_screenshot, f"{session_id}_claim_daily_gems")

    # Claim Daily Gem
    if not _action_allowed(action_guard_fn):
        _note_route(
            route_state_callback,
            "STORE",
            True,
            "auxiliary authority was lost before claim",
        )
        return _finish_daily_gem_check(
            DailyGemResult.INTERRUPTED,
            session_id=session_id,
            reason="auxiliary authority was lost; verified cleanup remains pending",
        )
    if not tap_if_visible(
        DAILY_GEM_BUTTON,
        screenshot=claim_screenshot,
        retries=1,
        **_input_guard_kwargs(action_guard_fn),
    ):
        return _finish_store_failure(
            "Claim_daily_gems",
            session_id,
            source_state,
            reason="the verified Daily Gem control could not be tapped",
            action_guard_fn=action_guard_fn,
            route_state_callback=route_state_callback,
        )
    _note_route(route_state_callback, "REWARD_REVEAL", True)
    time.sleep(1.2)

    # Skip
    if not _action_allowed(action_guard_fn):
        _note_route(
            route_state_callback,
            "REWARD_REVEAL",
            True,
            "auxiliary authority was lost before reward dismissal",
        )
        return _finish_daily_gem_check(
            DailyGemResult.INTERRUPTED,
            session_id=session_id,
            reason="auxiliary authority was lost; verified cleanup remains pending",
        )
    if not tap_if_visible(
        "buttons.skip_reward_reveal",
        retries=1,
        **_input_guard_kwargs(action_guard_fn),
    ):
        return _finish_store_failure(
            "Skip Claim_daily_gems",
            session_id,
            source_state,
            reason="the reward reveal could not be dismissed",
            action_guard_fn=action_guard_fn,
            route_state_callback=route_state_callback,
        )
    _note_route(route_state_callback, "STORE", True)
    time.sleep(1.2)

    if not _guarded_store_return(
        session_id,
        source_state,
        action_guard_fn,
    ):
        if not _action_allowed(action_guard_fn):
            _note_route(
                route_state_callback,
                "STORE",
                True,
                "auxiliary authority was lost before Store return",
            )
            return _finish_daily_gem_check(
                DailyGemResult.INTERRUPTED,
                session_id=session_id,
                reason="auxiliary authority was lost; verified cleanup remains pending",
            )
        return _finish_daily_gem_check(
            DailyGemResult.FAILED,
            session_id=session_id,
            reason=f"the automation could not return to {source_state}",
        )
    _note_route(route_state_callback, source_state, False)
    return _finish_daily_gem_check(
        DailyGemResult.CLAIMED,
        session_id=session_id,
        reason="the reward was claimed and the source screen was restored",
    )


def _make_session_id():
    return "Game" + time.strftime("%Y%m%d_%H%M")


def save_image(img, tag):
    if img is None:
        log(f"[CAPTURE] Skipping save for '{tag}' (no image)", "WARN")
        return
    path = os.path.join("screenshots", "matches", f"{tag}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)
    log(f"[CAPTURE] Saved screenshot: {path}", "DEBUG")


def _return_from_store(
    session_id: str,
    source_state: str,
    *,
    action_guard_fn: ActionGuard = None,
) -> bool:
    """Leave Store through the route appropriate to its verified source."""

    current = None
    if action_guard_fn is not None:
        current = capture_adb_screenshot()
        current_state = (
            detect_state_and_overlays(current).get("state")
            if current is not None
            else None
        )
        if current_state != "STORE":
            log(
                f"[DAILY_GEM] Refusing Store return from state={current_state!r}",
                "DEBUG",
            )
            return current_state == source_state
    if not _action_allowed(action_guard_fn):
        return False
    if source_state == "RUNNING":
        if current is None:
            returned = tap_if_visible(
                "buttons.return_to_game",
                retries=1,
                **_input_guard_kwargs(action_guard_fn),
            )
        else:
            returned = tap_if_visible(
                "buttons.return_to_game",
                screenshot=current,
                retries=1,
                **_input_guard_kwargs(action_guard_fn),
            )
        step = "Return to Game"
    elif source_state == "HOME_SCREEN":
        returned = safe_tap(
            "navigation.goto_home_store",
            dispatch="now",
            **_input_guard_kwargs(action_guard_fn),
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
    if not _action_allowed(action_guard_fn):
        return False
    return True


def resume_daily_gem_cleanup(
    source_state: str,
    *,
    action_guard_fn: Callable[[], bool],
    max_steps: int = 3,
) -> DailyGemCleanupResult:
    """Restore an interrupted Store route without generic recovery input."""

    expected_source = str(source_state or "").upper()
    if expected_source not in {"RUNNING", "HOME_SCREEN"}:
        return DailyGemCleanupResult.ABANDONED
    for _ in range(max(1, int(max_steps))):
        if not _action_allowed(action_guard_fn):
            return DailyGemCleanupResult.INTERRUPTED
        screenshot = capture_adb_screenshot()
        if screenshot is None:
            return DailyGemCleanupResult.FAILED
        state = detect_state_and_overlays(screenshot).get("state")
        if state == expected_source:
            return DailyGemCleanupResult.COMPLETE
        if is_visible("buttons.skip_reward_reveal", screenshot=screenshot):
            if not _action_allowed(action_guard_fn):
                return DailyGemCleanupResult.INTERRUPTED
            if not tap_if_visible(
                "buttons.skip_reward_reveal",
                screenshot=screenshot,
                retries=1,
                **_input_guard_kwargs(action_guard_fn),
            ):
                return DailyGemCleanupResult.FAILED
            time.sleep(0.6)
            continue
        if state != "STORE":
            # Game Over, Home/battle identity changes, and unexpected screens
            # are ownership boundaries. Never improvise cleanup navigation.
            return DailyGemCleanupResult.ABANDONED
        if not _return_from_store(
            "auxiliary-cleanup",
            expected_source,
            action_guard_fn=action_guard_fn,
        ):
            return (
                DailyGemCleanupResult.INTERRUPTED
                if not _action_allowed(action_guard_fn)
                else DailyGemCleanupResult.FAILED
            )
        return DailyGemCleanupResult.COMPLETE
    return DailyGemCleanupResult.FAILED


def _abort_handler(step, session_id):
    """
    Logs error, saves screenshot, and aborts handler.
    """
    log(f"[ABORT]  Daily Gem handler failed at: {step}", "ERROR")
    debug_img = capture_adb_screenshot()
    save_image(debug_img, f"{session_id}_ABORT_{step.replace(' ', '_')}")
    return DailyGemResult.FAILED
