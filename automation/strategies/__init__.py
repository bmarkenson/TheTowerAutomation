from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import BaseStrategy
from .yaml_strategy import YamlStrategy

UNKNOWN_STRATEGY_MSG = (
    "Unknown strategy; use gc, gc_farm_t18, gc_farm_t19_experiment, none, "
    "or provide --strategy-config with a YAML plan."
)

_BUNDLED_STRATEGY_PROFILES = {
    "gc": "gc_farm_t18",
    "gc_farm_t18": "gc_farm_t18",
    "gc_farm_t19_experiment": "gc_farm_t19_experiment",
    # Compatibility names all resolve to an explicit generated profile.
    "gc_skipper": "gc_farm_t18",
    "glass_cannon": "gc_farm_t18",
    "gc_manual_target_priority": "gc_farm_t19_experiment",
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
