# TheTower Roadmap — Priority View (Retired)

> Retired on 2026-07-13 after an implementation audit. All unfinished items
> that remain relevant were moved to the canonical
> [`PENDING_DEVELOPMENT.md`](../../PENDING_DEVELOPMENT.md). This file is retained
> only as a historical snapshot and should not be updated.

This document presents a flattened, priority-sorted view of all active roadmap items. Use it to quickly identify what to work on next without scanning the full roadmap structure.

---

## 🔼 HIGH PRIORITY

- Refactor `state_definitions` to YAML format
- Add support for `overlays` in YAML (e.g., ad gem, lab ready icons)
- Update `state_detector.py` to load YAML and return state + overlays
- Convert function-based handlers into class-based modules
- Implement `@register("STATE")` decorator
- Add `should_run()` and `run()` interface for all handlers
- Create centralized handler dispatch loop
- Allow per-handler pause/resume support
- Design a pause system per handler (toggleable)
- Improve "running indicators" logic (support mutually exclusive indicators)
- Add overlay definition and handler for `lab_ready`
- Add overlay and handler for daily quest claiming
- Add `test_overlay_detection.py`
- Improve clickmap tooling and schema validation

---

## ⏳ MEDIUM PRIORITY

- Add confidence threshold logic to matchers
- Add composite match logic (e.g., `all_of`, `any_of`)
- Extract shared utility functions from handlers (`save_image`, `_make_session_id`)
- Add timeout-based resume system (for pauses and stuck states)
- Fix `handle_game_over` scroll/swipe logic (only if needed)
- Generalize upgrade detection logic and region validation
- Add `is_upgrade_available(name, screen)` helper
- Add upgrade priority logic (e.g., Damage > Coins > Speed)
- Add scroll-aware match system (track visible area, scroll index)
- Add regression test for state transitions

---

## ⏬ LOW PRIORITY

- Define automation modes (e.g., farming, ad-farming)
- Store strategy mode config in YAML or JSON
- Add conditional handler logic based on active mode
- Add GUI window for manual control (pause/resume, state view, override)
