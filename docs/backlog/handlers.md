# Handler Backlog

This file contains active handler orchestration and lifecycle work. Historical
completion evidence remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Mission rewards

- [ ] Deploy and live-verify open-menu Mission reward scheduling at a safe
  process boundary. Commit `2b4315d` uses section badges when the
  in-battle menu is verified open and retains the attention-dot trigger when it
  is verified closed.
- [ ] Deploy commit `e14999c` at a safe process boundary and confirm that a
  scheduler-owned Event Mission pass uses overlapping downward viewports. A
  paused one-off live pass with the repair claimed all four available Event
  rewards and returned to the active battle.
- [ ] Repair false Event Mission stall warnings recorded in
  [`../observed_issues.md`](../observed_issues.md#event-mission-warnings-treated-stale-rows-as-current-stalled-missions).
  A warning must require repeated, sufficiently fresh observations of the same
  incomplete tier; elapsed time without another observation is not evidence of
  stalled progress. Reconcile claimed or advanced tiers before the post-claim
  inventory, retain OCR-missed rows without granting them warning authority,
  and add regressions for claim-before-inventory, a stale `Login for 7 days`
  row alongside `Login for 10 days`, and a mission seen only once.

## Dispatch architecture

- [ ] Replace ad-hoc handler calls with a centralized handler registry and
  dispatcher.
  - Define a consistent `should_run()` / `run()` interface or equivalent.
  - Register by primary state, menu, secondary state, or overlay.
  - Preserve ordering and mutual exclusion for handlers that can tap.
  - Integrate per-handler pause/resume with global controls rather than adding a
    second control mechanism.
