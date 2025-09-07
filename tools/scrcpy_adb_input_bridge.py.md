tools/scrcpy_adb_input_bridge.py
tools.scrcpy_adb_input_bridge.ensure_scrcpy_window_rect(rect_source='top', diagnose=False, android_size=None) — R: (x, y, w, h) chosen from top/child/auto; S: [log]; E: RuntimeError if the window cannot be found.
tools.scrcpy_adb_input_bridge.get_android_screen_size() — R: (width, height) from capture_adb_screenshot(); S: [adb][cv2]; E: RuntimeError if capture fails.
tools.scrcpy_adb_input_bridge.get_scrcpy_window_rect(rect_source='top', diagnose=False, android_size=None) — R: (x, y, w, h) using the current selection policy; S: [log]; E: RuntimeError if window cannot be found.
tools.scrcpy_adb_input_bridge.map_to_android(x, y, window_rect, android_size) — R: (ax, ay) mapped Android coordinates with letterboxing handled; S: None; E: None.
tools.scrcpy_adb_input_bridge.send_tap(x, y) — R: action result (inject tap); S: [adb][log]; E: CalledProcessError when ADB command fails (via adb_shell).
tools.scrcpy_adb_input_bridge.send_swipe(x1, y1, x2, y2, duration_ms) — R: action result (inject swipe); S: [adb][log]; E: CalledProcessError when ADB command fails (via adb_shell).
tools.scrcpy_adb_input_bridge.get_pixel_color_at_android_coords(x, y) — R: (R, G, B) at Android coords or None on failure; S: [adb][cv2][log]; E: Exceptions caught and logged; returns None.
tools.scrcpy_adb_input_bridge.start_mouse_listener(android_size, args) — R: None (starts background listener thread); S: [loop][adb][log]; E: Non-fatal logging on window lookup errors; emits JSON lines when --json-stream is set.
tools.scrcpy_adb_input_bridge.launch_scrcpy() — R: None; S: starts scrcpy subprocess titled "scrcpy-bridge"; [log]; E: OSError if spawn fails.
tools.scrcpy_adb_input_bridge.cleanup_and_exit(signum=None, frame=None) — R: None; S: [log]; E: None (best-effort terminate/kill of scrcpy).
tools.scrcpy_adb_input_bridge.main() — R: action result (runs bridge until killed); S: [loop][adb][log]; E: Process exits on SIGINT/SIGTERM; CLI: --json-stream emits "__GESTURE_JSON__{...}" lines for gestures; --rect-source {top,child,auto} selects rect policy (default top); --rect-diagnose prints candidate/AR info.
