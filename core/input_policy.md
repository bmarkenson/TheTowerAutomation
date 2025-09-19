# Input Policy and Tap Architecture for TheTower Automation

This document defines the expected behavior, purpose, and constraints around input injection in the automation system for *The Tower: Idle Tower Defense*. It ensures correct use of tap and swipe functions across modules.

---

## 🔧 Input Injection Overview

There are **two distinct input paths** for injecting taps or swipes into the Android game, with a new unified helper for visibility-aware taps:

### 0. `safe_tap()` (new canonical helper)
- **Module:** `core/tap.py`
- **Functions:** `safe_tap`, `tap_if_visible`, `tap_blind`
- **Purpose:**
  - Normalize tap behavior: visibility-first by default, optional retries, consistent logging
  - Route to immediate or queued path via `dispatch='now'|'queue'`
- **Examples:**
  - `tap_if_visible("buttons.retry:game_over", retries=1)`
  - `safe_tap("navigation.goto_store", require_visible=True, dispatch='now')`
  - `tap_blind("gesture_targets.floating_gem_blind_tap", dispatch='queue')`

### 1. `tap_dispatcher`
- **Function:** `tap(x, y, label=None)`
- **Execution:** Queued
- **Threaded:** Yes — one centralized background worker
- **Purpose:**
  - Low-priority interactions
  - Periodic keepalive swipes
  - Background or delayed taps
- **Behavioral Constraints:**
  - Non-blocking
  - No assumption of immediate screen feedback
  - Can be throttled or batched

### 2. `tap_now(name)` and `swipe_now(name)`
- **Module:** `core/clickmap_access.py`
- **Execution:** Immediate
- **Threaded:** Runs in caller’s thread
- **Purpose:**
  - High-priority interactions
  - Feedback-driven taps (e.g., button press waiting for screen change)
  - Emergency recovery swipes
- **Behavioral Constraints:**
  - Must be visually gated (template match or pixel check)
  - No batching or queuing — executes instantly
  - Blocking OK — often used in state handlers

---

## 🚫 DO NOT

- ❌ Mix both systems in the same handler without clear intent
- ❌ Use `tap_now()` for routine clicks — prefer `safe_tap()` or `tap()` as appropriate
- ❌ Call `adb` directly outside these wrappers
- ❌ Fire multiple tap paths concurrently on different threads without conflict management

---

## ✅ DO

- ✅ Use `tap_if_visible()`/`safe_tap(require_visible=True)` for state transitions and modal dismissals
- ✅ Use `tap_dispatcher.tap()` or `tap_blind()` for background taps, keepalives, low-urgency triggers
- ✅ Use `swipe_now()` for immediate swipes that are part of a controlled flow
- ✅ Always confirm screen state before issuing immediate tap
- ✅ Always confirm screen state before issuing immediate tap
- ✅ Log input events meaningfully (label, position, type)

---

## 🧠 Examples

### Example 1: Keepalive Swipe
```python
# OK — queued
from core.tap_dispatcher import tap
tap(100, 200, label="keepalive")
```

### Example 2: Game Over Dismiss Button
```python
# Preferred — immediate, visibility-gated with retry
from core.tap import tap_if_visible
tap_if_visible("buttons.retry:game_over", retries=1)

# Alternative — strict immediate without retry
from core.tap import safe_tap
safe_tap("buttons.retry:game_over", require_visible=True, dispatch='now')
```

---

## 📎 Notes
- These rules will be enforced during handler class refactor (via `@register()` decorators)
- A future enhancement may include a unified `safe_tap(name, mode="auto|queued|now")` wrapper to standardize logic further
