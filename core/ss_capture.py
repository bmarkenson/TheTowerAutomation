import subprocess
import numpy as np
import cv2
from utils.logger import log

LATEST_SCREENSHOT="screenshots/latest.png"

def capture_adb_screenshot():
    try:
        # Run adb to get screenshot data as raw PNG
        result = subprocess.run(
            ["adb", "exec-out", "screencap", "-p"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
        png_data = result.stdout
        if not png_data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError("Invalid screenshot data (not PNG)")

        # Convert PNG bytes to OpenCV image
        img_array = np.frombuffer(png_data, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("OpenCV failed to decode image")

        return img

    except subprocess.CalledProcessError as e:
        log(f"[ADB Error] {e.stderr.decode().strip()}", "ERROR")
    except Exception as e:
        print(f"[Error] {e}", "ERROR")
    return None

def capture_and_save_screenshot(path=LATEST_SCREENSHOT):
    img = capture_adb_screenshot()
    if img is not None:
        import cv2
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cv2.imwrite(path, img)
        log(f"Captured and saved screenshot: shape={img.shape}, path={path}", level="DEBUG")
    return img

if __name__ == "__main__":
    image = capture_adb_screenshot()
    if image is not None:
        log(f"[Info] Screenshot shape: {image.shape}", "INFO")

        # Resize for screen display if too large (e.g., fit height to 720px)
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

