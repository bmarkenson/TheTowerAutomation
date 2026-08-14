"""Fast, exclusive EHLS -> EALS initialization for new GC runs."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from core.adb_utils import input_swipe
from core.clickmap_access import resolve_dot_path
from core.input import TapVerification, safe_tap
from core.label_tapper import get_label_match
from core.screenrecord_frame_stream import ScreenrecordFrameStream
from core.ss_capture import capture_adb_raw_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_box_detector import (
    UpgradeBox,
    detect_visible_box_rects,
    evaluate_upgrade_box_gold_box,
)
from core.upgrade_buy_quantity import detect_current_buy_quantity, ensure_buy_quantity
from utils.logger import (
    log,
    log_action_intent,
    log_input,
    log_mission,
    log_result,
)
from utils.wave_detector import detect_wave_number_from_image


Frame = np.ndarray
Point = Tuple[int, int]
EHLS = "Enemy Health Level Skip"
EALS = "Enemy Attack Level Skip"
TARGET_COLUMN = {EHLS: "left", EALS: "right"}
TARGET_TEMPLATE = {
    EHLS: "upgrades.utility.left.EHLS",
    EALS: "upgrades.utility.right.EALS",
}
PURCHASE_X_FRACTION = 0.78
PURCHASE_Y_FRACTION = 0.77
EHLS_TAPS_PER_BURST = 4
EALS_TAPS_PER_BURST = 8


@dataclass(frozen=True)
class LevelSkipInitializationResult:
    success: bool
    ehls_maxed: bool
    eals_maxed: bool
    elapsed_s: float
    ehls_wave: Optional[int]
    eals_wave: Optional[int]
    taps_sent: int
    reason: str
    eals_first_tap_wave: Optional[int] = None
    eals_first_tap_elapsed_s: Optional[float] = None


def _default_tap(
    point: Point,
    *,
    label: str,
    verification: TapVerification,
) -> bool:
    return safe_tap(
        point,
        dispatch="now",
        log_label=label,
        verification=verification,
    )


def _default_scroll_to_bottom() -> bool:
    entry = resolve_dot_path("_shared_match_regions.upgrade_menu_area")
    region = entry.get("match_region") if isinstance(entry, dict) else None
    if not isinstance(region, dict):
        return False
    x = int(region["x"]) + int(region["w"]) // 2
    y = int(region["y"])
    h = int(region["h"])
    start_y = y + int(h * 0.82)
    end_y = y + int(h * 0.22)
    log_input(
        "Swipe requested: Utility upgrade menu toward the bottom",
        detail=(
            f"RUN_INIT_SCROLL_TO_BOTTOM: ({x},{start_y})→({x},{end_y}) "
            "in 220ms"
        ),
    )
    result = input_swipe(
        x,
        start_y,
        x,
        end_y,
        220,
        check=False,
    )
    return bool(result and result.returncode == 0)


def _is_verified_utility(frame: Frame) -> bool:
    detection = detect_state_and_overlays(frame)
    return detection.get("state") == "RUNNING" and detection.get("menu") == "UTILITY_MENU"


def _target_boxes(frame: Frame) -> Dict[str, UpgradeBox]:
    rects_by_column = detect_visible_box_rects(frame)
    found: Dict[str, UpgradeBox] = {}
    for target, column in TARGET_COLUMN.items():
        try:
            match = get_label_match(
                TARGET_TEMPLATE[target],
                screenshot=frame,
                return_meta=True,
            )
        except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
            log(f"[RUN_INIT] {target} template not visible: {exc}", "DEBUG")
            continue

        match_center_x = int(match["x"]) + int(match["w"]) // 2
        match_center_y = int(match["y"]) + int(match["h"]) // 2
        for rect in rects_by_column.get(column, []):
            x, y, w, h = rect
            if x <= match_center_x < x + w and y <= match_center_y < y + h:
                found[target] = UpgradeBox(
                    column=column,
                    rect=rect,
                    text=target,
                    match_score=float(match["match_score"]),
                )
                break
    return found


def _purchase_point(box: UpgradeBox) -> Point:
    x, y, w, h = box.rect
    if w <= 0 or h <= 0:
        raise ValueError("Invalid level-skip upgrade box")
    tap_x = x + min(w - 12, max(28, int(w * PURCHASE_X_FRACTION)))
    tap_y = y + min(h - 12, max(24, int(h * PURCHASE_Y_FRACTION)))
    return tap_x, tap_y


def _wave(frame: Frame) -> Optional[int]:
    try:
        value, _confidence = detect_wave_number_from_image(frame)
        return value
    except Exception:
        return None


def _tap_and_capture(
    *,
    point: Point,
    label: str,
    capture_fn: Callable[[], Optional[Frame]],
    tap_fn: Callable[..., bool],
    verification: TapVerification,
    max_taps: Optional[int] = None,
) -> Tuple[Optional[Frame], int, bool]:
    """Dispatch a bounded or continuous burst during a blocking capture."""

    if not tap_fn(point, label=label, verification=verification):
        return None, 0, False
    taps_sent = 1
    tap_limit = None if max_taps is None else max(1, int(max_taps))

    captured: Dict[str, Optional[Frame]] = {"frame": None}

    def capture() -> None:
        captured["frame"] = capture_fn()

    capture_thread = threading.Thread(target=capture, daemon=True)
    capture_thread.start()
    dispatch_ok = True
    while capture_thread.is_alive() and (
        tap_limit is None or taps_sent < tap_limit
    ):
        capture_thread.join(timeout=0.04)
        if not capture_thread.is_alive():
            break
        if not tap_fn(point, label=label, verification=verification):
            dispatch_ok = False
            break
        taps_sent += 1
    capture_thread.join()
    return captured["frame"], taps_sent, dispatch_ok


def _tap_burst(
    *,
    point: Point,
    label: str,
    tap_fn: Callable[..., bool],
    verification: TapVerification,
    max_taps: int,
) -> Tuple[int, bool]:
    """Dispatch one bounded burst from a reusable verified target."""

    taps_sent = 0
    for _attempt in range(max(1, int(max_taps))):
        if not tap_fn(point, label=label, verification=verification):
            return taps_sent, False
        taps_sent += 1
    return taps_sent, True


def _target_tap_verification(
    frame: Frame,
    target: str,
    box: UpgradeBox,
) -> TapVerification:
    return TapVerification(
        screenshot=frame,
        target_region=box.rect,
        description=f"level_skip:{target}",
        verifier=lambda candidate: (
            _is_verified_utility(candidate)
            and (fresh_box := _target_boxes(candidate).get(target)) is not None
            and not evaluate_upgrade_box_gold_box(
                candidate,
                fresh_box.rect,
            )[0]
        ),
        reuse_authority=True,
    )


def initialize_level_skips(
    *,
    screenshot: Optional[Frame] = None,
    capture_fn: Callable[[], Optional[Frame]] = capture_adb_raw_screenshot,
    tap_fn: Callable[..., bool] = _default_tap,
    scroll_fn: Callable[[], bool] = _default_scroll_to_bottom,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    timeout_s: float = 35.0,
    target_wave: int = 40,
    frame_stream_factory: Optional[Callable[[], ScreenrecordFrameStream]] = None,
    stream_ready_timeout_s: float = 8.0,
    ehls_taps_per_burst: int = EHLS_TAPS_PER_BURST,
    eals_taps_per_burst: int = EALS_TAPS_PER_BURST,
    mutation_observer_fn: Optional[Callable[[], None]] = None,
) -> LevelSkipInitializationResult:
    """Gold-box EHLS then EALS with minimum capture and navigation overhead.

    The guarded screenshot path is the production default. Android's H.264
    stream has never become live before this short startup race on the current
    emulator, and warming it contends with the screenshots and inputs that
    actually complete the upgrades. Tests may still inject a live stream to
    exercise that optional low-latency path.
    """

    log_action_intent(
        "Initializing level skips",
        reason=(
            f"maximize Enemy Health and Attack Level Skip before wave "
            f"{target_wave} so normal strategy actions can continue"
        ),
        detail=(
            f"[RUN_INIT] target_wave={target_wave} timeout_s={timeout_s} "
            f"ehls_taps_per_burst={ehls_taps_per_burst} "
            f"eals_taps_per_burst={eals_taps_per_burst}"
        ),
    )
    started = monotonic_fn()
    deadline = started + max(5.0, float(timeout_s))
    taps_sent = 0
    completion_waves: Dict[str, Optional[int]] = {EHLS: None, EALS: None}
    maxed = {EHLS: False, EALS: False}
    eals_first_tap_wave: Optional[int] = None
    eals_first_tap_elapsed_s: Optional[float] = None
    eals_first_tap_frame: Optional[Frame] = None
    completion_frames: Dict[str, Frame] = {}
    completion_elapsed: Dict[str, float] = {}
    frame_stream: Optional[ScreenrecordFrameStream] = None
    mutation_observed = False
    mutation_observer_failed = False

    def observe_before_mutation() -> bool:
        """Invalidate carried save authority before the first state change."""

        nonlocal mutation_observed, mutation_observer_failed
        if mutation_observed:
            return True
        if mutation_observer_fn is not None:
            try:
                mutation_observer_fn()
            except Exception as exc:
                mutation_observer_failed = True
                log(
                    "[RUN_INIT] Snapshot invalidation failed before the first "
                    f"level-skip mutation: {exc}",
                    "WARN",
                )
                return False
        mutation_observed = True
        return True

    def guarded_mutation_tap(
        point: Point,
        *,
        label: str,
        verification: TapVerification,
    ) -> bool:
        if not observe_before_mutation():
            return False
        return tap_fn(point, label=label, verification=verification)

    def finish(reason: str) -> LevelSkipInitializationResult:
        if mutation_observer_failed:
            reason = "snapshot_invalidation_failed"
        result = _result(
            started,
            monotonic_fn,
            maxed,
            completion_waves,
            taps_sent,
            reason,
            eals_first_tap_wave=eals_first_tap_wave,
            eals_first_tap_elapsed_s=eals_first_tap_elapsed_s,
        )
        if frame_stream is not None:
            frame_stream.stop()
        if result.success:
            summary = (
                "Level-skip initialization complete — EHLS and EALS maxed "
                f"in {result.elapsed_s:.2f}s"
            )
        else:
            summary = (
                "Level-skip initialization failed — "
                f"{result.reason.replace('_', ' ')}"
            )
        log_result(
            summary,
            detail=(
                f"[RUN_INIT] result={'completed' if result.success else result.reason} "
                f"ehls_maxed={result.ehls_maxed} eals_maxed={result.eals_maxed} "
                f"elapsed_s={result.elapsed_s:.3f} ehls_wave={result.ehls_wave} "
                f"eals_wave={result.eals_wave} taps_sent={result.taps_sent} "
                f"eals_first_tap_wave={result.eals_first_tap_wave} "
                f"eals_first_tap_elapsed_s={result.eals_first_tap_elapsed_s}"
            ),
        )
        return result

    frame = screenshot if screenshot is not None else capture_fn()
    if frame is None:
        return finish("capture_failed")

    detection = detect_state_and_overlays(frame)
    if detection.get("state") != "RUNNING":
        return finish("not_running")

    if frame_stream_factory is not None:
        try:
            frame_stream = frame_stream_factory()
            frame_stream.start()
        except Exception as exc:
            log(f"[RUN_INIT] Live frame stream failed to start: {exc}", "DEBUG")
            if frame_stream is not None:
                frame_stream.stop()
            frame_stream = None

    if detection.get("menu") != "UTILITY_MENU":
        if not safe_tap(
            "navigation.goto_utility",
            dispatch="now",
            screenshot=frame,
        ):
            return finish("utility_nav_failed")
        sleep_fn(0.12)
        frame = capture_fn()
        if frame is None or not _is_verified_utility(frame):
            return finish("utility_not_verified")
    elif not _is_verified_utility(frame):
        return finish("utility_not_verified")

    quantity = detect_current_buy_quantity(screenshot=frame)
    if quantity != "max":
        log_mission(
            f"[RUN_INIT] Utility buy quantity is {quantity!r}; setting Max before level skips",
            "DEBUG",
        )
        if not observe_before_mutation():
            return finish("snapshot_invalidation_failed")
        try:
            frame = ensure_buy_quantity(
                "max",
                screenshot=frame,
                capture_fn=capture_fn,
                sleep_fn=sleep_fn,
            )
        except Exception as exc:
            log(f"[RUN_INIT] Unable to set Max buy quantity: {exc}", "WARN")
            return finish("max_quantity_failed")
        if frame is None or not _is_verified_utility(frame):
            return finish("utility_lost_after_quantity")

    boxes = _target_boxes(frame)
    for _attempt in range(3):
        if EHLS in boxes and EALS in boxes:
            break
        if not _is_verified_utility(frame):
            return finish("utility_lost_before_scroll")
        if not scroll_fn():
            return finish("utility_scroll_failed")
        sleep_fn(0.12)
        frame = capture_fn()
        if frame is None:
            return finish("capture_after_scroll_failed")
        boxes = _target_boxes(frame)

    if EHLS not in boxes or EALS not in boxes:
        missing = [target for target in (EHLS, EALS) if target not in boxes]
        log(f"[RUN_INIT] Level-skip boxes not found together: {missing}", "WARN")
        return finish("level_skip_boxes_missing")

    target_boxes = boxes
    ehls_burst_limit = max(1, int(ehls_taps_per_burst))
    eals_burst_limit = max(1, int(eals_taps_per_burst))
    ehls_unobserved_taps = 0
    last_stream_sequence = -1
    last_stream_state_check = monotonic_fn()
    stream_ready_logged = False

    for target in (EHLS, EALS):
        while monotonic_fn() < deadline:
            using_live_stream = False
            if frame_stream is not None:
                if (
                    target == EHLS
                    and not frame_stream.is_live
                    and ehls_unobserved_taps >= ehls_burst_limit
                ):
                    log(
                        f"[RUN_INIT] EHLS warm-up burst reached "
                        f"{ehls_burst_limit} taps; acquiring guarded screenshot "
                        "feedback",
                        "DEBUG",
                    )
                    frame_stream.stop()
                    frame_stream = None
                    frame = capture_fn()
                    if frame is None:
                        return finish("capture_after_ehls_burst_failed")
                elif frame_stream.failed or (
                    not frame_stream.is_live
                    and frame_stream.age_s >= max(1.0, float(stream_ready_timeout_s))
                ):
                    log(
                        "[RUN_INIT] Live frame stream unavailable; using guarded "
                        "screenshot fallback",
                        "DEBUG",
                    )
                    frame_stream.stop()
                    frame_stream = None
                elif frame_stream.is_live:
                    if not stream_ready_logged:
                        log(
                            f"[RUN_INIT] Live frame stream ready after "
                            f"{frame_stream.age_s:.2f}s",
                            "DEBUG",
                        )
                        stream_ready_logged = True
                    sequence, latest = frame_stream.latest_frame()
                    if latest is not None:
                        using_live_stream = True
                        if sequence != last_stream_sequence:
                            frame = latest
                            last_stream_sequence = sequence

            if (
                frame_stream is None
                or monotonic_fn() - last_stream_state_check >= 0.75
            ):
                if not _is_verified_utility(frame):
                    return finish("utility_screen_lost")
                last_stream_state_check = monotonic_fn()

            current_box = _target_boxes(frame).get(target)
            if current_box is None:
                return finish(f"{target}_target_lost")
            is_gold_boxed, _metrics = evaluate_upgrade_box_gold_box(
                frame,
                current_box.rect,
            )
            if is_gold_boxed:
                maxed[target] = True
                completion_frames[target] = frame
                completion_elapsed[target] = monotonic_fn() - started

                # EHLS and EALS are deliberately kept in view together. The
                # guarded-screenshot fallback primes EALS during the EHLS
                # feedback cycle below. The optional live-stream path still
                # uses this handoff to avoid another state scan. Wave OCR is
                # deferred until both upgrades are complete.
                if target == EHLS:
                    eals_box = _target_boxes(frame).get(EALS)
                    if eals_box is None:
                        return finish("eals_handoff_target_lost")
                    eals_is_gold_boxed, _metrics = evaluate_upgrade_box_gold_box(
                        frame,
                        eals_box.rect,
                    )
                    if not eals_is_gold_boxed:
                        if eals_first_tap_elapsed_s is None:
                            eals_first_tap_elapsed_s = monotonic_fn() - started
                            eals_first_tap_frame = frame
                        if frame_stream is not None:
                            if not guarded_mutation_tap(
                                _purchase_point(eals_box),
                                label=f"level_skip:{EALS}",
                                verification=_target_tap_verification(
                                    frame,
                                    EALS,
                                    eals_box,
                                ),
                            ):
                                return finish("eals_handoff_tap_dispatch_failed")
                            taps_sent += 1
                            sleep_fn(0.04)
                        else:
                            frame, burst_taps, dispatch_ok = _tap_and_capture(
                                point=_purchase_point(eals_box),
                                label=f"level_skip:{EALS}",
                                capture_fn=capture_fn,
                                tap_fn=guarded_mutation_tap,
                                verification=_target_tap_verification(
                                    frame,
                                    EALS,
                                    eals_box,
                                ),
                                max_taps=eals_burst_limit,
                            )
                            taps_sent += burst_taps
                            if not dispatch_ok:
                                return finish("eals_handoff_tap_dispatch_failed")
                            if frame is None:
                                return finish("capture_after_eals_handoff_failed")
                break

            point = _purchase_point(current_box)
            verification = _target_tap_verification(
                frame,
                target,
                current_box,
            )
            if target == EALS and eals_first_tap_elapsed_s is None:
                eals_first_tap_elapsed_s = monotonic_fn() - started
                eals_first_tap_frame = frame
            if using_live_stream or frame_stream is not None:
                if not guarded_mutation_tap(
                    point,
                    label=f"level_skip:{target}",
                    verification=verification,
                ):
                    return finish("tap_dispatch_failed")
                taps_sent += 1
                if target == EHLS and not frame_stream.is_live:
                    ehls_unobserved_taps += 1
                sleep_fn(0.04)
            else:
                if target == EHLS:
                    burst_taps, dispatch_ok = _tap_burst(
                        point=point,
                        label=f"level_skip:{EHLS}",
                        tap_fn=guarded_mutation_tap,
                        verification=verification,
                        max_taps=ehls_burst_limit,
                    )
                    taps_sent += burst_taps
                    if not dispatch_ok:
                        return finish("tap_dispatch_failed")

                    eals_box = _target_boxes(frame).get(EALS)
                    if eals_box is None:
                        return finish("eals_early_target_lost")
                    eals_is_gold_boxed, _metrics = evaluate_upgrade_box_gold_box(
                        frame,
                        eals_box.rect,
                    )
                    if eals_is_gold_boxed:
                        frame = capture_fn()
                    else:
                        if eals_first_tap_elapsed_s is None:
                            eals_first_tap_elapsed_s = monotonic_fn() - started
                            eals_first_tap_frame = frame
                        frame, eals_taps, dispatch_ok = _tap_and_capture(
                            point=_purchase_point(eals_box),
                            label=f"level_skip:{EALS}",
                            capture_fn=capture_fn,
                            tap_fn=guarded_mutation_tap,
                            verification=_target_tap_verification(
                                frame,
                                EALS,
                                eals_box,
                            ),
                            max_taps=eals_burst_limit,
                        )
                        taps_sent += eals_taps
                        if not dispatch_ok:
                            return finish("eals_early_tap_dispatch_failed")
                else:
                    frame, burst_taps, dispatch_ok = _tap_and_capture(
                        point=point,
                        label=f"level_skip:{EALS}",
                        capture_fn=capture_fn,
                        tap_fn=guarded_mutation_tap,
                        verification=verification,
                        max_taps=eals_burst_limit,
                    )
                    taps_sent += burst_taps
                    if not dispatch_ok:
                        return finish("tap_dispatch_failed")
                if frame is None:
                    return finish("capture_after_tap_failed")
        else:
            return finish(f"{target}_timeout")

    # Wave OCR is intentionally outside the purchase-critical loop. The stored
    # frames still represent the exact observed completion/first-tap moments.
    wave_cache: Dict[int, Optional[int]] = {}

    def deferred_wave(captured: Optional[Frame]) -> Optional[int]:
        if captured is None:
            return None
        key = id(captured)
        if key not in wave_cache:
            wave_cache[key] = _wave(captured)
        return wave_cache[key]

    for target in (EHLS, EALS):
        completion_waves[target] = deferred_wave(completion_frames.get(target))
    eals_first_tap_wave = deferred_wave(eals_first_tap_frame)

    if eals_first_tap_elapsed_s is not None:
        log_mission(
            f"[RUN_INIT] EALS first tap dispatched immediately after EHLS "
            f"at {eals_first_tap_elapsed_s:.2f}s wave={eals_first_tap_wave}",
            "DEBUG",
        )
    for target in (EHLS, EALS):
        elapsed = completion_elapsed[target]
        log_mission(
            f"[RUN_INIT] {target} gold boxed in {elapsed:.2f}s "
            f"at wave={completion_waves[target]}",
            "DEBUG",
        )
        if completion_waves[target] is not None and completion_waves[target] > target_wave:
            log(
                f"[RUN_INIT] {target} missed wave-{target_wave} objective "
                f"(completed at wave {completion_waves[target]})",
                "WARN",
            )

    return finish("complete")


def _result(
    started: float,
    monotonic_fn: Callable[[], float],
    maxed: Dict[str, bool],
    waves: Dict[str, Optional[int]],
    taps_sent: int,
    reason: str,
    *,
    eals_first_tap_wave: Optional[int] = None,
    eals_first_tap_elapsed_s: Optional[float] = None,
) -> LevelSkipInitializationResult:
    return LevelSkipInitializationResult(
        success=bool(maxed[EHLS] and maxed[EALS]),
        ehls_maxed=bool(maxed[EHLS]),
        eals_maxed=bool(maxed[EALS]),
        elapsed_s=max(0.0, monotonic_fn() - started),
        ehls_wave=waves[EHLS],
        eals_wave=waves[EALS],
        taps_sent=taps_sent,
        reason=reason,
        eals_first_tap_wave=eals_first_tap_wave,
        eals_first_tap_elapsed_s=eals_first_tap_elapsed_s,
    )


__all__ = [
    "EALS",
    "EHLS",
    "LevelSkipInitializationResult",
    "initialize_level_skips",
]
