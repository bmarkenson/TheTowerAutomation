# Handler Backlog

This file contains active handler orchestration and lifecycle work. Historical
completion evidence remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Dispatch architecture

- [ ] Replace ad-hoc handler calls with a centralized handler registry and
  dispatcher.
  - Define a consistent `should_run()` / `run()` interface or equivalent.
  - Register by primary state, menu, secondary state, or overlay.
  - Preserve ordering and mutual exclusion for handlers that can tap.
  - Integrate per-handler pause/resume with global controls rather than adding a
    second control mechanism.

## Game Over

- [ ] Make More Stats paging/capture failures recoverable instead of forcing a
  global WAIT for every capture problem.

Existing safeguards that must remain intact include persistent control polling
during Game Over WAIT, action blocking under `PAUSED`, clean exit under
`STOPPED`, and exact visible Perks-button plus destination-panel evidence. See
[`../issues/resolved-2026.md`](../issues/resolved-2026.md) for the originating
failures and regressions.

## Mission tracking

- [ ] Live-validate one complete top-to-bottom Event Mission inventory at the
  next natural Event badge without interrupting or surrendering the active
  battle.

The handler already piggybacks inventory on guarded Event-badge visits,
persists accepted OCR observations in `logs/event_mission_tracker.json`, warns
after 72 hours incomplete and 48 hours without progress, and preserves prior
state when an OCR inventory is incomplete.
