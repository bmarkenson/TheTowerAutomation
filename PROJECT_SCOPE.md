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
  - Uses dual-path tap system:
    - `tap_dispatcher` for queued, low-priority, and periodic input
    - `tap_now()` / `swipe_now()` for immediate, feedback-dependent actions
- **Manual override**
  - Automation can be paused or bypassed for scrcpy / NoMachine manual interaction.
- **Minimal tech stack**
  - Pure Python + ADB on Linux. No Android apps or emulators required.

---

## 🟧 CURRENT IMPLEMENTATION STATUS

- Modular architecture: `core/`, `handlers/`, `matchers/`, `utils/`, `tools/`
- Main loop (`main.py`) handles screenshot capture, state detection, and dispatch
- `state_detector.py` uses `clickmap.json` + OpenCV region matchers to identify key states
- Implemented states: `GAME_OVER`, `HOME_SCREEN`, `RUNNING`, `RESUME_GAME`
- Initial support for overlays (e.g. floating gem, lab ready) under active development
- Handler modules: `handle_game_over`, `handle_home_screen`, and others (function-based, migrating to class-based)
- Tap injection split between `tap_dispatcher` (background taps) and `tap_now` / `swipe_now` (immediate)
- Watchdog monitors for backgrounded app, re-foregrounds game if needed
- Tools: `crop_region.py`, `gesture_logger.py`, `tune_gesture.py`
- Resource monitoring: `log_meminfo.py` logs memory/thermal stats and logcat data

---

## 🟨 IN PROGRESS / NEAR FUTURE

- Migrate `state_definitions` to YAML with overlay support and composite matching logic
- Build class-based handler system with `@register("STATE")`, `should_run()`, `run()` interfaces
- Expand `state_detector.py` to support overlay detection (e.g., lab ready, ad prompt)
- Improve `handle_game_over` swipe/scroll behavior
- Add per-handler pause/resume support and optional timeout-based recovery
- Improve image archival, session tagging, and debug output
- Debug watchdog false positives (foregrounding unnecessary)
- Increase automation runtime stability (current cap ~1–8 hrs)

---

## 🟦 FUTURE GOALS

- Implement decision-tree or state machine model for managing automation flow
- Add CLI and GUI control toggles for pause/resume
- Improve ADB reconnect logic (USB or Wi-Fi resilience)
- Dashboard or web-based log viewer (low priority)
- Generalize upgrade detection and scroll matching systems

---

## 📎 Internal References

- Input logic is governed by `core/input_policy.md`
- Click/tap region mapping is stored in `clickmap.json`, likely to migrate to `config/`
- Gesture data and one-off tooling lives in `tools/` (some of `coords/` to be deprecated)

