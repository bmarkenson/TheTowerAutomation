# TheTower Automation Roadmap

This roadmap outlines current architectural goals, bugfixes, and planned features for the "The Tower: Idle Tower Defense" ADB-based automation project. Each item is tagged with a priority:

- 🔼 High: Critical for correctness, stability, or progress
- ⏳ Medium: Useful or unblockers, but not critical now
- ⏬ Low: Optional or long-term features

---

## ⚙️ Architectural Refactors

### State Detection System
- 🔼 [ ] Refactor `state_definitions` to YAML format.
- 🔼 [ ] Add support for `overlays` in YAML (non-exclusive UI elements like ad gems, lab ready icons).
- 🔼 [ ] Update `state_detector.py` to:
  - Load YAML
  - Return structured results with `state` and `overlays`
- ⏳ [ ] Add confidence threshold logic (global or per-state optional).
- ⏳ [ ] Support composite match logic (e.g. `all_of`, `any_of`).

### Handler Architecture
- 🔼 [ ] Convert function-based handlers into class-based handler modules.
- 🔼 [ ] Implement `@register("STATE_NAME")` decorator for automatic handler registration.
- 🔼 [ ] Add consistent `should_run()` and `run()` interface for all handlers.
- 🔼 [ ] Create centralized handler dispatch loop.
- 🔼 [ ] Allow handler pause/resume support.
- ⏳ [ ] Extract utility functions (e.g. `save_image`, `_make_session_id`) into methods or shared modules.

---

## ⏰ Pause & Timeout Management

- 🔼 [ ] Design a pause system per handler (toggleable).
- ⏳ [ ] Add CLI and future GUI interface to manage pause state.
- ⏳ [ ] Add timeout-based resume system:
  - If paused for too long, resume automatically.
  - If stuck on non-running state for too long, trigger fallback or recovery.

---

## 📊 State Enhancements

- 🔼 [ ] Improve "running indicators" logic:
  - Support mutually exclusive UI elements for gameplay confirmation.
- 🔼 [ ] Add overlay definition for `lab_ready` icon and corresponding handler.
- 🔼 [ ] Add overlay and handler for claiming daily quests.
- ⏳ [ ] Fix `handle_game_over` swipe/scroll logic (only if needed).

---

## 🔄 Upgrade & Scroll Systems

- ⏳ [ ] Generalize upgrade detection framework:
  - Match upgrade buttons from `clickmap`
  - Use secondary region check (color/indicator)
  - Handle varied scroll positions
- ⏳ [ ] Create helper: `is_upgrade_available(name, screen) -> bool`
- ⏳ [ ] Add priorities for upgrades (Damage > Coins > Speed, etc.)

- ⏳ [ ] Add scroll-aware matching system:
  - Track visible screen region or scroll index
  - Allow region offsetting in template matches
  - Support looped or stepped scroll actions

---

## 🔢 Strategy Modes

- ⏬ [ ] Define automation modes (e.g., farming, ad-farming, leveling)
- ⏬ [ ] Store in YAML or JSON config
- ⏬ [ ] Allow mode-based conditional logic in handlers and dispatcher

---

## 🎨 Future UI Features

- ⏬ [ ] GUI window for live control:
  - Pause/resume
  - View current state/overlays
  - Trigger manual handlers or override

---

## 🔄 Utilities and Testing

- 🔼 [ ] Add `test_overlay_detection.py` to verify overlays independently.
- ⏳ [ ] Add regression test for state transitions
- 🔼 [ ] Improve clickmap tooling and schema validation
- (low) Allow individual entry editing in the clickmap instead of always doing a full overwrite dump

---

This roadmap is evolving and modular. Items may be pulled forward or deferred depending on system stability and automation priorities.


