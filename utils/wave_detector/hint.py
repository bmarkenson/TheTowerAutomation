"""Stateful helpers for tracking the last detected wave number."""

from __future__ import annotations

import time
from typing import Optional, Tuple

_LAST_WAVE_SEEN: Optional[int] = None
_LAST_WAVE_TS: Optional[float] = None


def set_wave_hint(val: Optional[int], ts: Optional[float] = None) -> None:
    """Seed the last-wave hint and optional timestamp used for proximity scoring."""
    global _LAST_WAVE_SEEN, _LAST_WAVE_TS
    _LAST_WAVE_SEEN = val
    _LAST_WAVE_TS = time.time() if ts is None else float(ts)


def get_wave_hint() -> Optional[int]:
    """Return the current wave hint, if any."""

    return _LAST_WAVE_SEEN


def get_hint_state() -> Tuple[Optional[int], Optional[float]]:
    """Return the full hint state as (last_wave, timestamp)."""

    return _LAST_WAVE_SEEN, _LAST_WAVE_TS
