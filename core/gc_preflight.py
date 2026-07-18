"""Visual evidence evaluation for the staged GC configuration preflight."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

from core.auto_pick_perks import AutoPickPerksEvidence, measure_auto_pick_perks
from core.gc_module_loadout import (
    GcModuleLoadoutEvidence,
    evaluate_gc_module_loadout,
)
from core.state_detector import detect_state_and_overlays
from core.workshop_preset import (
    BOTS_FARM_PRESET_SLOT,
    CARDS_FARM_PRESET_SLOT,
    PresetSlotSelection,
    measure_preset_slot_selection,
)


Detection = Mapping[str, Any]
Detector = Callable[[Any], Detection]


@dataclass(frozen=True)
class GcSectionSpec:
    name: str
    expected_state: str
    required_secondary: frozenset[str]


@dataclass(frozen=True)
class GcSectionResult:
    name: str
    valid: bool
    detected_state: str
    required_secondary: tuple[str, ...]
    detected_secondary: tuple[str, ...]
    missing_secondary: tuple[str, ...]


@dataclass(frozen=True)
class GcPreflightEvidence:
    cards: GcSectionResult
    cards_selection: PresetSlotSelection
    workshop: GcSectionResult
    workshop_selection: PresetSlotSelection
    bots: GcSectionResult
    bots_selection: PresetSlotSelection
    guardians: GcSectionResult

    @property
    def valid(self) -> bool:
        return (
            self.cards.valid
            and self.workshop.valid
            and self.bots.valid
            and self.guardians.valid
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["valid"] = self.valid
        return payload


@dataclass(frozen=True)
class UltimateWeaponResult:
    label: str
    valid: bool
    observed: bool
    required_toggles: tuple[str, ...]
    detected_toggles: tuple[str, ...]
    mismatched_toggles: tuple[str, ...]


@dataclass(frozen=True)
class UltimateWeaponEvidence:
    weapons: tuple[UltimateWeaponResult, ...]

    @property
    def valid(self) -> bool:
        return bool(self.weapons) and all(weapon.valid for weapon in self.weapons)


@dataclass(frozen=True)
class GcSessionPreflightEvidence:
    configuration: GcPreflightEvidence
    module_mode: str
    modules: Optional[GcModuleLoadoutEvidence]
    auto_pick_perks: AutoPickPerksEvidence
    ultimate_weapons: UltimateWeaponEvidence

    @property
    def modules_blocking_valid(self) -> bool:
        return self.module_mode != "enforce" or bool(
            self.modules is not None and self.modules.valid
        )

    @property
    def valid(self) -> bool:
        return (
            self.configuration.valid
            and self.modules_blocking_valid
            and self.auto_pick_perks.enabled
            and self.ultimate_weapons.valid
        )

    @property
    def requires_no_battle_repair(self) -> bool:
        """Whether a mismatch belongs to a no-battle configuration surface."""

        return (
            not self.configuration.valid
            or bool(
                self.module_mode == "enforce"
                and self.modules is not None
                and self.modules.has_authoritative_mismatch
            )
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["configuration"]["valid"] = self.configuration.valid
        if self.modules is None:
            payload["modules"] = {
                "mode": self.module_mode,
                "checked": False,
                "matches_expected": None,
                "blocking_valid": self.modules_blocking_valid,
            }
        else:
            payload["modules"] = self.modules.as_dict()
            payload["modules"].update(
                mode=self.module_mode,
                checked=True,
                matches_expected=self.modules.valid,
                blocking_valid=self.modules_blocking_valid,
            )
        payload["ultimate_weapons"]["valid"] = self.ultimate_weapons.valid
        payload["valid"] = self.valid
        return payload


GC_SECTION_SPECS = {
    "cards": GcSectionSpec(
        name="cards",
        expected_state="CARDS",
        required_secondary=frozenset({"CARDS_FARM_ACTIVE", "CARDS_FARM_SLOT"}),
    ),
    "workshop": GcSectionSpec(
        name="workshop",
        expected_state="WORKSHOP",
        required_secondary=frozenset(
            {"WORKSHOP_FARM_SLOT", "WORKSHOP_FARM_ACTIVE"}
        ),
    ),
    "bots": GcSectionSpec(
        name="bots",
        expected_state="EVENT",
        required_secondary=frozenset(
            {"EVENT_BOTS_SCREEN", "BOTS_FARM_ACTIVE", "BOTS_FARM_SLOT"}
        ),
    ),
    "guardians": GcSectionSpec(
        name="guardians",
        expected_state="GUILD",
        required_secondary=frozenset(
            {
                "GUILD_GUARDIAN_SCREEN",
                "GUARDIAN_FETCH_EQUIPPED",
                "GUARDIAN_SUMMON_EQUIPPED",
                "GUARDIAN_SCOUT_EQUIPPED",
            }
        ),
    ),
}


def evaluate_gc_section(spec: GcSectionSpec, detection: Detection) -> GcSectionResult:
    """Evaluate one already-captured configuration screen."""

    detected_state = str(detection.get("state") or "UNKNOWN")
    detected_secondary = frozenset(detection.get("secondary_states") or ())
    missing = spec.required_secondary - detected_secondary
    return GcSectionResult(
        name=spec.name,
        valid=detected_state == spec.expected_state and not missing,
        detected_state=detected_state,
        required_secondary=tuple(sorted(spec.required_secondary)),
        detected_secondary=tuple(sorted(detected_secondary)),
        missing_secondary=tuple(sorted(missing)),
    )


def validate_gc_preflight_screens(
    *,
    cards_screen,
    workshop_screen,
    bots_screen,
    guardians_screen,
    detector: Detector = detect_state_and_overlays,
) -> GcPreflightEvidence:
    """Validate captured GC preflight sections without sending input."""

    cards_detection = dict(detector(cards_screen))
    cards_selection = measure_preset_slot_selection(
        cards_screen,
        CARDS_FARM_PRESET_SLOT,
    )
    cards_secondary = set(cards_detection.get("secondary_states") or ())
    if (
        cards_detection.get("state") == "CARDS"
        and "CARDS_FARM_SLOT" in cards_secondary
        and cards_selection.selected
    ):
        cards_secondary.add("CARDS_FARM_ACTIVE")
    cards_detection["secondary_states"] = sorted(cards_secondary)

    workshop_detection = dict(detector(workshop_screen))
    workshop_selection = measure_preset_slot_selection(workshop_screen)
    workshop_secondary = set(workshop_detection.get("secondary_states") or ())
    if (
        workshop_detection.get("state") == "WORKSHOP"
        and "WORKSHOP_FARM_SLOT" in workshop_secondary
        and workshop_selection.selected
    ):
        workshop_secondary.add("WORKSHOP_FARM_ACTIVE")
    workshop_detection["secondary_states"] = sorted(workshop_secondary)

    bots_detection = dict(detector(bots_screen))
    bots_selection = measure_preset_slot_selection(
        bots_screen,
        BOTS_FARM_PRESET_SLOT,
    )
    bots_secondary = set(bots_detection.get("secondary_states") or ())
    if (
        bots_detection.get("state") == "EVENT"
        and "EVENT_BOTS_SCREEN" in bots_secondary
        and "BOTS_FARM_SLOT" in bots_secondary
        and bots_selection.selected
    ):
        bots_secondary.add("BOTS_FARM_ACTIVE")
    bots_detection["secondary_states"] = sorted(bots_secondary)

    return GcPreflightEvidence(
        cards=evaluate_gc_section(GC_SECTION_SPECS["cards"], cards_detection),
        cards_selection=cards_selection,
        workshop=evaluate_gc_section(
            GC_SECTION_SPECS["workshop"], workshop_detection
        ),
        workshop_selection=workshop_selection,
        bots=evaluate_gc_section(GC_SECTION_SPECS["bots"], bots_detection),
        bots_selection=bots_selection,
        guardians=evaluate_gc_section(
            GC_SECTION_SPECS["guardians"], detector(guardians_screen)
        ),
    )


def merge_ultimate_weapon_observations(
    boxes: Iterable[Any],
) -> dict[str, dict[str, str]]:
    """Merge detector boxes from multiple scroll positions by weapon label."""

    observed: dict[str, dict[str, str]] = {}
    for box in boxes:
        label = str(getattr(box, "text", "") or "").strip()
        toggles = getattr(box, "toggles", None)
        if not label or not isinstance(toggles, Mapping):
            continue
        observed.setdefault(label, {}).update({
            str(name).strip(): str(state).strip().lower()
            for name, state in toggles.items()
            if str(name).strip()
        })
    return observed


def evaluate_ultimate_weapon_state(
    requirements: Mapping[str, Mapping[str, Any]],
    observed: Mapping[str, Mapping[str, Any]],
) -> UltimateWeaponEvidence:
    """Compare read-only toggle observations with profile requirements."""

    observed_by_label = {
        str(label).strip().lower(): {
            str(name).strip().lower(): str(state).strip().lower()
            for name, state in toggles.items()
        }
        for label, toggles in observed.items()
    }
    results: list[UltimateWeaponResult] = []
    for label, required in requirements.items():
        canonical_label = str(label).strip()
        detected = observed_by_label.get(canonical_label.lower())
        required_pairs = {
            str(name).strip().lower(): (
                "on"
                if state is True
                else "off"
                if state is False
                else str(state).strip().lower()
            )
            for name, state in required.items()
        }
        mismatched = []
        if detected is None:
            mismatched = [
                f"{name}={state}" for name, state in sorted(required_pairs.items())
            ]
        else:
            mismatched = [
                f"{name}={state} (actual={detected.get(name, 'missing')})"
                for name, state in sorted(required_pairs.items())
                if detected.get(name) != state
            ]
        results.append(
            UltimateWeaponResult(
                label=canonical_label,
                valid=detected is not None and not mismatched,
                observed=detected is not None,
                required_toggles=tuple(
                    f"{name}={state}" for name, state in sorted(required_pairs.items())
                ),
                detected_toggles=tuple(
                    f"{name}={state}"
                    for name, state in sorted((detected or {}).items())
                ),
                mismatched_toggles=tuple(mismatched),
            )
        )
    return UltimateWeaponEvidence(tuple(results))


def validate_gc_session_preflight_screens(
    *,
    cards_screen,
    workshop_screen,
    bots_screen,
    guardians_screen,
    modules_screen=None,
    perks_screen,
    module_requirements: Optional[Mapping[str, Any]] = None,
    module_mode: str = "enforce",
    ultimate_requirements: Mapping[str, Mapping[str, Any]],
    ultimate_observations: Mapping[str, Mapping[str, Any]],
    detector: Detector = detect_state_and_overlays,
) -> GcSessionPreflightEvidence:
    """Validate every currently implemented read-only GC session requirement."""

    configuration = validate_gc_preflight_screens(
        cards_screen=cards_screen,
        workshop_screen=workshop_screen,
        bots_screen=bots_screen,
        guardians_screen=guardians_screen,
        detector=detector,
    )
    perks_detection = detector(perks_screen)
    auto_pick = measure_auto_pick_perks(perks_screen)
    if perks_detection.get("state") != "PERKS":
        auto_pick = AutoPickPerksEvidence(
            region=auto_pick.region,
            valid_region=auto_pick.valid_region,
            enabled=False,
            green_pixels=auto_pick.green_pixels,
        )
    if module_mode not in {"enforce", "observe", "preserve"}:
        raise ValueError(f"unsupported module policy {module_mode!r}")
    modules = None
    if module_mode != "preserve":
        if modules_screen is None or not isinstance(module_requirements, Mapping):
            raise ValueError(
                f"module policy {module_mode!r} requires screen and requirements"
            )
        modules = evaluate_gc_module_loadout(
            modules_screen,
            module_requirements,
        )
    return GcSessionPreflightEvidence(
        configuration=configuration,
        module_mode=module_mode,
        modules=modules,
        auto_pick_perks=auto_pick,
        ultimate_weapons=evaluate_ultimate_weapon_state(
            ultimate_requirements,
            ultimate_observations,
        ),
    )


__all__ = [
    "GC_SECTION_SPECS",
    "GcPreflightEvidence",
    "GcSessionPreflightEvidence",
    "GcSectionResult",
    "GcSectionSpec",
    "UltimateWeaponEvidence",
    "UltimateWeaponResult",
    "evaluate_gc_section",
    "evaluate_ultimate_weapon_state",
    "merge_ultimate_weapon_observations",
    "validate_gc_preflight_screens",
    "validate_gc_session_preflight_screens",
]
