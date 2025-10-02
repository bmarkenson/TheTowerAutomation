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


def build_strategy_yaml(source: Dict[str, Any]) -> Dict[str, Any]:
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


__all__ = ["build_strategy_yaml"]
