"""Resolve compact Farm profiles into explicit builder input."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[2]
FARM_PROFILE_PATH = ROOT / "config" / "run_profiles" / "farm.yaml"
MODULE_PRESETS_PATH = ROOT / "config" / "loadouts" / "modules.yaml"
TARGET_PRIORITY_PRESETS_PATH = (
    ROOT / "config" / "loadouts" / "target_priorities.yaml"
)
POLICY_MODES = frozenset({"enforce", "observe", "preserve"})
LOADOUT_KEYS = frozenset({"modules", "damage_slider", "target_priority"})


def resolve_farm_source(source: Mapping[str, Any]) -> dict[str, Any]:
    """Expand one Farm source while keeping runtime plans self-contained."""

    if source.get("run_profile") != "farm":
        raise ValueError("farm builder requires run_profile: farm")
    for legacy_key in ("initialization", "session_preflight"):
        if legacy_key in source:
            raise ValueError(
                f"farm profiles derive {legacy_key} from the Farm baseline/loadout"
            )

    meta = copy.deepcopy(source.get("meta") or {})
    profile_name = str(meta.get("name") or "").strip()
    if not profile_name:
        raise ValueError("farm profile requires meta.name")
    family = str(meta.get("family") or "farm").strip().lower()
    if family != "farm":
        raise ValueError("farm profile meta.family must be farm")
    meta["family"] = "farm"
    try:
        tier = int(meta["tier"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("farm profile requires an integer meta.tier") from exc
    if tier < 1:
        raise ValueError("farm profile meta.tier must be positive")
    meta["tier"] = tier

    profile = _load_mapping(FARM_PROFILE_PATH, "Farm run profile")
    if profile.get("name") != "farm":
        raise ValueError("Farm run profile must declare name: farm")
    invariants = copy.deepcopy(profile.get("invariants"))
    if not isinstance(invariants, dict):
        raise ValueError("Farm run profile invariants must be a mapping")

    loadout = source.get("loadout")
    if not isinstance(loadout, Mapping):
        raise ValueError("farm profile loadout must be a mapping")
    missing = sorted(LOADOUT_KEYS - set(loadout))
    extra = sorted(set(loadout) - LOADOUT_KEYS)
    if missing or extra:
        raise ValueError(
            "farm profile loadout must define exactly modules, damage_slider, "
            f"and target_priority (missing={missing}, extra={extra})"
        )

    module_policy = _resolve_preset_policy(
        "modules",
        loadout["modules"],
        MODULE_PRESETS_PATH,
    )
    target_policy = _resolve_preset_policy(
        "target_priority",
        loadout["target_priority"],
        TARGET_PRIORITY_PRESETS_PATH,
    )
    damage_policy = _resolve_damage_slider_policy(loadout["damage_slider"])

    requirements = invariants
    gate_fallbacks = _normalize_gate_fallbacks(
        profile.get("gate_fallbacks"),
        supported_checks=set(requirements) | {"modules"},
    )
    requirements["loadout_policies"] = {
        "modules": module_policy["mode"],
    }
    if module_policy["mode"] != "preserve":
        requirements["modules"] = copy.deepcopy(module_policy["resolved"])

    target_priority: dict[str, Any] = {"mode": target_policy["mode"]}
    if target_policy["mode"] != "preserve":
        target_priority["order"] = copy.deepcopy(target_policy["resolved"])

    run_configuration = {
        "schema_version": 2,
        "profile": "farm",
        "profile_version": int(profile.get("schema_version") or 1),
        "tier": tier,
        "settings": {
            key: copy.deepcopy(requirements[key])
            for key in (
                "cards_deck",
                "workshop_preset",
                "free_upgrade_locks",
                "bots_preset",
                "guardian_chips",
                "auto_pick_perks",
                "ultimate_weapons",
            )
        },
        "loadout": {
            "modules": module_policy,
            "damage_slider": damage_policy,
            "target_priority": target_policy,
        },
        "gate_fallbacks": copy.deepcopy(gate_fallbacks),
    }

    return {
        "meta": meta,
        "builder": "gc_farm",
        "initialization": {
            "damage_slider": copy.deepcopy(damage_policy),
            "target_priority": target_priority,
        },
        "session_preflight": requirements,
        "gate_fallbacks": gate_fallbacks,
        "run_configuration": run_configuration,
    }


def _normalize_gate_fallbacks(
    raw: Any,
    *,
    supported_checks: set[str],
) -> dict[str, list[dict[str, str]]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("Farm gate_fallbacks must be a mapping")
    unknown = sorted(str(key) for key in set(raw) - supported_checks)
    if unknown:
        raise ValueError(
            "Farm gate_fallbacks has unsupported checks: " + ", ".join(unknown)
        )
    normalized: dict[str, list[dict[str, str]]] = {}
    for check_id, configured in raw.items():
        if not isinstance(configured, list) or not configured:
            raise ValueError(f"Farm gate_fallbacks.{check_id} must be a non-empty list")
        choices: list[dict[str, str]] = []
        seen: set[str] = set()
        for option in configured:
            if not isinstance(option, Mapping):
                raise ValueError(
                    f"Farm gate_fallbacks.{check_id} choices must be mappings"
                )
            option_id = str(option.get("id") or "").strip().lower()
            label = str(option.get("label") or "").strip()
            if not option_id or not label or option_id in seen:
                raise ValueError(
                    f"Farm gate_fallbacks.{check_id} choices need unique ids and labels"
                )
            choice = {"id": option_id, "label": label}
            for key in ("value", "description"):
                value = str(option.get(key) or "").strip()
                if value:
                    choice[key] = value
            choices.append(choice)
            seen.add(option_id)
        normalized[str(check_id)] = choices
    return normalized


def _resolve_preset_policy(
    setting: str,
    raw: Any,
    catalog_path: Path,
) -> dict[str, Any]:
    policy = _normalize_policy(setting, raw)
    mode = policy["mode"]
    preset = str(policy.get("preset") or "").strip()
    if mode == "preserve":
        if preset:
            raise ValueError(
                f"farm loadout {setting} preserve mode must not supply a preset"
            )
        return {"mode": mode}
    if not preset:
        raise ValueError(
            f"farm loadout {setting} {mode} mode requires a preset"
        )
    catalog = _load_mapping(catalog_path, f"{setting} preset catalog")
    presets = catalog.get("presets")
    if not isinstance(presets, Mapping) or preset not in presets:
        raise ValueError(f"unknown {setting} preset {preset!r}")
    resolved = copy.deepcopy(presets[preset])
    if not isinstance(resolved, (dict, list)):
        raise ValueError(f"{setting} preset {preset!r} has invalid data")
    return {"mode": mode, "preset": preset, "resolved": resolved}


def _resolve_damage_slider_policy(raw: Any) -> dict[str, Any]:
    policy = _normalize_policy("damage_slider", raw)
    mode = policy["mode"]
    if mode == "preserve":
        if "value" in policy:
            raise ValueError(
                "farm loadout damage_slider preserve mode must not supply a value"
            )
        return {"mode": "preserve"}
    if "value" not in policy:
        raise ValueError(
            f"farm loadout damage_slider {mode} mode requires a value"
        )
    from core.damage_adjuster import normalize_damage_percentage

    try:
        value = normalize_damage_percentage(policy["value"])
    except ValueError as exc:
        raise ValueError(f"farm loadout damage_slider {exc}") from exc
    return {"mode": mode, "value": value}


def _normalize_policy(setting: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"farm loadout {setting} must be a mapping")
    policy = copy.deepcopy(dict(raw))
    mode = str(policy.get("mode") or "").strip().lower()
    if mode not in POLICY_MODES:
        raise ValueError(
            f"farm loadout {setting}.mode must be enforce, observe, or preserve"
        )
    policy["mode"] = mode
    return policy


def _load_mapping(path: Path, description: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ValueError(f"unable to read {description}: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{description} must be a mapping")
    return data


__all__ = ["resolve_farm_source"]
