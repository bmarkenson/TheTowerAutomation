from pathlib import Path
from unittest.mock import patch

import cv2

from core.battle_lifecycle import HomeBattleControl
from core.clickmap_access import get_click
from handlers.tournament_launch_handler import (
    dispatch_tournament_launch,
    tournament_battle_control_visible,
    tournament_open_control_visible,
)


ROOT = Path(__file__).resolve().parents[1]
HOME_OPEN = ROOT / "test" / "fixtures" / "home_screen_no_reward_badges_20260714.png"
TOURNAMENT_ENTRY = (
    ROOT / "test" / "fixtures" / "ui_state_20260714" / "home_tournament.png"
)


def _load(path: Path):
    image = cv2.imread(str(path))
    assert image is not None, f"fixture is unreadable: {path}"
    return image


def test_tournament_launch_controls_have_explicit_bounded_geometry():
    assert get_click("buttons.tournament_open:home") == (120, 555)
    assert get_click("buttons.battle:tournament") == (540, 1550)


def test_home_open_control_requires_new_battle_and_open_ocr():
    screenshot = _load(HOME_OPEN)
    evidence = type(
        "HomeEvidence",
        (),
        {"control": HomeBattleControl.NEW_BATTLE},
    )()
    with patch(
        "handlers.tournament_launch_handler.detect_home_battle_control",
        return_value=evidence,
    ):
        assert tournament_open_control_visible(screenshot)

    resumed = type(
        "HomeEvidence",
        (),
        {"control": HomeBattleControl.RESUME_BATTLE},
    )()
    with patch(
        "handlers.tournament_launch_handler.detect_home_battle_control",
        return_value=resumed,
    ):
        assert not tournament_open_control_visible(screenshot)


def test_tournament_battle_control_requires_entry_state_and_battle_ocr():
    screenshot = _load(TOURNAMENT_ENTRY)
    with (
        patch(
            "handlers.tournament_launch_handler.detect_state_and_overlays",
            return_value={"state": "TOURNAMENT_SCREEN"},
        ),
        patch(
            "handlers.tournament_launch_handler.ocr_text_and_conf",
            return_value=("BATTLE", 95.0),
        ),
    ):
        assert tournament_battle_control_visible(screenshot)

    with (
        patch(
            "handlers.tournament_launch_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.tournament_launch_handler.ocr_text_and_conf",
            return_value=("BATTLE", 95.0),
        ),
    ):
        assert not tournament_battle_control_visible(screenshot)


def test_dispatch_from_home_rechecks_authority_before_each_tap():
    home = _load(HOME_OPEN)
    entry = _load(TOURNAMENT_ENTRY)
    authority = iter((True, True, True))
    with (
        patch(
            "handlers.tournament_launch_handler.detect_state_and_overlays",
            side_effect=(
                {"state": "HOME_SCREEN"},
                {"state": "TOURNAMENT_SCREEN"},
            ),
        ),
        patch(
            "handlers.tournament_launch_handler.tournament_open_control_visible",
            return_value=True,
        ),
        patch(
            "handlers.tournament_launch_handler.tournament_battle_control_visible",
            return_value=True,
        ),
        patch(
            "handlers.tournament_launch_handler.tap_verified_tournament_open",
            return_value=True,
        ) as open_tournament,
        patch(
            "handlers.tournament_launch_handler.tap_verified_tournament_battle",
            return_value=True,
        ) as battle,
    ):
        result = dispatch_tournament_launch(
            home,
            action_guard=lambda: next(authority),
            capture_fn=lambda: entry,
            sleep_fn=lambda _seconds: None,
            monotonic_fn=iter((0.0, 0.0)).__next__,
        )

    assert result.dispatched
    open_tournament.assert_called_once_with(home)
    battle.assert_called_once_with(entry)


def test_dispatch_fails_closed_when_authority_is_withdrawn():
    entry = _load(TOURNAMENT_ENTRY)
    with (
        patch(
            "handlers.tournament_launch_handler.detect_state_and_overlays",
            return_value={"state": "TOURNAMENT_SCREEN"},
        ),
        patch(
            "handlers.tournament_launch_handler.tournament_battle_control_visible",
            return_value=True,
        ),
        patch(
            "handlers.tournament_launch_handler.tap_verified_tournament_battle",
        ) as battle,
    ):
        result = dispatch_tournament_launch(
            entry,
            action_guard=lambda: False,
        )

    assert not result.dispatched
    assert "withdrawn" in result.reason
    battle.assert_not_called()
