"""Run-scoped perk selection timeline from the top bar and Perks panel."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from datetime import datetime
import re
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import cv2
import numpy as np

from core.battle_perks import (
    ocr_latest_selected_perk,
    ocr_selected_perks,
)
from core.input import safe_tap, swipe_now, tap_if_visible
from core.label_tapper import is_visible
from core.run_perk_selector import canonical_perk_family
from core.scrolling import capture_scroll_to_edge, scroll_to_edge
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log, log_action_intent, log_result
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]
PERKS_INDICATOR = "indicators.perks_panel"
PERKS_CONTENT_REGION = (100, 414, 880, 1340)
PERK_PROGRESS_TEXT_REGION = (400, 25, 340, 71)
# The largest retained real lead is 191 waves. Keep margin for future profiles
# while rejecting separator artifacts such as ``705`` becoming ``7705``.
MAX_PERK_SCHEDULE_LEAD_WAVES = 250
INVALID_PROGRESS_WARNING_FRAMES = 3
PWR_FAMILY = "perk_wave_requirement"
PERKS_CLOSE_DESTINATIONS = {
    "RUNNING",
    "GAME_OVER",
    "TOURNAMENT_RESULTS",
}
PWR_MAX_PATTERN = re.compile(
    r"perk wave requirement.*-\s*75(?:\.0+)?\s*%",
    re.IGNORECASE,
)


def _recorded_selection_summary(selection_labels: Sequence[str]) -> str:
    """Describe recorded Perk changes without calling a singleton a batch."""

    if len(selection_labels) == 1:
        return f"Perk timeline selection recorded — {selection_labels[0]}"
    if selection_labels:
        return (
            "Perk timeline selections recorded — "
            + ", ".join(selection_labels)
        )
    return "Perk timeline observation recorded — no selection changes detected"


@dataclass(frozen=True)
class PerkProgress:
    """One OCR observation of the compact in-battle perk progress control."""

    status: str
    current_wave: Optional[int]
    next_wave: Optional[int]
    text_raw: str
    confidence: float

    @property
    def token(self) -> Optional[tuple[str, Optional[int]]]:
        if (
            self.status == "scheduled"
            and _scheduled_progress_is_plausible(
                self.current_wave,
                self.next_wave,
            )
        ):
            return ("scheduled", self.next_wave)
        if self.status == "complete":
            return ("complete", None)
        return None


@dataclass(frozen=True)
class PerkCaptureRequest:
    """A stable top-bar transition that requires a panel observation."""

    kind: str
    scheduled_wave: Optional[int]
    observed_wave: Optional[int]
    progress_after: PerkProgress
    snapshot_mode: str
    scheduled_waves: tuple[int, ...] = ()
    observed_wave_end: Optional[int] = None


@dataclass(frozen=True)
class _PanelCloseResult:
    """Outcome of one verified attempt to leave the owned Perks panel."""

    dispatched: bool
    closed: bool
    observed_state: str


class PerkTimelineTracker:
    """Turn stable progress changes and panel reads into atomic selection batches."""

    def __init__(self, *, confirmation_frames: int = 2) -> None:
        if confirmation_frames < 1:
            raise ValueError("confirmation_frames must be positive")
        self._confirmation_frames = int(confirmation_frames)
        self.reset(fresh_battle=False)

    def reset(self, *, fresh_battle: bool = True) -> None:
        """Begin a new run, or an unknown mid-run attachment."""

        self._fresh_battle = bool(fresh_battle)
        self._baseline_status = (
            "new_battle_empty" if fresh_battle else "not_observed"
        )
        self._snapshot_known = bool(fresh_battle)
        self._selected_by_family: dict[str, dict[str, Any]] = {}
        self._pwr_maxed = False
        self._batches: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._candidate_token: Optional[tuple[str, Optional[int]]] = None
        self._candidate_count = 0
        self._armed_next_wave: Optional[int] = None
        self._pending: Optional[PerkCaptureRequest] = None

    @property
    def pending(self) -> Optional[PerkCaptureRequest]:
        return self._pending

    @property
    def pwr_maxed(self) -> bool:
        return self._pwr_maxed

    @property
    def latest_batch(self) -> Optional[dict[str, Any]]:
        return copy.deepcopy(self._batches[-1]) if self._batches else None

    def observe(
        self,
        progress: PerkProgress,
        *,
        wave: Optional[int],
    ) -> Optional[PerkCaptureRequest]:
        """Observe progress and return a request after its token stabilizes."""

        token = progress.token
        if token is None:
            self._candidate_token = None
            self._candidate_count = 0
            return self._pending
        if token == self._candidate_token:
            self._candidate_count += 1
        else:
            self._candidate_token = token
            self._candidate_count = 1
        if self._candidate_count < self._confirmation_frames:
            return self._pending
        if self._pending is not None:
            self._refresh_pending(progress, wave=wave)
            return self._pending

        if not self._snapshot_known:
            self._pending = PerkCaptureRequest(
                kind="baseline",
                scheduled_wave=None,
                observed_wave=wave,
                progress_after=progress,
                snapshot_mode="full",
                observed_wave_end=wave,
            )
            return self._pending

        if self._armed_next_wave is None:
            if progress.status == "scheduled":
                self._armed_next_wave = progress.next_wave
            return None

        if (
            progress.status == "scheduled"
            and progress.current_wave is not None
            and self._armed_next_wave
            > progress.current_wave + MAX_PERK_SCHEDULE_LEAD_WAVES
        ):
            poisoned_wave = self._armed_next_wave
            self._armed_next_wave = progress.next_wave
            self._warn_once(
                "An implausible armed Perk wave "
                f"({poisoned_wave}) was discarded and the timeline "
                f"resynchronized at {progress.next_wave}"
            )
            return None

        transitioned = (
            progress.status == "complete"
            or (
                progress.current_wave is not None
                and progress.next_wave is not None
                and progress.current_wave >= self._armed_next_wave
                and progress.next_wave > self._armed_next_wave
            )
        )
        if not transitioned:
            return None

        self._pending = PerkCaptureRequest(
            kind="selection",
            scheduled_wave=self._armed_next_wave,
            observed_wave=wave,
            progress_after=progress,
            snapshot_mode="latest" if self._pwr_maxed else "full",
            scheduled_waves=(self._armed_next_wave,),
            observed_wave_end=wave,
        )
        return self._pending

    def record_full_snapshot(
        self,
        capture: Mapping[str, Any],
        *,
        observed_at: Optional[datetime] = None,
    ) -> bool:
        """Accept a complete selected-list capture for a baseline or batch."""

        request = self._pending
        if request is None or request.snapshot_mode != "full":
            return False
        quality = capture.get("quality")
        selected = capture.get("selected")
        if (
            not isinstance(quality, Mapping)
            or not quality.get("source_complete")
            or not isinstance(selected, Sequence)
            or isinstance(selected, (str, bytes))
        ):
            self._warn_once(
                "A complete Perks panel snapshot could not be read; "
                "the pending timeline event will be retried"
            )
            return False

        after = _index_selected_perks(selected)
        if request.kind == "baseline":
            self._selected_by_family = after
            self._snapshot_known = True
            self._baseline_status = "observed_mid_battle"
            self._pwr_maxed = _snapshot_has_max_pwr(after)
            self._advance_after_capture(request)
            return True

        changes = _diff_selected_perks(self._selected_by_family, after)
        if not changes:
            self._warn_once(
                "The Perks panel did not yet show the scheduled selection; "
                "the pending timeline event will be retried"
            )
            return False
        if (
            self._pwr_maxed
            and len(request.scheduled_waves) > 1
            and len(changes) == len(request.scheduled_waves)
        ):
            self._append_ordered_post_pwr_batches(
                request,
                changes,
                observed_at=observed_at,
            )
        else:
            self._append_batch(
                request,
                changes,
                observed_at=observed_at,
            )
            if len(request.scheduled_waves) > 1:
                detail = (
                    "because the number of distinct changes did not match "
                    "the number of scheduled boundaries"
                    if self._pwr_maxed
                    else "because the interval includes pre-max PWR cascades"
                )
                self._warn_once(
                    "Perk panel capture was deferred across multiple selection "
                    "boundaries; changes are recorded as an interval aggregate "
                    f"without per-wave attribution {detail}"
                )
        self._selected_by_family = after
        if _snapshot_has_max_pwr(after):
            self._pwr_maxed = True
        self._advance_after_capture(request)
        return True

    def record_latest(
        self,
        perk: Mapping[str, Any],
        *,
        observed_at: Optional[datetime] = None,
    ) -> bool:
        """Accept the newest top row once PWR was already maxed."""

        request = self._pending
        if (
            request is None
            or request.snapshot_mode != "latest"
            or not self._pwr_maxed
        ):
            return False
        normalized = _timeline_entry(perk)
        if normalized is None:
            self._warn_once(
                "The latest selected Perk row could not be read; "
                "the pending timeline event will be retried"
            )
            return False
        family = normalized["family"]
        before = self._selected_by_family.get(family)
        if before is not None and _same_display(before, normalized):
            self._warn_once(
                "The latest selected Perk row had not changed yet; "
                "the pending timeline event will be retried"
            )
            return False
        change = _selection_change(before, normalized)
        self._append_batch(request, [change], observed_at=observed_at)
        self._selected_by_family[family] = normalized
        self._advance_after_capture(request)
        return True

    def snapshot(self) -> dict[str, Any]:
        """Return a detached battle-record payload."""

        return {
            "schema_version": 2,
            "source": "top_bar_schedule_and_selected_perks_panel",
            "batch_order_semantics": "selection_wave_order",
            "within_batch_order_semantics": "simultaneous_unordered",
            "deferred_post_pwr_order_semantics": (
                "latest_selected_first_reconstructed_when_one_distinct_"
                "change_matches_each_scheduled_boundary"
            ),
            "baseline_status": self._baseline_status,
            "pwr_maxed_observed": self._pwr_maxed,
            "batches": copy.deepcopy(self._batches),
            "warnings": list(self._warnings),
            "pending_scheduled_wave": (
                self._pending.scheduled_wave if self._pending else None
            ),
            "pending_scheduled_waves": (
                list(self._pending.scheduled_waves)
                if self._pending is not None
                else []
            ),
        }

    def _append_batch(
        self,
        request: PerkCaptureRequest,
        changes: Sequence[Mapping[str, Any]],
        *,
        observed_at: Optional[datetime],
        selection_model: Optional[str] = None,
    ) -> None:
        when = observed_at or datetime.now().astimezone()
        interval_aggregate = len(request.scheduled_waves) > 1
        self._batches.append(
            {
                "sequence": len(self._batches) + 1,
                "scheduled_wave": request.scheduled_wave,
                "scheduled_waves": list(request.scheduled_waves),
                "observed_wave": request.observed_wave,
                "observed_wave_end": request.observed_wave_end,
                "observed_at": when.isoformat(),
                "selection_model": (
                    selection_model
                    or (
                        "interval_aggregate"
                        if interval_aggregate
                        else (
                            "singleton_after_pwr_max"
                            if request.snapshot_mode == "latest"
                            else "simultaneous_batch"
                        )
                    )
                ),
                "snapshot_mode": request.snapshot_mode,
                "selections": [copy.deepcopy(dict(change)) for change in changes],
            }
        )

    def _append_ordered_post_pwr_batches(
        self,
        request: PerkCaptureRequest,
        changes_newest_first: Sequence[Mapping[str, Any]],
        *,
        observed_at: Optional[datetime],
    ) -> None:
        """Reconstruct deferred post-PWR singletons from newest-first rows."""

        source_waves = list(request.scheduled_waves)
        for scheduled_wave, change in zip(
            source_waves,
            reversed(changes_newest_first),
        ):
            reconstructed = replace(
                request,
                scheduled_wave=scheduled_wave,
                scheduled_waves=(scheduled_wave,),
            )
            self._append_batch(
                reconstructed,
                [change],
                observed_at=observed_at,
                selection_model="singleton_after_pwr_max_reconstructed",
            )
            self._batches[-1]["reconstructed_from_scheduled_waves"] = (
                source_waves
            )
            self._batches[-1]["source_order_semantics"] = (
                "latest_selected_first"
            )

    def _advance_after_capture(self, request: PerkCaptureRequest) -> None:
        if request.progress_after.status == "scheduled":
            self._armed_next_wave = request.progress_after.next_wave
        else:
            self._armed_next_wave = None
        self._pending = None

    def _refresh_pending(
        self,
        progress: PerkProgress,
        *,
        wave: Optional[int],
    ) -> None:
        """Advance deferred capture authority to the newest stable token."""

        request = self._pending
        if request is None or progress.token == request.progress_after.token:
            return
        previous_next = request.progress_after.next_wave
        advanced = (
            progress.status == "complete"
            or (
                progress.current_wave is not None
                and progress.next_wave is not None
                and previous_next is not None
                and progress.current_wave >= previous_next
                and progress.next_wave > previous_next
            )
        )
        if not advanced:
            return

        if request.kind == "baseline":
            self._pending = replace(
                request,
                progress_after=progress,
                observed_wave_end=wave,
            )
            return

        scheduled_waves = list(request.scheduled_waves)
        if previous_next is not None and previous_next not in scheduled_waves:
            scheduled_waves.append(previous_next)
        aggregate = len(scheduled_waves) > 1
        self._pending = replace(
            request,
            progress_after=progress,
            snapshot_mode="full" if aggregate else request.snapshot_mode,
            scheduled_waves=tuple(scheduled_waves),
            observed_wave_end=wave,
        )
    def _warn_once(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)


class PerkTimelineObserver:
    """Own the bounded Perks-panel route needed by a timeline tracker."""

    def __init__(self, tracker: Optional[PerkTimelineTracker] = None) -> None:
        self.tracker = tracker or PerkTimelineTracker()
        self._route_open = False
        self._invalid_progress_count = 0
        self._invalid_progress_warned = False

    def reset(self, *, fresh_battle: bool = True) -> None:
        self.tracker.reset(fresh_battle=fresh_battle)
        self._route_open = False
        self._invalid_progress_count = 0
        self._invalid_progress_warned = False

    def snapshot(self) -> dict[str, Any]:
        return self.tracker.snapshot()

    def handle(
        self,
        screenshot: Frame,
        detection: Mapping[str, Any],
        *,
        wave: Optional[int],
        actions_allowed: bool,
        action_guard_fn: Callable[[], bool],
        progress_fn: Callable[[Optional[Frame]], PerkProgress] = (
            lambda frame: measure_perk_progress(frame)
        ),
        capture_fn: Capture = capture_adb_screenshot,
        detector: Detector = detect_state_and_overlays,
        safe_tap_fn: Callable[..., bool] = safe_tap,
        tap_visible_fn: Callable[..., bool] = tap_if_visible,
        visible_fn: Callable[..., bool] = is_visible,
        swipe_fn: Callable[[str], bool] = swipe_now,
        full_ocr_fn: Callable[..., Mapping[str, Any]] = ocr_selected_perks,
        latest_ocr_fn: Callable[[Frame], Optional[Mapping[str, Any]]] = (
            ocr_latest_selected_perk
        ),
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> bool:
        """Observe one frame and run a pending panel route when allowed.

        Returns ``True`` after any navigation so the caller discards its stale
        pre-route screenshot.
        """

        state = str(detection.get("state") or "UNKNOWN")
        if state == "RUNNING":
            progress = progress_fn(screenshot)
            self._record_progress_health(progress)
            self.tracker.observe(progress, wave=wave)
        request = self.tracker.pending
        if request is None:
            if not self._route_open:
                return False
            if state != "PERKS":
                self._route_open = False
                return False
            if not actions_allowed:
                return False
            log_action_intent(
                "Restoring the battle view",
                reason="close the perk timeline's completed panel route",
                detail="[PERK_TIMELINE] route_restore=pending",
            )
            if not action_guard_fn():
                log_result(
                    "Perk timeline panel restore paused",
                    detail="[PERK_TIMELINE] route_restore=interrupted",
                )
                return False
            close_result = _close_perks_panel(
                screenshot=screenshot,
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )
            if not close_result.closed:
                log_result(
                    "Perk timeline panel restore failed",
                    detail=(
                        "[PERK_TIMELINE] route_restore=failed "
                        f"dispatched={close_result.dispatched} "
                        f"observed_state={close_result.observed_state}"
                    ),
                )
                return close_result.dispatched
            self._route_open = False
            log_result(
                "Battle view restored after perk timeline capture",
                detail=(
                    "[PERK_TIMELINE] route_restore=complete "
                    f"observed_state={close_result.observed_state}"
                ),
            )
            return True
        if not actions_allowed:
            return False
        if state not in {"RUNNING", "PERKS"}:
            return False
        if state == "PERKS" and not self._route_open:
            return False

        reason = (
            "establish a mid-battle selected-Perks baseline"
            if request.kind == "baseline"
            else (
                "record perk selections deferred across scheduled waves "
                + ", ".join(str(value) for value in request.scheduled_waves)
                if len(request.scheduled_waves) > 1
                else (
                    f"record the perk selection scheduled for wave "
                    f"{request.scheduled_wave}"
                )
            )
        )
        log_action_intent(
            "Recording the perk selection timeline",
            reason=reason,
            detail=(
                f"[PERK_TIMELINE] mode={request.snapshot_mode} "
                f"observed_wave={request.observed_wave}"
            ),
        )

        navigated = state == "PERKS"
        current = screenshot
        try:
            if state == "RUNNING":
                if not action_guard_fn():
                    raise _RouteInterrupted("control no longer allows inputs")
                if not safe_tap_fn(
                    "navigation.open_perks",
                    dispatch="now",
                    log_label="perk_timeline:open",
                    screenshot=screenshot,
                ):
                    raise _RouteFailed("verified Perks control was unavailable")
                self._route_open = True
                navigated = True
                current = _wait_for_perks(
                    capture_fn=capture_fn,
                    detector=detector,
                    sleep_fn=sleep_fn,
                )

            capture_ok, completed_request = self._capture_pending(
                current,
                action_guard_fn=action_guard_fn,
                progress_fn=progress_fn,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                full_ocr_fn=full_ocr_fn,
                latest_ocr_fn=latest_ocr_fn,
                sleep_fn=sleep_fn,
            )
            refreshed = capture_fn()
            if refreshed is not None:
                current = refreshed
            if not action_guard_fn():
                raise _RouteInterrupted("control changed before panel restore")
            close_result = _close_perks_panel(
                screenshot=current,
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )
            if not close_result.dispatched:
                raise _RouteFailed("verified Perks close control was unavailable")
            if not close_result.closed:
                raise _RouteFailed(
                    "Perks panel remained visible after the verified close input"
                )
            self._route_open = False
            latest_batch = self.tracker.latest_batch if capture_ok else None
            selections = (
                latest_batch.get("selections", [])
                if isinstance(latest_batch, Mapping)
                else []
            )
            selection_labels = [
                str(selection.get("display_text") or "unknown")
                for selection in selections
                if isinstance(selection, Mapping)
            ]
            if capture_ok and completed_request.kind == "baseline":
                summary = "Perk timeline baseline recorded"
            elif capture_ok:
                summary = _recorded_selection_summary(selection_labels)
            else:
                summary = "Perk timeline observation will be retried"
            log_result(
                summary,
                detail=(
                    f"[PERK_TIMELINE] result="
                    f"{'recorded' if capture_ok else 'retry'} "
                    f"scheduled_wave={completed_request.scheduled_wave} "
                    f"scheduled_waves="
                    f"{list(completed_request.scheduled_waves)} "
                    f"selection_count={len(selection_labels)} "
                    f"close_state={close_result.observed_state}"
                ),
            )
            return True
        except _RouteInterrupted as exc:
            log_result(
                "Perk timeline observation paused",
                detail=f"[PERK_TIMELINE] result=interrupted reason={exc}",
            )
            return navigated
        except _RouteFailed as exc:
            log_result(
                "Perk timeline observation failed",
                detail=f"[PERK_TIMELINE] result=failed reason={exc}",
            )
            return navigated
        except Exception as exc:
            log(
                f"[PERK_TIMELINE] Unexpected panel capture failure: {exc}",
                "ERROR",
            )
            log_result(
                "Perk timeline observation failed",
                detail=f"[PERK_TIMELINE] result=failed error={exc}",
            )
            return navigated

    def _record_progress_health(self, progress: PerkProgress) -> None:
        """Report persistent invalid OCR while continuing read-only retries."""

        if progress.status != "invalid_schedule":
            if self._invalid_progress_warned:
                log(
                    "[PERK_TIMELINE] Top-bar schedule recovered after "
                    f"{self._invalid_progress_count} invalid observation(s)",
                    "INFO",
                )
            self._invalid_progress_count = 0
            self._invalid_progress_warned = False
            return

        self._invalid_progress_count += 1
        log(
            "[PERK_TIMELINE] Ignoring implausible top-bar schedule "
            f"current={progress.current_wave} next={progress.next_wave} "
            f"raw={progress.text_raw!r} "
            f"attempt={self._invalid_progress_count}",
            "DEBUG",
        )
        if (
            self._invalid_progress_count >= INVALID_PROGRESS_WARNING_FRAMES
            and not self._invalid_progress_warned
        ):
            self._invalid_progress_warned = True
            log(
                "[PERK_TIMELINE] Top-bar schedule remained implausible for "
                f"{self._invalid_progress_count} observations; timeline "
                "capture is retrying without device input",
                "WARN",
            )

    def _capture_pending(
        self,
        screenshot: Frame,
        *,
        action_guard_fn: Callable[[], bool],
        progress_fn: Callable[[Optional[Frame]], PerkProgress],
        capture_fn: Capture,
        visible_fn: Callable[..., bool],
        swipe_fn: Callable[[str], bool],
        full_ocr_fn: Callable[..., Mapping[str, Any]],
        latest_ocr_fn: Callable[[Frame], Optional[Mapping[str, Any]]],
        sleep_fn: Callable[[float], None],
    ) -> tuple[bool, PerkCaptureRequest]:
        def guarded_swipe_fn(key: str) -> bool:
            if not action_guard_fn():
                raise _RouteInterrupted("control changed before panel swipe")
            return swipe_fn(key)

        top = scroll_to_edge(
            "gesture_targets.goto_top:perks",
            source_label=PERKS_INDICATOR,
            screenshot=screenshot,
            progress_region=PERKS_CONTENT_REGION,
            max_swipes=8,
            settle_s=0.8,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=guarded_swipe_fn,
            sleep_fn=sleep_fn,
        )
        if top.screenshot is None or not top.success:
            raise _RouteFailed(f"could not reach Perks top ({top.reason})")

        top_frame = self._refresh_progress_from_panel(
            top.screenshot,
            progress_fn=progress_fn,
            capture_fn=capture_fn,
            sleep_fn=sleep_fn,
        )
        request = self.tracker.pending
        if request is None:
            raise _RouteFailed("Perk timeline request disappeared during capture")

        if request.snapshot_mode == "latest":
            latest = latest_ocr_fn(top_frame)
            return bool(self.tracker.record_latest(latest or {})), request

        for capture_attempt in range(2):
            request_before = self.tracker.pending
            if request_before is None:
                raise _RouteFailed(
                    "Perk timeline request disappeared during full capture"
                )
            capture = capture_scroll_to_edge(
                "gesture_targets.goto_next:perks",
                source_label=PERKS_INDICATOR,
                screenshot=top_frame,
                progress_region=PERKS_CONTENT_REGION,
                max_swipes=20,
                settle_s=0.8,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=guarded_swipe_fn,
                sleep_fn=sleep_fn,
            )
            progress_frame = (
                capture.screenshots[-1]
                if capture.screenshots
                else top_frame
            )
            settled_frame = self._refresh_progress_from_panel(
                progress_frame,
                progress_fn=progress_fn,
                capture_fn=capture_fn,
                sleep_fn=sleep_fn,
            )
            request_after = self.tracker.pending
            if request_after is None:
                raise _RouteFailed(
                    "Perk timeline request disappeared after full capture"
                )
            if (
                request_after.progress_after.token
                != request_before.progress_after.token
            ):
                if capture_attempt == 1:
                    return False, request_after
                top = scroll_to_edge(
                    "gesture_targets.goto_top:perks",
                    source_label=PERKS_INDICATOR,
                    screenshot=settled_frame,
                    progress_region=PERKS_CONTENT_REGION,
                    max_swipes=8,
                    settle_s=0.8,
                    capture_fn=capture_fn,
                    visible_fn=visible_fn,
                    swipe_fn=guarded_swipe_fn,
                    sleep_fn=sleep_fn,
                )
                if top.screenshot is None or not top.success:
                    raise _RouteFailed(
                        "could not repeat Perks capture after the schedule "
                        f"advanced ({top.reason})"
                    )
                top_frame = self._refresh_progress_from_panel(
                    top.screenshot,
                    progress_fn=progress_fn,
                    capture_fn=capture_fn,
                    sleep_fn=sleep_fn,
                )
                continue

            full = full_ocr_fn(
                list(capture.screenshots),
                source_complete=capture.success,
                source_reason=capture.reason,
            )
            return (
                bool(self.tracker.record_full_snapshot(full)),
                request_after,
            )

        raise _RouteFailed("Perk timeline full capture retry was exhausted")

    def _refresh_progress_from_panel(
        self,
        screenshot: Frame,
        *,
        progress_fn: Callable[[Optional[Frame]], PerkProgress],
        capture_fn: Capture,
        sleep_fn: Callable[[float], None],
    ) -> Frame:
        """Refresh pending schedule evidence while the modal route owns input."""

        current = screenshot
        progress = progress_fn(current)
        self.tracker.observe(
            progress,
            wave=progress.current_wave,
        )
        for _ in range(2):
            sleep_fn(0.25)
            refreshed = capture_fn()
            if refreshed is None:
                continue
            current = refreshed
            progress = progress_fn(current)
            self.tracker.observe(
                progress,
                wave=progress.current_wave,
            )
        return current


def measure_perk_progress(
    screenshot: Optional[Frame],
    *,
    text_fn: Callable[[Frame], tuple[str, float]] = (
        lambda crop: ocr_text_and_conf(crop, psm=7)
    ),
) -> PerkProgress:
    """Read ``current wave / next perk wave`` or the terminal View Perks label."""

    if screenshot is None or screenshot.ndim < 2:
        return PerkProgress("unreadable", None, None, "", -1.0)
    x, y, width, height = PERK_PROGRESS_TEXT_REGION
    crop = screenshot[y : y + height, x : x + width]
    if crop.size == 0:
        return PerkProgress("unreadable", None, None, "", -1.0)
    enlarged = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    raw_text, confidence = text_fn(enlarged)
    normalized = " ".join(str(raw_text or "").split())
    upper = normalized.upper()
    if "VIEW" in upper and "PERK" in upper:
        return PerkProgress(
            "complete",
            None,
            None,
            normalized,
            float(confidence),
        )
    if "NEW" in upper and "PERK" in upper:
        return PerkProgress(
            "selection_pending",
            None,
            None,
            normalized,
            float(confidence),
        )
    numbers = [
        int(value)
        for value in re.findall(r"(?<!\d)(\d{1,6})(?!\d)", normalized)
    ]
    if len(numbers) >= 2:
        current_wave = numbers[0]
        next_wave = numbers[-1]
        status = (
            "scheduled"
            if _scheduled_progress_is_plausible(current_wave, next_wave)
            else "invalid_schedule"
        )
        return PerkProgress(
            status,
            current_wave,
            next_wave,
            normalized,
            float(confidence),
        )
    return PerkProgress(
        "unreadable",
        None,
        None,
        normalized,
        float(confidence),
    )


def _scheduled_progress_is_plausible(
    current_wave: Optional[int],
    next_wave: Optional[int],
) -> bool:
    if current_wave is None or next_wave is None:
        return False
    return 0 < next_wave - current_wave <= MAX_PERK_SCHEDULE_LEAD_WAVES


def timeline_perk_family(text: Any) -> str:
    """Return a stable family for every currently known selected-Perk label."""

    normalized = _comparison_text(str(text or ""))
    if "coins" in normalized and "tower max health" in normalized:
        return "coin_tradeoff"
    if "enemies have" in normalized and "tower health regen" in normalized:
        return "enemy_health_tradeoff"
    if "tower damage" in normalized and "bosses have" in normalized:
        return "tower_damage_boss_health_tradeoff"
    canonical = canonical_perk_family(text)
    if canonical is not None:
        return canonical
    ordered_contains = (
        ("cash per wave", "cash_wave_tradeoff"),
        ("enemies speed", "enemy_speed_tradeoff"),
        ("ranged enemies attack distance", "ranged_distance_tradeoff"),
        ("bounce shot", "bounce_shot"),
    )
    value_free = re.sub(r"\b\d+(?:\.\d+)?\b", "", normalized)
    value_free = re.sub(r"\s+", " ", value_free).strip()
    for fragment, family in ordered_contains:
        if fragment in value_free:
            return family
    return re.sub(r"[^a-z0-9]+", "_", value_free).strip("_") or "unknown"


def _index_selected_perks(
    selected: Sequence[Any],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for raw in selected:
        if not isinstance(raw, Mapping):
            continue
        entry = _timeline_entry(raw)
        if entry is not None:
            indexed[entry["family"]] = entry
    return indexed


def _timeline_entry(perk: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    display = " ".join(str(perk.get("display_text") or "").split())
    if not display:
        return None
    return {
        "family": timeline_perk_family(display),
        "display_text": display,
        "color": str(perk.get("color") or "unknown"),
        "instance_model": str(perk.get("instance_model") or "unknown"),
        "confidence": float(perk.get("confidence") or -1.0),
    }


def _diff_selected_perks(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    changes = []
    for family, current in after.items():
        previous = before.get(family)
        if previous is None or not _same_display(previous, current):
            changes.append(_selection_change(previous, current))
    return changes


def _selection_change(
    before: Optional[Mapping[str, Any]],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    change = copy.deepcopy(dict(after))
    change["change"] = "added" if before is None else "level_changed"
    if before is not None:
        change["before_display_text"] = str(before.get("display_text") or "")
    return change


def _same_display(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    return _comparison_text(str(left.get("display_text") or "")) == (
        _comparison_text(str(right.get("display_text") or ""))
    )


def _snapshot_has_max_pwr(
    selected: Mapping[str, Mapping[str, Any]],
) -> bool:
    entry = selected.get(PWR_FAMILY)
    return bool(
        entry
        and PWR_MAX_PATTERN.search(str(entry.get("display_text") or ""))
    )


def _comparison_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9.%+-]+", " ", text.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _wait_for_perks(
    *,
    capture_fn: Capture,
    detector: Detector,
    sleep_fn: Callable[[float], None],
    attempts: int = 8,
) -> Frame:
    for _ in range(max(1, attempts)):
        sleep_fn(0.4)
        frame = capture_fn()
        if frame is not None and detector(frame).get("state") == "PERKS":
            return frame
    raise _RouteFailed("Perks panel did not become visible")


def _close_perks_panel(
    screenshot: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    tap_visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
    attempts: int = 8,
) -> _PanelCloseResult:
    """Dispatch one verified close and require an authoritative destination."""

    if not tap_visible_fn(
        "buttons.close:perks",
        screenshot=screenshot,
        retries=1,
    ):
        return _PanelCloseResult(
            dispatched=False,
            closed=False,
            observed_state="PERKS",
        )

    observed_state = "UNKNOWN"
    for _ in range(max(1, attempts)):
        sleep_fn(0.4)
        frame = capture_fn()
        if frame is None:
            continue
        observed_state = str(detector(frame).get("state") or "UNKNOWN")
        if observed_state in PERKS_CLOSE_DESTINATIONS:
            return _PanelCloseResult(
                dispatched=True,
                closed=True,
                observed_state=observed_state,
            )
    return _PanelCloseResult(
        dispatched=True,
        closed=False,
        observed_state=observed_state,
    )


class _RouteFailed(RuntimeError):
    pass


class _RouteInterrupted(RuntimeError):
    pass


__all__ = [
    "PerkCaptureRequest",
    "PerkProgress",
    "PerkTimelineObserver",
    "PerkTimelineTracker",
    "measure_perk_progress",
    "timeline_perk_family",
]
