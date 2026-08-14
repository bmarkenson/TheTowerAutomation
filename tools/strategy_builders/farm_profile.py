"""Resolve compact Farm profiles into explicit builder input."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import yaml

from core.gate_decisions import normalize_profile_skip_checks
from core.perk_configuration import (
    normalize_perk_configuration_requirements,
    normalize_perk_first_choice_requirement,
)
from core.player_save_preflight import normalize_player_save_preflight_mode


ROOT = Path(__file__).resolve().parents[2]
FARM_PROFILE_PATH = ROOT / "config" / "run_profiles" / "farm.yaml"
MODULE_PRESETS_PATH = ROOT / "config" / "loadouts" / "modules.yaml"
TARGET_PRIORITY_PRESETS_PATH = (
    ROOT / "config" / "loadouts" / "target_priorities.yaml"
)
ORB_DISTANCE_PRESETS_PATH = (
    ROOT / "config" / "loadouts" / "orb_distances.yaml"
)
POLICY_MODES = frozenset({"enforce", "observe", "preserve"})
PLAYER_SAVE_PREFLIGHT_POLICY = "save_first"
LOADOUT_KEYS = frozenset(
    {"modules", "damage_slider", "orb_distance", "target_priority"}
)
SETUP_KEYS = frozenset({"skipped_checks", "settings"})


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
    raw_runtime_policy = source.get("runtime_policy") or {}
    if not isinstance(raw_runtime_policy, Mapping):
        raise ValueError("farm profile runtime_policy must be a mapping")
    unknown_runtime_policy = sorted(
        set(raw_runtime_policy) - {"player_save_preflight"}
    )
    if unknown_runtime_policy:
        raise ValueError(
            "farm profile runtime_policy has unsupported settings: "
            + ", ".join(str(value) for value in unknown_runtime_policy)
        )
    player_save_preflight_policy = normalize_player_save_preflight_mode(
        raw_runtime_policy.get(
            "player_save_preflight",
            PLAYER_SAVE_PREFLIGHT_POLICY,
        )
    )

    loadout = source.get("loadout")
    if not isinstance(loadout, Mapping):
        raise ValueError("farm profile loadout must be a mapping")
    missing = sorted(LOADOUT_KEYS - set(loadout))
    extra = sorted(set(loadout) - LOADOUT_KEYS)
    if missing or extra:
        raise ValueError(
            "farm profile loadout must define exactly modules, damage_slider, "
            f"orb_distance, and target_priority (missing={missing}, "
            f"extra={extra})"
        )

    module_policy = _resolve_definition_policy(
        "modules",
        loadout["modules"],
        MODULE_PRESETS_PATH,
    )
    target_policy = _resolve_definition_policy(
        "target_priority",
        loadout["target_priority"],
        TARGET_PRIORITY_PRESETS_PATH,
    )
    damage_policy = _resolve_damage_slider_policy(loadout["damage_slider"])
    orb_distance_policy = _resolve_orb_distance_policy(
        loadout["orb_distance"]
    )

    setup = _resolve_setup(source.get("setup"), invariants)
    requirements = copy.deepcopy(setup["settings"])
    if setup["skipped_checks"]:
        requirements["profile_skips"] = copy.deepcopy(
            setup["skipped_checks"]
        )
    if damage_policy["mode"] != "preserve":
        requirements["damage_slider"] = copy.deepcopy(damage_policy)
    if orb_distance_policy["mode"] != "preserve":
        requirements["orb_distance"] = {
            key: copy.deepcopy(value)
            for key, value in orb_distance_policy.items()
            if key != "preset"
        }
    gate_fallbacks = _normalize_gate_fallbacks(
        profile.get("gate_fallbacks"),
        supported_checks=set(requirements) | {"modules", "target_priority"},
    )
    session_recovery = copy.deepcopy(
        profile.get("session_preflight_recovery")
    )
    if not isinstance(session_recovery, dict):
        raise ValueError(
            "Farm run profile session_preflight_recovery must be a mapping"
        )
    requirements["loadout_policies"] = {
        "modules": module_policy["mode"],
        "target_priority": target_policy["mode"],
    }
    if module_policy["mode"] != "preserve":
        requirements["modules"] = copy.deepcopy(module_policy["resolved"])
    if target_policy["mode"] != "preserve":
        requirements["target_priority"] = copy.deepcopy(
            target_policy["resolved"]
        )

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
                "card_recharge_modes",
                "workshop_preset",
                "free_upgrade_locks",
                "bots_preset",
                "guardian_chips",
                "auto_pick_perks",
                "perk_first_choice",
                "perk_bans",
                "perk_auto_pick_order",
                "ultimate_weapons",
            )
        },
        "loadout": {
            "modules": module_policy,
            "damage_slider": damage_policy,
            "orb_distance": orb_distance_policy,
            "target_priority": target_policy,
        },
        "gate_fallbacks": copy.deepcopy(gate_fallbacks),
        "session_preflight_recovery": copy.deepcopy(session_recovery),
    }
    if setup["skipped_checks"]:
        run_configuration["skipped_checks"] = copy.deepcopy(
            setup["skipped_checks"]
        )

    return {
        "meta": meta,
        "builder": "gc_farm",
        "runtime_policy": {
            "player_save_preflight": player_save_preflight_policy,
        },
        "initialization": {
            "damage_slider": copy.deepcopy(damage_policy),
            "orb_distance": copy.deepcopy(orb_distance_policy),
            "target_priority": target_priority,
        },
        "session_preflight": requirements,
        "session_preflight_recovery": session_recovery,
        "gate_fallbacks": gate_fallbacks,
        "run_configuration": run_configuration,
    }


def _resolve_setup(
    raw: Any,
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve profile-owned persistent setup over the Farm baseline."""

    if raw is None:
        configured: dict[str, Any] = {}
    elif isinstance(raw, Mapping):
        configured = copy.deepcopy(dict(raw))
    else:
        raise ValueError("farm profile setup must be a mapping")
    unknown = sorted(set(configured) - SETUP_KEYS)
    if unknown:
        raise ValueError(
            "farm profile setup has unsupported settings: "
            + ", ".join(str(key) for key in unknown)
        )
    raw_settings = configured.get("settings")
    if raw_settings is None:
        raw_settings = {}
    if not isinstance(raw_settings, Mapping):
        raise ValueError("farm profile setup.settings must be a mapping")
    unknown_settings = sorted(set(raw_settings) - set(baseline))
    if unknown_settings:
        raise ValueError(
            "farm profile setup.settings has unsupported settings: "
            + ", ".join(str(key) for key in unknown_settings)
        )
    requirements = copy.deepcopy(dict(baseline))
    requirements.update(copy.deepcopy(dict(raw_settings)))
    try:
        first_choice = normalize_perk_first_choice_requirement(requirements)
        bans, auto_pick_order = normalize_perk_configuration_requirements(
            requirements
        )
        skipped = normalize_profile_skip_checks(
            configured.get("skipped_checks")
        )
    except ValueError as exc:
        raise ValueError(f"farm profile setup {exc}") from exc
    return {
        "skipped_checks": skipped,
        "settings": {
            **requirements,
            "perk_first_choice": first_choice,
            "perk_bans": bans,
            "perk_auto_pick_order": auto_pick_order,
        },
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


def _resolve_definition_policy(
    setting: str,
    raw: Any,
    catalog_path: Path,
) -> dict[str, Any]:
    policy = _normalize_policy(setting, raw)
    mode = policy["mode"]
    if mode == "preserve":
        if set(policy) != {"mode"}:
            raise ValueError(
                f"farm loadout {setting} preserve mode must not supply a "
                "preset or local definition"
            )
        return {"mode": mode}

    source_fields = set(policy) - {"mode"}
    if source_fields == {"preset"}:
        preset = str(policy.get("preset") or "").strip()
        if not preset:
            raise ValueError(
                f"farm loadout {setting} {mode} mode requires a preset"
            )
        catalog = _load_mapping(catalog_path, f"{setting} preset catalog")
        if catalog.get("schema_version") != 1:
            raise ValueError(
                f"{setting} preset catalog has an unsupported schema"
            )
        presets = catalog.get("presets")
        if not isinstance(presets, Mapping) or preset not in presets:
            raise ValueError(f"unknown {setting} preset {preset!r}")
        resolved = _normalize_definition(setting, presets[preset])
        return {"mode": mode, "preset": preset, "resolved": resolved}

    if source_fields == {"preset_snapshot"}:
        snapshot = policy.get("preset_snapshot")
        expected_snapshot_fields = (
            {"preset", "definition", "range_relationships"}
            if setting == "orb_distance"
            else {"preset", "definition"}
        )
        if (
            not isinstance(snapshot, Mapping)
            or set(snapshot) != expected_snapshot_fields
        ):
            raise ValueError(
                f"farm loadout {setting} preset_snapshot is incomplete or ambiguous"
            )
        preset = str(snapshot.get("preset") or "").strip()
        if not preset:
            raise ValueError(
                f"farm loadout {setting} preset_snapshot requires a preset id"
            )
        resolved = _normalize_definition(setting, snapshot.get("definition"))
        result = {"mode": mode, "preset": preset, "resolved": resolved}
        if setting == "orb_distance":
            result["range_presets"] = _normalize_orb_relationships(
                resolved,
                snapshot.get("range_relationships"),
            )
        return result

    expected_local_fields = (
        {"local", "range_relationships"}
        if setting == "orb_distance"
        else {"local"}
    )
    if source_fields != expected_local_fields:
        raise ValueError(
            f"farm loadout {setting} {mode} mode must define exactly one "
            "preset or local definition"
        )
    resolved = _normalize_definition(setting, policy.get("local"))
    result = {"mode": mode, "resolved": resolved}
    if setting == "orb_distance":
        result["range_presets"] = _normalize_orb_relationships(
            resolved,
            policy.get("range_relationships"),
        )
    return result


def _normalize_definition(setting: str, raw: Any) -> Any:
    try:
        if setting == "modules":
            from core.gc_module_loadout import normalize_gc_module_requirements

            return normalize_gc_module_requirements(raw)
        if setting == "target_priority":
            from core.target_priority_config import validate_target_priority_order

            if not isinstance(raw, list):
                raise ValueError("target_priority local definition must be a list")
            return validate_target_priority_order(raw)
        if setting == "orb_distance":
            from core.orb_distance import normalize_orb_distance_preset

            return normalize_orb_distance_preset(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"farm loadout {setting} {exc}") from exc
    raise ValueError(f"farm loadout has unsupported definition {setting!r}")


def _normalize_orb_relationships(
    selected: Mapping[str, str],
    raw: Any,
) -> list[dict[str, str]]:
    from core.orb_distance import normalize_orb_distance_presets

    try:
        relationships = normalize_orb_distance_presets(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"farm loadout orb_distance range_relationships {exc}"
        ) from exc
    matching = [
        item
        for item in relationships
        if item["range_basis"] == selected["range_basis"]
    ]
    if matching != [dict(selected)]:
        raise ValueError(
            "farm loadout orb_distance range_relationships must contain "
            "the selected relationship exactly"
        )
    return relationships


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


def _resolve_orb_distance_policy(raw: Any) -> dict[str, Any]:
    policy = _resolve_definition_policy(
        "orb_distance",
        raw,
        ORB_DISTANCE_PRESETS_PATH,
    )
    if policy["mode"] == "preserve":
        return policy
    from core.orb_distance import (
        normalize_orb_distance_preset,
        normalize_orb_distance_presets,
    )

    try:
        policy["resolved"] = normalize_orb_distance_preset(policy["resolved"])
        if "range_presets" not in policy:
            catalog = _load_mapping(
                ORB_DISTANCE_PRESETS_PATH,
                "orb_distance preset catalog",
            )
            policy["range_presets"] = normalize_orb_distance_presets(
                catalog.get("presets")
            )
    except ValueError as exc:
        raise ValueError(f"farm loadout orb_distance {exc}") from exc
    return policy


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
