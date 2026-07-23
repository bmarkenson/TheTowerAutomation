# Handler Backlog

This file contains active handler orchestration and lifecycle work. Historical
completion evidence remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Mission rewards

- [ ] Make the Daily Missions weekly-chest claim converge across the complete
  bounded list rather than only the initial viewport. Preserve fresh
  `DAILY_MISSIONS` authority before every scroll and tap, add visible/offscreen
  regression fixtures, and live-validate against the preserved opportunity
  recorded in
  [`../observed_issues.md`](../observed_issues.md#offscreen-weekly-mission-chest-was-skipped).
- [ ] Deploy and live-verify open-menu Mission reward scheduling at a safe
  process boundary. Commit `2b4315d` uses section badges when the
  in-battle menu is verified open and retains the attention-dot trigger when it
  is verified closed.
- [ ] Deploy commit `e14999c` at a safe process boundary and confirm that a
  scheduler-owned Event Mission pass uses overlapping downward viewports. A
  paused one-off live pass with the repair claimed all four available Event
  rewards and returned to the active battle.

## Dispatch architecture

- [ ] Replace ad-hoc handler calls with a centralized handler registry and
  dispatcher.
  - Define a consistent `should_run()` / `run()` interface or equivalent.
  - Register by primary state, menu, secondary state, or overlay.
  - Preserve ordering and mutual exclusion for handlers that can tap.
  - Integrate per-handler pause/resume with global controls rather than adding a
    second control mechanism.
