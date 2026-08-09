from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from core.battle_lifecycle import HomeBattleControl
from core.free_upgrade_locks import FARM_FREE_UPGRADE_LOCKS
from core.gc_preflight_navigation import (
    GcPreflightNavigationStatus,
    _ensure_auto_pick_perks_enabled,
    _guarded_visible_tap,
    _home_ultimate_weapon_observations,
    _log_gc_preflight_workflow,
    _select_running_menu,
    run_read_only_gc_preflight,
)
from core.home_battle import HomeBattleEvidence
from core.player_save_temporal import BoundRunningAttachmentSaveEvidence
from core.poison_swamp_stun import PoisonSwampStunState
from core.upgrade_box_detector import UpgradeBox
from test.player_save_temporal_fixtures import running_attachment_observations


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
        if key == "buttons.perks:auto_pick":
            self.frame[220:310, 255:355] = (0, 255, 0)
        elif key == "navigation.goto_home":
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


def _lock_boundary_evidence():
    locks = [
        {"label": label, "state": "checked", "valid": True}
        for label in FARM_FREE_UPGRADE_LOCKS
    ]
    return {
        "status": "verified",
        "boundary": "NEW_BATTLE",
        "required": list(FARM_FREE_UPGRADE_LOCKS),
        "checked": True,
        "valid": True,
        "has_authoritative_mismatch": False,
        "locks": locks,
        "changed_labels": [],
    }


def test_game_over_observation_aborts_without_sending_input():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    static_taps = []
    visible_taps = []
    swipes = []
    go_home_calls = []

    with (
        patch("core.gc_preflight_navigation.log_action_intent") as action_log,
        patch("core.gc_preflight_navigation.log_result") as result_log,
    ):
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
    action_log.assert_called_once()
    assert action_log.call_args.args[0] == "Checking session configuration"
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Session configuration check interrupted — the battle ended during inspection"
    )


def test_mismatch_result_names_the_wrong_module_in_operator_summary():
    evidence_payload = {
        "failed_checks": ["modules"],
        "modules": {
            "slots": [
                {
                    "slot_key": "generator_assist",
                    "expected": "Singularity Harness",
                    "actual": "Galaxy Compressor",
                    "match_status": "matched",
                    "valid": False,
                }
            ]
        },
    }
    evidence = SimpleNamespace(
        valid=False,
        deferred_checks=(),
        as_dict=lambda: evidence_payload,
    )

    @_log_gc_preflight_workflow
    def mismatch(_requirements):
        return SimpleNamespace(
            status=GcPreflightNavigationStatus.MISMATCH,
            reason="configuration mismatch",
            evidence=evidence,
            valid=False,
        )

    with (
        patch("core.gc_preflight_navigation.log_action_intent"),
        patch("core.gc_preflight_navigation.log_result") as result_log,
    ):
        mismatch({})

    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Session configuration check complete — mismatch found; "
        "Generator Assist module: expected Singularity Harness, observed "
        "Galaxy Compressor"
    )


def test_complete_result_surfaces_observed_module_variation():
    evidence_payload = {
        "module_mode": "observe",
        "failed_checks": [],
        "modules": {
            "mode": "observe",
            "slots": [
                {
                    "slot_key": "generator_assist",
                    "expected": "Singularity Harness",
                    "actual": "Galaxy Compressor",
                    "match_status": "matched",
                    "valid": False,
                }
            ],
        },
    }
    evidence = SimpleNamespace(
        valid=True,
        deferred_checks=(),
        as_dict=lambda: evidence_payload,
    )

    @_log_gc_preflight_workflow
    def complete(_requirements):
        return SimpleNamespace(
            status=GcPreflightNavigationStatus.COMPLETE,
            reason="all requirements verified",
            evidence=evidence,
            valid=True,
        )

    with (
        patch("core.gc_preflight_navigation.log_action_intent"),
        patch("core.gc_preflight_navigation.log_result") as result_log,
    ):
        complete({})

    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Session configuration check complete — required settings verified; "
        "module variation observed — Generator Assist module: reference "
        "Singularity Harness, observed Galaxy Compressor"
    )


def test_enabled_auto_pick_perks_does_not_send_a_toggle():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    taps = []

    result = _ensure_auto_pick_perks_enabled(
        frame,
        capture_fn=lambda: (_ for _ in ()).throw(
            AssertionError("enabled evidence must not recapture")
        ),
        detector=lambda _frame: {"state": "PERKS"},
        safe_tap_fn=lambda *args, **kwargs: taps.append((args, kwargs)),
        sleep_fn=lambda _seconds: None,
        measure_fn=lambda _frame: SimpleNamespace(
            valid_region=True,
            enabled=True,
        ),
    )

    assert result is frame
    assert taps == []


def test_guarded_route_corrects_declared_in_run_controls_and_returns_to_running():
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
        {
            **PREFLIGHT_REQUIREMENTS,
            "free_upgrade_locks": list(FARM_FREE_UPGRADE_LOCKS),
        },
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
    assert "navigation.goto_uw" not in ui.visible_taps
    assert "navigation.menu_modules" not in ui.static_taps
    assert "navigation.menu_event" not in ui.static_taps
    assert "navigation.menu_guild" not in ui.static_taps
    assert "navigation.goto_modules_home" not in ui.static_taps
    assert "navigation.home_event" not in ui.visible_taps
    assert "navigation.home_guild" not in ui.visible_taps
    assert ui.static_taps.count("navigation.goto_home") == 1
    assert ui.static_taps.count("buttons.perks:auto_pick") == 1
    assert ui.visible_taps.count("buttons.return_to_game") == 4
    all_keys = set(ui.static_taps + ui.visible_taps)
    assert not any("surrender" in key for key in all_keys)
    assert not any("preset" in key for key in all_keys)
    assert not any("toggle" in key for key in all_keys)
    assert len(corrections) == 1
    assert validated["ultimate_observations"]["Poison Swamp"]["stun"] == "off"
    assert ui.swipes == [("towards_top", "extended")] * 3
    assert ui.event_swipes == ["gesture_targets.goto_top:event_bots"]


def test_attached_route_defers_workshop_without_going_home():
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
        return SimpleNamespace(
            valid=True,
            deferred_checks=("workshop_preset",),
        )

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=lambda: (_ for _ in ()).throw(
            AssertionError("attached validation must stay in battle")
        ),
        swipe_fn=ui.swipe,
        event_swipe_fn=ui.event_swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {
            "left": boxes,
            "right": [],
        },
        ensure_poison_swamp_stun_fn=lambda **_kwargs: _stun_off_result(ui),
        stay_in_battle=True,
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert result.reason == "active requirements verified; boundary checks deferred"
    assert ui.state == "RUNNING"
    assert validated["workshop_screen"] is None
    assert validated["deferred_checks"] == ("workshop_preset",)
    assert "navigation.Cards" in ui.visible_taps
    assert "navigation.menu_modules" in ui.visible_taps
    assert "navigation.menu_event" in ui.visible_taps
    assert "navigation.menu_guild" in ui.visible_taps
    assert "navigation.goto_workshop_home" not in ui.static_taps
    assert "navigation.goto_home" not in ui.static_taps
    assert "buttons.battle_control:home" not in ui.static_taps


def test_attached_route_uses_bound_workshop_save_evidence_without_going_home():
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

    class BoundSave:
        def consume(self, check_id):
            return "Tourney" if check_id == "workshop_preset" else None

    validated = {}

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True, deferred_checks=())

    result = run_read_only_gc_preflight(
        {**PREFLIGHT_REQUIREMENTS, "workshop_preset": "Tourney"},
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=lambda: (_ for _ in ()).throw(
            AssertionError("bound save evidence must avoid the Home route")
        ),
        swipe_fn=ui.swipe,
        event_swipe_fn=ui.event_swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {
            "left": boxes,
            "right": [],
        },
        ensure_poison_swamp_stun_fn=lambda **_kwargs: _stun_off_result(ui),
        player_save_preflight=BoundSave(),
        stay_in_battle=True,
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert result.reason == "all requirements verified"
    assert validated["accepted_sections"] == {
        "workshop": {
            "disposition": "save_match",
            "source": "bound_player_save_preflight",
        }
    }
    assert "deferred_checks" not in validated
    assert "navigation.goto_workshop_home" not in ui.static_taps
    assert "buttons.battle_control:home" not in ui.static_taps


def test_attached_route_uses_all_bound_round_invariant_save_evidence():
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
    observed_modules = {
        **MODULE_REQUIREMENTS,
        "generator_primary": "Project Funding",
    }
    carried = {
        "workshop_preset": "Tourney",
        "bots_preset": "Amplify",
        "guardian_chips": ["Scout", "Attack", "Ally"],
        "modules": observed_modules,
    }
    bound = BoundRunningAttachmentSaveEvidence(
        running_attachment_observations(carried),
        lambda: SimpleNamespace(
            runtime_session_id="runtime-1",
            activity_scope_id="scope-1",
            target="private-target",
            target_generation=3,
            active_battle_observed=True,
        ),
    )

    validated = {}

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True, deferred_checks=())

    result = run_read_only_gc_preflight(
        {
            "cards_deck": "Tournament",
            "workshop_preset": "Tourney",
            "bots_preset": "Amplify",
            "guardian_chips": ["Attack", "Ally", "Scout"],
            "ultimate_weapons": ULTIMATE_REQUIREMENTS,
            "loadout_policies": {"modules": "observe"},
            "modules": MODULE_REQUIREMENTS,
        },
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=lambda: (_ for _ in ()).throw(
            AssertionError("attached validation must stay in battle")
        ),
        swipe_fn=ui.swipe,
        event_swipe_fn=ui.event_swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {
            "left": boxes,
            "right": [],
        },
        ensure_poison_swamp_stun_fn=lambda **_kwargs: _stun_off_result(ui),
        player_save_preflight=bound,
        stay_in_battle=True,
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert result.reason == "all requirements verified"
    assert validated["accepted_sections"] == {
        "workshop": {
            "disposition": "save_match",
            "source": "bound_player_save_preflight",
        },
        "bots": {
            "disposition": "save_match",
            "source": "bound_player_save_preflight",
        },
        "guardians": {
            "disposition": "save_match",
            "source": "bound_player_save_preflight",
        },
    }
    module_boundary = validated["module_boundary_evidence"]
    assert module_boundary["source"] == "bound_player_save_preflight"
    assert module_boundary["disposition"] == "save_observation"
    assert module_boundary["fully_observed"] is True
    assert module_boundary["valid"] is False
    assert "deferred_checks" not in validated
    assert "navigation.Cards" in ui.visible_taps
    assert ui.swipes
    assert "navigation.menu_modules" not in ui.visible_taps
    assert "navigation.menu_event" not in ui.visible_taps
    assert "navigation.menu_guild" not in ui.visible_taps
    assert "navigation.goto_workshop_home" not in ui.static_taps
    assert "buttons.battle_control:home" not in ui.static_taps
    for check_id in carried:
        assert bound.consume(check_id) is None


def test_bound_new_battle_carry_skips_all_redundant_configuration_ui():
    ui = _FakeUi()
    carried = {
        "cards_deck": "Farm",
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "modules": dict(MODULE_REQUIREMENTS),
        "free_upgrade_locks": list(FARM_FREE_UPGRADE_LOCKS),
        "auto_pick_perks": True,
        "ultimate_weapon_primaries": {
            label: {"primary": "on"}
            for label in ULTIMATE_REQUIREMENTS
        },
        "poison_swamp_stun": "off",
        "spotlight_missiles": "on",
    }
    consumed = []

    class BoundSave:
        def consume(self, check_id):
            consumed.append(check_id)
            return carried.get(check_id)

        def invalidate(self, reason):
            raise AssertionError(f"matching carried evidence invalidated: {reason}")

    requirements = {
        "cards_deck": "Farm",
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "modules": dict(MODULE_REQUIREMENTS),
        "free_upgrade_locks": list(FARM_FREE_UPGRADE_LOCKS),
        "auto_pick_perks": True,
        "ultimate_weapons": ULTIMATE_REQUIREMENTS,
        "loadout_policies": {"modules": "enforce"},
    }

    result = run_read_only_gc_preflight(
        requirements,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=lambda: (_ for _ in ()).throw(
            AssertionError("bound new-battle evidence must avoid the Home route")
        ),
        swipe_fn=ui.swipe,
        event_swipe_fn=ui.event_swipe,
        detect_boxes_fn=lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError("bound Ultimate Weapon evidence must avoid UI")
            )
        ),
        ensure_poison_swamp_stun_fn=lambda **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError("bound Stun evidence must avoid UI")
            )
        ),
        player_save_preflight=BoundSave(),
        detect_home_control_fn=lambda _frame: _home_evidence(),
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert result.reason == "all requirements verified"
    assert consumed == [
        "auto_pick_perks",
        "cards_deck",
        "workshop_preset",
        "bots_preset",
        "guardian_chips",
        "modules",
        "free_upgrade_locks",
        "ultimate_weapon_primaries",
        "poison_swamp_stun",
        "spotlight_missiles",
    ]
    assert result.evidence is not None
    rendered = result.evidence.as_dict()
    assert set(rendered["configuration"]["save_backed_sections"]) == {
        "cards",
        "workshop",
        "bots",
        "guardians",
    }
    assert rendered["modules"]["source"] == "bound_player_save_preflight"
    assert rendered["auto_pick_perks"]["source"] == (
        "bound_player_save_preflight"
    )
    assert rendered["free_upgrade_locks"]["source"] == (
        "bound_player_save_preflight"
    )
    assert ui.static_taps == []
    assert ui.visible_taps == []
    assert ui.swipes == []


def test_attached_route_keeps_ui_fallback_for_invariant_mismatches():
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
    carried = {
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "modules": {
            **MODULE_REQUIREMENTS,
            "generator_primary": "Project Funding",
        },
    }

    bound = BoundRunningAttachmentSaveEvidence(
        running_attachment_observations(carried),
        lambda: SimpleNamespace(
            runtime_session_id="runtime-1",
            activity_scope_id="scope-1",
            target="private-target",
            target_generation=3,
            active_battle_observed=True,
        ),
    )

    validated = {}

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(
            valid=True,
            deferred_checks=("workshop_preset",),
        )

    result = run_read_only_gc_preflight(
        {
            "cards_deck": "Tournament",
            "workshop_preset": "Tourney",
            "bots_preset": "Amplify",
            "guardian_chips": ["Attack", "Ally", "Scout"],
            "ultimate_weapons": ULTIMATE_REQUIREMENTS,
            "modules": MODULE_REQUIREMENTS,
        },
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=lambda: (_ for _ in ()).throw(
            AssertionError("attached validation must stay in battle")
        ),
        swipe_fn=ui.swipe,
        event_swipe_fn=ui.event_swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {
            "left": boxes,
            "right": [],
        },
        ensure_poison_swamp_stun_fn=lambda **_kwargs: _stun_off_result(ui),
        player_save_preflight=bound,
        stay_in_battle=True,
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert result.reason == "active requirements verified; boundary checks deferred"
    assert validated["deferred_checks"] == ("workshop_preset",)
    assert "accepted_sections" not in validated
    assert "module_boundary_evidence" not in validated
    assert "navigation.menu_modules" in ui.visible_taps
    assert "navigation.menu_event" in ui.visible_taps
    assert "navigation.menu_guild" in ui.visible_taps
    assert "navigation.goto_workshop_home" not in ui.static_taps
    assert "buttons.battle_control:home" not in ui.static_taps


def test_farm_route_consumes_home_boundary_evidence_without_revisiting_sections():
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
    setup_evidence = {
        "configuration": {"valid": True, "source": "NEW_BATTLE"},
        "modules": {"checked": True, "valid": True},
        "ultimate_weapons": {
            "boundary": "NEW_BATTLE",
            "checked": ["Poison Swamp.stun"],
            "observations": {"Poison Swamp": {"stun": "off"}},
            "valid": True,
            "changed": False,
        },
    }
    validated = {}

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True)

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=lambda: (_ for _ in ()).throw(
            AssertionError("Home route must not repeat")
        ),
        swipe_fn=ui.swipe,
        detect_boxes_fn=lambda _frame, **_kwargs: {"left": boxes, "right": []},
        ensure_poison_swamp_stun_fn=lambda **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError("fresh Home Stun proof must be reused")
            )
        ),
        no_battle_setup_evidence=setup_evidence,
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert ui.state == "RUNNING"
    assert validated["configuration_boundary_evidence"] == setup_evidence[
        "configuration"
    ]
    assert validated["module_boundary_evidence"] == setup_evidence["modules"]
    assert validated["ultimate_observations"]["Poison Swamp"] == {
        "primary": "on",
        "stun": "off",
    }
    assert "navigation.Cards" not in ui.visible_taps
    assert "navigation.menu_modules" not in ui.visible_taps
    assert "navigation.menu_event" not in ui.visible_taps
    assert "navigation.menu_guild" not in ui.visible_taps
    assert "navigation.goto_workshop_home" not in ui.static_taps
    assert "buttons.battle_control:home" not in ui.static_taps


def test_bound_save_components_skip_redundant_auto_pick_and_uw_navigation():
    ui = _FakeUi()
    setup_evidence = {
        "configuration": {"valid": True, "source": "NEW_BATTLE"},
        "modules": {"checked": True, "valid": True},
    }
    carried = {
        "auto_pick_perks": True,
        "ultimate_weapon_primaries": {
            label: {"primary": "on"}
            for label in ULTIMATE_REQUIREMENTS
        },
        "poison_swamp_stun": "off",
        "spotlight_missiles": "on",
    }
    consumed = []

    class BoundSave:
        def consume(self, check_id):
            consumed.append(check_id)
            return carried.get(check_id)

        def invalidate(self, reason):
            raise AssertionError(f"matching carried evidence invalidated: {reason}")

    validated = {}

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True)

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=lambda: (_ for _ in ()).throw(
            AssertionError("Home route must not repeat")
        ),
        swipe_fn=ui.swipe,
        detect_boxes_fn=lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError("UW UI must not be inspected")
            )
        ),
        no_battle_setup_evidence=setup_evidence,
        player_save_preflight=BoundSave(),
        detect_home_control_fn=lambda _frame: _home_evidence(),
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert consumed == [
        "auto_pick_perks",
        "ultimate_weapon_primaries",
        "poison_swamp_stun",
        "spotlight_missiles",
    ]
    assert validated["auto_pick_boundary_evidence"] == {
        "source": "bound_player_save_preflight",
        "value": True,
    }
    assert validated["ultimate_observations"] == {
        "Golden Tower": {"primary": "on"},
        "Black Hole": {"primary": "on"},
        "Poison Swamp": {"primary": "on", "stun": "off"},
        "Spotlight": {"primary": "on", "missiles": "on"},
    }
    assert "navigation.open_perks" not in ui.static_taps
    assert "navigation.goto_uw" not in ui.visible_taps
    assert ui.swipes == []


def test_poison_stun_repair_preserves_unrelated_carried_evidence():
    ui = _FakeUi()
    setup_evidence = {
        "configuration": {
            "valid": True,
            "source": "NEW_BATTLE",
            "save_backed_sections": {
                "cards": {"disposition": "save_match"},
            },
        },
        "modules": {"checked": True, "valid": True},
    }
    carried = {
        "auto_pick_perks": True,
        "ultimate_weapon_primaries": {
            label: {"primary": "on"}
            for label in ULTIMATE_REQUIREMENTS
        },
        "poison_swamp_stun": None,
        "spotlight_missiles": "on",
    }
    invalidations = []
    ui_verifications = []

    class BoundSave:
        def consume(self, check_id):
            return carried.get(check_id)

        def invalidate(self, reason):
            invalidations.append(reason)

        def record_ui_verification(self, check_id, *, changed):
            ui_verifications.append((check_id, changed))
            return True

    poison_box = UpgradeBox(
        "left",
        (0, 0, 1, 1),
        text="Poison Swamp",
        toggles={"primary": "on"},
    )
    validated = {}

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True)

    result = run_read_only_gc_preflight(
        {
            **PREFLIGHT_REQUIREMENTS,
            "free_upgrade_locks": list(FARM_FREE_UPGRADE_LOCKS),
        },
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=ui.go_home,
        swipe_fn=ui.swipe,
        detect_boxes_fn=lambda *_args, **_kwargs: {
            "left": [poison_box],
            "right": [],
        },
        ensure_poison_swamp_stun_fn=lambda **_kwargs: _stun_off_result(
            ui,
            changed=True,
        ),
        no_battle_setup_evidence=setup_evidence,
        free_upgrade_lock_boundary_evidence={
            "status": "save_match",
            "source": "bound_player_save_preflight",
        },
        player_save_preflight=BoundSave(),
        detect_home_control_fn=lambda _frame: _home_evidence(),
        sleep_fn=lambda _seconds: None,
        validate_fn=validate,
    )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    assert invalidations == []
    assert ui_verifications == [("poison_swamp_stun", True)]
    assert validated["ultimate_observations"] == {
        "Golden Tower": {"primary": "on"},
        "Black Hole": {"primary": "on"},
        "Poison Swamp": {"primary": "on", "stun": "off"},
        "Spotlight": {"primary": "on", "missiles": "on"},
    }
    assert validated["configuration_boundary_evidence"] == setup_evidence[
        "configuration"
    ]
    assert validated["free_upgrade_lock_boundary_evidence"] == {
        "status": "save_match",
        "source": "bound_player_save_preflight",
    }
    assert "navigation.Cards" not in ui.visible_taps


def test_uw_ui_contradiction_to_carried_save_match_invalidates_snapshot():
    ui = _FakeUi()
    carried = {
        "auto_pick_perks": True,
        "ultimate_weapon_primaries": {
            label: {"primary": "on"}
            for label in ULTIMATE_REQUIREMENTS
        },
        "poison_swamp_stun": None,
        "spotlight_missiles": "on",
    }
    invalidations = []

    class BoundSave:
        def consume(self, check_id):
            return carried.get(check_id)

        def invalidate(self, reason):
            invalidations.append(reason)

    result = run_read_only_gc_preflight(
        PREFLIGHT_REQUIREMENTS,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.visible_tap,
        go_home_fn=ui.go_home,
        swipe_fn=ui.swipe,
        detect_boxes_fn=lambda *_args, **_kwargs: {
            "left": [
                UpgradeBox(
                    "left",
                    (0, 0, 1, 1),
                    text="Golden Tower",
                    toggles={"primary": "off"},
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
        player_save_preflight=BoundSave(),
        detect_home_control_fn=lambda _frame: _home_evidence(),
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is GcPreflightNavigationStatus.FAILED
    assert "contradicted current UI" in result.reason
    assert invalidations == ["save_ui_contradiction"]


def test_home_boundary_accepts_tournament_poison_swamp_stun_on():
    observations = _home_ultimate_weapon_observations(
        {
            "ultimate_weapons": {
                "boundary": "NEW_BATTLE",
                "checked": ["Poison Swamp.stun"],
                "observations": {"Poison Swamp": {"stun": "on"}},
                "valid": True,
            }
        }
    )

    assert observations == {"Poison Swamp": {"stun": "on"}}


def test_active_farm_route_never_inspects_locks_and_carries_boundary_evidence():
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
    boundary_evidence = _lock_boundary_evidence()
    validated = {}

    def validate(**kwargs):
        validated.update(kwargs)
        return SimpleNamespace(valid=True)

    with patch("core.free_upgrade_locks.inspect_free_upgrade_locks") as inspect:
        result = run_read_only_gc_preflight(
            {
                **PREFLIGHT_REQUIREMENTS,
                "free_upgrade_locks": list(FARM_FREE_UPGRADE_LOCKS),
            },
            capture_fn=ui.capture,
            detector=ui.detect,
            safe_tap_fn=ui.safe_tap,
            tap_visible_fn=ui.visible_tap,
            go_home_fn=ui.go_home,
            swipe_fn=ui.swipe,
            event_swipe_fn=ui.event_swipe,
            detect_boxes_fn=lambda _frame, **_kwargs: {
                "left": boxes,
                "right": [],
            },
            ensure_poison_swamp_stun_fn=lambda **_kwargs: _stun_off_result(ui),
            free_upgrade_lock_boundary_evidence=boundary_evidence,
            detect_home_control_fn=lambda _frame: _home_evidence(),
            sleep_fn=lambda _seconds: None,
            validate_fn=validate,
        )

    assert result.status is GcPreflightNavigationStatus.COMPLETE
    inspect.assert_not_called()
    assert validated["free_upgrade_lock_requirements"] == list(
        FARM_FREE_UPGRADE_LOCKS
    )
    assert validated["free_upgrade_lock_boundary_evidence"] == boundary_evidence


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
        {
            key: value
            for key, value in PREFLIGHT_REQUIREMENTS.items()
            if key != "auto_pick_perks"
        },
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


def test_profile_auto_pick_skip_does_not_open_or_change_perks():
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
        {
            **PREFLIGHT_REQUIREMENTS,
            "_gate_waivers": {
                "auto_pick_perks": {
                    "source": "strategy_profile",
                    "scope": "every_run",
                }
            },
        },
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
    assert "buttons.perks:auto_pick" not in ui.static_taps
    assert "buttons.close:perks" not in ui.visible_taps
    assert validated["perks_screen"] is None
    assert validated["waivers"]["auto_pick_perks"]["scope"] == "every_run"


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


def test_running_menu_selection_does_not_collapse_already_selected_menu():
    ui = _FakeUi()
    ui.menu = "UW_MENU"

    frame = _select_running_menu(
        "navigation.goto_uw",
        "UW_MENU",
        capture_fn=ui.capture,
        detector=ui.detect,
        tap_visible_fn=ui.visible_tap,
        sleep_fn=lambda _seconds: None,
    )

    assert frame is ui.frame
    assert ui.visible_taps == []
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
    assert [
        kwargs["failure_log_level"] for _key, kwargs in taps
    ] == ["DEBUG", "DEBUG", "DEBUG"]
    assert sleeps == [0.25, 0.25]
