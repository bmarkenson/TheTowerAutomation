"""Wave number detection helpers."""

from __future__ import annotations

from .cli import main as _cli_main
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
    "main",
    "get_wave_number",
    "get_wave_number_from_image",
]


def main() -> None:
    """CLI entrypoint kept for backwards compatibility."""

    _cli_main()
