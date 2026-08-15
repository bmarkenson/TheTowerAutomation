from pathlib import Path

import cv2

from core.gc_module_loadout import gc_module_loadout_evidence_from_assignments
from core.matcher import get_match
from core.tournament_preflight import (
    TOURNAMENT_SECTION_SPECS,
    load_tournament_contract,
    load_tournament_requirements,
    validate_tournament_session_preflight_screens,
)
from core.workshop_preset import measure_preset_slot_selection
from tools.validate_tournament_preflight import require_paused_control


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"
TOURNAMENT_FIXTURES = {
    "cards": FIXTURES / "cards_farm_inactive_20260717.png",
    "workshop": FIXTURES / "workshop_tourney_active_20260718.png",
    "bots": FIXTURES / "event_bots_farm_inactive_20260715.png",
    "guardians": FIXTURES / "guild_guardian_gc_inactive_20260715.png",
    "modules": (
        FIXTURES
        / "module_inventory_20260716"
        / "tournament_modules_overview_20260718.png"
    ),
}
FARM_FIXTURES = {
    "cards": FIXTURES / "cards_farm_active_20260717.png",
    "workshop": FIXTURES / "workshop_farm_active_20260714.png",
    "bots": FIXTURES / "event_bots_farm_active_20260713.png",
    "guardians": FIXTURES / "guild_guardian_gc_loadout_20260713.png",
    "modules": FIXTURES / "module_inventory_20260716" / "gc_modules_overview.png",
}


def _load(path: Path):
    image = cv2.imread(str(path))
    assert image is not None, f"fixture is unreadable: {path}"
    return image


def _ultimate_observations(requirements):
    return {
        label: dict(toggles)
        for label, toggles in requirements["ultimate_weapons"].items()
    }


def _validate(fixtures):
    requirements = load_tournament_requirements()
    return validate_tournament_session_preflight_screens(
        cards_screen=_load(fixtures["cards"]),
        workshop_screen=_load(fixtures["workshop"]),
        bots_screen=_load(fixtures["bots"]),
        guardians_screen=_load(fixtures["guardians"]),
        modules_screen=_load(fixtures["modules"]),
        module_requirements=requirements["modules"],
        module_mode=requirements["loadout_policies"]["modules"],
        ultimate_requirements=requirements["ultimate_weapons"],
        ultimate_observations=_ultimate_observations(requirements),
    )


def test_complete_tournament_fixture_set_passes_every_requirement():
    evidence = _validate(TOURNAMENT_FIXTURES)

    assert evidence.valid
    assert evidence.configuration.valid
    assert evidence.modules is not None and evidence.modules.valid
    assert not evidence.auto_pick_perks_required
    assert evidence.auto_pick_perks_valid
    assert evidence.ultimate_weapons.valid


def test_attached_tournament_can_defer_home_only_workshop_evidence():
    requirements = load_tournament_requirements()
    evidence = validate_tournament_session_preflight_screens(
        cards_screen=_load(TOURNAMENT_FIXTURES["cards"]),
        workshop_screen=None,
        bots_screen=_load(TOURNAMENT_FIXTURES["bots"]),
        guardians_screen=_load(TOURNAMENT_FIXTURES["guardians"]),
        modules_screen=_load(TOURNAMENT_FIXTURES["modules"]),
        module_requirements=requirements["modules"],
        module_mode=requirements["loadout_policies"]["modules"],
        ultimate_requirements=requirements["ultimate_weapons"],
        ultimate_observations=_ultimate_observations(requirements),
        deferred_checks=("workshop_preset",),
    )

    assert evidence.valid
    assert not evidence.configuration.valid
    assert not evidence.configuration.workshop.valid
    assert evidence.configuration.workshop.detected_state == "DEFERRED"
    assert evidence.deferred_checks == ("workshop_preset",)
    assert evidence.failed_checks == ()
    assert not evidence.requires_no_battle_repair
    payload = evidence.as_dict()
    assert payload["configuration"]["blocking_valid"] is True
    assert payload["deferred_checks"] == ["workshop_preset"]
    assert payload["failed_checks"] == []


def test_attached_tournament_accepts_exact_bound_workshop_save_evidence():
    requirements = load_tournament_requirements()
    evidence = validate_tournament_session_preflight_screens(
        cards_screen=_load(TOURNAMENT_FIXTURES["cards"]),
        workshop_screen=None,
        bots_screen=_load(TOURNAMENT_FIXTURES["bots"]),
        guardians_screen=_load(TOURNAMENT_FIXTURES["guardians"]),
        modules_screen=_load(TOURNAMENT_FIXTURES["modules"]),
        module_requirements=requirements["modules"],
        module_mode=requirements["loadout_policies"]["modules"],
        ultimate_requirements=requirements["ultimate_weapons"],
        ultimate_observations=_ultimate_observations(requirements),
        accepted_sections={
            "workshop": {
                "disposition": "save_match",
                "source": "bound_player_save_preflight",
            }
        },
    )

    assert evidence.valid
    assert evidence.configuration.valid
    assert evidence.configuration.workshop.valid
    assert evidence.deferred_checks == ()
    assert evidence.failed_checks == ()
    assert evidence.as_dict()["configuration"]["save_backed_sections"] == {
        "workshop": {
            "disposition": "save_match",
            "source": "bound_player_save_preflight",
        }
    }


def test_confident_tournament_module_variation_is_observed_without_failing():
    fixtures = {**TOURNAMENT_FIXTURES, "modules": FARM_FIXTURES["modules"]}
    evidence = _validate(fixtures)

    assert evidence.valid
    assert evidence.modules is not None
    assert evidence.modules.fully_observed
    assert not evidence.modules.valid
    assert evidence.modules_blocking_valid
    assert evidence.failed_checks == ()


def test_bound_module_observation_retains_save_source_in_session_evidence():
    requirements = load_tournament_requirements()
    module_boundary = gc_module_loadout_evidence_from_assignments(
        requirements["modules"],
        requirements["modules"],
    ).as_dict()
    module_boundary["source"] = "bound_player_save_preflight"

    evidence = validate_tournament_session_preflight_screens(
        cards_screen=_load(TOURNAMENT_FIXTURES["cards"]),
        workshop_screen=_load(TOURNAMENT_FIXTURES["workshop"]),
        bots_screen=_load(TOURNAMENT_FIXTURES["bots"]),
        guardians_screen=_load(TOURNAMENT_FIXTURES["guardians"]),
        modules_screen=None,
        module_requirements=requirements["modules"],
        module_mode=requirements["loadout_policies"]["modules"],
        ultimate_requirements=requirements["ultimate_weapons"],
        ultimate_observations=_ultimate_observations(requirements),
        module_boundary_evidence=module_boundary,
    )

    assert evidence.valid
    assert evidence.as_dict()["modules"]["source"] == (
        "bound_player_save_preflight"
    )


def test_tournament_preflight_forwards_mapping_observation_callback():
    requirements = load_tournament_requirements()
    observations = []

    evidence = validate_tournament_session_preflight_screens(
        cards_screen=_load(TOURNAMENT_FIXTURES["cards"]),
        workshop_screen=_load(TOURNAMENT_FIXTURES["workshop"]),
        bots_screen=_load(TOURNAMENT_FIXTURES["bots"]),
        guardians_screen=_load(TOURNAMENT_FIXTURES["guardians"]),
        modules_screen=_load(TOURNAMENT_FIXTURES["modules"]),
        module_requirements=requirements["modules"],
        module_mode=requirements["loadout_policies"]["modules"],
        ultimate_requirements=requirements["ultimate_weapons"],
        ultimate_observations=_ultimate_observations(requirements),
        mapping_observation_fn=lambda check_id, payload: observations.append(
            (check_id, payload)
        ),
    )

    assert evidence.valid
    assert {check_id for check_id, _payload in observations} == {
        "guardian_chips",
        "modules",
    }


def test_farm_setup_fails_the_tournament_contract():
    evidence = _validate(FARM_FIXTURES)

    assert not evidence.valid
    assert not evidence.configuration.valid
    assert evidence.modules is not None and not evidence.modules.valid


def test_tournament_slot_identity_does_not_imply_selection():
    cases = (
        (
            "indicators.cards:tournament_slot",
            FARM_FIXTURES["cards"],
            TOURNAMENT_SECTION_SPECS["cards"].selection_region,
        ),
        (
            "indicators.workshop:tourney_slot",
            FARM_FIXTURES["workshop"],
            TOURNAMENT_SECTION_SPECS["workshop"].selection_region,
        ),
        (
            "indicators.bots:amplify_slot",
            FARM_FIXTURES["bots"],
            TOURNAMENT_SECTION_SPECS["bots"].selection_region,
        ),
    )
    for label, fixture, region in cases:
        screen = _load(fixture)
        point, confidence = get_match(label, screenshot=screen)
        selection = measure_preset_slot_selection(screen, region)
        assert point is not None
        assert confidence >= 0.99
        assert not selection.selected


def test_tournament_profile_omits_non_applicable_auto_pick_perks():
    requirements = load_tournament_requirements()
    contract = load_tournament_contract()

    assert "auto_pick_perks" not in requirements
    assert requirements["loadout_policies"] == {"modules": "observe"}
    assert contract.module_mode == "observe"
    assert contract.module_preset == "tournament_standard"
    assert requirements["card_recharge_modes"] == {
        "Demon Mode": "auto_reactivate",
        "Nuke": "ready_after_recharge",
    }
    assert requirements["ultimate_weapons"]["Poison Swamp"]["stun"] == "on"
    assert requirements["damage_slider"] == {
        "mode": "enforce",
        "value": "1E2%",
    }
    assert requirements["orb_distance"] == {
        "mode": "enforce",
        "preset": "tournament_range_98_38",
        "resolved": {
            "range_basis": "98.38m",
            "extra": "87.16m",
            "workshop": "80.37m",
        },
        "range_presets": [
            {
                "range_basis": "30.00m",
                "extra": "30.00m",
                "workshop": "39.00m",
            },
            {
                "range_basis": "98.38m",
                "extra": "87.16m",
                "workshop": "80.37m",
            },
        ],
    }


def test_live_tournament_validation_requires_persisted_pause(tmp_path):
    control = tmp_path / "automation_ctl.json"
    control.write_text('{"state": "PAUSED"}\n', encoding="utf-8")
    require_paused_control(control)

    control.write_text('{"state": "RUNNING"}\n', encoding="utf-8")
    try:
        require_paused_control(control)
    except ValueError as exc:
        assert "requires PAUSED control" in str(exc)
    else:
        raise AssertionError("RUNNING control should be rejected")
