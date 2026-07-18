import struct

import numpy as np
import pytest

from core.ss_capture import _decode_raw_screencap, is_complete_screenshot


def test_complete_screenshot_rejects_wrong_size_and_majority_black_frames():
    assert not is_complete_screenshot(None)
    assert not is_complete_screenshot(np.full((100, 100, 3), 255, dtype=np.uint8))

    mostly_black = np.zeros((1920, 1080, 3), dtype=np.uint8)
    mostly_black[:200] = 255
    assert not is_complete_screenshot(mostly_black)

    complete = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    assert is_complete_screenshot(complete)


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
