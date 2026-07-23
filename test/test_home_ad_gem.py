from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

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
    ):
        assert not ad_gems.handle_home_ad_gem()

    tap.assert_not_called()
    start.assert_not_called()
