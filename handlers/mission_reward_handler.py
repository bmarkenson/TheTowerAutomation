"""Screen-guarded Daily Mission, Event Mission, and Guild chest claims."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import time
from typing import Callable, Optional

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
from core.scrolling import scroll_to_edge, scroll_until_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log
from utils.ocr_utils import ocr_text_and_conf


DAILY_MISSION_CLAIM = "buttons.claim_daily_mission"
WEEKLY_MISSION_CHEST = "buttons.claim_weekly_mission_chest"
EVENT_MISSION_CLAIM = "buttons.claim_event_mission"
EVENT_MISSIONS_TAB = "navigation.event:missions_tab"
GUILD_CHEST_CLAIM = "buttons.claim_guild_chest"
REWARD_REVEAL_SKIP = "buttons.skip_reward_reveal"

EVENT_CONTENT_REGION = (0, 840, 1080, 900)
DAILY_MISSION_CAPACITY_REGION = (0, 485, 500, 100)
DAILY_MISSION_CAPACITY_MIN_CONFIDENCE = 80.0
SUNDAY_FULL_CAPACITY_CLAIMS = 2
MAX_DAILY_REWARDS = 12
MAX_EVENT_REWARDS = 24
MAX_GUILD_CHESTS = 4


class MissionRewardResult(str, Enum):
    CLAIMED = "claimed"
    NOTHING_AVAILABLE = "nothing_available"
    FAILED = "failed"


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


def handle_mission_rewards(
    screenshot=None,
    *,
    claim_daily_missions: bool = True,
    event_inventory_callback: Optional[
        Callable[[EventMissionInventory], object]
    ] = None,
) -> MissionRewardResult:
    """Inspect relevant badges, claim proven rewards, and restore the source UI."""

    initial = screenshot if screenshot is not None else capture_adb_screenshot()
    source_state = _reward_source_state(initial)
    reward_hub = _ensure_reward_hub(initial, source_state=source_state)
    if reward_hub is None:
        return MissionRewardResult.FAILED

    badges = (
        measure_menu_reward_badges(reward_hub)
        if source_state == "RUNNING"
        else measure_home_reward_badges(reward_hub)
    )
    log(
        f"[MISSION_REWARDS] Badges source={source_state}: "
        f"daily={badges.daily_missions} event={badges.event_missions} "
        f"guild={badges.guild_chests}",
        "INFO",
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
        reward_hub = _ensure_reward_hub(reward_hub, source_state=source_state)
        if reward_hub is None or navigation is None:
            success = False
            break
        if not tap_if_visible(navigation, screenshot=reward_hub, retries=1):
            log(f"[MISSION_REWARDS] Could not open {name}", "WARN")
            success = False
            break
        panel = _wait_for_state(state)
        if panel is None:
            log(f"[MISSION_REWARDS] {name} identity was not verified", "WARN")
            success = False
            break

        if summary_field == "daily":
            section_success, claimed = claim_fn(
                panel,
                claim_missions=claim_daily_missions,
            )
        elif summary_field == "event":
            section_success, claimed = claim_fn(
                panel,
                inventory_callback=event_inventory_callback,
            )
        else:
            section_success, claimed = claim_fn(panel)
        summary = MissionRewardSummary(
            daily=claimed if summary_field == "daily" else summary.daily,
            event=claimed if summary_field == "event" else summary.event,
            guild=claimed if summary_field == "guild" else summary.guild,
        )
        success = success and section_success

        reward_hub = _return_to_reward_hub(state, source_state=source_state)
        if reward_hub is None:
            success = False
            break

    if (
        source_state == "RUNNING"
        and reward_hub is not None
        and not _close_menu(reward_hub)
    ):
        success = False

    log(
        "[MISSION_REWARDS] Claim summary: "
        f"daily={summary.daily} event={summary.event} guild={summary.guild}",
        "INFO",
    )
    if not success:
        return MissionRewardResult.FAILED
    if summary.total:
        return MissionRewardResult.CLAIMED
    return MissionRewardResult.NOTHING_AVAILABLE


def _claim_daily_rewards(
    screenshot,
    *,
    claim_missions: bool = True,
) -> tuple[bool, int]:
    current = screenshot
    claimed = 0
    ordinary_claimed = 0
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
                "INFO",
            )
        else:
            ordinary_claim_limit = 0
            log(
                "[MISSION_REWARDS] Holding ordinary Daily Mission claims "
                f"until the weekly reset (capacity={capacity_text}, "
                f"OCR confidence={capacity.confidence:.1f})",
                "INFO",
            )

    for _ in range(MAX_DAILY_REWARDS):
        if not _is_state(current, "DAILY_MISSIONS"):
            return False, claimed
        if is_visible(WEEKLY_MISSION_CHEST, screenshot=current):
            if not tap_if_visible(WEEKLY_MISSION_CHEST, screenshot=current):
                return False, claimed
            current = _dismiss_reward_reveal("DAILY_MISSIONS")
            if current is None:
                return False, claimed
            claimed += 1
            continue
        if (
            ordinary_claim_limit is not None
            and ordinary_claimed >= ordinary_claim_limit
        ):
            if ordinary_claimed:
                log(
                    "[MISSION_REWARDS] Sunday capacity relief complete: "
                    f"claimed {ordinary_claimed} ordinary Daily Mission rewards",
                    "INFO",
                )
            return True, claimed
        if is_visible(DAILY_MISSION_CLAIM, screenshot=current):
            if not tap_if_visible(DAILY_MISSION_CLAIM, screenshot=current):
                return False, claimed
            current = _wait_for_state("DAILY_MISSIONS", settle_s=0.6)
            if current is None:
                return False, claimed
            claimed += 1
            ordinary_claimed += 1
            continue
        return True, claimed

    log("[MISSION_REWARDS] Daily reward claim bound reached", "WARN")
    return False, claimed


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
) -> tuple[bool, int]:
    # Event retains its last-selected tab.  Re-enter Missions explicitly so a
    # prior Bots or Event Shop visit cannot be mistaken for the mission list.
    if not _is_state(screenshot, "EVENT"):
        return False, 0
    if not tap_if_visible(EVENT_MISSIONS_TAB, screenshot=screenshot, retries=1):
        log("[MISSION_REWARDS] Could not select the Event Missions tab", "WARN")
        return False, 0
    missions = _wait_for_state("EVENT", settle_s=0.8)
    if missions is None:
        return False, 0

    top = scroll_to_edge(
        "gesture_targets.goto_top:event_missions",
        source_label="indicators.event",
        screenshot=missions,
        progress_region=EVENT_CONTENT_REGION,
        max_swipes=8,
        settle_s=0.8,
        stable_threshold=2.0,
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
        if current is None or not tap_if_visible(EVENT_MISSION_CLAIM, screenshot=current):
            return False, claimed
        current = _wait_for_state("EVENT", settle_s=0.6)
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
            "INFO" if inventory.complete else "WARN",
        )
    except Exception as exc:
        # Reminders are advisory and must not turn an otherwise successful
        # reward claim into a navigation failure.
        log(f"[EVENT_MISSIONS] Inventory failed: {exc}", "WARN")


def _claim_guild_chests(screenshot) -> tuple[bool, int]:
    current = screenshot
    if not _is_state(current, "GUILD"):
        return False, 0
    # Guild retains its last selected tab.  Always reselect Members before
    # interpreting absence of glowing contribution chests as authoritative.
    if not tap_if_visible(
        "navigation.guild:members_tab",
        screenshot=current,
        retries=1,
    ):
        return False, 0
    current = _wait_for_state("GUILD", settle_s=0.8)
    if current is None:
        return False, 0

    claimed = 0
    for _ in range(MAX_GUILD_CHESTS):
        if not _is_state(current, "GUILD"):
            return False, claimed
        if not is_visible(GUILD_CHEST_CLAIM, screenshot=current):
            return True, claimed
        if not tap_if_visible(GUILD_CHEST_CLAIM, screenshot=current):
            return False, claimed
        current = _dismiss_reward_reveal("GUILD")
        if current is None:
            return False, claimed
        claimed += 1

    log("[MISSION_REWARDS] Guild chest claim bound reached", "WARN")
    return False, claimed


def _dismiss_reward_reveal(return_state: str):
    reveal = _wait_for_label(REWARD_REVEAL_SKIP, timeout=8.0)
    if reveal is None:
        log("[MISSION_REWARDS] Reward reveal SKIP was not verified", "WARN")
        return None
    if not tap_if_visible(REWARD_REVEAL_SKIP, screenshot=reveal):
        return None
    return _wait_for_state(return_state, settle_s=0.6)


def _reward_source_state(screenshot) -> Optional[str]:
    if screenshot is None:
        return None
    state = detect_state_and_overlays(screenshot).get("state")
    return state if state in {"RUNNING", "HOME_SCREEN"} else None


def _ensure_reward_hub(screenshot=None, *, source_state: Optional[str]):
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
    if not tap_if_visible("navigation.toggle_menu", screenshot=current):
        return None
    return _wait_for_state("RUNNING", required_overlay="MENU_OPEN", settle_s=0.6)


def _return_to_reward_hub(panel_state: str, *, source_state: str):
    current = capture_adb_screenshot()
    if current is None or not _is_state(current, panel_state):
        log(
            f"[MISSION_REWARDS] Refusing Return to Game from an unverified "
            f"{panel_state} panel",
            "WARN",
        )
        return None
    if not tap_if_visible("buttons.return_to_game", screenshot=current):
        return None
    source = _wait_for_state(source_state, settle_s=0.6)
    if source is None:
        return None
    return _ensure_reward_hub(source, source_state=source_state)


def _close_menu(screenshot) -> bool:
    detection = detect_state_and_overlays(screenshot)
    if detection.get("state") != "RUNNING":
        return False
    overlays = set(detection.get("overlays") or [])
    if "MENU_CLOSED" in overlays:
        return True
    if "MENU_OPEN" not in overlays:
        return False
    if not tap_if_visible("navigation.menu_close_button", screenshot=screenshot):
        return False
    return _wait_for_state(
        "RUNNING",
        required_overlay="MENU_CLOSED",
        settle_s=0.6,
    ) is not None


def _wait_for_state(
    state: str,
    *,
    required_overlay: Optional[str] = None,
    timeout: float = 8.0,
    poll: float = 0.3,
    settle_s: float = 0.0,
):
    if settle_s > 0:
        time.sleep(settle_s)
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
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


def _wait_for_label(label: str, *, timeout: float, poll: float = 0.3):
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        screenshot = capture_adb_screenshot()
        if screenshot is not None and is_visible(label, screenshot=screenshot):
            return screenshot
        time.sleep(max(0.05, poll))
    return None


def _is_state(screenshot, state: str) -> bool:
    return detect_state_and_overlays(screenshot).get("state") == state


__all__ = [
    "MissionRewardResult",
    "MissionRewardSummary",
    "handle_mission_rewards",
]
