"""Screen-guarded Daily Mission, Event Mission, and Guild chest claims."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import time
from typing import Callable, Optional

import cv2

from core.event_missions import (
    EventMissionInventory,
    capture_event_mission_inventory,
)
from core.input import tap_if_visible
from core.label_tapper import is_visible
from core.menu_reward_badges import (
    measure_home_reward_badges,
    measure_menu_reward_badges,
)
from core.mission_reward_scheduler import WeeklyChestReviewState
from core.scrolling import ScrollResult, scroll_to_edge, scroll_until_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log, log_action_intent, log_result
from utils.ocr_utils import ocr_text_and_conf


DAILY_MISSION_CLAIM = "buttons.claim_daily_mission"
WEEKLY_MISSION_CHEST = "buttons.claim_weekly_mission_chest"
EVENT_MISSION_CLAIM = "buttons.claim_event_mission"
EVENT_MISSIONS_TAB = "navigation.event:missions_tab"
GUILD_CHEST_CLAIM = "buttons.claim_guild_chest"
REWARD_REVEAL_SKIP = "buttons.skip_reward_reveal"

EVENT_CONTENT_REGION = (0, 840, 1080, 900)
WEEKLY_MISSION_CHEST_REGION = (0, 290, 1080, 210)
WEEKLY_MISSION_PROGRESS_REGION = (740, 175, 340, 80)
WEEKLY_MISSION_CHECKMARK_REGION = (0, 320, 1080, 120)
DAILY_MISSION_CAPACITY_REGION = (0, 485, 500, 100)
DAILY_MISSION_CAPACITY_MIN_CONFIDENCE = 80.0
WEEKLY_MISSION_PROGRESS_MIN_CONFIDENCE = 60.0
WEEKLY_MISSION_EXPECTED_TOTAL = 35
WEEKLY_MISSION_CHEST_INTERVAL = 5
WEEKLY_MISSION_CHECKMARK_MIN_AREA = 700
WEEKLY_MISSION_CHECKMARK_MIN_WIDTH = 70
WEEKLY_MISSION_CHECKMARK_MAX_WIDTH = 115
WEEKLY_MISSION_CHECKMARK_MIN_HEIGHT = 50
WEEKLY_MISSION_CHECKMARK_MAX_HEIGHT = 100
WEEKLY_MISSION_CHECKMARK_CENTER_TOLERANCE = 25.0
WEEKLY_MISSION_MILESTONE_LABEL_Y = 430
WEEKLY_MISSION_MILESTONE_LABEL_HEIGHT = 60
WEEKLY_MISSION_MILESTONE_LABEL_HALF_WIDTH = 55
WEEKLY_MISSION_MILESTONE_MIN_CONFIDENCE = 80.0
WEEKLY_MISSION_SEARCH_COMPLETE = frozenset(
    {
        "all_unlocked_claimed",
        "edge_before_target",
        "max_swipes_exceeded",
        "weekly_progress_already_reviewed",
    }
)
SUNDAY_FULL_CAPACITY_CLAIMS = 2
MAX_DAILY_REWARDS = 12
MAX_EVENT_REWARDS = 24
MAX_GUILD_CHESTS = 4


class MissionRewardResult(str, Enum):
    CLAIMED = "claimed"
    NOTHING_AVAILABLE = "nothing_available"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class MissionRewardCleanupResult(str, Enum):
    COMPLETE = "complete"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"
    FAILED = "failed"


ActionGuard = Optional[Callable[[], bool]]
RouteStateCallback = Optional[
    Callable[[str, bool, Optional[str]], None]
]


class _AuxiliaryAuthorityLost(RuntimeError):
    def __init__(self, expected_state: str):
        super().__init__("auxiliary authority was lost")
        self.expected_state = expected_state


def _action_allowed(action_guard_fn: ActionGuard) -> bool:
    if action_guard_fn is None:
        return True
    try:
        return bool(action_guard_fn())
    except Exception as exc:
        log(
            f"[MISSION_REWARDS] Auxiliary authority check failed: {exc}",
            "ERROR",
        )
        return False


def _require_action_authority(
    action_guard_fn: ActionGuard,
    *,
    expected_state: str,
) -> None:
    if not _action_allowed(action_guard_fn):
        raise _AuxiliaryAuthorityLost(expected_state)


def _note_route(
    callback: RouteStateCallback,
    expected_state: str,
    cleanup_pending: bool,
    reason: Optional[str] = None,
) -> None:
    if callback is not None:
        callback(expected_state, cleanup_pending, reason)


def _tap_if_visible_guarded(
    label: str,
    *,
    action_guard_fn: ActionGuard,
    expected_state: str,
    screenshot=None,
    retries: Optional[int] = None,
) -> bool:
    if (
        action_guard_fn is not None
        and screenshot is not None
        and expected_state not in {
            "REWARD_REVEAL",
            "RUNNING_MENU",
        }
    ):
        if not _is_state(screenshot, expected_state):
            return False
    _require_action_authority(
        action_guard_fn,
        expected_state=expected_state,
    )
    kwargs: dict[str, object] = {}
    if screenshot is not None:
        kwargs["screenshot"] = screenshot
    if retries is not None:
        kwargs["retries"] = retries
    if action_guard_fn is not None:
        kwargs["action_guard_fn"] = action_guard_fn
    return tap_if_visible(label, **kwargs)


def _guarded_wait_for_state(
    state: str,
    *,
    action_guard_fn: ActionGuard,
    **kwargs,
):
    if action_guard_fn is None:
        return _wait_for_state(state, **kwargs)
    return _wait_for_state(
        state,
        action_guard_fn=action_guard_fn,
        **kwargs,
    )


def _guarded_wait_for_label(
    label: str,
    *,
    action_guard_fn: ActionGuard,
    **kwargs,
):
    if action_guard_fn is None:
        return _wait_for_label(label, **kwargs)
    return _wait_for_label(
        label,
        action_guard_fn=action_guard_fn,
        **kwargs,
    )


def _guarded_close_menu(screenshot, action_guard_fn: ActionGuard) -> bool:
    if action_guard_fn is None:
        return _close_menu(screenshot)
    return _close_menu(
        screenshot,
        action_guard_fn=action_guard_fn,
    )


@dataclass(frozen=True)
class MissionRewardSummary:
    daily: int = 0
    event: int = 0
    guild: int = 0

    @property
    def total(self) -> int:
        return self.daily + self.event + self.guild


@dataclass(frozen=True)
class DailyMissionCapacity:
    current: Optional[int]
    limit: Optional[int]
    confidence: float = -1.0
    raw_text: str = ""

    @property
    def is_authoritative_full(self) -> bool:
        return (
            self.current == 8
            and self.limit == 8
            and self.confidence >= DAILY_MISSION_CAPACITY_MIN_CONFIDENCE
        )


@dataclass(frozen=True)
class WeeklyMissionTrackEvidence:
    completed: Optional[int]
    total: Optional[int]
    checkmarks: int
    confidence: float = -1.0
    raw_text: str = ""
    claimed_milestones: tuple[int, ...] = ()

    @property
    def unlocked_chests(self) -> Optional[int]:
        if (
            self.completed is None
            or self.total != WEEKLY_MISSION_EXPECTED_TOTAL
            or self.confidence < WEEKLY_MISSION_PROGRESS_MIN_CONFIDENCE
            or not 0 <= self.completed <= self.total
        ):
            return None
        return self.completed // WEEKLY_MISSION_CHEST_INTERVAL

    @property
    def all_unlocked_claimed(self) -> bool:
        unlocked = self.unlocked_chests
        return (
            unlocked is not None
            and unlocked > 0
            and self.checkmarks == unlocked
        )

    @property
    def visible_claimed_prefix(self) -> bool:
        """Whether visible checks prove the track begins with a claimed prefix."""

        unlocked = self.unlocked_chests
        expected = tuple(
            range(
                WEEKLY_MISSION_CHEST_INTERVAL,
                (self.checkmarks + 1) * WEEKLY_MISSION_CHEST_INTERVAL,
                WEEKLY_MISSION_CHEST_INTERVAL,
            )
        )
        return (
            unlocked is not None
            and 0 < self.checkmarks < unlocked
            and len(self.claimed_milestones) == self.checkmarks
            and self.claimed_milestones == expected
        )


@dataclass(frozen=True)
class WeeklyChestSearchResult(ScrollResult):
    """Weekly search outcome plus proof that nothing left was skipped."""

    left_search_complete: bool = False


def _finish_mission_reward_review(
    result: MissionRewardResult,
    *,
    summary: MissionRewardSummary,
    source_state: str,
    reason: str,
) -> MissionRewardResult:
    """Emit the terminal operator result for one mission-reward review."""

    if result == MissionRewardResult.CLAIMED:
        reward_word = "reward" if summary.total == 1 else "rewards"
        result_summary = (
            "Mission reward review complete — "
            f"claimed {summary.total} {reward_word}"
        )
    elif result == MissionRewardResult.NOTHING_AVAILABLE:
        result_summary = (
            "Mission reward review complete — no claimable rewards found"
        )
    elif result == MissionRewardResult.INTERRUPTED:
        result_summary = (
            "Mission reward review interrupted — verified cleanup remains pending"
        )
    else:
        result_summary = f"Mission reward review failed — {reason}"

    log_result(
        result_summary,
        detail=(
            f"[MISSION_REWARDS] result={result.value} source={source_state} "
            f"daily={summary.daily} event={summary.event} guild={summary.guild} "
            f"reason={reason}"
        ),
    )
    return result


def _handle_mission_rewards_route(
    screenshot=None,
    *,
    claim_daily_missions: bool = True,
    event_inventory_callback: Optional[
        Callable[[EventMissionInventory], object]
    ] = None,
    weekly_review_state: Optional[WeeklyChestReviewState] = None,
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
) -> MissionRewardResult:
    """Inspect relevant badges, claim proven rewards, and restore the source UI."""

    log_action_intent(
        "Reviewing mission rewards",
        reason=(
            "reward badges may indicate claimable Daily Missions, Weekly "
            "Mission chests, Event Missions, or Guild chests"
        ),
    )
    initial = screenshot if screenshot is not None else capture_adb_screenshot()
    source_state = _reward_source_state(initial)
    reward_hub = _ensure_reward_hub(
        initial,
        source_state=source_state,
        action_guard_fn=action_guard_fn,
        route_state_callback=route_state_callback,
    )
    if reward_hub is None:
        return _finish_mission_reward_review(
            MissionRewardResult.FAILED,
            summary=MissionRewardSummary(),
            source_state=source_state,
            reason="the reward hub could not be verified",
        )

    badges = (
        measure_menu_reward_badges(reward_hub)
        if source_state == "RUNNING"
        else measure_home_reward_badges(reward_hub)
    )
    log(
        f"[MISSION_REWARDS] Badges source={source_state}: "
        f"daily={badges.daily_missions} event={badges.event_missions} "
        f"guild={badges.guild_chests}",
        "DEBUG",
    )

    navigation = (
        {
            "daily": "navigation.menu_daily_missions",
            "event": "navigation.menu_event",
            "guild": "navigation.menu_guild",
        }
        if source_state == "RUNNING"
        else {
            "daily": "navigation.home_daily_missions",
            "event": "navigation.home_event",
            "guild": None,
        }
    )

    summary = MissionRewardSummary()
    success = True
    sections = (
        (
            badges.daily_missions,
            "Daily Missions",
            navigation["daily"],
            "DAILY_MISSIONS",
            _claim_daily_rewards,
            "daily",
        ),
        (
            badges.event_missions,
            "Event Missions",
            navigation["event"],
            "EVENT",
            _claim_event_rewards,
            "event",
        ),
        (
            badges.guild_chests,
            "Guild chests",
            navigation["guild"],
            "GUILD",
            _claim_guild_chests,
            "guild",
        ),
    )

    for enabled, name, navigation, state, claim_fn, summary_field in sections:
        if not enabled:
            continue
        reward_hub = _ensure_reward_hub(
            reward_hub,
            source_state=source_state,
            action_guard_fn=action_guard_fn,
            route_state_callback=route_state_callback,
        )
        if reward_hub is None or navigation is None:
            success = False
            break
        _note_route(route_state_callback, str(source_state), True)
        if not _tap_if_visible_guarded(
            navigation,
            screenshot=reward_hub,
            retries=1,
            action_guard_fn=action_guard_fn,
            expected_state=str(source_state),
        ):
            log(f"[MISSION_REWARDS] Could not open {name}", "WARN")
            success = False
            break
        _note_route(route_state_callback, state, True)
        panel = _guarded_wait_for_state(
            state,
            action_guard_fn=action_guard_fn,
        )
        if panel is None:
            log(f"[MISSION_REWARDS] {name} identity was not verified", "WARN")
            success = False
            break

        if summary_field == "daily":
            claim_kwargs = {"claim_missions": claim_daily_missions}
            if weekly_review_state is not None:
                claim_kwargs["weekly_review_state"] = weekly_review_state
        elif summary_field == "event":
            claim_kwargs = {
                "inventory_callback": event_inventory_callback,
            }
        else:
            claim_kwargs = {}
        if action_guard_fn is not None:
            claim_kwargs["action_guard_fn"] = action_guard_fn
            claim_kwargs["route_state_callback"] = route_state_callback
        section_success, claimed = claim_fn(panel, **claim_kwargs)
        summary = MissionRewardSummary(
            daily=claimed if summary_field == "daily" else summary.daily,
            event=claimed if summary_field == "event" else summary.event,
            guild=claimed if summary_field == "guild" else summary.guild,
        )
        success = success and section_success

        reward_hub = _return_to_reward_hub(
            state,
            source_state=source_state,
            action_guard_fn=action_guard_fn,
            route_state_callback=route_state_callback,
        )
        if reward_hub is None:
            success = False
            break

    if (
        source_state == "RUNNING"
        and reward_hub is not None
        and not _guarded_close_menu(reward_hub, action_guard_fn)
    ):
        success = False
    if success:
        _note_route(route_state_callback, str(source_state), False)

    if not success:
        return _finish_mission_reward_review(
            MissionRewardResult.FAILED,
            summary=summary,
            source_state=source_state,
            reason="a reward section or cleanup step did not complete",
        )
    if summary.total:
        return _finish_mission_reward_review(
            MissionRewardResult.CLAIMED,
            summary=summary,
            source_state=source_state,
            reason="all enabled reward sections completed",
        )
    return _finish_mission_reward_review(
        MissionRewardResult.NOTHING_AVAILABLE,
        summary=summary,
        source_state=source_state,
        reason="all enabled reward sections completed without a claim",
    )


def handle_mission_rewards(
    screenshot=None,
    *,
    claim_daily_missions: bool = True,
    event_inventory_callback: Optional[
        Callable[[EventMissionInventory], object]
    ] = None,
    weekly_review_state: Optional[WeeklyChestReviewState] = None,
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
) -> MissionRewardResult:
    """Run a guarded reward route and retain cleanup ownership if interrupted."""

    try:
        return _handle_mission_rewards_route(
            screenshot,
            claim_daily_missions=claim_daily_missions,
            event_inventory_callback=event_inventory_callback,
            weekly_review_state=weekly_review_state,
            action_guard_fn=action_guard_fn,
            route_state_callback=route_state_callback,
        )
    except _AuxiliaryAuthorityLost as exc:
        _note_route(
            route_state_callback,
            exc.expected_state,
            True,
            "auxiliary authority was lost",
        )
        return _finish_mission_reward_review(
            MissionRewardResult.INTERRUPTED,
            summary=MissionRewardSummary(),
            source_state=_reward_source_state(screenshot) or "UNKNOWN",
            reason="auxiliary authority was lost",
        )


def _claim_daily_rewards(
    screenshot,
    *,
    claim_missions: bool = True,
    weekly_review_state: Optional[WeeklyChestReviewState] = None,
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
) -> tuple[bool, int]:
    current = screenshot
    claimed = 0
    ordinary_claimed = 0
    weekly_checks = 0
    weekly_chests_claimed = 0
    weekly_left_search_complete = False
    weekly_check_reason = "Daily Mission claims were checked first"
    ordinary_claim_limit_logged = False
    ordinary_claim_limit: Optional[int] = None
    if not claim_missions:
        capacity = _read_daily_mission_capacity(current)
        capacity_text = (
            f"{capacity.current}/{capacity.limit}"
            if capacity.current is not None and capacity.limit is not None
            else "unknown"
        )
        if capacity.is_authoritative_full:
            ordinary_claim_limit = SUNDAY_FULL_CAPACITY_CLAIMS
            log(
                "[MISSION_REWARDS] Sunday Daily Mission capacity "
                f"{capacity_text} verified (OCR confidence={capacity.confidence:.1f}); "
                f"releasing {ordinary_claim_limit} ordinary claims",
                "DEBUG",
            )
        else:
            ordinary_claim_limit = 0
            log(
                "[MISSION_REWARDS] Holding ordinary Daily Mission claims "
                f"until the weekly reset (capacity={capacity_text}, "
                f"OCR confidence={capacity.confidence:.1f})",
                "DEBUG",
            )

    for _ in range(MAX_DAILY_REWARDS):
        if not _is_state(current, "DAILY_MISSIONS"):
            return False, claimed

        ordinary_claim_allowed = (
            ordinary_claim_limit is None
            or ordinary_claimed < ordinary_claim_limit
        )
        if ordinary_claim_allowed and is_visible(
            DAILY_MISSION_CLAIM,
            screenshot=current,
        ):
            if not _tap_if_visible_guarded(
                DAILY_MISSION_CLAIM,
                screenshot=current,
                action_guard_fn=action_guard_fn,
                expected_state="DAILY_MISSIONS",
            ):
                return False, claimed
            current = _guarded_wait_for_state(
                "DAILY_MISSIONS",
                settle_s=0.6,
                action_guard_fn=action_guard_fn,
            )
            if current is None:
                return False, claimed
            claimed += 1
            ordinary_claimed += 1
            weekly_check_reason = (
                "all currently claimable Daily Mission rewards were claimed "
                "first; checking for unlocked milestone chests"
            )
            continue

        if (
            ordinary_claim_limit is not None
            and ordinary_claimed >= ordinary_claim_limit
            and ordinary_claimed
            and not ordinary_claim_limit_logged
        ):
            log(
                "[MISSION_REWARDS] Sunday capacity relief complete: "
                f"claimed {ordinary_claimed} ordinary Daily Mission rewards",
                "DEBUG",
            )
            ordinary_claim_limit_logged = True

        weekly_checks += 1
        log(
            f"[MISSION_REWARDS] Weekly chest check {weekly_checks} starting: "
            f"{weekly_check_reason} (claimed={claimed}, "
            f"ordinary_claimed={ordinary_claimed})",
            "DEBUG",
        )
        search_kwargs = {"action_guard_fn": action_guard_fn}
        if weekly_left_search_complete:
            search_kwargs["left_search_complete"] = True
        if weekly_review_state is not None:
            search_kwargs["review_state"] = weekly_review_state
        weekly_chest = _find_weekly_mission_chest(current, **search_kwargs)
        log(
            f"[MISSION_REWARDS] Weekly chest check {weekly_checks} result: "
            f"found={weekly_chest.success} swipes={weekly_chest.swipes} "
            f"reason={weekly_chest.reason}",
            "DEBUG",
        )
        if weekly_chest.success:
            weekly_left_search_complete = weekly_chest.left_search_complete
            if weekly_review_state is not None:
                weekly_review_state.invalidate()
            current = weekly_chest.screenshot
            if current is None or not _tap_if_visible_guarded(
                WEEKLY_MISSION_CHEST,
                screenshot=current,
                action_guard_fn=action_guard_fn,
                expected_state="DAILY_MISSIONS",
            ):
                return False, claimed
            _note_route(
                route_state_callback,
                "REWARD_REVEAL:DAILY_MISSIONS",
                True,
            )
            current = _dismiss_reward_reveal(
                "DAILY_MISSIONS",
                action_guard_fn=action_guard_fn,
                route_state_callback=route_state_callback,
            )
            if current is None:
                return False, claimed
            claimed += 1
            weekly_chests_claimed += 1
            weekly_check_reason = (
                "the previous Weekly Mission chest was claimed; checking for "
                "another completed milestone"
            )
            continue
        if weekly_chest.reason not in WEEKLY_MISSION_SEARCH_COMPLETE:
            log(
                "[MISSION_REWARDS] Weekly chest search failed: "
                f"{weekly_chest.reason}",
                "WARN",
            )
            return False, claimed
        if weekly_chest.screenshot is not None:
            current = weekly_chest.screenshot
        break
    else:
        log("[MISSION_REWARDS] Daily reward claim bound reached", "WARN")
        return False, claimed

    log(
        "[MISSION_REWARDS] Weekly chest review complete: "
        f"checks={weekly_checks} chests_claimed={weekly_chests_claimed} "
        f"post_claim_rechecks={max(0, weekly_checks - 1)}",
        "DEBUG",
    )
    return True, claimed


def _find_weekly_mission_chest(
    screenshot,
    *,
    left_search_complete: bool = False,
    review_state: Optional[WeeklyChestReviewState] = None,
    action_guard_fn: ActionGuard = None,
) -> WeeklyChestSearchResult:
    """Find an available weekly chest across the bounded horizontal track."""

    if is_visible(WEEKLY_MISSION_CHEST, screenshot=screenshot):
        return WeeklyChestSearchResult(
            True,
            screenshot,
            0,
            "target_visible",
            left_search_complete,
        )

    track = _measure_weekly_mission_track(screenshot)
    log(
        "[MISSION_REWARDS] Weekly track evidence: "
        f"completed={track.completed}/{track.total} "
        f"confidence={track.confidence:.1f} checkmarks={track.checkmarks} "
        f"milestones={track.claimed_milestones} "
        f"unlocked={track.unlocked_chests} raw={track.raw_text!r}",
        "DEBUG",
    )
    if track.all_unlocked_claimed:
        if review_state is not None:
            review_state.mark_reviewed(track.unlocked_chests)
        log(
            "[MISSION_REWARDS] Weekly track already shows every unlocked "
            "chest as claimed; skipping left-edge normalization",
            "DEBUG",
        )
        return WeeklyChestSearchResult(
            False,
            screenshot,
            0,
            "all_unlocked_claimed",
            True,
        )

    if review_state is not None and review_state.covers(
        track.unlocked_chests
    ):
        log(
            "[MISSION_REWARDS] Weekly milestone level "
            f"{track.unlocked_chests * WEEKLY_MISSION_CHEST_INTERVAL} "
            "was already fully reviewed this cycle; skipping repeat track "
            "normalization",
            "DEBUG",
        )
        return WeeklyChestSearchResult(
            False,
            screenshot,
            0,
            "weekly_progress_already_reviewed",
            True,
        )

    guard_kwargs = (
        {"action_guard_fn": action_guard_fn}
        if action_guard_fn is not None
        else {}
    )
    rewind_swipes = 0
    current = screenshot
    coverage_complete = left_search_complete
    if left_search_complete:
        log(
            "[MISSION_REWARDS] The current claim loop already reviewed every "
            "milestone to the left; continuing right without normalization",
            "DEBUG",
        )
    elif track.visible_claimed_prefix:
        coverage_complete = True
        log(
            "[MISSION_REWARDS] Weekly track shows a contiguous claimed "
            f"prefix through milestone {track.claimed_milestones[-1]}; "
            "searching right without left-edge normalization",
            "DEBUG",
        )
    else:
        first = scroll_to_edge(
            "gesture_targets.goto_first:weekly_mission_chests",
            source_label="indicators.daily_missions",
            screenshot=screenshot,
            progress_region=WEEKLY_MISSION_CHEST_REGION,
            max_swipes=4,
            settle_s=0.8,
            stable_threshold=2.0,
            **guard_kwargs,
        )
        rewind_swipes = first.swipes
        current = first.screenshot if first.screenshot is not None else screenshot
        if first.reason not in {"edge_reached", "max_swipes_exceeded"}:
            return WeeklyChestSearchResult(
                first.success,
                first.screenshot,
                first.swipes,
                first.reason,
                False,
            )
        coverage_complete = first.reason == "edge_reached"
        if is_visible(WEEKLY_MISSION_CHEST, screenshot=current):
            return WeeklyChestSearchResult(
                True,
                current,
                rewind_swipes,
                "target_visible",
                coverage_complete,
            )

    found = scroll_until_visible(
        "gesture_targets.goto_next:weekly_mission_chests",
        source_label="indicators.daily_missions",
        target_label=WEEKLY_MISSION_CHEST,
        screenshot=current,
        progress_region=WEEKLY_MISSION_CHEST_REGION,
        max_swipes=8,
        settle_s=0.8,
        stable_threshold=2.0,
        **guard_kwargs,
    )
    result = WeeklyChestSearchResult(
        found.success,
        found.screenshot,
        rewind_swipes + found.swipes,
        found.reason,
        coverage_complete,
    )
    if (
        not result.success
        and result.reason == "edge_before_target"
        and result.left_search_complete
        and track.unlocked_chests is not None
        and review_state is not None
    ):
        review_state.mark_reviewed(track.unlocked_chests)
        log(
            "[MISSION_REWARDS] Weekly chest review covered every unlocked "
            "milestone through "
            f"{track.unlocked_chests * WEEKLY_MISSION_CHEST_INTERVAL}; "
            "retaining that coverage for unchanged progress",
            "DEBUG",
        )
    return result


def _read_weekly_claimed_milestones(
    screenshot,
    checkmark_centers: list[float],
) -> tuple[int, ...]:
    """OCR the milestone directly below each detected checkmark, fail closed."""

    screen_height, screen_width = screenshot.shape[:2]
    label_y = WEEKLY_MISSION_MILESTONE_LABEL_Y
    label_height = WEEKLY_MISSION_MILESTONE_LABEL_HEIGHT
    half_width = WEEKLY_MISSION_MILESTONE_LABEL_HALF_WIDTH
    if label_y + label_height > screen_height:
        return ()

    milestones: list[int] = []
    for center in sorted(checkmark_centers):
        center_x = int(round(center))
        left = center_x - half_width
        right = center_x + half_width
        if left < 0 or right > screen_width:
            return ()
        crop = screenshot[label_y : label_y + label_height, left:right]
        try:
            raw_text, confidence = ocr_text_and_conf(crop, psm=7)
        except Exception:
            return ()
        match = re.fullmatch(r"\s*(\d+)\s*", raw_text or "")
        if (
            match is None
            or confidence < WEEKLY_MISSION_MILESTONE_MIN_CONFIDENCE
        ):
            return ()
        milestones.append(int(match.group(1)))
    return tuple(milestones)


def _measure_weekly_mission_track(screenshot) -> WeeklyMissionTrackEvidence:
    """Return positive evidence for unlocked weekly chests already claimed."""

    if (
        screenshot is None
        or not hasattr(screenshot, "shape")
        or len(screenshot.shape) < 2
    ):
        return WeeklyMissionTrackEvidence(None, None, 0)

    screen_height, screen_width = screenshot.shape[:2]
    progress_x, progress_y, progress_width, progress_height = (
        WEEKLY_MISSION_PROGRESS_REGION
    )
    check_x, check_y, check_width, check_height = WEEKLY_MISSION_CHECKMARK_REGION
    if (
        progress_x + progress_width > screen_width
        or progress_y + progress_height > screen_height
        or check_x + check_width > screen_width
        or check_y + check_height > screen_height
    ):
        return WeeklyMissionTrackEvidence(None, None, 0)

    progress_crop = screenshot[
        progress_y : progress_y + progress_height,
        progress_x : progress_x + progress_width,
    ]
    try:
        raw_text, confidence = ocr_text_and_conf(progress_crop, psm=7)
    except Exception:
        raw_text, confidence = "", -1.0
    match = re.search(
        r"\bcompleted?\s*(\d+)\s*/\s*(\d+)(?!\d)",
        raw_text,
        re.IGNORECASE,
    )
    completed = int(match.group(1)) if match is not None else None
    total = int(match.group(2)) if match is not None else None

    checkmark_crop = screenshot[
        check_y : check_y + check_height,
        check_x : check_x + check_width,
    ]
    try:
        hsv = cv2.cvtColor(checkmark_crop, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(hsv, (40, 100, 150), (80, 255, 255))
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            green,
            connectivity=8,
        )
    except cv2.error:
        return WeeklyMissionTrackEvidence(
            completed,
            total,
            0,
            confidence,
            raw_text,
        )

    # The glow and solid stroke can be separate overlapping components. Merge
    # those by horizontal center so each visible checkmark is counted once.
    checkmark_centers: list[float] = []
    for x, _y, width, height, area in stats[1:]:
        if (
            area < WEEKLY_MISSION_CHECKMARK_MIN_AREA
            or not WEEKLY_MISSION_CHECKMARK_MIN_WIDTH
            <= width
            <= WEEKLY_MISSION_CHECKMARK_MAX_WIDTH
            or not WEEKLY_MISSION_CHECKMARK_MIN_HEIGHT
            <= height
            <= WEEKLY_MISSION_CHECKMARK_MAX_HEIGHT
        ):
            continue
        center = float(x) + (float(width) / 2.0)
        if any(
            abs(center - known) <= WEEKLY_MISSION_CHECKMARK_CENTER_TOLERANCE
            for known in checkmark_centers
        ):
            continue
        checkmark_centers.append(center)

    claimed_milestones = _read_weekly_claimed_milestones(
        screenshot,
        [check_x + center for center in checkmark_centers],
    )
    return WeeklyMissionTrackEvidence(
        completed,
        total,
        len(checkmark_centers),
        confidence,
        raw_text,
        claimed_milestones,
    )


def _read_daily_mission_capacity(screenshot) -> DailyMissionCapacity:
    if (
        screenshot is None
        or not hasattr(screenshot, "shape")
        or len(screenshot.shape) < 2
    ):
        return DailyMissionCapacity(None, None)

    x, y, width, height = DAILY_MISSION_CAPACITY_REGION
    screen_height, screen_width = screenshot.shape[:2]
    if x + width > screen_width or y + height > screen_height:
        return DailyMissionCapacity(None, None)

    try:
        raw_text, confidence = ocr_text_and_conf(
            screenshot[y : y + height, x : x + width],
            psm=7,
        )
    except Exception:
        return DailyMissionCapacity(None, None)

    match = re.search(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", raw_text)
    if match is None:
        return DailyMissionCapacity(None, None, confidence, raw_text)
    return DailyMissionCapacity(
        int(match.group(1)),
        int(match.group(2)),
        confidence,
        raw_text,
    )


def _claim_event_rewards(
    screenshot,
    *,
    inventory_callback: Optional[
        Callable[[EventMissionInventory], object]
    ] = None,
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
) -> tuple[bool, int]:
    # Event retains its last-selected tab.  Re-enter Missions explicitly so a
    # prior Bots or Event Shop visit cannot be mistaken for the mission list.
    if not _is_state(screenshot, "EVENT"):
        return False, 0
    if not _tap_if_visible_guarded(
        EVENT_MISSIONS_TAB,
        screenshot=screenshot,
        retries=1,
        action_guard_fn=action_guard_fn,
        expected_state="EVENT",
    ):
        log("[MISSION_REWARDS] Could not select the Event Missions tab", "WARN")
        return False, 0
    missions = _guarded_wait_for_state(
        "EVENT",
        settle_s=0.8,
        action_guard_fn=action_guard_fn,
    )
    if missions is None:
        return False, 0

    guard_kwargs = (
        {"action_guard_fn": action_guard_fn}
        if action_guard_fn is not None
        else {}
    )
    top = scroll_to_edge(
        "gesture_targets.goto_top:event_missions",
        source_label="indicators.event",
        screenshot=missions,
        progress_region=EVENT_CONTENT_REGION,
        max_swipes=8,
        settle_s=0.8,
        stable_threshold=2.0,
        **guard_kwargs,
    )
    current = top.screenshot if top.screenshot is not None else screenshot
    if top.reason not in {"edge_reached", "max_swipes_exceeded"}:
        return False, 0

    claimed = 0
    for _ in range(MAX_EVENT_REWARDS):
        if not _is_state(current, "EVENT"):
            return False, claimed
        if not is_visible(EVENT_MISSION_CLAIM, screenshot=current):
            found = scroll_until_visible(
                "gesture_targets.goto_next:event_missions",
                source_label="indicators.event",
                target_label=EVENT_MISSION_CLAIM,
                screenshot=current,
                progress_region=EVENT_CONTENT_REGION,
                max_swipes=10,
                settle_s=0.8,
                stable_threshold=2.0,
                **guard_kwargs,
            )
            if not found.success:
                if found.reason in {"edge_before_target", "max_swipes_exceeded"}:
                    _record_event_inventory(
                        found.screenshot if found.screenshot is not None else current,
                        inventory_callback,
                    )
                    return True, claimed
                return False, claimed
            current = found.screenshot
        if current is None or not _tap_if_visible_guarded(
            EVENT_MISSION_CLAIM,
            screenshot=current,
            action_guard_fn=action_guard_fn,
            expected_state="EVENT",
        ):
            return False, claimed
        current = _guarded_wait_for_state(
            "EVENT",
            settle_s=0.6,
            action_guard_fn=action_guard_fn,
        )
        if current is None:
            return False, claimed
        claimed += 1

    log("[MISSION_REWARDS] Event reward claim bound reached", "WARN")
    return False, claimed


def _record_event_inventory(
    screenshot,
    callback: Optional[Callable[[EventMissionInventory], object]],
) -> None:
    """Inventory Event rows during an existing badge-triggered panel visit."""

    if callback is None or screenshot is None:
        return
    try:
        inventory = capture_event_mission_inventory(screenshot)
        accepted = callback(inventory)
        incomplete = sum(1 for mission in inventory.missions if mission.incomplete)
        log(
            "[EVENT_MISSIONS] Inventory "
            f"event={inventory.event_name or 'unknown'!r} "
            f"rows={len(inventory.missions)} incomplete={incomplete} "
            f"complete={inventory.complete} accepted={accepted is not False}",
            "DEBUG" if inventory.complete else "WARN",
        )
    except Exception as exc:
        # Reminders are advisory and must not turn an otherwise successful
        # reward claim into a navigation failure.
        log(f"[EVENT_MISSIONS] Inventory failed: {exc}", "WARN")


def _claim_guild_chests(
    screenshot,
    *,
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
) -> tuple[bool, int]:
    current = screenshot
    if not _is_state(current, "GUILD"):
        return False, 0
    # Guild retains its last selected tab.  Always reselect Members before
    # interpreting absence of glowing contribution chests as authoritative.
    if not _tap_if_visible_guarded(
        "navigation.guild:members_tab",
        screenshot=current,
        retries=1,
        action_guard_fn=action_guard_fn,
        expected_state="GUILD",
    ):
        return False, 0
    current = _guarded_wait_for_state(
        "GUILD",
        settle_s=0.8,
        action_guard_fn=action_guard_fn,
    )
    if current is None:
        return False, 0

    claimed = 0
    for _ in range(MAX_GUILD_CHESTS):
        if not _is_state(current, "GUILD"):
            return False, claimed
        if not is_visible(GUILD_CHEST_CLAIM, screenshot=current):
            return True, claimed
        if not _tap_if_visible_guarded(
            GUILD_CHEST_CLAIM,
            screenshot=current,
            action_guard_fn=action_guard_fn,
            expected_state="GUILD",
        ):
            return False, claimed
        _note_route(
            route_state_callback,
            "REWARD_REVEAL:GUILD",
            True,
        )
        current = _dismiss_reward_reveal(
            "GUILD",
            action_guard_fn=action_guard_fn,
            route_state_callback=route_state_callback,
        )
        if current is None:
            return False, claimed
        claimed += 1

    log("[MISSION_REWARDS] Guild chest claim bound reached", "WARN")
    return False, claimed


def _dismiss_reward_reveal(
    return_state: str,
    *,
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
):
    reveal = _guarded_wait_for_label(
        REWARD_REVEAL_SKIP,
        timeout=8.0,
        action_guard_fn=action_guard_fn,
    )
    if reveal is None:
        log("[MISSION_REWARDS] Reward reveal SKIP was not verified", "WARN")
        return None
    if not _tap_if_visible_guarded(
        REWARD_REVEAL_SKIP,
        screenshot=reveal,
        action_guard_fn=action_guard_fn,
        expected_state="REWARD_REVEAL",
    ):
        return None
    _note_route(route_state_callback, return_state, True)
    return _guarded_wait_for_state(
        return_state,
        settle_s=0.6,
        action_guard_fn=action_guard_fn,
    )


def _reward_source_state(screenshot) -> Optional[str]:
    if screenshot is None:
        return None
    state = detect_state_and_overlays(screenshot).get("state")
    return state if state in {"RUNNING", "HOME_SCREEN"} else None


def _ensure_reward_hub(
    screenshot=None,
    *,
    source_state: Optional[str],
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
):
    current = screenshot if screenshot is not None else capture_adb_screenshot()
    if current is None:
        return None
    detection = detect_state_and_overlays(current)
    if detection.get("state") != source_state:
        log(
            f"[MISSION_REWARDS] Refusing reward navigation from "
            f"state={detection.get('state')!r}",
            "WARN",
        )
        return None
    if source_state == "HOME_SCREEN":
        return current
    if source_state != "RUNNING":
        log("[MISSION_REWARDS] Unsupported reward source", "WARN")
        return None
    overlays = set(detection.get("overlays") or [])
    if "MENU_OPEN" in overlays:
        return current
    if "MENU_CLOSED" not in overlays:
        log("[MISSION_REWARDS] Menu state is not verified", "WARN")
        return None
    if not _tap_if_visible_guarded(
        "navigation.toggle_menu",
        screenshot=current,
        action_guard_fn=action_guard_fn,
        expected_state="RUNNING",
    ):
        return None
    _note_route(route_state_callback, "RUNNING_MENU", True)
    return _guarded_wait_for_state(
        "RUNNING",
        required_overlay="MENU_OPEN",
        settle_s=0.6,
        action_guard_fn=action_guard_fn,
    )


def _return_to_reward_hub(
    panel_state: str,
    *,
    source_state: str,
    action_guard_fn: ActionGuard = None,
    route_state_callback: RouteStateCallback = None,
):
    current = capture_adb_screenshot()
    if current is None or not _is_state(current, panel_state):
        log(
            f"[MISSION_REWARDS] Refusing Return to Game from an unverified "
            f"{panel_state} panel",
            "WARN",
        )
        return None
    if not _tap_if_visible_guarded(
        "buttons.return_to_game",
        screenshot=current,
        action_guard_fn=action_guard_fn,
        expected_state=panel_state,
    ):
        return None
    _note_route(route_state_callback, source_state, True)
    source = _guarded_wait_for_state(
        source_state,
        settle_s=0.6,
        action_guard_fn=action_guard_fn,
    )
    if source is None:
        return None
    return _ensure_reward_hub(
        source,
        source_state=source_state,
        action_guard_fn=action_guard_fn,
        route_state_callback=route_state_callback,
    )


def _close_menu(
    screenshot,
    *,
    action_guard_fn: ActionGuard = None,
) -> bool:
    if action_guard_fn is not None:
        # A panel wait may have consumed newer boundary frames. Never let its
        # older reward-hub image authorize cleanup after Game Over, Home, or
        # unexpected navigation has taken ownership.
        screenshot = capture_adb_screenshot()
        if screenshot is None:
            return False
    detection = detect_state_and_overlays(screenshot)
    if detection.get("state") != "RUNNING":
        return False
    overlays = set(detection.get("overlays") or [])
    if "MENU_CLOSED" in overlays:
        return True
    if "MENU_OPEN" not in overlays:
        return False
    if not _tap_if_visible_guarded(
        "navigation.menu_close_button",
        screenshot=screenshot,
        action_guard_fn=action_guard_fn,
        expected_state="RUNNING_MENU",
    ):
        return False
    return _guarded_wait_for_state(
        "RUNNING",
        required_overlay="MENU_CLOSED",
        settle_s=0.6,
        action_guard_fn=action_guard_fn,
    ) is not None


def _wait_for_state(
    state: str,
    *,
    required_overlay: Optional[str] = None,
    timeout: float = 8.0,
    poll: float = 0.3,
    settle_s: float = 0.0,
    action_guard_fn: ActionGuard = None,
):
    if settle_s > 0:
        time.sleep(settle_s)
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        _require_action_authority(
            action_guard_fn,
            expected_state=state,
        )
        screenshot = capture_adb_screenshot()
        if screenshot is not None:
            detection = detect_state_and_overlays(screenshot)
            overlays = set(detection.get("overlays") or [])
            if detection.get("state") == state and (
                required_overlay is None or required_overlay in overlays
            ):
                return screenshot
        time.sleep(max(0.05, poll))
    return None


def _wait_for_label(
    label: str,
    *,
    timeout: float,
    poll: float = 0.3,
    action_guard_fn: ActionGuard = None,
):
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        _require_action_authority(
            action_guard_fn,
            expected_state="REWARD_REVEAL",
        )
        screenshot = capture_adb_screenshot()
        if screenshot is not None and is_visible(label, screenshot=screenshot):
            return screenshot
        time.sleep(max(0.05, poll))
    return None


def resume_mission_reward_cleanup(
    source_state: str,
    expected_state: str,
    *,
    action_guard_fn: Callable[[], bool],
    max_steps: int = 4,
) -> MissionRewardCleanupResult:
    """Restore an interrupted reward route through only declared route states."""

    source = str(source_state or "").upper()
    expected = str(expected_state or "").upper()
    if source not in {"RUNNING", "HOME_SCREEN"}:
        return MissionRewardCleanupResult.ABANDONED
    allowed_panels = {"DAILY_MISSIONS", "EVENT", "GUILD"}
    reveal_return = None
    if expected.startswith("REWARD_REVEAL:"):
        candidate = expected.split(":", 1)[1]
        if candidate in allowed_panels:
            reveal_return = candidate

    try:
        for _ in range(max(1, int(max_steps))):
            _require_action_authority(
                action_guard_fn,
                expected_state=expected or source,
            )
            screenshot = capture_adb_screenshot()
            if screenshot is None:
                return MissionRewardCleanupResult.FAILED
            detection = detect_state_and_overlays(screenshot)
            state = str(detection.get("state") or "UNKNOWN").upper()
            overlays = set(detection.get("overlays") or [])
            if state == source:
                if source != "RUNNING" or "MENU_CLOSED" in overlays:
                    return MissionRewardCleanupResult.COMPLETE
                if "MENU_OPEN" not in overlays:
                    return MissionRewardCleanupResult.ABANDONED
                return (
                    MissionRewardCleanupResult.COMPLETE
                    if _close_menu(
                        screenshot,
                        action_guard_fn=action_guard_fn,
                    )
                    else MissionRewardCleanupResult.FAILED
                )
            if is_visible(REWARD_REVEAL_SKIP, screenshot=screenshot):
                if reveal_return is None:
                    return MissionRewardCleanupResult.ABANDONED
                if not _tap_if_visible_guarded(
                    REWARD_REVEAL_SKIP,
                    screenshot=screenshot,
                    action_guard_fn=action_guard_fn,
                    expected_state="REWARD_REVEAL",
                ):
                    return MissionRewardCleanupResult.FAILED
                expected = reveal_return
                time.sleep(0.6)
                continue
            if state not in allowed_panels:
                # A natural boundary or unexpected route state belongs to the
                # ordinary boundary observer; never improvise recovery input.
                return MissionRewardCleanupResult.ABANDONED
            hub = _return_to_reward_hub(
                state,
                source_state=source,
                action_guard_fn=action_guard_fn,
            )
            if hub is None:
                return MissionRewardCleanupResult.FAILED
            if source == "RUNNING" and not _close_menu(
                hub,
                action_guard_fn=action_guard_fn,
            ):
                return MissionRewardCleanupResult.FAILED
            return MissionRewardCleanupResult.COMPLETE
    except _AuxiliaryAuthorityLost:
        return MissionRewardCleanupResult.INTERRUPTED
    return MissionRewardCleanupResult.FAILED


def _is_state(screenshot, state: str) -> bool:
    return detect_state_and_overlays(screenshot).get("state") == state


__all__ = [
    "MissionRewardCleanupResult",
    "MissionRewardResult",
    "MissionRewardSummary",
    "handle_mission_rewards",
    "resume_mission_reward_cleanup",
]
