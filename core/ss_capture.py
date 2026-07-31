#!/usr/bin/env python3
"""ADB screenshot helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import struct
from typing import Optional

import cv2
import numpy as np
from numpy.typing import NDArray

from utils.logger import log
from core.adb_utils import resolve_adb_device, screencap_png, screencap_raw
from core.screen_geometry import (
    CANONICAL_SCREEN_SIZE,
    record_device_screen_size,
)


Frame = NDArray[np.uint8]


class ScreenshotFailure(str, Enum):
    """Classify why a screenshot produced no usable frame."""

    EMPTY = "empty"
    MALFORMED = "malformed"
    INCOMPLETE = "incomplete"
    ERROR = "error"


@dataclass(frozen=True)
class ScreenshotCaptureResult:
    """Return a frame or a structured capture failure."""

    frame: Optional[Frame]
    failure: Optional[ScreenshotFailure] = None
    detail: Optional[str] = None


LATEST_SCREENSHOT = Path("screenshots/latest.png")
INCOMPLETE_CAPTURE_ATTEMPTS = 2


def is_complete_screenshot(frame: Optional[Frame]) -> bool:
    """Reject malformed or majority-black canonical action evidence."""

    canonical_width, canonical_height = CANONICAL_SCREEN_SIZE
    return bool(
        isinstance(frame, np.ndarray)
        and frame.ndim == 3
        and frame.shape[0] == canonical_height
        and frame.shape[1] == canonical_width
        and frame.shape[2] >= 3
        and _has_complete_pixels(frame)
    )


def normalize_device_screenshot(
    frame: Frame,
    *,
    device_id: Optional[str] = None,
) -> Optional[Frame]:
    """Validate a native frame and normalize supported sizes to 1080x1920."""

    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError("Screenshot must be a color image")
    source_height, source_width = frame.shape[:2]
    record_device_screen_size(
        source_width,
        source_height,
        device_id=device_id,
    )
    if not _has_complete_pixels(frame):
        return None
    canonical_width, canonical_height = CANONICAL_SCREEN_SIZE
    if (source_width, source_height) == CANONICAL_SCREEN_SIZE:
        return frame
    return cv2.resize(
        frame,
        (canonical_width, canonical_height),
        interpolation=cv2.INTER_LINEAR,
    )


def _has_complete_pixels(frame: Frame) -> bool:
    return float(np.mean(np.max(frame[:, :, :3], axis=2) < 8)) < 0.5


def _decode_raw_screencap(raw_data: bytes) -> Frame:
    """Decode Android ``screencap`` RGBA/RGBX/BGRA framebuffer bytes."""

    if len(raw_data) < 12:
        raise ValueError("Raw screenshot header is incomplete")

    width, height, pixel_format = struct.unpack_from("<III", raw_data)
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid raw screenshot dimensions {width}x{height}")

    expected_pixels = width * height * 4
    header_size = len(raw_data) - expected_pixels
    if header_size not in {12, 16}:
        raise ValueError(
            f"Unexpected raw screenshot size {len(raw_data)} for {width}x{height}"
        )

    pixels = np.frombuffer(raw_data, dtype=np.uint8, offset=header_size)
    frame = pixels.reshape((height, width, 4))
    if pixel_format in {1, 2}:  # RGBA_8888 / RGBX_8888
        return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
    if pixel_format == 5:  # BGRA_8888
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    raise ValueError(f"Unsupported raw screenshot pixel format {pixel_format}")


def capture_adb_raw_screenshot() -> Optional[Frame]:
    """Capture a fresh, complete uncompressed Android framebuffer."""

    try:
        device_id = resolve_adb_device()
        for attempt in range(1, INCOMPLETE_CAPTURE_ATTEMPTS + 1):
            raw_data = screencap_raw()
            if not raw_data:
                log("[ADB] Empty raw screenshot data", "ERROR")
                return None
            image = normalize_device_screenshot(
                _decode_raw_screencap(raw_data),
                device_id=device_id,
            )
            if image is not None:
                return image
            _log_incomplete_capture("raw", attempt)
        return None
    except Exception as exc:
        log(f"[ADB] Raw screenshot capture failed: {exc}", "ERROR")
        return None


def capture_adb_screenshot_result(
    *,
    log_empty: bool = True,
    report_adb_errors: bool = True,
) -> ScreenshotCaptureResult:
    """Capture one screenshot with a structured transport/content outcome."""

    try:
        device_id = resolve_adb_device()
        for attempt in range(1, INCOMPLETE_CAPTURE_ATTEMPTS + 1):
            png_data = screencap_png(report_errors=report_adb_errors)
            if not png_data:
                detail = "empty screenshot data"
                if log_empty:
                    log("[ADB] Empty screenshot data", "ERROR")
                return ScreenshotCaptureResult(
                    None,
                    ScreenshotFailure.EMPTY,
                    detail,
                )

            if not png_data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError("Invalid screenshot data (not PNG)")

            img_array = np.frombuffer(png_data, dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("OpenCV failed to decode image")

            img = normalize_device_screenshot(img, device_id=device_id)
            if img is not None:
                return ScreenshotCaptureResult(img)
            _log_incomplete_capture("PNG", attempt)
        return ScreenshotCaptureResult(
            None,
            ScreenshotFailure.INCOMPLETE,
            "two incomplete screenshots",
        )
    except ValueError as exc:
        log(f"[ADB] Screenshot capture failed: {exc}", "ERROR")
        return ScreenshotCaptureResult(
            None,
            ScreenshotFailure.MALFORMED,
            str(exc),
        )
    except Exception as exc:
        log(f"[ADB] Screenshot capture failed: {exc}", "ERROR")
        return ScreenshotCaptureResult(
            None,
            ScreenshotFailure.ERROR,
            str(exc),
        )


def capture_adb_screenshot() -> Optional[Frame]:
    """
    ---
    spec:
      r: "np.ndarray | None (BGR)"
      s: ["adb", "cv2", "log"]
      e:
        - "Returns None on capture/decode failure or two incomplete frames"
      params: {}
      notes:
        - "Uses core.adb_utils.screencap_png() → PNG bytes"
        - "Validates PNG signature before decode"
        - "Decodes via cv2.imdecode to BGR ndarray"
        - "Retries once when a decoded frame is majority-black"
    ---
    Capture a screenshot from the connected ADB device/emulator and decode to an OpenCV BGR image.

    Returns:
        np.ndarray (BGR) on success, or None on failure.
    """
    return capture_adb_screenshot_result().frame


def _log_incomplete_capture(source: str, attempt: int) -> None:
    if attempt < INCOMPLETE_CAPTURE_ATTEMPTS:
        outcome = "retrying with a fresh capture"
    else:
        outcome = "capture rejected"
    log(
        f"[ADB] Incomplete {source} screenshot "
        f"({attempt}/{INCOMPLETE_CAPTURE_ATTEMPTS}); {outcome}",
        "WARN",
    )


def capture_and_save_screenshot_result(
    path: Path | str = LATEST_SCREENSHOT,
    *,
    log_capture: bool = True,
    log_empty: bool = True,
    report_adb_errors: bool = True,
) -> ScreenshotCaptureResult:
    """Capture and save a screenshot while retaining its failure kind."""

    target = Path(path)
    result = capture_adb_screenshot_result(
        log_empty=log_empty,
        report_adb_errors=report_adb_errors,
    )
    if result.frame is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(target), result.frame)
        if log_capture:
            log(
                f"Captured screenshot: shape={result.frame.shape}, path={target}",
                level="DEBUG",
            )
    return result


def capture_and_save_screenshot(
    path: Path | str = LATEST_SCREENSHOT,
    *,
    log_capture: bool = True,
) -> Optional[Frame]:
    """
    ---
    spec:
      r: "np.ndarray | None (BGR)"
      s: ["adb", "cv2", "fs", "log"]
      e:
        - "Returns None if capture fails"
        - "OSError may propagate from os.makedirs/cv2.imwrite on filesystem errors"
      params:
        path: "str — output PNG path (parents created)"
        log_capture: "bool — when False, suppress DEBUG log after save"
      notes:
        - "Delegates capture to capture_adb_screenshot()"
        - "Writes PNG to disk if capture succeeds"
    ---
    Capture a screenshot and save it to disk.

    Args:
        path: Output PNG path; parent directories will be created if needed.
        log_capture (bool): When False, suppress the debug log after saving.

    Returns:
        np.ndarray (BGR) on success, or None on failure.
    """
    return capture_and_save_screenshot_result(
        path,
        log_capture=log_capture,
    ).frame


def main():
    """
    ---
    spec:
      r: "None"
      s: ["adb", "cv2", "log"]
      e: []
      params: {}
      notes:
        - "Utility viewer: captures, logs size, optionally resizes to fit height≈2048, displays via cv2.imshow"
        - "Blocks on key; closes window afterward"
    ---
    CLI/display helper: capture once, log size, show a window for inspection.
    """
    image = capture_adb_screenshot()
    if image is not None:
        log(f"[Info] Screenshot shape: {image.shape}", "INFO")

        # Resize for screen display if too large (e.g., fit height to 2048px)
        max_height = 2048
        scale = min(1.0, max_height / image.shape[0])
        if scale < 1.0:
            resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            log(f"Resized for display: {resized.shape}", "INFO")
        else:
            resized = image

        cv2.imshow("ADB Screenshot", resized)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    else:
        log("Failed to capture or decode screenshot", "ERROR")


if __name__ == "__main__":
    main()
