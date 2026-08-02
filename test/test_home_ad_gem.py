from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pytest

from core.action_authority import (
    AuxiliaryCollector,
    RuntimeActionAuthority,
    RuntimeActionClass,
)
from core.app import App
from core.label_tapper import get_label_match
from core.matcher import get_match
from core.state_detector import detect_state_and_overlays
import handlers.ad_gem_handler as ad_gems


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path):
    image = cv2.imread(str(path))
    assert image is not None, path
    return image


def test_home_ad_gem_template_and_overlay_have_positive_and_negative_evidence():
    available = _load(
        ROOT / "test" / "fixtures" / "home_screen_no_reward_badges_20260714.png"
    )
    unavailable = _load(
        ROOT
        / "test"
        / "fixtures"
        / "gc_module_gate_20260716"
        / "home_scrolled_new_battle.png"
    )

    point, confidence = get_match(
        "overlays.claim_ad_gem:home",
        screenshot=available,
    )
    unavailable_point, unavailable_confidence = get_match(
        "overlays.claim_ad_gem:home",
        screenshot=unavailable,
    )

    assert point == (124, 251)
    assert confidence >= 0.92
    assert unavailable_point is None
    assert unavailable_confidence < 0.9
    x, y, width, height = get_label_match(
        "buttons.claim_ad_gem:home",
        screenshot=available,
    )
    assert (x + width // 2, y + height // 2) == (124, 251)
    assert "HOME_AD_GEMS_AVAILABLE" in set(
        detect_state_and_overlays(available)["overlays"]
    )
    assert "HOME_AD_GEMS_AVAILABLE" not in set(
        detect_state_and_overlays(unavailable)["overlays"]
    )


def test_home_ad_gem_claim_revalidates_and_never_starts_blind_tapper():
    with (
        patch.object(ad_gems, "is_visible", side_effect=[True, False]),
        patch.object(ad_gems, "safe_tap", return_value=True) as tap,
        patch.object(ad_gems, "start_blind_gem_tapper") as start,
        patch.object(ad_gems, "stop_blind_gem_tapper") as stop,
        patch.object(ad_gems, "log_action_intent") as action_log,
        patch.object(ad_gems, "log_result") as result_log,
        patch.object(ad_gems.time, "sleep"),
    ):
        assert ad_gems.handle_home_ad_gem()

    stop.assert_called_once_with()
    start.assert_not_called()
    tap.assert_called_once_with(
        "buttons.claim_ad_gem:home",
        retries=1,
        retry_delay=0.4,
        dispatch="now",
    )
    action_log.assert_called_once_with(
        "Collecting the Home ad gem",
        reason="the Home overlay indicates that a five-gem reward is available",
        detail="[AD_GEM] source=home label=buttons.claim_ad_gem:home",
    )
    result_log.assert_called_once_with(
        "Home ad-gem collection complete — reward collected",
        detail=(
            "[AD_GEM] result=collected source=home "
            "label=buttons.claim_ad_gem:home"
        ),
    )


def test_home_ad_gem_dispatch_precedes_home_battle_handling():
    app = App.__new__(App)
    app._handler_enabled = Mock(return_value=True)
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with (
        patch("core.app.handle_home_ad_gem") as claim,
        patch("core.app.handle_home_screen") as home,
    ):
        app._handle_primary_states(
            "HOME_SCREEN",
            {"HOME_AD_GEMS_AVAILABLE"},
            frame,
        )

    claim.assert_called_once_with()
    home.assert_not_called()


def test_home_ad_gem_disappearance_before_action_fails_closed():
    with (
        patch.object(ad_gems, "is_visible", return_value=False),
        patch.object(ad_gems, "safe_tap") as tap,
        patch.object(ad_gems, "start_blind_gem_tapper") as start,
        patch.object(ad_gems, "stop_blind_gem_tapper"),
        patch.object(ad_gems, "log_result") as result_log,
    ):
        assert not ad_gems.handle_home_ad_gem()

    tap.assert_not_called()
    start.assert_not_called()
    result_log.assert_called_once_with(
        "Home ad-gem collection complete — no reward was collected",
        detail=(
            "[AD_GEM] result=no_op source=home "
            "label=buttons.claim_ad_gem:home"
        ),
    )


def test_blind_floating_gem_taps_retain_input_logging():
    times = iter((0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0))
    original_state = ad_gems.AUTOMATION.state
    ad_gems.AUTOMATION.state = ad_gems.RunState.RUNNING
    try:
        with (
            patch.object(ad_gems, "get_click", return_value=(250, 1200)),
            patch.object(ad_gems.time, "time", side_effect=lambda: next(times)),
            patch.object(ad_gems.time, "sleep"),
            patch.object(ad_gems, "tap") as tap,
            patch.object(ad_gems, "log_action_intent") as action_log,
            patch.object(ad_gems, "log_result") as result_log,
        ):
            ad_gems.start_blind_gem_tapper(
                duration=1,
                interval=1,
                blocking=True,
            )
    finally:
        ad_gems.AUTOMATION.state = original_state

    tap.assert_called_once_with(
        250,
        1200,
        label="floating_gem_blind_tap",
        log_it=True,
    )
    action_log.assert_called_once_with(
        "Scanning for floating gems",
        reason="an in-battle ad-gem overlay can coincide with a moving gem",
        detail="[AD_GEM] duration_s=1 interval_s=1",
    )
    result_log.assert_called_once_with(
        "Floating-gem scan complete — dispatched 1 tap",
        detail=(
            "[AD_GEM] result=completed taps=1 elapsed_s=1 "
            "stop_requested=False failure=None"
        ),
    )


def test_blind_floating_gem_rechecks_authority_before_every_tap():
    class Clock:
        now = 0.0

        def time(self):
            return self.now

        def sleep(self, duration):
            self.now += max(0.0, duration)

    clock = Clock()
    guard = Mock(side_effect=[True, True, True, False])
    with (
        patch.object(ad_gems, "get_click", return_value=(250, 1200)),
        patch.object(ad_gems.time, "time", side_effect=clock.time),
        patch.object(ad_gems.time, "sleep", side_effect=clock.sleep),
        patch.object(ad_gems, "tap") as tap,
        patch.object(ad_gems, "log_action_intent"),
        patch.object(ad_gems, "log_result") as result_log,
    ):
        ad_gems.start_blind_gem_tapper(
            duration=5,
            interval=1,
            blocking=True,
            action_guard_fn=guard,
        )

    assert guard.call_count == 4  # start check plus one check per attempted tap
    assert tap.call_count == 2
    assert not ad_gems.is_blind_gem_tapper_active()
    assert "result=interrupted taps=2" in result_log.call_args.kwargs["detail"]
    assert "authority_lost=True" in result_log.call_args.kwargs["detail"]


def test_global_pause_stops_floating_gem_scan_before_its_next_tap():
    class Clock:
        now = 0.0

        def time(self):
            return self.now

        def sleep(self, duration):
            self.now += max(0.0, duration)

    clock = Clock()
    authority = RuntimeActionAuthority()

    def set_pause(paused):
        authority.update_context(
            global_pause=paused,
            active_battle=True,
            battle_scope="run-1",
            primary_state="RUNNING",
        )

    set_pause(False)
    authority.activate_strategy_gate(
        strategy="farm_t18",
        battle_scope="run-1",
        source="session_preflight",
        phase="running_battle",
        failed_check_ids=("modules",),
        reason="Modules do not match",
    )

    def guarded():
        return authority.decision(
            RuntimeActionClass.AUXILIARY_COLLECTION,
            collector=AuxiliaryCollector.FLOATING_GEM_SCAN,
        ).allowed

    def tap_once_then_pause(*_args, **_kwargs):
        set_pause(True)

    with (
        patch.object(ad_gems, "get_click", return_value=(250, 1200)),
        patch.object(ad_gems.time, "time", side_effect=clock.time),
        patch.object(ad_gems.time, "sleep", side_effect=clock.sleep),
        patch.object(ad_gems, "tap", side_effect=tap_once_then_pause) as tap,
        patch.object(ad_gems, "log_action_intent"),
        patch.object(ad_gems, "log_result") as result_log,
    ):
        ad_gems.start_blind_gem_tapper(
            duration=5,
            interval=1,
            blocking=True,
            action_guard_fn=guarded,
        )

    tap.assert_called_once()
    assert "result=interrupted taps=1" in result_log.call_args.kwargs["detail"]
    assert "authority_lost=True" in result_log.call_args.kwargs["detail"]


def test_floating_gem_guard_latency_does_not_accumulate_in_tap_cadence():
    class Clock:
        now = 0.0

        def time(self):
            return self.now

        def sleep(self, duration):
            self.now += max(0.0, duration)

    clock = Clock()
    tap_times = []

    def guarded():
        # Deliberately exaggerate the real guard cost. The next tap should
        # remain on the original wall-clock cadence rather than drifting by
        # another 200 ms every iteration.
        clock.now += 0.2
        return True

    with (
        patch.object(ad_gems, "get_click", return_value=(250, 1200)),
        patch.object(ad_gems.time, "time", side_effect=clock.time),
        patch.object(ad_gems.time, "sleep", side_effect=clock.sleep),
        patch.object(
            ad_gems,
            "tap",
            side_effect=lambda *_args, **_kwargs: tap_times.append(clock.now),
        ),
        patch.object(ad_gems, "log_action_intent"),
        patch.object(ad_gems, "log_result"),
    ):
        ad_gems._blind_floating_gem_tapper(
            duration=3,
            interval=1,
            stop_event=ad_gems.threading.Event(),
            action_guard_fn=guarded,
        )

    assert tap_times == pytest.approx([0.2, 1.2, 2.2])


def test_visible_ad_gem_rechecks_authority_before_each_retry_input():
    guard = Mock(side_effect=[True, False])
    with (
        patch.object(ad_gems, "is_visible", side_effect=[True, True, True]),
        patch.object(ad_gems, "safe_tap", return_value=True) as tap,
        patch.object(ad_gems.time, "sleep"),
    ):
        assert not ad_gems._collect_visible_ad_gem(
            "overlays.ad_gem",
            action_guard_fn=guard,
        )

    assert guard.call_count == 2
    tap.assert_called_once()


def test_in_battle_ad_gem_has_one_intent_and_terminal_result():
    with (
        patch.object(ad_gems, "start_blind_gem_tapper") as start,
        patch.object(ad_gems, "_collect_visible_ad_gem", return_value=True),
        patch.object(ad_gems, "log_action_intent") as action_log,
        patch.object(ad_gems, "log_result") as result_log,
        patch.object(ad_gems.time, "sleep"),
    ):
        assert ad_gems.handle_ad_gem()

    start.assert_called_once_with(duration=20, interval=1, blocking=False)
    action_log.assert_called_once_with(
        "Collecting the in-battle ad gem",
        reason=(
            "the current battle frame indicates that an ad-gem reward is available"
        ),
        detail="[AD_GEM] source=battle label=overlays.ad_gem",
    )
    result_log.assert_called_once_with(
        "In-battle ad-gem collection complete — reward collected",
        detail=(
            "[AD_GEM] result=collected source=battle label=overlays.ad_gem"
        ),
    )
