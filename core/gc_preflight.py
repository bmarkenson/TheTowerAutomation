"""Visual evidence evaluation for the staged GC configuration preflight."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from core.auto_pick_perks import AutoPickPerksEvidence, measure_auto_pick_perks
from core.free_upgrade_locks import (
    FreeUpgradeLocksEvidence,
    normalize_free_upgrade_lock_requirements,
)
from core.gc_module_loadout import (
    GcModuleLoadoutEvidence,
    evaluate_gc_module_loadout,
)
from core.state_detector import detect_state_and_overlays
from core.workshop_preset import (
    BOTS_FARM_PRESET_SLOT,
    CARDS_FARM_PRESET_SLOT,
    FARM_PRESET_SLOT,
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
    selection_region: Optional[tuple[int, int, int, int]] = None
    slot_secondary: Optional[str] = None
    selected_secondary: Optional[str] = None


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
    free_upgrade_lock_requirements: tuple[str, ...]
    free_upgrade_locks: Optional[FreeUpgradeLocksEvidence]
    module_mode: str
    modules: Optional[GcModuleLoadoutEvidence]
    auto_pick_perks_required: bool
    auto_pick_perks: AutoPickPerksEvidence
    ultimate_weapons: UltimateWeaponEvidence

    @property
    def modules_blocking_valid(self) -> bool:
        return self.module_mode != "enforce" or bool(
            self.modules is not None and self.modules.valid
        )

    @property
    def auto_pick_perks_valid(self) -> bool:
        return not self.auto_pick_perks_required or self.auto_pick_perks.enabled

    @property
    def free_upgrade_locks_valid(self) -> bool:
        if not self.free_upgrade_lock_requirements:
            return True
        if self.free_upgrade_locks is None or not self.free_upgrade_locks.valid:
            return False
        return tuple(
            lock.label for lock in self.free_upgrade_locks.locks
        ) == self.free_upgrade_lock_requirements

    @property
    def valid(self) -> bool:
        return (
            self.configuration.valid
            and self.free_upgrade_locks_valid
            and self.modules_blocking_valid
            and self.auto_pick_perks_valid
            and self.ultimate_weapons.valid
        )

    @property
    def requires_no_battle_repair(self) -> bool:
        """Whether a mismatch belongs to a no-battle configuration surface."""

        return (
            not self.configuration.valid
            or bool(
                self.free_upgrade_lock_requirements
                and self.free_upgrade_locks is not None
                and self.free_upgrade_locks.has_authoritative_mismatch
            )
            or bool(
                self.module_mode == "enforce"
                and self.modules is not None
                and self.modules.has_authoritative_mismatch
            )
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["configuration"]["valid"] = self.configuration.valid
        if self.free_upgrade_locks is None:
            payload["free_upgrade_locks"] = {
                "required": list(self.free_upgrade_lock_requirements),
                "checked": False,
                "valid": self.free_upgrade_locks_valid,
            }
        else:
            payload["free_upgrade_locks"] = self.free_upgrade_locks.as_dict()
            payload["free_upgrade_locks"].update(
                required=list(self.free_upgrade_lock_requirements),
                checked=True,
                valid=self.free_upgrade_locks_valid,
            )
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
        payload["auto_pick_perks"].update(
            required=self.auto_pick_perks_required,
            checked=self.auto_pick_perks_required,
            valid=self.auto_pick_perks_valid,
        )
        payload["ultimate_weapons"]["valid"] = self.ultimate_weapons.valid
        payload["valid"] = self.valid
        return payload


GC_SECTION_SPECS = {
    "cards": GcSectionSpec(
        name="cards",
        expected_state="CARDS",
        required_secondary=frozenset({"CARDS_FARM_ACTIVE", "CARDS_FARM_SLOT"}),
        selection_region=CARDS_FARM_PRESET_SLOT,
        slot_secondary="CARDS_FARM_SLOT",
        selected_secondary="CARDS_FARM_ACTIVE",
    ),
    "workshop": GcSectionSpec(
        name="workshop",
        expected_state="WORKSHOP",
        required_secondary=frozenset(
            {"WORKSHOP_FARM_SLOT", "WORKSHOP_FARM_ACTIVE"}
        ),
        selection_region=FARM_PRESET_SLOT,
        slot_secondary="WORKSHOP_FARM_SLOT",
        selected_secondary="WORKSHOP_FARM_ACTIVE",
    ),
    "bots": GcSectionSpec(
        name="bots",
        expected_state="EVENT",
        required_secondary=frozenset(
            {"EVENT_BOTS_SCREEN", "BOTS_FARM_ACTIVE", "BOTS_FARM_SLOT"}
        ),
        selection_region=BOTS_FARM_PRESET_SLOT,
        slot_secondary="BOTS_FARM_SLOT",
        selected_secondary="BOTS_FARM_ACTIVE",
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


def _detect_section_selection(
    screen,
    spec: GcSectionSpec,
    detector: Detector,
) -> tuple[dict[str, Any], Optional[PresetSlotSelection]]:
    detection = dict(detector(screen))
    if spec.selection_region is None:
        return detection, None
    selection = measure_preset_slot_selection(screen, spec.selection_region)
    secondary = set(detection.get("secondary_states") or ())
    if (
        detection.get("state") == spec.expected_state
        and spec.slot_secondary in secondary
        and selection.selected
        and spec.selected_secondary
    ):
        secondary.add(spec.selected_secondary)
    detection["secondary_states"] = sorted(secondary)
    return detection, selection


def validate_gc_preflight_screens(
    *,
    cards_screen,
    workshop_screen,
    bots_screen,
    guardians_screen,
    detector: Detector = detect_state_and_overlays,
    section_specs: Mapping[str, GcSectionSpec] = GC_SECTION_SPECS,
) -> GcPreflightEvidence:
    """Validate captured profile preflight sections without sending input."""

    required_names = {"cards", "workshop", "bots", "guardians"}
    missing_names = sorted(required_names - set(section_specs))
    if missing_names:
        raise ValueError(
            "preflight section specs are missing: " + ", ".join(missing_names)
        )

    cards_detection, cards_selection = _detect_section_selection(
        cards_screen,
        section_specs["cards"],
        detector,
    )
    workshop_detection, workshop_selection = _detect_section_selection(
        workshop_screen,
        section_specs["workshop"],
        detector,
    )
    bots_detection, bots_selection = _detect_section_selection(
        bots_screen,
        section_specs["bots"],
        detector,
    )
    if (
        cards_selection is None
        or workshop_selection is None
        or bots_selection is None
    ):
        raise ValueError("cards, workshop, and bots specs require selection regions")
    guardians_detection, _guardians_selection = _detect_section_selection(
        guardians_screen,
        section_specs["guardians"],
        detector,
    )

    return GcPreflightEvidence(
        cards=evaluate_gc_section(section_specs["cards"], cards_detection),
        cards_selection=cards_selection,
        workshop=evaluate_gc_section(
            section_specs["workshop"], workshop_detection
        ),
        workshop_selection=workshop_selection,
        bots=evaluate_gc_section(section_specs["bots"], bots_detection),
        bots_selection=bots_selection,
        guardians=evaluate_gc_section(
            section_specs["guardians"], guardians_detection
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
    free_upgrade_lock_requirements: Optional[Sequence[Any]] = None,
    free_upgrade_locks: Optional[FreeUpgradeLocksEvidence] = None,
    detector: Detector = detect_state_and_overlays,
    section_specs: Mapping[str, GcSectionSpec] = GC_SECTION_SPECS,
    auto_pick_perks_required: bool = True,
) -> GcSessionPreflightEvidence:
    """Validate every currently implemented read-only session requirement."""

    normalized_free_upgrade_locks = (
        normalize_free_upgrade_lock_requirements(free_upgrade_lock_requirements)
        if free_upgrade_lock_requirements is not None
        else ()
    )

    configuration = validate_gc_preflight_screens(
        cards_screen=cards_screen,
        workshop_screen=workshop_screen,
        bots_screen=bots_screen,
        guardians_screen=guardians_screen,
        detector=detector,
        section_specs=section_specs,
    )
    auto_pick = measure_auto_pick_perks(perks_screen)
    if auto_pick_perks_required and (
        perks_screen is None or detector(perks_screen).get("state") != "PERKS"
    ):
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
        free_upgrade_lock_requirements=normalized_free_upgrade_locks,
        free_upgrade_locks=free_upgrade_locks,
        module_mode=module_mode,
        modules=modules,
        auto_pick_perks_required=auto_pick_perks_required,
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
