from pathlib import Path
from unittest.mock import Mock, patch

import cv2

from core.app import App
from core.battle_lifecycle import HomeBattleControl
from core.gc_no_battle_setup import (
    GcNoBattleSetupResult,
    GcNoBattleSetupStatus,
    run_gc_no_battle_setup,
)
from core.home_battle import HomeBattleEvidence
from core.matcher import get_match
from core.workshop_preset import (
    BOTS_FARM_PRESET_SLOT,
    CARDS_GC_PRESET_SLOT,
    FARM_PRESET_SLOT,
    PresetSlotSelection,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"
REQUIREMENTS = {
    "cards_deck": "GC",
    "workshop_preset": "Farm",
    "bots_preset": "Farm",
    "guardian_chips": ["Fetch", "Summon", "Scout"],
}


class _NoBattleRouter:
    def __init__(self, *, selected: bool = False, correct_guardians: bool = False):
        self.state = "home"
        self.selected = {
            CARDS_GC_PRESET_SLOT: selected,
            FARM_PRESET_SLOT: selected,
            BOTS_FARM_PRESET_SLOT: selected,
        }
        self.guardians = (
            {"fetch", "summon", "scout"}
            if correct_guardians
            else {"attack", "ally", "scout"}
        )
        self.static_actions = []
        self.visible_actions = []

    def capture(self):
        return self.state

    def detect(self, frame):
        assert frame == self.state
        if frame == "home":
            return {"state": "HOME_SCREEN", "secondary_states": []}
        if frame == "cards":
            return {"state": "CARDS", "secondary_states": ["CARDS_GC_SLOT"]}
        if frame == "workshop":
            return {
                "state": "WORKSHOP",
                "secondary_states": ["WORKSHOP_FARM_SLOT"],
            }
        if frame == "event":
            return {"state": "EVENT", "secondary_states": []}
        if frame == "bots":
            return {
                "state": "EVENT",
                "secondary_states": ["EVENT_BOTS_SCREEN", "BOTS_FARM_SLOT"],
            }
        if frame == "guild":
            return {"state": "GUILD", "secondary_states": []}
        if frame == "guardians":
            secondary = ["GUILD_GUARDIAN_SCREEN"]
            for chip in ("fetch", "summon", "scout"):
                if chip in self.guardians:
                    secondary.append(f"GUARDIAN_{chip.upper()}_EQUIPPED")
            return {"state": "GUILD", "secondary_states": secondary}
        raise AssertionError(frame)

    def home_control(self, _frame):
        return HomeBattleEvidence(HomeBattleControl.NEW_BATTLE, "test", 100.0)

    def measure(self, _frame, region):
        selected = self.selected[region]
        return PresetSlotSelection(
            region,
            True,
            selected,
            2000 if selected else 0,
            0 if selected else 2000,
        )

    def static_tap(self, label, **_kwargs):
        self.static_actions.append(label)
        transitions = {
            ("home", "navigation.goto_cards_home"): "cards",
            ("home", "navigation.goto_workshop_home"): "workshop",
            ("event", "navigation.event:bots_tab"): "bots",
            ("guild", "navigation.guild:guardian_tab"): "guardians",
        }
        if label == "navigation.goto_home" and self.state != "home":
            self.state = "home"
            return True
        destination = transitions.get((self.state, label))
        if destination is None:
            return False
        self.state = destination
        return True

    def visible_tap(self, label, **_kwargs):
        self.visible_actions.append(label)
        if self.state == "cards" and label == "indicators.cards:gc_slot":
            self.selected[CARDS_GC_PRESET_SLOT] = True
            return True
        if self.state == "workshop" and label == "indicators.workshop:farm_slot":
            self.selected[FARM_PRESET_SLOT] = True
            return True
        if self.state == "home" and label == "navigation.home_event":
            self.state = "event"
            return True
        if self.state == "bots" and label == "indicators.bots:farm_slot":
            self.selected[BOTS_FARM_PRESET_SLOT] = True
            return True
        if self.state == "home" and label == "navigation.home_guild":
            self.state = "guild"
            return True
        if self.state != "guardians":
            return False
        guardian_actions = {
            "indicators.guardian:attack_equipped": ("attack", None),
            "buttons.guardian:fetch_inventory": (None, "fetch"),
            "indicators.guardian:ally_equipped": ("ally", None),
            "buttons.guardian:summon_inventory": (None, "summon"),
        }
        remove, add = guardian_actions.get(label, ("unknown", "unknown"))
        if remove == "unknown":
            return False
        if remove:
            if remove not in self.guardians:
                return False
            self.guardians.remove(remove)
        if add:
            self.guardians.add(add)
        return True


def _run(router):
    return run_gc_no_battle_setup(
        REQUIREMENTS,
        screenshot="home",
        capture_fn=router.capture,
        detector=router.detect,
        detect_home_control_fn=router.home_control,
        safe_tap_fn=router.static_tap,
        tap_visible_fn=router.visible_tap,
        measure_selection_fn=router.measure,
        sleep_fn=lambda _seconds: None,
    )


def test_no_battle_setup_corrects_supported_gc_presets_and_guardians():
    router = _NoBattleRouter()

    result = _run(router)

    assert result.complete
    assert router.state == "home"
    assert all(router.selected.values())
    assert router.guardians == {"fetch", "summon", "scout"}
    assert router.visible_actions == [
        "indicators.cards:gc_slot",
        "indicators.workshop:farm_slot",
        "navigation.home_event",
        "indicators.bots:farm_slot",
        "navigation.home_guild",
        "indicators.guardian:attack_equipped",
        "buttons.guardian:fetch_inventory",
        "indicators.guardian:ally_equipped",
        "buttons.guardian:summon_inventory",
    ]


def test_no_battle_setup_leaves_already_correct_settings_untouched():
    router = _NoBattleRouter(selected=True, correct_guardians=True)

    result = _run(router)

    assert result.complete
    assert router.visible_actions == [
        "navigation.home_event",
        "navigation.home_guild",
    ]


def test_no_battle_setup_rejects_unconfigured_profile_without_actions():
    router = _NoBattleRouter()
    result = run_gc_no_battle_setup(
        {**REQUIREMENTS, "workshop_preset": "Tourney"},
        screenshot="home",
        capture_fn=router.capture,
        detector=router.detect,
        detect_home_control_fn=router.home_control,
        safe_tap_fn=router.static_tap,
        tap_visible_fn=router.visible_tap,
        measure_selection_fn=router.measure,
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is GcNoBattleSetupStatus.UNSUPPORTED
    assert router.static_actions == []
    assert router.visible_actions == []


def test_guardian_replacement_templates_require_known_visible_loadout():
    mismatch = cv2.imread(str(FIXTURES / "guild_guardian_gc_inactive_20260715.png"))
    correct = cv2.imread(str(FIXTURES / "guild_guardian_gc_loadout_20260713.png"))
    assert mismatch is not None
    assert correct is not None

    expected = {
        "indicators.guardian:attack_equipped": (170, 525),
        "indicators.guardian:ally_equipped": (910, 525),
        "buttons.guardian:fetch_inventory": (195, 1540),
        "buttons.guardian:summon_inventory": (540, 1540),
    }
    for label, point in expected.items():
        actual, confidence = get_match(label, screenshot=mismatch)
        assert actual == point
        assert confidence >= 0.99

    for label in (
        "indicators.guardian:attack_equipped",
        "indicators.guardian:ally_equipped",
    ):
        point, confidence = get_match(label, screenshot=correct)
        assert point is None
        assert confidence < 0.9


def test_app_runs_no_battle_setup_before_starting_profile_battle():
    frame = object()
    manager = Mock()
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    setup = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.COMPLETE,
        "ok",
        {"cards_deck": "GC"},
    )

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch("core.app.run_gc_no_battle_setup", return_value=setup) as run_setup,
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    run_setup.assert_called_once_with(REQUIREMENTS, screenshot=frame)
    manager.mark_no_battle_setup_complete.assert_called_once_with(setup.evidence)
    handle_home.assert_called_once_with(restart_enabled=True)
    manager.on_home.assert_called_once_with()


def test_app_blocks_battle_start_when_no_battle_setup_fails():
    frame = object()
    manager = Mock()
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    setup = GcNoBattleSetupResult(GcNoBattleSetupStatus.FAILED, "mismatch")

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch("core.app.run_gc_no_battle_setup", return_value=setup),
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    manager.mark_no_battle_setup_complete.assert_not_called()
    handle_home.assert_not_called()
    manager.on_home.assert_not_called()
