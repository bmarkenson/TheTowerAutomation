from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from core.clickmap_access import get_click
from core.app import App
from core.matcher import get_match
from core.menu_reward_badges import (
    measure_menu_reward_badges,
    menu_reward_alert_visible,
)
from core.mission_reward_scheduler import (
    FAILURE_RETRY_SECONDS,
    MissionRewardScheduler,
    PROBE_COOLDOWN_SECONDS,
)
import handlers.mission_reward_handler as rewards
from handlers.mission_reward_handler import MissionRewardResult


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"


def _load(name: str):
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None, name
    return image


def test_closed_menu_attention_dot_and_open_menu_section_badges_are_distinct():
    closed_red = _load("running_menu_reward_alert_20260715.png")
    closed_purple = _load("running_menu_unrelated_alert_20260715.png")
    opened = _load("running_menu_reward_badges_20260715.png")
    opened_after_claims = _load("running_menu_no_reward_badges_20260715.png")

    assert menu_reward_alert_visible(closed_red)
    assert menu_reward_alert_visible(closed_purple)
    assert not menu_reward_alert_visible(opened)

    badges = measure_menu_reward_badges(opened)
    assert badges.daily_missions
    assert badges.event_missions
    assert not badges.guild_chests
    assert badges.any

    post_claim_badges = measure_menu_reward_badges(opened_after_claims)
    assert not post_claim_badges.daily_missions
    assert not post_claim_badges.event_missions
    assert not post_claim_badges.guild_chests
    assert not post_claim_badges.any


def test_daily_claim_and_weekly_chest_templates_require_available_artwork():
    available = _load("daily_rewards_claimable_20260715.png")
    claimed = _load("daily_weekly_chest_claimed_20260715.png")

    mission_point, mission_confidence = get_match(
        "buttons.claim_daily_mission",
        screenshot=available,
    )
    chest_point, chest_confidence = get_match(
        "buttons.claim_weekly_mission_chest",
        screenshot=available,
    )
    claimed_point, claimed_confidence = get_match(
        "buttons.claim_weekly_mission_chest",
        screenshot=claimed,
    )

    assert mission_point == (529, 778)
    assert mission_confidence >= 0.99
    assert chest_point == (738, 370)
    assert chest_confidence >= 0.99
    assert claimed_point is None
    assert claimed_confidence < 0.9


def test_event_claim_template_has_positive_and_incomplete_negative_evidence():
    positive = _load("event_missions_claimable_20260715.png")
    negative = _load("event_missions_20260713.png")

    point, confidence = get_match("buttons.claim_event_mission", screenshot=positive)
    negative_point, negative_confidence = get_match(
        "buttons.claim_event_mission",
        screenshot=negative,
    )

    assert point == (349, 1213)
    assert confidence >= 0.99
    assert negative_point is None
    assert negative_confidence < 0.9


def test_guild_chest_template_separates_glowing_from_claimed_and_locked():
    available = _load("guild_members_chest_20260713.png")
    unavailable = _load("guild_members_chests_claimed_20260715.png")

    point, confidence = get_match("buttons.claim_guild_chest", screenshot=available)
    unavailable_point, unavailable_confidence = get_match(
        "buttons.claim_guild_chest",
        screenshot=unavailable,
    )

    assert point == (375, 705)
    assert confidence >= 0.99
    assert unavailable_point is None
    assert unavailable_confidence < 0.9


def test_reward_reveal_uses_shared_skip_control():
    point, confidence = get_match(
        "buttons.skip_reward_reveal",
        screenshot=_load("reward_reveal_skip_20260715.png"),
    )

    assert point == (899, 53)
    assert confidence >= 0.99


def test_menu_reward_navigation_coordinates_are_mapped():
    assert get_click("navigation.menu_daily_missions") == (910, 174)
    assert get_click("navigation.menu_event") == (910, 484)
    assert get_click("navigation.menu_guild") == (910, 589)


def test_scheduler_bounds_persistent_attention_and_failed_attempts():
    scheduler = MissionRewardScheduler()

    assert not scheduler.should_attempt(alert_visible=False, now=100.0)
    assert scheduler.should_attempt(alert_visible=True, now=100.0)
    scheduler.mark_completed(now=100.0)
    assert not scheduler.should_attempt(
        alert_visible=True,
        now=100.0 + PROBE_COOLDOWN_SECONDS - 0.1,
    )
    assert scheduler.should_attempt(
        alert_visible=True,
        now=100.0 + PROBE_COOLDOWN_SECONDS,
    )

    scheduler.mark_failed(now=1000.0)
    assert not scheduler.should_attempt(
        alert_visible=True,
        now=1000.0 + FAILURE_RETRY_SECONDS - 0.1,
    )
    assert scheduler.should_attempt(
        alert_visible=True,
        now=1000.0 + FAILURE_RETRY_SECONDS,
    )


def test_daily_claim_loop_revalidates_before_mission_and_chest_actions():
    initial = np.zeros((2, 2, 3), dtype=np.uint8)
    after_mission = np.ones((2, 2, 3), dtype=np.uint8)
    after_chest = np.full((2, 2, 3), 2, dtype=np.uint8)

    def visible(label, *, screenshot):
        value = int(screenshot[0, 0, 0])
        if label == rewards.WEEKLY_MISSION_CHEST:
            return value == 1
        if label == rewards.DAILY_MISSION_CLAIM:
            return value == 0
        return False

    with (
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(rewards, "is_visible", side_effect=visible),
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(rewards, "_wait_for_state", return_value=after_mission),
        patch.object(rewards, "_dismiss_reward_reveal", return_value=after_chest),
    ):
        success, claimed = rewards._claim_daily_rewards(initial)

    assert success
    assert claimed == 2
    assert [call.args[0] for call in tap.call_args_list] == [
        rewards.DAILY_MISSION_CLAIM,
        rewards.WEEKLY_MISSION_CHEST,
    ]


def test_app_dispatches_alert_probe_and_records_success():
    app = App.__new__(App)
    app._mission_reward_scheduler = Mock()
    app._mission_reward_scheduler.should_attempt.return_value = True
    app._blind_tapper_suspended = False
    screenshot = np.zeros((2, 2, 3), dtype=np.uint8)

    with (
        patch("core.app.menu_reward_alert_visible", return_value=True),
        patch("core.app.stop_blind_gem_tapper", return_value=True),
        patch(
            "core.app.handle_mission_rewards",
            return_value=MissionRewardResult.CLAIMED,
        ) as handler,
    ):
        assert app._handle_mission_rewards_if_due("RUNNING", screenshot)

    app._mission_reward_scheduler.should_attempt.assert_called_once_with(
        alert_visible=True,
    )
    handler.assert_called_once_with(screenshot=screenshot)
    app._mission_reward_scheduler.mark_completed.assert_called_once_with()
    app._mission_reward_scheduler.mark_failed.assert_not_called()
    assert app._blind_tapper_suspended


def test_app_defers_reward_probe_from_unsafe_screen():
    app = App.__new__(App)
    app._mission_reward_scheduler = Mock()

    assert not app._handle_mission_rewards_if_due(
        "DAILY_MISSIONS",
        np.zeros((2, 2, 3), dtype=np.uint8),
    )
    app._mission_reward_scheduler.should_attempt.assert_not_called()
