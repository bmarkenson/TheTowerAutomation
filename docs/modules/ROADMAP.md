# TheTower Automation Roadmap (Retired)

> Retired on 2026-07-13 after an implementation audit. All unfinished items
> that remain relevant were moved to the canonical
> [`PENDING_DEVELOPMENT.md`](../../PENDING_DEVELOPMENT.md). This file is retained
> only as a historical snapshot and should not be updated.

This roadmap outlines current architectural goals, bugfixes, and planned features for the "The Tower: Idle Tower Defense" ADB-based automation project. Each item is tagged with a priority:

- 🔼 High: Critical for correctness, stability, or progress
- ⏳ Medium: Useful or unblockers, but not critical now
- ⏬ Low: Optional or long-term features

---

## ⚙️ Architectural Refactors

### State Detection System
- ⏳ [ ] Add confidence threshold logic (global or per-state optional).
- ⏳ [ ] Support composite match logic (e.g. `all_of`, `any_of`).
-    [ ] Enforce unified naming convention across clickmap and YAML (e.g., role__name). Build validator to detect drift and optionally suggest YAML stubs for new clickmap keys.


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

- 🔼 [ ] Add overlay and handler for claiming daily quests.
- ⏳ [ ] Fix `handle_game_over` (currently blind tapping; migrate to match checking)
---

## 🔄 Upgrade & Scroll Systems

- ⏳ [ ] Generalize upgrade detection framework:
  - Match upgrade buttons from `clickmap`
  - Use secondary region check (color/indicator)
  - Handle varied scroll positions
- ⏳ [ ] Create helper: `is_upgrade_available(name, screen) -> bool`
- ⏳ [ ] Add priorities for upgrades (Damage > Coins > Speed, etc.)
- 🔼 [ ] Implement upgrade detection system for scrollable upgrade panel. For each upgrade label, match its location in the scrollable area, then inspect the price box to the right. Only tap if the box is active (not dimmed) and not labeled "Max". Use pixel brightness and optional OCR to determine availability. Support scrolling if the upgrade is not visible. Build modular helpers: `is_upgrade_available()`, `find_scrollable_upgrade()`, and `tap_relative_to_label()`.


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

- ⏳ [ ] Add regression test for state transitions
- (low) Allow individual entry editing in the clickmap instead of always doing a full overwrite dump

---

This roadmap is evolving and modular. Items may be pulled forward or deferred depending on system stability and automation priorities.

