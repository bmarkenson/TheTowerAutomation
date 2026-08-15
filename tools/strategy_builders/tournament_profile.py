"""Build the Tournament exclusive validator and observer from its profile."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from core.gc_module_loadout import normalize_gc_module_requirements
from core.orb_distance import (
    normalize_orb_distance_preset,
    normalize_orb_distance_presets,
)
from core.player_save_preflight import normalize_player_save_preflight_mode
from core.tournament_preflight import load_tournament_contract


TOURNAMENT_RUNTIME_POLICY = {
    "player_save_preflight": "save_first",
    "handlers": [
        "ad_gem",
        "daily_gem",
        "mission_rewards",
        "game_over",
        "game_speed",
    ],
    "auto_return": False,
    "home_preflight": True,
    "session_preflight_on_attach": True,
    "exclusive_validation": {
        "battle_kind": "ordinary_new_battle",
        "timeout_seconds": 300,
        "ready_message": (
            "Tournament validation passed; waiting for operator confirmation"
        ),
        "failure_prefix": "Tournament validation failed",
        "operator_launch": {
            "kind": "tournament_battle",
            "timeout_seconds": 60,
            "prompt_title": "Tournament validation passed",
            "prompt_message": (
                "Start the Tournament now? Automation will verify the current "
                "Home or Tournament entry screen and start exactly one "
                "Tournament battle."
            ),
            "reminder": (
                "When the Tournament battle begins, set Target Priorities for "
                "the current Tournament Battle Conditions."
            ),
        },
    },
}


def _resolve_authoring_loadout(
    source: Mapping[str, Any],
    requirements: dict[str, Any],
    *,
    default_module_mode: str,
    default_module_preset: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Apply retained authoring definitions over protected Tournament defaults."""

    raw_loadout = source.get("loadout")
    if raw_loadout is None:
        return (
            {
                "mode": default_module_mode,
                "preset": default_module_preset,
                "resolved": copy.deepcopy(requirements["modules"]),
            },
            copy.deepcopy(requirements["orb_distance"]),
            str(requirements["orb_distance"]["mode"]),
        )
    if not isinstance(raw_loadout, Mapping) or set(raw_loadout) != {
        "modules",
        "orb_distance",
    }:
        raise ValueError(
            "authored Tournament loadout must define exactly modules and "
            "orb_distance"
        )

    raw_modules = raw_loadout["modules"]
    if not isinstance(raw_modules, Mapping):
        raise ValueError("authored Tournament modules must be a mapping")
    module_mode = str(raw_modules.get("mode") or "").strip().lower()
    if module_mode not in {"enforce", "observe", "preserve"}:
        raise ValueError(
            "authored Tournament modules mode must be enforce, observe, or preserve"
        )
    if module_mode == "preserve":
        if set(raw_modules) != {"mode"}:
            raise ValueError(
                "preserved Tournament modules must define only mode"
            )
        requirements.pop("modules", None)
        module_configuration: dict[str, Any] = {"mode": "preserve"}
    else:
        unknown_modules = sorted(
            set(raw_modules) - {"mode", "preset", "resolved"}
        )
        if unknown_modules or "resolved" not in raw_modules:
            raise ValueError(
                "authored Tournament modules require resolved and support "
                "only mode, preset, and resolved"
            )
        modules = normalize_gc_module_requirements(raw_modules["resolved"])
        requirements["modules"] = copy.deepcopy(modules)
        module_configuration = {
            "mode": module_mode,
            "resolved": copy.deepcopy(modules),
        }
        if "preset" in raw_modules:
            preset = str(raw_modules.get("preset") or "").strip()
            if not preset:
                raise ValueError(
                    "authored Tournament module preset must be non-empty"
                )
            module_configuration["preset"] = preset
    requirements["loadout_policies"] = {"modules": module_mode}

    raw_orb = raw_loadout["orb_distance"]
    if not isinstance(raw_orb, Mapping):
        raise ValueError("authored Tournament orb_distance must be a mapping")
    orb_mode = str(raw_orb.get("mode") or "").strip().lower()
    if orb_mode not in {"enforce", "observe", "preserve"}:
        raise ValueError(
            "authored Tournament orb_distance mode must be enforce, observe, "
            "or preserve"
        )
    if orb_mode == "preserve":
        if set(raw_orb) != {"mode"}:
            raise ValueError(
                "preserved Tournament orb_distance must define only mode"
            )
        requirements.pop("orb_distance", None)
        orb_configuration: dict[str, Any] = {"mode": "preserve"}
    else:
        unknown_orb = sorted(
            set(raw_orb) - {"mode", "preset", "resolved", "range_presets"}
        )
        if unknown_orb or not {"resolved", "range_presets"}.issubset(raw_orb):
            raise ValueError(
                "authored Tournament orb_distance requires resolved and "
                "range_presets and supports only mode, preset, resolved, and "
                "range_presets"
            )
        orb_values = normalize_orb_distance_preset(raw_orb["resolved"])
        orb_presets = normalize_orb_distance_presets(raw_orb["range_presets"])
        matching = [
            item
            for item in orb_presets
            if item["range_basis"] == orb_values["range_basis"]
        ]
        if matching != [orb_values]:
            raise ValueError(
                "authored Tournament Orb Distance range relationships do not "
                "contain the selected relationship exactly"
            )
        orb_configuration = {
            "mode": orb_mode,
            "resolved": copy.deepcopy(orb_values),
            "range_presets": copy.deepcopy(orb_presets),
        }
        if "preset" in raw_orb:
            preset = str(raw_orb.get("preset") or "").strip()
            if not preset:
                raise ValueError(
                    "authored Tournament Orb Distance preset must be non-empty"
                )
            orb_configuration["preset"] = preset
        requirements["orb_distance"] = copy.deepcopy(orb_configuration)

    return module_configuration, orb_configuration, orb_mode


def build_tournament_strategy(source: Mapping[str, Any]) -> dict[str, Any]:
    """Expand a Tournament source into a self-contained validation plan."""

    if source.get("run_profile") != "tournament":
        raise ValueError("tournament builder requires run_profile: tournament")
    meta = copy.deepcopy(source.get("meta") or {})
    name = str(meta.get("name") or "").strip()
    if not name:
        raise ValueError("tournament profile requires meta.name")
    family = str(meta.get("family") or "tournament").strip().lower()
    if family != "tournament":
        raise ValueError("tournament profile meta.family must be tournament")
    meta["family"] = "tournament"

    contract = load_tournament_contract()
    requirements = copy.deepcopy(contract.requirements)
    module_configuration, orb_distance, orb_distance_mode = (
        _resolve_authoring_loadout(
            source,
            requirements,
            default_module_mode=contract.module_mode,
            default_module_preset=contract.module_preset,
        )
    )
    damage_slider = copy.deepcopy(requirements["damage_slider"])
    orb_distance_values = copy.deepcopy(orb_distance.get("resolved"))
    orb_distance_presets = copy.deepcopy(orb_distance.get("range_presets"))
    runtime_policy = copy.deepcopy(TOURNAMENT_RUNTIME_POLICY)
    requested_runtime_policy = source.get("runtime_policy") or {}
    if not isinstance(requested_runtime_policy, Mapping):
        raise ValueError("tournament runtime_policy must be a mapping")
    unknown_runtime_policy = sorted(
        set(requested_runtime_policy) - {"player_save_preflight"}
    )
    if unknown_runtime_policy:
        raise ValueError(
            "tournament runtime_policy has unsupported settings: "
            + ", ".join(str(value) for value in unknown_runtime_policy)
        )
    runtime_policy["player_save_preflight"] = (
        normalize_player_save_preflight_mode(
            requested_runtime_policy.get(
                "player_save_preflight",
                runtime_policy["player_save_preflight"],
            )
        )
    )
    variables: dict[str, Any] = {
        "exclusive_validation_battle": False,
        "ehls_completed": False,
        "eals_completed": False,
        "maxed_enemy_health_level_skip": False,
        "maxed_enemy_attack_level_skip": False,
        "ehls_completion_wave": None,
        "eals_completion_wave": None,
        "eals_first_tap_wave": None,
        "eals_first_tap_elapsed_s": None,
        "level_skip_elapsed_s": 0.0,
        "level_skip_taps_sent": 0,
        "level_skip_last_reason": "",
        "damage_slider_checked": False,
        "damage_slider_observation": {},
        "gc_session_preflight_completed": False,
        "gc_session_preflight_attempted": False,
        "gc_session_preflight_degraded": False,
        "gc_session_preflight_disposition": "",
        "gc_session_preflight_blocked": False,
        "gc_session_preflight_repair_required": False,
        "gc_session_preflight_repair_in_progress": False,
        "gc_session_preflight_last_status": "",
        "gc_session_preflight_last_reason": "",
        "gc_session_preflight_evidence": {},
    }
    orb_completion_var: str | None = None
    if orb_distance_mode == "enforce":
        orb_completion_var = "orb_distance_checked"
        variables["orb_distance_checked"] = False
        variables["orb_distance_observation"] = {}
    elif orb_distance_mode == "observe":
        orb_completion_var = "orb_distance_observed"
        variables["orb_distance_observed"] = False
        variables["orb_distance_observation"] = {}

    session_complete_when = ["damage_slider_checked"]
    if orb_completion_var is not None:
        session_complete_when.append(orb_completion_var)
    session_complete_when.append("gc_session_preflight_attempted")

    rules: list[dict[str, Any]] = [
        {
            "name": "initialize_tournament_level_skips",
            "gate_phase": "run_initialization",
            "when": {"state": "RUNNING"},
            "assert": [
                "!exclusive_validation_battle",
                "!eals_completed",
            ],
            "cooldown_sec": 0.25,
            "do": [{"type": "level_skip_initialize"}],
        },
        {
            "name": "enforce_tournament_damage_slider",
            "gate_phase": "session_preflight",
            "run_when_attached": True,
            "when": {"state": "RUNNING"},
            "assert": ["!damage_slider_checked"],
            "cooldown_sec": 30.0,
            "do": [
                {
                    "type": "damage_slider_configure",
                    "mode": damage_slider["mode"],
                    "value": damage_slider["value"],
                }
            ],
        },
    ]
    if orb_completion_var is not None:
        if not isinstance(orb_distance_values, Mapping) or not isinstance(
            orb_distance_presets, list
        ):
            raise ValueError(
                "active Tournament Orb Distance requires resolved values and "
                "range presets"
            )
        rules.append(
            {
                "name": f"{orb_distance_mode}_tournament_orb_distance",
                "gate_phase": "session_preflight",
                "run_when_attached": True,
                "when": {"state": "RUNNING"},
                "assert": [
                    "damage_slider_checked",
                    f"!{orb_completion_var}",
                ],
                "cooldown_sec": 30.0,
                "do": [
                    {
                        "type": "orb_distance_configure",
                        "mode": orb_distance_mode,
                        **copy.deepcopy(dict(orb_distance_values)),
                        "range_presets": copy.deepcopy(orb_distance_presets),
                    }
                ],
            }
        )
    session_assertions = ["damage_slider_checked"]
    if orb_completion_var is not None:
        session_assertions.append(orb_completion_var)
    session_assertions.append("!gc_session_preflight_attempted")
    rules.append(
        {
            "name": "validate_tournament_session_preflight",
            "gate_phase": "session_preflight",
            "run_when_attached": True,
            "when": {"state": "RUNNING"},
            "assert": session_assertions,
            "cooldown_sec": 30.0,
            "do": [
                {
                    "type": "session_preflight",
                    "validator": "tournament",
                    "allow_repair": False,
                    "mismatch_policy": "notify",
                    "stay_in_battle_when_attached": True,
                    "requirements": copy.deepcopy(requirements),
                }
            ],
        }
    )

    return {
        "meta": meta,
        "runtime_policy": runtime_policy,
        "run_initialization": {
            "complete_when": ["ehls_completed", "eals_completed"],
        },
        "session_preflight": {
            # A conclusive mismatch is still a completed observer check. It is
            # recorded and reported, but it never authorizes a repair or blocks
            # natural Game Over handling.
            "complete_when": session_complete_when,
            "requirements": copy.deepcopy(requirements),
        },
        "vars": variables,
        "per_run_reset": [
            "exclusive_validation_battle",
            "ehls_completed",
            "eals_completed",
            "maxed_enemy_health_level_skip",
            "maxed_enemy_attack_level_skip",
            "ehls_completion_wave",
            "eals_completion_wave",
            "eals_first_tap_wave",
            "eals_first_tap_elapsed_s",
            "level_skip_elapsed_s",
            "level_skip_taps_sent",
            "level_skip_last_reason",
        ],
        "rules": rules,
        "run_configuration": {
            "schema_version": 2,
            "profile": "tournament",
            "profile_version": 1,
            "settings": {
                key: copy.deepcopy(requirements[key])
                for key in (
                    "cards_deck",
                    "card_recharge_modes",
                    "workshop_preset",
                    "bots_preset",
                    "guardian_chips",
                    "ultimate_weapons",
                )
            },
            "loadout": {
                "modules": {
                    **copy.deepcopy(module_configuration),
                },
                "damage_slider": damage_slider,
                "orb_distance": orb_distance,
            },
            "runtime_policy": copy.deepcopy(runtime_policy),
        },
    }


__all__ = ["TOURNAMENT_RUNTIME_POLICY", "build_tournament_strategy"]
