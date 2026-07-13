# Template Audit — 2026-07-13

This is a dated static audit snapshot. The ongoing live/fixture audit is tracked
in `PENDING_DEVELOPMENT.md`.

## Recursive clickmap audit

- Nested clickmap entries inspected: 145
- Referenced template files: 120
- Hard integrity errors after correction: 0
- Orphaned files requiring classification: 18
- Actively referenced but untracked templates: 14

The audit corrected the `multishot_chance` filename reference and removed two
unused Ultimate Weapon toggle clickmap entries. Current Ultimate Weapon toggle
state is detected from saturation in dynamically located rows, so the old fixed
templates had no valid search region or active caller.

## Actively referenced but untracked

- `buttons/Cards:GCFarmEarly.png`
- `buttons/Cards:GCFarmLate.png`
- `buttons/cards:locked:ok.png`
- `indicators/Cards:GCFarmEarly.png`
- `indicators/Cards:GCFarmLate.png`
- `indicators/cards:deck1.png`
- `indicators/cards:deck3.png`
- `indicators/cards:locked.png`
- `indicators/menu:cards.png`
- `navigation/Cards.png`
- `navigation/menu_close_button.png`
- `navigation/menu_open_button.png`
- `overlays/menu_closed.png`
- `overlays/menu_open.png`

These must be committed with the current configuration or their references must
be deliberately removed/replaced. The legacy Cards names remain under review
because the desired behavior is now a fixed `GC` deck validation.

## Orphaned assets requiring classification

- `_shared_match_regions/floating_buttons.png`
- `_shared_match_regions/floating_gem_region.png`
- `_shared_match_regions/upgrades_left.png`
- `_shared_match_regions/upgrades_right.png`
- `_shared_match_regions/wave_number.png`
- `buttons/goto_store.png`
- `buttons/uw_toggle_to_off.png`
- `buttons/uw_toggle_to_on.png`
- `indicators/[STATUS 2025-11-10 09:31:57] State=RUNNING | Wave=2758 | Coins/min=7/6T | Menu=UTILITY_MENU | Secondary=[—] | Overlays=[—].png`
- `overlays/floating_gem-east.png`
- `overlays/floating_gem-north.png`
- `overlays/floating_gem-south.png`
- `overlays/floating_gem-west.png`
- `overlays/floating_gem.png`
- `overlays/uw_detail_popup.png`
- `upgrades/attack/left.png`
- `upgrades/attack/right/multi_shot_targets.png`
- `upgrades/attack/right/super_crit_mult.png`

No orphan is removed solely from this report. The live coverage pass must first
classify each as obsolete, calibration-only, dynamically referenced, or missing
from current configuration.
