core/ss_capture.py
core.ss_capture.capture_adb_screenshot() — R: OpenCV BGR ndarray of current device/emulator screen (or None on failure); S: [adb][cv2][log]; E: Returns None when PNG capture or decode fails; logs errors via utils.logger.log.
core.ss_capture.capture_and_save_screenshot(path=LATEST_SCREENSHOT) — R: same image ndarray as capture_adb_screenshot (or None); S: [adb][cv2][fs][log]; Defaults: saves to screenshots/latest.png; E: Returns None if capture fails; creates parent directories when saving.
core.ss_capture.main() — R: action result (UI preview only); S: [adb][cv2][log]; Displays captured screenshot in a preview window when run as a script.
