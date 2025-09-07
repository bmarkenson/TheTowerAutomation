
test/test_gesture.py
test.test_gesture.run_gesture(dot_path) — R: True if a gesture executes (visual tap success for match_template; otherwise static tap/swipe executed); S: [cv2][tap][swipe][adb][log]; E: resolve failure logged and returns False; ADB/tap errors may surface via called utilities.
test.test_gesture.main() — R: action result (runs one gesture); S: [cv2][tap][swipe][adb][log]; E: process exits with code 1 on failure; CLI: dot_path.
