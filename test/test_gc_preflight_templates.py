from pathlib import Path

import cv2
import numpy as np

from core.auto_pick_perks import measure_auto_pick_perks
from core.clickmap_access import get_click
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
    CARDS_GC_PRESET_SLOT,
    FARM_PRESET_SLOT,
    INACTIVE_PRESET_SLOTS,
    measure_preset_slot_selection,
)


ROOT = Path(__file__).resolve().parents[1]
GC_ACTIVE_FIXTURE = ROOT / "test" / "fixtures" / "cards_gc_active_20260713.png"
GC_INACTIVE_FIXTURE = ROOT / "test" / "fixtures" / "cards_gc_inactive_20260715.png"
HOME_NEGATIVE_FIXTURE = (
    ROOT / "test" / "fixtures" / "home_screen_new_day_store_badge_20260713.png"
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

GC_ULTIMATE_REQUIREMENTS = {
    "Chain Lightning": {"primary": "on"},
    "Smart Missiles": {"primary": "on"},
    "Death Wave": {"primary": "on"},
    "Chrono Field": {"primary": "on"},
    "Inner Land Mines": {"primary": "on"},
    "Golden Tower": {"primary": "on"},
    "Poison Swamp": {"primary": "on"},
    "Black Hole": {"primary": "on"},
    "Spotlight": {"primary": "on", "missiles": "on"},
}


def _load(path: Path):
    image = cv2.imread(str(path))
    assert image is not None, f"fixture is unreadable: {path}"
    return image


def test_live_gc_cards_fixture_identifies_active_gc_preset():
    screen = _load(GC_ACTIVE_FIXTURE)

    point, confidence = get_match(
        "indicators.cards:gc_slot",
        screenshot=screen,
    )
    detection = detect_state_and_overlays(screen)
    selection = measure_preset_slot_selection(screen, CARDS_GC_PRESET_SLOT)

    assert point == (118, 420)
    assert confidence >= 0.99
    assert detection["state"] == "CARDS"
    assert detection["secondary_states"] == ["CARDS_GC_SLOT"]
    assert selection.selected


def test_inactive_gc_cards_slot_is_not_claimed_as_active():
    screen = _load(GC_INACTIVE_FIXTURE)
    detection = detect_state_and_overlays(screen)
    selection = measure_preset_slot_selection(screen, CARDS_GC_PRESET_SLOT)
    evidence = validate_gc_preflight_screens(
        cards_screen=screen,
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
    )

    assert detection["state"] == "CARDS"
    assert detection["secondary_states"] == ["CARDS_GC_SLOT"]
    assert not selection.selected
    assert not evidence.valid
    assert evidence.cards.missing_secondary == ("CARDS_GC_ACTIVE",)
    assert not evidence.cards_selection.selected


def test_gc_active_preset_does_not_match_home_screen():
    point, confidence = get_match(
        "indicators.cards:gc_slot",
        screenshot=_load(HOME_NEGATIVE_FIXTURE),
    )

    assert point is None
    assert confidence < 0.9


def test_live_gc_configuration_fixtures_form_complete_preflight_evidence():
    evidence = validate_gc_preflight_screens(
        cards_screen=_load(GC_ACTIVE_FIXTURE),
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
        cards_screen=_load(GC_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=screen,
        guardians_screen=_load(GUARDIAN_FIXTURE),
    )

    assert detection["state"] == "EVENT"
    assert detection["secondary_states"] == [
        "EVENT_BOTS_SCREEN",
        "BOTS_FARM_SLOT",
    ]
    assert not selection.selected
    assert not evidence.valid
    assert evidence.bots.missing_secondary == ("BOTS_FARM_ACTIVE",)
    assert not evidence.bots_selection.selected


def test_wrong_tabs_do_not_satisfy_bots_or_guardian_preflight_sections():
    evidence = validate_gc_preflight_screens(
        cards_screen=_load(GC_ACTIVE_FIXTURE),
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


def test_live_workshop_fixture_identifies_farm_and_selected_border():
    screen = _load(WORKSHOP_FIXTURE)
    detection = detect_state_and_overlays(screen)
    selection = measure_preset_slot_selection(screen, FARM_PRESET_SLOT)

    assert detection["state"] == "WORKSHOP"
    assert detection["secondary_states"] == ["WORKSHOP_FARM_SLOT"]
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
        label: {name: "on" for name in toggles}
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
        label: {name: "on" for name in toggles}
        for label, toggles in GC_ULTIMATE_REQUIREMENTS.items()
    }
    evidence = validate_gc_session_preflight_screens(
        cards_screen=_load(GC_ACTIVE_FIXTURE),
        workshop_screen=_load(WORKSHOP_FIXTURE),
        bots_screen=_load(BOT_FIXTURE),
        guardians_screen=_load(GUARDIAN_FIXTURE),
        perks_screen=_load(AUTO_PICK_FIXTURE),
        ultimate_requirements=GC_ULTIMATE_REQUIREMENTS,
        ultimate_observations=observed,
    )

    assert evidence.valid
    assert evidence.auto_pick_perks.enabled
    assert evidence.ultimate_weapons.valid
