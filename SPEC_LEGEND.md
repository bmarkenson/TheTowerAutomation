
Tags (glossary + hygiene)
[adb]     — Executes device commands via ADB; may block; catches and returns None of failure
[cv2]     — Uses OpenCV; images are BGR ndarrays; template matching via cv2.matchTemplate.
[fs]      — Reads/writes files on disk.
[network] — Performs network I/O.
[state]   — Evaluates UI/game state from screen content.
[tap]     — Injects a tap/click on device/emulator.
[swipe]   — Injects a swipe gesture on device/emulator.
[log]     — Emits logs.
[loop]    — Repeating/infinite loop until interrupted.
[thread] — Spawns or manages a background thread.
[signal] — Uses Event/flag or signal for cooperative cancel.
[sleep] — Uses time.sleep/timeout waits.
Default match threshold = 0.90; Images are BGR; origin=(0,0) top-left.
Templates at assets/match_templates/; clickmap.json = static; state_definitions.yaml = logic.

Abbreviations
R=Returns | S=SideFX | E=Errors | CLI=flags

