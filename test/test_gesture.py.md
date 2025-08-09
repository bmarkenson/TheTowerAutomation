$PROJECT_ROOT/test/test_gesture.py — Entrypoint
test.test_gesture.run_gesture(dot_path) — Returns: True if a gesture executes (visual tap success for match_template; otherwise static tap/swipe executed); Side effects: [cv2][tap][swipe][adb][log]; Errors: resolve failure logged and returns False; ADB/tap errors may surface via called utilities.
test.test_gesture.main() — Returns: action result (runs one gesture); Side effects: [cv2][tap][swipe][adb][log]; Errors: process exits with code 1 on failure; CLI: dot_path.
