from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.strategy_profiles import (
    LEGACY_STRATEGY_ALIASES,
    load_published_strategy_plan,
)

from .base import BaseStrategy
from .yaml_strategy import YamlStrategy

UNKNOWN_STRATEGY_MSG = (
    "Unknown strategy; use a bundled or published custom profile, none, a "
    "legacy alias, or provide --strategy-config with a YAML plan."
)

_BUNDLED_STRATEGY_PROFILES = {
    "farm_t18": "farm_t18",
    "farm_t19": "farm_t19",
    "tournament": "tournament",
    **LEGACY_STRATEGY_ALIASES,
}


class NoOpStrategy(BaseStrategy):
    name = "none"


def get_strategy(name: str) -> Optional[BaseStrategy]:
    nm = (name or "").strip().lower()
    if nm in ("", "none"):
        return None
    profile_name = _BUNDLED_STRATEGY_PROFILES.get(nm)
    if profile_name:
        path = (
            Path(__file__).resolve().parents[2]
            / "config"
            / "strategies"
            / f"{profile_name}.strategy.yaml"
        )
        return YamlStrategy.from_file(str(path))
    custom_plan = load_published_strategy_plan(nm)
    if custom_plan is not None:
        return YamlStrategy(custom_plan)
    raise ValueError(UNKNOWN_STRATEGY_MSG)
