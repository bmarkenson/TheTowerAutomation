# UI Detection Schema Reference

`config/clickmap.json` owns static visual geometry and assets;
`config/state_definitions.yaml` owns how those visual facts classify a frame.
State, ordering, retry, recovery, and action policy do not belong in the
clickmap. Current consumers and validators remain authoritative for exact
field behavior.

## Clickmap entries

Entries may be nested under descriptive groups and are addressed by their full
dot path. A colon inside a key is ordinary key text, not a path separator.

| Field | Contract |
| --- | --- |
| `roles` | Optional nonempty list of nonempty strings used for discovery and specialized consumers. Reuse a role already owned by current source. |
| `match_template` | Nonempty path relative to `assets/match_templates/`; requires a resolvable search region. |
| `match_region` | Canonical `{x, y, w, h}` integer rectangle within `1080x1920`; width and height are positive. |
| `region_ref` | Name under `_shared_match_regions`; used only when the entry has no direct `match_region`. |
| `match_threshold` | Optional numeric value in `(0, 1]`; default `0.9`. |
| `match_padding` | Optional nonnegative integer; the color detector default is 12 pixels, while label matching deliberately selects zero padding. |
| `tap` | Explicit `{x, y}` integer point within canonical bounds. It is geometry, not action authority. |
| `tap_offset` | Optional `{x, y}` offset from a freshly matched template's top-left corner for supported template-backed input. |
| `swipe` | `{x1, y1, x2, y2, duration_ms}` integers; both points are in bounds and duration is positive. |

Shared regions wrap their geometry rather than exposing coordinates directly:

```json
"_shared_match_regions": {
  "upgrades_left": {
    "match_region": {"x": 26, "y": 1253, "w": 511, "h": 542},
    "roles": ["_shared_match_region"]
  }
}
```

An entry with both `match_region` and `region_ref` uses the direct region. The
configured padding expands the search rectangle and clamps it to the screen;
the template must fit inside that effective region.

Template-backed `safe_tap` input rematches immediately and uses the match
center or supported `tap_offset`. A static `tap` or calculated coordinate still
requires a fresh `TapVerification`; a broad `region_ref` never becomes a blind
tap center. Unchecked input is isolated to explicit tooling. See
[`../tooling/template_workflow.md`](../tooling/template_workflow.md) for asset
creation and review.

## State definitions

Both `states` and `overlays` are ordered lists. Each item has a unique `name`
and a nonempty `match_keys` list of existing clickmap entry dot paths. A state
also declares one supported `type`:

| Type | Result behavior |
| --- | --- |
| `terminal_primary` | Authoritative terminal modal; at most one may match. |
| `primary` | Ordinary exclusive screen; multiple matches are an error when no terminal primary is present. |
| `fallback_primary` | Modal fallback used only when neither terminal nor ordinary primary matched; YAML order breaks ties with a warning. |
| `background_primary` | Underlying screen used only after the stronger primary classes fail; YAML order breaks ties. |
| `secondary` | Non-primary state appended to `secondary_states`. |
| `menu` | Mutually exclusive menu result; the first matching YAML entry wins. |

Primary selection priority is terminal, ordinary, fallback, then background.
Overlays are evaluated separately after state classification and any number may
coexist. Detection returns `state`, `secondary_states`, `overlays`, and `menu`;
it returns `UNKNOWN` for an incomplete frame without matching.

Do not put semantic qualifiers in clickmap keys or entries. Add a visual fact
to the clickmap, reference it from state YAML when it participates in generic
classification, and keep workflow-specific conditions in the owning handler,
strategy, or architecture contract.

## Validation

After any clickmap, template, or state-definition change, run:

```bash
.venv/bin/python test/clickmap_integrity.py
.venv/bin/python test/validate_state_defs.py
```

The clickmap audit validates recursive entry shapes, regions, coordinates,
thresholds, padding, referenced assets, and template fit; its orphan list is a
review input unless strict-orphan mode is explicitly selected. The state audit
requires core states, rejects dangling `match_keys`, and requires at least one
valid key per state. Add fixture-based match tests when a present template also
needs proof against representative positive and negative screens.
