# README_UPLOAD.md

This archive contains the full source code for an ADB-based automation project targeting the Android game "The Tower: Idle Tower Defense".

---

## 📦 Instructions for ChatGPT

- Fully extract this archive.
- Index the directory structure and core source files.
- Load only what is needed per request (avoid early context overload).
- The project follows a modular Python layout:
  - `main.py`: Main loop (entry point)
  - `core/`: Core utilities (tap handling, ADB, state detection)
  - `handlers/`: State-specific logic modules
  - `matchers/`: Template matching logic (OpenCV)
  - `assets/`: PNGs used for screen detection
  - `automation/`: Legacy; being deprecated
  - `coords/`: Legacy; contains gesture and region info (migrating out)
  - `config/`: For static settings or clickmaps (destination for some migrations)
  - `screenshots/`: Captured screenshots (live + archive)
  - `tools/`: Utility scripts used outside `main.py`
  - `utils/`: Shared helper functions
  - `PROJECT_SCOPE.md`: Core requirements and current state
  - `ROADMAP.md`: In-progress work and planned features
  - `core/input_policy.md`: Defines allowed input behavior (tap, swipe)

---

## ✅ Required Assistant Behavior

- Maintain full project context across modules
- Respect modular separation: `core`, `handlers`, `matchers`, etc.
- Avoid suggesting rewrites without fully understanding the system
- **Do not suggest blind input macros** — all input must be visual-state-aware
- Understand the dual-path tap architecture (see below)
- Respect watchdog, tap queue, cooldowns, and handler conditions

---

## 👆 Tap Injection Policy Summary

> 📎 Full details in: `core/input_policy.md`

- The system uses **two input paths**:
  - `tap_dispatcher.tap()` → for queued, low-priority, or periodic taps
  - `tap_now()` / `swipe_now()` → for immediate-response, feedback-gated actions
- Avoid bypassing these wrappers or using raw ADB calls
- Use the appropriate method based on urgency and visual context requirements

---

## ⚠️ Caution on Refactors

- When making changes to the codebase, **compare against the extracted version** to ensure no functionality is lost or regressed.
- Avoid proposing rewrites without fully understanding how the change may impact other modules or architectural constraints (e.g., tap handling, state detection, watchdog coordination, overlays).

---

## 🧠 First Steps After Extraction

Start by loading:
- `README_UPLOAD.md` (this file)
- `PROJECT_SCOPE.md`
- `ROADMAP.md`

All other modules can be loaded on demand. 

