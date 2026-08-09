from datetime import datetime, timezone
import json
import os
from pathlib import Path
import struct
from unittest.mock import call, patch

import cv2
import numpy as np
import pytest

from core.ss_capture import (
    ScreenshotCaptureResult,
    ScreenshotFailure,
    _decode_raw_screencap,
    capture_adb_raw_screenshot,
    capture_adb_screenshot,
    capture_adb_screenshot_result,
    capture_and_save_screenshot_result,
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


def _complete_frame(value: int = 32) -> np.ndarray:
    return np.full((1920, 1080, 3), value, dtype=np.uint8)


def _owned_temporary_files(target: Path) -> list[Path]:
    return list(target.parent.glob(f".{target.name}.*.tmp"))


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

    with (
        patch(
            "core.ss_capture.screencap_png",
            side_effect=[incomplete_png.tobytes(), complete_png.tobytes()],
        ) as capture,
        patch("core.ss_capture.log") as runtime_log,
    ):
        frame = capture_adb_screenshot()

    assert frame is not None
    assert np.array_equal(frame, complete)
    assert capture.call_count == 2
    assert runtime_log.call_args_list == [
        call(
            "[ADB] Incomplete PNG screenshot (1/2); retrying with a fresh "
            "capture",
            "DEBUG",
        ),
        call(
            "[ADB] PNG screenshot capture recovered on attempt 2/2 after "
            "rejecting an incomplete frame",
            "DEBUG",
        ),
    ]


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

    with (
        patch(
            "core.ss_capture.screencap_png",
            return_value=encoded.tobytes(),
        ) as capture,
        patch("core.ss_capture.log") as runtime_log,
    ):
        frame = capture_adb_screenshot()

    assert frame is None
    assert capture.call_count == 2
    assert runtime_log.call_args_list == [
        call(
            "[ADB] Incomplete PNG screenshot (1/2); retrying with a fresh "
            "capture",
            "DEBUG",
        ),
        call(
            "[ADB] Incomplete PNG screenshot (2/2); capture rejected",
            "WARN",
        ),
    ]


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
    capture.assert_called_once_with(
        device_id="localhost:5555",
        report_errors=False,
    )


def test_successful_custom_capture_atomically_replaces_complete_png(tmp_path):
    target = tmp_path / "captures" / "frame.png"
    target.parent.mkdir()
    target.write_bytes(b"prior complete artifact")
    frame = _complete_frame(47)
    real_replace = os.replace
    replacements = []

    def record_replace(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.parent.resolve() == destination_path.parent.resolve()
        assert source_path.exists()
        replacements.append((source_path, destination_path))
        real_replace(source, destination)

    with (
        patch(
            "core.ss_capture.capture_adb_screenshot_result",
            return_value=ScreenshotCaptureResult(frame),
        ),
        patch("core.ss_capture.os.replace", side_effect=record_replace),
        patch("core.ss_capture.log") as runtime_log,
    ):
        result = capture_and_save_screenshot_result(target, log_capture=False)

    assert result.frame is frame
    assert len(replacements) == 1
    assert replacements[0][1] == target
    assert not replacements[0][0].exists()
    assert not _owned_temporary_files(target)
    assert not target.with_suffix(".json").exists()
    decoded = cv2.imread(str(target), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert np.array_equal(decoded, frame)
    runtime_log.assert_not_called()


def test_encoding_failure_preserves_existing_png(tmp_path):
    target = tmp_path / "frame.png"
    previous = b"previous PNG evidence"
    target.write_bytes(previous)

    with (
        patch(
            "core.ss_capture.capture_adb_screenshot_result",
            return_value=ScreenshotCaptureResult(_complete_frame()),
        ),
        patch("core.ss_capture.cv2.imencode", return_value=(False, None)),
        pytest.raises(OSError, match="failed to encode screenshot"),
    ):
        capture_and_save_screenshot_result(target, log_capture=False)

    assert target.read_bytes() == previous
    assert not _owned_temporary_files(target)


def test_temporary_write_failure_preserves_existing_png_and_cleans_up(tmp_path):
    target = tmp_path / "frame.png"
    previous = b"previous PNG evidence"
    target.write_bytes(previous)

    with (
        patch(
            "core.ss_capture.capture_adb_screenshot_result",
            return_value=ScreenshotCaptureResult(_complete_frame()),
        ),
        patch(
            "core.ss_capture._write_temporary_payload",
            side_effect=OSError("simulated temporary write failure"),
        ),
        pytest.raises(OSError, match="simulated temporary write failure"),
    ):
        capture_and_save_screenshot_result(target, log_capture=False)

    assert target.read_bytes() == previous
    assert not _owned_temporary_files(target)


def test_replacement_failure_preserves_existing_png_and_cleans_up(tmp_path):
    target = tmp_path / "frame.png"
    previous = b"previous PNG evidence"
    target.write_bytes(previous)

    with (
        patch(
            "core.ss_capture.capture_adb_screenshot_result",
            return_value=ScreenshotCaptureResult(_complete_frame()),
        ),
        patch(
            "core.ss_capture.os.replace",
            side_effect=OSError("simulated replacement failure"),
        ),
        pytest.raises(OSError, match="simulated replacement failure"),
    ):
        capture_and_save_screenshot_result(target, log_capture=False)

    assert target.read_bytes() == previous
    assert not _owned_temporary_files(target)


def test_capture_failure_leaves_latest_png_and_metadata_untouched(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    png_path = screenshot_dir / "latest.png"
    metadata_path = screenshot_dir / "latest.json"
    previous_png = b"previous PNG evidence"
    previous_metadata = b'{"previous": true}\n'
    png_path.write_bytes(previous_png)
    metadata_path.write_bytes(previous_metadata)
    failure = ScreenshotCaptureResult(
        None,
        ScreenshotFailure.EMPTY,
        "empty screenshot data",
    )

    with (
        patch(
            "core.ss_capture.capture_adb_screenshot_result",
            return_value=failure,
        ),
        patch("core.ss_capture.os.replace") as replace,
    ):
        result = capture_and_save_screenshot_result(log_capture=False)

    assert result is failure
    assert png_path.read_bytes() == previous_png
    assert metadata_path.read_bytes() == previous_metadata
    assert not _owned_temporary_files(png_path)
    assert not _owned_temporary_files(metadata_path)
    replace.assert_not_called()


def test_latest_sidecar_reports_normalized_capture_and_is_atomically_replaced(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    png_path = screenshot_dir / "latest.png"
    metadata_path = screenshot_dir / "latest.json"
    png_path.write_bytes(b"previous PNG evidence")
    metadata_path.write_text('{"schema_version": 0}\n', encoding="utf-8")

    source = np.full((1280, 720, 3), 63, dtype=np.uint8)
    encoded_ok, encoded = cv2.imencode(".png", source)
    assert encoded_ok
    captured_at = datetime(
        2026,
        8,
        4,
        18,
        19,
        20,
        123456,
        tzinfo=timezone.utc,
    )
    adb_target = "localhost:5565"
    real_replace = os.replace
    replacements = []

    def record_replace(source_path, destination_path):
        source_path = Path(source_path)
        destination_path = Path(destination_path)
        assert source_path.parent.resolve() == destination_path.parent.resolve()
        assert source_path.exists()
        replacements.append(destination_path.resolve())
        real_replace(source_path, destination_path)

    with (
        patch("core.ss_capture.resolve_adb_device", return_value=adb_target),
        patch(
            "core.ss_capture.screencap_png",
            return_value=encoded.tobytes(),
        ) as capture,
        patch("core.ss_capture.datetime") as datetime_type,
        patch("core.ss_capture.os.replace", side_effect=record_replace),
    ):
        datetime_type.now.return_value = captured_at
        result = capture_and_save_screenshot_result(log_capture=False)

    assert result.frame is not None
    assert result.frame.shape == (1920, 1080, 3)
    assert result.captured_at == captured_at
    assert result.adb_target == adb_target
    assert (result.native_width, result.native_height) == (720, 1280)
    capture.assert_called_once_with(
        device_id=adb_target,
        report_errors=True,
    )
    datetime_type.now.assert_called_once_with(timezone.utc)

    decoded = cv2.imread(str(png_path), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert np.array_equal(decoded, result.frame)
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "captured_at": "2026-08-04T18:19:20.123456Z",
        "adb_target": adb_target,
        "native_width": 720,
        "native_height": 1280,
        "canonical_width": 1080,
        "canonical_height": 1920,
    }
    assert replacements == [png_path.resolve(), metadata_path.resolve()]
    assert not _owned_temporary_files(png_path)
    assert not _owned_temporary_files(metadata_path)


def test_sidecar_failure_keeps_valid_frame_and_published_png(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    png_path = screenshot_dir / "latest.png"
    metadata_path = screenshot_dir / "latest.json"
    png_path.write_bytes(b"previous PNG evidence")
    previous_metadata = b'{"schema_version": 0}\n'
    metadata_path.write_bytes(previous_metadata)
    frame = _complete_frame(79)
    capture_result = ScreenshotCaptureResult(
        frame,
        captured_at=datetime(2026, 8, 4, 19, tzinfo=timezone.utc),
        adb_target="localhost:5555",
        native_width=1080,
        native_height=1920,
    )
    real_replace = os.replace

    def fail_metadata_replace(source, destination):
        if Path(destination).suffix == ".json":
            raise OSError("simulated metadata replacement failure")
        real_replace(source, destination)

    with (
        patch(
            "core.ss_capture.capture_adb_screenshot_result",
            return_value=capture_result,
        ),
        patch(
            "core.ss_capture.os.replace",
            side_effect=fail_metadata_replace,
        ),
        patch("core.ss_capture.log") as runtime_log,
    ):
        result = capture_and_save_screenshot_result(log_capture=False)

    assert result is capture_result
    assert result.frame is frame
    decoded = cv2.imread(str(png_path), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert np.array_equal(decoded, frame)
    assert metadata_path.read_bytes() == previous_metadata
    assert not _owned_temporary_files(png_path)
    assert not _owned_temporary_files(metadata_path)
    runtime_log.assert_called_once()
    assert "could not publish advisory metadata" in runtime_log.call_args.args[0]
    assert runtime_log.call_args.args[1] == "ERROR"


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
        patch("core.ss_capture.log") as runtime_log,
    ):
        frame = capture_adb_raw_screenshot()

    assert frame is complete
    assert capture.call_count == 2
    assert runtime_log.call_args_list == [
        call(
            "[ADB] Incomplete raw screenshot (1/2); retrying with a fresh "
            "capture",
            "DEBUG",
        ),
        call(
            "[ADB] raw screenshot capture recovered on attempt 2/2 after "
            "rejecting an incomplete frame",
            "DEBUG",
        ),
    ]


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
