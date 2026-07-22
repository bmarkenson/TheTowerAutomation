"""Shared startup-gate decision options and terminal prompting."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Sequence


VALID_GATE_DECISION_ACTIONS = frozenset({"pause", "retry", "waive"})
STARTUP_GATE_CHECK_LABELS = {
    "cards_deck": "Cards deck",
    "workshop_preset": "Workshop preset",
    "free_upgrade_locks": "Free Upgrade locks",
    "bots_preset": "Bot preset",
    "guardian_chips": "Guardian Chips",
    "modules": "Modules",
    "target_priority": "Target Priority",
    "auto_pick_perks": "Auto Pick Perks",
    "ultimate_weapons": "Ultimate Weapons",
}


def startup_gate_check_catalog(
    requirements: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return operator-facing checks, filtered by strategy requirements."""

    configured = dict(requirements or {})
    if requirements is None:
        included = set(STARTUP_GATE_CHECK_LABELS)
    else:
        included = set(configured) & set(STARTUP_GATE_CHECK_LABELS)
        policies = configured.get("loadout_policies")
        module_mode = (
            str(policies.get("modules") or "").strip().lower()
            if isinstance(policies, Mapping)
            else ""
        )
        if module_mode != "enforce":
            included.discard("modules")
        target_priority_mode = (
            str(policies.get("target_priority") or "").strip().lower()
            if isinstance(policies, Mapping)
            else ""
        )
        if target_priority_mode != "enforce":
            included.discard("target_priority")
        if not bool(configured.get("auto_pick_perks")):
            included.discard("auto_pick_perks")
        if not configured.get("free_upgrade_locks"):
            included.discard("free_upgrade_locks")
        if not configured.get("ultimate_weapons"):
            included.discard("ultimate_weapons")
    catalog = []
    for check_id, label in STARTUP_GATE_CHECK_LABELS.items():
        if check_id not in included:
            continue
        entry = {"id": check_id, "label": label}
        expected = configured.get(check_id)
        if expected is not None:
            if isinstance(expected, (Mapping, list, tuple)):
                expected_text = json.dumps(expected, ensure_ascii=False)
            else:
                expected_text = str(expected)
            entry["expected"] = expected_text[:500]
        catalog.append(entry)
    return catalog


def startup_gate_context_for_strategy(strategy_name: str) -> dict[str, Any]:
    """Resolve one bundled strategy into its configurable run checks."""

    normalized = str(strategy_name or "none").strip().lower() or "none"
    from automation.strategies import get_strategy

    strategy = get_strategy(normalized)
    requirements = strategy.session_preflight_requirements() if strategy else {}
    return {
        "strategy": str(strategy.name if strategy else normalized).strip().lower(),
        "checks": startup_gate_check_catalog(requirements),
    }


def build_gate_decision_options(
    check_id: str,
    configured_fallbacks: Sequence[Mapping[str, Any]] = (),
    *,
    advisory: bool = False,
) -> list[dict[str, str]]:
    """Return safe operator choices for one failed requirement."""

    normalized_check = str(check_id or "startup_setup").strip() or "startup_setup"
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    if not isinstance(configured_fallbacks, Sequence) or isinstance(
        configured_fallbacks,
        (str, bytes),
    ):
        configured_fallbacks = ()
    for raw in configured_fallbacks:
        if not isinstance(raw, Mapping):
            continue
        option_id = str(raw.get("id") or "").strip().lower()
        label = str(raw.get("label") or "").strip()
        description = str(raw.get("description") or "").strip()
        if not option_id or not label or option_id in seen:
            continue
        option = {
            "id": option_id,
            "label": label,
            "action": "waive",
            "kind": "fallback",
        }
        if description:
            option["description"] = description
        value = str(raw.get("value") or "").strip()
        if value:
            option["value"] = value
        options.append(option)
        seen.add(option_id)

    if advisory:
        return [
            {
                "id": "pause_for_changes",
                "label": "Pause for manual changes",
                "description": (
                    "Pause automation without ending the Tournament; resume "
                    "after changing the setting to review the warning again."
                ),
                "action": "pause",
                "kind": "standard",
            },
            {
                "id": "retry",
                "label": "Retry the read-only check",
                "description": "Re-run the observer check with fresh evidence.",
                "action": "retry",
                "kind": "standard",
            },
            {
                "id": "continue_observing",
                "label": f"Continue despite {normalized_check}",
                "description": (
                    "Acknowledge only this mismatch; Tournament result capture "
                    "continues."
                ),
                "action": "waive",
                "kind": "standard",
            },
        ]

    defaults = (
        {
            "id": "retry",
            "label": "Retry the required check",
            "description": "Run the normal requirement again without a waiver.",
            "action": "retry",
            "kind": "standard",
        },
        {
            "id": "bypass_once",
            "label": f"Bypass {normalized_check} for this run",
            "description": "Waive only this check; every other startup check still runs.",
            "action": "waive",
            "kind": "standard",
        },
    )
    for option in defaults:
        if option["id"] not in seen:
            options.append(dict(option))
    return options


def prompt_for_gate_decision(
    decision: Mapping[str, Any],
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> str | None:
    """Prompt for one published gate decision and return its option id."""

    options = [
        option
        for option in decision.get("options") or ()
        if isinstance(option, Mapping)
        and str(option.get("id") or "").strip()
        and str(option.get("label") or "").strip()
    ]
    if not options:
        return None

    output_fn("")
    output_fn("Startup gate requires a decision")
    output_fn(f"Check: {decision.get('check_id') or 'unknown'}")
    output_fn(f"Issue: {decision.get('reason') or 'requirement failed'}")
    expected = str(decision.get("expected") or "").strip()
    if expected:
        output_fn(f"Required: {expected}")
    for index, option in enumerate(options, start=1):
        description = str(option.get("description") or "").strip()
        suffix = f" — {description}" if description else ""
        output_fn(f"  {index}) {option['label']}{suffix}")

    while True:
        try:
            raw = input_fn(f"Choose 1-{len(options)} (Enter leaves it pending): ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("")
            return None
        if not raw:
            return None
        try:
            selected = int(raw)
        except ValueError:
            output_fn("Enter the number of one available choice.")
            continue
        if 1 <= selected <= len(options):
            return str(options[selected - 1]["id"])
        output_fn("That choice is outside the available range.")


__all__ = [
    "STARTUP_GATE_CHECK_LABELS",
    "VALID_GATE_DECISION_ACTIONS",
    "build_gate_decision_options",
    "prompt_for_gate_decision",
    "startup_gate_check_catalog",
    "startup_gate_context_for_strategy",
]
