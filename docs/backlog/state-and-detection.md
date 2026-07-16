# State, Detection, Capture, and Action Backlog

This file contains active state-observation and action-authority work. Current
architectural boundaries are in [`../architecture/runtime.md`](../architecture/runtime.md).
Completed and superseded detail remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## State coverage and recovery

- [ ] Audit every reachable menu, popup, overlay, and transition.
  - Exercise each state on a live 1080x1920 device and save representative
    screenshots/templates where appropriate.
  - Ensure expected screens never resolve to `UNKNOWN`.
  - Add regression fixtures for every recognized primary, secondary, menu, and
    overlay state.
  - The active-battle and Home-with-Resume evidence is recorded in
    [`../ui_state_traversal_2026-07-14.md`](../ui_state_traversal_2026-07-14.md).
    A full no-battle Home traversal remains pending.
- [ ] Perform a guided live traversal of every reachable screen and a complete
  farm-run lifecycle to find missing or stale templates.
  - Save one canonical fixture per distinct screen state, not routine gameplay
    screenshots.
  - Exercise Battle/Resume, Exit Battle, Game Stats, Store availability,
    modules, cards, workshop/bot presets, perks, labs, and transient dialogs.
  - Compare every fixture with the recursive static template audit and add
    explicit state definitions for expected `UNKNOWN` results.
- [ ] Replace non-running recovery with an interruptible five-minute timer.
  - Cancel or reset it immediately when `RUNNING` returns.
  - Warn at least twice before recovery.
  - Allow pause, cancellation, and extension through CLI/GUI controls.
  - Do not interrupt an automation action making expected progress.
  - Attempt the least destructive route back to the game first.

## Detection architecture

- [ ] Finish matcher API and policy consolidation after fixture coverage is
  broad enough to make the compatibility decision safely.
  - Migrate remaining `utils.template_matcher` shim callers to `core.matcher`.
  - Measure color/padding profiles against representative positive and negative
    fixtures.
  - Choose one canonical policy deliberately, then remove the compatibility
    shim and profile split.
- [ ] Preserve the scheduled floating-gem intercept and add a fresh on-screen
  `RUNNING` authorization check without delaying its cadence.
  - Bob follows an approximately 180–190 px circular orbit around `(540,480)`;
    the existing `(542,671)` bottom intercept is proven. Bob speed is static;
    retrieval timestamps, buffering, skipped sequences, and manual labels are
    not game-motion evidence.
  - Historical directional templates and the magenta heuristic are unsafe as
    single-frame detectors. Multi-frame tracking remains optional research.
  - The positive and negative capture provenance is preserved in the
    [`2026-07-14 architecture history`](../architecture/history/architecture_direction_2026-07-14.md#floating-gem-bob-conclusion).
    Promote reviewed fixtures from `/tmp` before relying on them durably.
  - Have an app-owned observer publish a short-lived `RUNNING` lease. The tapper
    should make an O(1) check immediately before each tap and skip stale or
    invalid leases without shifting the absolute monotonic schedule.
- [ ] Audit the Home `CLAIM` control in available and unavailable states;
  determine whether its artwork changes and split templates/state rules if so.
- [ ] Add composite state-definition logic such as `all_of`, `any_of`, and
  explicit exclusions.
  - Preserve simple `match_keys` for straightforward states.
  - Add deterministic conflict handling for mutually exclusive primary, menu,
    and secondary indicators.
  - Add per-rule confidence overrides only when entry thresholds are
    insufficient.
- [ ] Add a `LAB_READY` overlay and handler after capturing stable evidence,
  then design optional Lab automation around a configured queue and explicit
  spending safeguards.
- [ ] Add automated overlay-coexistence and state-transition regression tests as
  part of the full live state-coverage audit.

## Capture and action architecture

- [ ] Evaluate an app-owned low-latency frame source for scrolling and other
  multi-frame decisions.
  - Define sequence, capture-time, freshness, and post-input frame semantics.
  - Replace fixed post-swipe sleeps with bounded fresh-frame observation and
    require stable consecutive frames before declaring settle or edge.
  - Preserve source-screen guards and a guarded raw screenshot fallback.
  - Handle the device's 180-second `screenrecord` limit without exposing
    buffered or pre-action frames as current.
  - Benchmark latency, ADB load, missed transitions, and edge detection before
    migrating callers.
  - Keep frame-source ownership at the App layer so handlers cannot start
    competing recording processes.
- [ ] Review tap/action execution with the shared frame source.
  - Decide whether a post-input frame barrier belongs in the action layer.
  - Include a thread-safe UI-state snapshot and short-lived action lease.
  - Preserve absolute monotonic schedules; a stale guard skips the current
    action instead of phase-shifting later attempts.
