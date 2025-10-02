"""Wave number detection helpers."""

from __future__ import annotations

from .cli import main as _cli_main
from .hint import get_hint_state, get_wave_hint, set_wave_hint
from .pipeline import (
    FALLBACK_DOT_PATH,
    PRIMARY_DOT_PATH,
    detect_wave_number,
    detect_wave_number_from_image,
    get_wave_number,
    get_wave_number_from_image,
)

__all__ = [
    "FALLBACK_DOT_PATH",
    "PRIMARY_DOT_PATH",
    "detect_wave_number",
    "detect_wave_number_from_image",
    "get_hint_state",
    "main",
    "get_wave_hint",
    "get_wave_number",
    "get_wave_number_from_image",
    "set_wave_hint",
]


def main() -> None:
    """CLI entrypoint kept for backwards compatibility."""

    _cli_main()
