"""Pure configuration schema for Target Priority ordering."""

from __future__ import annotations

from collections.abc import Sequence


TARGET_PRIORITY_TARGETS = (
    "Fleets",
    "Boss",
    "Elites",
    "In Spotlight",
    "Tank",
    "Closest (Default)",
    "Ranged",
    "Protector",
    "Fast",
    "Basic",
)


def validate_target_priority_order(order: Sequence[str]) -> list[str]:
    """Return a concrete valid order or raise for missing/duplicate targets."""

    expected = list(TARGET_PRIORITY_TARGETS)
    actual = [str(item).strip() for item in order]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError(
            "Expected Target Priority order must contain every target exactly once"
        )
    return actual


__all__ = ["TARGET_PRIORITY_TARGETS", "validate_target_priority_order"]
