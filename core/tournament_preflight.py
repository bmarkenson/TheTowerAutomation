"""Read-only evaluation for Tournament session configuration."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import yaml

from core.gc_preflight import (
    Detector,
    GcSectionSpec,
    GcSessionPreflightEvidence,
    detect_state_and_overlays,
    validate_gc_session_preflight_screens,
)


ROOT = Path(__file__).resolve().parents[1]
TOURNAMENT_PROFILE_PATH = ROOT / "config" / "run_profiles" / "tournament.yaml"
MODULE_LOADOUTS_PATH = ROOT / "config" / "loadouts" / "modules.yaml"


TOURNAMENT_SECTION_SPECS = {
    "cards": GcSectionSpec(
        name="cards",
        expected_state="CARDS",
        required_secondary=frozenset(
            {"CARDS_TOURNAMENT_SLOT", "CARDS_TOURNAMENT_ACTIVE"}
        ),
        selection_region=(225, 371, 210, 98),
        slot_secondary="CARDS_TOURNAMENT_SLOT",
        selected_secondary="CARDS_TOURNAMENT_ACTIVE",
    ),
    "workshop": GcSectionSpec(
        name="workshop",
        expected_state="WORKSHOP",
        required_secondary=frozenset(
            {"WORKSHOP_TOURNEY_SLOT", "WORKSHOP_TOURNEY_ACTIVE"}
        ),
        selection_region=(225, 185, 210, 98),
        slot_secondary="WORKSHOP_TOURNEY_SLOT",
        selected_secondary="WORKSHOP_TOURNEY_ACTIVE",
    ),
    "bots": GcSectionSpec(
        name="bots",
        expected_state="EVENT",
        required_secondary=frozenset(
            {"EVENT_BOTS_SCREEN", "BOTS_AMPLIFY_SLOT", "BOTS_AMPLIFY_ACTIVE"}
        ),
        selection_region=(713, 496, 347, 98),
        slot_secondary="BOTS_AMPLIFY_SLOT",
        selected_secondary="BOTS_AMPLIFY_ACTIVE",
    ),
    "guardians": GcSectionSpec(
        name="guardians",
        expected_state="GUILD",
        required_secondary=frozenset(
            {
                "GUILD_GUARDIAN_SCREEN",
                "GUARDIAN_ATTACK_EQUIPPED",
                "GUARDIAN_ALLY_EQUIPPED",
                "GUARDIAN_SCOUT_EQUIPPED",
            }
        ),
    ),
}


def _load_mapping(path: Path, description: str) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{description} must be a mapping: {path}")
    return data


def load_tournament_requirements(
    *,
    profile_path: Path = TOURNAMENT_PROFILE_PATH,
    modules_path: Path = MODULE_LOADOUTS_PATH,
) -> dict[str, Any]:
    """Resolve and validate the compact Tournament preflight contract."""

    profile = _load_mapping(profile_path, "Tournament profile")
    if profile.get("name") != "tournament":
        raise ValueError("Tournament profile must declare name: tournament")
    requirements = copy.deepcopy(profile.get("invariants"))
    if not isinstance(requirements, dict):
        raise ValueError("Tournament profile invariants must be a mapping")
    fixed_values = {
        "cards_deck": "Tournament",
        "workshop_preset": "Tourney",
        "bots_preset": "Amplify",
    }
    for key, expected in fixed_values.items():
        if str(requirements.get(key) or "").strip() != expected:
            raise ValueError(f"Tournament profile {key} must be {expected!r}")
    guardian_chips = {
        str(chip).strip() for chip in requirements.get("guardian_chips") or ()
    }
    if guardian_chips != {"Attack", "Ally", "Scout"}:
        raise ValueError(
            "Tournament profile guardian_chips must contain Attack, Ally, and Scout"
        )
    if requirements.get("auto_pick_perks") is not False:
        raise ValueError("Tournament profile auto_pick_perks must be false")

    ultimate_weapons = requirements.get("ultimate_weapons")
    if not isinstance(ultimate_weapons, dict) or not ultimate_weapons:
        raise ValueError("Tournament Ultimate Weapon requirements must be a mapping")
    for label, toggles in ultimate_weapons.items():
        if not isinstance(toggles, dict) or not toggles:
            raise ValueError(f"Tournament Ultimate Weapon {label!r} needs toggles")
        for toggle, state in toggles.items():
            normalized = (
                "on"
                if state is True
                else "off"
                if state is False
                else str(state).strip().lower()
            )
            if normalized not in {"on", "off"}:
                raise ValueError(
                    f"Tournament Ultimate Weapon {label!r} toggle {toggle!r} "
                    "must be on or off"
                )
            toggles[toggle] = normalized

    loadout = profile.get("loadout")
    if not isinstance(loadout, dict):
        raise ValueError("Tournament profile loadout must be a mapping")
    preset_name = str(loadout.get("modules") or "").strip()
    catalog = _load_mapping(modules_path, "Module loadout catalog")
    presets = catalog.get("presets")
    if not isinstance(presets, dict) or preset_name not in presets:
        raise ValueError(f"unknown Tournament module preset {preset_name!r}")
    modules = presets[preset_name]
    if not isinstance(modules, dict):
        raise ValueError(f"Tournament module preset {preset_name!r} must be a mapping")

    return {
        **requirements,
        "loadout_policies": {"modules": "enforce"},
        "modules": copy.deepcopy(modules),
    }


def validate_tournament_session_preflight_screens(
    *,
    cards_screen,
    workshop_screen,
    bots_screen,
    guardians_screen,
    modules_screen,
    perks_screen=None,
    module_requirements: Mapping[str, Any],
    module_mode: str = "enforce",
    ultimate_requirements: Mapping[str, Mapping[str, Any]],
    ultimate_observations: Mapping[str, Mapping[str, Any]],
    detector: Detector = detect_state_and_overlays,
) -> GcSessionPreflightEvidence:
    """Validate Tournament presets, loadouts, and controls without Perks."""

    if perks_screen is not None:
        raise ValueError("Tournament preflight does not accept a Perks screen")
    return validate_gc_session_preflight_screens(
        cards_screen=cards_screen,
        workshop_screen=workshop_screen,
        bots_screen=bots_screen,
        guardians_screen=guardians_screen,
        modules_screen=modules_screen,
        perks_screen=None,
        module_requirements=module_requirements,
        module_mode=module_mode,
        ultimate_requirements=ultimate_requirements,
        ultimate_observations=ultimate_observations,
        detector=detector,
        section_specs=TOURNAMENT_SECTION_SPECS,
        auto_pick_perks_required=False,
    )


__all__ = [
    "TOURNAMENT_SECTION_SPECS",
    "load_tournament_requirements",
    "validate_tournament_session_preflight_screens",
]
