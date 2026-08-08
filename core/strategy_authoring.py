"""Sparse, versioned strategy-authoring primitives.

This module owns authoring-time validation and resolution.  Runtime strategy
loading deliberately remains unaware of bases and consumes only a publication's
embedded generated plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

import yaml

from core.card_recharge_modes import (
    CARD_RECHARGE_LABELS,
    CardRechargeMode,
    normalize_card_recharge_modes,
)
from core.damage_adjuster import normalize_damage_percentage
from core.free_upgrade_locks import (
    FARM_FREE_UPGRADE_LOCKS,
    normalize_free_upgrade_lock_requirements,
)
from core.gate_decisions import PROFILE_SKIPPABLE_CHECKS, normalize_profile_skip_checks
from core.gc_module_loadout import normalize_gc_module_requirements
from core.module_presets import (
    BUNDLED_MODULE_PRESETS_PATH,
    MODULE_PRESET_CATALOG_ID,
)
from core.module_icon_index import load_module_icon_catalog
from core.orb_distance import (
    normalize_orb_distance_preset,
    normalize_orb_distance_presets,
)
from core.perk_configuration import (
    PERK_BAN_CAPACITY,
    PERK_CONFIGURATION_LABELS,
    normalize_perk_configuration_requirements,
)
from core.target_priority_config import (
    TARGET_PRIORITY_TARGETS,
    validate_target_priority_order,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FARM_RUN_PROFILE_PATH = PROJECT_ROOT / "config" / "run_profiles" / "farm.yaml"
FARM_DEFAULT_STRATEGY_SOURCE_PATH = (
    PROJECT_ROOT / "config" / "strategies" / "farm_t18.source.yaml"
)
MODULE_PRESETS_PATH = BUNDLED_MODULE_PRESETS_PATH
ORB_DISTANCE_PRESETS_PATH = (
    PROJECT_ROOT / "config" / "loadouts" / "orb_distances.yaml"
)
TARGET_PRIORITY_PRESETS_PATH = (
    PROJECT_ROOT / "config" / "loadouts" / "target_priorities.yaml"
)
_LOADOUT_PRESET_PATHS = {
    "modules": MODULE_PRESETS_PATH,
    "orb_distance": ORB_DISTANCE_PRESETS_PATH,
    "target_priority": TARGET_PRIORITY_PRESETS_PATH,
}

LEGACY_AUTHORING_SCHEMA_VERSION = 2
AUTHORING_SCHEMA_VERSION = 3
BASE_PUBLICATION_SCHEMA_VERSION = 1
LOADOUT_DEFINITION_SCHEMA_VERSION = 1
MAX_AUTHORING_FILE_BYTES = 4 * 1024 * 1024
AUTHORING_POLICIES = ("enforce", "observe", "ignore")
EDITOR_METADATA_SCHEMA_VERSION = 1
_SAFE_ID_RE = re.compile(r"[a-z][a-z0-9_]{2,47}")


class StrategyAuthoringError(ValueError):
    """Raised when an authoring source or stored revision is invalid."""


class StrategyAuthoringConflictError(StrategyAuthoringError):
    """Raised when optimistic publication state is stale or immutable."""


Normalizer = Callable[[object], Any]
InitialValueFactory = Callable[[], Any]
EditorMetadataFactory = Callable[[Any], Mapping[str, Any]]


@dataclass(frozen=True)
class SettingDefinition:
    """Immutable metadata and validation for one stable authoring setting."""

    id: str
    display_name: str
    section: str
    editor_type: str
    allowed_policies: tuple[str, ...]
    normalizer: Normalizer
    initial_value_factory: InitialValueFactory
    editor_metadata_factory: EditorMetadataFactory
    dependencies: tuple[str, ...]
    runtime_destination: str
    adapter: str
    observation_supported: bool
    repair_supported: bool

    def catalog_item(
        self,
        *,
        module_preset_catalog: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        try:
            initial_value = self.normalizer(self.initial_value_factory())
            raw_editor = self.editor_metadata_factory(
                copy.deepcopy(initial_value)
            )
            if self.id == "modules" and module_preset_catalog is not None:
                raw_editor = _merge_module_preset_editor_metadata(
                    raw_editor,
                    module_preset_catalog,
                )
            editor = _validate_editor_metadata(
                self,
                initial_value,
                raw_editor,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StrategyAuthoringError(
                f"setting {self.id!r} has invalid editor metadata: {exc}"
            ) from exc
        return {
            "id": self.id,
            "display_name": self.display_name,
            "section": self.section,
            "editor_type": self.editor_type,
            "allowed_policies": list(self.allowed_policies),
            "dependencies": list(self.dependencies),
            "dependency_display_names": [
                FARM_SETTING_REGISTRY[dependency].display_name
                for dependency in self.dependencies
            ],
            "runtime_destination": self.runtime_destination,
            "observation_supported": self.observation_supported,
            "repair_supported": self.repair_supported,
            "initial_value": copy.deepcopy(initial_value),
            "editor": editor,
        }


def _fixed_value(setting_id: str, expected: str) -> Normalizer:
    def normalize(value: object) -> str:
        normalized = str(value or "").strip()
        if normalized != expected:
            raise ValueError(f"{setting_id} currently requires {expected!r}")
        return normalized

    return normalize


def _card_recharge_modes(value: object) -> dict[str, str]:
    return {
        label: mode.value
        for label, mode in normalize_card_recharge_modes(value).items()
    }


def _free_upgrade_locks(value: object) -> list[str]:
    return list(
        normalize_free_upgrade_lock_requirements(value, require_farm_set=True)
    )


def _guardian_chips(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("guardian_chips must be a list")
    normalized = [str(chip or "").strip() for chip in value]
    if len(normalized) != 3 or set(normalized) != {"Fetch", "Summon", "Scout"}:
        raise ValueError("guardian_chips must contain Fetch, Summon, and Scout")
    return normalized


def _enabled_auto_pick(value: object) -> bool:
    if value is not True:
        raise ValueError("auto_pick_perks must currently be true")
    return True


def _perk_bans(value: object) -> list[str]:
    bans, _ = normalize_perk_configuration_requirements(
        {"perk_bans": value, "perk_auto_pick_order": ["damage"]}
    )
    return bans


def _perk_auto_pick_order(value: object) -> list[str]:
    _, order = normalize_perk_configuration_requirements(
        {"perk_bans": [], "perk_auto_pick_order": value}
    )
    return order


def _ultimate_weapons(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("ultimate_weapons must be a non-empty object")
    normalized: dict[str, dict[str, str]] = {}
    for raw_label, raw_toggles in value.items():
        label = str(raw_label or "").strip()
        if not label or not isinstance(raw_toggles, Mapping) or not raw_toggles:
            raise ValueError(
                "ultimate_weapons entries require a label and toggle object"
            )
        toggles: dict[str, str] = {}
        for raw_toggle, raw_state in raw_toggles.items():
            toggle = str(raw_toggle or "").strip().lower()
            state = (
                "on"
                if raw_state is True
                else "off"
                if raw_state is False
                else str(raw_state or "").strip().lower()
            )
            if not toggle or state not in {"on", "off"}:
                raise ValueError(
                    "ultimate_weapons toggles require on/off states"
                )
            if toggle == "stun" and (
                label.lower() != "poison swamp" or state != "off"
            ):
                raise ValueError(
                    "ultimate_weapons supports only Poison Swamp stun=off"
                )
            toggles[toggle] = state
        normalized[label] = toggles
    return normalized


def _normalize_loadout_definition(setting_id: str, value: object) -> Any:
    if setting_id == "modules":
        return normalize_gc_module_requirements(value)
    if setting_id == "target_priority":
        if not isinstance(value, list):
            raise ValueError("target_priority local definition must be a list")
        return validate_target_priority_order(value)
    if setting_id == "orb_distance":
        return normalize_orb_distance_preset(value)
    raise ValueError(f"unsupported loadout definition setting {setting_id!r}")


def _preset_or_local_value(setting_id: str) -> Normalizer:
    def normalize(value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(
                f"{setting_id} must select exactly one preset or local definition"
            )
        if set(value) == {"preset"}:
            preset = str(value.get("preset") or "").strip()
            if not preset:
                raise ValueError(f"{setting_id} preset must be a non-empty id")
            return {"preset": preset}
        if set(value) == {"local"}:
            return {
                "local": _normalize_loadout_definition(
                    setting_id,
                    value.get("local"),
                )
            }
        raise ValueError(
            f"{setting_id} must define exactly one of preset or local"
        )

    return normalize


def _damage_slider(value: object) -> str:
    return normalize_damage_percentage(value)


def _farm_setup_initial(setting_id: str) -> InitialValueFactory:
    def load() -> Any:
        baseline = _farm_baseline_settings()
        if setting_id not in baseline:
            raise ValueError(f"Farm baseline is missing {setting_id!r}")
        return copy.deepcopy(baseline[setting_id])

    return load


def _farm_loadout_initial(setting_id: str, *, preset: bool) -> InitialValueFactory:
    def load() -> Any:
        source = _load_yaml_mapping(
            FARM_DEFAULT_STRATEGY_SOURCE_PATH,
            "default Farm authoring source",
        )
        loadout = source.get("loadout")
        if not isinstance(loadout, Mapping):
            raise ValueError("default Farm authoring source requires loadout")
        policy = loadout.get(setting_id)
        if not isinstance(policy, Mapping):
            raise ValueError(
                f"default Farm authoring source is missing {setting_id!r}"
            )
        return (
            {"preset": copy.deepcopy(policy.get("preset"))}
            if preset
            else copy.deepcopy(policy.get("value"))
        )

    return load


def _editor_option(value: object, display_name: str) -> dict[str, Any]:
    return {
        "value": copy.deepcopy(value),
        "display_name": str(display_name),
    }


def _title_identifier(value: object) -> str:
    return " ".join(
        word.capitalize() for word in str(value or "").replace("_", " ").split()
    )


def _fixed_editor(initial_value: Any) -> Mapping[str, Any]:
    return {
        "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
        "value_kind": "string",
        "fixed": True,
        "help_text": "This setting has one runtime-supported value.",
        "options": [_editor_option(initial_value, str(initial_value))],
    }


def _boolean_editor(initial_value: Any) -> Mapping[str, Any]:
    return {
        "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
        "value_kind": "boolean",
        "fixed": True,
        "help_text": "Auto Pick Perks is currently required to be enabled when managed.",
        "options": [_editor_option(True, "Enabled")],
    }


def _card_recharge_editor(initial_value: Any) -> Mapping[str, Any]:
    display_names = {
        CardRechargeMode.AUTO_REACTIVATE.value: "Auto-reactivate",
        CardRechargeMode.READY_AFTER_RECHARGE.value: "Ready after recharge",
    }
    options = [
        _editor_option(mode.value, display_names[mode.value])
        for mode in CardRechargeMode
        if mode is not CardRechargeMode.UNKNOWN
    ]
    return {
        "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
        "value_kind": "object",
        "fixed": False,
        "help_text": (
            "Choose one recharge behavior for every server-declared Card; "
            "the complete mapping is validated on Linux."
        ),
        "preserve_unknown_fields": False,
        "fields": [
            {
                "key": label,
                "display_name": label,
                "required": True,
                "fixed": False,
                "initial_value": initial_value[label],
                "options": copy.deepcopy(options),
            }
            for label in CARD_RECHARGE_LABELS
        ],
    }


def _ordered_list_editor(
    *,
    options: tuple[str, ...],
    allow_reorder: bool,
    order_significant: bool,
    help_text: str,
) -> EditorMetadataFactory:
    def metadata(initial_value: Any) -> Mapping[str, Any]:
        return {
            "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
            "value_kind": "array",
            "fixed": not allow_reorder,
            "help_text": help_text,
            "options": [
                _editor_option(option, option) for option in options
            ],
            "list_constraints": {
                "minimum_items": len(options),
                "maximum_items": len(options),
                "unique_items": True,
                "allow_add": False,
                "allow_remove": False,
                "allow_reorder": allow_reorder,
                "order_significant": order_significant,
                "exact_items": list(options),
            },
        }

    return metadata


def _perk_list_editor(
    *,
    maximum_items: int,
    allow_empty: bool,
    allow_reorder: bool,
    order_significant: bool,
    help_text: str,
) -> EditorMetadataFactory:
    def metadata(initial_value: Any) -> Mapping[str, Any]:
        return {
            "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
            "value_kind": "array",
            "fixed": False,
            "help_text": help_text,
            "options": [
                _editor_option(identifier, display_name)
                for identifier, display_name in PERK_CONFIGURATION_LABELS.items()
            ],
            "list_constraints": {
                "minimum_items": 0 if allow_empty else 1,
                "maximum_items": maximum_items,
                "unique_items": True,
                "allow_add": True,
                "allow_remove": True,
                "allow_reorder": allow_reorder,
                "order_significant": order_significant,
                "exact_items": [],
            },
        }

    return metadata


def _ultimate_weapon_editor(initial_value: Any) -> Mapping[str, Any]:
    groups = []
    for label, toggles in initial_value.items():
        fields = []
        for toggle, initial_state in toggles.items():
            options = []
            for state in ("on", "off"):
                candidate = copy.deepcopy(initial_value)
                candidate[label][toggle] = state
                try:
                    _ultimate_weapons(candidate)
                except (TypeError, ValueError):
                    continue
                options.append(_editor_option(state, _title_identifier(state)))
            fields.append(
                {
                    "key": toggle,
                    "display_name": _title_identifier(toggle),
                    "required": False,
                    "fixed": len(options) == 1,
                    "initial_value": initial_state,
                    "options": options,
                }
            )
        groups.append(
            {
                "key": label,
                "display_name": label,
                "initially_included": True,
                "allow_selection": True,
                "minimum_selected_fields": 1,
                "preserve_unknown_fields": True,
                "fields": fields,
            }
        )
    return {
        "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
        "value_kind": "object",
        "fixed": False,
        "help_text": (
            "Manage only server-declared toggles. Unrecognized retained weapons "
            "and toggle fields remain embedded unchanged."
        ),
        "preserve_unknown_fields": True,
        "allow_group_selection": True,
        "minimum_selected_groups": 1,
        "groups": groups,
    }


def _loadout_preset_definitions(
    setting_id: str,
    *,
    module_preset_definitions: Optional[Mapping[str, Any]] = None,
    catalog_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Load one current shared catalog without normalizing its definitions."""

    if setting_id not in _LOADOUT_PRESET_PATHS:
        raise StrategyAuthoringError(
            f"setting {setting_id!r} has no loadout-definition contract"
        )
    if setting_id == "modules" and module_preset_definitions is not None:
        if not module_preset_definitions:
            raise StrategyAuthoringError("modules preset catalog requires presets")
        return copy.deepcopy(dict(module_preset_definitions))

    path = catalog_path or _LOADOUT_PRESET_PATHS[setting_id]
    catalog = _load_yaml_mapping(path, f"{setting_id} preset catalog")
    if catalog.get("schema_version") != 1:
        raise StrategyAuthoringError(
            f"{setting_id} preset catalog has an unsupported schema"
        )
    presets = catalog.get("presets")
    if not isinstance(presets, Mapping) or not presets:
        raise StrategyAuthoringError(
            f"{setting_id} preset catalog requires presets"
        )
    return copy.deepcopy(dict(presets))


def _loadout_preset_catalog_fingerprint(
    setting_id: str,
    presets: Mapping[str, Any],
) -> str:
    """Fingerprint the exact catalog snapshot offered to an authoring client."""

    return fingerprint_document(
        {
            "schema_version": 1,
            "setting_id": setting_id,
            "presets": copy.deepcopy(dict(presets)),
        }
    )


def _preset_editor(path: Path, setting_id: str) -> EditorMetadataFactory:
    def metadata(initial_value: Any) -> Mapping[str, Any]:
        presets = _loadout_preset_definitions(
            setting_id,
            catalog_path=path,
        )
        options = [
            _editor_option(identifier, _title_identifier(identifier))
            for identifier in presets
        ]
        return {
            "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
            "value_kind": "object",
            "fixed": len(options) == 1,
            "help_text": "Choices come from the current server preset catalog.",
            "preserve_unknown_fields": False,
            "preset_catalog_fingerprint": _loadout_preset_catalog_fingerprint(
                setting_id,
                presets,
            ),
            "fields": [
                {
                    "key": "preset",
                    "display_name": "Preset",
                    "required": True,
                    "fixed": len(options) == 1,
                    "initial_value": initial_value["preset"],
                    "options": options,
                }
            ],
        }

    return metadata


def _merge_module_preset_editor_metadata(
    raw_editor: Mapping[str, Any],
    module_preset_catalog: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace only Module option values from one validated merged snapshot.

    The revision-24 field/option wire shape remains unchanged. Rich preset
    details are returned separately by the authoring catalog.
    """

    editor = copy.deepcopy(dict(raw_editor))
    fields = editor.get("fields")
    items = module_preset_catalog.get("items")
    if (
        not isinstance(fields, list)
        or len(fields) != 1
        or not isinstance(fields[0], Mapping)
        or not isinstance(items, list)
        or not items
    ):
        raise ValueError("merged Module preset catalog is incomplete")
    options: list[dict[str, Any]] = []
    definitions: dict[str, Any] = {}
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("merged Module preset items must be objects")
        identifier = str(item.get("id") or "").strip()
        display_name = str(item.get("display_name") or "").strip()
        if not identifier or not display_name or identifier in seen:
            raise ValueError("merged Module preset IDs and names must be unique")
        options.append(_editor_option(identifier, display_name))
        definitions[identifier] = copy.deepcopy(item.get("definition"))
        seen.add(identifier)
    fields[0]["options"] = options
    fields[0]["fixed"] = len(options) == 1
    editor["fixed"] = len(options) == 1
    if module_preset_catalog.get("id") != MODULE_PRESET_CATALOG_ID:
        raise ValueError("merged Module preset catalog has the wrong identity")
    editor["preset_catalog"] = MODULE_PRESET_CATALOG_ID
    editor["preset_catalog_fingerprint"] = _loadout_preset_catalog_fingerprint(
        "modules",
        definitions,
    )
    return editor


def _local_definition_initial(setting_id: str, initial_value: Any) -> Any:
    """Resolve the default preset into one canonical local-editor draft."""

    if not isinstance(initial_value, Mapping) or set(initial_value) != {"preset"}:
        raise ValueError(
            f"{setting_id} local editor requires a preset initial value"
        )
    catalog = _load_yaml_mapping(
        _LOADOUT_PRESET_PATHS[setting_id],
        f"{setting_id} preset catalog",
    )
    presets = catalog.get("presets")
    preset = initial_value.get("preset")
    if not isinstance(presets, Mapping) or preset not in presets:
        raise ValueError(
            f"{setting_id} initial preset {preset!r} is absent from the catalog"
        )
    return _normalize_loadout_definition(
        setting_id,
        copy.deepcopy(presets[preset]),
    )


def _module_local_editor(initial_value: Any) -> Mapping[str, Any]:
    local_initial = _local_definition_initial("modules", initial_value)
    catalog = load_module_icon_catalog()
    modules_by_family = {
        family: [module for module in catalog.modules if module.family == family]
        for family in {slot.family for slot in catalog.slots}
    }
    return {
        "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
        "key": "local",
        "display_name": "Profile-local definition",
        "value_kind": "object",
        "fixed": False,
        "help_text": (
            "Choose one family-valid Ancestral module for every server-declared "
            "slot. A module cannot be selected more than once."
        ),
        "initial_value": local_initial,
        "server_normalized_text": False,
        "preserve_unknown_fields": False,
        "unique_field_values": True,
        "fields": [
            {
                "key": slot.key,
                "display_name": _title_identifier(slot.key),
                "required": True,
                "fixed": len(modules_by_family[slot.family]) == 1,
                "initial_value": local_initial[slot.key],
                "options": [
                    _editor_option(module.name, module.name)
                    for module in modules_by_family[slot.family]
                ],
            }
            for slot in catalog.slots
        ],
    }


def _target_priority_local_editor(initial_value: Any) -> Mapping[str, Any]:
    local_initial = _local_definition_initial("target_priority", initial_value)
    return {
        "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
        "key": "local",
        "display_name": "Profile-local definition",
        "value_kind": "array",
        "fixed": False,
        "help_text": (
            "Arrange the complete server-declared target membership in exact "
            "top-to-bottom priority order."
        ),
        "initial_value": local_initial,
        "options": [
            _editor_option(target, target) for target in TARGET_PRIORITY_TARGETS
        ],
        "list_constraints": {
            "minimum_items": len(TARGET_PRIORITY_TARGETS),
            "maximum_items": len(TARGET_PRIORITY_TARGETS),
            "unique_items": True,
            "allow_add": False,
            "allow_remove": False,
            "allow_reorder": True,
            "order_significant": True,
            "exact_items": list(TARGET_PRIORITY_TARGETS),
        },
    }


def _orb_distance_local_editor(initial_value: Any) -> Mapping[str, Any]:
    local_initial = _local_definition_initial("orb_distance", initial_value)
    fields = ("range_basis", "extra", "workshop")
    return {
        "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
        "key": "local",
        "display_name": "Profile-local definition",
        "value_kind": "object",
        "fixed": False,
        "help_text": (
            "Enter Attack Range, Extra Orb distance, and Workshop distance. "
            "Linux validates and normalizes all three values."
        ),
        "initial_value": local_initial,
        "server_normalized_text": True,
        "preserve_unknown_fields": False,
        "unique_field_values": False,
        "fields": [
            {
                "key": key,
                "display_name": _title_identifier(key),
                "required": True,
                "fixed": False,
                "initial_value": local_initial[key],
                "options": [],
            }
            for key in fields
        ],
    }


def _preset_or_local_editor(
    path: Path,
    setting_id: str,
    local_editor_factory: EditorMetadataFactory,
) -> EditorMetadataFactory:
    """Extend the revision-23 preset contract with ignorable local metadata."""

    preset_factory = _preset_editor(path, setting_id)

    def metadata(initial_value: Any) -> Mapping[str, Any]:
        editor = dict(preset_factory(initial_value))
        editor["local_editor"] = local_editor_factory(initial_value)
        return editor

    return metadata


def _damage_percentage_editor(initial_value: Any) -> Mapping[str, Any]:
    return {
        "schema_version": EDITOR_METADATA_SCHEMA_VERSION,
        "value_kind": "string",
        "fixed": False,
        "help_text": (
            "Enter a positive percentage such as 1E-22%. Linux remains the "
            "only parser and canonicalizer."
        ),
        "server_normalized_text": True,
    }


def _definition(
    setting_id: str,
    display_name: str,
    section: str,
    editor_type: str,
    allowed_policies: tuple[str, ...],
    normalizer: Normalizer,
    initial_value_factory: InitialValueFactory,
    editor_metadata_factory: EditorMetadataFactory,
    runtime_destination: str,
    adapter: str,
    *,
    dependencies: tuple[str, ...] = (),
    observation_supported: bool = True,
    repair_supported: bool = True,
) -> SettingDefinition:
    return SettingDefinition(
        id=setting_id,
        display_name=display_name,
        section=section,
        editor_type=editor_type,
        allowed_policies=allowed_policies,
        normalizer=normalizer,
        initial_value_factory=initial_value_factory,
        editor_metadata_factory=editor_metadata_factory,
        dependencies=dependencies,
        runtime_destination=runtime_destination,
        adapter=adapter,
        observation_supported=observation_supported,
        repair_supported=repair_supported,
    )


_ENFORCE = ("enforce",)
_ENFORCE_IGNORE = ("enforce", "ignore")
_LOADOUT_POLICIES = ("enforce", "observe", "ignore")

_SETTING_DEFINITIONS = (
    _definition(
        "cards_deck",
        "Cards deck",
        "Setup",
        "fixed_value",
        _ENFORCE,
        _fixed_value("cards_deck", "Farm"),
        _farm_setup_initial("cards_deck"),
        _fixed_editor,
        "session_preflight.requirements.cards_deck",
        "setup_setting",
    ),
    _definition(
        "card_recharge_modes",
        "Card recharge modes",
        "Setup",
        "card_recharge_modes",
        _ENFORCE,
        _card_recharge_modes,
        _farm_setup_initial("card_recharge_modes"),
        _card_recharge_editor,
        "session_preflight.requirements.card_recharge_modes",
        "setup_setting",
        dependencies=("cards_deck",),
    ),
    _definition(
        "workshop_preset",
        "Workshop preset",
        "Setup",
        "fixed_value",
        _ENFORCE,
        _fixed_value("workshop_preset", "Farm"),
        _farm_setup_initial("workshop_preset"),
        _fixed_editor,
        "session_preflight.requirements.workshop_preset",
        "setup_setting",
    ),
    _definition(
        "free_upgrade_locks",
        "Free Upgrade locks",
        "Setup",
        "ordered_list",
        _ENFORCE,
        _free_upgrade_locks,
        _farm_setup_initial("free_upgrade_locks"),
        _ordered_list_editor(
            options=FARM_FREE_UPGRADE_LOCKS,
            allow_reorder=True,
            order_significant=True,
            help_text=(
                "Farm requires this exact three-lock set. Membership is fixed; "
                "the supported inspection order may be rearranged."
            ),
        ),
        "session_preflight.requirements.free_upgrade_locks",
        "setup_setting",
        dependencies=("workshop_preset",),
    ),
    _definition(
        "bots_preset",
        "Bots preset",
        "Setup",
        "fixed_value",
        _ENFORCE,
        _fixed_value("bots_preset", "Farm"),
        _farm_setup_initial("bots_preset"),
        _fixed_editor,
        "session_preflight.requirements.bots_preset",
        "setup_setting",
    ),
    _definition(
        "guardian_chips",
        "Guardian chips",
        "Setup",
        "ordered_list",
        _ENFORCE,
        _guardian_chips,
        _farm_setup_initial("guardian_chips"),
        _ordered_list_editor(
            options=("Fetch", "Summon", "Scout"),
            allow_reorder=False,
            order_significant=False,
            help_text=(
                "Farm currently supports exactly Fetch, Summon, and Scout. "
                "Runtime behavior treats their source order as equivalent."
            ),
        ),
        "session_preflight.requirements.guardian_chips",
        "setup_setting",
    ),
    _definition(
        "auto_pick_perks",
        "Auto Pick Perks",
        "Perks",
        "boolean",
        _ENFORCE_IGNORE,
        _enabled_auto_pick,
        _farm_setup_initial("auto_pick_perks"),
        _boolean_editor,
        "session_preflight.requirements.auto_pick_perks",
        "setup_setting",
    ),
    _definition(
        "perk_bans",
        "Perk bans",
        "Perks",
        "perk_multiselect",
        _ENFORCE_IGNORE,
        _perk_bans,
        _farm_setup_initial("perk_bans"),
        _perk_list_editor(
            maximum_items=PERK_BAN_CAPACITY,
            allow_empty=True,
            allow_reorder=False,
            order_significant=False,
            help_text=(
                f"Choose at most {PERK_BAN_CAPACITY} unique Perks. Ban order "
                "does not affect runtime comparison."
            ),
        ),
        "session_preflight.requirements.perk_bans",
        "setup_setting",
        dependencies=("auto_pick_perks",),
    ),
    _definition(
        "perk_auto_pick_order",
        "Auto Pick priority",
        "Perks",
        "perk_order",
        _ENFORCE_IGNORE,
        _perk_auto_pick_order,
        _farm_setup_initial("perk_auto_pick_order"),
        _perk_list_editor(
            maximum_items=len(PERK_CONFIGURATION_LABELS),
            allow_empty=False,
            allow_reorder=True,
            order_significant=True,
            help_text=(
                "Choose unique Perks in exact top-to-bottom Auto Pick priority."
            ),
        ),
        "session_preflight.requirements.perk_auto_pick_order",
        "setup_setting",
        dependencies=("auto_pick_perks",),
    ),
    _definition(
        "ultimate_weapons",
        "Ultimate Weapons",
        "Ultimate Weapons",
        "ultimate_weapon_toggles",
        _ENFORCE,
        _ultimate_weapons,
        _farm_setup_initial("ultimate_weapons"),
        _ultimate_weapon_editor,
        "session_preflight.requirements.ultimate_weapons",
        "setup_setting",
    ),
    _definition(
        "modules",
        "Modules",
        "Loadout",
        "preset",
        _LOADOUT_POLICIES,
        _preset_or_local_value("modules"),
        _farm_loadout_initial("modules", preset=True),
        _preset_or_local_editor(
            MODULE_PRESETS_PATH,
            "modules",
            _module_local_editor,
        ),
        "run_configuration.loadout.modules",
        "loadout_definition",
    ),
    _definition(
        "damage_slider",
        "Damage Slider",
        "Loadout",
        "damage_percentage",
        _LOADOUT_POLICIES,
        _damage_slider,
        _farm_loadout_initial("damage_slider", preset=False),
        _damage_percentage_editor,
        "run_configuration.loadout.damage_slider",
        "loadout_value",
    ),
    _definition(
        "orb_distance",
        "Orb Distance",
        "Loadout",
        "preset",
        _LOADOUT_POLICIES,
        _preset_or_local_value("orb_distance"),
        _farm_loadout_initial("orb_distance", preset=True),
        _preset_or_local_editor(
            ORB_DISTANCE_PRESETS_PATH,
            "orb_distance",
            _orb_distance_local_editor,
        ),
        "run_configuration.loadout.orb_distance",
        "loadout_definition",
    ),
    _definition(
        "target_priority",
        "Target Priority",
        "Loadout",
        "preset",
        _LOADOUT_POLICIES,
        _preset_or_local_value("target_priority"),
        _farm_loadout_initial("target_priority", preset=True),
        _preset_or_local_editor(
            TARGET_PRIORITY_PRESETS_PATH,
            "target_priority",
            _target_priority_local_editor,
        ),
        "run_configuration.loadout.target_priority",
        "loadout_definition",
    ),
)

FARM_SETTING_REGISTRY: Mapping[str, SettingDefinition] = MappingProxyType(
    {definition.id: definition for definition in _SETTING_DEFINITIONS}
)
FARM_SETUP_SETTING_IDS = tuple(
    definition.id
    for definition in _SETTING_DEFINITIONS
    if definition.adapter == "setup_setting"
)
FARM_LOADOUT_SETTING_IDS = tuple(
    definition.id
    for definition in _SETTING_DEFINITIONS
    if definition.adapter.startswith("loadout_")
)
_IGNORED_SETUP_PLACEHOLDERS = {
    "auto_pick_perks": True,
    "perk_bans": [],
    "perk_auto_pick_order": ["damage"],
}


def _metadata_value_key(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validated_editor_options(raw: object, path: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path} must be a non-empty list")
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_option in enumerate(raw):
        option_path = f"{path}[{index}]"
        if not isinstance(raw_option, Mapping):
            raise ValueError(f"{option_path} must be an object")
        unknown = sorted(set(raw_option) - {"value", "display_name"})
        if unknown or "value" not in raw_option:
            raise ValueError(
                f"{option_path} has invalid fields: {', '.join(unknown) or 'value'}"
            )
        display_name = str(raw_option.get("display_name") or "").strip()
        if not display_name:
            raise ValueError(f"{option_path}.display_name is required")
        value = copy.deepcopy(raw_option["value"])
        key = _metadata_value_key(value)
        if key in seen:
            raise ValueError(f"{path} cannot repeat option values")
        seen.add(key)
        options.append({"value": value, "display_name": display_name})
    return options


def _metadata_option_contains(
    options: list[dict[str, Any]],
    value: object,
) -> bool:
    key = _metadata_value_key(value)
    return any(_metadata_value_key(option["value"]) == key for option in options)


def _normalize_local_editor_candidate(
    definition: SettingDefinition,
    key: str,
    value: Any,
) -> Any:
    return definition.normalizer({key: copy.deepcopy(value)})[key]


def _validate_local_object_editor_metadata(
    definition: SettingDefinition,
    metadata: dict[str, Any],
) -> None:
    initial_value = metadata["initial_value"]
    if not isinstance(initial_value, Mapping):
        raise ValueError("editor.local_editor initial_value must be an object")
    if metadata.get("preserve_unknown_fields") is not False:
        raise ValueError(
            "editor.local_editor must reject unknown object fields"
        )
    if not isinstance(metadata.get("unique_field_values"), bool):
        raise ValueError(
            "editor.local_editor.unique_field_values must be boolean"
        )
    if not isinstance(metadata.get("server_normalized_text"), bool):
        raise ValueError(
            "editor.local_editor.server_normalized_text must be boolean"
        )
    raw_fields = metadata.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError("editor.local_editor.fields must be a non-empty list")

    fields: list[dict[str, Any]] = []
    keys: set[str] = set()
    for index, raw_field in enumerate(raw_fields):
        path = f"editor.local_editor.fields[{index}]"
        if not isinstance(raw_field, Mapping):
            raise ValueError(f"{path} must be an object")
        unknown = sorted(
            set(raw_field)
            - {
                "key",
                "display_name",
                "required",
                "fixed",
                "initial_value",
                "options",
            }
        )
        if unknown:
            raise ValueError(
                f"{path} has unsupported fields: {', '.join(unknown)}"
            )
        key = str(raw_field.get("key") or "").strip()
        display_name = str(raw_field.get("display_name") or "").strip()
        if not key or not display_name or key in keys:
            raise ValueError(f"{path} requires a unique key and display_name")
        keys.add(key)
        if raw_field.get("required") is not True:
            raise ValueError(f"{path} must be required")
        if not isinstance(raw_field.get("fixed"), bool):
            raise ValueError(f"{path}.fixed must be boolean")
        if key not in initial_value:
            raise ValueError(
                f"editor.local_editor initial_value is missing field {key!r}"
            )
        field_initial = copy.deepcopy(raw_field.get("initial_value"))
        if field_initial != initial_value[key]:
            raise ValueError(
                f"{path}.initial_value does not match the local initial value"
            )

        if metadata["server_normalized_text"]:
            if not isinstance(field_initial, str):
                raise ValueError(
                    f"{path}.initial_value must be text for Linux normalization"
                )
            if raw_field["fixed"] is not False:
                raise ValueError(f"{path} server-normalized text cannot be fixed")
            raw_options = raw_field.get("options")
            if raw_options not in (None, []):
                raise ValueError(
                    f"{path} server-normalized text cannot declare choices"
                )
            options: list[dict[str, Any]] = []
        else:
            options = _validated_editor_options(
                raw_field.get("options"),
                f"{path}.options",
            )
            if not _metadata_option_contains(options, field_initial):
                raise ValueError(f"{path}.initial_value is absent from options")
            if raw_field["fixed"] != (len(options) == 1):
                raise ValueError(f"{path}.fixed does not match its options")

        fields.append(
            {
                "key": key,
                "display_name": display_name,
                "required": True,
                "fixed": raw_field["fixed"],
                "initial_value": field_initial,
                "options": options,
            }
        )

    if keys != set(initial_value):
        raise ValueError(
            "editor.local_editor.fields must cover the complete local initial value"
        )

    if metadata["unique_field_values"]:
        values = list(initial_value.values())
        if len({_metadata_value_key(value) for value in values}) != len(values):
            raise ValueError(
                "editor.local_editor initial object violates unique_field_values"
            )

    if not metadata["server_normalized_text"]:
        for field in fields:
            for option in field["options"]:
                candidate = copy.deepcopy(dict(initial_value))
                previous = candidate[field["key"]]
                selected = copy.deepcopy(option["value"])
                duplicate_key = next(
                    (
                        other_key
                        for other_key, current in candidate.items()
                        if other_key != field["key"]
                        and _metadata_value_key(current)
                        == _metadata_value_key(selected)
                    ),
                    None,
                )
                if duplicate_key is not None:
                    candidate[duplicate_key] = previous
                candidate[field["key"]] = selected
                normalized = _normalize_local_editor_candidate(
                    definition,
                    metadata["key"],
                    candidate,
                )
                if normalized != candidate:
                    raise ValueError(
                        f"{field['key']!r} local option is not canonical"
                    )

    if not metadata["server_normalized_text"]:
        overlapping: tuple[str, str, Any] | None = None
        for first_index, first in enumerate(fields):
            first_keys = {
                _metadata_value_key(option["value"]): option["value"]
                for option in first["options"]
            }
            for second in fields[first_index + 1 :]:
                common = next(
                    (
                        (key, value)
                        for key, value in first_keys.items()
                        if any(
                            _metadata_value_key(option["value"]) == key
                            for option in second["options"]
                        )
                    ),
                    None,
                )
                if common is not None:
                    overlapping = (first["key"], second["key"], common[1])
                    break
            if overlapping is not None:
                break
        if metadata["unique_field_values"] and overlapping is None:
            raise ValueError(
                "unique_field_values metadata has no overlapping choices to enforce"
            )
        if overlapping is not None:
            first_key, second_key, repeated = overlapping
            duplicate = copy.deepcopy(dict(initial_value))
            duplicate[first_key] = copy.deepcopy(repeated)
            duplicate[second_key] = copy.deepcopy(repeated)
            try:
                _normalize_local_editor_candidate(
                    definition,
                    metadata["key"],
                    duplicate,
                )
            except (KeyError, TypeError, ValueError):
                if not metadata["unique_field_values"]:
                    raise ValueError(
                        "setting normalizer enforces unique field values but "
                        "editor.local_editor does not declare them"
                    )
            else:
                if metadata["unique_field_values"]:
                    raise ValueError(
                        "unique_field_values is not enforced by the setting normalizer"
                    )

    metadata["initial_value"] = copy.deepcopy(dict(initial_value))
    metadata["fields"] = fields


def _validate_local_array_editor_metadata(
    definition: SettingDefinition,
    metadata: dict[str, Any],
) -> None:
    initial_value = metadata["initial_value"]
    if not isinstance(initial_value, list):
        raise ValueError("editor.local_editor initial_value must be an array")
    options = _validated_editor_options(
        metadata.get("options"),
        "editor.local_editor.options",
    )
    if any(not isinstance(option["value"], str) for option in options):
        raise ValueError("editor.local_editor array options must be strings")
    raw_constraints = metadata.get("list_constraints")
    if not isinstance(raw_constraints, Mapping):
        raise ValueError("editor.local_editor requires list_constraints")
    constraint_fields = {
        "minimum_items",
        "maximum_items",
        "unique_items",
        "allow_add",
        "allow_remove",
        "allow_reorder",
        "order_significant",
        "exact_items",
    }
    if set(raw_constraints) != constraint_fields:
        raise ValueError(
            "editor.local_editor list_constraints must define the complete contract"
        )
    minimum = raw_constraints.get("minimum_items")
    maximum = raw_constraints.get("maximum_items")
    if (
        isinstance(minimum, bool)
        or isinstance(maximum, bool)
        or not isinstance(minimum, int)
        or not isinstance(maximum, int)
        or minimum < 0
        or maximum < minimum
    ):
        raise ValueError("editor.local_editor list item bounds are invalid")
    for flag in constraint_fields - {
        "minimum_items",
        "maximum_items",
        "exact_items",
    }:
        if not isinstance(raw_constraints.get(flag), bool):
            raise ValueError(
                f"editor.local_editor.list_constraints.{flag} must be boolean"
            )
    exact_items = raw_constraints.get("exact_items")
    if not isinstance(exact_items, list) or any(
        not isinstance(item, str) or not item for item in exact_items
    ):
        raise ValueError(
            "editor.local_editor.list_constraints.exact_items must be an array"
        )
    if len(set(exact_items)) != len(exact_items):
        raise ValueError("editor.local_editor exact_items must be unique")
    if maximum > len(options) or not minimum <= len(initial_value) <= maximum:
        raise ValueError("editor.local_editor list bounds do not match its values")
    if raw_constraints["unique_items"] and len(set(initial_value)) != len(
        initial_value
    ):
        raise ValueError("editor.local_editor initial list must be unique")
    if any(not _metadata_option_contains(options, item) for item in initial_value):
        raise ValueError("editor.local_editor initial list has an unknown option")
    if set(exact_items) != set(initial_value):
        raise ValueError(
            "editor.local_editor exact_items must match initial membership"
        )
    if minimum != len(exact_items) or maximum != len(exact_items):
        raise ValueError("editor.local_editor exact_items must match fixed bounds")
    if raw_constraints["allow_add"] or raw_constraints["allow_remove"]:
        raise ValueError("editor.local_editor exact list cannot add or remove")
    if not raw_constraints["allow_reorder"] or not raw_constraints[
        "order_significant"
    ]:
        raise ValueError(
            "editor.local_editor exact priority list must allow significant ordering"
        )
    expected_fixed = not any(
        raw_constraints[action]
        for action in ("allow_add", "allow_remove", "allow_reorder")
    )
    if metadata["fixed"] != expected_fixed:
        raise ValueError("editor.local_editor fixed does not match list actions")
    reversed_value = list(reversed(initial_value))
    if (
        _normalize_local_editor_candidate(
            definition,
            metadata["key"],
            reversed_value,
        )
        != reversed_value
    ):
        raise ValueError("editor.local_editor normalizer does not preserve ordering")
    if initial_value:
        duplicate = [initial_value[0], initial_value[0], *initial_value[2:]]
        try:
            _normalize_local_editor_candidate(
                definition,
                metadata["key"],
                duplicate,
            )
        except (KeyError, TypeError, ValueError):
            pass
        else:
            raise ValueError(
                "editor.local_editor unique_items is not enforced by the normalizer"
            )
    metadata["options"] = options
    metadata["list_constraints"] = copy.deepcopy(dict(raw_constraints))


def _validate_local_editor_metadata(
    definition: SettingDefinition,
    raw_metadata: object,
) -> dict[str, Any]:
    if not isinstance(raw_metadata, Mapping):
        raise ValueError("editor.local_editor must be an object")
    allowed_fields = {
        "schema_version",
        "key",
        "display_name",
        "value_kind",
        "fixed",
        "help_text",
        "initial_value",
        "server_normalized_text",
        "preserve_unknown_fields",
        "unique_field_values",
        "options",
        "fields",
        "list_constraints",
    }
    unknown = sorted(set(raw_metadata) - allowed_fields)
    if unknown:
        raise ValueError(
            "editor.local_editor has unsupported fields: " + ", ".join(unknown)
        )
    metadata = copy.deepcopy(dict(raw_metadata))
    if metadata.get("schema_version") != EDITOR_METADATA_SCHEMA_VERSION:
        raise ValueError("editor.local_editor has an unsupported schema version")
    key = str(metadata.get("key") or "").strip()
    display_name = str(metadata.get("display_name") or "").strip()
    if not key or not display_name:
        raise ValueError("editor.local_editor requires key and display_name")
    metadata["key"] = key
    metadata["display_name"] = display_name
    if metadata.get("fixed") is not False:
        raise ValueError("editor.local_editor cannot be fixed")
    if not isinstance(metadata.get("help_text"), str) or not metadata[
        "help_text"
    ].strip():
        raise ValueError("editor.local_editor help_text is required")
    if "initial_value" not in metadata:
        raise ValueError("editor.local_editor initial_value is required")
    try:
        normalized = _normalize_local_editor_candidate(
            definition,
            key,
            metadata["initial_value"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"editor.local_editor initial_value is invalid: {exc}") from exc
    if normalized != metadata["initial_value"]:
        raise ValueError("editor.local_editor initial_value is not canonical")

    value_kind = metadata.get("value_kind")
    if value_kind == "object":
        _validate_local_object_editor_metadata(definition, metadata)
    elif value_kind == "array":
        _validate_local_array_editor_metadata(definition, metadata)
    else:
        raise ValueError(
            "editor.local_editor value_kind must be 'object' or 'array'"
        )
    return metadata


def _validate_editor_metadata(
    definition: SettingDefinition,
    initial_value: Any,
    raw_metadata: object,
) -> dict[str, Any]:
    """Validate the complete behavior-free editor contract before exposure."""

    if not isinstance(raw_metadata, Mapping):
        raise ValueError("editor metadata must be an object")
    allowed_top_fields = {
        "schema_version",
        "value_kind",
        "fixed",
        "help_text",
        "options",
        "preserve_unknown_fields",
        "fields",
        "list_constraints",
        "allow_group_selection",
        "minimum_selected_groups",
        "groups",
        "server_normalized_text",
        "local_editor",
        "preset_catalog",
        "preset_catalog_fingerprint",
    }
    unknown_top = sorted(set(raw_metadata) - allowed_top_fields)
    if unknown_top:
        raise ValueError(
            "editor metadata has unsupported fields: " + ", ".join(unknown_top)
        )
    metadata = copy.deepcopy(dict(raw_metadata))
    if metadata.get("schema_version") != EDITOR_METADATA_SCHEMA_VERSION:
        raise ValueError("editor metadata has an unsupported schema version")
    expected_kinds = {
        "fixed_value": "string",
        "boolean": "boolean",
        "damage_percentage": "string",
        "card_recharge_modes": "object",
        "preset": "object",
        "ordered_list": "array",
        "perk_multiselect": "array",
        "perk_order": "array",
        "ultimate_weapon_toggles": "object",
    }
    expected_kind = expected_kinds.get(definition.editor_type)
    if expected_kind is None:
        raise ValueError(f"unsupported editor type {definition.editor_type!r}")
    if metadata.get("value_kind") != expected_kind:
        raise ValueError(
            f"{definition.editor_type} editor must declare value_kind={expected_kind!r}"
        )
    if not isinstance(metadata.get("fixed"), bool):
        raise ValueError("editor metadata fixed must be boolean")
    if not isinstance(metadata.get("help_text"), str) or not metadata[
        "help_text"
    ].strip():
        raise ValueError("editor metadata help_text is required")
    if definition.normalizer(copy.deepcopy(initial_value)) != initial_value:
        raise ValueError("initial_value is not in canonical normalized form")
    if "local_editor" in metadata and definition.editor_type != "preset":
        raise ValueError("local_editor metadata is supported only for preset editors")
    if "local_editor" in metadata:
        catalog_fingerprint = str(
            metadata.get("preset_catalog_fingerprint") or ""
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", catalog_fingerprint):
            raise ValueError(
                "preset/local editor metadata requires a catalog fingerprint"
            )
        metadata["preset_catalog_fingerprint"] = catalog_fingerprint
    elif "preset_catalog_fingerprint" in metadata:
        raise ValueError(
            "preset_catalog_fingerprint requires a preset editor with a local editor"
        )
    if "preset_catalog" in metadata:
        preset_catalog = str(metadata.get("preset_catalog") or "").strip()
        if definition.editor_type != "preset" or "local_editor" not in metadata:
            raise ValueError(
                "preset_catalog metadata requires a preset editor with a local editor"
            )
        if not _SAFE_ID_RE.fullmatch(preset_catalog):
            raise ValueError("preset_catalog must be a safe identifier")
        metadata["preset_catalog"] = preset_catalog

    if definition.editor_type in {"fixed_value", "boolean"}:
        options = _validated_editor_options(
            metadata.get("options"),
            "editor.options",
        )
        expected_type = str if definition.editor_type == "fixed_value" else bool
        if any(type(option["value"]) is not expected_type for option in options):
            raise ValueError(
                f"{definition.editor_type} options have the wrong value type"
            )
        if not _metadata_option_contains(options, initial_value):
            raise ValueError("initial_value is absent from editor options")
        if metadata["fixed"] != (len(options) == 1):
            raise ValueError("fixed scalar metadata must expose exactly one option")
        for option in options:
            definition.normalizer(copy.deepcopy(option["value"]))
        metadata["options"] = options

    elif definition.editor_type == "damage_percentage":
        if metadata["fixed"] is not False:
            raise ValueError("damage_percentage editor cannot be fixed")
        if metadata.get("server_normalized_text") is not True:
            raise ValueError(
                "damage_percentage must remain explicitly server-normalized text"
            )
        if not isinstance(initial_value, str):
            raise ValueError("damage_percentage initial_value must be a string")

    elif definition.editor_type in {"card_recharge_modes", "preset"}:
        if not isinstance(initial_value, Mapping):
            raise ValueError("object editor initial_value must be an object")
        if not isinstance(metadata.get("preserve_unknown_fields"), bool):
            raise ValueError("object editor must declare preserve_unknown_fields")
        raw_fields = metadata.get("fields")
        if not isinstance(raw_fields, list) or not raw_fields:
            raise ValueError("object editor fields must be a non-empty list")
        fields: list[dict[str, Any]] = []
        keys: set[str] = set()
        for index, raw_field in enumerate(raw_fields):
            path = f"editor.fields[{index}]"
            if not isinstance(raw_field, Mapping):
                raise ValueError(f"{path} must be an object")
            unknown = sorted(
                set(raw_field)
                - {
                    "key",
                    "display_name",
                    "required",
                    "fixed",
                    "initial_value",
                    "options",
                }
            )
            if unknown:
                raise ValueError(
                    f"{path} has unsupported fields: {', '.join(unknown)}"
                )
            key = str(raw_field.get("key") or "").strip()
            display_name = str(raw_field.get("display_name") or "").strip()
            if not key or not display_name or key in keys:
                raise ValueError(f"{path} requires a unique key and display_name")
            keys.add(key)
            if raw_field.get("required") is not True:
                raise ValueError(f"{path} must be required")
            if not isinstance(raw_field.get("fixed"), bool):
                raise ValueError(f"{path}.fixed must be boolean")
            if key not in initial_value:
                raise ValueError(f"initial_value is missing object field {key!r}")
            field_initial = copy.deepcopy(raw_field.get("initial_value"))
            if field_initial != initial_value[key]:
                raise ValueError(f"{path}.initial_value does not match the setting")
            options = _validated_editor_options(
                raw_field.get("options"),
                f"{path}.options",
            )
            if not _metadata_option_contains(options, field_initial):
                raise ValueError(f"{path}.initial_value is absent from options")
            if raw_field["fixed"] != (len(options) == 1):
                raise ValueError(f"{path}.fixed does not match its options")
            for option in options:
                candidate = copy.deepcopy(dict(initial_value))
                candidate[key] = copy.deepcopy(option["value"])
                definition.normalizer(candidate)
            fields.append(
                {
                    "key": key,
                    "display_name": display_name,
                    "required": True,
                    "fixed": raw_field["fixed"],
                    "initial_value": field_initial,
                    "options": options,
                }
            )
        if keys != set(initial_value):
            raise ValueError("object editor fields must cover the complete initial value")
        if metadata["fixed"] != all(field["fixed"] for field in fields):
            raise ValueError("object editor fixed does not match its fields")
        if metadata["preserve_unknown_fields"] is False:
            unknown_candidate = copy.deepcopy(dict(initial_value))
            unknown_candidate["__unsupported_editor_field__"] = "off"
            try:
                definition.normalizer(unknown_candidate)
            except (TypeError, ValueError):
                pass
            else:
                raise ValueError(
                    "object editor claims unknown fields are rejected, but its "
                    "normalizer accepted one"
                )
        metadata["fields"] = fields
        if "local_editor" in metadata:
            metadata["local_editor"] = _validate_local_editor_metadata(
                definition,
                metadata["local_editor"],
            )

    elif definition.editor_type in {
        "ordered_list",
        "perk_multiselect",
        "perk_order",
    }:
        if not isinstance(initial_value, list):
            raise ValueError("list editor initial_value must be an array")
        options = _validated_editor_options(
            metadata.get("options"),
            "editor.options",
        )
        if any(not isinstance(option["value"], str) for option in options):
            raise ValueError("list editor options must be strings")
        raw_constraints = metadata.get("list_constraints")
        if not isinstance(raw_constraints, Mapping):
            raise ValueError("list editor requires list_constraints")
        constraint_fields = {
            "minimum_items",
            "maximum_items",
            "unique_items",
            "allow_add",
            "allow_remove",
            "allow_reorder",
            "order_significant",
            "exact_items",
        }
        unknown = sorted(set(raw_constraints) - constraint_fields)
        if unknown or set(raw_constraints) != constraint_fields:
            raise ValueError(
                "list_constraints must define the complete constraint contract"
            )
        minimum = raw_constraints.get("minimum_items")
        maximum = raw_constraints.get("maximum_items")
        if (
            isinstance(minimum, bool)
            or isinstance(maximum, bool)
            or not isinstance(minimum, int)
            or not isinstance(maximum, int)
            or minimum < 0
            or maximum < minimum
        ):
            raise ValueError("list item bounds are invalid")
        for flag in constraint_fields - {
            "minimum_items",
            "maximum_items",
            "exact_items",
        }:
            if not isinstance(raw_constraints.get(flag), bool):
                raise ValueError(f"list_constraints.{flag} must be boolean")
        exact_items = raw_constraints.get("exact_items")
        if not isinstance(exact_items, list) or any(
            not isinstance(item, str) or not item for item in exact_items
        ):
            raise ValueError("list_constraints.exact_items must be an array")
        if len(set(exact_items)) != len(exact_items):
            raise ValueError("list_constraints.exact_items must be unique")
        if maximum > len(options):
            raise ValueError("list maximum_items exceeds its unique options")
        if not minimum <= len(initial_value) <= maximum:
            raise ValueError("initial list violates its item bounds")
        if raw_constraints["unique_items"] and len(set(initial_value)) != len(
            initial_value
        ):
            raise ValueError("initial list violates unique_items")
        if any(not _metadata_option_contains(options, item) for item in initial_value):
            raise ValueError("initial list contains a value absent from options")
        if exact_items:
            if set(exact_items) != set(initial_value):
                raise ValueError("exact_items must match initial list membership")
            if minimum != len(exact_items) or maximum != len(exact_items):
                raise ValueError("exact_items must match the fixed item bounds")
            if raw_constraints["allow_add"] or raw_constraints["allow_remove"]:
                raise ValueError("an exact list cannot allow add or remove")
            try:
                definition.normalizer(initial_value[:-1])
            except (TypeError, ValueError):
                pass
            else:
                raise ValueError(
                    "exact_items metadata requires membership the normalizer accepts "
                    "without"
                )
        expected_fixed = not any(
            raw_constraints[action]
            for action in ("allow_add", "allow_remove", "allow_reorder")
        )
        if metadata["fixed"] != expected_fixed:
            raise ValueError("list editor fixed does not match its allowed actions")
        if raw_constraints["unique_items"] and initial_value:
            try:
                definition.normalizer([initial_value[0], initial_value[0]])
            except (TypeError, ValueError):
                pass
            else:
                raise ValueError(
                    "unique_items metadata is not enforced by the normalizer"
                )
        if raw_constraints["allow_reorder"] and len(initial_value) > 1:
            reversed_value = list(reversed(initial_value))
            if definition.normalizer(reversed_value) != reversed_value:
                raise ValueError("list normalizer does not preserve allowed ordering")
        if raw_constraints["allow_remove"] and len(initial_value) > minimum:
            definition.normalizer(initial_value[:-1])
        if raw_constraints["allow_add"]:
            starting_value = (
                initial_value
                if len(initial_value) < maximum
                else initial_value[:-1]
            )
            additional = next(
                (
                    option["value"]
                    for option in options
                    if option["value"] not in starting_value
                ),
                None,
            )
            if additional is not None:
                definition.normalizer([*starting_value, additional])
        metadata["options"] = options
        metadata["list_constraints"] = copy.deepcopy(dict(raw_constraints))

    else:
        if metadata["fixed"] is not False:
            raise ValueError("ultimate_weapon_toggles editor cannot be fixed")
        if metadata.get("preserve_unknown_fields") is not True:
            raise ValueError(
                "ultimate_weapon_toggles must preserve unknown fields"
            )
        if metadata.get("allow_group_selection") is not True:
            raise ValueError("Ultimate Weapon groups must be selectable")
        minimum_groups = metadata.get("minimum_selected_groups")
        if isinstance(minimum_groups, bool) or not isinstance(minimum_groups, int):
            raise ValueError("minimum_selected_groups must be an integer")
        if minimum_groups < 1:
            raise ValueError("minimum_selected_groups must be positive")
        if not isinstance(initial_value, Mapping) or not initial_value:
            raise ValueError("Ultimate Weapon initial_value must be an object")
        raw_groups = metadata.get("groups")
        if not isinstance(raw_groups, list) or not raw_groups:
            raise ValueError("Ultimate Weapon editor requires groups")
        group_keys: set[str] = set()
        groups: list[dict[str, Any]] = []
        for group_index, raw_group in enumerate(raw_groups):
            path = f"editor.groups[{group_index}]"
            if not isinstance(raw_group, Mapping):
                raise ValueError(f"{path} must be an object")
            unknown = sorted(
                set(raw_group)
                - {
                    "key",
                    "display_name",
                    "initially_included",
                    "allow_selection",
                    "minimum_selected_fields",
                    "preserve_unknown_fields",
                    "fields",
                }
            )
            if unknown:
                raise ValueError(
                    f"{path} has unsupported fields: {', '.join(unknown)}"
                )
            group_key = str(raw_group.get("key") or "").strip()
            display_name = str(raw_group.get("display_name") or "").strip()
            if not group_key or not display_name or group_key in group_keys:
                raise ValueError(f"{path} requires a unique key and display_name")
            group_keys.add(group_key)
            if group_key not in initial_value or not isinstance(
                initial_value[group_key], Mapping
            ):
                raise ValueError(f"{path} is absent from initial_value")
            if (
                raw_group.get("initially_included") is not True
                or raw_group.get("allow_selection") is not True
                or raw_group.get("preserve_unknown_fields") is not True
            ):
                raise ValueError(f"{path} has invalid selection/preservation flags")
            minimum_fields = raw_group.get("minimum_selected_fields")
            if (
                isinstance(minimum_fields, bool)
                or not isinstance(minimum_fields, int)
                or minimum_fields < 1
            ):
                raise ValueError(f"{path}.minimum_selected_fields is invalid")
            raw_fields = raw_group.get("fields")
            if not isinstance(raw_fields, list) or not raw_fields:
                raise ValueError(f"{path}.fields must be a non-empty list")
            fields: list[dict[str, Any]] = []
            field_keys: set[str] = set()
            for field_index, raw_field in enumerate(raw_fields):
                field_path = f"{path}.fields[{field_index}]"
                if not isinstance(raw_field, Mapping):
                    raise ValueError(f"{field_path} must be an object")
                unknown = sorted(
                    set(raw_field)
                    - {
                        "key",
                        "display_name",
                        "required",
                        "fixed",
                        "initial_value",
                        "options",
                    }
                )
                if unknown:
                    raise ValueError(
                        f"{field_path} has unsupported fields: {', '.join(unknown)}"
                    )
                field_key = str(raw_field.get("key") or "").strip()
                field_display = str(raw_field.get("display_name") or "").strip()
                if not field_key or not field_display or field_key in field_keys:
                    raise ValueError(
                        f"{field_path} requires a unique key and display_name"
                    )
                field_keys.add(field_key)
                if raw_field.get("required") is not False:
                    raise ValueError(f"{field_path}.required must be false")
                if not isinstance(raw_field.get("fixed"), bool):
                    raise ValueError(f"{field_path}.fixed must be boolean")
                group_initial = initial_value[group_key]
                if field_key not in group_initial:
                    raise ValueError(f"{field_path} is absent from initial_value")
                field_initial = copy.deepcopy(raw_field.get("initial_value"))
                if field_initial != group_initial[field_key]:
                    raise ValueError(
                        f"{field_path}.initial_value does not match the setting"
                    )
                options = _validated_editor_options(
                    raw_field.get("options"),
                    f"{field_path}.options",
                )
                if not _metadata_option_contains(options, field_initial):
                    raise ValueError(
                        f"{field_path}.initial_value is absent from options"
                    )
                if raw_field["fixed"] != (len(options) == 1):
                    raise ValueError(f"{field_path}.fixed does not match options")
                for option in options:
                    candidate = copy.deepcopy(dict(initial_value))
                    candidate[group_key] = copy.deepcopy(dict(group_initial))
                    candidate[group_key][field_key] = copy.deepcopy(
                        option["value"]
                    )
                    definition.normalizer(candidate)
                fields.append(
                    {
                        "key": field_key,
                        "display_name": field_display,
                        "required": False,
                        "fixed": raw_field["fixed"],
                        "initial_value": field_initial,
                        "options": options,
                    }
                )
            if field_keys != set(initial_value[group_key]):
                raise ValueError(
                    f"{path}.fields must cover the complete initial group"
                )
            if minimum_fields > len(fields):
                raise ValueError(f"{path}.minimum_selected_fields is too large")
            groups.append(
                {
                    "key": group_key,
                    "display_name": display_name,
                    "initially_included": True,
                    "allow_selection": True,
                    "minimum_selected_fields": minimum_fields,
                    "preserve_unknown_fields": True,
                    "fields": fields,
                }
            )
        if group_keys != set(initial_value):
            raise ValueError("Ultimate Weapon groups must cover initial_value")
        if minimum_groups > len(groups):
            raise ValueError("minimum_selected_groups is too large")
        for group in groups:
            if len(initial_value) > minimum_groups:
                candidate = copy.deepcopy(dict(initial_value))
                candidate.pop(group["key"])
                definition.normalizer(candidate)
            group_initial = initial_value[group["key"]]
            if len(group_initial) > group["minimum_selected_fields"]:
                candidate = copy.deepcopy(dict(initial_value))
                candidate[group["key"]] = copy.deepcopy(dict(group_initial))
                candidate[group["key"]].pop(next(iter(group_initial)))
                definition.normalizer(candidate)
        unknown_candidate = copy.deepcopy(dict(initial_value))
        first_group = next(iter(unknown_candidate))
        unknown_candidate[first_group] = copy.deepcopy(
            dict(unknown_candidate[first_group])
        )
        unknown_candidate[first_group]["__retained_toggle__"] = "off"
        unknown_candidate["__Retained Weapon__"] = {"primary": "on"}
        normalized_unknown = definition.normalizer(unknown_candidate)
        if normalized_unknown != unknown_candidate:
            raise ValueError(
                "ultimate_weapon_toggles normalizer does not preserve unknown fields"
            )
        metadata["groups"] = groups

    # This is an API contract; fail before serving a non-JSON-safe value.
    json.dumps(
        {"initial_value": initial_value, "editor": metadata},
        ensure_ascii=False,
        allow_nan=False,
    )
    return copy.deepcopy(metadata)


def setting_registry_catalog(
    *,
    module_preset_catalog: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Return the serializable, behavior-free view of the Farm registry."""

    return [
        definition.catalog_item(module_preset_catalog=module_preset_catalog)
        for definition in _SETTING_DEFINITIONS
    ]


def normalize_base_source(
    raw_source: object,
    *,
    revision: Optional[int] = None,
) -> dict[str, Any]:
    """Validate one sparse base snapshot."""

    if not isinstance(raw_source, Mapping):
        raise StrategyAuthoringError("base source must be an object")
    unknown_fields = sorted(
        set(raw_source)
        - {
            "schema_version",
            "kind",
            "id",
            "display_name",
            "family",
            "revision",
            "settings",
        }
    )
    if unknown_fields:
        raise StrategyAuthoringError(
            "base source has unsupported fields: "
            + ", ".join(str(item) for item in unknown_fields)
        )
    schema_version = _authoring_schema_version(raw_source.get("schema_version"))
    if schema_version not in {
        LEGACY_AUTHORING_SCHEMA_VERSION,
        AUTHORING_SCHEMA_VERSION,
    }:
        raise StrategyAuthoringError("Unsupported base source schema")
    if raw_source.get("kind", "base") != "base":
        raise StrategyAuthoringError("base source has the wrong kind")
    identifier = _safe_id(raw_source.get("id"), "base id")
    display_name = _display_name(raw_source.get("display_name"), identifier)
    family = str(raw_source.get("family") or "farm").strip().lower()
    if not _SAFE_ID_RE.fullmatch(family):
        raise StrategyAuthoringError("base family must be a safe identifier")
    selected_revision = revision if revision is not None else raw_source.get("revision")
    if isinstance(selected_revision, bool):
        raise StrategyAuthoringError("base revision must be a positive integer")
    try:
        selected_revision = int(selected_revision)
    except (TypeError, ValueError) as exc:
        raise StrategyAuthoringError(
            "base revision must be a positive integer"
        ) from exc
    if selected_revision < 1:
        raise StrategyAuthoringError("base revision must be a positive integer")
    settings = _normalize_settings(
        raw_source.get("settings"),
        base=True,
        schema_version=schema_version,
    )
    return {
        "schema_version": schema_version,
        "kind": "base",
        "id": identifier,
        "display_name": display_name,
        "family": family,
        "revision": selected_revision,
        "settings": settings,
    }


def normalize_strategy_source(
    raw_source: object,
    *,
    version: Optional[int] = None,
) -> dict[str, Any]:
    """Validate one sparse strategy source without resolving its base."""

    if not isinstance(raw_source, Mapping):
        raise StrategyAuthoringError("strategy source must be an object")
    unknown_fields = sorted(
        set(raw_source)
        - {
            "schema_version",
            "kind",
            "id",
            "display_name",
            "family",
            "tier",
            "version",
            "base",
            "settings",
        }
    )
    if unknown_fields:
        raise StrategyAuthoringError(
            "strategy source has unsupported fields: "
            + ", ".join(str(item) for item in unknown_fields)
        )
    schema_version = _authoring_schema_version(raw_source.get("schema_version"))
    if schema_version not in {
        LEGACY_AUTHORING_SCHEMA_VERSION,
        AUTHORING_SCHEMA_VERSION,
    }:
        raise StrategyAuthoringError("Unsupported strategy source schema")
    if raw_source.get("kind", "strategy") != "strategy":
        raise StrategyAuthoringError("strategy source has the wrong kind")
    identifier = _safe_id(raw_source.get("id"), "strategy id")
    display_name = _display_name(raw_source.get("display_name"), identifier)
    family = str(raw_source.get("family") or "farm").strip().lower()
    if family != "farm":
        raise StrategyAuthoringError("strategy family must be farm")
    raw_tier = raw_source.get("tier")
    if isinstance(raw_tier, bool):
        raise StrategyAuthoringError("strategy tier must be an integer")
    try:
        tier = int(raw_tier)
    except (TypeError, ValueError) as exc:
        raise StrategyAuthoringError("strategy tier must be an integer") from exc
    if not 1 <= tier <= 100:
        raise StrategyAuthoringError("strategy tier must be between 1 and 100")
    selected_version = version if version is not None else raw_source.get("version", 1)
    if isinstance(selected_version, bool):
        raise StrategyAuthoringError("strategy version must be a positive integer")
    try:
        selected_version = int(selected_version)
    except (TypeError, ValueError) as exc:
        raise StrategyAuthoringError(
            "strategy version must be a positive integer"
        ) from exc
    if selected_version < 1:
        raise StrategyAuthoringError("strategy version must be a positive integer")

    normalized = {
        "schema_version": schema_version,
        "kind": "strategy",
        "id": identifier,
        "display_name": display_name,
        "family": family,
        "tier": tier,
        "version": selected_version,
    }
    base = raw_source.get("base")
    if base is not None:
        if not isinstance(base, Mapping):
            raise StrategyAuthoringError("strategy base must be one object")
        unknown = sorted(set(base) - {"id", "revision"})
        if unknown:
            raise StrategyAuthoringError(
                "strategy base has unsupported fields: "
                + ", ".join(str(item) for item in unknown)
            )
        base_id = _safe_id(base.get("id"), "base id")
        raw_revision = base.get("revision")
        if isinstance(raw_revision, bool):
            raise StrategyAuthoringError("base revision must be a positive integer")
        try:
            base_revision = int(raw_revision)
        except (TypeError, ValueError) as exc:
            raise StrategyAuthoringError(
                "base revision must be a positive integer"
            ) from exc
        if base_revision < 1:
            raise StrategyAuthoringError("base revision must be a positive integer")
        normalized["base"] = {"id": base_id, "revision": base_revision}
    normalized["settings"] = _normalize_settings(
        raw_source.get("settings"),
        base=False,
        schema_version=schema_version,
    )
    return normalized


def upgrade_authoring_source_schema(raw_source: object) -> dict[str, Any]:
    """Return a new draft in the current sparse authoring schema.

    Stored schema-2 evidence is normalized in place by the compatibility
    readers.  Only prospective Base/Strategy drafts cross this explicit
    migration boundary.
    """

    if not isinstance(raw_source, Mapping):
        raise StrategyAuthoringError("authoring source must be an object")
    upgraded = copy.deepcopy(dict(raw_source))
    schema_version = _authoring_schema_version(upgraded.get("schema_version"))
    if schema_version not in {
        LEGACY_AUTHORING_SCHEMA_VERSION,
        AUTHORING_SCHEMA_VERSION,
    }:
        raise StrategyAuthoringError("Unsupported authoring source schema")
    upgraded["schema_version"] = AUTHORING_SCHEMA_VERSION
    return upgraded


def _current_definition_snapshot(
    setting_id: str,
    selector: Mapping[str, Any],
    *,
    module_preset_definitions: Optional[Mapping[str, Any]] = None,
    preset_definitions: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve one source selector against the current shared catalogs."""

    if setting_id not in _LOADOUT_PRESET_PATHS:
        raise StrategyAuthoringError(
            f"setting {setting_id!r} has no loadout-definition contract"
        )
    if set(selector) == {"preset"}:
        preset = str(selector["preset"])
        presets: object = (
            copy.deepcopy(dict(preset_definitions))
            if preset_definitions is not None
            else _loadout_preset_definitions(
                setting_id,
                module_preset_definitions=module_preset_definitions,
            )
        )
        if not isinstance(presets, Mapping) or preset not in presets:
            raise StrategyAuthoringError(
                f"unknown {setting_id} preset {preset!r}"
            )
        try:
            definition = _normalize_loadout_definition(
                setting_id,
                presets[preset],
            )
        except (TypeError, ValueError) as exc:
            raise StrategyAuthoringError(
                f"{setting_id} preset {preset!r} is invalid: {exc}"
            ) from exc
        source = "preset"
    elif set(selector) == {"local"}:
        preset = None
        definition = copy.deepcopy(selector["local"])
        source = "local"
    else:
        raise StrategyAuthoringError(
            f"setting {setting_id!r} has an ambiguous definition selector"
        )

    payload: dict[str, Any] = {
        "schema_version": LOADOUT_DEFINITION_SCHEMA_VERSION,
        "source": source,
    }
    if preset is not None:
        payload["preset"] = preset
    payload["definition"] = copy.deepcopy(definition)
    if setting_id == "orb_distance":
        payload["range_relationships"] = _current_orb_range_relationships(
            definition
        )
    return {**payload, "fingerprint": fingerprint_document(payload)}


def materialize_loadout_preset(
    setting_id: object,
    preset_id: object,
    expected_catalog_fingerprint: object,
    *,
    module_preset_definitions: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Return one exact current preset as a normalized profile-local definition.

    The optimistic catalog fingerprint binds the request to the choices the
    client actually displayed. The returned definition comes from the same
    snapshot and the same normalizer used by ordinary authoring resolution.
    """

    normalized_setting_id = str(setting_id or "").strip()
    if normalized_setting_id not in _LOADOUT_PRESET_PATHS:
        raise StrategyAuthoringError(
            "setting_id must be one of: modules, orb_distance, target_priority"
        )
    normalized_preset_id = str(preset_id or "").strip()
    if not normalized_preset_id:
        raise StrategyAuthoringError("preset must be a non-empty id")
    expected_fingerprint = str(expected_catalog_fingerprint or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint):
        raise StrategyAuthoringError(
            "expected_catalog_fingerprint must be a SHA-256 fingerprint"
        )

    presets = _loadout_preset_definitions(
        normalized_setting_id,
        module_preset_definitions=module_preset_definitions,
    )
    current_fingerprint = _loadout_preset_catalog_fingerprint(
        normalized_setting_id,
        presets,
    )
    if current_fingerprint != expected_fingerprint:
        raise StrategyAuthoringConflictError(
            f"{normalized_setting_id} preset catalog changed after it was opened; "
            "reload before editing a copy"
        )

    snapshot = _current_definition_snapshot(
        normalized_setting_id,
        {"preset": normalized_preset_id},
        preset_definitions=presets,
    )
    return {
        "schema_version": LOADOUT_DEFINITION_SCHEMA_VERSION,
        "setting_id": normalized_setting_id,
        "preset": normalized_preset_id,
        "catalog_fingerprint": current_fingerprint,
        "definition": copy.deepcopy(snapshot["definition"]),
        "definition_fingerprint": snapshot["fingerprint"],
    }


def _current_orb_range_relationships(
    selected_definition: object,
) -> list[dict[str, str]]:
    catalog = _load_yaml_mapping(
        ORB_DISTANCE_PRESETS_PATH,
        "orb_distance preset catalog",
    )
    if catalog.get("schema_version") != 1:
        raise StrategyAuthoringError(
            "orb_distance preset catalog has an unsupported schema"
        )
    try:
        selected = normalize_orb_distance_preset(selected_definition)
        relationships = normalize_orb_distance_presets(catalog.get("presets"))
    except (TypeError, ValueError) as exc:
        raise StrategyAuthoringError(
            f"orb_distance range relationships are invalid: {exc}"
        ) from exc
    replaced = False
    merged: list[dict[str, str]] = []
    for relationship in relationships:
        if relationship["range_basis"] == selected["range_basis"]:
            merged.append(copy.deepcopy(selected))
            replaced = True
        else:
            merged.append(copy.deepcopy(relationship))
    if not replaced:
        merged.append(copy.deepcopy(selected))
    return normalize_orb_distance_presets(merged)


def _normalize_retained_definition_snapshot(
    setting_id: str,
    selector: Mapping[str, Any],
    raw_snapshot: object,
) -> dict[str, Any]:
    """Validate retained effective data without consulting mutable catalogs."""

    if not isinstance(raw_snapshot, Mapping):
        raise StrategyAuthoringError(
            f"setting {setting_id!r} lacks its retained definition snapshot"
        )
    expected_source = "preset" if set(selector) == {"preset"} else "local"
    expected_fields = {
        "schema_version",
        "source",
        "definition",
        "fingerprint",
    }
    if expected_source == "preset":
        expected_fields.add("preset")
    if setting_id == "orb_distance":
        expected_fields.add("range_relationships")
    if set(raw_snapshot) != expected_fields:
        raise StrategyAuthoringError(
            f"setting {setting_id!r} retained definition snapshot has "
            "unsupported or missing fields"
        )
    if raw_snapshot.get("schema_version") != LOADOUT_DEFINITION_SCHEMA_VERSION:
        raise StrategyAuthoringError(
            f"setting {setting_id!r} retained definition snapshot has an "
            "unsupported schema"
        )
    if raw_snapshot.get("source") != expected_source:
        raise StrategyAuthoringError(
            f"setting {setting_id!r} retained definition source disagrees "
            "with its selector"
        )
    if expected_source == "preset" and raw_snapshot.get("preset") != selector.get(
        "preset"
    ):
        raise StrategyAuthoringError(
            f"setting {setting_id!r} retained preset identity disagrees "
            "with its selector"
        )
    try:
        definition = _normalize_loadout_definition(
            setting_id,
            raw_snapshot.get("definition"),
        )
    except (TypeError, ValueError) as exc:
        raise StrategyAuthoringError(
            f"setting {setting_id!r} retained definition is invalid: {exc}"
        ) from exc
    if expected_source == "local":
        try:
            selector_definition = _normalize_loadout_definition(
                setting_id,
                selector.get("local"),
            )
        except (TypeError, ValueError) as exc:
            raise StrategyAuthoringError(
                f"setting {setting_id!r} local selector is invalid: {exc}"
            ) from exc
        if definition != selector_definition:
            raise StrategyAuthoringError(
                f"setting {setting_id!r} retained local definition disagrees "
                "with its source selector"
            )
    payload: dict[str, Any] = {
        "schema_version": LOADOUT_DEFINITION_SCHEMA_VERSION,
        "source": expected_source,
    }
    if expected_source == "preset":
        payload["preset"] = str(selector["preset"])
    payload["definition"] = definition
    if setting_id == "orb_distance":
        try:
            relationships = normalize_orb_distance_presets(
                raw_snapshot.get("range_relationships")
            )
        except (TypeError, ValueError) as exc:
            raise StrategyAuthoringError(
                f"setting {setting_id!r} retained range relationships are "
                f"invalid: {exc}"
            ) from exc
        matching = [
            item
            for item in relationships
            if item["range_basis"] == definition["range_basis"]
        ]
        if matching != [definition]:
            raise StrategyAuthoringError(
                "orb_distance retained range relationships do not contain "
                "the selected relationship exactly"
            )
        payload["range_relationships"] = relationships
    fingerprint = str(raw_snapshot.get("fingerprint") or "")
    if fingerprint != fingerprint_document(payload):
        raise StrategyAuthoringError(
            f"setting {setting_id!r} retained definition fingerprint does not match"
        )
    normalized = {**payload, "fingerprint": fingerprint}
    if normalized != dict(raw_snapshot):
        raise StrategyAuthoringError(
            f"setting {setting_id!r} retained definition snapshot is not canonical"
        )
    return normalized


def _resolution_definition_snapshot(
    resolution: object,
    setting_id: str,
) -> object:
    if not isinstance(resolution, Mapping):
        return None
    settings = resolution.get("settings")
    if not isinstance(settings, Mapping):
        return None
    entry = settings.get(setting_id)
    if not isinstance(entry, Mapping):
        return None
    return entry.get("definition_snapshot")


def resolve_strategy_source(
    strategy_source: object,
    base_snapshot: object = None,
    *,
    base_resolution_snapshot: object = None,
    retained_resolution: object = None,
    require_base_definition_snapshots: bool = False,
    module_preset_definitions: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve one sparse source against zero or one exact base snapshot."""

    analysis = analyze_strategy_source(
        strategy_source,
        base_snapshot,
        base_resolution_snapshot=base_resolution_snapshot,
        retained_resolution=retained_resolution,
        require_base_definition_snapshots=require_base_definition_snapshots,
        module_preset_definitions=module_preset_definitions,
    )
    errors = analysis["errors"]
    if errors:
        raise StrategyAuthoringError(str(errors[0]["message"]))
    return copy.deepcopy(analysis["resolution"])


def analyze_strategy_source(
    strategy_source: object,
    base_snapshot: object = None,
    *,
    base_resolution_snapshot: object = None,
    retained_resolution: object = None,
    require_base_definition_snapshots: bool = False,
    module_preset_definitions: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve source intent while retaining dependency errors for review.

    Publication still uses :func:`resolve_strategy_source`, which raises on
    every invalid dependency.  Authoring previews use this non-throwing view so
    a reviewed rebase can show the exact resulting dependency failures without
    reproducing resolution rules in the API layer.
    """

    source = normalize_strategy_source(strategy_source)
    base_ref = source.get("base")
    base: Optional[dict[str, Any]] = None
    if base_ref is None:
        if base_snapshot is not None:
            raise StrategyAuthoringError(
                "strategy without a base cannot receive a base snapshot"
            )
    else:
        if base_snapshot is None:
            raise StrategyAuthoringError(
                f"base revision {base_ref['id']}@{base_ref['revision']} is unavailable"
            )
        base = normalize_base_source(base_snapshot)
        if base["id"] != base_ref["id"] or base["revision"] != base_ref["revision"]:
            raise StrategyAuthoringError(
                "embedded base snapshot does not match the pinned base revision"
            )
        if base["family"] != source["family"]:
            raise StrategyAuthoringError(
                f"base family {base['family']!r} is incompatible with "
                f"strategy family {source['family']!r}"
            )

    base_settings = base["settings"] if base is not None else {}
    local_settings = source["settings"]
    resolved: dict[str, dict[str, Any]] = {}
    for setting_id in FARM_SETTING_REGISTRY:
        inherited = base_settings.get(setting_id)
        local = local_settings.get(setting_id)
        if local is not None and local["policy"] == "ignore":
            entry: dict[str, Any] = {
                "state": "ignored",
                "provenance": {"kind": "local_ignore"},
            }
            if inherited is not None:
                entry["masked_base"] = copy.deepcopy(inherited)
            resolved[setting_id] = entry
            continue
        if local is not None:
            entry = {
                "state": "effective",
                "policy": local["policy"],
                "value": copy.deepcopy(local["value"]),
                "provenance": {"kind": "local"},
            }
            if inherited is not None:
                entry["overridden_base"] = copy.deepcopy(inherited)
            resolved[setting_id] = entry
            continue
        if inherited is not None:
            resolved[setting_id] = {
                "state": "effective",
                "policy": inherited["policy"],
                "value": copy.deepcopy(inherited["value"]),
                "provenance": {
                    "kind": "base",
                    "base_id": base["id"],
                    "revision": base["revision"],
                },
            }
            continue
        resolved[setting_id] = {
            "state": "unmanaged",
            "provenance": {"kind": "unmanaged"},
        }

    source_schema = source["schema_version"]
    for setting_id, definition in FARM_SETTING_REGISTRY.items():
        if definition.adapter != "loadout_definition":
            continue
        entry = resolved[setting_id]
        if entry["state"] != "effective":
            continue
        selector = entry.get("value")
        if not isinstance(selector, Mapping):
            raise StrategyAuthoringError(
                f"setting {setting_id!r} has no definition selector"
            )
        if source_schema == LEGACY_AUTHORING_SCHEMA_VERSION:
            # Exact schema-2 publications retain their original compact
            # resolution.  Ordinary schema-2 drafts are migrated before
            # publication and still validate their current preset here.
            if retained_resolution is None:
                _current_definition_snapshot(
                    setting_id,
                    selector,
                    module_preset_definitions=module_preset_definitions,
                )
            continue

        raw_retained_snapshot = _resolution_definition_snapshot(
            retained_resolution,
            setting_id,
        )
        provenance = entry.get("provenance")
        inherited = (
            isinstance(provenance, Mapping)
            and provenance.get("kind") == "base"
        )
        raw_base_snapshot = None
        if inherited:
            raw_base_snapshot = _resolution_definition_snapshot(
                base_resolution_snapshot,
                setting_id,
            )
            if (
                raw_base_snapshot is None
                and base is not None
                and base["schema_version"] == AUTHORING_SCHEMA_VERSION
                and require_base_definition_snapshots
            ):
                raise StrategyAuthoringError(
                    f"base setting {setting_id!r} lacks its immutable "
                    "definition snapshot"
                )

        retained_snapshot = (
            _normalize_retained_definition_snapshot(
                setting_id,
                selector,
                raw_retained_snapshot,
            )
            if raw_retained_snapshot is not None
            else None
        )
        base_definition_snapshot = (
            _normalize_retained_definition_snapshot(
                setting_id,
                selector,
                raw_base_snapshot,
            )
            if raw_base_snapshot is not None
            else None
        )
        if (
            retained_snapshot is not None
            and base_definition_snapshot is not None
            and retained_snapshot != base_definition_snapshot
        ):
            raise StrategyAuthoringError(
                f"setting {setting_id!r} retained definition snapshot "
                "disagrees with its embedded Base"
            )
        if base_definition_snapshot is not None:
            snapshot = base_definition_snapshot
        elif retained_snapshot is not None:
            snapshot = retained_snapshot
        else:
            snapshot = _current_definition_snapshot(
                setting_id,
                selector,
                module_preset_definitions=module_preset_definitions,
            )
        entry["definition_snapshot"] = snapshot

    errors: list[dict[str, Any]] = []
    for setting_id, definition in FARM_SETTING_REGISTRY.items():
        if resolved[setting_id]["state"] != "effective":
            continue
        missing = [
            dependency
            for dependency in definition.dependencies
            if resolved[dependency]["state"] != "effective"
        ]
        if missing:
            message = (
                f"setting {setting_id!r} requires effective setting(s): "
                + ", ".join(missing)
            )
            errors.append(
                {
                    "code": "missing_dependency",
                    "setting_id": setting_id,
                    "dependencies": missing,
                    "message": message,
                }
            )

    return {
        "source": source,
        "base_snapshot": copy.deepcopy(base),
        "resolution": {
            "schema_version": source_schema,
            "family": source["family"],
            "settings": resolved,
        },
        "errors": errors,
    }


def describe_base_resolution(
    base_source: object,
    retained_resolution: object = None,
    *,
    module_preset_definitions: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Project sparse Base entries through the authoritative resolver.

    A Base is not runnable and may remain incomplete, so dependency errors do
    not make this display projection invalid.  The synthetic empty Strategy is
    used only to obtain the same effective-setting and provenance vocabulary
    that a pinned Strategy receives.
    """

    base = normalize_base_source(base_source)
    analysis = analyze_strategy_source(
        {
            "schema_version": base["schema_version"],
            "kind": "strategy",
            "id": base["id"],
            "display_name": base["display_name"],
            "family": base["family"],
            "tier": 1,
            "version": 1,
            "base": {
                "id": base["id"],
                "revision": base["revision"],
            },
            "settings": {},
        },
        base,
        base_resolution_snapshot=retained_resolution,
        retained_resolution=retained_resolution,
        module_preset_definitions=module_preset_definitions,
    )
    return copy.deepcopy(analysis["resolution"])


def diff_source_documents(
    before_source: object,
    after_source: object,
) -> dict[str, Any]:
    """Return a stable semantic source diff for Base or Strategy review."""

    before, after = _normalize_matching_sources(before_source, after_source)
    before_settings = before["settings"]
    after_settings = after["settings"]
    setting_diff = _diff_setting_mappings(before_settings, after_settings)
    metadata_fields = (
        ("display_name", "Display name"),
        ("family", "Family"),
        ("tier", "Tier"),
        ("base", "Pinned base"),
    )
    metadata_changes = []
    for field, label in metadata_fields:
        if before.get(field) == after.get(field):
            continue
        metadata_changes.append(
            {
                "field": field,
                "label": label,
                "before": copy.deepcopy(before.get(field)),
                "after": copy.deepcopy(after.get(field)),
            }
        )
    return {
        **setting_diff,
        "metadata_changes": metadata_changes,
        "change_count": (
            len(setting_diff["added"])
            + len(setting_diff["removed"])
            + len(setting_diff["changed"])
            + len(metadata_changes)
        ),
    }


def diff_strategy_resolutions(
    before_resolution: object,
    after_resolution: object,
) -> dict[str, Any]:
    """Compare effective values separately from provenance-only changes."""

    before_settings = _resolution_settings(before_resolution)
    after_settings = _resolution_settings(after_resolution)
    changed = []
    provenance_changed = []
    for setting_id in FARM_SETTING_REGISTRY:
        before_entry = before_settings[setting_id]
        after_entry = after_settings[setting_id]
        before_effective = _effective_resolution_view(before_entry)
        after_effective = _effective_resolution_view(after_entry)
        item = {
            "setting_id": setting_id,
            "display_name": FARM_SETTING_REGISTRY[setting_id].display_name,
            "before": copy.deepcopy(before_entry),
            "after": copy.deepcopy(after_entry),
        }
        if before_effective != after_effective:
            changed.append(item)
        elif before_entry.get("provenance") != after_entry.get("provenance"):
            provenance_changed.append(item)
    return {
        "changed": changed,
        "provenance_changed": provenance_changed,
        "change_count": len(changed),
    }


def preview_strategy_rebase(
    strategy_source: object,
    current_base_snapshot: object,
    target_base_snapshot: object,
    *,
    current_base_resolution_snapshot: object = None,
    target_base_resolution_snapshot: object = None,
    module_preset_definitions: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Compute the complete semantic review for an explicit Base rebase."""

    source = normalize_strategy_source(strategy_source)
    current_ref = source.get("base")
    current: Optional[dict[str, Any]] = None
    if current_ref is not None:
        current = normalize_base_source(current_base_snapshot)
        if (
            current["id"] != current_ref["id"]
            or current["revision"] != current_ref["revision"]
        ):
            raise StrategyAuthoringError(
                "current base snapshot does not match the pinned revision"
            )
    elif current_base_snapshot is not None:
        raise StrategyAuthoringError(
            "a strategy without a pinned base cannot receive a current snapshot"
        )

    target = normalize_base_source(target_base_snapshot)
    if target["family"] != source["family"]:
        raise StrategyAuthoringError(
            f"base family {target['family']!r} is incompatible with "
            f"strategy family {source['family']!r}"
        )
    if current_ref is not None:
        if target["id"] != current_ref["id"]:
            raise StrategyAuthoringError(
                "a rebase must keep the pinned base id unchanged"
            )
        if target["revision"] <= current_ref["revision"]:
            raise StrategyAuthoringError(
                "the reviewed base revision must be newer than the pinned revision"
            )

    current_analysis = analyze_strategy_source(
        source,
        current,
        base_resolution_snapshot=current_base_resolution_snapshot,
        require_base_definition_snapshots=(
            current_base_resolution_snapshot is not None
        ),
        module_preset_definitions=module_preset_definitions,
    )
    rebased_source = copy.deepcopy(source)
    rebased_source["base"] = {
        "id": target["id"],
        "revision": target["revision"],
    }
    target_analysis = analyze_strategy_source(
        rebased_source,
        target,
        base_resolution_snapshot=target_base_resolution_snapshot,
        require_base_definition_snapshots=(
            target_base_resolution_snapshot is not None
        ),
        module_preset_definitions=module_preset_definitions,
    )
    current_resolution = current_analysis["resolution"]
    target_resolution = target_analysis["resolution"]

    base_changes = _diff_setting_mappings(
        current["settings"] if current is not None else {},
        target["settings"],
    )
    inherited_changes = []
    local_overrides = []
    explicit_ignores = []
    local_settings = source["settings"]
    for setting_id, definition in FARM_SETTING_REGISTRY.items():
        local = local_settings.get(setting_id)
        before_entry = current_resolution["settings"][setting_id]
        after_entry = target_resolution["settings"][setting_id]
        if local is None:
            if _effective_resolution_view(before_entry) != _effective_resolution_view(
                after_entry
            ):
                inherited_changes.append(
                    {
                        "setting_id": setting_id,
                        "display_name": definition.display_name,
                        "before": copy.deepcopy(before_entry),
                        "after": copy.deepcopy(after_entry),
                    }
                )
            continue
        if local["policy"] == "ignore":
            explicit_ignores.append(
                {
                    "setting_id": setting_id,
                    "display_name": definition.display_name,
                    "directive": copy.deepcopy(local),
                    "result": copy.deepcopy(after_entry),
                }
            )
            continue
        local_overrides.append(
            {
                "setting_id": setting_id,
                "display_name": definition.display_name,
                "directive": copy.deepcopy(local),
                "before": copy.deepcopy(before_entry),
                "after": copy.deepcopy(after_entry),
            }
        )

    errors = copy.deepcopy(target_analysis["errors"])
    return {
        "source": rebased_source,
        "current_resolution": current_resolution,
        "resolution": target_resolution,
        "base_changes": base_changes,
        "inherited_effective_changes": inherited_changes,
        "local_overrides_unchanged": local_overrides,
        "explicit_ignores_unchanged": explicit_ignores,
        "validation_errors": errors,
        "review_fingerprint": rebase_review_fingerprint(rebased_source),
        "summary": {
            "base_added": len(base_changes["added"]),
            "base_removed": len(base_changes["removed"]),
            "base_changed": len(base_changes["changed"]),
            "inherited_effective_changed": len(inherited_changes),
            "local_overrides_unchanged": len(local_overrides),
            "explicit_ignores_unchanged": len(explicit_ignores),
            "validation_error_count": len(errors),
        },
    }


def rebase_review_fingerprint(strategy_source: object) -> str:
    """Bind reviewed rebase approval to the complete proposed sparse source."""

    source = normalize_strategy_source(strategy_source)
    source.pop("version", None)
    return fingerprint_document(
        {
            "kind": "strategy_rebase_review",
            "source": source,
        }
    )


def legacy_farm_source_to_strategy_source(
    source: object,
    *,
    display_name: object = None,
) -> dict[str, Any]:
    """Conservatively convert a compact Farm source to explicit local intent."""

    if not isinstance(source, Mapping):
        raise StrategyAuthoringError("legacy Farm source must be an object")
    meta = source.get("meta")
    if not isinstance(meta, Mapping):
        raise StrategyAuthoringError("legacy Farm source requires meta")
    if source.get("builder") != "farm" or source.get("run_profile") != "farm":
        raise StrategyAuthoringError("legacy source is not a compact Farm profile")
    baseline = _farm_baseline_settings()
    raw_setup = source.get("setup") or {}
    if not isinstance(raw_setup, Mapping):
        raise StrategyAuthoringError("legacy Farm setup must be an object")
    raw_setup_settings = raw_setup.get("settings") or {}
    if not isinstance(raw_setup_settings, Mapping):
        raise StrategyAuthoringError("legacy Farm setup.settings must be an object")
    baseline.update(copy.deepcopy(dict(raw_setup_settings)))
    try:
        skipped = normalize_profile_skip_checks(raw_setup.get("skipped_checks"))
    except ValueError as exc:
        raise StrategyAuthoringError(f"legacy Farm setup {exc}") from exc

    settings: dict[str, dict[str, Any]] = {}
    for setting_id in FARM_SETUP_SETTING_IDS:
        definition = FARM_SETTING_REGISTRY[setting_id]
        try:
            value = definition.normalizer(baseline[setting_id])
        except (KeyError, TypeError, ValueError) as exc:
            raise StrategyAuthoringError(
                f"legacy setting {setting_id!r} is invalid: {exc}"
            ) from exc
        policy = "ignore" if setting_id in skipped else "enforce"
        directive: dict[str, Any] = {"policy": policy, "value": value}
        settings[setting_id] = directive

    loadout = source.get("loadout")
    if not isinstance(loadout, Mapping):
        raise StrategyAuthoringError("legacy Farm source requires loadout")
    for setting_id in FARM_LOADOUT_SETTING_IDS:
        raw_policy = loadout.get(setting_id)
        if not isinstance(raw_policy, Mapping):
            raise StrategyAuthoringError(
                f"legacy Farm loadout.{setting_id} must be an object"
            )
        mode = str(raw_policy.get("mode") or "").strip().lower()
        if mode == "preserve":
            settings[setting_id] = {"policy": "ignore"}
            continue
        if mode not in {"enforce", "observe"}:
            raise StrategyAuthoringError(
                f"legacy Farm loadout.{setting_id} has invalid mode {mode!r}"
            )
        raw_value: object
        if FARM_SETTING_REGISTRY[setting_id].adapter == "loadout_definition":
            raw_value = {"preset": raw_policy.get("preset")}
        else:
            raw_value = raw_policy.get("value")
        try:
            value = FARM_SETTING_REGISTRY[setting_id].normalizer(raw_value)
        except (TypeError, ValueError) as exc:
            raise StrategyAuthoringError(
                f"legacy Farm loadout.{setting_id} is invalid: {exc}"
            ) from exc
        settings[setting_id] = {"policy": mode, "value": value}

    return normalize_strategy_source(
        {
            "id": meta.get("name"),
            "display_name": display_name,
            "family": meta.get("family") or "farm",
            "tier": meta.get("tier"),
            "version": meta.get("version") or 1,
            "settings": settings,
        }
    )


def farm_source_from_resolution(
    strategy_source: object,
    resolution: object,
) -> dict[str, Any]:
    """Adapt resolved Farm intent into the existing protected builder contract."""

    source = normalize_strategy_source(strategy_source)
    if not isinstance(resolution, Mapping):
        raise StrategyAuthoringError("strategy resolution must be an object")
    resolved_settings = resolution.get("settings")
    if not isinstance(resolved_settings, Mapping):
        raise StrategyAuthoringError("strategy resolution requires settings")

    setup_settings: dict[str, Any] = {}
    skipped: list[str] = []
    local_settings = source["settings"]
    for setting_id in FARM_SETUP_SETTING_IDS:
        entry = resolved_settings.get(setting_id)
        if not isinstance(entry, Mapping):
            raise StrategyAuthoringError(
                f"strategy resolution is missing {setting_id!r}"
            )
        if entry.get("state") == "effective":
            if entry.get("policy") != "enforce":
                raise StrategyAuthoringError(
                    f"Farm setup setting {setting_id!r} cannot translate policy "
                    f"{entry.get('policy')!r}"
                )
            setup_settings[setting_id] = copy.deepcopy(entry.get("value"))
            continue
        if setting_id not in PROFILE_SKIPPABLE_CHECKS:
            raise StrategyAuthoringError(
                f"Farm setting {setting_id!r} must resolve to an enforce directive"
            )
        skipped.append(setting_id)
        # The compact builder still requires a structurally complete setup.
        # A retained ignored value is carried only as dormant compatibility
        # data; profile_skips prevents the generated gate from consuming it.
        dormant = local_settings.get(setting_id, {}).get("value")
        setup_settings[setting_id] = copy.deepcopy(
            dormant
            if dormant is not None
            else _IGNORED_SETUP_PLACEHOLDERS[setting_id]
        )

    loadout: dict[str, dict[str, Any]] = {}
    for setting_id in FARM_LOADOUT_SETTING_IDS:
        entry = resolved_settings.get(setting_id)
        if not isinstance(entry, Mapping):
            raise StrategyAuthoringError(
                f"strategy resolution is missing {setting_id!r}"
            )
        if entry.get("state") != "effective":
            loadout[setting_id] = {"mode": "preserve"}
            continue
        policy = str(entry.get("policy") or "")
        if policy not in {"enforce", "observe"}:
            raise StrategyAuthoringError(
                f"Farm loadout setting {setting_id!r} has invalid policy {policy!r}"
            )
        value = entry.get("value")
        definition = FARM_SETTING_REGISTRY[setting_id]
        if definition.adapter == "loadout_definition":
            if not isinstance(value, Mapping):
                raise StrategyAuthoringError(
                    f"Farm loadout setting {setting_id!r} requires a definition selector"
                )
            snapshot = entry.get("definition_snapshot")
            if isinstance(snapshot, Mapping):
                if snapshot.get("source") == "preset":
                    preset_snapshot: dict[str, Any] = {
                        "preset": snapshot.get("preset"),
                        "definition": copy.deepcopy(snapshot.get("definition")),
                    }
                    if setting_id == "orb_distance":
                        preset_snapshot["range_relationships"] = copy.deepcopy(
                            snapshot.get("range_relationships")
                        )
                    loadout_entry = {
                        "mode": policy,
                        "preset_snapshot": preset_snapshot,
                    }
                else:
                    loadout_entry = {
                        "mode": policy,
                        "local": copy.deepcopy(snapshot.get("definition")),
                    }
                    if setting_id == "orb_distance":
                        loadout_entry["range_relationships"] = copy.deepcopy(
                            snapshot.get("range_relationships")
                        )
                loadout[setting_id] = loadout_entry
            else:
                # Exact schema-2 sources retain the preset-only compact facade.
                loadout[setting_id] = {
                    "mode": policy,
                    "preset": str(value.get("preset") or ""),
                }
        else:
            loadout[setting_id] = {"mode": policy, "value": copy.deepcopy(value)}

    return {
        "meta": {
            "name": source["id"],
            "family": source["family"],
            "tier": source["tier"],
            "version": source["version"],
        },
        "builder": "farm",
        "run_profile": "farm",
        "loadout": loadout,
        "setup": {
            "skipped_checks": [
                setting_id
                for setting_id in PROFILE_SKIPPABLE_CHECKS
                if setting_id in skipped
            ],
            "settings": setup_settings,
        },
    }


class StrategyBaseStore:
    """Publish and load immutable sparse base revisions from fixed names."""

    def __init__(
        self,
        base_directory: Path | str,
        *,
        module_preset_definitions_factory: Optional[
            Callable[[], Mapping[str, Any]]
        ] = None,
    ) -> None:
        expanded = Path(base_directory).expanduser()
        self.base_directory = Path(os.path.abspath(expanded))
        self._publish_lock = threading.Lock()
        self._module_preset_definitions_factory = (
            module_preset_definitions_factory
        )

    def publish(
        self,
        raw_base: object,
        *,
        expected_latest_fingerprint: object = None,
    ) -> dict[str, Any]:
        """Atomically create the next immutable revision."""

        if not isinstance(raw_base, Mapping):
            raise StrategyAuthoringError("base source must be an object")
        upgraded = upgrade_authoring_source_schema(raw_base)
        identifier = _safe_id(upgraded.get("id"), "base id")
        with self._publish_lock:
            self._prepare_directory()
            lock_path = self.base_directory / ".strategy-bases.write.lock"
            try:
                lock_handle = lock_path.open("a+", encoding="utf-8")
            except OSError as exc:
                raise StrategyAuthoringError(
                    f"Unable to lock the strategy base catalog: {exc}"
                ) from exc
            with lock_handle:
                try:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                except OSError as exc:
                    raise StrategyAuthoringError(
                        f"Unable to lock the strategy base catalog: {exc}"
                    ) from exc
                latest = self.latest(identifier)
                expected = str(expected_latest_fingerprint or "").strip() or None
                if latest is None:
                    if expected is not None:
                        raise StrategyAuthoringConflictError(
                            f"Base {identifier!r} no longer exists; reload the catalog"
                        )
                    revision = 1
                else:
                    current = str(latest["source_fingerprint"])
                    if expected != current:
                        raise StrategyAuthoringConflictError(
                            f"Base {identifier!r} changed after it was opened; "
                            "reload it before publishing"
                        )
                    revision = int(latest["snapshot"]["revision"]) + 1
                snapshot = normalize_base_source(upgraded, revision=revision)
                resolution = describe_base_resolution(
                    snapshot,
                    module_preset_definitions=(
                        self._module_preset_definitions_factory()
                        if self._module_preset_definitions_factory is not None
                        else None
                    ),
                )
                publication = {
                    "schema_version": BASE_PUBLICATION_SCHEMA_VERSION,
                    "kind": "strategy_base_revision",
                    "id": identifier,
                    "revision": revision,
                    "published_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                    "source_fingerprint": fingerprint_document(snapshot),
                    "resolution_fingerprint": fingerprint_document(resolution),
                    "snapshot": snapshot,
                    "resolution": resolution,
                }
                path = self._revision_path(identifier, revision)
                _atomic_create_immutable(
                    self.base_directory,
                    path,
                    publication,
                    description="strategy base",
                )
                return self.load(identifier, revision)

    def load(self, base_id: object, revision: object) -> dict[str, Any]:
        identifier = _safe_id(base_id, "base id")
        if isinstance(revision, bool):
            raise StrategyAuthoringError("base revision must be a positive integer")
        try:
            revision_number = int(revision)
        except (TypeError, ValueError) as exc:
            raise StrategyAuthoringError(
                "base revision must be a positive integer"
            ) from exc
        if revision_number < 1:
            raise StrategyAuthoringError("base revision must be a positive integer")
        path = self._revision_path(identifier, revision_number)
        if path.is_symlink():
            raise StrategyAuthoringError("symbolic-link base revisions are unsupported")
        if not path.is_file():
            raise StrategyAuthoringError(
                f"base revision {identifier}@{revision_number} is unavailable"
            )
        publication = _load_yaml_mapping_limited(path, "strategy base revision")
        return _validate_base_publication(
            publication,
            expected_id=identifier,
            expected_revision=revision_number,
        )

    def latest(self, base_id: object) -> Optional[dict[str, Any]]:
        identifier = _safe_id(base_id, "base id")
        if not self.base_directory.is_dir() or self.base_directory.is_symlink():
            return None
        revisions: list[int] = []
        pattern = re.compile(
            rf"{re.escape(identifier)}\.base\.([1-9][0-9]*)\.yaml"
        )
        for path in self.base_directory.glob(f"{identifier}.base.*.yaml"):
            if path.is_symlink():
                continue
            match = pattern.fullmatch(path.name)
            if match:
                revisions.append(int(match.group(1)))
        if not revisions:
            return None
        return self.load(identifier, max(revisions))

    def revisions(self, base_id: object) -> tuple[dict[str, Any], ...]:
        identifier = _safe_id(base_id, "base id")
        if not self.base_directory.is_dir() or self.base_directory.is_symlink():
            return ()
        pattern = re.compile(
            rf"{re.escape(identifier)}\.base\.([1-9][0-9]*)\.yaml"
        )
        revision_numbers = []
        for path in self.base_directory.glob(f"{identifier}.base.*.yaml"):
            if path.is_symlink():
                continue
            match = pattern.fullmatch(path.name)
            if match:
                revision_numbers.append(int(match.group(1)))
        return tuple(self.load(identifier, revision) for revision in sorted(revision_numbers))

    def catalog(self) -> dict[str, Any]:
        """Enumerate valid immutable revisions without following symlinks."""

        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        if not self.base_directory.exists():
            return {"items": items, "errors": errors}
        if self.base_directory.is_symlink() or not self.base_directory.is_dir():
            return {
                "items": items,
                "errors": [
                    {
                        "id": "bases",
                        "error": "strategy base catalog is not a regular directory",
                    }
                ],
            }

        pattern = re.compile(
            r"(?P<id>[a-z][a-z0-9_]{2,47})\.base\."
            r"(?P<revision>[1-9][0-9]*)\.yaml"
        )
        grouped: dict[str, list[tuple[int, Path]]] = {}
        try:
            paths = sorted(self.base_directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            return {
                "items": items,
                "errors": [{"id": "bases", "error": str(exc)}],
            }
        for path in paths:
            if ".base." not in path.name or not path.name.endswith(".yaml"):
                continue
            match = pattern.fullmatch(path.name)
            if match is None:
                errors.append(
                    {
                        "id": path.name,
                        "error": "invalid strategy base revision filename",
                    }
                )
                continue
            grouped.setdefault(match.group("id"), []).append(
                (int(match.group("revision")), path)
            )

        for identifier, candidates in sorted(grouped.items()):
            revisions: list[dict[str, Any]] = []
            publications: list[dict[str, Any]] = []
            for revision, path in sorted(candidates):
                try:
                    if path.is_symlink():
                        raise StrategyAuthoringError(
                            "symbolic-link base revisions are unsupported"
                        )
                    publication = self.load(identifier, revision)
                except StrategyAuthoringError as exc:
                    errors.append(
                        {
                            "id": f"{identifier}@{revision}",
                            "error": str(exc),
                        }
                    )
                    continue
                publications.append(publication)
                snapshot = publication["snapshot"]
                revisions.append(
                    {
                        "revision": revision,
                        "published_at": publication["published_at"],
                        "source_fingerprint": publication[
                            "source_fingerprint"
                        ],
                        "resolution_fingerprint": publication.get(
                            "resolution_fingerprint"
                        ),
                        "setting_count": len(snapshot["settings"]),
                    }
                )
            if not publications:
                continue
            latest = publications[-1]
            snapshot = latest["snapshot"]
            items.append(
                {
                    "id": identifier,
                    "display_name": snapshot["display_name"],
                    "family": snapshot["family"],
                    "built_in": False,
                    "editable": True,
                    "latest_revision": snapshot["revision"],
                    "published_at": latest["published_at"],
                    "source_fingerprint": latest["source_fingerprint"],
                    "resolution_fingerprint": latest.get(
                        "resolution_fingerprint"
                    ),
                    "source": copy.deepcopy(snapshot),
                    "resolution": base_publication_resolution(latest),
                    "revisions": revisions,
                }
            )
        return {"items": items, "errors": errors}

    def _prepare_directory(self) -> None:
        if self.base_directory.exists() and self.base_directory.is_symlink():
            raise StrategyAuthoringConflictError(
                "symbolic-link base directories are unsupported"
            )
        try:
            self.base_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StrategyAuthoringError(
                f"Unable to create strategy base catalog: {exc}"
            ) from exc

    def _revision_path(self, identifier: str, revision: int) -> Path:
        path = self.base_directory / f"{identifier}.base.{revision}.yaml"
        if path.parent != self.base_directory:
            raise StrategyAuthoringError("Invalid strategy base path")
        return path


def fingerprint_document(value: object) -> str:
    """Return the canonical fingerprint used by all authoring artifacts."""

    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalize_matching_sources(
    before_source: object,
    after_source: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(before_source, Mapping) or not isinstance(
        after_source, Mapping
    ):
        raise StrategyAuthoringError("source review requires two documents")
    before_kind = str(before_source.get("kind") or "").strip().lower()
    after_kind = str(after_source.get("kind") or "").strip().lower()
    if before_kind == after_kind == "base":
        before = normalize_base_source(before_source)
        after = normalize_base_source(after_source)
    elif before_kind == after_kind == "strategy":
        before = normalize_strategy_source(before_source)
        after = normalize_strategy_source(after_source)
    else:
        raise StrategyAuthoringError(
            "source review requires two Base documents or two Strategy documents"
        )
    if before["id"] != after["id"]:
        raise StrategyAuthoringError("source review cannot change the document id")
    return before, after


def _diff_setting_mappings(
    before_settings: Mapping[str, Any],
    after_settings: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for setting_id, definition in FARM_SETTING_REGISTRY.items():
        before = before_settings.get(setting_id)
        after = after_settings.get(setting_id)
        if before is None and after is None:
            continue
        item = {
            "setting_id": setting_id,
            "display_name": definition.display_name,
            "before": copy.deepcopy(before),
            "after": copy.deepcopy(after),
        }
        if before is None:
            added.append(item)
        elif after is None:
            removed.append(item)
        elif before != after:
            changed.append(item)
        else:
            unchanged.append(item)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
    }


def _resolution_settings(resolution: object) -> Mapping[str, Any]:
    if not isinstance(resolution, Mapping):
        raise StrategyAuthoringError("strategy resolution must be an object")
    settings = resolution.get("settings")
    if not isinstance(settings, Mapping):
        raise StrategyAuthoringError("strategy resolution requires settings")
    missing = [setting_id for setting_id in FARM_SETTING_REGISTRY if setting_id not in settings]
    if missing:
        raise StrategyAuthoringError(
            "strategy resolution is missing setting(s): " + ", ".join(missing)
        )
    return settings


def _effective_resolution_view(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(entry.get(key))
        for key in ("state", "policy", "value", "definition_snapshot")
        if key in entry
    }


def _normalize_settings(
    raw_settings: object,
    *,
    base: bool,
    schema_version: int,
) -> dict[str, Any]:
    if raw_settings is None:
        raw_settings = {}
    if not isinstance(raw_settings, Mapping):
        owner = "base" if base else "strategy"
        raise StrategyAuthoringError(f"{owner} settings must be an object")
    unknown = sorted(set(raw_settings) - set(FARM_SETTING_REGISTRY))
    if unknown:
        raise StrategyAuthoringError(
            "unknown setting ids: " + ", ".join(str(item) for item in unknown)
        )
    normalized: dict[str, Any] = {}
    for setting_id in FARM_SETTING_REGISTRY:
        if setting_id not in raw_settings:
            continue
        directive = raw_settings[setting_id]
        if not isinstance(directive, Mapping):
            raise StrategyAuthoringError(
                f"setting {setting_id!r} directive must be an object"
            )
        unknown_fields = sorted(set(directive) - {"policy", "value"})
        if unknown_fields:
            raise StrategyAuthoringError(
                f"setting {setting_id!r} directive has unsupported fields: "
                + ", ".join(str(item) for item in unknown_fields)
            )
        policy = str(directive.get("policy") or "").strip().lower()
        definition = FARM_SETTING_REGISTRY[setting_id]
        if base and policy == "ignore":
            raise StrategyAuthoringError(
                f"base setting {setting_id!r} cannot use ignore; omit it instead"
            )
        if policy not in definition.allowed_policies:
            allowed = ", ".join(definition.allowed_policies)
            raise StrategyAuthoringError(
                f"setting {setting_id!r} policy must be one of: {allowed}"
            )
        if policy != "ignore" and "value" not in directive:
            raise StrategyAuthoringError(
                f"setting {setting_id!r} {policy} policy requires a value"
            )
        entry: dict[str, Any] = {"policy": policy}
        if "value" in directive:
            try:
                entry["value"] = definition.normalizer(directive["value"])
            except (TypeError, ValueError) as exc:
                raise StrategyAuthoringError(
                    f"setting {setting_id!r} has invalid value: {exc}"
                ) from exc
            if (
                schema_version == LEGACY_AUTHORING_SCHEMA_VERSION
                and definition.adapter == "loadout_definition"
                and "local" in entry["value"]
            ):
                raise StrategyAuthoringError(
                    f"setting {setting_id!r} local definitions require "
                    f"authoring schema {AUTHORING_SCHEMA_VERSION}"
                )
        normalized[setting_id] = entry
    return normalized


def _authoring_schema_version(value: object) -> int:
    if value is None:
        return AUTHORING_SCHEMA_VERSION
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return -1


def _validate_base_publication(
    publication: Mapping[str, Any],
    *,
    expected_id: str,
    expected_revision: int,
) -> dict[str, Any]:
    if publication.get("schema_version") != BASE_PUBLICATION_SCHEMA_VERSION:
        raise StrategyAuthoringError("Unsupported base publication schema")
    if publication.get("kind") != "strategy_base_revision":
        raise StrategyAuthoringError("Stored base revision has the wrong kind")
    if publication.get("id") != expected_id or publication.get("revision") != expected_revision:
        raise StrategyAuthoringError("Stored base revision does not match its filename")
    snapshot = normalize_base_source(publication.get("snapshot"))
    if snapshot["id"] != expected_id or snapshot["revision"] != expected_revision:
        raise StrategyAuthoringError("Stored base snapshot identity is invalid")
    source_fingerprint = str(publication.get("source_fingerprint") or "")
    if source_fingerprint != fingerprint_document(snapshot):
        raise StrategyAuthoringError("Base source fingerprint does not match")
    published_at = str(publication.get("published_at") or "").strip()
    if not published_at:
        raise StrategyAuthoringError("Base publication metadata is incomplete")
    if snapshot["schema_version"] == AUTHORING_SCHEMA_VERSION:
        stored_resolution = publication.get("resolution")
        if not isinstance(stored_resolution, Mapping):
            raise StrategyAuthoringError(
                "Base publication lacks its immutable definition resolution"
            )
        resolution = describe_base_resolution(snapshot, stored_resolution)
        if resolution != stored_resolution:
            raise StrategyAuthoringError(
                "Base definition resolution is not derived from its snapshot"
            )
        if publication.get("resolution_fingerprint") != fingerprint_document(
            resolution
        ):
            raise StrategyAuthoringError(
                "Base resolution fingerprint does not match"
            )
    elif "resolution" in publication or "resolution_fingerprint" in publication:
        raise StrategyAuthoringError(
            "Legacy Base publication has unexpected definition-resolution data"
        )
    return copy.deepcopy(dict(publication))


def base_publication_resolution(publication: Mapping[str, Any]) -> dict[str, Any]:
    """Return retained Base resolution, or the exact legacy projection."""

    stored = publication.get("resolution")
    if isinstance(stored, Mapping):
        return copy.deepcopy(dict(stored))
    return describe_base_resolution(publication.get("snapshot"))


def _atomic_create_immutable(
    directory: Path,
    path: Path,
    publication: Mapping[str, Any],
    *,
    description: str,
) -> None:
    temp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            yaml.safe_dump(
                dict(publication),
                handle,
                sort_keys=False,
                allow_unicode=True,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        try:
            os.link(temp_name, path)
        except FileExistsError as exc:
            raise StrategyAuthoringConflictError(
                f"Immutable {description} revision already exists: {path.name}"
            ) from exc
        Path(temp_name).unlink()
        temp_name = None
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except StrategyAuthoringError:
        raise
    except OSError as exc:
        raise StrategyAuthoringError(
            f"Unable to publish {description} {path.name}: {exc}"
        ) from exc
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def _safe_id(value: object, description: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SAFE_ID_RE.fullmatch(normalized) or str(value or "").strip() != normalized:
        raise StrategyAuthoringError(
            f"{description} must use 3-48 lowercase letters, digits, or "
            "underscores and start with a letter"
        )
    return normalized


def _display_name(value: object, identifier: str) -> str:
    display_name = str(value or "").strip() or " ".join(
        part.capitalize() for part in identifier.split("_")
    )
    if len(display_name) > 80:
        raise StrategyAuthoringError("display_name must be at most 80 characters")
    return display_name


def _farm_baseline_settings() -> dict[str, Any]:
    profile = _load_yaml_mapping(FARM_RUN_PROFILE_PATH, "Farm run profile")
    invariants = profile.get("invariants")
    if not isinstance(invariants, Mapping):
        raise StrategyAuthoringError("Farm run profile invariants must be an object")
    return copy.deepcopy(dict(invariants))


def _load_yaml_mapping(path: Path, description: str) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise StrategyAuthoringError(f"Unable to read {description}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise StrategyAuthoringError(f"{description} must be an object")
    return loaded


def _load_yaml_mapping_limited(path: Path, description: str) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_AUTHORING_FILE_BYTES:
            raise StrategyAuthoringError(
                f"{description} exceeds {MAX_AUTHORING_FILE_BYTES} bytes"
            )
        return _load_yaml_mapping(path, description)
    except OSError as exc:
        raise StrategyAuthoringError(f"Unable to read {description}: {exc}") from exc


__all__ = [
    "AUTHORING_POLICIES",
    "AUTHORING_SCHEMA_VERSION",
    "LEGACY_AUTHORING_SCHEMA_VERSION",
    "LOADOUT_DEFINITION_SCHEMA_VERSION",
    "EDITOR_METADATA_SCHEMA_VERSION",
    "FARM_LOADOUT_SETTING_IDS",
    "FARM_SETTING_REGISTRY",
    "FARM_SETUP_SETTING_IDS",
    "SettingDefinition",
    "StrategyAuthoringConflictError",
    "StrategyAuthoringError",
    "StrategyBaseStore",
    "analyze_strategy_source",
    "base_publication_resolution",
    "describe_base_resolution",
    "diff_source_documents",
    "diff_strategy_resolutions",
    "farm_source_from_resolution",
    "fingerprint_document",
    "legacy_farm_source_to_strategy_source",
    "materialize_loadout_preset",
    "normalize_base_source",
    "normalize_strategy_source",
    "preview_strategy_rebase",
    "rebase_review_fingerprint",
    "resolve_strategy_source",
    "setting_registry_catalog",
    "upgrade_authoring_source_schema",
]
