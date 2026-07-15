from types import SimpleNamespace

import numpy as np

from core.battle_lifecycle import HomeBattleControl
from core.gc_preflight_navigation import (
    GcPreflightNavigationStatus,
    _select_running_menu,
    run_read_only_gc_preflight,
)
from core.home_battle import HomeBattleEvidence
from core.upgrade_box_detector import UpgradeBox


ULTIMATE_REQUIREMENTS = {
    "Golden Tower": {"primary": "on"},
    "Black Hole": {"primary": "on"},
    "Spotlight": {"primary": "on", "missiles": "on"},
}
PREFLIGHT_REQUIREMENTS = {"ultimate_weapons": ULTIMATE_REQUIREMENTS}


class _FakeUi:
    def __init__(self) -> None:
        self.frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        self.state = "RUNNING"
        self.menu = "UW_MENU"
        self.secondary: set[str] = set()
        self.static_taps: list[str] = []
        self.visible_taps: list[str] = []
        self.swipes: list[tuple[str, str]] = []

    def capture(self):
        return self.frame

    def detect(self, _frame):
        return {
            "state": self.state,
            "menu": self.menu if self.state == "RUNNING" else None,
            "secondary_states": sorted(self.secondary),
            "overlays": ["MENU_OPEN"] if self.state == "RUNNING" else [],
        }

    def safe_tap(self, key, **_kwargs):
        self.static_taps.append(key)
        transitions = {
            "navigation.open_perks": ("PERKS", None, set()),
            "navigation.goto_uw": ("RUNNING", "UW_MENU", set()),
            "navigation.goto_workshop_home": ("WORKSHOP", None, set()),
            "navigation.event:bots_tab": (
                "EVENT",
                None,
                {"EVENT_BOTS_SCREEN"},
            ),
            "navigation.guild:guardian_tab": (
                "GUILD",
                None,
                {"GUILD_GUARDIAN_SCREEN"},
            ),
            "buttons.battle_control:home": ("RUNNING", "UW_MENU", set()),
        }
        if key == "navigation.goto_home":
            self.state, self.menu, self.secondary = "HOME_SCREEN", None, set()
        elif key in transitions:
            self.state, self.menu, self.secondary = transitions[key]
        return True

    def visible_tap(self, key, **_kwargs):
        self.visible_taps.append(key)
        transitions = {
            "navigation.Cards": ("CARDS", None),
            "navigation.home_event": ("EVENT", None),
            "navigation.home_guild": ("GUILD", None),
            "buttons.return_to_game": ("RUNNING", "UW_MENU"),
            "buttons.close:perks": ("RUNNING", "UW_MENU"),
        }
        if key in transitions:
            self.state, self.menu = transitions[key]
            self.secondary = set()
        return True

    def go_home(self):
        self.state, self.menu, self.secondary = "HOME_SCREEN", None, set()
        return True

    def swipe(self, direction, span):
        self.swipes.append((direction, span))


def _home_evidence(control=HomeBattleControl.RESUME_BATTLE):
    return HomeBattleEvidence(control=control, source="test", confidence=100.0)


def test_game_over_observation_aborts_without_sending_input():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    static_taps = []
    visible_taps = []
    swipes = []
    go_home_calls = []

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=lambda: frame,
        detector=lambda _frame: {"state": "GAME_OVER"},
        safe_tap_fn=lambda *args, **kwargs: static_taps.append((args, kwargs)),
        tap_visible_fn=lambda *args, **kwargs: visible_taps.append((args, kwargs)),
        go_home_fn=lambda: go_home_calls.append(True),
        swipe_fn=lambda *args: swipes.append(args),
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is GcPreflightNavigationStatus.BATTLE_ENDED
    assert static_taps == []
    assert visible_taps == []
    assert swipes == []
    assert go_home_calls == []


def test_read_only_route_returns_to_running_and_never_uses_mutating_controls():
    ui = _FakeUi()
    boxes = [
        UpgradeBox(
            "left",
            (0, 0, 1, 1),
            text=label,
            toggles={name: "on" for name in toggles},
        )
        for label, toggles in ULTIMATE_REQUIREMENTS.items()
    ]

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=ui.go_home,
        swipe_fn=ui.swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {"left": boxes, "right": []},
        detect_home_control_fn=lambda _frame: _home_evidence(),
        sleep_fn=lambda _seconds: None,
        validate_fn=lambda **_kwargs: SimpleNamespace(valid=True),
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert result.valid
    assert ui.state == "RUNNING"
    assert "buttons.battle_control:home" in ui.static_taps
    all_keys = set(ui.static_taps + ui.visible_taps)
    assert not any("surrender" in key for key in all_keys)
    assert not any("preset" in key for key in all_keys)
    assert not any("toggle" in key for key in all_keys)
    assert ui.swipes == [("towards_top", "extended")] * 3


def test_new_battle_home_control_aborts_without_tapping_battle():
    ui = _FakeUi()

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=ui.go_home,
        swipe_fn=ui.swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {"left": [], "right": []},
        detect_home_control_fn=lambda _frame: _home_evidence(
            HomeBattleControl.NEW_BATTLE
        ),
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is GcPreflightNavigationStatus.BATTLE_ENDED
    assert "buttons.battle_control:home" not in ui.static_taps


def test_resume_control_is_reverified_on_fresh_frame_immediately_before_tap():
    ui = _FakeUi()
    boxes = [
        UpgradeBox(
            "left",
            (0, 0, 1, 1),
            text=label,
            toggles={name: "on" for name in toggles},
        )
        for label, toggles in ULTIMATE_REQUIREMENTS.items()
    ]
    home_observations = 0

    def changing_home_control(_frame):
        nonlocal home_observations
        home_observations += 1
        control = (
            HomeBattleControl.RESUME_BATTLE
            if home_observations <= 4
            else HomeBattleControl.NEW_BATTLE
        )
        return _home_evidence(control)

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=ui.go_home,
        swipe_fn=ui.swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {"left": boxes, "right": []},
        detect_home_control_fn=changing_home_control,
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is GcPreflightNavigationStatus.BATTLE_ENDED
    assert home_observations >= 5
    assert "buttons.battle_control:home" not in ui.static_taps


def test_running_menu_selection_retries_when_cinematic_mode_consumes_first_tap():
    ui = _FakeUi()
    ui.menu = None
    ui.secondary = {"CINEMATIC_MODE"}
    attempts = 0

    def cinematic_safe_tap(key, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            ui.secondary = set()
            return True
        return ui.safe_tap(key, **kwargs)

    frame = _select_running_menu(
        "navigation.goto_uw",
        "UW_MENU",
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=cinematic_safe_tap,
        sleep_fn=lambda _seconds: None,
    )

    assert frame is ui.frame
    assert attempts == 2
    assert ui.menu == "UW_MENU"
