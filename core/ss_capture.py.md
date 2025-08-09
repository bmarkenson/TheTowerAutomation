$PROJECT_ROOT/core/ss_capture.py — Library|Entrypoint
core.ss_capture.capture_adb_screenshot() — Returns: OpenCV BGR ndarray of current device/emulator screen (or None on failure); Side effects: [adb][cv2][log]; Errors: Returns None when PNG capture or decode fails; logs errors via utils.logger.log.
core.ss_capture.capture_and_save_screenshot(path=LATEST_SCREENSHOT) — Returns: same image ndarray as capture_adb_screenshot (or None); Side effects: [adb][cv2][fs][log]; Defaults: saves to screenshots/latest.png; Errors: Returns None if capture fails; creates parent directories when saving.
core.ss_capture.main() — Returns: action result (UI preview only); Side effects: [adb][cv2][log]; Displays captured screenshot in a preview window when run as a script.
