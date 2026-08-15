# State, Detection, Capture, and Action Backlog

This file contains active state-observation and action-authority work. Current
architectural boundaries are in [`../architecture/runtime.md`](../architecture/runtime.md).
Completed and superseded detail remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## State coverage and recovery

- [ ] Complete state-coverage auditing across every reachable menu, popup,
  overlay, transition, and full Farm-run lifecycle.
  - Exercise each state on live supported `1080x1920` and `720x1280` devices
    and save representative screenshots/templates where appropriate.
  - Exercise Battle/Resume, Exit Battle, Game Stats, Store availability,
    Modules, Cards, Workshop/Bot presets, Perks, Labs, and transient dialogs.
  - Accept `UNKNOWN` only for genuinely unsupported states; add explicit state
    definitions for expected screens.
  - Add regression fixtures for every recognized primary, secondary, menu, and
    overlay state, including overlay coexistence and state transitions.
  - Keep one canonical fixture per distinct screen state and compare the live
    inventory with the recursive static template audit.
  - The active-battle, Home-with-Resume, and completed no-battle Home evidence
    is recorded in
    [`../ui_state_traversal_2026-07-14.md`](../ui_state_traversal_2026-07-14.md).
- [ ] Replace non-running recovery with an interruptible five-minute timer.
  - Cancel or reset it immediately when `RUNNING` returns.
  - Warn at least twice before recovery.
  - Allow pause, cancellation, and extension through CLI/GUI controls.
  - Do not interrupt an automation action making expected progress.
  - Attempt the least destructive route back to the game first.
  - Confirm deployed commit `af3d1b0` for `ISSUE-2026-041` at one natural
    completed-battle Home launch: Free Ticket
    may be claimed only by the exact retained launch, at most one verified
    Battle retry may follow two stable fresh Home `NEW_BATTLE` observations,
    and an
    exhausted Home ad-gem transaction must remain circuit-broken across
    equivalent heartbeats without blocking lifecycle progress.
- [ ] **Deferred:** evaluate a bounded global recovery supervisor before adding
  more one-off recovery behaviors for sustained unsupported or `UNKNOWN`
  states.
  - Write an architecture decision comparing continued state-specific recipes,
    a shared supervisor, and a hybrid. Evaluate incident frequency, operator
    burden, false-action risk, testability, and long-term maintenance cost
    before deciding whether the broader mechanism is worth implementing.
  - Reconcile with the existing interruptible non-running timer instead of
    creating a second scheduler or authority owner.
  - Treat the supervisor, if selected, as an orchestrator of registered typed
    recovery recipes. `UNKNOWN` alone must never authorize a tap, swipe, Back,
    app restart, Battle/Retry, Surrender, or terminal action.
  - Define distinct evidence and postconditions for incomplete capture,
    transient detection loss, a recognized blocking modal, lost foreground,
    process/ADB-target change, an expected transition still in flight, and a
    genuinely unsupported screen.
  - Preserve exact runtime, PID, ADB target/generation, workflow/control
    operation, canonical battle identity when battle-bound, and transition-
    receipt ownership. Activity scope is report metadata only. Pause, Stop, manual control,
    changed ownership, or uncertain accepted input must abort or fail closed
    without replay.
  - Retain attempt and circuit-breaker state across equivalent heartbeats so
    time or one detector miss cannot silently replenish input. Decide
    explicitly which observation-only state may survive process replacement;
    never replay an unresolved mutation after restart.
  - Specify a bounded escalation ladder from observation/backoff, through only
    freshly verified low-risk recipes, to an operator-visible indefinite hold.
    Validate it first with incident fixtures, transition fault injection, and
    an observation-only rollout before enabling any recovery input.

## Detection architecture

- [ ] Revalidate and harden Coins/min OCR for the case-sensitive `q` and `Q`
  magnitude suffixes. Retain the relevant crop and OCR candidates when a
  suffix is absent or ambiguous, cover both suffixes with fixtures, and verify
  the accepted live value cannot silently lose or change its scale. Track the
  evidence in
  [`ISSUE-2026-024`](../issues/open-2026.md#coinsmin-visual-ocr-failed-to-recognize-q-or-q).
- [ ] Harden runtime wave consumption against isolated OCR rollbacks.
  - Preserve stateless per-frame OCR; do not restore fixed progression-rate,
    digit-width, or wave-ceiling assumptions.
  - Add battle-aware or cross-frame confirmation before a lower observation
    replaces the app's accepted wave, with an explicit new-battle reset path.
  - Automatically retain the rejected frame, crop, candidate support, and
    confidence so the underlying visual failure can be reproduced.
  - Cover the 1070 -> false 80 -> 1270 sequence and genuine new-battle reset in
    regression tests. The originating anomaly is preserved in its
    [open issue dossier](../issues/open-2026.md#wave-ocr-dropped-the-leading-digits-from-wave-1180).
- [ ] Finish matcher API and policy consolidation after fixture coverage is
  broad enough to make the compatibility decision safely.
  - Migrate the remaining `utils.template_matcher` shim callers in
    `core/floating_button_detector.py` and `core/state_detector.py` to
    `core.matcher`.
  - Measure color/padding profiles against representative positive and negative
    fixtures.
  - Choose one canonical policy deliberately, then remove the compatibility
    shim and profile split.
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
## Capture and action architecture

- [ ] Build and validate an app-owned low-latency frame/state source for
  multi-frame decisions and short-lived action authorization.
  A task-bounded, operator-authorized no-control viewer is a separate passive
  observation facility and does not complete or substitute for this runtime
  architecture.
  - Define sequence, capture-time, freshness, and post-input frame semantics.
  - Publish a thread-safe UI-state snapshot and short-lived `RUNNING` action
    lease. Immediately before a scheduled floating-gem tap, make an O(1)
    freshness check and skip stale authority without phase-shifting the
    absolute monotonic cadence. Preserve Bob evidence in the
    [`2026-07-14 architecture history`](../architecture/history/architecture_direction_2026-07-14.md#floating-gem-bob-conclusion).
  - Replace fixed post-swipe sleeps with bounded fresh-frame observation and
    require stable consecutive frames before declaring settle or edge.
    The common scroll primitives gained consecutive edge confirmation in
    `ISSUE-2026-031`; migrate the remaining bespoke stateful list traversals
    only as their semantic stop boundaries are modeled.
  - Preserve source-screen guards and a guarded raw screenshot fallback.
  - Handle the device's 180-second `screenrecord` limit without exposing
    buffered or pre-action frames as current.
  - Benchmark latency, ADB load, missed transitions, and edge detection before
    migrating callers.
  - Keep frame-source ownership at the App layer so handlers cannot start
    competing recording processes.
  - Decide whether a post-input fresh-frame barrier belongs in the action
    layer while preserving source-screen guards and action ownership.
- [ ] Reduce and characterize emulator FPS degradation from the bounded passive
  scrcpy viewer. The first full-resolution 15-FPS/2-Mbps run reduced the
  emulator-side counter from approximately 55–59 FPS to 45 FPS. Compare the
  1280/15-FPS/2-Mbps low-load profile with only the minimum useful alternatives
  and retain production capture cadence, ADB health, scrcpy `--print-fps`, and
  host-performance evidence. Game speed x1 or x2 may be tested for readability
  and render headroom, but is not a substitute for transport-load measurement.
