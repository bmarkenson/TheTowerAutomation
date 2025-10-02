## 📐 Architectural Plan: Visibility-Aware Tapping Model

### 🎯 Core Principle
> All tap operations should confirm visual presence on screen before executing — unless explicitly marked as blind (e.g. `tap_dispatcher`).

---

## ✅ Architectural Goals

| Goal | Why |
|------|-----|
| Visibility-aware by default | Prevents false taps, improves reliability |
| Separation of data and execution | `clickmap_access` only resolves; tap logic lives elsewhere |
| Support for dynamic elements | Floating buttons, overlays, conditional UIs |
| Centralized logic | Shared offset/click resolution in one place |

---

## 📁 Module Breakdown

### `core/clickmap_access.py`
**Responsibilities:**
- `resolve_dot_path()`
- `get_clickmap()`
- `get_click(name)` ← **deprecated except for fallbacks**
- `get_swipe(name)`

**No tap logic.** No visibility detection. No ADB calls.

---

### `core/label_tapper.py`
**Responsibilities:**
- `get_label_match()` / `is_visible()` for template-driven detection
- Region normalization helpers
- No direct tap injection (pure matching logic)

### `core/tap.py`
**Unified tap entry point:**

```python
safe_tap(name, *, require_visible=True, retries=0, retry_delay=0.5,
         dispatch='now', allow_fallback=False, screenshot=None)
```

Helpers:
- `tap_if_visible(name, ...)` — visibility-gated tap
- `tap_blind(name, ...)` — blind tap via clickmap coordinates
- Centralized logging, offset handling, retry policy, fallback path

---

### `core/tap_dispatcher.py`
**Keep for:**
- Background "keepalive" swipes/taps
- Async tap queue (`tap(x, y, label)`)
- Failsafe blind input if needed

**Used only when:**
- Visibility is known implicitly (e.g. we just detected and are tapping immediately)
- Tap delays must be managed outside mission code

---

### 🚧 Deprecated Pattern

```python
tap_now("foo.bar")
```

Becomes:

```python
tap_if_visible("foo.bar")
```

Or:
```python
tap(x, y, label)  # async, visibility-assumed
```

---

## 💪 Migration Plan (Future)

1. Move tap logic out of `clickmap_access.py`
2. Route handlers through `core.tap` helpers (`tap_if_visible`, `tap_blind`, `safe_tap`)
3. Add logging, retries, and fallback handling to tap stack
