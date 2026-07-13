#!/usr/bin/env python3
"""ADB screenshot helpers."""

from __future__ import annotations

from pathlib import Path
import struct
from typing import Optional

import cv2
import numpy as np
from numpy.typing import NDArray

from utils.logger import log
from core.adb_utils import screencap_png, screencap_raw


Frame = NDArray[np.uint8]

LATEST_SCREENSHOT = Path("screenshots/latest.png")


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
    """Capture and decode the faster uncompressed Android framebuffer."""

    try:
        raw_data = screencap_raw()
        if not raw_data:
            log("[ADB] Empty raw screenshot data", "ERROR")
            return None
        image = _decode_raw_screencap(raw_data)
        expected_w, expected_h = 1080, 1920
        if image.shape[1] != expected_w or image.shape[0] != expected_h:
            raise ValueError(
                f"Unsupported emulator resolution {image.shape[1]}x{image.shape[0]}; "
                f"expected {expected_w}x{expected_h}. Update the BlueStacks display settings."
            )
        return image
    except Exception as exc:
        log(f"[ADB] Raw screenshot capture failed: {exc}", "ERROR")
        return None


def capture_adb_screenshot() -> Optional[Frame]:
    """
    ---
    spec:
      r: "np.ndarray | None (BGR)"
      s: ["adb", "cv2", "log"]
      e:
        - "Returns None on capture or decode failure; logs ERROR"
      params: {}
      notes:
        - "Uses core.adb_utils.screencap_png() → PNG bytes"
        - "Validates PNG signature before decode"
        - "Decodes via cv2.imdecode to BGR ndarray"
    ---
    Capture a screenshot from the connected ADB device/emulator and decode to an OpenCV BGR image.

    Returns:
        np.ndarray (BGR) on success, or None on failure.
    """
    try:
        png_data = screencap_png()
        if not png_data:
            log("[ADB] Empty screenshot data", "ERROR")
            return None

        if not png_data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError("Invalid screenshot data (not PNG)")

        # Convert PNG bytes to OpenCV image
        img_array = np.frombuffer(png_data, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("OpenCV failed to decode image")

        expected_w, expected_h = 1080, 1920
        if img.shape[1] != expected_w or img.shape[0] != expected_h:
            raise ValueError(
                f"Unsupported emulator resolution {img.shape[1]}x{img.shape[0]}; "
                f"expected {expected_w}x{expected_h}. Update the BlueStacks display settings."
            )

        return img

    except Exception as e:
        log(f"[ADB] Screenshot capture failed: {e}", "ERROR")
        return None


def capture_and_save_screenshot(path: Path | str = LATEST_SCREENSHOT, *, log_capture: bool = True) -> Optional[Frame]:
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
    target = Path(path)
    img = capture_adb_screenshot()
    if img is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(target), img)
        if log_capture:
            log(f"Captured screenshot: shape={img.shape}, path={target}", level="DEBUG")
    return img


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
