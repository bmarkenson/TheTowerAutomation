# TheTower Automation Project Scope

## 🎮 Game
- **Title**: "The Tower: Idle Tower Defense"
- **Platform**: Pixel 4a via USB ADB
- **Host System**: Linux with Python + OpenCV + scrcpy

---

## 🟥 CORE REQUIREMENTS

- **24/7 unattended automation**
  - Must recover from ADB disconnects, unresponsive UI, and game crashes.
- **Visual state awareness**
  - Screens are analyzed before taking action using OpenCV template matching.
- **Reliable input injection**
  - All taps/swipes must register properly even with Unity-based input handling.
- **Manual override**
  - Automation can be paused or bypassed for scrcpy / NoMachine manual interaction.
- **Minimal tech stack**
  - Pure Python + ADB on Linux. No Android apps or emulators required.

---

## 🟧 CURRENT IMPLEMENTATION STATUS

- Modular architecture: `core/`, `handlers/`, `matchers/`, `automation/`
- Main loop (`main.py`) handles screenshot capture, state detection, and dispatch
- `state_detector.py` identifies key states using `clickmap.json` + region matchers
- Implemented state detection: `GAME_OVER`, `HOME_SCREEN`, `RUNNING`, `RESUME_GAME`
- Handlers: `handle_game_over`, `handle_home_screen`
- Tap injection handled via queue dispatcher (`tap_dispatcher.py`) with keepalive
- Tools: `crop_region.py`, `gesture_logger.py`, `tune_gesture.py`
- Resource monitoring via `log_meminfo.py` with thermal + logcat dumping

---

## 🟨 IN PROGRESS / NEAR FUTURE

- Add more screen matchers (e.g., upgrade ready, ad prompt)
- Expand handler behavior for WAIT, RETRY, HOME logic
- Image archival + session tagging improvements
- Debug watchdog false positives on foreground state
- Stability target: multi-day automation (current cap ~1–8 hrs)

---

## 🟦 FUTURE GOALS

- Implement decision-tree or state machine model
- CLI/GUI pause-resume toggle
- ADB reconnect resilience (USB or Wi-Fi)
- Optional dashboard/log viewer (low priority)
