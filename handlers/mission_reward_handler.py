"""Screen-guarded Daily Mission, Event Mission, and Guild chest claims."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Optional

from core.input import safe_tap, tap_if_visible
from core.label_tapper import is_visible
from core.menu_reward_badges import measure_menu_reward_badges
from core.scrolling import scroll_to_edge, scroll_until_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log


DAILY_MISSION_CLAIM = "buttons.claim_daily_mission"
WEEKLY_MISSION_CHEST = "buttons.claim_weekly_mission_chest"
EVENT_MISSION_CLAIM = "buttons.claim_event_mission"
GUILD_CHEST_CLAIM = "buttons.claim_guild_chest"
REWARD_REVEAL_SKIP = "buttons.skip_reward_reveal"

EVENT_CONTENT_REGION = (0, 840, 1080, 900)
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


def handle_mission_rewards(screenshot=None) -> MissionRewardResult:
    """Inspect relevant menu badges, claim proven rewards, and resume the run."""

    menu_screen = _ensure_menu_open(screenshot)
    if menu_screen is None:
        return MissionRewardResult.FAILED

    badges = measure_menu_reward_badges(menu_screen)
    log(
        "[MISSION_REWARDS] Menu badges: "
        f"daily={badges.daily_missions} event={badges.event_missions} "
        f"guild={badges.guild_chests}",
        "INFO",
    )

    summary = MissionRewardSummary()
    success = True
    sections = (
        (
            badges.daily_missions,
            "Daily Missions",
            "navigation.menu_daily_missions",
            "DAILY_MISSIONS",
            _claim_daily_rewards,
            "daily",
        ),
        (
            badges.event_missions,
            "Event Missions",
            "navigation.menu_event",
            "EVENT",
            _claim_event_rewards,
            "event",
        ),
        (
            badges.guild_chests,
            "Guild chests",
            "navigation.menu_guild",
            "GUILD",
            _claim_guild_chests,
            "guild",
        ),
    )

    for enabled, name, navigation, state, claim_fn, summary_field in sections:
        if not enabled:
            continue
        menu_screen = _ensure_menu_open(menu_screen)
        if menu_screen is None:
            success = False
            break
        if not safe_tap(navigation, require_visible=False, dispatch="now"):
            log(f"[MISSION_REWARDS] Could not open {name}", "WARN")
            success = False
            break
        panel = _wait_for_state(state)
        if panel is None:
            log(f"[MISSION_REWARDS] {name} identity was not verified", "WARN")
            success = False
            break

        section_success, claimed = claim_fn(panel)
        summary = MissionRewardSummary(
            daily=claimed if summary_field == "daily" else summary.daily,
            event=claimed if summary_field == "event" else summary.event,
            guild=claimed if summary_field == "guild" else summary.guild,
        )
        success = success and section_success

        menu_screen = _return_to_open_menu(state)
        if menu_screen is None:
            success = False
            break

    if menu_screen is not None and not _close_menu(menu_screen):
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


def _claim_daily_rewards(screenshot) -> tuple[bool, int]:
    current = screenshot
    claimed = 0
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
        if is_visible(DAILY_MISSION_CLAIM, screenshot=current):
            if not tap_if_visible(DAILY_MISSION_CLAIM, screenshot=current):
                return False, claimed
            current = _wait_for_state("DAILY_MISSIONS", settle_s=0.6)
            if current is None:
                return False, claimed
            claimed += 1
            continue
        return True, claimed

    log("[MISSION_REWARDS] Daily reward claim bound reached", "WARN")
    return False, claimed


def _claim_event_rewards(screenshot) -> tuple[bool, int]:
    top = scroll_to_edge(
        "gesture_targets.goto_top:event_missions",
        source_label="indicators.event",
        screenshot=screenshot,
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


def _claim_guild_chests(screenshot) -> tuple[bool, int]:
    current = screenshot
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


def _ensure_menu_open(screenshot=None):
    current = screenshot if screenshot is not None else capture_adb_screenshot()
    if current is None:
        return None
    detection = detect_state_and_overlays(current)
    if detection.get("state") != "RUNNING":
        log(
            f"[MISSION_REWARDS] Refusing menu navigation from "
            f"state={detection.get('state')!r}",
            "WARN",
        )
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


def _return_to_open_menu(panel_state: str):
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
    running = _wait_for_state("RUNNING", settle_s=0.6)
    return _ensure_menu_open(running) if running is not None else None


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
