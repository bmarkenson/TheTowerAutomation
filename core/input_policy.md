# Input Policy and Tap Architecture for TheTower Automation

This document defines the expected behavior, purpose, and constraints around input injection in the automation system for *The Tower: Idle Tower Defense*. It ensures correct use of tap and swipe functions across modules.

---

## 🔧 Input Injection Overview

There are **two distinct input paths** for injecting taps or swipes into the Android game:

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
- ❌ Use `tap_now()` for routine clicks — prefer `tap()`
- ❌ Call `adb` directly outside these wrappers
- ❌ Fire multiple tap paths concurrently on different threads without conflict management

---

## ✅ DO

- ✅ Use `tap_dispatcher.tap()` for background taps, keepalives, low-urgency triggers
- ✅ Use `tap_now()` / `swipe_now()` for state transitions, modal dismissals, or cases needing screen response
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
# OK — immediate
from core.clickmap_access import tap_now

if match_region("game_over_button"):
    tap_now("game_over_button")
```

---

## 📎 Notes
- These rules will be enforced during handler class refactor (via `@register()` decorators)
- A future enhancement may include a unified `safe_tap(name, mode="auto|queued|now")` wrapper to standardize logic further
