"""Profile-driven definitions for one-shot strategy validation battles."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class ExclusiveValidationDefinition:
    """Resolved policy and stable plan identity for one validation request."""

    strategy: str
    battle_kind: str
    timeout_seconds: float
    configuration_fingerprint: str
    ready_message: str
    failure_prefix: str


def exclusive_validation_definition(
    strategy: object,
) -> Optional[ExclusiveValidationDefinition]:
    """Return a validated exclusive-validation contract declared by a strategy."""

    if strategy is None:
        return None
    strategy_name = str(getattr(strategy, "name", "") or "").strip().lower()
    runtime_policy_fn = getattr(strategy, "runtime_policy", None)
    if not strategy_name or not callable(runtime_policy_fn):
        return None
    runtime_policy = runtime_policy_fn()
    if not isinstance(runtime_policy, Mapping):
        return None
    raw = runtime_policy.get("exclusive_validation")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("exclusive_validation runtime policy must be a mapping")

    battle_kind = str(raw.get("battle_kind") or "").strip().lower()
    if battle_kind != "ordinary_new_battle":
        raise ValueError(
            "exclusive_validation battle_kind must be ordinary_new_battle"
        )
    try:
        timeout_seconds = float(raw.get("timeout_seconds"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "exclusive_validation timeout_seconds must be numeric"
        ) from exc
    if not 30.0 <= timeout_seconds <= 900.0:
        raise ValueError(
            "exclusive_validation timeout_seconds must be between 30 and 900"
        )

    ready_message = str(raw.get("ready_message") or "").strip()
    failure_prefix = str(raw.get("failure_prefix") or "").strip()
    if not ready_message or not failure_prefix:
        raise ValueError(
            "exclusive_validation requires ready_message and failure_prefix"
        )

    plan = getattr(strategy, "config", None)
    if not isinstance(plan, Mapping):
        run_configuration_fn = getattr(strategy, "run_configuration", None)
        plan = {
            "runtime_policy": dict(runtime_policy),
            "run_configuration": (
                run_configuration_fn()
                if callable(run_configuration_fn)
                else {}
            ),
        }
    canonical = json.dumps(
        {
            "schema_version": 1,
            "strategy": strategy_name,
            "plan": plan,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    return ExclusiveValidationDefinition(
        strategy=strategy_name,
        battle_kind=battle_kind,
        timeout_seconds=timeout_seconds,
        configuration_fingerprint=fingerprint,
        ready_message=ready_message,
        failure_prefix=failure_prefix,
    )


def exclusive_validation_definition_for_strategy(
    strategy_name: str,
) -> Optional[ExclusiveValidationDefinition]:
    """Load one bundled strategy and resolve its validation definition."""

    from automation.strategies import get_strategy

    return exclusive_validation_definition(get_strategy(strategy_name))


__all__ = [
    "ExclusiveValidationDefinition",
    "exclusive_validation_definition",
    "exclusive_validation_definition_for_strategy",
]
