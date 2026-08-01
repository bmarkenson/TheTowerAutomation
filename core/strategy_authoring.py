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

from core.card_recharge_modes import normalize_card_recharge_modes
from core.damage_adjuster import normalize_damage_percentage
from core.free_upgrade_locks import normalize_free_upgrade_lock_requirements
from core.gate_decisions import PROFILE_SKIPPABLE_CHECKS, normalize_profile_skip_checks
from core.perk_configuration import normalize_perk_configuration_requirements


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FARM_RUN_PROFILE_PATH = PROJECT_ROOT / "config" / "run_profiles" / "farm.yaml"
MODULE_PRESETS_PATH = PROJECT_ROOT / "config" / "loadouts" / "modules.yaml"
ORB_DISTANCE_PRESETS_PATH = (
    PROJECT_ROOT / "config" / "loadouts" / "orb_distances.yaml"
)
TARGET_PRIORITY_PRESETS_PATH = (
    PROJECT_ROOT / "config" / "loadouts" / "target_priorities.yaml"
)

AUTHORING_SCHEMA_VERSION = 2
BASE_PUBLICATION_SCHEMA_VERSION = 1
MAX_AUTHORING_FILE_BYTES = 4 * 1024 * 1024
AUTHORING_POLICIES = ("enforce", "observe", "ignore")
_SAFE_ID_RE = re.compile(r"[a-z][a-z0-9_]{2,47}")


class StrategyAuthoringError(ValueError):
    """Raised when an authoring source or stored revision is invalid."""


class StrategyAuthoringConflictError(StrategyAuthoringError):
    """Raised when optimistic publication state is stale or immutable."""


Normalizer = Callable[[object], Any]


@dataclass(frozen=True)
class SettingDefinition:
    """Immutable metadata and validation for one stable authoring setting."""

    id: str
    display_name: str
    section: str
    editor_type: str
    allowed_policies: tuple[str, ...]
    normalizer: Normalizer
    dependencies: tuple[str, ...]
    runtime_destination: str
    adapter: str
    observation_supported: bool
    repair_supported: bool

    def catalog_item(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "section": self.section,
            "editor_type": self.editor_type,
            "allowed_policies": list(self.allowed_policies),
            "dependencies": list(self.dependencies),
            "runtime_destination": self.runtime_destination,
            "observation_supported": self.observation_supported,
            "repair_supported": self.repair_supported,
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


def _preset_value(setting_id: str, path: Path) -> Normalizer:
    def normalize(value: object) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{setting_id} must be an object containing preset")
        unknown = sorted(set(value) - {"preset"})
        if unknown:
            raise ValueError(
                f"{setting_id} has unsupported fields: "
                + ", ".join(str(item) for item in unknown)
            )
        preset = str(value.get("preset") or "").strip()
        catalog = _load_yaml_mapping(path, f"{setting_id} preset catalog")
        presets = catalog.get("presets")
        if not isinstance(presets, Mapping) or preset not in presets:
            raise ValueError(f"unknown {setting_id} preset {preset!r}")
        return {"preset": preset}

    return normalize


def _damage_slider(value: object) -> str:
    return normalize_damage_percentage(value)


def _definition(
    setting_id: str,
    display_name: str,
    section: str,
    editor_type: str,
    allowed_policies: tuple[str, ...],
    normalizer: Normalizer,
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
        "session_preflight.requirements.ultimate_weapons",
        "setup_setting",
    ),
    _definition(
        "modules",
        "Modules",
        "Loadout",
        "preset",
        _LOADOUT_POLICIES,
        _preset_value("modules", MODULE_PRESETS_PATH),
        "run_configuration.loadout.modules",
        "loadout_preset",
    ),
    _definition(
        "damage_slider",
        "Damage Slider",
        "Loadout",
        "damage_percentage",
        _LOADOUT_POLICIES,
        _damage_slider,
        "run_configuration.loadout.damage_slider",
        "loadout_value",
    ),
    _definition(
        "orb_distance",
        "Orb Distance",
        "Loadout",
        "preset",
        _LOADOUT_POLICIES,
        _preset_value("orb_distance", ORB_DISTANCE_PRESETS_PATH),
        "run_configuration.loadout.orb_distance",
        "loadout_preset",
    ),
    _definition(
        "target_priority",
        "Target Priority",
        "Loadout",
        "preset",
        _LOADOUT_POLICIES,
        _preset_value("target_priority", TARGET_PRIORITY_PRESETS_PATH),
        "run_configuration.loadout.target_priority",
        "loadout_preset",
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


def setting_registry_catalog() -> list[dict[str, Any]]:
    """Return the serializable, behavior-free view of the Farm registry."""

    return [definition.catalog_item() for definition in _SETTING_DEFINITIONS]


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
    if raw_source.get("schema_version", AUTHORING_SCHEMA_VERSION) != AUTHORING_SCHEMA_VERSION:
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
    settings = _normalize_settings(raw_source.get("settings"), base=True)
    return {
        "schema_version": AUTHORING_SCHEMA_VERSION,
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
    if raw_source.get("schema_version", AUTHORING_SCHEMA_VERSION) != AUTHORING_SCHEMA_VERSION:
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
        "schema_version": AUTHORING_SCHEMA_VERSION,
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
        raw_source.get("settings"), base=False
    )
    return normalized


def resolve_strategy_source(
    strategy_source: object,
    base_snapshot: object = None,
) -> dict[str, Any]:
    """Resolve one sparse source against zero or one exact base snapshot."""

    analysis = analyze_strategy_source(strategy_source, base_snapshot)
    errors = analysis["errors"]
    if errors:
        raise StrategyAuthoringError(str(errors[0]["message"]))
    return copy.deepcopy(analysis["resolution"])


def analyze_strategy_source(
    strategy_source: object,
    base_snapshot: object = None,
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
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "family": source["family"],
            "settings": resolved,
        },
        "errors": errors,
    }


def describe_base_resolution(base_source: object) -> dict[str, Any]:
    """Project sparse Base entries through the authoritative resolver.

    A Base is not runnable and may remain incomplete, so dependency errors do
    not make this display projection invalid.  The synthetic empty Strategy is
    used only to obtain the same effective-setting and provenance vocabulary
    that a pinned Strategy receives.
    """

    base = normalize_base_source(base_source)
    analysis = analyze_strategy_source(
        {
            "schema_version": AUTHORING_SCHEMA_VERSION,
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

    current_analysis = analyze_strategy_source(source, current)
    rebased_source = copy.deepcopy(source)
    rebased_source["base"] = {
        "id": target["id"],
        "revision": target["revision"],
    }
    target_analysis = analyze_strategy_source(rebased_source, target)
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
        if FARM_SETTING_REGISTRY[setting_id].adapter == "loadout_preset":
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
        if definition.adapter == "loadout_preset":
            if not isinstance(value, Mapping):
                raise StrategyAuthoringError(
                    f"Farm loadout setting {setting_id!r} requires a preset"
                )
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

    def __init__(self, base_directory: Path | str) -> None:
        expanded = Path(base_directory).expanduser()
        self.base_directory = Path(os.path.abspath(expanded))
        self._publish_lock = threading.Lock()

    def publish(
        self,
        raw_base: object,
        *,
        expected_latest_fingerprint: object = None,
    ) -> dict[str, Any]:
        """Atomically create the next immutable revision."""

        if not isinstance(raw_base, Mapping):
            raise StrategyAuthoringError("base source must be an object")
        identifier = _safe_id(raw_base.get("id"), "base id")
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
                snapshot = normalize_base_source(raw_base, revision=revision)
                publication = {
                    "schema_version": BASE_PUBLICATION_SCHEMA_VERSION,
                    "kind": "strategy_base_revision",
                    "id": identifier,
                    "revision": revision,
                    "published_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                    "source_fingerprint": fingerprint_document(snapshot),
                    "snapshot": snapshot,
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
                    "source": copy.deepcopy(snapshot),
                    "resolution": describe_base_resolution(snapshot),
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
        for key in ("state", "policy", "value")
        if key in entry
    }


def _normalize_settings(raw_settings: object, *, base: bool) -> dict[str, Any]:
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
        normalized[setting_id] = entry
    return normalized


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
    return copy.deepcopy(dict(publication))


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
    "FARM_LOADOUT_SETTING_IDS",
    "FARM_SETTING_REGISTRY",
    "FARM_SETUP_SETTING_IDS",
    "SettingDefinition",
    "StrategyAuthoringConflictError",
    "StrategyAuthoringError",
    "StrategyBaseStore",
    "analyze_strategy_source",
    "describe_base_resolution",
    "diff_source_documents",
    "diff_strategy_resolutions",
    "farm_source_from_resolution",
    "fingerprint_document",
    "legacy_farm_source_to_strategy_source",
    "normalize_base_source",
    "normalize_strategy_source",
    "preview_strategy_rebase",
    "rebase_review_fingerprint",
    "resolve_strategy_source",
    "setting_registry_catalog",
]
