from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import BaseStrategy
from .yaml_strategy import YamlStrategy

UNKNOWN_STRATEGY_MSG = (
    "Unknown strategy; use farm, farm_t18, farm_t19, tournament, none, or a "
    "legacy alias, or provide --strategy-config with a YAML plan."
)

_BUNDLED_STRATEGY_PROFILES = {
    "farm": "farm_t18",
    "farm_t18": "farm_t18",
    "farm_t19": "farm_t19",
    "tournament": "tournament",
    # Compatibility names all resolve to an explicit generated profile.
    "farm_t19_experiment": "farm_t19",
    "gc": "farm_t18",
    "gc_farm_t18": "farm_t18",
    "gc_farm_t19": "farm_t19",
    "gc_farm_t19_experiment": "farm_t19",
    "gc_skipper": "farm_t18",
    "glass_cannon": "farm_t18",
    "gc_manual_target_priority": "farm_t19",
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
    raise ValueError(UNKNOWN_STRATEGY_MSG)
