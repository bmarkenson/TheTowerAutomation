$PROJECT_ROOT/core/floating_button_detector.py — Library
core.floating_button_detector.tap_floating_button(name, buttons) — Returns: True if the named floating_button was tapped; False if not found; Side effects: [adb][log]; Errors: CalledProcessError when ADB command fails (via adb_shell).
core.floating_button_detector.detect_floating_buttons(screen) — Returns: list of detected buttons with {name, match_region, confidence, tap_point}; Side effects: [cv2][fs][state][log]; Errors: Per-entry exceptions are caught and logged; function returns partial results.
