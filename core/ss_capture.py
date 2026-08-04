#!/usr/bin/env python3
"""ADB screenshot helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import struct
import tempfile
from typing import BinaryIO, Optional

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
    captured_at: Optional[datetime] = None
    adb_target: Optional[str] = None
    native_width: Optional[int] = None
    native_height: Optional[int] = None


LATEST_SCREENSHOT = Path("screenshots/latest.png")
LATEST_SCREENSHOT_METADATA = Path("screenshots/latest.json")
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
            png_data = screencap_png(
                device_id=device_id,
                report_errors=report_adb_errors,
            )
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

            captured_at = datetime.now(timezone.utc)
            native_height, native_width = img.shape[:2]
            img = normalize_device_screenshot(img, device_id=device_id)
            if img is not None:
                return ScreenshotCaptureResult(
                    img,
                    captured_at=captured_at,
                    adb_target=device_id,
                    native_width=native_width,
                    native_height=native_height,
                )
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


def _atomic_replace_bytes(target: Path, payload: bytes) -> None:
    """Write bytes beside ``target`` and publish them with one replacement."""

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            _write_temporary_payload(temporary, payload)
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _write_temporary_payload(temporary: BinaryIO, payload: bytes) -> None:
    written = temporary.write(payload)
    if written != len(payload):
        raise OSError(
            f"Incomplete temporary screenshot write: {written}/{len(payload)} bytes"
        )
    temporary.flush()


def _atomic_write_png(target: Path, frame: Frame) -> None:
    """Encode and atomically publish one canonical screenshot frame."""

    canonical_width, canonical_height = CANONICAL_SCREEN_SIZE
    if (
        not isinstance(frame, np.ndarray)
        or frame.ndim != 3
        or frame.shape[0] != canonical_height
        or frame.shape[1] != canonical_width
        or frame.shape[2] < 3
    ):
        raise ValueError("Published screenshot must use canonical screen geometry")

    encoded_ok, encoded = cv2.imencode(".png", frame)
    if not encoded_ok or encoded is None:
        raise OSError(f"OpenCV failed to encode screenshot for {target}")
    _atomic_replace_bytes(target, encoded.tobytes())


def _capture_metadata(result: ScreenshotCaptureResult) -> dict[str, object]:
    if (
        result.captured_at is None
        or result.captured_at.tzinfo is None
        or result.captured_at.utcoffset() is None
    ):
        raise ValueError("capture time is unavailable or not timezone-aware")
    if not result.adb_target:
        raise ValueError("resolved ADB target is unavailable")
    if result.native_width is None or result.native_height is None:
        raise ValueError("native screenshot geometry is unavailable")

    canonical_width, canonical_height = CANONICAL_SCREEN_SIZE
    captured_at = result.captured_at.astimezone(timezone.utc)
    return {
        "schema_version": 1,
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "adb_target": result.adb_target,
        "native_width": int(result.native_width),
        "native_height": int(result.native_height),
        "canonical_width": canonical_width,
        "canonical_height": canonical_height,
    }


def _is_latest_screenshot_path(target: Path) -> bool:
    return target.resolve(strict=False) == LATEST_SCREENSHOT.resolve(strict=False)


def _publish_latest_metadata(
    target: Path,
    result: ScreenshotCaptureResult,
) -> None:
    payload = json.dumps(
        _capture_metadata(result),
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    _atomic_replace_bytes(target, payload)


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
        _atomic_write_png(target, result.frame)
        if log_capture:
            log(
                f"Captured screenshot: shape={result.frame.shape}, path={target}",
                level="DEBUG",
            )
        if _is_latest_screenshot_path(target):
            metadata_target = target.with_name(LATEST_SCREENSHOT_METADATA.name)
            try:
                _publish_latest_metadata(metadata_target, result)
            except Exception as exc:
                log(
                    "Captured a usable screenshot but could not publish "
                    f"advisory metadata to {metadata_target}: {exc}",
                    "ERROR",
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
        - "PNG encoding or atomic publication errors may propagate"
      params:
        path: "str — output PNG path (parents created)"
        log_capture: "bool — when False, suppress DEBUG log after save"
      notes:
        - "Delegates capture to capture_adb_screenshot()"
        - "Atomically replaces the PNG if capture succeeds"
        - "The default latest path also gets best-effort advisory JSON metadata"
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
