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
**New tap entry point for static/dynamic labels:**

```python
def tap_label_if_visible(name: str, screen) -> bool:
    """Match label from screen and tap if found. Returns True if tapped."""
```

**Floating button support:**
```python
def tap_floating_button(name: str, match_results) -> bool:
    """Tap a floating button if it exists in a prior detect_floating_buttons() call."""
```

**Optional fallback:**
```python
def tap_label_now(name: str):
    """Taps label after confirming visibility. Raises if not visible."""
```

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
tap_label_if_visible("foo.bar", screen)
```

Or:
```python
tap_label_now("foo.bar")
```

Or:
```python
tap(x, y, label)  # async, visibility-assumed
```

---

## 💪 Migration Plan (Future)

1. Move tap logic out of `clickmap_access.py`
2. Refactor handlers to use `tap_label_if_visible()` or `tap_label_now()`
3. Add logging, retries, and fallback handling to tap stack
