import os
import numpy as np
import cv2
from utils.logger import log
from core.adb_utils import screencap_png

LATEST_SCREENSHOT = "screenshots/latest.png"

def capture_adb_screenshot():
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
            log("[ADB Error] Empty screenshot data", "ERROR")
            return None

        if not png_data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError("Invalid screenshot data (not PNG)")

        # Convert PNG bytes to OpenCV image
        img_array = np.frombuffer(png_data, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("OpenCV failed to decode image")

        return img

    except Exception as e:
        log(f"[Error] {e}", "ERROR")
        return None


def capture_and_save_screenshot(path=LATEST_SCREENSHOT, *, log_capture: bool = True):
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
    img = capture_adb_screenshot()
    if img is not None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cv2.imwrite(path, img)
        if log_capture:
            log(f"Captured and saved screenshot: shape={img.shape}, path={path}", level="DEBUG")
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
