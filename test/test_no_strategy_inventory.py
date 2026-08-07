from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from core.no_strategy_inventory import (
    NoStrategyInventoryStatus,
    run_no_strategy_in_battle_inventory,
)
from core.no_strategy_observer import (
    DISSONANCE_BADGE_REGION,
    NoStrategyRunObserver,
)


class _InventoryUi:
    def __init__(self, *, dissonance_subtype: str = "attack") -> None:
        self.frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        x, y, width, height = DISSONANCE_BADGE_REGION
        if dissonance_subtype == "utility":
            badge = cv2.imread(
                "test/fixtures/utility_dissonance_badge_20260806.png"
            )
            assert badge is not None
            self.frame[y : y + height, x : x + width] = badge
        else:
            purple = cv2.cvtColor(
                np.uint8([[[145, 220, 220]]]), cv2.COLOR_HSV2BGR
            )[0, 0]
            self.frame[y : y + height, x : x + width] = purple
            reference = cv2.imread(
                "assets/match_templates/navigation/goto_attack.png"
            )
            assert reference is not None
            hsv = cv2.cvtColor(reference, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(
                hsv,
                np.array((0, 10, 80), dtype=np.uint8),
                np.array((179, 255, 255), dtype=np.uint8),
            )
            contours, _hierarchy = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            ref_x, ref_y, ref_width, ref_height = cv2.boundingRect(
                max(contours, key=cv2.contourArea)
            )
            symbol = mask[
                ref_y : ref_y + ref_height,
                ref_x : ref_x + ref_width,
            ]
            scale = min(26 / ref_width, 26 / ref_height)
            symbol = cv2.resize(
                symbol,
                (
                    max(1, round(ref_width * scale)),
                    max(1, round(ref_height * scale)),
                ),
                interpolation=cv2.INTER_NEAREST,
            )
            symbol_y = 1007
            symbol_x = 703 - symbol.shape[1] // 2
            target = self.frame[
                symbol_y : symbol_y + symbol.shape[0],
                symbol_x : symbol_x + symbol.shape[1],
            ]
            target[symbol > 0] = (255, 255, 255)
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


def test_guarded_save_and_dissonance_badge_eliminate_redundant_ui_route():
    ui = _InventoryUi()
    observer = NoStrategyRunObserver()
    observer.record_player_save_observations(
        {
            "schema_version": 1,
            "source": "guarded_active_attachment_player_save",
            "mapping_id": "data-9-game-1073",
            "captured_at": "2026-08-06T23:31:05+00:00",
            "checks": {
                "cards_deck": {"value": "Farm"},
                "bots_preset": {"value": "Farm"},
                "guardian_chips": {"value": ["Fetch", "Summon", "Scout"]},
                "modules": {
                    "value": {"cannon_primary": "Amplifying Strike"}
                },
                "target_priority": {"value": ["Fast", "Boss", "Closest"]},
                "auto_pick_perks": {"value": True},
                "ultimate_weapon_primaries": {
                    "value": {
                        "Poison Swamp": {"primary": "on"},
                        "Spotlight": {"primary": "on"},
                    }
                },
                "poison_swamp_stun": {"value": "off"},
                "spotlight_missiles": {"value": "on"},
            },
        }
    )

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
    assert result.reason == (
        "all in-battle fields were already resolved without UI navigation"
    )
    assert ui.safe_taps == []
    assert ui.visible_taps == []
    assert ui.swipes == []
    assert observer.snapshot()["fields"]["damage_slider"]["status"] == (
        "unavailable"
    )


def test_utility_dissonance_keeps_attack_damage_slider_in_inventory_plan():
    ui = _InventoryUi(dissonance_subtype="utility")
    observer = NoStrategyRunObserver()
    observer.record_player_save_observations(
        {
            "schema_version": 1,
            "source": "guarded_active_attachment_player_save",
            "mapping_id": "data-9-game-1073",
            "captured_at": "2026-08-07T01:13:04+00:00",
            "checks": {
                "cards_deck": {"value": "Farm"},
                "bots_preset": {"value": "Farm"},
                "guardian_chips": {"value": ["Fetch", "Summon", "Scout"]},
                "modules": {"value": {"cannon_primary": "Amplifying Strike"}},
                "target_priority": {"value": ["Fast", "Boss", "Closest"]},
                "auto_pick_perks": {"value": True},
                "ultimate_weapon_primaries": {
                    "value": {
                        "Poison Swamp": {"primary": "on"},
                        "Spotlight": {"primary": "on"},
                    }
                },
                "poison_swamp_stun": {"value": "off"},
                "spotlight_missiles": {"value": "on"},
            },
        }
    )

    with patch("core.no_strategy_inventory._capture_damage_slider") as capture:
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
    assert result.reason == "visited only the remaining UI fields: damage_slider"
    capture.assert_called_once()
    identity = observer.snapshot()["fields"]["run_identity"]
    assert identity["value"]["label"] == "Utility Dissonance"


def test_guarded_save_limits_ui_route_to_the_one_unresolved_section():
    ui = _InventoryUi()
    observer = NoStrategyRunObserver()
    observer.record_player_save_observations(
        {
            "schema_version": 1,
            "source": "guarded_active_attachment_player_save",
            "mapping_id": "data-9-game-1073",
            "captured_at": "2026-08-06T23:31:05+00:00",
            "checks": {
                "bots_preset": {"value": "Farm"},
                "guardian_chips": {"value": ["Fetch", "Summon", "Scout"]},
                "modules": {"value": {"cannon_primary": "Amplifying Strike"}},
                "target_priority": {"value": ["Fast", "Boss", "Closest"]},
                "auto_pick_perks": {"value": True},
                "ultimate_weapon_primaries": {
                    "value": {
                        "Poison Swamp": {"primary": "on"},
                        "Spotlight": {"primary": "on"},
                    }
                },
                "poison_swamp_stun": {"value": "off"},
                "spotlight_missiles": {"value": "on"},
            },
        }
    )

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
    assert result.reason == "visited only the remaining UI fields: cards_deck"
    assert ui.visible_taps == [
        "navigation.menu_open_button",
        "navigation.Cards",
        "buttons.return_to_game",
    ]
    assert ui.safe_taps == []
    assert ui.swipes == []


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
