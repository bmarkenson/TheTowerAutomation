import struct

import numpy as np
import pytest

from core.ss_capture import _decode_raw_screencap


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
