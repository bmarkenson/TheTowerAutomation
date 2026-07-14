# `core/damage_adjuster.py`

Read-only inspection of the persistent Damage detail panel in a running battle.

- `read_damage_adjuster(screenshot)` verifies the `Percent Of Enemy Health`
  panel guard and OCRs the configured percentage. It intentionally ignores the
  absolute damage shown below it because that value changes during the run.
- `open_damage_adjuster()` requires `RUNNING` plus `ATTACK_MENU`, taps the
  center of the Damage label through a dedicated button entry, then polls
  ordinary ADB screenshots for the persistent panel.
- `dismiss_damage_adjuster()` requires the panel guard before tapping the
  non-interactive dimmed backdrop, then verifies that Attack is restored.

`DAMAGE_ADJUSTER` is a primary state because the modal dims the underlying
Attack screen enough that its normal `RUNNING` and `ATTACK_MENU` templates no
longer match. This keeps an expected persistent modal out of `UNKNOWN` recovery.

The workflow does not use the H.264 stream and never taps either adjustment
arrow. The desired GC percentage remains strategy configuration work.
