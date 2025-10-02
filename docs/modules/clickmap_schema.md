
# 📘 `clickmap.json` Schema Documentation

This file defines all UI elements, buttons, overlays, and indicators for screen detection and automation input. It contains only **static visual structure** — no logic, conditions, or behavior.

---

## 🧱 Top-Level Structure

Organized into logical groups:

```json
{
  "buttons": { ... },
  "indicators": { ... },
  "overlays": { ... },
  "upgrades": { ... },
  "labels": { ... },
  "_shared_match_regions": { ... }
}
```

Each leaf entry represents a single screen element.

---

## 🔑 Allowed Fields

| Field            | Type       | Description |
|------------------|------------|-------------|
| `match_template` | `str`      | Filename in `assets/match_templates/` |
| `match_region`   | `dict`     | Region in `{x, y, w, h}` format |
| `tap`            | `dict`     | Optional override tap point `{x, y}` |
| `swipe`          | `dict`     | Optional gesture action `{x1, y1, x2, y2, duration_ms}` |
| `roles`          | `list[str]`| Element classification (`button`, `label`, `overlay`, etc.) |
| `region_ref`     | `str`      | Optional pointer to `_shared_match_regions` key |

---

## ❌ Banned Fields (Semantic Logic Belongs Elsewhere)

These keys are **prohibited** and must be removed from all `clickmap.json` entries:

- `menu_name`
- `state_qualifier`
- `tap_offset`
- `ui_context`
- `is_required`
- Any other logic-bearing field or condition

👉 These must instead be defined in:
- `state_definitions.yaml`
- Handlers or dynamic runtime logic

---

## 🧠 Role Semantics

| Role      | Meaning |
|-----------|---------|
| `button`  | Tap target (with optional `tap`) |
| `state`   | Contributes to state detection |
| `menu`    | Implies a specific menu is active |
| `overlay` | Optional/parallel visual element |
| `label`   | Used to infer tap or check near elements |

---

## ✅ Example

```json
"indicators": {
  "attack_menu": {
    "match_template": "attack_menu.png",
    "match_region": { "x": 10, "y": 1592, "w": 513, "h": 55 },
    "roles": ["state", "menu"]
  }
}
```

---

## 🔗 Dot-Path Convention

Entries are referenced using their full path, e.g.:
- `buttons.resume_game`
- `indicators.attack_menu`
- `overlays.daily_free_gems_badge`

---

## 📌 Notes

- If `tap` is not present, tap defaults to center of `match_region`
- `match_threshold` is optional and usually defined globally
- `clickmap.json` must be tool-friendly: YAML-compatible, grepable, and stable


---

## 🔤 Key Naming Notes

- Keys may include colons (`:`) to indicate contextual disambiguation.  Keys should never be split on ':'; that is a part of the name
- For example, `"retry:game_over"` means "the retry button found in the game_over screen".
- This convention is encouraged where multiple buttons may share a label across states or views.

Example:

```json
"buttons": {
  "retry:game_over": {
    "match_template": "buttons/retry.png",
    "match_region": { "x": 800, "y": 1200, "w": 200, "h": 100 },
    "roles": ["button"]
  },
  "retry:lab_restart": {
    "match_template": "buttons/retry_lab.png",
    "match_region": { "x": 350, "y": 890, "w": 180, "h": 80 },
    "roles": ["button"]
  }
}
```

These keys remain fully compatible with dot-path lookups (e.g., `buttons.retry:game_over`).



---

## ♻️ Reusing Match Regions with `region_ref`

To avoid repeating identical regions across entries (especially upgrade buttons and floating bars), use `region_ref`.

- Define common regions under `_shared_match_regions`
- Reference them in any element using `region_ref`

### 🔧 Structure of `_shared_match_regions`

```json
"_shared_match_regions": {
  "upgrade_left": {
    "x": 30,
    "y": 1500,
    "w": 480,
    "h": 1400
  },
  "upgrade_right": {
    "x": 550,
    "y": 1500,
    "w": 480,
    "h": 1400
  },
  "floating_button": {
    "x": 0,
    "y": 1200,
    "w": 1080,
    "h": 200
  }
}
```

### 🧱 Referencing a Shared Region

```json
"upgrades": {
  "attack": {
    "left": {
      "upgrade_damage": {
        "match_template": "upgrades/attack/upgrade_damage.png",
        "region_ref": "upgrade_left",
        "roles": ["label"]
      }
    },
    "right": {
      "upgrade_attack_speed": {
        "match_template": "upgrades/attack/upgrade_attack_speed.png",
        "region_ref": "upgrade_right",
        "roles": ["label"]
      }
    }
  }
}
```

### ⚠️ Priority Rules

If both `match_region` and `region_ref` are specified in an entry:

- `match_region` **takes precedence**
- `region_ref` is ignored (can be flagged by a linter)
