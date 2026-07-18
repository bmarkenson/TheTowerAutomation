from types import SimpleNamespace

import numpy as np

from core.battle_lifecycle import HomeBattleControl
from core.gc_preflight_navigation import (
    GcPreflightNavigationStatus,
    _guarded_visible_tap,
    _select_running_menu,
    run_read_only_gc_preflight,
)
from core.home_battle import HomeBattleEvidence
from core.poison_swamp_stun import PoisonSwampStunState
from core.upgrade_box_detector import UpgradeBox


ULTIMATE_REQUIREMENTS = {
    "Golden Tower": {"primary": "on"},
    "Black Hole": {"primary": "on"},
    "Poison Swamp": {"primary": "on", "stun": "off"},
    "Spotlight": {"primary": "on", "missiles": "on"},
}
MODULE_REQUIREMENTS = {
    "cannon_assist": "Being Annihilator",
    "cannon_primary": "Amplifying Strike",
    "generator_primary": "Black Hole Digestor",
    "generator_assist": "Singularity Harness",
    "armor_assist": "Anti-Cube Portal",
    "armor_primary": "Orbital Augment",
    "core_primary": "Multiverse Nexus",
    "core_assist": "Dimension Core",
}
PREFLIGHT_REQUIREMENTS = {
    "ultimate_weapons": ULTIMATE_REQUIREMENTS,
    "modules": MODULE_REQUIREMENTS,
    "auto_pick_perks": True,
}


class _FakeUi:
    def __init__(self) -> None:
        self.frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        self.state = "RUNNING"
        self.menu = "UW_MENU"
        self.secondary: set[str] = set()
        self.static_taps: list[str] = []
        self.visible_taps: list[str] = []
        self.swipes: list[tuple[str, str]] = []
        self.event_swipes: list[str] = []
        self.bots_offscreen = False

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
            "navigation.goto_workshop_home": ("WORKSHOP", None, set()),
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
            "navigation.Cards": ("CARDS", None, set()),
            "navigation.goto_uw": ("RUNNING", "UW_MENU", set()),
            "navigation.menu_modules": ("MODULES", None, set()),
            "navigation.menu_event": ("EVENT", None, set()),
            "navigation.menu_guild": ("GUILD", None, set()),
            "navigation.event:bots_tab": (
                "EVENT",
                None,
                set() if self.bots_offscreen else {"EVENT_BOTS_SCREEN"},
            ),
            "navigation.guild:guardian_tab": (
                "GUILD",
                None,
                {"GUILD_GUARDIAN_SCREEN"},
            ),
            "navigation.home_event": ("EVENT", None, set()),
            "navigation.home_guild": ("GUILD", None, set()),
            "buttons.return_to_game": ("RUNNING", "UW_MENU", set()),
            "buttons.close:perks": ("RUNNING", "UW_MENU", set()),
        }
        if key in transitions:
            self.state, self.menu, self.secondary = transitions[key]
        return True

    def go_home(self):
        self.state, self.menu, self.secondary = "HOME_SCREEN", None, set()
        return True

    def swipe(self, direction, span):
        self.swipes.append((direction, span))

    def event_swipe(self, label):
        self.event_swipes.append(label)
        self.secondary = {"EVENT_BOTS_SCREEN"}
        return True


def _home_evidence(control=HomeBattleControl.RESUME_BATTLE):
    return HomeBattleEvidence(control=control, source="test", confidence=100.0)


def _stun_off_result(ui, *, changed=False):
    return SimpleNamespace(
        screenshot=ui.frame,
        evidence=SimpleNamespace(state=PoisonSwampStunState.OFF),
        changed=changed,
    )


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
    ui.bots_offscreen = True
    boxes = [
        UpgradeBox(
            "left",
            (0, 0, 1, 1),
            text=label,
            toggles={name: "on" for name in toggles if name != "stun"},
        )
        for label, toggles in ULTIMATE_REQUIREMENTS.items()
    ]
    corrections = []
    validated = {}

    def ensure_stun(**kwargs):
        corrections.append(kwargs)
        return _stun_off_result(ui, changed=True)

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True)

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=ui.go_home,
        swipe_fn=ui.swipe,
        event_swipe_fn=ui.event_swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {"left": boxes, "right": []},
        ensure_poison_swamp_stun_fn=ensure_stun,
        detect_home_control_fn=lambda _frame: _home_evidence(),
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert result.valid
    assert ui.state == "RUNNING"
    assert "buttons.battle_control:home" in ui.static_taps
    assert "navigation.menu_modules" in ui.visible_taps
    assert "navigation.menu_event" in ui.visible_taps
    assert "navigation.menu_guild" in ui.visible_taps
    assert "navigation.event:bots_tab" in ui.visible_taps
    assert "navigation.guild:guardian_tab" in ui.visible_taps
    assert "navigation.goto_uw" in ui.visible_taps
    assert "navigation.menu_modules" not in ui.static_taps
    assert "navigation.menu_event" not in ui.static_taps
    assert "navigation.menu_guild" not in ui.static_taps
    assert "navigation.goto_modules_home" not in ui.static_taps
    assert "navigation.home_event" not in ui.visible_taps
    assert "navigation.home_guild" not in ui.visible_taps
    assert ui.static_taps.count("navigation.goto_home") == 1
    assert ui.visible_taps.count("buttons.return_to_game") == 4
    all_keys = set(ui.static_taps + ui.visible_taps)
    assert not any("surrender" in key for key in all_keys)
    assert not any("preset" in key for key in all_keys)
    assert not any("toggle" in key for key in all_keys)
    assert len(corrections) == 1
    assert validated["ultimate_observations"]["Poison Swamp"]["stun"] == "off"
    assert ui.swipes == [("towards_top", "extended")] * 3
    assert ui.event_swipes == ["gesture_targets.goto_top:event_bots"]


def test_stun_evidence_survives_later_primary_only_observation():
    ui = _FakeUi()
    positions = iter(
        (
            {
                "left": [
                    UpgradeBox(
                        "left",
                        (0, 0, 1, 1),
                        text="Golden Tower",
                        toggles={"primary": "on"},
                    ),
                    UpgradeBox(
                        "left",
                        (0, 0, 1, 1),
                        text="Poison Swamp",
                        toggles={"primary": "on"},
                    ),
                ],
                "right": [],
            },
            {
                "left": [
                    UpgradeBox(
                        "left",
                        (0, 0, 1, 1),
                        text="Poison Swamp",
                        toggles={"primary": "on"},
                    ),
                    UpgradeBox(
                        "left",
                        (0, 0, 1, 1),
                        text="Black Hole",
                        toggles={"primary": "on"},
                    ),
                ],
                "right": [
                    UpgradeBox(
                        "right",
                        (0, 0, 1, 1),
                        text="Spotlight",
                        toggles={"primary": "on", "missiles": "on"},
                    ),
                ],
            },
        )
    )
    corrections = []
    validated = {}

    def ensure_stun(**kwargs):
        corrections.append(kwargs)
        return _stun_off_result(ui)

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True)

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=ui.go_home,
        swipe_fn=ui.swipe,
        event_swipe_fn=ui.event_swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: next(positions),
        ensure_poison_swamp_stun_fn=ensure_stun,
        detect_home_control_fn=lambda _frame: _home_evidence(),
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert len(corrections) == 1
    assert validated["ultimate_observations"]["Poison Swamp"] == {
        "primary": "on",
        "stun": "off",
    }
    assert ui.swipes == [
        *(("towards_top", "extended"),) * 3,
        ("towards_bottom", "medium"),
    ]


def test_preserved_modules_skip_module_navigation_and_validation():
    ui = _FakeUi()
    boxes = [
        UpgradeBox(
            "left",
            (0, 0, 1, 1),
            text=label,
            toggles={name: "on" for name in toggles if name != "stun"},
        )
        for label, toggles in ULTIMATE_REQUIREMENTS.items()
    ]
    requirements = {
        "ultimate_weapons": ULTIMATE_REQUIREMENTS,
        "loadout_policies": {"modules": "preserve"},
        "auto_pick_perks": True,
    }
    validated = {}

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True)

    result = run_read_only_gc_preflight(
        requirements,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=ui.go_home,
        swipe_fn=ui.swipe,
        event_swipe_fn=ui.event_swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {"left": boxes, "right": []},
        ensure_poison_swamp_stun_fn=lambda **_kwargs: _stun_off_result(ui),
        detect_home_control_fn=lambda _frame: _home_evidence(),
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert "navigation.menu_modules" not in ui.static_taps
    assert "navigation.menu_modules" not in ui.visible_taps
    assert validated["module_mode"] == "preserve"
    assert validated["modules_screen"] is None
    assert validated["module_requirements"] is None


def test_profile_without_perks_skips_perks_navigation_and_validation():
    ui = _FakeUi()
    boxes = [
        UpgradeBox(
            "left",
            (0, 0, 1, 1),
            text=label,
            toggles={name: "on" for name in toggles if name != "stun"},
        )
        for label, toggles in ULTIMATE_REQUIREMENTS.items()
    ]
    validated = {}

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True)

    result = run_read_only_gc_preflight(
        {**PREFLIGHT_REQUIREMENTS, "auto_pick_perks": False},
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=ui.go_home,
        swipe_fn=ui.swipe,
        event_swipe_fn=ui.event_swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {"left": boxes, "right": []},
        ensure_poison_swamp_stun_fn=lambda **_kwargs: _stun_off_result(ui),
        detect_home_control_fn=lambda _frame: _home_evidence(),
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert "navigation.open_perks" not in ui.static_taps
    assert "buttons.close:perks" not in ui.visible_taps
    assert validated["perks_screen"] is None


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
            toggles={name: "on" for name in toggles if name != "stun"},
        )
        for label, toggles in ULTIMATE_REQUIREMENTS.items()
    ]
    home_observations = 0

    def changing_home_control(_frame):
        nonlocal home_observations
        home_observations += 1
        control = (
            HomeBattleControl.RESUME_BATTLE
            if home_observations <= 2
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
        ensure_poison_swamp_stun_fn=lambda **_kwargs: _stun_off_result(ui),
        detect_home_control_fn=changing_home_control,
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is GcPreflightNavigationStatus.BATTLE_ENDED
    assert home_observations >= 3
    assert "buttons.battle_control:home" not in ui.static_taps


def test_running_menu_selection_retries_when_cinematic_mode_consumes_first_tap():
    ui = _FakeUi()
    ui.menu = None
    ui.secondary = {"CINEMATIC_MODE"}
    attempts = 0

    def cinematic_visible_tap(key, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            ui.secondary = set()
            return True
        return ui.visible_tap(key, **kwargs)

    frame = _select_running_menu(
        "navigation.goto_uw",
        "UW_MENU",
        capture_fn=ui.capture,
        detector=ui.detect,
        tap_visible_fn=cinematic_visible_tap,
        sleep_fn=lambda _seconds: None,
    )

    assert frame is ui.frame
    assert attempts == 2
    assert ui.menu == "UW_MENU"


def test_visible_navigation_rechecks_fresh_state_while_destination_renders():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    captures = 0
    taps = []
    sleeps = []

    def capture():
        nonlocal captures
        captures += 1
        return frame

    def tap_visible(key, **kwargs):
        taps.append((key, kwargs))
        return len(taps) == 3

    _guarded_visible_tap(
        "navigation.home_event",
        allowed_states={"HOME_SCREEN"},
        capture_fn=capture,
        detector=lambda _frame: {"state": "HOME_SCREEN"},
        tap_visible_fn=tap_visible,
        retries=4,
        retry_delay_s=0.25,
        sleep_fn=sleeps.append,
    )

    assert captures == 3
    assert [kwargs["retries"] for _key, kwargs in taps] == [0, 0, 0]
    assert sleeps == [0.25, 0.25]
