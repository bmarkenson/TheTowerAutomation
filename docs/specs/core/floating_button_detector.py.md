
core/floating_button_detector.py
core.floating_button_detector.tap_floating_button(name, buttons) — R: True if the named floating_button was tapped; False if not found; S: [adb][log]; E: CalledProcessError when ADB command fails (via adb_shell).
core.floating_button_detector.detect_floating_buttons(screen) — R: list of detected buttons with {name, match_region, confidence, tap_point}; S: [cv2][fs][state][log]; E: Per-entry exceptions are caught and logged; function returns partial results.
