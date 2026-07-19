import struct
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from core.ss_capture import (
    _decode_raw_screencap,
    capture_adb_raw_screenshot,
    capture_adb_screenshot,
    is_complete_screenshot,
)


def test_complete_screenshot_rejects_wrong_size_and_majority_black_frames():
    assert not is_complete_screenshot(None)
    assert not is_complete_screenshot(np.full((100, 100, 3), 255, dtype=np.uint8))

    mostly_black = np.zeros((1920, 1080, 3), dtype=np.uint8)
    mostly_black[:200] = 255
    assert not is_complete_screenshot(mostly_black)

    complete = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    assert is_complete_screenshot(complete)


def test_png_capture_retries_incomplete_frame_and_returns_fresh_complete_frame():
    incomplete = np.zeros((1920, 1080, 3), dtype=np.uint8)
    incomplete[:200] = 255
    complete = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    incomplete_ok, incomplete_png = cv2.imencode(".png", incomplete)
    complete_ok, complete_png = cv2.imencode(".png", complete)
    assert incomplete_ok and complete_ok

    with patch(
        "core.ss_capture.screencap_png",
        side_effect=[incomplete_png.tobytes(), complete_png.tobytes()],
    ) as capture:
        frame = capture_adb_screenshot()

    assert frame is not None
    assert np.array_equal(frame, complete)
    assert capture.call_count == 2


def test_png_capture_returns_none_when_fresh_retry_is_also_incomplete():
    incomplete = np.zeros((1920, 1080, 3), dtype=np.uint8)
    incomplete[:200] = 255
    encoded_ok, encoded = cv2.imencode(".png", incomplete)
    assert encoded_ok

    with patch(
        "core.ss_capture.screencap_png",
        return_value=encoded.tobytes(),
    ) as capture:
        frame = capture_adb_screenshot()

    assert frame is None
    assert capture.call_count == 2


def test_raw_capture_retries_incomplete_frame_before_returning_evidence():
    incomplete = np.zeros((1920, 1080, 3), dtype=np.uint8)
    incomplete[:200] = 255
    complete = np.full((1920, 1080, 3), 32, dtype=np.uint8)

    with (
        patch("core.ss_capture.screencap_raw", return_value=b"raw") as capture,
        patch(
            "core.ss_capture._decode_raw_screencap",
            side_effect=[incomplete, complete],
        ),
    ):
        frame = capture_adb_raw_screenshot()

    assert frame is complete
    assert capture.call_count == 2


def test_decode_raw_screencap_rgba_with_16_byte_header():
    header = struct.pack("<IIII", 2, 1, 1, 0)
    pixels = bytes((255, 0, 0, 255, 0, 255, 0, 255))

    frame = _decode_raw_screencap(header + pixels)

    assert frame.shape == (1, 2, 3)
    assert np.array_equal(frame[0, 0], (0, 0, 255))
    assert np.array_equal(frame[0, 1], (0, 255, 0))


def test_decode_raw_screencap_bgra_with_12_byte_header():
    header = struct.pack("<III", 1, 1, 5)
    pixels = bytes((255, 0, 0, 255))

    frame = _decode_raw_screencap(header + pixels)

    assert np.array_equal(frame[0, 0], (255, 0, 0))


def test_decode_raw_screencap_rejects_unknown_pixel_format():
    header = struct.pack("<III", 1, 1, 99)
    with pytest.raises(ValueError, match="pixel format 99"):
        _decode_raw_screencap(header + bytes((0, 0, 0, 0)))
