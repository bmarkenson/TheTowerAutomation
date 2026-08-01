"""Visual evidence evaluation for the staged GC configuration preflight."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from core.auto_pick_perks import AutoPickPerksEvidence, measure_auto_pick_perks
from core.free_upgrade_locks import (
    normalize_free_upgrade_lock_requirements,
)
from core.gc_module_loadout import (
    GcModuleLoadoutEvidence,
    evaluate_gc_module_loadout,
    gc_module_loadout_evidence_from_dict,
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


_CHECK_LABELS = {
    "cards_deck": "Cards deck",
    "workshop_preset": "Workshop preset",
    "bots_preset": "Bot preset",
    "guardian_chips": "Guardian Chips",
    "modules": "Modules",
    "auto_pick_perks": "Auto Pick Perks",
    "ultimate_weapons": "Ultimate Weapons",
}


def summarize_gc_preflight_mismatch(
    evidence: Mapping[str, Any],
    *,
    max_details: int = 4,
) -> str:
    """Return concise expected-versus-observed preflight failure details."""

    if not isinstance(evidence, Mapping):
        return "configuration mismatch"
    raw_failed_checks = evidence.get("failed_checks")
    failed_checks = [
        str(check).strip()
        for check in (
            raw_failed_checks
            if isinstance(raw_failed_checks, (list, tuple))
            else ()
        )
        if str(check).strip()
    ]
    details: list[str] = []
    detailed_checks: set[str] = set()

    if "modules" in failed_checks:
        modules = evidence.get("modules")
        slots = modules.get("slots") if isinstance(modules, Mapping) else None
        if isinstance(slots, (list, tuple)):
            for slot in slots:
                if not isinstance(slot, Mapping) or slot.get("valid") is not False:
                    continue
                slot_label = str(slot.get("slot_key") or "slot").replace(
                    "_", " "
                ).title()
                expected = str(slot.get("expected") or "unknown")
                actual = slot.get("actual")
                if actual is None:
                    actual = str(slot.get("match_status") or "unknown")
                details.append(
                    f"{slot_label} module: expected {expected}, observed {actual}"
                )
                detailed_checks.add("modules")

    configuration = evidence.get("configuration")
    section_fields = {
        "cards_deck": "cards",
        "workshop_preset": "workshop",
        "bots_preset": "bots",
        "guardian_chips": "guardians",
    }
    if isinstance(configuration, Mapping):
        for check_id, field in section_fields.items():
            if check_id not in failed_checks:
                continue
            section = configuration.get(field)
            if not isinstance(section, Mapping):
                continue
            missing = [str(item) for item in section.get("missing_secondary") or ()]
            label = _CHECK_LABELS[check_id]
            if missing:
                details.append(f"{label}: missing evidence {', '.join(missing)}")
            else:
                observed = str(section.get("detected_state") or "unknown")
                details.append(f"{label}: observed state {observed}")
            detailed_checks.add(check_id)

    if "auto_pick_perks" in failed_checks:
        auto_pick = evidence.get("auto_pick_perks")
        if isinstance(auto_pick, Mapping):
            observed = "disabled" if auto_pick.get("valid_region") else "uncertain"
            details.append(
                "Auto Pick Perks: expected enabled, "
                f"observed {observed}"
            )
            detailed_checks.add("auto_pick_perks")

    if "ultimate_weapons" in failed_checks:
        ultimate_weapons = evidence.get("ultimate_weapons")
        weapons = (
            ultimate_weapons.get("weapons")
            if isinstance(ultimate_weapons, Mapping)
            else None
        )
        if isinstance(weapons, (list, tuple)):
            for weapon in weapons:
                if not isinstance(weapon, Mapping) or weapon.get("valid") is not False:
                    continue
                label = str(weapon.get("label") or "unknown")
                mismatches = [
                    str(item) for item in weapon.get("mismatched_toggles") or ()
                ]
                observed = (
                    ", ".join(mismatches)
                    if mismatches
                    else "required controls were not observed"
                )
                details.append(f"Ultimate Weapons {label}: {observed}")
                detailed_checks.add("ultimate_weapons")

    for check_id in failed_checks:
        if check_id not in detailed_checks:
            details.append(_CHECK_LABELS.get(check_id, check_id.replace("_", " ")))

    if not details:
        return "configuration mismatch"
    limit = max(1, int(max_details))
    omitted = len(details) - limit
    summary = "; ".join(details[:limit])
    if omitted > 0:
        summary += f"; +{omitted} more mismatch{'es' if omitted != 1 else ''}"
    return summary


def summarize_gc_preflight_variations(
    evidence: Mapping[str, Any],
    *,
    max_details: int = 4,
) -> str:
    """Return confident observe-mode differences from the module reference."""

    if not isinstance(evidence, Mapping):
        return ""
    modules = evidence.get("modules")
    if not isinstance(modules, Mapping):
        return ""
    module_mode = str(
        modules.get("mode") or evidence.get("module_mode") or ""
    ).strip().lower()
    if module_mode != "observe":
        return ""
    slots = modules.get("slots")
    if not isinstance(slots, (list, tuple)):
        return ""
    details = []
    for slot in slots:
        if not isinstance(slot, Mapping):
            continue
        expected = str(slot.get("expected") or "").strip()
        actual = str(slot.get("actual") or "").strip()
        if (
            slot.get("match_status") == "matched"
            and expected
            and actual
            and actual != expected
        ):
            slot_label = str(slot.get("slot_key") or "slot").replace(
                "_", " "
            ).title()
            details.append(
                f"{slot_label} module: reference {expected}, observed {actual}"
            )
    if not details:
        return ""
    limit = max(1, int(max_details))
    omitted = len(details) - limit
    summary = "; ".join(details[:limit])
    if omitted > 0:
        summary += f"; +{omitted} more variation{'s' if omitted != 1 else ''}"
    return summary


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


def gc_preflight_evidence_from_dict(raw: Mapping[str, Any]) -> GcPreflightEvidence:
    """Rehydrate retained Home-boundary configuration evidence."""

    if not isinstance(raw, Mapping):
        raise ValueError("configuration evidence must be a mapping")

    def section(name: str) -> GcSectionResult:
        value = raw.get(name)
        if not isinstance(value, Mapping):
            raise ValueError(f"configuration evidence is missing {name}")
        return GcSectionResult(
            name=str(value.get("name") or name),
            valid=bool(value.get("valid")),
            detected_state=str(value.get("detected_state") or "UNKNOWN"),
            required_secondary=tuple(value.get("required_secondary") or ()),
            detected_secondary=tuple(value.get("detected_secondary") or ()),
            missing_secondary=tuple(value.get("missing_secondary") or ()),
        )

    def selection(name: str) -> PresetSlotSelection:
        value = raw.get(name)
        if not isinstance(value, Mapping):
            raise ValueError(f"configuration evidence is missing {name}")
        region = tuple(value.get("region") or ())
        if len(region) != 4:
            raise ValueError(f"configuration evidence {name} has invalid region")
        return PresetSlotSelection(
            region=region,
            valid_region=bool(value.get("valid_region")),
            selected=bool(value.get("selected")),
            green_pixels=int(value.get("green_pixels") or 0),
            cyan_pixels=int(value.get("cyan_pixels") or 0),
        )

    return GcPreflightEvidence(
        cards=section("cards"),
        cards_selection=selection("cards_selection"),
        workshop=section("workshop"),
        workshop_selection=selection("workshop_selection"),
        bots=section("bots"),
        bots_selection=selection("bots_selection"),
        guardians=section("guardians"),
    )


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
    free_upgrade_locks: Mapping[str, Any]
    module_mode: str
    modules: Optional[GcModuleLoadoutEvidence]
    auto_pick_perks_required: bool
    auto_pick_perks: AutoPickPerksEvidence
    ultimate_weapons: UltimateWeaponEvidence
    waivers: Mapping[str, Any] = field(default_factory=dict)

    def is_waived(self, check_id: str) -> bool:
        return str(check_id) in self.waivers

    @property
    def configuration_valid(self) -> bool:
        return (
            (self.configuration.cards.valid or self.is_waived("cards_deck"))
            and (
                self.configuration.workshop.valid
                or self.is_waived("workshop_preset")
            )
            and (self.configuration.bots.valid or self.is_waived("bots_preset"))
            and (
                self.configuration.guardians.valid
                or self.is_waived("guardian_chips")
            )
        )

    @property
    def modules_blocking_valid(self) -> bool:
        if self.is_waived("modules") or self.module_mode == "preserve":
            return True
        if self.modules is None:
            return False
        if self.module_mode == "observe":
            return self.modules.fully_observed
        return self.modules.valid

    @property
    def auto_pick_perks_valid(self) -> bool:
        return (
            self.is_waived("auto_pick_perks")
            or not self.auto_pick_perks_required
            or self.auto_pick_perks.enabled
        )

    @property
    def free_upgrade_locks_valid(self) -> Optional[bool]:
        """Return boundary validity, or None when that check was unavailable."""

        status = str(self.free_upgrade_locks.get("status") or "").strip()
        if status in {"not_required", "waived"}:
            return True
        valid = self.free_upgrade_locks.get("valid")
        return valid if isinstance(valid, bool) else None

    @property
    def valid(self) -> bool:
        return (
            self.configuration_valid
            and self.modules_blocking_valid
            and self.auto_pick_perks_valid
            and (
                self.ultimate_weapons.valid
                or self.is_waived("ultimate_weapons")
            )
        )

    @property
    def failed_checks(self) -> tuple[str, ...]:
        failures = []
        for check_id, valid in (
            ("cards_deck", self.configuration.cards.valid),
            ("workshop_preset", self.configuration.workshop.valid),
            ("bots_preset", self.configuration.bots.valid),
            ("guardian_chips", self.configuration.guardians.valid),
            ("modules", self.modules_blocking_valid),
            ("auto_pick_perks", self.auto_pick_perks_valid),
            (
                "ultimate_weapons",
                self.ultimate_weapons.valid
                or self.is_waived("ultimate_weapons"),
            ),
        ):
            if not valid and not self.is_waived(check_id):
                failures.append(check_id)
        return tuple(failures)

    @property
    def deferred_checks(self) -> tuple[str, ...]:
        if self.free_upgrade_locks.get("status") == "unavailable_deferred":
            return ("free_upgrade_locks",)
        return ()

    @property
    def requires_no_battle_repair(self) -> bool:
        """Whether a mismatch belongs to a no-battle configuration surface."""

        return (
            not self.configuration_valid
            or bool(
                not self.is_waived("modules")
                and self.module_mode == "enforce"
                and self.modules is not None
                and self.modules.has_authoritative_mismatch
            )
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["configuration"]["valid"] = self.configuration.valid
        payload["free_upgrade_locks"] = dict(self.free_upgrade_locks)
        payload["free_upgrade_locks"]["blocking_valid"] = True
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
        payload["configuration"]["blocking_valid"] = self.configuration_valid
        payload["failed_checks"] = list(self.failed_checks)
        payload["deferred_checks"] = list(self.deferred_checks)
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


def _free_upgrade_lock_boundary_evidence(
    requirements: tuple[str, ...],
    evidence: Optional[Mapping[str, Any]],
    *,
    waiver: Any = None,
) -> dict[str, Any]:
    """Normalize run-boundary lock proof without turning absence into a pass."""

    required = list(requirements)
    if not requirements:
        return {
            "status": "not_required",
            "boundary": "NEW_BATTLE",
            "required": required,
            "checked": False,
            "valid": True,
        }
    if waiver is not None:
        return {
            "status": "waived",
            "boundary": "NEW_BATTLE",
            "required": required,
            "checked": False,
            "valid": None,
            "waiver": dict(waiver) if isinstance(waiver, Mapping) else waiver,
        }

    candidate = dict(evidence) if isinstance(evidence, Mapping) else {}
    raw_locks = candidate.get("locks")
    locks = list(raw_locks) if isinstance(raw_locks, (list, tuple)) else []
    labels = tuple(
        str(lock.get("label") or "").strip()
        for lock in locks
        if isinstance(lock, Mapping)
    )
    locks_verified = len(locks) == len(requirements) and all(
        isinstance(lock, Mapping)
        and str(lock.get("state") or "").strip().lower() == "checked"
        and lock.get("valid") is True
        for lock in locks
    )
    verified = bool(
        candidate.get("status") == "verified"
        and candidate.get("boundary") == "NEW_BATTLE"
        and candidate.get("checked") is True
        and candidate.get("valid") is True
        and tuple(candidate.get("required") or ()) == requirements
        and labels == requirements
        and locks_verified
    )
    if verified:
        candidate["required"] = required
        candidate["blocking_valid"] = True
        return candidate

    return {
        "status": "unavailable_deferred",
        "boundary": "NEW_BATTLE",
        "required": required,
        "checked": False,
        "valid": None,
        "blocking_valid": True,
        "reason": (
            "authoritative no-battle NEW_BATTLE lock evidence was not "
            "available for this run"
        ),
    }


def validate_gc_session_preflight_screens(
    *,
    cards_screen=None,
    workshop_screen=None,
    bots_screen=None,
    guardians_screen=None,
    modules_screen=None,
    perks_screen,
    module_requirements: Optional[Mapping[str, Any]] = None,
    module_mode: str = "enforce",
    ultimate_requirements: Mapping[str, Mapping[str, Any]],
    ultimate_observations: Mapping[str, Mapping[str, Any]],
    free_upgrade_lock_requirements: Optional[Sequence[Any]] = None,
    free_upgrade_lock_boundary_evidence: Optional[Mapping[str, Any]] = None,
    detector: Detector = detect_state_and_overlays,
    section_specs: Mapping[str, GcSectionSpec] = GC_SECTION_SPECS,
    auto_pick_perks_required: bool = True,
    waivers: Optional[Mapping[str, Any]] = None,
    configuration_boundary_evidence: Optional[Mapping[str, Any]] = None,
    module_boundary_evidence: Optional[Mapping[str, Any]] = None,
) -> GcSessionPreflightEvidence:
    """Validate every currently implemented read-only session requirement."""

    normalized_free_upgrade_locks = (
        normalize_free_upgrade_lock_requirements(free_upgrade_lock_requirements)
        if free_upgrade_lock_requirements is not None
        else ()
    )
    active_waivers = dict(waivers or {})
    free_upgrade_locks = _free_upgrade_lock_boundary_evidence(
        normalized_free_upgrade_locks,
        free_upgrade_lock_boundary_evidence,
        waiver=active_waivers.get("free_upgrade_locks"),
    )

    configuration = (
        gc_preflight_evidence_from_dict(configuration_boundary_evidence)
        if configuration_boundary_evidence is not None
        else validate_gc_preflight_screens(
            cards_screen=cards_screen,
            workshop_screen=workshop_screen,
            bots_screen=bots_screen,
            guardians_screen=guardians_screen,
            detector=detector,
            section_specs=section_specs,
        )
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
        if not isinstance(module_requirements, Mapping):
            raise ValueError(
                f"module policy {module_mode!r} requires requirements"
            )
        if "modules" in active_waivers:
            modules = None
        elif module_boundary_evidence is not None:
            modules = gc_module_loadout_evidence_from_dict(
                module_boundary_evidence
            )
        elif modules_screen is not None:
            modules = evaluate_gc_module_loadout(
                modules_screen,
                module_requirements,
            )
        else:
            raise ValueError(
                f"module policy {module_mode!r} requires screen or boundary evidence"
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
        waivers=active_waivers,
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
    "gc_preflight_evidence_from_dict",
    "summarize_gc_preflight_mismatch",
    "summarize_gc_preflight_variations",
    "validate_gc_preflight_screens",
    "validate_gc_session_preflight_screens",
]
