# `tap_function_consistency.md`
## 📌 Architectural Note: Unifying Tap Semantics Across Automation Layers

### 🧩 Current Problem

The project uses multiple `tap_*` functions with differing assumptions about:

- Whether the target is *currently* visible
- Whether to *perform matching* before tapping
- Whether failure is *graceful* or throws
- Where logging responsibility lies

This inconsistency causes **runtime bugs**, unclear tap logic, and poor reuse across modules (e.g., ad gem vs. mission scripts).

---

### 🗂️ Current Tap Function Types

| Function                        | Performs Matching | Assumes Match Done | Blind Tap | Graceful Fallback | Logging |
|--------------------------------|-------------------|---------------------|-----------|-------------------|---------|
| `tap_now(name)`                 | ❌                | ✅                  | ✅        | ❌                | Partial |
| `tap_label_now(name)`          | ✅                | ❌                  | ❌        | ❌                | ✅      |
| `tap_floating_button(name, buttons)` | ✅        | ❌                  | ❌        | ✅ (if coded)     | ✅      |
| `tap_dispatcher.tap(x, y)`     | ❌                | ✅ (manual)         | ✅        | ❌                | None    |

---

### 🧱 Architectural Issues

- `tap_now()` is fast and clean but **blind** — misused if visibility isn’t confirmed upstream
- `tap_label_now()` **performs match itself**, but doesn’t retry or fallback
- `get_label_match()` silently fails if `region_ref` resolution fails (causes misleading errors)
- Handlers mix-and-match these behaviors with no enforced semantic boundary

---

### ✅ Proposal: Normalize Tap API

Introduce a unified interface:

```python
tap(key, *, require_visible=True, retries=0, delay=0.5)

With clear behavior:

    If require_visible, always match and verify visibility

    Supports retries (e.g., for transient states)

    Falls back to get_click() only if explicitly allowed

    Logs tap attempt, location, and fallback info

Use this as a future abstraction layer to route all other tap calls (blind, label, floating) through a consistent API.
🛣️ Migration Plan
Phase	Action
1	Identify current usage patterns (done)
2	Fix get_label_match() bug (next step)
3	Deprecate blind tap_now() in favor of safe_tap()
4	Migrate handlers (ad_gem, mission, menu ops)
