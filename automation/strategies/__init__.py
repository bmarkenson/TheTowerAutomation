from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import BaseStrategy
from .yaml_strategy import YamlStrategy

LEGACY_MSG = (
    "Python strategies have been removed; provide --strategy-config with a YAML plan instead."
)


class NoOpStrategy(BaseStrategy):
    name = "none"


def get_strategy(name: str) -> Optional[BaseStrategy]:
    nm = (name or "").strip().lower()
    if nm in ("", "none"):
        return None
    if nm in {"gc", "gc_skipper", "glass_cannon"}:
        path = (
            Path(__file__).resolve().parents[2]
            / "config"
            / "strategies"
            / "gc_skipper.strategy.yaml"
        )
        return YamlStrategy.from_file(str(path))
    raise ValueError(LEGACY_MSG)
