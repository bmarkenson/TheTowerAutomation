# ✅ Finalized Clickmap Refactor Plan (Nested Structure with Dual-Role Support)

---

## 🎯 GOALS

- Migrate from a flat key structure (e.g., `"button:resume_game"`) to a **fully nested and semantically grouped structure**
- Introduce support for **dual-role elements** (e.g., presence indicates both `RUNNING` and current menu)
- Centralize all match logic, eliminate `generic`, and make `clickmap.json` self-describing via metadata
- Update all consumers (`clickmap_access.py`, `state_definitions.yaml`, etc.) to support the new structure

---

## 🧱 STRUCTURE OVERVIEW

### Top-Level Blocks in `clickmap.json`

```json
{
  "upgrades": {
    "attack": { ... },
    "defense": { ... },
    "utility": { ... }
  },
  "buttons": {
    "resume_game": { ... },
    "claim_daily_gems": { ... },
    "buy_multiplier": {
      "x1": { ... },
      "x10": { ... },
      ...
    }
  },
  "navigation": {
    "goto_attack": { ... },
    "goto_defense": { ... },
    ...
  },
  "labels": {
    "demon_mode": { ... },
    "nuke": { ... }
  },
  "overlays": {
    "floating_gem": { ... },
    "claim_ad_gem": { ... }
  },
  "utils": {
    "more_stats": { ... },
    "close_more_stats": { ... }
  },
  "indicators": {
    "attack_menu": {
      "match_template": "...",
      "roles": ["state", "menu"],
      "state_qualifier": "running",
      "menu_name": "attack"
    },
    ...
  },
  "_shared_match_regions": { ... }
}
```

---

## 🧠 KEY DESIGN CONCEPTS

- **Location** = primary grouping
- **`roles`** = additional metadata (e.g. `"state"`, `"menu"`, `"button"`)
- Dual-role items like `attack_menu` live in `indicators`, tagged with both `state` and `menu`

---

## 🔁 STATE DETECTION CHANGES

```yaml
- name: RUNNING
  match_keys:
    - indicators.attack_menu
    - indicators.defense_menu

- name: ATTACK_MENU
  match_keys:
    - indicators.attack_menu
```

---

## 🛠 CLICKMAP ACCESS CHANGES

- Dot-path lookup: `get_click("buttons.resume_game")`
- Role-aware access/filtering
- Optional flattening compatibility

---

## 🧰 MIGRATION PLAN

1. Transform flat keys into nested structure
2. Move dual-role items to `indicators.*`
3. Add metadata (`roles`, `state_qualifier`, `menu_name`)
4. Update consumers (`state_definitions.yaml`, `clickmap_access.py`, etc.)

---

## 🧱 STRUCTURAL CONVENTIONS

| Use Case            | Location             | Role Tag         |
|---------------------|----------------------|------------------|
| Tap target          | `buttons.*`          | `"button"`       |
| Menu presence       | `indicators.*`       | `"menu"`         |
| Game state checker  | `indicators.*`       | `"state"`        |
| Upgrade UI          | `upgrades.attack.*`  | `"label"`+tap    |
| Overlay detection   | `overlays.*`         | `"overlay"`      |

---

## 📎 FUTURE EXTENSIONS

Supports:
- Overlay layering
- Modular menu detection
- Role-based filtering/debug
- Centralized indicator logic