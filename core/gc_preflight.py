"""Visual evidence evaluation for the staged GC configuration preflight."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

from core.state_detector import detect_state_and_overlays
from core.workshop_preset import PresetSlotSelection, measure_preset_slot_selection


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
    workshop: GcSectionResult
    workshop_selection: PresetSlotSelection
    bots: GcSectionResult
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


GC_SECTION_SPECS = {
    "cards": GcSectionSpec(
        name="cards",
        expected_state="CARDS",
        required_secondary=frozenset({"CARDS_GC_ACTIVE"}),
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
        required_secondary=frozenset({"EVENT_BOTS_SCREEN", "BOTS_FARM_ACTIVE"}),
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

    return GcPreflightEvidence(
        cards=evaluate_gc_section(GC_SECTION_SPECS["cards"], detector(cards_screen)),
        workshop=evaluate_gc_section(
            GC_SECTION_SPECS["workshop"], workshop_detection
        ),
        workshop_selection=workshop_selection,
        bots=evaluate_gc_section(GC_SECTION_SPECS["bots"], detector(bots_screen)),
        guardians=evaluate_gc_section(
            GC_SECTION_SPECS["guardians"], detector(guardians_screen)
        ),
    )


__all__ = [
    "GC_SECTION_SPECS",
    "GcPreflightEvidence",
    "GcSectionResult",
    "GcSectionSpec",
    "evaluate_gc_section",
    "validate_gc_preflight_screens",
]
