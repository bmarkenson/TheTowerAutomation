"""Build the Tournament exclusive validator and observer from its profile."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from core.player_save_preflight import normalize_player_save_preflight_mode
from core.tournament_preflight import load_tournament_contract


TOURNAMENT_RUNTIME_POLICY = {
    "player_save_preflight": "save_first",
    "handlers": ["ad_gem", "game_over", "game_speed"],
    "auto_return": False,
    "game_over_mode": "wait",
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
    requirements = contract.requirements
    damage_slider = copy.deepcopy(requirements["damage_slider"])
    orb_distance = copy.deepcopy(requirements["orb_distance"])
    orb_distance_values = copy.deepcopy(orb_distance["resolved"])
    orb_distance_presets = copy.deepcopy(orb_distance["range_presets"])
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
    variables = {
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
        "orb_distance_checked": False,
        "orb_distance_observation": {},
        "gc_session_preflight_completed": False,
        "gc_session_preflight_attempted": False,
        "gc_session_preflight_blocked": False,
        "gc_session_preflight_repair_required": False,
        "gc_session_preflight_repair_in_progress": False,
        "gc_session_preflight_last_status": "",
        "gc_session_preflight_last_reason": "",
        "gc_session_preflight_evidence": {},
    }

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
            "complete_when": [
                "damage_slider_checked",
                "orb_distance_checked",
                "gc_session_preflight_attempted",
            ],
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
        "rules": [
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
            {
                "name": "enforce_tournament_orb_distance",
                "gate_phase": "session_preflight",
                "run_when_attached": True,
                "when": {"state": "RUNNING"},
                "assert": [
                    "damage_slider_checked",
                    "!orb_distance_checked",
                ],
                "cooldown_sec": 30.0,
                "do": [
                    {
                        "type": "orb_distance_configure",
                        "mode": orb_distance["mode"],
                        **orb_distance_values,
                        "range_presets": orb_distance_presets,
                    }
                ],
            },
            {
                "name": "validate_tournament_session_preflight",
                "gate_phase": "session_preflight",
                "run_when_attached": True,
                "when": {"state": "RUNNING"},
                "assert": [
                    "damage_slider_checked",
                    "orb_distance_checked",
                    "!gc_session_preflight_attempted",
                ],
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
        ],
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
                    "mode": contract.module_mode,
                    "preset": contract.module_preset,
                    "resolved": copy.deepcopy(requirements["modules"]),
                },
                "damage_slider": damage_slider,
                "orb_distance": orb_distance,
            },
            "runtime_policy": copy.deepcopy(runtime_policy),
        },
    }


__all__ = ["TOURNAMENT_RUNTIME_POLICY", "build_tournament_strategy"]
