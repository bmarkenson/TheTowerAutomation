from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import cv2
import numpy as np

from core.app import App
from core.clickmap_access import get_swipe
from core.event_mission_tracker import EventMissionWarning
from core.matcher import get_match
from core.menu_reward_badges import (
    MenuRewardBadges,
    measure_home_reward_badges,
    measure_menu_reward_badges,
    menu_reward_alert_visible,
)
from core.mission_reward_scheduler import (
    FAILURE_RETRY_SECONDS,
    MissionRewardScheduler,
    PROBE_COOLDOWN_SECONDS,
    WeeklyChestReviewState,
    daily_mission_claims_allowed,
    seconds_until_daily_mission_release,
)
from core.scrolling import ScrollResult
import handlers.mission_reward_handler as rewards
from handlers.mission_reward_handler import (
    MissionRewardCleanupResult,
    MissionRewardResult,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"


def _load(name: str):
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None, name
    return image


def _weekly_found(
    frame,
    *,
    swipes: int = 0,
    left_search_complete: bool = False,
) -> rewards.WeeklyChestSearchResult:
    return rewards.WeeklyChestSearchResult(
        True,
        frame,
        swipes,
        "target_visible",
        left_search_complete,
    )


def _weekly_absent(
    frame,
    *,
    swipes: int = 0,
    left_search_complete: bool = False,
) -> rewards.WeeklyChestSearchResult:
    return rewards.WeeklyChestSearchResult(
        False,
        frame,
        swipes,
        "edge_before_target",
        left_search_complete,
    )


def test_authority_loss_before_reward_navigation_retains_cleanup_ownership():
    screenshot = np.zeros((2, 2, 3), dtype=np.uint8)
    route_state = Mock()
    badges = MenuRewardBadges(True, False, False)
    with (
        patch.object(rewards, "_reward_source_state", return_value="RUNNING"),
        patch.object(rewards, "_ensure_reward_hub", return_value=screenshot),
        patch.object(rewards, "measure_menu_reward_badges", return_value=badges),
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(rewards, "tap_if_visible") as tap,
    ):
        result = rewards.handle_mission_rewards(
            screenshot=screenshot,
            action_guard_fn=lambda: False,
            route_state_callback=route_state,
        )

    assert result is MissionRewardResult.INTERRUPTED
    tap.assert_not_called()
    assert route_state.call_args.args[:2] == ("RUNNING", True)


def test_mission_cleanup_abandons_unexpected_boundary_without_input():
    screenshot = np.zeros((2, 2, 3), dtype=np.uint8)
    with (
        patch.object(rewards, "capture_adb_screenshot", return_value=screenshot),
        patch.object(
            rewards,
            "detect_state_and_overlays",
            return_value={"state": "GAME_OVER", "overlays": []},
        ),
        patch.object(rewards, "is_visible", return_value=False),
        patch.object(rewards, "tap_if_visible") as tap,
    ):
        result = rewards.resume_mission_reward_cleanup(
            "RUNNING",
            "EVENT",
            action_guard_fn=lambda: True,
        )

    assert result is MissionRewardCleanupResult.ABANDONED
    tap.assert_not_called()


def test_guarded_menu_cleanup_recaptures_and_refuses_game_over():
    stale_menu = np.zeros((2, 2, 3), dtype=np.uint8)
    game_over = np.ones((2, 2, 3), dtype=np.uint8)
    with (
        patch.object(
            rewards,
            "capture_adb_screenshot",
            return_value=game_over,
        ) as capture,
        patch.object(
            rewards,
            "detect_state_and_overlays",
            return_value={"state": "GAME_OVER", "overlays": []},
        ),
        patch.object(rewards, "tap_if_visible") as tap,
    ):
        assert not rewards._close_menu(
            stale_menu,
            action_guard_fn=lambda: True,
        )

    capture.assert_called_once_with()
    tap.assert_not_called()


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


def test_guild_badge_tracks_guild_icon_when_tournament_displaces_it():
    positive = _load("running_menu_tournament_guild_badge_20260722.png")
    negative = _load("running_menu_tournament_trophy_20260718.png")

    badges = measure_menu_reward_badges(positive)
    assert not badges.daily_missions
    assert not badges.event_missions
    assert badges.guild_chests
    assert badges.any

    no_badges = measure_menu_reward_badges(negative)
    assert not no_badges.daily_missions
    assert not no_badges.event_missions
    assert not no_badges.guild_chests
    assert not no_badges.any


def test_home_daily_and_event_badges_have_distinct_positive_and_negative_evidence():
    positive = _load("home_screen_new_day_store_badge_20260713.png")
    negative = _load("home_screen_no_reward_badges_20260714.png")

    badges = measure_home_reward_badges(positive)
    assert badges.daily_missions
    assert badges.event_missions
    assert not badges.guild_chests
    assert badges.any

    no_badges = measure_home_reward_badges(negative)
    assert not no_badges.daily_missions
    assert not no_badges.event_missions
    assert not no_badges.guild_chests
    assert not no_badges.any


def test_home_daily_mission_navigation_uses_static_header_artwork():
    for name in (
        "home_screen_new_day_store_badge_20260713.png",
        "home_screen_no_reward_badges_20260714.png",
    ):
        point, confidence = get_match(
            "navigation.home_daily_missions",
            screenshot=_load(name),
        )
        assert point == (1006, 214)
        assert confidence >= 0.99


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


def test_weekly_track_evidence_requires_every_unlocked_chest_checkmark():
    claimed = rewards._measure_weekly_mission_track(
        _load("daily_weekly_chest_claimed_20260715.png")
    )
    available = rewards._measure_weekly_mission_track(
        _load("daily_rewards_claimable_20260715.png")
    )
    partially_visible = rewards._measure_weekly_mission_track(
        _load("daily_missions_full_20260719.png")
    )
    unknown = rewards._measure_weekly_mission_track(
        np.zeros((2, 2, 3), dtype=np.uint8)
    )

    assert (claimed.completed, claimed.total) == (20, 35)
    assert claimed.confidence >= 60.0
    assert claimed.checkmarks == 4
    assert claimed.claimed_milestones == (5, 10, 15, 20)
    assert claimed.unlocked_chests == 4
    assert claimed.all_unlocked_claimed
    assert not claimed.visible_claimed_prefix

    assert (available.completed, available.total) == (20, 35)
    assert available.checkmarks == 3
    assert available.claimed_milestones == (5, 10, 15)
    assert not available.all_unlocked_claimed
    assert available.visible_claimed_prefix

    assert (partially_visible.completed, partially_visible.total) == (35, 35)
    assert partially_visible.checkmarks == 5
    assert partially_visible.claimed_milestones == (5, 10, 15, 20, 25)
    assert partially_visible.unlocked_chests == 7
    assert not partially_visible.all_unlocked_claimed
    assert partially_visible.visible_claimed_prefix

    assert unknown.unlocked_chests is None
    assert not unknown.all_unlocked_claimed
    assert not unknown.visible_claimed_prefix
    assert not rewards.WeeklyMissionTrackEvidence(
        20,
        35,
        4,
        rewards.WEEKLY_MISSION_PROGRESS_MIN_CONFIDENCE - 0.1,
    ).all_unlocked_claimed
    assert not rewards.WeeklyMissionTrackEvidence(
        20,
        40,
        4,
        99.0,
    ).all_unlocked_claimed
    assert not rewards.WeeklyMissionTrackEvidence(
        35,
        35,
        3,
        99.0,
        "completed 35/35",
        (5, 15, 20),
    ).visible_claimed_prefix
    assert not rewards.WeeklyMissionTrackEvidence(
        35,
        35,
        3,
        99.0,
        "completed 35/35",
        (10, 15, 20),
    ).visible_claimed_prefix


def test_daily_mission_capacity_ocr_distinguishes_full_from_partial():
    full = rewards._read_daily_mission_capacity(
        _load("daily_missions_full_20260719.png")
    )
    partial = rewards._read_daily_mission_capacity(
        _load("daily_rewards_claimable_20260715.png")
    )
    unknown = rewards._read_daily_mission_capacity(
        np.zeros((2, 2, 3), dtype=np.uint8)
    )

    assert (full.current, full.limit) == (8, 8)
    assert full.confidence >= 90.0
    assert full.is_authoritative_full
    assert (partial.current, partial.limit) == (4, 8)
    assert partial.confidence >= 90.0
    assert not partial.is_authoritative_full
    assert (unknown.current, unknown.limit) == (None, None)
    assert not unknown.is_authoritative_full
    assert not rewards.DailyMissionCapacity(
        8,
        8,
        rewards.DAILY_MISSION_CAPACITY_MIN_CONFIDENCE - 0.1,
        "8/8 Missions",
    ).is_authoritative_full


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


def test_event_mission_search_uses_short_overlapping_scroll_steps():
    assert get_swipe("gesture_targets.goto_next:event_missions") == {
        "x1": 540,
        "y1": 1600,
        "x2": 540,
        "y2": 1350,
        "duration_ms": 250,
    }


def test_weekly_chest_search_uses_bounded_horizontal_swipes():
    assert get_swipe("gesture_targets.goto_first:weekly_mission_chests") == {
        "x1": 250,
        "y1": 390,
        "x2": 950,
        "y2": 390,
        "duration_ms": 300,
    }
    assert get_swipe("gesture_targets.goto_next:weekly_mission_chests") == {
        "x1": 900,
        "y1": 390,
        "x2": 650,
        "y2": 390,
        "duration_ms": 250,
    }


def test_weekly_chest_search_normalizes_then_finds_offscreen_target():
    initial = np.zeros((2, 2, 3), dtype=np.uint8)
    first = np.ones((2, 2, 3), dtype=np.uint8)
    found = np.full((2, 2, 3), 2, dtype=np.uint8)
    normalized = ScrollResult(True, first, 2, "edge_reached")
    searched = ScrollResult(True, found, 3, "target_visible")

    with (
        patch.object(rewards, "is_visible", side_effect=[False, False]),
        patch.object(
            rewards,
            "_measure_weekly_mission_track",
            return_value=rewards.WeeklyMissionTrackEvidence(
                20,
                35,
                3,
                95.0,
                "completed 20/35",
            ),
        ),
        patch.object(
            rewards,
            "scroll_to_edge",
            return_value=normalized,
        ) as to_edge,
        patch.object(
            rewards,
            "scroll_until_visible",
            return_value=searched,
        ) as until_visible,
    ):
        result = rewards._find_weekly_mission_chest(initial)

    assert result == rewards.WeeklyChestSearchResult(
        True,
        found,
        5,
        "target_visible",
        True,
    )
    to_edge.assert_called_once_with(
        "gesture_targets.goto_first:weekly_mission_chests",
        source_label="indicators.daily_missions",
        screenshot=initial,
        progress_region=rewards.WEEKLY_MISSION_CHEST_REGION,
        max_swipes=4,
        settle_s=0.8,
        stable_threshold=2.0,
    )
    until_visible.assert_called_once_with(
        "gesture_targets.goto_next:weekly_mission_chests",
        source_label="indicators.daily_missions",
        target_label=rewards.WEEKLY_MISSION_CHEST,
        screenshot=first,
        progress_region=rewards.WEEKLY_MISSION_CHEST_REGION,
        max_swipes=8,
        settle_s=0.8,
        stable_threshold=2.0,
    )


def test_weekly_chest_search_skips_rewind_for_fully_claimed_visible_track():
    claimed = _load("daily_weekly_chest_claimed_20260715.png")

    with (
        patch.object(rewards, "scroll_to_edge") as to_edge,
        patch.object(rewards, "scroll_until_visible") as until_visible,
    ):
        result = rewards._find_weekly_mission_chest(claimed)

    assert not result.success
    assert result.screenshot is claimed
    assert result.swipes == 0
    assert result.reason == "all_unlocked_claimed"
    to_edge.assert_not_called()
    until_visible.assert_not_called()


def test_weekly_chest_search_starts_right_for_visible_claimed_prefix():
    initial = _load("daily_missions_full_20260719.png")
    final = np.zeros((2, 2, 3), dtype=np.uint8)
    searched = ScrollResult(False, final, 3, "edge_before_target")
    action_guard = Mock(return_value=True)

    with (
        patch.object(rewards, "scroll_to_edge") as to_edge,
        patch.object(
            rewards,
            "scroll_until_visible",
            return_value=searched,
        ) as until_visible,
    ):
        result = rewards._find_weekly_mission_chest(
            initial,
            action_guard_fn=action_guard,
        )

    assert result == rewards.WeeklyChestSearchResult(
        False,
        final,
        3,
        "edge_before_target",
        True,
    )
    to_edge.assert_not_called()
    until_visible.assert_called_once_with(
        "gesture_targets.goto_next:weekly_mission_chests",
        source_label="indicators.daily_missions",
        target_label=rewards.WEEKLY_MISSION_CHEST,
        screenshot=initial,
        progress_region=rewards.WEEKLY_MISSION_CHEST_REGION,
        max_swipes=8,
        settle_s=0.8,
        stable_threshold=2.0,
        action_guard_fn=action_guard,
    )


def test_weekly_chest_search_skips_repeat_scan_for_unchanged_reviewed_progress():
    initial = np.zeros((2, 2, 3), dtype=np.uint8)
    review_state = WeeklyChestReviewState()
    review_state.mark_reviewed(7)
    shifted_claimed_suffix = rewards.WeeklyMissionTrackEvidence(
        35,
        35,
        5,
        95.0,
        "completed 35/35",
        (15, 20, 25, 30, 35),
    )

    with (
        patch.object(rewards, "is_visible", return_value=False),
        patch.object(
            rewards,
            "_measure_weekly_mission_track",
            return_value=shifted_claimed_suffix,
        ),
        patch.object(rewards, "scroll_to_edge") as to_edge,
        patch.object(rewards, "scroll_until_visible") as until_visible,
    ):
        result = rewards._find_weekly_mission_chest(
            initial,
            review_state=review_state,
        )

    assert result == rewards.WeeklyChestSearchResult(
        False,
        initial,
        0,
        "weekly_progress_already_reviewed",
        True,
    )
    to_edge.assert_not_called()
    until_visible.assert_not_called()


def test_weekly_chest_search_retains_complete_scan_for_same_unlock_level():
    initial = np.zeros((2, 2, 3), dtype=np.uint8)
    left_edge = np.ones((2, 2, 3), dtype=np.uint8)
    right_edge = np.full((2, 2, 3), 2, dtype=np.uint8)
    review_state = WeeklyChestReviewState()

    with (
        patch.object(rewards, "is_visible", side_effect=[False, False]),
        patch.object(
            rewards,
            "_measure_weekly_mission_track",
            return_value=rewards.WeeklyMissionTrackEvidence(
                35,
                35,
                5,
                95.0,
                "completed 35/35",
                (15, 20, 25, 30, 35),
            ),
        ),
        patch.object(
            rewards,
            "scroll_to_edge",
            return_value=ScrollResult(True, left_edge, 3, "edge_reached"),
        ),
        patch.object(
            rewards,
            "scroll_until_visible",
            return_value=ScrollResult(
                False,
                right_edge,
                3,
                "edge_before_target",
            ),
        ),
    ):
        result = rewards._find_weekly_mission_chest(
            initial,
            review_state=review_state,
        )

    assert not result.success
    assert result.left_search_complete
    assert review_state.covers(7)


def test_event_missions_tab_navigation_is_visible_from_retained_bots_tab():
    bots = _load("event_bots_farm_inactive_20260715.png")

    point, confidence = get_match(rewards.EVENT_MISSIONS_TAB, screenshot=bots)

    assert point == (169, 309)
    assert confidence >= 0.99


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


def test_guild_chest_template_covers_rightmost_750_slot():
    template = cv2.imread(
        str(ROOT / "assets" / "match_templates" / "buttons" / "claim_guild_chest.png")
    )
    assert template is not None
    screenshot = np.zeros((1920, 1080, 3), dtype=np.uint8)
    screenshot[660:750, 970:1060] = template

    point, confidence = get_match("buttons.claim_guild_chest", screenshot=screenshot)

    assert point == (1015, 705)
    assert confidence >= 0.99


def test_reward_reveal_uses_shared_skip_control():
    point, confidence = get_match(
        "buttons.skip_reward_reveal",
        screenshot=_load("reward_reveal_skip_20260715.png"),
    )

    assert point == (899, 53)
    assert confidence >= 0.99


def test_menu_navigation_matches_actual_buttons_with_and_without_trophy():
    normal = _load("running_menu_reward_badges_20260715.png")
    tournament = _load("running_menu_tournament_trophy_20260718.png")

    expected = {
        "navigation.menu_daily_missions": ((910, 172), (910, 172)),
        "navigation.menu_modules": ((1015, 380), (1015, 380)),
        "navigation.menu_event": ((910, 484), (910, 484)),
        "navigation.menu_guild": ((910, 588), (1015, 693)),
    }
    for key, (normal_point, tournament_point) in expected.items():
        match, confidence = get_match(key, screenshot=normal)
        tournament_match, tournament_confidence = get_match(
            key,
            screenshot=tournament,
        )
        assert match == normal_point
        assert confidence >= 0.9
        assert tournament_match == tournament_point
        assert tournament_confidence >= 0.9


def test_guild_claim_reselects_members_before_matching_retained_tab():
    guardian = np.zeros((2, 2, 3), dtype=np.uint8)
    members = np.ones((2, 2, 3), dtype=np.uint8)
    after_claim = np.full((2, 2, 3), 2, dtype=np.uint8)

    def visible(label, *, screenshot):
        return (
            label == rewards.GUILD_CHEST_CLAIM
            and int(screenshot[0, 0, 0]) == 1
        )

    with (
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(rewards, "_wait_for_state", return_value=members) as wait,
        patch.object(rewards, "is_visible", side_effect=visible),
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(rewards, "_dismiss_reward_reveal", return_value=after_claim),
    ):
        success, claimed = rewards._claim_guild_chests(guardian)

    assert success
    assert claimed == 1
    wait.assert_called_once_with("GUILD", settle_s=0.8)
    assert tap.call_args_list[0].args == ("navigation.guild:members_tab",)
    assert tap.call_args_list[0].kwargs == {
        "screenshot": guardian,
        "retries": 1,
    }
    assert tap.call_args_list[1].args == (rewards.GUILD_CHEST_CLAIM,)
    assert tap.call_args_list[1].kwargs == {"screenshot": members}


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


def test_daily_mission_claims_release_at_monday_utc_reset():
    before_pdt = datetime(2026, 7, 19, 23, 59, 59, tzinfo=timezone.utc)
    reset_pdt = datetime(2026, 7, 20, 0, 0, 0, tzinfo=timezone.utc)
    before_pst = datetime(2026, 12, 6, 23, 59, 59, tzinfo=timezone.utc)
    reset_pst = datetime(2026, 12, 7, 0, 0, 0, tzinfo=timezone.utc)

    assert not daily_mission_claims_allowed(before_pdt)
    assert daily_mission_claims_allowed(reset_pdt)
    assert not daily_mission_claims_allowed(before_pst)
    assert daily_mission_claims_allowed(reset_pst)
    assert seconds_until_daily_mission_release(before_pdt) == 1.0
    assert seconds_until_daily_mission_release(reset_pdt) is None


def test_daily_mission_claims_are_not_held_outside_local_sunday():
    saturday = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
    monday_local = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)

    assert daily_mission_claims_allowed(saturday)
    assert daily_mission_claims_allowed(monday_local)


def test_scheduler_cooldown_does_not_straddle_weekly_reset():
    scheduler = MissionRewardScheduler()
    before_reset = datetime(2026, 7, 19, 23, 59, 50, tzinfo=timezone.utc)

    scheduler.mark_completed(now=100.0, wall_now=before_reset)
    assert not scheduler.should_attempt(alert_visible=True, now=109.9)
    assert scheduler.should_attempt(alert_visible=True, now=110.0)


def test_weekly_chest_review_state_expires_on_progress_or_cycle_change():
    state = WeeklyChestReviewState()
    sunday = datetime(2026, 8, 9, 23, 59, 59, tzinfo=timezone.utc)
    monday = datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)

    state.mark_reviewed(6, now=sunday)
    assert state.covers(6, now=sunday)
    assert not state.covers(7, now=sunday)
    assert not state.covers(6, now=sunday)

    state.mark_reviewed(7, now=sunday)
    assert not state.covers(7, now=monday)


def test_daily_claim_loop_drains_missions_before_searching_for_chests():
    initial = np.zeros((2, 2, 3), dtype=np.uint8)
    after_first_mission = np.ones((2, 2, 3), dtype=np.uint8)
    after_missions = np.full((2, 2, 3), 2, dtype=np.uint8)
    after_chest = np.full((2, 2, 3), 3, dtype=np.uint8)

    def visible(label, *, screenshot):
        value = int(screenshot[0, 0, 0])
        if label == rewards.DAILY_MISSION_CLAIM:
            return value < 2
        return False

    with (
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(rewards, "is_visible", side_effect=visible),
        patch.object(
            rewards,
            "_find_weekly_mission_chest",
            side_effect=[
                _weekly_found(after_missions),
                _weekly_absent(after_chest),
            ],
        ) as find_weekly,
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(
            rewards,
            "_wait_for_state",
            side_effect=[after_first_mission, after_missions],
        ),
        patch.object(rewards, "_dismiss_reward_reveal", return_value=after_chest),
        patch.object(rewards, "log") as reward_log,
    ):
        success, claimed = rewards._claim_daily_rewards(initial)

    assert success
    assert claimed == 3
    assert [call.args[0] for call in tap.call_args_list] == [
        rewards.DAILY_MISSION_CLAIM,
        rewards.DAILY_MISSION_CLAIM,
        rewards.WEEKLY_MISSION_CHEST,
    ]
    assert [
        call.args[0] for call in find_weekly.call_args_list
    ] == [after_missions, after_chest]
    debug_messages = [
        entry.args[0]
        for entry in reward_log.call_args_list
        if entry.args[1] == "DEBUG"
    ]
    assert len(debug_messages) == 5
    assert "Daily Mission rewards were claimed first" in debug_messages[0]
    assert "previous Weekly Mission chest was claimed" in debug_messages[2]
    assert "Weekly chest review complete" in debug_messages[4]
    operational_messages = [
        entry.args
        for entry in reward_log.call_args_list
        if entry.args[1] != "DEBUG"
    ]
    assert operational_messages == []


def test_sunday_hold_still_claims_glowing_weekly_chest():
    initial = np.zeros((2, 2, 3), dtype=np.uint8)
    after_chest = np.ones((2, 2, 3), dtype=np.uint8)

    def visible(label, *, screenshot):
        return (
            label == rewards.WEEKLY_MISSION_CHEST
            and int(screenshot[0, 0, 0]) == 0
        )

    with (
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(rewards, "is_visible", side_effect=visible),
        patch.object(
            rewards,
            "_find_weekly_mission_chest",
            side_effect=[
                _weekly_found(initial),
                _weekly_absent(after_chest),
            ],
        ),
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(rewards, "_dismiss_reward_reveal", return_value=after_chest),
    ):
        success, claimed = rewards._claim_daily_rewards(
            initial,
            claim_missions=False,
        )

    assert success
    assert claimed == 1
    tap.assert_called_once_with(rewards.WEEKLY_MISSION_CHEST, screenshot=initial)


def test_sunday_hold_does_not_tap_ordinary_daily_claim():
    screenshot = np.zeros((2, 2, 3), dtype=np.uint8)

    def visible(label, *, screenshot):
        return label == rewards.DAILY_MISSION_CLAIM

    with (
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(
            rewards,
            "_read_daily_mission_capacity",
            return_value=rewards.DailyMissionCapacity(6, 8, 95.0, "6/8 Missions"),
        ),
        patch.object(
            rewards,
            "_find_weekly_mission_chest",
            return_value=_weekly_absent(screenshot),
        ),
        patch.object(rewards, "is_visible", side_effect=visible),
        patch.object(rewards, "tap_if_visible") as tap,
    ):
        success, claimed = rewards._claim_daily_rewards(
            screenshot,
            claim_missions=False,
        )

    assert success
    assert claimed == 0
    tap.assert_not_called()


def test_sunday_full_capacity_claims_exactly_two_ordinary_rewards():
    initial = np.zeros((2, 2, 3), dtype=np.uint8)
    after_first = np.ones((2, 2, 3), dtype=np.uint8)
    after_second = np.full((2, 2, 3), 2, dtype=np.uint8)

    def visible(label, *, screenshot):
        return label == rewards.DAILY_MISSION_CLAIM

    with (
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(
            rewards,
            "_read_daily_mission_capacity",
            return_value=rewards.DailyMissionCapacity(8, 8, 95.0, "8/8 Missions"),
        ),
        patch.object(
            rewards,
            "_find_weekly_mission_chest",
            return_value=_weekly_absent(after_second),
        ) as find_weekly,
        patch.object(rewards, "is_visible", side_effect=visible),
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(
            rewards,
            "_wait_for_state",
            side_effect=[after_first, after_second],
        ),
    ):
        success, claimed = rewards._claim_daily_rewards(
            initial,
            claim_missions=False,
        )

    assert success
    assert claimed == 2
    assert tap.call_count == 2
    assert [call.args[0] for call in tap.call_args_list] == [
        rewards.DAILY_MISSION_CLAIM,
        rewards.DAILY_MISSION_CLAIM,
    ]
    find_weekly.assert_called_once_with(after_second, action_guard_fn=None)


def test_sunday_weekly_chest_does_not_consume_full_capacity_claim_budget():
    initial = np.zeros((2, 2, 3), dtype=np.uint8)
    after_chest = np.ones((2, 2, 3), dtype=np.uint8)
    after_first = np.full((2, 2, 3), 2, dtype=np.uint8)
    after_second = np.full((2, 2, 3), 3, dtype=np.uint8)

    def visible(label, *, screenshot):
        value = int(screenshot[0, 0, 0])
        if label == rewards.WEEKLY_MISSION_CHEST:
            return value == 0
        return label == rewards.DAILY_MISSION_CLAIM and value > 0

    with (
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(
            rewards,
            "_read_daily_mission_capacity",
            return_value=rewards.DailyMissionCapacity(8, 8, 95.0, "8/8 Missions"),
        ),
        patch.object(
            rewards,
            "_find_weekly_mission_chest",
            side_effect=[
                _weekly_found(initial),
                _weekly_absent(after_second),
            ],
        ),
        patch.object(rewards, "is_visible", side_effect=visible),
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(
            rewards,
            "_dismiss_reward_reveal",
            return_value=after_chest,
        ),
        patch.object(
            rewards,
            "_wait_for_state",
            side_effect=[after_first, after_second],
        ),
    ):
        success, claimed = rewards._claim_daily_rewards(
            initial,
            claim_missions=False,
        )

    assert success
    assert claimed == 3
    assert [call.args[0] for call in tap.call_args_list] == [
        rewards.WEEKLY_MISSION_CHEST,
        rewards.DAILY_MISSION_CLAIM,
        rewards.DAILY_MISSION_CLAIM,
    ]


def test_daily_claim_uses_fresh_frame_found_by_offscreen_weekly_search():
    initial = np.zeros((2, 2, 3), dtype=np.uint8)
    found = np.ones((2, 2, 3), dtype=np.uint8)
    after_chest = np.full((2, 2, 3), 2, dtype=np.uint8)

    with (
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(
            rewards,
            "_find_weekly_mission_chest",
            side_effect=[
                _weekly_found(
                    found,
                    swipes=3,
                    left_search_complete=True,
                ),
                _weekly_absent(after_chest, swipes=5),
            ],
        ) as find_weekly,
        patch.object(rewards, "is_visible", return_value=False),
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(rewards, "_dismiss_reward_reveal", return_value=after_chest),
    ):
        success, claimed = rewards._claim_daily_rewards(initial)

    assert success
    assert claimed == 1
    tap.assert_called_once_with(rewards.WEEKLY_MISSION_CHEST, screenshot=found)
    assert find_weekly.call_args_list[1].kwargs == {
        "action_guard_fn": None,
        "left_search_complete": True,
    }


def test_app_dispatches_alert_probe_and_records_success():
    app = App.__new__(App)
    app._mission_reward_scheduler = Mock()
    app._mission_reward_scheduler.should_attempt.return_value = True
    app._weekly_chest_review_state = WeeklyChestReviewState()
    app._event_mission_tracker = Mock()
    app._blind_tapper_suspended = False
    app._authority_battle_active = True
    app._authority_primary_state = "RUNNING"
    app._authority_holds = ()
    app._supervisor = Mock(is_paused=False)
    app._supervisor.apply_control.return_value = False
    app._status_reporter = Mock()
    screenshot = np.zeros((2, 2, 3), dtype=np.uint8)

    with (
        patch("core.app.menu_reward_alert_visible", return_value=True),
        patch("core.app.daily_mission_claims_allowed", return_value=False),
        patch("core.app.stop_blind_gem_tapper", return_value=True),
        patch(
            "core.app.handle_mission_rewards",
            return_value=MissionRewardResult.CLAIMED,
        ) as handler,
    ):
        assert app._handle_mission_rewards_if_due(
            "RUNNING",
            screenshot,
            {"MENU_CLOSED"},
        )

    app._mission_reward_scheduler.should_attempt.assert_called_once_with(
        alert_visible=True,
    )
    handler.assert_called_once()
    handler_kwargs = handler.call_args.kwargs
    assert handler_kwargs["screenshot"] is screenshot
    assert handler_kwargs["claim_daily_missions"] is False
    assert (
        handler_kwargs["event_inventory_callback"]
        == app._event_mission_tracker.record_inventory
    )
    assert (
        handler_kwargs["weekly_review_state"]
        is app._weekly_chest_review_state
    )
    assert callable(handler_kwargs["action_guard_fn"])
    assert callable(handler_kwargs["route_state_callback"])
    assert app._mission_reward_scheduler.mark_completed.call_count == 1
    wall_now = app._mission_reward_scheduler.mark_completed.call_args.kwargs["wall_now"]
    assert wall_now.tzinfo is timezone.utc
    app._mission_reward_scheduler.mark_failed.assert_not_called()
    assert app._blind_tapper_suspended


def test_app_dispatches_reward_probe_from_verified_open_menu_badge():
    app = App.__new__(App)
    app._mission_reward_scheduler = Mock()
    app._mission_reward_scheduler.should_attempt.return_value = True
    app._event_mission_tracker = Mock()
    app._blind_tapper_suspended = False
    app._authority_battle_active = True
    app._authority_primary_state = "RUNNING"
    app._authority_holds = ()
    app._supervisor = Mock(is_paused=False)
    app._supervisor.apply_control.return_value = False
    app._status_reporter = Mock()
    screenshot = _load("running_menu_reward_badges_20260715.png")

    with (
        patch("core.app.daily_mission_claims_allowed", return_value=True),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch(
            "core.app.handle_mission_rewards",
            return_value=MissionRewardResult.CLAIMED,
        ) as handler,
    ):
        assert app._handle_mission_rewards_if_due(
            "RUNNING",
            screenshot,
            {"MENU_OPEN"},
        )

    app._mission_reward_scheduler.should_attempt.assert_called_once_with(
        alert_visible=True,
    )
    handler.assert_called_once()
    handler_kwargs = handler.call_args.kwargs
    assert handler_kwargs["screenshot"] is screenshot
    assert handler_kwargs["claim_daily_missions"] is True
    assert (
        handler_kwargs["event_inventory_callback"]
        == app._event_mission_tracker.record_inventory
    )
    assert callable(handler_kwargs["action_guard_fn"])
    assert callable(handler_kwargs["route_state_callback"])
    app._mission_reward_scheduler.mark_completed.assert_called_once()
    app._mission_reward_scheduler.mark_failed.assert_not_called()


def test_app_dispatches_home_badge_probe_before_home_state_handling():
    app = App.__new__(App)
    app._mission_reward_scheduler = Mock()
    app._mission_reward_scheduler.should_attempt.return_value = True
    app._event_mission_tracker = Mock()
    app._blind_tapper_suspended = False
    screenshot = np.zeros((2, 2, 3), dtype=np.uint8)
    badges = MenuRewardBadges(True, True, False)

    with (
        patch("core.app.measure_home_reward_badges", return_value=badges),
        patch("core.app.daily_mission_claims_allowed", return_value=False),
        patch("core.app.stop_blind_gem_tapper", return_value=False),
        patch(
            "core.app.handle_mission_rewards",
            return_value=MissionRewardResult.NOTHING_AVAILABLE,
        ) as handler,
    ):
        assert app._handle_mission_rewards_if_due("HOME_SCREEN", screenshot)

    app._mission_reward_scheduler.should_attempt.assert_called_once_with(
        alert_visible=True,
    )
    handler.assert_called_once_with(
        screenshot=screenshot,
        claim_daily_missions=False,
        event_inventory_callback=app._event_mission_tracker.record_inventory,
    )
    assert app._mission_reward_scheduler.mark_completed.call_count == 1
    app._mission_reward_scheduler.mark_failed.assert_not_called()


def test_home_reward_handler_uses_direct_navigation_and_does_not_close_menu():
    home = np.zeros((2, 2, 3), dtype=np.uint8)
    daily = np.ones((2, 2, 3), dtype=np.uint8)
    event = np.full((2, 2, 3), 2, dtype=np.uint8)
    badges = MenuRewardBadges(True, True, False)
    weekly_review_state = WeeklyChestReviewState()

    with (
        patch.object(rewards, "_reward_source_state", return_value="HOME_SCREEN"),
        patch.object(rewards, "_ensure_reward_hub", return_value=home),
        patch.object(rewards, "measure_home_reward_badges", return_value=badges),
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(rewards, "_wait_for_state", side_effect=[daily, event]),
        patch.object(
            rewards,
            "_claim_daily_rewards",
            return_value=(True, 1),
        ) as claim_daily,
        patch.object(rewards, "_claim_event_rewards", return_value=(True, 0)),
        patch.object(rewards, "_return_to_reward_hub", return_value=home),
        patch.object(rewards, "_close_menu") as close_menu,
        patch.object(rewards, "log_action_intent") as action_intent,
        patch.object(rewards, "log_result") as result_log,
    ):
        result = rewards.handle_mission_rewards(
            home,
            weekly_review_state=weekly_review_state,
        )

    assert result == MissionRewardResult.CLAIMED
    action_intent.assert_called_once_with(
        "Reviewing mission rewards",
        reason=(
            "reward badges may indicate claimable Daily Missions, Weekly "
            "Mission chests, Event Missions, or Guild chests"
        ),
    )
    assert [call.args[0] for call in tap.call_args_list] == [
        "navigation.home_daily_missions",
        "navigation.home_event",
    ]
    claim_daily.assert_called_once_with(
        daily,
        claim_missions=True,
        weekly_review_state=weekly_review_state,
    )
    close_menu.assert_not_called()
    result_log.assert_called_once_with(
        "Mission reward review complete — claimed 1 reward",
        detail=(
            "[MISSION_REWARDS] result=claimed source=HOME_SCREEN "
            "daily=1 event=0 guild=0 "
            "reason=all enabled reward sections completed"
        ),
    )


def test_running_reward_handler_preserves_side_menu_navigation_and_cleanup():
    menu = np.zeros((2, 2, 3), dtype=np.uint8)
    daily = np.ones((2, 2, 3), dtype=np.uint8)
    event = np.full((2, 2, 3), 2, dtype=np.uint8)
    badges = MenuRewardBadges(True, True, False)

    with (
        patch.object(rewards, "_reward_source_state", return_value="RUNNING"),
        patch.object(rewards, "_ensure_reward_hub", return_value=menu),
        patch.object(rewards, "measure_menu_reward_badges", return_value=badges),
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(rewards, "_wait_for_state", side_effect=[daily, event]),
        patch.object(rewards, "_claim_daily_rewards", return_value=(True, 0)),
        patch.object(rewards, "_claim_event_rewards", return_value=(True, 0)),
        patch.object(rewards, "_return_to_reward_hub", return_value=menu),
        patch.object(rewards, "_close_menu", return_value=True) as close_menu,
        patch.object(rewards, "log_result") as result_log,
    ):
        result = rewards.handle_mission_rewards(menu)

    assert result == MissionRewardResult.NOTHING_AVAILABLE
    assert [call.args[0] for call in tap.call_args_list] == [
        "navigation.menu_daily_missions",
        "navigation.menu_event",
    ]
    close_menu.assert_called_once_with(menu)
    result_log.assert_called_once_with(
        "Mission reward review complete — no claimable rewards found",
        detail=(
            "[MISSION_REWARDS] result=nothing_available source=RUNNING "
            "daily=0 event=0 guild=0 "
            "reason=all enabled reward sections completed without a claim"
        ),
    )


def test_reward_handler_emits_failed_result_when_hub_cannot_be_verified():
    screenshot = np.zeros((2, 2, 3), dtype=np.uint8)

    with (
        patch.object(rewards, "_reward_source_state", return_value="RUNNING"),
        patch.object(rewards, "_ensure_reward_hub", return_value=None),
        patch.object(rewards, "log_result") as result_log,
    ):
        result = rewards.handle_mission_rewards(screenshot)

    assert result == MissionRewardResult.FAILED
    result_log.assert_called_once_with(
        "Mission reward review failed — the reward hub could not be verified",
        detail=(
            "[MISSION_REWARDS] result=failed source=RUNNING "
            "daily=0 event=0 guild=0 "
            "reason=the reward hub could not be verified"
        ),
    )


def test_event_inventory_piggybacks_on_badge_triggered_claim_pass():
    screenshot = np.zeros((2, 2, 3), dtype=np.uint8)
    missions = np.ones((2, 2, 3), dtype=np.uint8)
    callback = Mock()
    edge = ScrollResult(False, missions, 1, "edge_before_target")

    with (
        patch.object(rewards, "_is_state", return_value=True),
        patch.object(rewards, "tap_if_visible", return_value=True) as tap,
        patch.object(rewards, "_wait_for_state", return_value=missions) as wait,
        patch.object(
            rewards,
            "scroll_to_edge",
            return_value=ScrollResult(True, missions, 1, "edge_reached"),
        ),
        patch.object(rewards, "is_visible", return_value=False),
        patch.object(rewards, "scroll_until_visible", return_value=edge),
        patch.object(rewards, "_record_event_inventory") as inventory,
    ):
        success, claimed = rewards._claim_event_rewards(
            screenshot,
            inventory_callback=callback,
        )

    assert success
    assert claimed == 0
    tap.assert_called_once_with(
        rewards.EVENT_MISSIONS_TAB,
        screenshot=screenshot,
        retries=1,
    )
    wait.assert_called_once_with("EVENT", settle_s=0.8)
    inventory.assert_called_once_with(missions, callback)


def test_due_event_warning_is_forced_to_stdout_and_action_log():
    app = App.__new__(App)
    app._event_mission_tracker = Mock()
    app._event_mission_tracker.due_warnings.return_value = (
        EventMissionWarning(
            name="Reach wave 20 without cards",
            progress="0/20",
            incomplete_seconds=3 * 24 * 3600,
            stalled_seconds=2 * 24 * 3600,
        ),
    )

    with patch("core.app.log") as log:
        app._emit_event_mission_warnings()

    log.assert_called_once()
    message, level = log.call_args.args
    assert "[EVENT_MISSION_WARNING]" in message
    assert level == "WARN"
    assert log.call_args.kwargs == {"console": True}


def test_event_warning_state_failure_does_not_stop_the_runtime():
    app = App.__new__(App)
    app._event_mission_tracker = Mock()
    app._event_mission_tracker.due_warnings.side_effect = OSError("read failed")

    with patch("core.app.log") as log:
        app._emit_event_mission_warnings()

    log.assert_called_once_with(
        "[EVENT_MISSIONS] Warning check failed: read failed",
        "WARN",
        console=True,
    )


def test_app_defers_reward_probe_from_unsafe_screen():
    app = App.__new__(App)
    app._mission_reward_scheduler = Mock()

    assert not app._handle_mission_rewards_if_due(
        "DAILY_MISSIONS",
        np.zeros((2, 2, 3), dtype=np.uint8),
    )
    app._mission_reward_scheduler.should_attempt.assert_not_called()
