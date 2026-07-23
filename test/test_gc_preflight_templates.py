from pathlib import Path

import cv2
import numpy as np

from core.auto_pick_perks import measure_auto_pick_perks
from core.clickmap_access import get_click
from core.free_upgrade_locks import FARM_FREE_UPGRADE_LOCKS
from core.gc_preflight import (
    evaluate_ultimate_weapon_state,
    merge_ultimate_weapon_observations,
    validate_gc_preflight_screens,
    validate_gc_session_preflight_screens,
)
from core.matcher import get_match
from core.state_detector import detect_state_and_overlays
from core.upgrade_box_detector import UpgradeBox
from core.workshop_preset import (
    BOTS_FARM_PRESET_SLOT,
    CARDS_FARM_PRESET_SLOT,
    FARM_PRESET_SLOT,
    INACTIVE_PRESET_SLOTS,
    measure_preset_slot_selection,
)


ROOT = Path(__file__).resolve().parents[1]
FARM_ACTIVE_FIXTURE = (
    ROOT / "test" / "fixtures" / "cards_farm_active_20260717.png"
)
FARM_INACTIVE_FIXTURE = (
    ROOT / "test" / "fixtures" / "cards_farm_inactive_20260717.png"
)
LEGACY_GC_FIXTURE = ROOT / "test" / "fixtures" / "cards_gc_active_20260713.png"
HOME_NEGATIVE_FIXTURE = (
    ROOT / "test" / "fixtures" / "home_screen_new_day_store_badge_20260713.png"
)
HOME_SCROLLED_FIXTURE = (
    ROOT
    / "test"
    / "fixtures"
    / "gc_module_gate_20260716"
    / "home_scrolled_new_battle.png"
)
BOT_FIXTURE = ROOT / "test" / "fixtures" / "event_bots_farm_active_20260713.png"
BOT_INACTIVE_FIXTURE = (
    ROOT / "test" / "fixtures" / "event_bots_farm_inactive_20260715.png"
)
WORKSHOP_FIXTURE = ROOT / "test" / "fixtures" / "workshop_farm_active_20260714.png"
EVENT_MISSIONS_FIXTURE = ROOT / "test" / "fixtures" / "event_missions_20260713.png"
GUARDIAN_FIXTURE = (
    ROOT / "test" / "fixtures" / "guild_guardian_gc_loadout_20260713.png"
)
GUILD_MEMBERS_FIXTURE = (
    ROOT / "test" / "fixtures" / "guild_members_chest_20260713.png"
)
AUTO_PICK_FIXTURE = (
    ROOT
    / "test"
    / "fixtures"
    / "ui_state_20260714"
    / "active_perks_selected_auto_pick_on.png"
)
MODULES_FIXTURE = (
    ROOT
    / "test"
    / "fixtures"
    / "module_inventory_20260716"
    / "gc_modules_overview.png"
)

GC_MODULE_REQUIREMENTS = {
    "cannon_assist": "Being Annihilator",
    "cannon_primary": "Amplifying Strike",
    "generator_primary": "Black Hole Digestor",
    "generator_assist": "Singularity Harness",
    "armor_assist": "Anti-Cube Portal",
    "armor_primary": "Orbital Augment",
    "core_primary": "Multiverse Nexus",
    "core_assist": "Dimension Core",
}

GC_ULTIMATE_REQUIREMENTS = {
    "Chain Lightning": {"primary": "on"},
    "Smart Missiles": {"primary": "on"},
    "Death Wave": {"primary": "on"},
    "Chrono Field": {"primary": "on"},
    "Inner Land Mines": {"primary": "on"},
    "Golden Tower": {"primary": "on"},
    "Poison Swamp": {"primary": "on", "stun": "off"},
    "Black Hole": {"primary": "on"},
    "Spotlight": {"primary": "on", "missiles": "on"},
}


def _load(path: Path):
    image = cv2.imread(str(path))
    assert image is not None, f"fixture is unreadable: {path}"
    return image


def test_live_farm_cards_fixture_identifies_active_farm_preset():
    screen = _load(FARM_ACTIVE_FIXTURE)

    point, confidence = get_match(
        "indicators.cards:farm_slot",
        screenshot=screen,
    )
    detection = detect_state_and_overlays(screen)
    selection = measure_preset_slot_selection(screen, CARDS_FARM_PRESET_SLOT)

    assert point == (118, 420)
    assert confidence >= 0.99
    assert detection["state"] == "CARDS"
    assert detection["secondary_states"] == [
        "CARDS_FARM_SLOT",
        "CARDS_TOURNAMENT_SLOT",
    ]
    assert selection.selected


def test_inactive_farm_cards_slot_is_not_claimed_as_active():
    screen = _load(FARM_INACTIVE_FIXTURE)
    detection = detect_state_and_overlays(screen)
    selection = measure_preset_slot_selection(screen, CARDS_FARM_PRESET_SLOT)
    evidence = validate_gc_preflight_screens(
        cards_screen=screen,
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
    )

    assert detection["state"] == "CARDS"
    assert detection["secondary_states"] == [
        "CARDS_FARM_SLOT",
        "CARDS_TOURNAMENT_SLOT",
    ]
    assert not selection.selected
    assert not evidence.valid
    assert evidence.cards.missing_secondary == ("CARDS_FARM_ACTIVE",)
    assert not evidence.cards_selection.selected


def test_farm_cards_preset_does_not_match_home_screen():
    point, confidence = get_match(
        "indicators.cards:farm_slot",
        screenshot=_load(HOME_NEGATIVE_FIXTURE),
    )

    assert point is None
    assert confidence < 0.9


def test_legacy_gc_cards_preset_does_not_match_farm_identity():
    point, confidence = get_match(
        "indicators.cards:farm_slot",
        screenshot=_load(LEGACY_GC_FIXTURE),
    )

    assert point is None
    assert confidence < 0.9


def test_live_farm_configuration_fixtures_form_complete_preflight_evidence():
    evidence = validate_gc_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
    )

    assert evidence.valid
    assert evidence.cards.missing_secondary == ()
    assert evidence.cards_selection.selected
    assert evidence.workshop.missing_secondary == ()
    assert evidence.workshop_selection.selected
    assert evidence.bots.missing_secondary == ()
    assert evidence.bots_selection.selected
    assert evidence.guardians.missing_secondary == ()


def test_inactive_farm_bot_slot_is_not_claimed_as_active():
    screen = _load(BOT_INACTIVE_FIXTURE)
    detection = detect_state_and_overlays(screen)
    selection = measure_preset_slot_selection(screen, BOTS_FARM_PRESET_SLOT)
    evidence = validate_gc_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=screen,
        guardians_screen=_load(GUARDIAN_FIXTURE),
    )

    assert detection["state"] == "EVENT"
    assert detection["secondary_states"] == [
        "EVENT_BOTS_SCREEN",
        "BOTS_FARM_SLOT",
        "BOTS_AMPLIFY_SLOT",
    ]
    assert not selection.selected
    assert not evidence.valid
    assert evidence.bots.missing_secondary == ("BOTS_FARM_ACTIVE",)
    assert not evidence.bots_selection.selected


def test_wrong_tabs_do_not_satisfy_bots_or_guardian_preflight_sections():
    evidence = validate_gc_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(HOME_NEGATIVE_FIXTURE),
        bots_screen=_load(EVENT_MISSIONS_FIXTURE),
        guardians_screen=_load(GUILD_MEMBERS_FIXTURE),
    )

    assert not evidence.valid
    assert evidence.workshop.missing_secondary == (
        "WORKSHOP_FARM_ACTIVE",
        "WORKSHOP_FARM_SLOT",
    )
    assert evidence.bots.missing_secondary == (
        "BOTS_FARM_ACTIVE",
        "BOTS_FARM_SLOT",
        "EVENT_BOTS_SCREEN",
    )
    assert evidence.guardians.missing_secondary == (
        "GUARDIAN_FETCH_EQUIPPED",
        "GUARDIAN_SCOUT_EQUIPPED",
        "GUARDIAN_SUMMON_EQUIPPED",
        "GUILD_GUARDIAN_SCREEN",
    )


def test_gc_preflight_navigation_coordinates_are_mapped():
    assert get_click("navigation.open_perks") == (540, 60)
    assert get_click("buttons.perks:auto_pick") == (305, 265)
    assert get_click("navigation.goto_home") == (80, 1830)
    assert get_click("navigation.goto_workshop_home") == (270, 1830)
    assert get_click("navigation.event:bots_tab") == (900, 307)
    assert get_click("navigation.guild:guardian_tab") == (472, 313)


def test_home_preflight_destinations_require_visible_identity_evidence():
    home = _load(HOME_NEGATIVE_FIXTURE)

    event_point, event_confidence = get_match(
        "navigation.home_event",
        screenshot=home,
    )
    guild_point, guild_confidence = get_match(
        "navigation.home_guild",
        screenshot=home,
    )

    assert event_point == (115, 695)
    assert event_confidence >= 0.95
    assert guild_point == (122, 960)
    assert guild_confidence >= 0.99


def test_home_preflight_destinations_allow_retained_vertical_scroll():
    home = _load(HOME_SCROLLED_FIXTURE)

    event_point, event_confidence = get_match(
        "navigation.home_event",
        screenshot=home,
    )
    guild_point, guild_confidence = get_match(
        "navigation.home_guild",
        screenshot=home,
    )

    assert event_point == (115, 539)
    assert event_confidence >= 0.99
    assert guild_point == (122, 804)
    assert guild_confidence >= 0.95


def test_live_workshop_fixture_identifies_farm_and_selected_border():
    screen = _load(WORKSHOP_FIXTURE)
    detection = detect_state_and_overlays(screen)
    selection = measure_preset_slot_selection(screen, FARM_PRESET_SLOT)

    assert detection["state"] == "WORKSHOP"
    assert detection["secondary_states"] == [
        "WORKSHOP_FARM_SLOT",
        "WORKSHOP_TOURNEY_SLOT",
    ]
    assert selection.valid_region
    assert selection.selected
    assert selection.green_pixels >= 1000
    assert selection.cyan_pixels == 0


def test_live_workshop_inactive_slots_are_not_selected():
    screen = _load(WORKSHOP_FIXTURE)

    for region in INACTIVE_PRESET_SLOTS:
        selection = measure_preset_slot_selection(screen, region)
        assert selection.valid_region
        assert not selection.selected
        assert selection.green_pixels == 0
        assert selection.cyan_pixels >= 1000


def test_live_auto_pick_fixture_has_positive_enabled_evidence():
    screen = _load(AUTO_PICK_FIXTURE)
    evidence = measure_auto_pick_perks(screen)

    assert detect_state_and_overlays(screen)["state"] == "PERKS"
    assert evidence.valid_region
    assert evidence.enabled
    assert evidence.green_pixels >= 1800


def test_live_perks_fixture_has_its_dedicated_close_control():
    point, confidence = get_match(
        "buttons.close:perks",
        screenshot=_load(AUTO_PICK_FIXTURE),
    )

    assert point == (937, 157)
    assert confidence >= 0.99


def test_auto_pick_classifier_does_not_invent_enabled_from_empty_region():
    evidence = measure_auto_pick_perks(np.zeros((1920, 1080, 3), dtype=np.uint8))

    assert evidence.valid_region
    assert not evidence.enabled
    assert evidence.green_pixels == 0


def test_ultimate_weapon_evidence_requires_every_requested_toggle():
    observed = {
        label: dict(toggles)
        for label, toggles in GC_ULTIMATE_REQUIREMENTS.items()
    }
    observed["Spotlight"]["missiles"] = "off"
    evidence = evaluate_ultimate_weapon_state(GC_ULTIMATE_REQUIREMENTS, observed)

    assert not evidence.valid
    spotlight = next(result for result in evidence.weapons if result.label == "Spotlight")
    assert spotlight.observed
    assert not spotlight.valid
    assert spotlight.mismatched_toggles == ("missiles=on (actual=off)",)


def test_ultimate_weapon_boxes_merge_across_scroll_positions():
    boxes = [
        UpgradeBox("left", (0, 0, 1, 1), text="Chain Lightning", toggles={"primary": "on"}),
        UpgradeBox(
            "left",
            (0, 0, 1, 1),
            text="Spotlight",
            toggles={"primary": "on"},
        ),
        UpgradeBox(
            "left",
            (0, 0, 1, 1),
            text="Spotlight",
            toggles={"missiles": "on"},
        ),
    ]

    assert merge_ultimate_weapon_observations(boxes) == {
        "Chain Lightning": {"primary": "on"},
        "Spotlight": {"primary": "on", "missiles": "on"},
    }


def test_complete_session_preflight_combines_all_positive_evidence():
    observed = {
        label: dict(toggles)
        for label, toggles in GC_ULTIMATE_REQUIREMENTS.items()
    }
    evidence = validate_gc_session_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
        modules_screen=_load(MODULES_FIXTURE),
        perks_screen=_load(AUTO_PICK_FIXTURE),
        module_requirements=GC_MODULE_REQUIREMENTS,
        ultimate_requirements=GC_ULTIMATE_REQUIREMENTS,
        ultimate_observations=observed,
    )

    assert evidence.valid
    assert evidence.modules.valid
    assert evidence.auto_pick_perks.enabled
    assert evidence.ultimate_weapons.valid


def test_session_preflight_rehydrates_home_boundary_configuration_and_modules():
    observed = {
        label: dict(toggles)
        for label, toggles in GC_ULTIMATE_REQUIREMENTS.items()
    }
    captured = validate_gc_session_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
        modules_screen=_load(MODULES_FIXTURE),
        perks_screen=_load(AUTO_PICK_FIXTURE),
        module_requirements=GC_MODULE_REQUIREMENTS,
        ultimate_requirements=GC_ULTIMATE_REQUIREMENTS,
        ultimate_observations=observed,
    )

    retained = validate_gc_session_preflight_screens(
        perks_screen=_load(AUTO_PICK_FIXTURE),
        module_requirements=GC_MODULE_REQUIREMENTS,
        ultimate_requirements=GC_ULTIMATE_REQUIREMENTS,
        ultimate_observations=observed,
        configuration_boundary_evidence=captured.configuration.as_dict(),
        module_boundary_evidence=captured.modules.as_dict(),
    )

    assert retained.valid
    assert retained.configuration == captured.configuration
    assert retained.modules == captured.modules


def test_session_preflight_retains_verified_new_battle_lock_evidence():
    observed = {
        label: dict(toggles)
        for label, toggles in GC_ULTIMATE_REQUIREMENTS.items()
    }
    boundary_evidence = {
        "status": "verified",
        "boundary": "NEW_BATTLE",
        "required": list(FARM_FREE_UPGRADE_LOCKS),
        "checked": True,
        "valid": True,
        "has_authoritative_mismatch": False,
        "locks": [
            {"label": label, "state": "checked", "valid": True}
            for label in FARM_FREE_UPGRADE_LOCKS
        ],
        "changed_labels": [],
    }

    evidence = validate_gc_session_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
        modules_screen=_load(MODULES_FIXTURE),
        perks_screen=_load(AUTO_PICK_FIXTURE),
        module_requirements=GC_MODULE_REQUIREMENTS,
        ultimate_requirements=GC_ULTIMATE_REQUIREMENTS,
        ultimate_observations=observed,
        free_upgrade_lock_requirements=FARM_FREE_UPGRADE_LOCKS,
        free_upgrade_lock_boundary_evidence=boundary_evidence,
    )

    assert evidence.valid
    assert evidence.free_upgrade_locks_valid is True
    assert evidence.as_dict()["free_upgrade_locks"] == {
        **boundary_evidence,
        "blocking_valid": True,
    }


def test_missing_boundary_lock_evidence_is_deferred_without_session_failure():
    observed = {
        label: dict(toggles)
        for label, toggles in GC_ULTIMATE_REQUIREMENTS.items()
    }

    evidence = validate_gc_session_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
        modules_screen=_load(MODULES_FIXTURE),
        perks_screen=_load(AUTO_PICK_FIXTURE),
        module_requirements=GC_MODULE_REQUIREMENTS,
        ultimate_requirements=GC_ULTIMATE_REQUIREMENTS,
        ultimate_observations=observed,
        free_upgrade_lock_requirements=FARM_FREE_UPGRADE_LOCKS,
    )

    payload = evidence.as_dict()["free_upgrade_locks"]
    assert evidence.valid
    assert evidence.free_upgrade_locks_valid is None
    assert "free_upgrade_locks" not in evidence.failed_checks
    assert evidence.deferred_checks == ("free_upgrade_locks",)
    assert not evidence.requires_no_battle_repair
    assert payload["status"] == "unavailable_deferred"
    assert payload["checked"] is False
    assert payload["valid"] is None
    assert payload["blocking_valid"] is True


def test_bots_waiver_does_not_waive_auto_pick_perks():
    observed = {
        label: dict(toggles)
        for label, toggles in GC_ULTIMATE_REQUIREMENTS.items()
    }
    evidence = validate_gc_session_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_INACTIVE_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
        modules_screen=_load(MODULES_FIXTURE),
        perks_screen=np.zeros((1920, 1080, 3), dtype=np.uint8),
        module_requirements=GC_MODULE_REQUIREMENTS,
        ultimate_requirements=GC_ULTIMATE_REQUIREMENTS,
        ultimate_observations=observed,
        waivers={"bots_preset": {"decision_id": "flame"}},
    )

    payload = evidence.as_dict()
    assert not evidence.configuration.bots.valid
    assert evidence.configuration_valid
    assert not evidence.valid
    assert evidence.failed_checks == ("auto_pick_perks",)
    assert payload["configuration"]["blocking_valid"] is True
    assert payload["waivers"] == {
        "bots_preset": {"decision_id": "flame"}
    }


def test_observed_module_mismatch_is_reported_without_blocking_preflight():
    observed = {
        label: dict(toggles)
        for label, toggles in GC_ULTIMATE_REQUIREMENTS.items()
    }
    expected_modules = dict(GC_MODULE_REQUIREMENTS)
    expected_modules["generator_primary"] = "Project Funding"

    evidence = validate_gc_session_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
        modules_screen=_load(MODULES_FIXTURE),
        perks_screen=_load(AUTO_PICK_FIXTURE),
        module_requirements=expected_modules,
        module_mode="observe",
        ultimate_requirements=GC_ULTIMATE_REQUIREMENTS,
        ultimate_observations=observed,
    )

    payload = evidence.as_dict()
    assert evidence.valid
    assert evidence.modules is not None
    assert not evidence.modules.valid
    assert not evidence.requires_no_battle_repair
    assert payload["modules"]["matches_expected"] is False
    assert payload["modules"]["blocking_valid"] is True


def test_preserved_modules_are_neither_opened_nor_required():
    observed = {
        label: dict(toggles)
        for label, toggles in GC_ULTIMATE_REQUIREMENTS.items()
    }

    evidence = validate_gc_session_preflight_screens(
        cards_screen=_load(FARM_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
        perks_screen=_load(AUTO_PICK_FIXTURE),
        module_mode="preserve",
        ultimate_requirements=GC_ULTIMATE_REQUIREMENTS,
        ultimate_observations=observed,
    )

    assert evidence.valid
    assert evidence.modules is None
    assert evidence.as_dict()["modules"] == {
        "mode": "preserve",
        "checked": False,
        "matches_expected": None,
        "blocking_valid": True,
    }
