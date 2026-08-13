"""Map supported device resolutions to the canonical 1080x1920 UI space."""

from __future__ import annotations

import threading
from typing import Optional, Tuple


CANONICAL_SCREEN_SIZE = (1080, 1920)
SUPPORTED_DEVICE_SCREEN_SIZES = frozenset(
    {
        CANONICAL_SCREEN_SIZE,
        (720, 1280),
    }
)

ScreenSize = Tuple[int, int]
Point = Tuple[int, int]

_screen_sizes: dict[str, ScreenSize] = {}
_screen_sizes_lock = threading.Lock()


class UnsupportedDeviceScreenSize(ValueError):
    """Report native geometry that cannot safely drive mapped UI input."""


def validate_device_screen_size(width: int, height: int) -> ScreenSize:
    """Return a supported device size or raise a diagnostic error."""

    size = (int(width), int(height))
    if size not in SUPPORTED_DEVICE_SCREEN_SIZES:
        supported = ", ".join(
            f"{supported_width}x{supported_height}"
            for supported_width, supported_height in sorted(
                SUPPORTED_DEVICE_SCREEN_SIZES
            )
        )
        raise UnsupportedDeviceScreenSize(
            f"Unsupported emulator resolution {size[0]}x{size[1]}; "
            f"supported resolutions are {supported}."
        )
    return size


def record_device_screen_size(
    width: int,
    height: int,
    *,
    device_id: Optional[str] = None,
) -> ScreenSize:
    """Record verified framebuffer geometry for subsequent input conversion."""

    size = validate_device_screen_size(width, height)
    with _screen_sizes_lock:
        _screen_sizes[_device_key(device_id)] = size
    return size


def get_device_screen_size(*, device_id: Optional[str] = None) -> ScreenSize:
    """Return observed geometry, defaulting safely to the canonical legacy size."""

    with _screen_sizes_lock:
        return _screen_sizes.get(_device_key(device_id), CANONICAL_SCREEN_SIZE)


def canonical_to_device_point(
    x: int | float,
    y: int | float,
    *,
    device_id: Optional[str] = None,
) -> Point:
    """Scale and clamp one canonical point into the active device pixel space."""

    device_width, device_height = get_device_screen_size(device_id=device_id)
    canonical_width, canonical_height = CANONICAL_SCREEN_SIZE
    device_x = round(float(x) * device_width / canonical_width)
    device_y = round(float(y) * device_height / canonical_height)
    return (
        max(0, min(device_width - 1, device_x)),
        max(0, min(device_height - 1, device_y)),
    )


def clear_recorded_device_screen_sizes() -> None:
    """Clear process-local observations; intended for isolated tests."""

    with _screen_sizes_lock:
        _screen_sizes.clear()


def _device_key(device_id: Optional[str]) -> str:
    return str(device_id or "__default__")


__all__ = [
    "CANONICAL_SCREEN_SIZE",
    "SUPPORTED_DEVICE_SCREEN_SIZES",
    "UnsupportedDeviceScreenSize",
    "canonical_to_device_point",
    "clear_recorded_device_screen_sizes",
    "get_device_screen_size",
    "record_device_screen_size",
    "validate_device_screen_size",
]
