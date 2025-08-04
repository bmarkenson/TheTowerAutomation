
# 📘 `state_definitions.yaml` Schema Documentation

This file defines how the automation system detects screen states, overlays, and menus by mapping visual indicators from `clickmap.json`.

---

## 🧱 Overall Structure

```yaml
states:
  - name: GAME_OVER
    type: primary
    match_keys:
      - indicators.game_over

overlays:
  - name: DAILY_GEMS_AVAILABLE
    match_keys:
      - overlays.daily_free_gems_badge
```

---

## 🔑 Keys and Their Meaning

### `states:`
A list of primary and contextual screen states the automation system must recognize.

Each state contains:

| Key         | Required | Description |
|-------------|----------|-------------|
| `name`      | ✅       | Unique identifier for the state (e.g., `RUNNING`, `ATTACK_MENU`) |
| `type`      | ✅       | Behavioral category (see below) |
| `match_keys`| ✅       | Dot-paths to entries in `clickmap.json` that visually indicate this state |

### `overlays:`
Non-blocking UI elements that may appear on top of any state.

| Key         | Required | Description |
|-------------|----------|-------------|
| `name`      | ✅       | Unique identifier (e.g., `DAILY_GEMS_AVAILABLE`) |
| `match_keys`| ✅       | Dot-paths to static entries in `clickmap.json` |

---

## 🧩 Accepted Values for `type`

| Type       | Description |
|------------|-------------|
| `primary`  | Fullscreen, mutually exclusive UI states (e.g. GAME_OVER, HOME_SCREEN) |
| `running`  | Confirms gameplay is active; may coexist with menus or overlays |
| `menu`     | A visible menu layered on top of a primary or running screen |
| *(none)*   | Not applicable to `overlays` (they have no type) |

---

## 🔗 Rules

- `match_keys` must **exactly match** dot-paths defined in `clickmap.json`
- Do **not** use legacy-style keys like `state:attack_menu:running`
- `clickmap.json` stores only structural data (no state logic, qualifiers, or conditions)
- State inference and tap behavior are defined entirely in this YAML or handler logic

---

## 🛠 Example State

```yaml
- name: RUNNING
  type: running
  match_keys:
    - indicators.attack_menu
    - indicators.defense_menu
    - indicators.uw_menu
    - indicators.wall_icon
```

This confirms that **any one** of those visual indicators means the game is actively running.

---

## ✅ Best Practices

- Use `indicators.*` for state/menu detection
- Use `overlays.*` only for parallel-action UI (e.g. red badge)
- Define only one `primary` state as active at a time
- Design handlers to act conditionally on `menu` or `overlay` presence

