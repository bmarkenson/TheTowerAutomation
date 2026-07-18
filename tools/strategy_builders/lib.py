from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Tuple


def slugify(label: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    if not slug:
        slug = fallback
    return slug


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def normalize_conditions(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    if not raw:
        return {}
    allowed = {
        "state",
        "menu",
        "assert",
        "wave",
        "elapsed_secs",
        "overlays_contains",
        "overlays_not_contains",
        "floating_visible",
    }
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed:
            raise ValueError(f"Unsupported condition key '{key}'")
        if key in {"overlays_contains", "overlays_not_contains"}:
            out[key] = list(value) if isinstance(value, list) else [value]
        elif key == "assert":
            out[key] = as_list(value)
        else:
            out[key] = value
    return out


def merge_conditions(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in extra.items():
        if key == "assert":
            merged.setdefault("assert", [])
            merged["assert"] = as_list(merged["assert"]) + as_list(value)
        elif key in {"overlays_contains", "overlays_not_contains"}:
            merged.setdefault(key, [])
            merged[key] = as_list(merged[key]) + (list(value) if isinstance(value, list) else [value])
        else:
            merged[key] = value
    return merged


def _with_assertions(when: Dict[str, Any], assertions: Iterable[Any] | None) -> Dict[str, Any]:
    data = copy.deepcopy(when or {})
    if assertions:
        existing = as_list(data.get("assert"))
        existing.extend(as_list(assertions))
        data["assert"] = existing
    return data


def build_strategy_yaml(source: Dict[str, Any]) -> Dict[str, Any]:
    builder = (source.get("builder") or source.get("strategy_type") or "").strip().lower()
    if builder in {"passthrough", "manual", "raw"}:
        return _build_manual_strategy(source)
    if builder == "gc_farm":
        return _build_gc_farm_strategy(source)
    if builder == "farm":
        from tools.strategy_builders.farm_profile import resolve_farm_source

        return _build_gc_farm_strategy(resolve_farm_source(source))
    if builder == "tournament":
        from tools.strategy_builders.tournament_profile import (
            build_tournament_strategy,
        )

        return build_tournament_strategy(source)
    if builder in {"", "default", "upgrade"}:
        return _build_upgrade_strategy(source)
    if builder == "glass_cannon":
        return _build_glass_cannon_strategy(source)
    raise ValueError(f"Unknown strategy builder '{builder}'")


def _build_manual_strategy(source: Dict[str, Any]) -> Dict[str, Any]:
    data = copy.deepcopy(source)
    data.pop("builder", None)
    data.pop("strategy_type", None)
    return data


def _build_gc_farm_strategy(source: Dict[str, Any]) -> Dict[str, Any]:
    """Compose the shared GC startup sequence with one concrete profile."""

    meta = copy.deepcopy(source.get("meta") or {})
    profile_name = str(meta.get("name") or "").strip()
    if not profile_name:
        raise ValueError("gc_farm profile requires meta.name")

    initialization = source.get("initialization") or {}
    target_priority = initialization.get("target_priority") or {}
    target_priority_mode = str(target_priority.get("mode") or "").strip().lower()
    if target_priority_mode not in {"preserve", "observe", "enforce"}:
        raise ValueError(
            "gc_farm initialization.target_priority.mode must be preserve, "
            "observe, or enforce"
        )

    target_priority_order: List[str] | None = None
    configured_order = target_priority.get("order")
    if target_priority_mode == "preserve":
        if configured_order is not None:
            raise ValueError("gc_farm preserve mode must not supply a Target Priority order")
    else:
        if not isinstance(configured_order, list):
            raise ValueError(
                f"gc_farm {target_priority_mode} mode requires a Target Priority order"
            )
        from core.target_priority_config import validate_target_priority_order

        try:
            target_priority_order = validate_target_priority_order(configured_order)
        except ValueError as exc:
            raise ValueError(f"gc_farm {exc}") from exc

    damage_slider = initialization.get("damage_slider") or {"mode": "preserve"}
    damage_slider_mode = str(damage_slider.get("mode") or "").strip().lower()
    if damage_slider_mode not in {"preserve", "observe", "enforce"}:
        raise ValueError(
            "gc_farm initialization.damage_slider.mode must be preserve, "
            "observe, or enforce"
        )
    damage_slider_value: str | None = None
    configured_damage_value = damage_slider.get("value")
    if damage_slider_mode == "preserve":
        if configured_damage_value is not None:
            raise ValueError(
                "gc_farm preserved Damage Slider must not supply a value"
            )
    else:
        if configured_damage_value is None:
            raise ValueError(
                f"gc_farm {damage_slider_mode} Damage Slider requires a value"
            )
        from core.damage_adjuster import normalize_damage_percentage

        try:
            damage_slider_value = normalize_damage_percentage(
                configured_damage_value
            )
        except ValueError as exc:
            raise ValueError(f"gc_farm Damage Slider {exc}") from exc

    session_requirements = _normalize_gc_session_preflight(
        source.get("session_preflight")
    )

    complete_when = ["ehls_completed", "eals_completed"]
    vars_block: Dict[str, Any] = {
        "run_initialised": False,
        "fast_loop_active": False,
        "loop_sleep_override_sec": 0.0,
        "ehls_completed": False,
        "eals_completed": False,
        "last_upgrade_label": "",
        "last_upgrade_reason": "",
        "last_upgrade_sent": False,
        "last_upgrade_maxed_after": False,
        "last_upgrade_menu": "",
        "last_upgrade_ts": 0,
        "maxed_enemy_health_level_skip": False,
        "maxed_enemy_attack_level_skip": False,
        "ehls_completion_wave": None,
        "eals_completion_wave": None,
        "eals_first_tap_wave": None,
        "eals_first_tap_elapsed_s": None,
        "level_skip_elapsed_s": 0.0,
        "level_skip_taps_sent": 0,
        "level_skip_last_reason": "",
    }
    if target_priority_mode == "enforce":
        vars_block["target_priority_checked"] = False
        complete_when.append("target_priority_checked")
    elif target_priority_mode == "observe":
        vars_block["target_priority_observed"] = False
        vars_block["target_priority_observation"] = {}
    if damage_slider_mode == "enforce":
        vars_block["damage_slider_checked"] = False
        vars_block["damage_slider_observation"] = {}
        complete_when.append("damage_slider_checked")
    elif damage_slider_mode == "observe":
        vars_block["damage_slider_observed"] = False
        vars_block["damage_slider_observation"] = {}
    vars_block.update(
        gc_no_battle_setup_completed=False,
        gc_no_battle_setup_evidence={},
        gc_session_preflight_completed=False,
        gc_session_preflight_attempted=False,
        gc_session_preflight_blocked=False,
        gc_session_preflight_repair_required=False,
        gc_session_preflight_repair_in_progress=False,
        gc_session_preflight_last_status="",
        gc_session_preflight_last_reason="",
        gc_session_preflight_evidence={},
    )

    per_run_reset = [
        "run_initialised",
        "fast_loop_active",
        "loop_sleep_override_sec",
        "ehls_completed",
        "eals_completed",
        "last_upgrade_label",
        "last_upgrade_reason",
        "last_upgrade_sent",
        "last_upgrade_maxed_after",
        "last_upgrade_menu",
        "last_upgrade_ts",
        "maxed_enemy_health_level_skip",
        "maxed_enemy_attack_level_skip",
        "ehls_completion_wave",
        "eals_completion_wave",
        "eals_first_tap_wave",
        "eals_first_tap_elapsed_s",
        "level_skip_elapsed_s",
        "level_skip_taps_sent",
        "level_skip_last_reason",
    ]
    if damage_slider_mode == "enforce":
        per_run_reset.extend(
            ["damage_slider_checked", "damage_slider_observation"]
        )
    elif damage_slider_mode == "observe":
        per_run_reset.extend(
            ["damage_slider_observed", "damage_slider_observation"]
        )

    reset_values = {
        "run_initialised": False,
        "fast_loop_active": False,
        "loop_sleep_override_sec": 0.0,
        "ehls_completed": False,
        "eals_completed": False,
        "maxed_enemy_health_level_skip": False,
        "maxed_enemy_attack_level_skip": False,
        "last_upgrade_label": "",
        "last_upgrade_reason": "",
        "last_upgrade_sent": False,
        "last_upgrade_maxed_after": False,
        "last_upgrade_menu": "",
        "last_upgrade_ts": 0,
        "ehls_completion_wave": None,
        "eals_completion_wave": None,
        "eals_first_tap_wave": None,
        "eals_first_tap_elapsed_s": None,
        "level_skip_elapsed_s": 0.0,
        "level_skip_taps_sent": 0,
        "level_skip_last_reason": "",
    }
    if damage_slider_mode == "enforce":
        reset_values.update(
            damage_slider_checked=False,
            damage_slider_observation={},
        )
    elif damage_slider_mode == "observe":
        reset_values.update(
            damage_slider_observed=False,
            damage_slider_observation={},
        )
    initialize_values = copy.deepcopy(reset_values)
    initialize_values.update(
        run_initialised=True,
        fast_loop_active=True,
        loop_sleep_override_sec=1.0,
    )

    rules: List[Dict[str, Any]] = [
        {
            "name": "reset_on_game_over",
            "when": {"state": "GAME_OVER"},
            "do": [
                {"type": "set", "var": name, "value": value}
                for name, value in reset_values.items()
            ],
        },
        {
            "name": "initialise_fast_loop",
            "when": {"state": "RUNNING"},
            "assert": ["!run_initialised"],
            "do": [
                {"type": "set", "var": name, "value": value}
                for name, value in initialize_values.items()
            ],
        },
        {
            "name": "disable_fast_loop",
            "when": {"state": "RUNNING"},
            "assert": ["fast_loop_active", "ehls_completed", "eals_completed"],
            "do": [
                {"type": "set", "var": "fast_loop_active", "value": False},
                {"type": "set", "var": "loop_sleep_override_sec", "value": 0.0},
            ],
        },
    ]

    rules.append(
        {
            "name": "initialize_level_skips_fast",
            "when": {"state": "RUNNING"},
            "assert": ["fast_loop_active", "!eals_completed"],
            "cooldown_sec": 0.25,
            "do": [{"type": "level_skip_initialize"}],
        }
    )

    if damage_slider_mode in {"observe", "enforce"}:
        completion_var = (
            "damage_slider_checked"
            if damage_slider_mode == "enforce"
            else "damage_slider_observed"
        )
        rules.append(
            {
                "name": f"{damage_slider_mode}_damage_slider",
                "when": {"state": "RUNNING"},
                "assert": [
                    "ehls_completed",
                    "eals_completed",
                    f"!{completion_var}",
                ],
                "cooldown_sec": 30.0,
                "do": [
                    {
                        "type": "damage_slider_configure",
                        "mode": damage_slider_mode,
                        "value": damage_slider_value,
                    }
                ],
            }
        )

    if target_priority_mode == "enforce":
        rules.append(
            {
                "name": "ensure_target_priority",
                "when": {"state": "RUNNING"},
                "assert": [
                    "ehls_completed",
                    "eals_completed",
                    "!target_priority_checked",
                ],
                "cooldown_sec": 30.0,
                "do": [
                    {
                        "type": "target_priority_ensure",
                        "order": copy.deepcopy(target_priority_order),
                    }
                ],
            }
        )
    elif target_priority_mode == "observe":
        rules.append(
            {
                "name": "observe_target_priority",
                "when": {"state": "RUNNING"},
                "assert": [
                    "ehls_completed",
                    "eals_completed",
                    "!target_priority_observed",
                ],
                "cooldown_sec": 30.0,
                "do": [
                    {
                        "type": "target_priority_observe",
                        "order": copy.deepcopy(target_priority_order),
                    }
                ],
            }
        )

    rules.append(
        {
            "name": "validate_gc_session_preflight",
            "when": {"state": "RUNNING"},
            "assert": [
                *complete_when,
                "!gc_session_preflight_completed",
                "!gc_session_preflight_attempted",
            ],
            "cooldown_sec": 30.0,
            "do": [
                {
                    "type": "gc_session_preflight",
                    "requirements": copy.deepcopy(session_requirements),
                }
            ],
        }
    )

    plan = {
        "meta": meta,
        "run_initialization": {"complete_when": complete_when},
        "session_preflight": {
            "complete_when": ["gc_session_preflight_completed"],
            "requirements": copy.deepcopy(session_requirements),
        },
        "vars": vars_block,
        "per_run_reset": per_run_reset,
        "rules": rules,
    }
    run_configuration = source.get("run_configuration")
    if isinstance(run_configuration, dict):
        plan["run_configuration"] = copy.deepcopy(run_configuration)
    return plan


def _normalize_gc_session_preflight(raw: Any) -> Dict[str, Any]:
    """Validate the concrete GC configuration supported by live evidence."""

    if not isinstance(raw, dict):
        raise ValueError("gc_farm session_preflight must be a mapping")

    requirements = copy.deepcopy(raw)
    fixed_values = {
        "cards_deck": "Farm",
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
    }
    for key, expected in fixed_values.items():
        actual = str(requirements.get(key) or "").strip()
        if actual != expected:
            raise ValueError(
                f"gc_farm session_preflight.{key} currently requires {expected!r}"
            )
        requirements[key] = actual

    guardian_chips = requirements.get("guardian_chips")
    if not isinstance(guardian_chips, list) or {
        str(chip).strip() for chip in guardian_chips
    } != {"Fetch", "Summon", "Scout"}:
        raise ValueError(
            "gc_farm session_preflight.guardian_chips must contain "
            "Fetch, Summon, and Scout"
        )
    requirements["guardian_chips"] = [
        str(chip).strip() for chip in guardian_chips
    ]

    raw_policies = requirements.get("loadout_policies") or {}
    if not isinstance(raw_policies, dict):
        raise ValueError("gc_farm session_preflight.loadout_policies must be a mapping")
    unknown_policies = sorted(set(raw_policies) - {"modules"})
    if unknown_policies:
        raise ValueError(
            "gc_farm session_preflight has unsupported loadout policies: "
            + ", ".join(unknown_policies)
        )
    module_mode = str(raw_policies.get("modules") or "enforce").strip().lower()
    if module_mode not in {"enforce", "observe", "preserve"}:
        raise ValueError(
            "gc_farm session_preflight modules policy must be enforce, "
            "observe, or preserve"
        )
    requirements["loadout_policies"] = {"modules": module_mode}
    if module_mode == "preserve":
        if "modules" in requirements:
            raise ValueError(
                "gc_farm preserved modules must not supply module requirements"
            )
    else:
        from core.gc_module_loadout import normalize_gc_module_requirements

        requirements["modules"] = normalize_gc_module_requirements(
            requirements.get("modules")
        )

    if requirements.get("auto_pick_perks") is not True:
        raise ValueError(
            "gc_farm session_preflight.auto_pick_perks must currently be true"
        )

    weapons = requirements.get("ultimate_weapons")
    if not isinstance(weapons, dict) or not weapons:
        raise ValueError(
            "gc_farm session_preflight.ultimate_weapons must be a non-empty mapping"
        )
    normalized_weapons: Dict[str, Dict[str, str]] = {}
    for label, toggles in weapons.items():
        canonical_label = str(label or "").strip()
        if not canonical_label or not isinstance(toggles, dict) or not toggles:
            raise ValueError(
                "gc_farm session_preflight Ultimate Weapon entries require "
                "a label and toggle mapping"
            )
        normalized_toggles: Dict[str, str] = {}
        for toggle, state in toggles.items():
            canonical_toggle = str(toggle or "").strip().lower()
            normalized_state = (
                "on"
                if state is True
                else "off"
                if state is False
                else str(state or "").strip().lower()
            )
            if not canonical_toggle or normalized_state not in {"on", "off"}:
                raise ValueError(
                    "gc_farm session_preflight Ultimate Weapon toggles require "
                    "on/off states"
                )
            if canonical_toggle == "stun" and (
                canonical_label.lower() != "poison swamp"
                or normalized_state != "off"
            ):
                raise ValueError(
                    "gc_farm session_preflight supports only Poison Swamp "
                    "stun=off"
                )
            normalized_toggles[canonical_toggle] = normalized_state
        normalized_weapons[canonical_label] = normalized_toggles
    requirements["ultimate_weapons"] = normalized_weapons
    return requirements


def _build_upgrade_strategy(source: Dict[str, Any]) -> Dict[str, Any]:
    meta = copy.deepcopy(source.get("meta") or {})
    settings = source.get("settings") or {}

    phases_src = source.get("phases")
    fallback_sequence = source.get("sequence")
    if phases_src and fallback_sequence:
        raise ValueError("Use either 'phases' or 'sequence', not both")

    if not phases_src:
        if not fallback_sequence:
            raise ValueError("strategy requires a 'phases' array or legacy 'sequence'")
        phases_src = [
            {
                "name": "default",
                "on_run_start": True,
                "sequence": fallback_sequence,
            }
        ]

    phases: List[Dict[str, Any]] = []
    phase_slug_counts: Dict[str, int] = {}
    stage_slug_counts: Dict[str, int] = {}

    for idx, phase in enumerate(phases_src):
        seq = phase.get("sequence") or []
        if not seq:
            raise ValueError(f"phase #{idx + 1} has no sequence entries")
        phase_name = phase.get("name") or f"phase_{idx + 1}"
        phase_slug = slugify(phase_name, fallback=f"phase_{idx}")
        if phase_slug in phase_slug_counts:
            phase_slug_counts[phase_slug] += 1
            phase_slug = f"{phase_slug}_{phase_slug_counts[phase_slug]}"
        else:
            phase_slug_counts[phase_slug] = 1

        conditions = normalize_conditions(phase.get("conditions"))
        on_run_start = bool(phase.get("on_run_start"))
        stage_var = f"stage_{phase_slug}"

        entries: List[Dict[str, Any]] = []
        for seq_idx, entry in enumerate(seq):
            menu = entry.get("menu")
            label = entry.get("label")
            if not menu or not label:
                raise ValueError(f"phase '{phase_name}' entry #{seq_idx + 1} missing menu or label")
            slug = entry.get("slug") or slugify(label, fallback=f"{phase_slug}_{seq_idx}")
            if slug in stage_slug_counts:
                stage_slug_counts[slug] += 1
                slug = f"{slug}_{stage_slug_counts[slug]}"
            else:
                stage_slug_counts[slug] = 1
            maxed_key = f"maxed_{slug}"
            entries.append(
                {
                    "menu": menu,
                    "label": label,
                    "slug": slug,
                    "maxed_key": maxed_key,
                }
            )

        phase_targets = phase.get("ultimate_targets")

        phases.append(
            {
                "name": phase_name,
                "slug": phase_slug,
                "stage_var": stage_var,
                "conditions": conditions,
                "on_run_start": on_run_start,
                "entries": entries,
                "ultimate_targets": phase_targets,
            }
        )

    if not phases:
        raise ValueError("No phases defined")

    cooldown_sec = float(settings.get("cooldown_sec") or 20.0)

    default_ultimate_targets = settings.get("ultimate_targets")
    if default_ultimate_targets is None:
        default_ultimate_targets = []
    else:
        default_ultimate_targets = list(default_ultimate_targets)

    vars_block: Dict[str, Any] = {
        "current_phase": phases[0]["slug"],
        "quantities_initialized": False,
        "completed": False,
        "ultimate_checked": False,
        "last_upgrade_label": "",
        "last_upgrade_reason": "",
        "last_upgrade_sent": False,
        "last_upgrade_maxed_after": False,
        "last_upgrade_menu": "",
        "last_upgrade_ts": 0,
        "ultimate_targets": copy.deepcopy(default_ultimate_targets),
    }

    per_run_reset: List[str] = [
        "quantities_initialized",
        "completed",
        "ultimate_checked",
        "last_upgrade_sent",
        "last_upgrade_maxed_after",
    ]

    all_maxed_keys: List[str] = []
    for phase in phases:
        vars_block[phase["stage_var"]] = 0
        per_run_reset.append(phase["stage_var"])
        for entry in phase["entries"]:
            vars_block[entry["maxed_key"]] = False
            per_run_reset.append(entry["maxed_key"])
            all_maxed_keys.append(entry["maxed_key"])

    per_run_reset = sorted(set(per_run_reset))

    rules: List[Dict[str, Any]] = []

    # Reset on GAME_OVER
    reset_ops: List[Dict[str, Any]] = [
        {"type": "set", "var": "current_phase", "value": phases[0]["slug"]},
        {"type": "set", "var": "quantities_initialized", "value": False},
        {"type": "set", "var": "completed", "value": False},
        {"type": "set", "var": "ultimate_checked", "value": False},
        {"type": "set", "var": "ultimate_targets", "value": copy.deepcopy(default_ultimate_targets)},
        {"type": "set", "var": "last_upgrade_label", "value": ""},
        {"type": "set", "var": "last_upgrade_reason", "value": ""},
        {"type": "set", "var": "last_upgrade_sent", "value": False},
        {"type": "set", "var": "last_upgrade_maxed_after", "value": False},
        {"type": "set", "var": "last_upgrade_menu", "value": ""},
        {"type": "set", "var": "last_upgrade_ts", "value": 0},
    ]
    reset_ops.extend({"type": "set", "var": phase["stage_var"], "value": 0} for phase in phases)
    reset_ops.extend({"type": "set", "var": key, "value": False} for key in all_maxed_keys)

    rules.append(
        {
            "name": "game_over_reset",
            "when": {"state": "GAME_OVER"},
            "do": reset_ops,
        }
    )

    # Phase selection rules (evaluate in source order)
    for phase in phases:
        phase_conditions = merge_conditions({"state": "RUNNING"}, phase["conditions"])
        asserts = as_list(phase_conditions.get("assert"))
        asserts.extend(["!completed", f"current_phase != {phase['slug']}"])
        phase_conditions["assert"] = asserts

        phase_target_list = phase.get("ultimate_targets")
        if phase_target_list is None:
            phase_target_list = copy.deepcopy(default_ultimate_targets)
        else:
            phase_target_list = copy.deepcopy(list(phase_target_list))

        rule_do = [
            {"type": "set", "var": "current_phase", "value": phase["slug"]},
            {"type": "set", "var": phase["stage_var"], "value": 0},
            {"type": "set", "var": "ultimate_checked", "value": False},
            {"type": "set", "var": "ultimate_targets", "value": phase_target_list},
            {"type": "set", "var": "last_upgrade_label", "value": ""},
            {"type": "set", "var": "last_upgrade_reason", "value": ""},
            {"type": "set", "var": "last_upgrade_sent", "value": False},
            {"type": "set", "var": "last_upgrade_maxed_after", "value": False},
            {"type": "set", "var": "last_upgrade_menu", "value": ""},
            {"type": "set", "var": "last_upgrade_ts", "value": 0},
        ]

        rules.append(
            {
                "name": f"phase_select_{phase['slug']}",
                "when": phase_conditions,
                "do": rule_do,
            }
        )

    # init buy quantities
    initial_quantities = settings.get("initial_buy_quantities") or {}
    if initial_quantities:
        rules.append(
            {
                "name": "init_buy_quantities",
                "when": {
                    "state": "RUNNING",
                    "assert": ["!quantities_initialized", "!completed"],
                },
                "do": [
                    dict(
                        {"type": "upgrade_set_buy_quantities"},
                        **{k: str(v) for k, v in initial_quantities.items()}
                    ),
                    {"type": "set", "var": "quantities_initialized", "value": True},
                ],
            }
        )

    # Helpers
    clear_last = [
        {"type": "set", "var": "last_upgrade_label", "value": ""},
        {"type": "set", "var": "last_upgrade_reason", "value": ""},
        {"type": "set", "var": "last_upgrade_sent", "value": False},
        {"type": "set", "var": "last_upgrade_maxed_after", "value": False},
        {"type": "set", "var": "last_upgrade_menu", "value": ""},
        {"type": "set", "var": "last_upgrade_ts", "value": 0},
    ]

    def stage_update_ops(phase_slug: str, stage_var: str, next_idx: int) -> List[Dict[str, Any]]:
        ops: List[Dict[str, Any]] = [{"type": "set", "var": stage_var, "value": next_idx}]
        if next_idx == 0:
            ops.append({"type": "set", "var": "ultimate_checked", "value": False})
        return ops

    # Phase-stage rules
    for phase in phases:
        phase_slug = phase["slug"]
        stage_var = phase["stage_var"]
        phase_conditions = merge_conditions({"state": "RUNNING"}, phase["conditions"])

        # ultimate check at start of phase loop
        when_ultimate = merge_conditions(phase_conditions, {})
        asserts = as_list(when_ultimate.get("assert"))
        asserts.extend([
            "!completed",
            f"current_phase == {phase_slug}",
            f"{stage_var} == 0",
            "!ultimate_checked",
        ])
        when_ultimate["assert"] = asserts
        rules.append(
            {
                "name": f"ensure_ultimate_on_{phase_slug}",
                "when": when_ultimate,
                "do": [
                    {
                        "type": "ultimate_ensure_state",
                        "targets": copy.deepcopy(
                            phase.get("ultimate_targets")
                            if phase.get("ultimate_targets") is not None
                            else default_ultimate_targets
                        ),
                    },
                    {"type": "set", "var": "ultimate_checked", "value": True},
                ],
            }
        )

        entries = phase["entries"]
        total_entries = len(entries)

        for idx, entry in enumerate(entries):
            menu = entry["menu"]
            label = entry["label"]
            slug = entry["slug"]
            maxed_key = entry["maxed_key"]
            next_idx = (idx + 1) % total_entries

            when_base = merge_conditions(phase_conditions, {})
            asserts = as_list(when_base.get("assert"))
            asserts.extend([
                "!completed",
                f"current_phase == {phase_slug}",
                f"{stage_var} == {idx}",
            ])
            when_base["assert"] = asserts

            # skip known maxed
            rules.append(
                {
                    "name": f"skip_{phase_slug}_{idx:02d}_{slug}_known",
                    "when": merge_conditions(when_base, {"assert": [maxed_key]}),
                    "do": stage_update_ops(phase_slug, stage_var, next_idx),
                }
            )

            # skip detected maxed
            ops = [
                {"type": "set", "var": maxed_key, "value": True},
                *stage_update_ops(phase_slug, stage_var, next_idx),
                *copy.deepcopy(clear_last),
            ]
            rules.append(
                {
                    "name": f"skip_{phase_slug}_{idx:02d}_{slug}_detected",
                    "when": merge_conditions(
                        when_base,
                        {"upgrade_maxed": {"menu": menu, "label": label}},
                    ),
                    "do": ops,
                }
            )

            # advance when purchase sent and maxed
            ops = [
                {"type": "set", "var": maxed_key, "value": True},
                *stage_update_ops(phase_slug, stage_var, next_idx),
                *copy.deepcopy(clear_last),
            ]
            rules.append(
                {
                    "name": f"advance_{phase_slug}_{idx:02d}_{slug}_sent_maxed",
                    "when": merge_conditions(
                        when_base,
                        {
                            "assert": [
                                f"last_upgrade_label == {label}",
                                "last_upgrade_sent",
                                "last_upgrade_maxed_after",
                            ]
                        },
                    ),
                    "do": ops,
                }
            )

            # advance when sent but not maxed
            ops = [
                {"type": "set", "var": maxed_key, "value": False},
                *stage_update_ops(phase_slug, stage_var, next_idx),
                *copy.deepcopy(clear_last),
            ]
            rules.append(
                {
                    "name": f"advance_{phase_slug}_{idx:02d}_{slug}_sent_not_maxed",
                    "when": merge_conditions(
                        when_base,
                        {
                            "assert": [
                                f"last_upgrade_label == {label}",
                                "last_upgrade_sent",
                                "!last_upgrade_maxed_after",
                            ]
                        },
                    ),
                    "do": ops,
                }
            )

            # advance when unaffordable
            ops = [
                {"type": "set", "var": maxed_key, "value": False},
                *stage_update_ops(phase_slug, stage_var, next_idx),
                *copy.deepcopy(clear_last),
            ]
            rules.append(
                {
                    "name": f"advance_{phase_slug}_{idx:02d}_{slug}_unaffordable",
                    "when": merge_conditions(
                        when_base,
                        {
                            "assert": [
                                f"last_upgrade_label == {label}",
                                "last_upgrade_reason == status=unaffordable",
                            ]
                        },
                    ),
                    "do": ops,
                }
            )

            # advance when reason status=maxed
            ops = [
                {"type": "set", "var": maxed_key, "value": True},
                *stage_update_ops(phase_slug, stage_var, next_idx),
                *copy.deepcopy(clear_last),
            ]
            rules.append(
                {
                    "name": f"advance_{phase_slug}_{idx:02d}_{slug}_maxed",
                    "when": merge_conditions(
                        when_base,
                        {
                            "assert": [
                                f"last_upgrade_label == {label}",
                                "last_upgrade_reason == status=maxed",
                            ]
                        },
                    ),
                    "do": ops,
                }
            )

            # purchase rule
            rules.append(
                {
                    "name": f"buy_{phase_slug}_{idx:02d}_{slug}",
                    "when": when_base,
                    "cooldown_sec": cooldown_sec,
                    "do": [
                        {
                            "type": "upgrade_purchase",
                            "menu": menu,
                            "label": label,
                        }
                    ],
                }
            )

    finish_assert = ["!completed", "ultimate_checked"] + all_maxed_keys
    rules.append(
        {
            "name": "finish_if_all_maxed",
            "when": {
                "state": "RUNNING",
                "assert": finish_assert,
            },
            "do": [
                {"type": "set", "var": "completed", "value": True},
                {"type": "set", "var": "current_phase", "value": ""},
            ],
        }
    )

    return {
        "meta": meta,
        "vars": vars_block,
        "per_run_reset": per_run_reset,
        "rules": rules,
    }


def _build_glass_cannon_strategy(source: Dict[str, Any]) -> Dict[str, Any]:
    meta = copy.deepcopy(source.get("meta") or {})
    settings = source.get("settings") or {}
    cards_cfg = source.get("cards") or {}
    upgrades_cfg = source.get("upgrades") or {}
    ultimate_targets = copy.deepcopy(source.get("ultimates") or _default_glass_cannon_ultimates())

    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    late_wave = _coerce_int(settings.get("late_wave") or cards_cfg.get("late_wave"), 5000)

    early_deck = (cards_cfg.get("early_deck") or "GCFarmEarly").strip()
    late_deck = (cards_cfg.get("late_deck") or "GCFarmLate").strip()

    ehls_cfg = upgrades_cfg.get("ehls") or {}
    ehls_menu = (ehls_cfg.get("menu") or "utility").strip()
    ehls_label = ehls_cfg.get("label") or "Enemy Health Level Skip"
    ehls_cooldown = _coerce_float(
        ehls_cfg.get("cooldown_sec") or settings.get("ehls_cooldown_sec"),
        15.0,
    )

    range_cfg = upgrades_cfg.get("range") or {}
    range_menu = (range_cfg.get("menu") or "attack").strip()
    range_label = range_cfg.get("label") or "Range"
    range_cooldown = _coerce_float(
        range_cfg.get("cooldown_sec") or settings.get("range_cooldown_sec"),
        15.0,
    )

    vars_block = {
        "phase": "early",
        "initial_uw_applied": False,
        "ehls_completed": False,
        "range_gold": False,
        "cards_mode": "",
        "cards_target": "",
        "cards_check_pending": False,
        "run_initialised": False,
        "cards_menu_requested": False,
        "early_cards_applied": False,
        "late_cards_applied": False,
    }

    per_run_reset = [
        "initial_uw_applied",
        "ehls_completed",
        "range_gold",
        "cards_mode",
        "cards_target",
        "cards_check_pending",
        "run_initialised",
        "cards_menu_requested",
        "early_cards_applied",
        "late_cards_applied",
    ]

    late_wave_condition = f">= {late_wave}"
    early_button_key = f"buttons.Cards:{early_deck}"
    late_button_key = f"buttons.Cards:{late_deck}"

    rules: List[Dict[str, Any]] = [
        {
            "name": "reset_on_game_over",
            "when": {"state": "GAME_OVER"},
            "do": [
                {"type": "set", "var": "phase", "value": "early"},
                {"type": "set", "var": "initial_uw_applied", "value": False},
                {"type": "set", "var": "ehls_completed", "value": False},
                {"type": "set", "var": "range_gold", "value": False},
                {"type": "set", "var": "cards_mode", "value": ""},
                {"type": "set", "var": "cards_target", "value": ""},
                {"type": "set", "var": "cards_check_pending", "value": False},
                {"type": "set", "var": "cards_menu_requested", "value": False},
                {"type": "set", "var": "run_initialised", "value": False},
                {"type": "set", "var": "early_cards_applied", "value": False},
                {"type": "set", "var": "late_cards_applied", "value": False},
            ],
        },
        {
            "name": "initialise_run_state",
            "when": _with_assertions({"state": "RUNNING"}, ["!run_initialised"]),
            "do": [
                {"type": "set", "var": "phase", "value": "early"},
                {"type": "set", "var": "cards_mode", "value": ""},
                {"type": "set", "var": "cards_target", "value": ""},
                {"type": "set", "var": "cards_check_pending", "value": False},
                {"type": "set", "var": "cards_menu_requested", "value": False},
                {"type": "set", "var": "run_initialised", "value": True},
                {"type": "set", "var": "early_cards_applied", "value": False},
                {"type": "set", "var": "late_cards_applied", "value": False},
            ],
        },
        {
            "name": "apply_initial_ultimates",
            "when": _with_assertions({"state": "RUNNING"}, ["!initial_uw_applied"]),
            "do": [
                {
                    "type": "ultimate_ensure_state",
                    "targets": ultimate_targets,
                },
                {"type": "set", "var": "initial_uw_applied", "value": True},
            ],
        },
        {
            "name": "detect_ehls_maxed",
            "when": {
                "state": "RUNNING",
                "assert": ["!ehls_completed"],
                "upgrade_maxed": {"menu": ehls_menu, "label": ehls_label},
            },
            "do": [
                {"type": "set", "var": "ehls_completed", "value": True},
            ],
        },
        {
            "name": "purchase_ehls",
            "when": _with_assertions({"state": "RUNNING"}, ["!ehls_completed"]),
            "cooldown_sec": ehls_cooldown,
            "do": [
                {
                    "type": "upgrade_purchase",
                    "menu": ehls_menu,
                    "label": ehls_label,
                }
            ],
        },
        {
            "name": "request_cards_initial",
            "when": _with_assertions(
                {"state": "RUNNING"},
                ["phase == early", "!cards_check_pending", "!early_cards_applied"],
            ),
            "do": [
                {"type": "set", "var": "cards_target", "value": "early"},
                {"type": "set", "var": "cards_check_pending", "value": True},
                {"type": "set", "var": "cards_menu_requested", "value": False},
            ],
        },
        {
            "name": "transition_to_late_phase",
            "when": _with_assertions(
                {"state": "RUNNING", "wave": late_wave_condition},
                ["phase == early"],
            ),
            "do": [
                {"type": "set", "var": "phase", "value": "late"},
            ],
        },
        {
            "name": "request_cards_late_phase",
            "when": _with_assertions(
                {"state": "RUNNING"},
                ["phase == late", "!cards_check_pending", "!late_cards_applied"],
            ),
            "do": [
                {"type": "set", "var": "cards_target", "value": "late"},
                {"type": "set", "var": "cards_check_pending", "value": True},
                {"type": "set", "var": "cards_menu_requested", "value": False},
            ],
        },
        {
            "name": "ensure_menu_open_for_cards",
            "when": _with_assertions(
                {"state": "RUNNING", "overlays_contains": ["MENU_CLOSED"]},
                ["cards_check_pending", "!cards_menu_requested"],
            ),
            "cooldown_sec": 4.0,
            "do": [
                {"type": "tap_label", "key": "navigation.menu_open_button"},
                {"type": "sleep", "ms": 300},
                {"type": "set", "var": "cards_menu_requested", "value": True},
            ],
        },
        {
            "name": "navigate_to_cards_menu",
            "when": _with_assertions(
                {"state": "RUNNING", "overlays_contains": ["MENU_OPEN"]},
                ["cards_check_pending"],
            ),
            "cooldown_sec": 3.0,
            "do": [
                {"type": "tap_label", "key": "navigation.Cards"},
                {"type": "sleep", "ms": 300},
                {"type": "set", "var": "cards_menu_requested", "value": False},
            ],
        },
        {
            "name": "select_cards_early",
            "when": _with_assertions({"state": "CARDS"}, ["cards_check_pending", "cards_target == early"]),
            "cooldown_sec": 2.0,
            "do": [
                {
                    "type": "tap_label",
                    "key": early_button_key,
                    "state_guard": ["CARDS"],
                },
                {"type": "sleep", "ms": 400},
                {"type": "set", "var": "cards_check_pending", "value": False},
            ],
        },
        {
            "name": "confirm_cards_early",
            "when": _with_assertions(
                {
                    "state": "CARDS",
                    "secondary_not_contains": ["LOCKED_CARDS"],
                },
                ["!cards_check_pending", "cards_target == early"],
            ),
            "do": [
                {
                    "type": "tap_label",
                    "key": "buttons.return_to_game",
                    "state_guard": ["CARDS"],
                },
                {"type": "sleep", "ms": 400},
                {"type": "set", "var": "cards_mode", "value": "early"},
                {"type": "set", "var": "cards_target", "value": ""},
                {"type": "set", "var": "cards_menu_requested", "value": False},
                {"type": "set", "var": "early_cards_applied", "value": True},
            ],
        },
        {
            "name": "select_cards_late",
            "when": _with_assertions({"state": "CARDS"}, ["cards_check_pending", "cards_target == late"]),
            "cooldown_sec": 2.0,
            "do": [
                {
                    "type": "tap_label",
                    "key": late_button_key,
                    "state_guard": ["CARDS"],
                },
                {"type": "sleep", "ms": 400},
                {"type": "set", "var": "cards_check_pending", "value": False},
            ],
        },
        {
            "name": "confirm_cards_late",
            "when": _with_assertions(
                {
                    "state": "CARDS",
                    "secondary_not_contains": ["LOCKED_CARDS"],
                },
                ["!cards_check_pending", "cards_target == late"],
            ),
            "do": [
                {
                    "type": "tap_label",
                    "key": "buttons.return_to_game",
                    "state_guard": ["CARDS"],
                },
                {"type": "sleep", "ms": 400},
                {"type": "set", "var": "cards_mode", "value": "late"},
                {"type": "set", "var": "cards_target", "value": ""},
                {"type": "set", "var": "cards_menu_requested", "value": False},
                {"type": "set", "var": "late_cards_applied", "value": True},
            ],
        },
        {
            "name": "handle_locked_cards",
            "when": _with_assertions(
                {
                    "state": "CARDS",
                    "secondary_contains": ["LOCKED_CARDS"],
                },
                ["cards_target"],
            ),
            "cooldown_sec": 6.0,
            "do": [
                {
                    "type": "tap_label",
                    "key": "buttons.cards:locked:ok",
                    "state_guard": ["CARDS"],
                },
                {"type": "sleep", "ms": 300},
                {
                    "type": "tap_label",
                    "key": "buttons.return_to_game",
                    "state_guard": ["CARDS"],
                },
                {"type": "sleep", "ms": 10000},
                {"type": "set", "var": "cards_menu_requested", "value": False},
                {"type": "set", "var": "cards_check_pending", "value": True},
            ],
        },
        {
            "name": "detect_range_gold",
            "when": {
                "state": "RUNNING",
                "assert": ["phase == late", "!range_gold"],
                "upgrade_maxed": {"menu": range_menu, "label": range_label},
            },
            "do": [
                {"type": "set", "var": "range_gold", "value": True},
            ],
        },
        {
            "name": "purchase_range",
            "when": _with_assertions({"state": "RUNNING"}, ["phase == late", "!range_gold"]),
            "cooldown_sec": range_cooldown,
            "do": [
                {
                    "type": "upgrade_purchase",
                    "menu": range_menu,
                    "label": range_label,
                }
            ],
        },
    ]

    return {
        "meta": meta,
        "vars": vars_block,
        "per_run_reset": per_run_reset,
        "rules": rules,
    }


def _default_glass_cannon_ultimates() -> List[Dict[str, Any]]:
    return [
        {"label": "Chain Lightning", "toggles": {"primary": True}},
        {"label": "Smart Missiles", "toggles": {"primary": True}},
        {"label": "Death Wave", "toggles": {"primary": True}},
        {"label": "Chrono Field", "toggles": {"primary": True}},
        {"label": "Inner Land Mines", "toggles": {"primary": True}},
        {"label": "Golden Tower", "toggles": {"primary": True}},
        {"label": "Poison Swamp", "toggles": {"primary": True}},
        {"label": "Black Hole", "toggles": {"primary": True}},
        {"label": "Spotlight", "toggles": {"primary": True, "missiles": True}},
    ]


__all__ = ["build_strategy_yaml"]
