# Pending Development

This is the canonical list of planned work for TheTower. Keep actionable items
here; move completed work to `docs/modules/completed_tasks_log.md`.

The older `docs/modules/ROADMAP.md` and `docs/modules/roadmap_priorities.md`
are retained as retired historical snapshots. Their unfinished items were
audited against the codebase and incorporated below on 2026-07-13.

## Current validation gates

- [ ] Live-revalidate the refreshed Home `Battle` template at the next Home
  boundary and separately capture/revalidate the `Resume Battle` state. Current
  `Battle` artwork was captured on 2026-07-13 and matches its canonical fixture,
  but the live start used the guarded OCR fallback before the asset was updated.
- [ ] Live-revalidate the distinct Home Store-badge template at the next daily
  availability. The badge was captured on Home and matches its canonical
  fixture; it was cleared before the new template could be exercised live. The
  in-run badge, Store navigation, active claim, ad skip, return to the running
  game, and inactive cooldown path are live-verified.
- [ ] Run the default GC strategy under the full main loop for at least one
  natural Game Over -> Retry boundary and confirm the exclusive startup gate
  repeats EHLS then EALS while Target Priority remains session-scoped.

## GC run initialization

- [ ] Implement a once-per-continuous-session GC preflight, always after the
  current run's EHLS/EALS startup gate:
  - Cards must use the fixed `GC` deck; otherwise surrender, correct, restart.
  - Workshop must use the `Farm` preset; otherwise surrender, correct, restart.
  - Bots must use the `Farm` preset; otherwise surrender, correct, restart.
  - Auto Pick Perks must be enabled; enable it in place if necessary.
  - Ultimate Weapons that should be active must be on; Golden Tower and Black
    Hole are permanent, so no sync enforcement with Death Wave is required.
  - Record the validation in explicit session logs so uninterrupted automation
    does not repeat it every run.
- [ ] Define the GC module preset and validate it during session preflight.
  - Inventory every equipped module slot and desired module name.
  - Tag the module shapes/icons with canonical module names and capture stable
    templates or another reliable visual representation.
  - If the module setup is wrong, surrender the run, correct it, and restart.
- [ ] Decide whether to validate perk bans and the Auto Pick Perk order during
  the session preflight. Keep automation-owned perk selection as a later option.
- [ ] Add the Damage Slider to new-GC-run initialization.
  - Detect its current position/value before changing it.
  - Define the desired setting in strategy configuration rather than hardcoding
    it in the runtime.
  - Verify the applied setting and make the operation safe to repeat.

## Runtime control

- [ ] Add a single-instance lock so two automation processes cannot send
  competing taps to the same ADB target.
- [ ] Provide a convenient pause/resume interface so stopping the process with
  `Ctrl-C` is unnecessary.
  - Build on the existing control-file and `tools/automation_ctl.py` support.
  - Make pause state obvious and ensure manual input does not race automation.
  - Support extending or cancelling pending recovery timers.
- [ ] Detect likely manual player activity and automatically yield tap authority.
  - Treat an unexpected Go Home/manual navigation sequence during an active run
    as operator activity rather than an error to immediately undo.
  - Pause automation while screens continue changing or recent external input
    is evident.
  - After a configurable static grace period, warn before offering/performing a
    guarded return to the running battle.
  - Make the grace period interruptible and extendable through CLI/GUI controls.
- [ ] Create a small GUI control window.
  - Show current primary state, menu, overlays, run mode, and pause status.
  - Provide pause, resume, return-now, and extend-recovery controls.
  - Keep the GUI as a thin client over the same controls used by the CLI.

## State coverage and recovery

- [ ] Audit every reachable menu, popup, overlay, and transition.
  - Exercise each state on a live 1080x1920 device and save representative
    screenshots/templates where appropriate.
  - Ensure expected screens never resolve to `UNKNOWN`.
  - Add regression fixtures for every recognized primary, secondary, menu, and
    overlay state.
- [ ] Perform a guided live traversal of every reachable screen and a complete
  farm-run lifecycle to find missing or stale templates.
  - Save one canonical fixture per distinct screen state, not routine gameplay
    screenshots.
  - Exercise Battle/Resume, Exit Battle, Game Stats, Store availability,
    modules, cards, workshop/bot presets, perks, labs, and transient dialogs.
  - Compare every fixture against the recursive static template audit and add
    explicit state definitions for any expected `UNKNOWN` result.
- [ ] Replace the current non-running recovery behavior with an interruptible
  five-minute timer whenever the primary state is not `RUNNING`.
  - Cancel/reset the timer immediately when `RUNNING` returns.
  - Warn at least twice before recovery is performed.
  - Allow pause, cancellation, and extension through CLI/GUI controls.
  - Do not interrupt an automation action that is making expected progress.
  - Attempt the least destructive route back to the game first; escalate only
    when a safe return action fails.

## Detection architecture

- [ ] Finish matcher API and policy consolidation after fixture coverage is
  broad enough to make the compatibility decision safely.
  - Migrate remaining `utils.template_matcher` shim callers to `core.matcher`.
  - Measure color/padding profiles against representative positive and negative
    fixtures.
  - Choose one canonical runtime policy deliberately, then remove the
    compatibility shim and profile split.
- [ ] Design a daily-rollover coordinator that detects the game-day boundary
  and checks for the Store badge with a fresh frame before taking any refresh
  action.
  - At the 2026-07-13 17:00 PDT reset, two Daily Missions appeared while the
    available Daily Gems Store badge remained absent.
  - Closing and reopening the in-run menu did not refresh the badge; opening
    Daily Missions and returning to the game did.
  - Treat the observed 17:00 PDT boundary as a candidate UTC-midnight reset;
    verify standard-time behavior before making that the canonical rule.
  - Handle both a running process crossing the boundary and a process starting
    after the boundary, with explicit once-per-game-day state and logs.
  - If the first badge check is negative, stop at a visible decision point:
    compare a controlled navigation refresh, a direct guarded Store probe, and
    waiting for the game bug to be fixed before choosing an implementation.
  - Schedule the check through the same exclusive tap authority as startup,
    pause, handlers, and operator activity; it must not race another action.
- [ ] Audit the floating ad-gem diamond and replace timed blind tapping if a
  reliable shape/color/contour or tracked-motion detector can locate it.
  - Compare the existing heuristic and orphaned directional templates against
    live samples.
  - Require a fresh detection immediately before each tap.
- [ ] Audit the home-screen `CLAIM` control in available and unavailable states;
  determine whether its artwork changes and split templates/state rules if so.
- [ ] Add composite state-definition logic such as `all_of`, `any_of`, and
  explicit exclusions.
  - Preserve the current simple `match_keys` form for straightforward states.
  - Add deterministic conflict handling for mutually exclusive primary, menu,
    and secondary indicators instead of relying only on YAML order or raising.
  - Add per-rule confidence overrides only where the existing per-clickmap-entry
    threshold is insufficient.
- [ ] Add a `LAB_READY` overlay and handler after capturing a stable live
  template and expected behavior, then design optional Lab automation around a
  configured research queue and explicit spending safeguards.
- [ ] Add daily-quest claiming separately from the existing daily-gem handler.
  The 2026-07-13 reset began at `2/8 Missions` with two new missions and the next
  pair due eight hours later; the menu badge changed from 3 to 1 after viewing
  the screen while one completed mission remained claimable.
- [ ] Add automated overlay coexistence and state-transition regression tests.
  This should be completed as part of the full live state-coverage audit above.

## Capture and action architecture

> Deferred on 2026-07-13 while higher-priority runtime and validation work is
> completed. Retain these findings for a later architecture package.

- [ ] Evaluate an app-owned low-latency frame source for scrolling and other
  multi-frame decisions instead of treating the level-skip H.264 stream as a
  one-off implementation detail.
  - Define a small frame-source interface with sequence, capture timestamp,
    freshness, and `wait for a frame after this input` semantics.
  - Replace fixed post-swipe sleeps with bounded observation of fresh frames and
    require consecutive stable frames before declaring settle/edge conditions.
  - Preserve pre- and post-action source-screen guards and retain a guarded raw
    screenshot fallback when the stream is unavailable or stale.
  - Account for this emulator's 180-second `screenrecord` maximum by supporting
    restart/handoff without exposing buffered or pre-action frames as current.
  - Benchmark latency, ADB load, missed transitions, and edge detection against
    the current screenshot-and-sleep implementation before migrating callers.
  - Decide stream ownership, shutdown, and single-instance behavior at the App
    level so handlers cannot start competing screenrecord processes.
- [ ] Review the tap/action execution architecture together with the shared
  frame source. A post-input frame barrier may belong in the action layer rather
  than being reimplemented independently by scrolling and every handler.

## Handler architecture

- [ ] Replace ad-hoc handler calls with a centralized handler registry and
  dispatcher.
  - Define a consistent handler interface (`should_run()` / `run()` or an
    equivalent protocol) before converting existing handlers.
  - Support registration by primary state, menu, secondary state, or overlay.
  - Preserve ordering and mutual-exclusion rules for handlers that can tap.
  - Integrate per-handler pause/resume with the global controls rather than
    creating a second control mechanism.
- [ ] Harden the Game Over handler.
  - Make More Stats paging/capture failures recoverable rather than forcing a
    global WAIT for every capture problem.
  - Add automated coverage for RETRY and WAIT; HOME is now covered.
- [ ] Replace routine Game Over screenshots with structured OCR capture.
  - OCR every Round Stats section into named fields and retain confidence/raw
    text for uncertain rows.
  - Store records in a durable format (initially JSON/JSONL or SQLite after
    comparing query and migration needs).
  - Keep screenshots only when OCR validation fails or confidence is below the
    configured threshold.
## Configuration and developer tooling

- [ ] Make Codex shell execution use the repository `.venv` by default instead
  of inheriting the SSH session's system `python` and user-level `pytest`.
  - Determine the supported workspace-level Codex instruction/configuration or
    checked-in runner that will persist this behavior across Codex sessions.
  - Until then, require Codex commands to invoke `.venv/bin/python` and
    `.venv/bin/python -m pytest` explicitly and fail clearly if `.venv` is absent.
- [ ] Continue the full template audit begun on 2026-07-13.
  - The recursive static validator now checks nested entries, regions, files,
    image readability, and geometry; resolve/classify its dated orphan list.
  - Add fixture-based match verification so a present template is also proven
    current against at least one canonical screen.
  - Require the recursive validator in the normal test/checkpoint workflow.
- [ ] Build one recursive validator for clickmap and state-definition schema.
  - Validate nested entries, roles, regions, templates, taps, swipes, thresholds,
    and dangling YAML references.
  - Enforce or migrate toward one naming convention across clickmap keys and
    state YAML.
  - Detect drift and optionally emit state-definition stubs for new indicators.
  - Consolidate or replace the partial `test/clickmap_integrity.py` and
    `test/validate_state_defs.py` scripts.
- [ ] Allow targeted editing of an existing clickmap entry without rewriting the
  entire clickmap document.
- [ ] Extract duplicated handler helpers such as image/session utilities only
  after the unused-code audit identifies their actual call sites.

## Codebase maintenance

- [ ] Audit the repository for unused or obsolete code, configuration, assets,
  generated files, tests, tools, and documentation.
  - Use import/reference searches, runtime entry-point tracing, strategy and
    clickmap validation, and test coverage as evidence.
  - Classify findings as active, generated, developer tooling, archival, or
    removable.
  - Check dynamic/YAML references before treating apparently unused Python or
    assets as dead.
  - Produce a reviewable removal/archive proposal before moving or deleting
    anything.
  - Include legacy `.old` files and generated documentation in the audit, while
    distinguishing source artifacts from intentionally retained history.
  - Specifically verify whether the `Cards:GCFarmEarly`, `Cards:GCFarmLate`,
    `cards:locked:*`, deck indicators, card navigation entries, and their PNG
    templates are still referenced by active strategies before retaining or
    removing them.

## Development process

- [ ] Treat behavioral blockers as explicit decision points.
  - Stop further state-changing actions, preserve evidence, and report the exact
    failed guard or assumption immediately.
  - Present repair, redesign, defer, and workaround options with their safety
    and maintenance tradeoffs; wait for agreement before choosing a behaviorally
    different path.
  - Do not lower a guard, use a blind/manual substitute, or encode an observed
    game bug as permanent behavior merely to finish the current test.
- [ ] Re-examine architecture whenever a new capability exposes duplicated
  polling, sleeps, state ownership, or recovery logic. Prefer a short measured
  design spike when it could simplify multiple pending features.
- [ ] Use incremental Git commits while iterating.
  - Each commit should contain one coherent, tested behavior or audit result.
  - Review staged files before committing and exclude editor swap files,
    captures, logs, and unrelated local work.
  - Do not preserve an existing implementation merely for compatibility when a
    simpler, safer, and better-tested design can replace it.
