# Clickmap JSON Schema Documentation

The `clickmap.json` file defines all screen regions, templates, and input actions used by the automation system.
Each key in the file corresponds to a named UI element, navigation item, overlay, or state indicator, and follows a structured naming convention.

---

## ✅ Naming Convention

Each key name uses the format:

```
<role>:<element>[:qualifier][:context]
```

### Roles
- `state`: UI element that identifies a screen state
- `button`: Tappable region used to trigger an action
- `nav`: Navigation control between views
- `running`: Indicator that gameplay is currently active
- `overlay`: Floating or event-driven UI layer
- `util`: Utility or info-only element
- `swipe`: Action requiring a gesture instead of a tap
- `upgrade`: A tappable item in the scrollable upgrade panel
- `label`: Indicates a matched location for dynamically identifying tap coordinates

### Qualifier (optional)
Used to disambiguate similar elements (e.g. `primary`, `badge`, `reward`).

### Context (optional)
Indicates the screen or state where this element is valid (e.g. `game_over`, `shop`).

---


## 🧱 Entry Structure

Each entry may define one or more of the following:

### 🔹 Match-based Entry (Template Matching)
Used for detecting a screen element visually before acting.

```json
{
  "match_template": "some_element.png",
  "match_region": { "x": ..., "y": ..., "w": ..., "h": ... },
  "match_threshold": 0.9,
  "tap": { "x": ..., "y": ... },
  "roles": ["state", "button"]
}
```

### 🔹 Tap-only Entry (Blind Tap)
Used when a reliable tap location is known but the element cannot be visually matched.

```json
{
  "tap": { "x": ..., "y": ... }
}
```

This is useful for:
- Background taps (e.g. `floating_gem`)
- Taps on visually dynamic or animated elements
- Fast actions where detection latency is undesirable

You may still assign a descriptive `role` if desired:

```json
{
  "tap": { "x": 527, "y": 948 },
  "roles": ["button"]
}
```

### 🔹 Swipe-only Entry
Used for gestures:

```json
{
  "swipe": {
    "x1": ..., "y1": ...,
    "x2": ..., "y2": ...,
    "duration_ms": ...
  }
}
```

### 🔹 Multi-role Support
An entry may have both visual detection and a gesture, or multiple semantic roles:

```json
{
  "match_template": "...",
  "tap": { ... },
  "roles": ["state", "button"]
}
```


Each entry may contain some or all of the following fields:

### Fields

- `match_template`: filename of the template image used for matching
- `match_region`: region to search for the match, with `x`, `y`, `w`, `h`
- `match_threshold`: float between 0 and 1 (higher = stricter match)
- `tap`: coordinates of the location to tap `{ "x": int, "y": int }`
- `swipe`: gesture coordinates and duration:

```json
"swipe": {
  "x1": ..., "y1": ...,
  "x2": ..., "y2": ...,
  "duration_ms": ...
}
```
- `roles`: array of strings, e.g. `["state", "button"]`, used to indicate dual-purpose keys


---

## 📝 Example

```json
"state:resume_game": {
  "match_template": "resume_game.png",
  "match_region": {
    "x": 500,
    "y": 1200,
    "w": 200,
    "h": 100
  },
  "match_threshold": 0.9,
  "tap": {
    "x": 600,
    "y": 1250
  },
  "roles": ["state", "button"]
}
```

This element:
- Is used to detect the `RESUME_GAME` state
- Also acts as a button to resume gameplay
- Triggers a tap when found

---

## 📂 Template File Location

All `match_template` image files are located in:
```
config/templates/
```

---

## 🛠️ Use

This file is loaded by `clickmap_access.py` and used by state detectors, handlers, and gesture logic.
