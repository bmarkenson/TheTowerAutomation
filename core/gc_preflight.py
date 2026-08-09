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
from core.player_save_temporal import ROUND_INVARIANT_ATTACHMENT_CHECKS
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
    "card_recharge_modes": "Card recharge modes",
    "workshop_preset": "Workshop preset",
    "free_upgrade_locks": "Free Upgrade locks",
    "bots_preset": "Bot preset",
    "guardian_chips": "Guardian Chips",
    "modules": "Modules",
    "auto_pick_perks": "Auto Pick Perks",
    "perk_first_choice": "First Perk Choice",
    "perk_bans": "Perk Bans",
    "perk_auto_pick_order": "Auto Pick priority",
    "target_priority": "Target Priority",
    "ultimate_weapons": "Ultimate Weapons",
}

_CONFIGURATION_CHECK_SECTIONS = {
    "cards_deck": "cards",
    "workshop_preset": "workshop",
    "bots_preset": "bots",
    "guardian_chips": "guardians",
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
    """Return nonblocking attachment or observe-mode configuration differences."""

    if not isinstance(evidence, Mapping):
        return ""
    reported = evidence.get("reported_attachment_mismatches")
    details: list[str] = []
    if isinstance(reported, Mapping):
        for check_id, raw in reported.items():
            if not isinstance(raw, Mapping):
                continue
            label = _CHECK_LABELS.get(
                str(check_id),
                str(check_id).replace("_", " ").title(),
            )
            expected = raw.get("expected")
            observed = raw.get("observed")
            if (
                str(check_id) == "modules"
                and isinstance(expected, Mapping)
                and isinstance(observed, Mapping)
            ):
                for slot_key, expected_module in expected.items():
                    observed_module = observed.get(slot_key)
                    if observed_module == expected_module:
                        continue
                    slot_label = str(slot_key).replace("_", " ").title()
                    details.append(
                        f"{slot_label} module: expected {expected_module}, "
                        f"observed {observed_module} "
                        "(immutable in active battle)"
                    )
                continue
            details.append(
                f"{label}: expected {expected!r}, observed {observed!r} "
                "(immutable in active battle)"
            )
    if details:
        limit = max(1, int(max_details))
        omitted = len(details) - limit
        summary = "; ".join(details[:limit])
        if omitted > 0:
            summary += (
                f"; +{omitted} more variation"
                f"{'s' if omitted != 1 else ''}"
            )
        return summary

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
    deferred_configuration_checks: tuple[str, ...] = ()
    accepted_configuration_sections: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    module_source: str = "ui"
    auto_pick_perks_source: str = "ui"
    ultimate_weapons_source: str = "ui"
    attachment_requirement_checks: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    reported_attachment_mismatches: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    waivers: Mapping[str, Any] = field(default_factory=dict)

    def is_waived(self, check_id: str) -> bool:
        return str(check_id) in self.waivers

    def is_deferred(self, check_id: str) -> bool:
        return str(check_id) in self.deferred_configuration_checks

    @property
    def configuration_valid(self) -> bool:
        return (
            (
                self.configuration.cards.valid
                or "cards_deck" in self.reported_attachment_mismatches
                or self.is_waived("cards_deck")
                or self.is_deferred("cards_deck")
            )
            and (
                self.configuration.workshop.valid
                or "workshop_preset" in self.reported_attachment_mismatches
                or self.is_waived("workshop_preset")
                or self.is_deferred("workshop_preset")
            )
            and (
                self.configuration.bots.valid
                or "bots_preset" in self.reported_attachment_mismatches
                or self.is_waived("bots_preset")
                or self.is_deferred("bots_preset")
            )
            and (
                self.configuration.guardians.valid
                or "guardian_chips" in self.reported_attachment_mismatches
                or self.is_waived("guardian_chips")
                or self.is_deferred("guardian_chips")
            )
        )

    @property
    def modules_blocking_valid(self) -> bool:
        if self.is_waived("modules") or self.module_mode == "preserve":
            return True
        if self.modules is None:
            return False
        if "modules" in self.reported_attachment_mismatches:
            return self.modules.fully_observed
        if self.module_mode == "observe":
            return self.modules.fully_observed
        return self.modules.valid

    @property
    def attachment_requirements_valid(self) -> bool:
        return all(
            check.get("blocking") is not True
            or check.get("valid") is True
            for check_id, check in self.attachment_requirement_checks.items()
            if isinstance(check, Mapping)
            and not self.is_waived(check_id)
        )

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
            and self.attachment_requirements_valid
            and (
                self.ultimate_weapons.valid
                or self.is_waived("ultimate_weapons")
            )
        )

    @property
    def failed_checks(self) -> tuple[str, ...]:
        failures = []
        for check_id, valid in (
            (
                "cards_deck",
                self.configuration.cards.valid
                or "cards_deck" in self.reported_attachment_mismatches,
            ),
            (
                "workshop_preset",
                self.configuration.workshop.valid
                or "workshop_preset" in self.reported_attachment_mismatches,
            ),
            (
                "bots_preset",
                self.configuration.bots.valid
                or "bots_preset" in self.reported_attachment_mismatches,
            ),
            (
                "guardian_chips",
                self.configuration.guardians.valid
                or "guardian_chips" in self.reported_attachment_mismatches,
            ),
            ("modules", self.modules_blocking_valid),
            ("auto_pick_perks", self.auto_pick_perks_valid),
            (
                "ultimate_weapons",
                self.ultimate_weapons.valid
                or self.is_waived("ultimate_weapons"),
            ),
        ):
            if (
                not valid
                and not self.is_waived(check_id)
                and not self.is_deferred(check_id)
            ):
                failures.append(check_id)
        for check_id, check in self.attachment_requirement_checks.items():
            if (
                isinstance(check, Mapping)
                and check.get("blocking") is True
                and check.get("valid") is not True
                and not self.is_waived(check_id)
            ):
                failures.append(str(check_id))
        return tuple(dict.fromkeys(failures))

    @property
    def deferred_checks(self) -> tuple[str, ...]:
        checks = list(self.deferred_configuration_checks)
        if (
            self.free_upgrade_locks.get("status") == "unavailable_deferred"
            and "free_upgrade_locks" not in self.attachment_requirement_checks
        ):
            checks.append("free_upgrade_locks")
        checks.extend(
            str(check_id)
            for check_id, check in self.attachment_requirement_checks.items()
            if isinstance(check, Mapping)
            and check.get("disposition") == "unavailable_deferred"
        )
        return tuple(dict.fromkeys(checks))

    @property
    def requires_no_battle_repair(self) -> bool:
        """Whether a mismatch belongs to a no-battle configuration surface."""

        return (
            not self.configuration_valid
            or bool(
                not self.is_waived("modules")
                and self.module_mode == "enforce"
                and "modules" not in self.reported_attachment_mismatches
                and self.modules is not None
                and self.modules.has_authoritative_mismatch
            )
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        module_source = str(payload.pop("module_source", self.module_source))
        accepted_sections = payload.pop(
            "accepted_configuration_sections",
            {},
        )
        payload["configuration"]["valid"] = self.configuration.valid
        if accepted_sections:
            payload["configuration"]["save_backed_sections"] = (
                accepted_sections
            )
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
                source=module_source,
            )
        payload["auto_pick_perks"].update(
            required=self.auto_pick_perks_required,
            checked=self.auto_pick_perks_required,
            valid=self.auto_pick_perks_valid,
            source=self.auto_pick_perks_source,
        )
        payload["ultimate_weapons"]["valid"] = self.ultimate_weapons.valid
        payload["ultimate_weapons"]["source"] = (
            self.ultimate_weapons_source
        )
        payload["configuration"]["blocking_valid"] = self.configuration_valid
        payload["deferred_configuration_checks"] = list(
            self.deferred_configuration_checks
        )
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
    accepted_sections: Optional[Mapping[str, Mapping[str, Any]]] = None,
    deferred_sections: Optional[Iterable[str]] = None,
) -> GcPreflightEvidence:
    """Validate captured, save-backed, or explicitly deferred sections.

    A missing screen is accepted only when that exact section has explicit
    authoritative save provenance or the caller explicitly marks that section
    as deferred. A saved mismatch remains separately represented in session
    evidence; this synthetic section only prevents a redundant UI confirmation.
    A deferred section remains invalid in raw configuration evidence so it
    cannot be mistaken for an observation. A supplied screen is always
    evaluated, even if save provenance or a deferral also exists, so an
    observed contradiction cannot be hidden.
    """

    required_names = {"cards", "workshop", "bots", "guardians"}
    missing_names = sorted(required_names - set(section_specs))
    if missing_names:
        raise ValueError(
            "preflight section specs are missing: " + ", ".join(missing_names)
        )

    accepted = dict(accepted_sections or {})
    unsupported_accepted = sorted(set(accepted) - required_names)
    if unsupported_accepted:
        raise ValueError(
            "preflight has unsupported accepted sections: "
            + ", ".join(unsupported_accepted)
        )
    deferred = {str(name).strip() for name in deferred_sections or ()}
    unsupported_deferred = sorted(deferred - required_names)
    if unsupported_deferred:
        raise ValueError(
            "preflight has unsupported deferred sections: "
            + ", ".join(unsupported_deferred)
        )

    def section_detection(name: str, screen):
        spec = section_specs[name]
        if screen is not None:
            return _detect_section_selection(screen, spec, detector)
        provenance = accepted.get(name)
        if (
            isinstance(provenance, Mapping)
            and provenance.get("disposition") == "save_match"
        ):
            detection = {
                "state": spec.expected_state,
                "secondary_states": sorted(spec.required_secondary),
            }
            selection = (
                PresetSlotSelection(
                    region=spec.selection_region,
                    valid_region=True,
                    selected=True,
                    green_pixels=0,
                    cyan_pixels=0,
                )
                if spec.selection_region is not None
                else None
            )
            return detection, selection
        if name not in deferred:
            raise ValueError(
                f"preflight section {name} has neither a screen nor accepted "
                "save provenance"
            )
        detection = {
            "state": "DEFERRED",
            "secondary_states": [],
        }
        selection = (
            PresetSlotSelection(
                region=spec.selection_region,
                valid_region=False,
                selected=False,
                green_pixels=0,
                cyan_pixels=0,
            )
            if spec.selection_region is not None
            else None
        )
        return detection, selection

    cards_detection, cards_selection = section_detection(
        "cards", cards_screen
    )
    workshop_detection, workshop_selection = section_detection(
        "workshop", workshop_screen
    )
    bots_detection, bots_selection = section_detection("bots", bots_screen)
    if (
        cards_selection is None
        or workshop_selection is None
        or bots_selection is None
    ):
        raise ValueError("cards, workshop, and bots specs require selection regions")
    guardians_detection, _guardians_selection = section_detection(
        "guardians", guardians_screen
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
    save_verified = bool(
        candidate.get("status") == "save_match"
        and candidate.get("source") == "bound_player_save_preflight"
        and candidate.get("boundary") == "NEW_BATTLE"
        and candidate.get("checked") is False
        and candidate.get("valid") is True
        and tuple(candidate.get("required") or ()) == requirements
        and len(set(requirements)) == len(requirements)
        and set(requirements).issubset(set(candidate.get("observed") or ()))
    )
    if verified or save_verified:
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
    auto_pick_boundary_evidence: Optional[Mapping[str, Any]] = None,
    ultimate_weapons_source: str = "ui",
    attachment_requirement_checks: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None,
    reported_attachment_mismatches: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None,
    attachment_report_only_requirements: Optional[Mapping[str, Any]] = None,
    waivers: Optional[Mapping[str, Any]] = None,
    configuration_boundary_evidence: Optional[Mapping[str, Any]] = None,
    module_boundary_evidence: Optional[Mapping[str, Any]] = None,
    accepted_sections: Optional[Mapping[str, Mapping[str, Any]]] = None,
    deferred_checks: Optional[Sequence[Any]] = None,
) -> GcSessionPreflightEvidence:
    """Validate every currently implemented read-only session requirement."""

    normalized_deferred_checks = tuple(
        dict.fromkeys(
            str(check_id).strip()
            for check_id in deferred_checks or ()
            if str(check_id).strip()
        )
    )
    unsupported_deferred = sorted(
        set(normalized_deferred_checks) - set(_CONFIGURATION_CHECK_SECTIONS)
    )
    if unsupported_deferred:
        raise ValueError(
            "session preflight has unsupported deferred checks: "
            + ", ".join(unsupported_deferred)
        )
    active_accepted_sections = {
        str(section): dict(provenance)
        for section, provenance in (accepted_sections or {}).items()
        if isinstance(provenance, Mapping)
    }
    active_attachment_checks = {
        str(check_id): dict(check)
        for check_id, check in (attachment_requirement_checks or {}).items()
        if isinstance(check, Mapping)
    }
    active_reported_mismatches = {
        str(check_id): dict(check)
        for check_id, check in (reported_attachment_mismatches or {}).items()
        if isinstance(check, Mapping)
    }
    active_report_only_requirements = {
        str(check_id): expected
        for check_id, expected in (
            attachment_report_only_requirements or {}
        ).items()
    }
    unsupported_report_only = sorted(
        set(active_report_only_requirements)
        - set(ROUND_INVARIANT_ATTACHMENT_CHECKS)
    )
    if unsupported_report_only:
        raise ValueError(
            "session preflight has unsupported attachment report-only checks: "
            + ", ".join(unsupported_report_only)
        )

    attachment_disposition_contracts = {
        "save_match": (True, False, "bound_player_save_preflight"),
        "save_mismatch": (False, True, "bound_player_save_preflight"),
        "save_mismatch_reported": (
            False,
            False,
            "bound_player_save_preflight",
        ),
        "ui_mismatch_reported": (False, False, "ui_fallback"),
        "unavailable_deferred": (None, False, "ui_fallback"),
    }
    for check_id, check in active_attachment_checks.items():
        disposition = str(check.get("disposition") or "")
        contract = attachment_disposition_contracts.get(disposition)
        if contract is None:
            raise ValueError(
                f"attachment check {check_id} has unsupported disposition"
            )
        valid, blocking, source = contract
        if (
            check.get("valid") is not valid
            or check.get("blocking") is not blocking
            or check.get("source") != source
        ):
            raise ValueError(
                f"attachment check {check_id} violates {disposition} contract"
            )
        if disposition in {
            "save_mismatch_reported",
            "ui_mismatch_reported",
        } and (
            check_id not in ROUND_INVARIANT_ATTACHMENT_CHECKS
            or check.get("temporal_class") != "round_invariant"
        ):
            raise ValueError(
                f"attachment check {check_id} lacks round-invariant authority"
            )
    for check_id, reported in active_reported_mismatches.items():
        check = active_attachment_checks.get(check_id)
        if (
            check is None
            or check.get("disposition")
            not in {"save_mismatch_reported", "ui_mismatch_reported"}
            or reported != check
            or check_id not in active_report_only_requirements
            or reported.get("expected")
            != active_report_only_requirements[check_id]
        ):
            raise ValueError(
                f"reported attachment mismatch {check_id} lacks matching evidence"
            )

    validation_accepted_sections = {
        section: dict(provenance)
        for section, provenance in active_accepted_sections.items()
    }
    section_check_ids = {
        section: check_id
        for check_id, section in _CONFIGURATION_CHECK_SECTIONS.items()
    }
    for section, provenance in validation_accepted_sections.items():
        disposition = str(provenance.get("disposition") or "")
        if disposition not in {"save_mismatch", "save_mismatch_reported"}:
            continue
        check_id = section_check_ids.get(section)
        check = active_attachment_checks.get(str(check_id))
        if (
            check_id is None
            or check is None
            or check.get("disposition") != disposition
            or provenance.get("source") != "bound_player_save_preflight"
            or check.get("expected") != provenance.get("expected")
            or check.get("observed") != provenance.get("observed")
        ):
            raise ValueError(
                f"accepted attachment section {section} lacks mismatch evidence"
            )
        provenance["disposition"] = "save_match"
    if configuration_boundary_evidence is not None and (
        normalized_deferred_checks or active_accepted_sections
    ):
        raise ValueError(
            "session preflight cannot combine complete boundary evidence "
            "with individual accepted or deferred configuration checks"
        )
    accepted_check_ids = {
        check_id
        for check_id, section in _CONFIGURATION_CHECK_SECTIONS.items()
        if section in active_accepted_sections
    }
    duplicate_dispositions = sorted(
        accepted_check_ids.intersection(normalized_deferred_checks)
    )
    if duplicate_dispositions:
        raise ValueError(
            "session preflight cannot both accept and defer checks: "
            + ", ".join(duplicate_dispositions)
        )
    supplied_screens = {
        "cards_deck": cards_screen,
        "workshop_preset": workshop_screen,
        "bots_preset": bots_screen,
        "guardian_chips": guardians_screen,
    }
    observed_deferrals = sorted(
        check_id
        for check_id in normalized_deferred_checks
        if supplied_screens[check_id] is not None
    )
    if observed_deferrals:
        raise ValueError(
            "session preflight cannot defer observed checks: "
            + ", ".join(observed_deferrals)
        )

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
    reported_locks = active_reported_mismatches.get("free_upgrade_locks")
    if isinstance(reported_locks, Mapping):
        free_upgrade_locks = {
            "status": str(reported_locks["disposition"]),
            "source": str(reported_locks["source"]),
            "boundary": "ACTIVE_BATTLE",
            "required": list(normalized_free_upgrade_locks),
            "observed": reported_locks.get("observed"),
            "checked": reported_locks.get("source") == "ui_fallback",
            "valid": False,
            "blocking_valid": True,
            "reason": "active_battle_free_upgrade_locks_are_immutable",
        }

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
            accepted_sections=validation_accepted_sections,
            deferred_sections=(
                _CONFIGURATION_CHECK_SECTIONS[check_id]
                for check_id in normalized_deferred_checks
            ),
        )
    )
    auto_pick = measure_auto_pick_perks(perks_screen)
    auto_pick_source = "ui"
    if (
        auto_pick_perks_required
        and isinstance(auto_pick_boundary_evidence, Mapping)
        and auto_pick_boundary_evidence.get("source")
        == "bound_player_save_preflight"
        and auto_pick_boundary_evidence.get("value") is True
    ):
        auto_pick = AutoPickPerksEvidence(
            region=auto_pick.region,
            valid_region=True,
            enabled=True,
            green_pixels=auto_pick.green_pixels,
        )
        auto_pick_source = "bound_player_save_preflight"
    elif auto_pick_perks_required and (
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
    module_source = "not_required"
    if module_mode != "preserve":
        if not isinstance(module_requirements, Mapping):
            raise ValueError(
                f"module policy {module_mode!r} requires requirements"
            )
        if "modules" in active_waivers:
            modules = None
            module_source = "waived"
        elif module_boundary_evidence is not None:
            modules = gc_module_loadout_evidence_from_dict(
                module_boundary_evidence
            )
            module_source = str(
                module_boundary_evidence.get("source") or "boundary_evidence"
            )
        elif modules_screen is not None:
            modules = evaluate_gc_module_loadout(
                modules_screen,
                module_requirements,
            )
            module_source = "ui"
        else:
            raise ValueError(
                f"module policy {module_mode!r} requires screen or boundary evidence"
            )

    section_results = {
        "workshop_preset": configuration.workshop,
        "bots_preset": configuration.bots,
        "guardian_chips": configuration.guardians,
    }
    for check_id, expected in active_report_only_requirements.items():
        if check_id in active_reported_mismatches:
            continue
        observed: Any = None
        source = "ui_fallback"
        if check_id == "modules":
            if (
                module_mode == "observe"
                or modules is None
                or not modules.fully_observed
                or modules.valid
            ):
                continue
            observed = {
                str(slot.slot_key): slot.actual
                for slot in modules.slots
            }
        else:
            section = section_results.get(check_id)
            if (
                section is None
                or section.valid
                or section.detected_state == "DEFERRED"
            ):
                continue
            observed = {
                "detected_state": section.detected_state,
                "detected_secondary": list(section.detected_secondary),
                "missing_secondary": list(section.missing_secondary),
            }
        report = {
            "source": source,
            "disposition": "ui_mismatch_reported",
            "expected": expected,
            "observed": observed,
            "valid": False,
            "blocking": False,
            "temporal_class": "round_invariant",
        }
        active_attachment_checks[check_id] = report
        active_reported_mismatches[check_id] = dict(report)

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
        deferred_configuration_checks=normalized_deferred_checks,
        accepted_configuration_sections=active_accepted_sections,
        module_source=module_source,
        auto_pick_perks_source=auto_pick_source,
        ultimate_weapons_source=str(ultimate_weapons_source or "ui"),
        attachment_requirement_checks=active_attachment_checks,
        reported_attachment_mismatches=active_reported_mismatches,
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
