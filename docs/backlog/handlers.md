# Handler Backlog

This file contains active handler orchestration and lifecycle work. Historical
completion evidence remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Mission rewards

- [ ] Live-confirm commit `3046c93`'s outcome-specific scheduling now that it
  is deployed: a productive reward sweep becomes eligible again after two
  minutes, an empty sweep backs a persistent alert off for 30 minutes, and the
  residual-badge diagnostic agrees with the restored reward hub. The guarded
  production smoke had no remaining Mission reward badge with which to exercise
  these natural-trigger outcomes.
- [ ] Deploy commit `e14999c` at a safe process boundary and confirm that a
  scheduler-owned Event Mission pass uses overlapping downward viewports. The
  repaired traversal and paused one-off validation are preserved in the
  [resolved issue](../issues/resolved-2026.md#event-mission-claim-search-skipped-past-the-claimable-row).

## Dispatch architecture

- [ ] Replace ad-hoc handler calls with a centralized handler registry and
  dispatcher.
  - Define a consistent `should_run()` / `run()` interface or equivalent.
  - Register by primary state, menu, secondary state, or overlay.
  - Preserve ordering and mutual exclusion for handlers that can tap.
  - Integrate per-handler pause/resume with global controls rather than adding a
    second control mechanism.
