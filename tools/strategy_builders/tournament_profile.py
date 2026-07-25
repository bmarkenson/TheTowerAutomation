"""Build the passive Tournament observer from its compact profile."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from core.tournament_preflight import load_tournament_requirements


TOURNAMENT_RUNTIME_POLICY = {
    "handlers": ["ad_gem", "game_over", "game_speed"],
    "auto_return": False,
    "game_over_mode": "wait",
    "home_preflight": True,
    "session_preflight_on_attach": True,
    "preflight_mismatch": "notify",
}


def build_tournament_strategy(source: Mapping[str, Any]) -> dict[str, Any]:
    """Expand a Tournament source into a self-contained observer plan."""

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

    requirements = load_tournament_requirements()
    runtime_policy = copy.deepcopy(TOURNAMENT_RUNTIME_POLICY)
    variables = {
        "gc_session_preflight_completed": False,
        "gc_session_preflight_attempted": False,
        "gc_session_preflight_blocked": False,
        "gc_session_preflight_repair_required": False,
        "gc_session_preflight_repair_in_progress": False,
        "gc_session_preflight_last_status": "",
        "gc_session_preflight_last_reason": "",
        "gc_session_preflight_evidence": {},
        "gc_session_preflight_advisory": False,
    }

    return {
        "meta": meta,
        "runtime_policy": runtime_policy,
        "session_preflight": {
            # A conclusive mismatch is still a completed observer check. It is
            # recorded and reported, but it never authorizes a repair or blocks
            # natural Game Over handling.
            "complete_when": ["gc_session_preflight_attempted"],
            "requirements": copy.deepcopy(requirements),
        },
        "vars": variables,
        "per_run_reset": [],
        "rules": [
            {
                "name": "validate_tournament_session_preflight",
                "gate_phase": "session_preflight",
                "run_when_attached": True,
                "when": {"state": "RUNNING"},
                "assert": ["!gc_session_preflight_attempted"],
                "cooldown_sec": 30.0,
                "do": [
                    {
                        "type": "session_preflight",
                        "validator": "tournament",
                        "allow_repair": False,
                        "mismatch_policy": "notify",
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
                    "workshop_preset",
                    "bots_preset",
                    "guardian_chips",
                    "ultimate_weapons",
                )
            },
            "loadout": {
                "modules": {
                    "mode": "enforce",
                    "preset": "tournament_standard",
                    "resolved": copy.deepcopy(requirements["modules"]),
                }
            },
            "runtime_policy": copy.deepcopy(runtime_policy),
        },
    }


__all__ = ["TOURNAMENT_RUNTIME_POLICY", "build_tournament_strategy"]
