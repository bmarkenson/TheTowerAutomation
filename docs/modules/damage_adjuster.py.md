# `core/damage_adjuster.py`

Inspection and guarded feedback control of the persistent Damage detail panel
in a running battle.

- `read_damage_adjuster(screenshot)` verifies the `Percent Of Enemy Health`
  panel guard and OCRs the configured percentage. Wrong-sized or majority-black
  frames cannot supply panel evidence. The reader intentionally ignores the
  absolute damage shown below it because that value changes during the run.
- `open_damage_adjuster()` requires `RUNNING` plus `ATTACK_MENU`, taps the
  center of the Damage label through a dedicated button entry, then polls
  ordinary ADB screenshots for the persistent panel.
- `dismiss_damage_adjuster()` requires the panel guard before tapping the
  non-interactive dimmed backdrop, then verifies that Attack is restored.
- `configure_damage_slider(expected, mode=...)` supports `observe` and
  `enforce`. Enforcement reacquires the panel guard, selector mode, and OCR
  percentage before every explicit increase/decrease action, requires each
  observed step to move strictly toward the target, verifies the final value,
  and dismisses back to `RUNNING/ATTACK_MENU`. Unknown, unchanged, cycling, or
  regressive feedback fails closed.

`DAMAGE_ADJUSTER` is a primary state because the modal dims the underlying
Attack screen enough that its normal `RUNNING` and `ATTACK_MENU` templates no
longer match. This keeps an expected persistent modal out of `UNKNOWN` recovery.

The workflow does not use the H.264 stream. Arrow positions are static action
geometry and acquire authority only from fresh complete panel evidence; the
read-only OCR fixture intentionally does not act as tap geometry.
