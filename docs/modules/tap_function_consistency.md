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

### 🗂️ Canonical Tap Interfaces (after refactor)

| Function                        | Performs Matching | Blind Tap | Retries | Fallback | Logging |
|--------------------------------|-------------------|-----------|---------|----------|---------|
| `safe_tap(name, ...)`          | ✅ (`require_visible=True` by default) | ⚠️ Optional (`allow_fallback`) | ✅ (configurable) | ✅ (when allowed) | ✅ Structured |
| `tap_if_visible(name, ...)`    | ✅                | ❌        | ✅       | ❌       | ✅      |
| `tap_blind(name, ...)`         | ❌ (uses click coords) | ✅    | ❌       | ❌       | ✅      |
| `tap_dispatcher.tap(x, y)`     | ❌                | ✅        | ❌       | ❌       | ❌ (caller logs) |
| `tap_now(name)` (legacy)       | ❌                | ✅        | ❌       | ❌       | Partial |

---

### 🧱 Architectural Issues (legacy state)

- `tap_now()` is fast and clean but **blind** — misused if visibility isn’t confirmed upstream
- Former `tap_label_now()` performed matches but lacked retries/fallbacks; replaced by `tap_if_visible`
- `get_label_match()` previously had error-handling gaps — now resolved with clearer raises
- Handlers once mixed semantics; the unified helpers enforce a consistent boundary

---

### ✅ Result: Normalized Tap API

All handlers call into `core.tap.safe_tap` (or wrappers) which:

- Confirms visibility when required, with configurable retries/delays
- Provides optional blind fallback through clickmap coordinates
- Centralizes logging, offsets, and dispatch routing (immediate vs. queued)

🛣️ Migration Status
Phase	Action
1	Identify current usage patterns ✅
2	Fix get_label_match() bug ✅
3	Deprecate blind tap_now() in favor of safe_tap() ✅ (warning emitted)
4	Migrate handlers (ad_gem, missions, menu ops) ✅
