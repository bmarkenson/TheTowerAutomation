# TheTower Automation Project Scope

## 🎮 Game
- **Title**: "The Tower: Idle Tower Defense"
- **Platform**: Bluestacks running on Windows 11 with adb active.  Windows runs a helper to maintain an ssh tunnel to linux for adb access.
- **Host System**: Linux with Python + OpenCV + scrcpy

---

## 🟥 CORE REQUIREMENTS

- **24/7 unattended automation**
  - Must recover from ADB disconnects, unresponsive UI, and game crashes.
- **Visual state awareness**
  - Screens are analyzed before taking action using OpenCV template matching.
- **Reliable input injection**
  - Uses dual-path tap system:
    - `tap_dispatcher` for queued, low-priority, and periodic input
    - `tap_now()` / `swipe_now()` for immediate, feedback-dependent blind taps/swipes
    - `tap_label_now` for dynamic generation of tap coordinates based on location of label
- **Manual override**
  - Automation can be paused or bypassed for scrcpy / Bluestacks manual interaction.
- **Minimal tech stack**
  - Pure Python + ADB and scrcpy on Linux. 

---

## 🟧 CURRENT IMPLEMENTATION STATUS

- Modular architecture: `core/`, `handlers/`, `matchers/`, `utils/`, `tools/`
- Main loop (`main.py`) handles screenshot capture, state detection, and dispatch
- `state_detector.py` uses `clickmap.json` + OpenCV region matchers to identify key states
- Implemented states: `GAME_OVER`, `HOME_SCREEN`, `RUNNING`, `RESUME_GAME`
- Initial support for overlays (e.g. floating gem, ad_gem) under active development
- Handler modules: `handle_game_over`, `handle_home_screen`, and others (function-based, migrating to class-based)
- Tap injection split between `tap_dispatcher` (background taps) and `tap_now` / `swipe_now` / `tap_label_now` (immediate)
- Watchdog monitors for backgrounded app, restarts or re-foregrounds game if needed
- Tools: `crop_region.py`, `gesture_logger.py`, `tune_gesture.py`
- Resource monitoring: `log_meminfo.py` logs memory/thermal stats and logcat data
- Implemention of decision-tree or state machine model for managing automation flow in progress (see handlers/mission_demon_nuke, handlers/mission_demon_mode)

---

## 🟨 IN PROGRESS / NEAR FUTURE

- Update `state_definitions` with additional states / overlays
- Build class-based handler system with `@register("STATE")`, `should_run()`, `run()` interfaces
- Improve `handle_game_over` swipe/scroll behavior
- Add per-handler pause/resume support and optional timeout-based recovery
- Improve image archival, session tagging, and debug output
- Implement tap architecture refactor based on `tap_architecture_plan.md` and `tap_function_consistency.md`

---

## 🟦 FUTURE GOALS

- Add CLI and GUI control toggles for pause/resume
- Improve ADB reconnect logic (USB or Wi-Fi resilience)
- Dashboard or web-based log viewer (low priority)
- Generalize upgrade detection and scroll matching systems

---

### Extensibility Consideration

While the current automation framework is purpose-built for *The Tower*, design and architectural decisions should strive to support future extensibility. This includes clean separation between game-specific assets (clickmaps, match templates, state definitions, handlers) and core engine logic, use of consistent naming conventions, modular handler dispatch, and human-readable configuration formats. The goal is to enable straightforward reuse or adaptation of this system for other games or visual automation tasks with minimal refactoring.

## 📎 Internal References

- Input logic is governed by `core/input_policy.md`
- Click/tap region mapping is stored in `config/clickmap.json`
- Gesture data and one-off tooling lives in `tools/`
- clickmap schema detailed by `config/clickmap_schema.md`
- state_definitions schema in `config/state_definitions_schema.md`

