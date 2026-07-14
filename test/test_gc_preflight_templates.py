from pathlib import Path

import cv2

from core.clickmap_access import get_click
from core.gc_preflight import validate_gc_preflight_screens
from core.matcher import get_match
from core.state_detector import detect_state_and_overlays
from core.workshop_preset import (
    FARM_PRESET_SLOT,
    INACTIVE_PRESET_SLOTS,
    measure_preset_slot_selection,
)


ROOT = Path(__file__).resolve().parents[1]
GC_ACTIVE_FIXTURE = ROOT / "test" / "fixtures" / "cards_gc_active_20260713.png"
HOME_NEGATIVE_FIXTURE = (
    ROOT / "test" / "fixtures" / "home_screen_new_day_store_badge_20260713.png"
)
BOT_FIXTURE = ROOT / "test" / "fixtures" / "event_bots_farm_active_20260713.png"
WORKSHOP_FIXTURE = ROOT / "test" / "fixtures" / "workshop_farm_active_20260714.png"
EVENT_MISSIONS_FIXTURE = ROOT / "test" / "fixtures" / "event_missions_20260713.png"
GUARDIAN_FIXTURE = (
    ROOT / "test" / "fixtures" / "guild_guardian_gc_loadout_20260713.png"
)
GUILD_MEMBERS_FIXTURE = (
    ROOT / "test" / "fixtures" / "guild_members_chest_20260713.png"
)


def _load(path: Path):
    image = cv2.imread(str(path))
    assert image is not None, f"fixture is unreadable: {path}"
    return image


def test_live_gc_cards_fixture_identifies_active_gc_preset():
    screen = _load(GC_ACTIVE_FIXTURE)

    point, confidence = get_match(
        "indicators.cards:gc_active",
        screenshot=screen,
    )
    detection = detect_state_and_overlays(screen)

    assert point == (118, 420)
    assert confidence >= 0.99
    assert detection["state"] == "CARDS"
    assert detection["secondary_states"] == ["CARDS_GC_ACTIVE"]


def test_gc_active_preset_does_not_match_home_screen():
    point, confidence = get_match(
        "indicators.cards:gc_active",
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
    assert evidence.workshop.missing_secondary == ()
    assert evidence.workshop_selection.selected
    assert evidence.bots.missing_secondary == ()
    assert evidence.guardians.missing_secondary == ()


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
        "EVENT_BOTS_SCREEN",
    )
    assert evidence.guardians.missing_secondary == (
        "GUARDIAN_FETCH_EQUIPPED",
        "GUARDIAN_SCOUT_EQUIPPED",
        "GUARDIAN_SUMMON_EQUIPPED",
        "GUILD_GUARDIAN_SCREEN",
    )


def test_gc_preflight_navigation_coordinates_are_mapped():
    assert get_click("navigation.goto_workshop_home") == (270, 1830)
    assert get_click("navigation.Event") == (910, 490)
    assert get_click("navigation.Guild") == (910, 595)
    assert get_click("navigation.event:bots_tab") == (900, 307)
    assert get_click("navigation.guild:guardian_tab") == (472, 313)


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
