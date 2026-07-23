from unittest.mock import MagicMock

import cv2
import numpy as np

from core.no_strategy_inventory import (
    NoStrategyInventoryStatus,
    run_no_strategy_in_battle_inventory,
)
from core.no_strategy_observer import ATTACK_DISSONANCE_BADGE_REGION


class _InventoryUi:
    def __init__(self) -> None:
        self.frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        purple = cv2.cvtColor(
            np.uint8([[[145, 220, 220]]]), cv2.COLOR_HSV2BGR
        )[0, 0]
        x, y, width, height = ATTACK_DISSONANCE_BADGE_REGION
        self.frame[y : y + height, x : x + width] = purple
        self.state = "RUNNING"
        self.menu = "UTILITY_MENU"
        self.side_menu_open = False
        self.secondary: set[str] = set()
        self.safe_taps: list[str] = []
        self.visible_taps: list[str] = []
        self.swipes: list[tuple[str, str]] = []

    def capture(self):
        return self.frame

    def detect(self, _frame):
        return {
            "state": self.state,
            "menu": self.menu if self.state == "RUNNING" else None,
            "secondary_states": sorted(self.secondary),
            "overlays": (
                ["MENU_OPEN" if self.side_menu_open else "MENU_CLOSED"]
                if self.state == "RUNNING"
                else []
            ),
        }

    def safe_tap(self, key, **_kwargs):
        self.safe_taps.append(key)
        if key == "navigation.open_perks":
            self.state = "PERKS"
        elif key == "navigation.target_priority":
            self.state = "TARGET_PRIORITY"
            self.side_menu_open = False
        elif key == "buttons.close:target_priority":
            self.state = "RUNNING"
            self.menu = "UTILITY_MENU"
        return True

    def visible_tap(self, key, **_kwargs):
        self.visible_taps.append(key)
        if key == "navigation.menu_open_button":
            self.side_menu_open = True
        elif key == "navigation.Cards":
            self.state = "CARDS"
            self.side_menu_open = False
        elif key == "buttons.close:perks":
            self.state = "RUNNING"
            self.menu = "UTILITY_MENU"
        elif key == "navigation.goto_uw":
            self.state = "RUNNING"
            self.menu = "UW_MENU"
            self.side_menu_open = False
        elif key == "navigation.menu_modules":
            self.state = "MODULES"
            self.side_menu_open = False
        elif key == "navigation.menu_event":
            self.state = "EVENT"
            self.side_menu_open = False
        elif key == "navigation.event:bots_tab":
            self.secondary = {"EVENT_BOTS_SCREEN"}
        elif key == "navigation.menu_guild":
            self.state = "GUILD"
            self.side_menu_open = False
        elif key == "navigation.guild:guardian_tab":
            self.secondary = {"GUILD_GUARDIAN_SCREEN"}
        elif key == "buttons.return_to_game":
            self.state = "RUNNING"
            self.menu = "UTILITY_MENU"
            self.side_menu_open = False
            self.secondary = set()
        return True

    def swipe(self, direction, span):
        self.swipes.append((direction, span))


def test_automatic_inventory_visits_read_only_screens_and_restores_running():
    ui = _InventoryUi()
    observer = MagicMock()

    result = run_no_strategy_in_battle_inventory(
        observer,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        swipe_fn=ui.swipe,
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is NoStrategyInventoryStatus.COMPLETE
    assert ui.state == "RUNNING"
    assert "navigation.Cards" in ui.visible_taps
    assert "navigation.menu_modules" in ui.visible_taps
    assert "navigation.menu_event" in ui.visible_taps
    assert "navigation.menu_guild" in ui.visible_taps
    assert "navigation.target_priority" in ui.safe_taps
    assert "buttons.close:target_priority" in ui.safe_taps
    assert ui.swipes == [
        ("towards_top", "extended"),
        ("towards_top", "extended"),
        ("towards_top", "extended"),
        ("towards_bottom", "medium"),
        ("towards_bottom", "medium"),
        ("towards_bottom", "medium"),
        ("towards_bottom", "medium"),
        ("towards_bottom", "medium"),
    ]
    observer.record_unavailable.assert_called_once_with(
        "damage_slider",
        reason="Attack menu disabled by Attack Dissonance",
        source="attack_dissonance_menu_constraint",
        phase="in_battle",
    )
    all_actions = ui.safe_taps + ui.visible_taps
    assert not any("surrender" in action for action in all_actions)
    assert not any("preset" in action for action in all_actions)
    assert not any("toggle" in action for action in all_actions)


def test_pause_after_menu_open_blocks_every_following_inventory_action():
    ui = _InventoryUi()
    observer = MagicMock()
    allowed = {"value": True}

    def visible_tap(key, **kwargs):
        result = ui.visible_tap(key, **kwargs)
        if key == "navigation.menu_open_button":
            allowed["value"] = False
        return result

    result = run_no_strategy_in_battle_inventory(
        observer,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=visible_tap,
        actions_allowed=lambda: allowed["value"],
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is NoStrategyInventoryStatus.PAUSED
    assert ui.visible_taps == ["navigation.menu_open_button"]
    assert ui.safe_taps == []


def test_natural_terminal_state_aborts_inventory_without_input():
    ui = _InventoryUi()
    ui.state = "GAME_OVER"
    observer = MagicMock()

    result = run_no_strategy_in_battle_inventory(
        observer,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is NoStrategyInventoryStatus.BATTLE_ENDED
    assert ui.safe_taps == []
    assert ui.visible_taps == []
