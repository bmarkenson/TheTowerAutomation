import struct
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from core.ss_capture import (
    ScreenshotFailure,
    _decode_raw_screencap,
    capture_adb_raw_screenshot,
    capture_adb_screenshot,
    capture_adb_screenshot_result,
    is_complete_screenshot,
    normalize_device_screenshot,
)
from core.screen_geometry import (
    clear_recorded_device_screen_sizes,
    get_device_screen_size,
)


@pytest.fixture(autouse=True)
def _clear_screen_geometry():
    clear_recorded_device_screen_sizes()
    yield
    clear_recorded_device_screen_sizes()


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


def test_png_capture_normalizes_720p_and_records_input_geometry():
    source = np.full((1280, 720, 3), 32, dtype=np.uint8)
    encoded_ok, encoded = cv2.imencode(".png", source)
    assert encoded_ok

    with patch("core.ss_capture.screencap_png", return_value=encoded.tobytes()):
        frame = capture_adb_screenshot()

    assert frame is not None
    assert frame.shape == (1920, 1080, 3)
    assert np.all(frame == 32)
    assert get_device_screen_size(device_id="localhost:5555") == (720, 1280)


def test_normalization_rejects_unsupported_resolution():
    with pytest.raises(ValueError, match="Unsupported emulator resolution 900x1600"):
        normalize_device_screenshot(
            np.full((1600, 900, 3), 32, dtype=np.uint8),
            device_id="localhost:5555",
        )


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


def test_png_capture_reports_connected_malformed_data():
    with (
        patch("core.ss_capture.screencap_png", return_value=b"not a PNG"),
        patch("core.ss_capture.log") as runtime_log,
    ):
        result = capture_adb_screenshot_result()

    assert result.frame is None
    assert result.failure is ScreenshotFailure.MALFORMED
    assert result.detail == "Invalid screenshot data (not PNG)"
    runtime_log.assert_called_once_with(
        "[ADB] Screenshot capture failed: Invalid screenshot data (not PNG)",
        "ERROR",
    )


def test_png_capture_can_silence_an_expected_transport_outage():
    with (
        patch("core.ss_capture.screencap_png", return_value=None) as capture,
        patch("core.ss_capture.log") as runtime_log,
    ):
        result = capture_adb_screenshot_result(
            log_empty=False,
            report_adb_errors=False,
        )

    assert result.frame is None
    assert result.failure is ScreenshotFailure.EMPTY
    runtime_log.assert_not_called()
    capture.assert_called_once_with(report_errors=False)


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


def test_raw_capture_normalizes_720p_and_records_input_geometry():
    source = np.full((1280, 720, 3), 32, dtype=np.uint8)

    with (
        patch("core.ss_capture.screencap_raw", return_value=b"raw"),
        patch("core.ss_capture._decode_raw_screencap", return_value=source),
    ):
        frame = capture_adb_raw_screenshot()

    assert frame is not None
    assert frame.shape == (1920, 1080, 3)
    assert np.all(frame == 32)
    assert get_device_screen_size(device_id="localhost:5555") == (720, 1280)


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
