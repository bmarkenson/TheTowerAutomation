# Pending Development

This is the canonical list of planned work for TheTower. Keep actionable items
here; move completed work to `docs/modules/completed_tasks_log.md`.

The older `docs/modules/ROADMAP.md` and `docs/modules/roadmap_priorities.md`
are retained as retired historical snapshots. Their unfinished items were
audited against the codebase and incorporated below on 2026-07-13.

The result and scope boundary of the 2026-07-14 architecture-review thread are
recorded in `docs/architecture_direction_2026-07-14.md`. In short: retain the
clickmap as a declarative UI evidence/action-geometry catalog, but keep state,
lifecycle, orchestration, and action authority in separate layers.

## Immediate architecture handoff

- [x] Fix the paused exclusive-startup-gate semantics before continuing the
  broader architecture package.
  - Initialization incompleteness must not depend on the current primary screen
    being `RUNNING`; a transient `UNKNOWN` observation is not completion.
  - While paused, continue capture, detection, and status reporting, but issue
    no strategy or handler actions.
  - Log gate completion only after the strategy completion assertion is true.
  - Add regression coverage for paused startup and for a
    `RUNNING -> UNKNOWN -> RUNNING` observation sequence.
- [x] After that fix, rerun the focused architecture/initialization tests and
  live-validate paused status behavior. Inspect the process, lock, control file,
  and latest logs first; do not assume the dated live instance is still paused.

## Current validation gates

- [x] Live-revalidate the refreshed Home `Battle` template at a genuine new-run
  boundary and confirm that `NEW_BATTLE` arms the lifecycle boundary. On
  2026-07-14 at ADB port 5565, repeated paused observations classified the Home
  control as `NEW_BATTLE` by OCR at 96.0 confidence without activating the
  startup gate. The guarded visible Battle tap started exactly one gate; EHLS
  completed at wave 20 and EALS at wave 30. The distinct `Resume Battle` path
  remains live-validated separately.
- [ ] Live-revalidate the distinct Home Store-badge template at the next daily
  availability. The badge was captured on Home and matches its canonical
  fixture; it was cleared before the new template could be exercised live. The
  in-run badge, Store navigation, active claim, ad skip, return to the running
  game, and inactive cooldown path—including its automatic Return-to-Game
  action—are live-verified.
- [ ] Live-validate the once-per-UTC-day Daily Gem Store probe across the next
  game-day boundary. Confirm that its direct Store navigation claims the gem
  despite the initially missing badge and that persisted completion suppresses
  a second probe after an automation restart.
- [x] Fix the Daily Gem `NOT_READY` return policy around transitional Home.
  Scheduled probes now wait for `RUNNING` instead of preempting Home -> Battle.
  The retained rare Home-origin route returns through the bottom Home selection
  and verifies `HOME_SCREEN`; the normal in-run route verifies `RUNNING`. Live
  validation detected the manually claimed gem's cooldown, returned to the
  battle, and persisted UTC day `2026-07-15` as `not_ready`.
- [x] Run the profile-based GC strategy under the full main loop for at least
  one natural Game Over -> Retry boundary. Confirm one new lifecycle boundary,
  EHLS then EALS initialization, the selected profile's Target Priority policy,
  and no false completion across transient `UNKNOWN` frames.
  - The Tier 19 run ended naturally at wave 2558 on 2026-07-15, the explicit
    `gc_farm_t19_experiment` profile retried it, and EHLS then EALS completed at
    waves 20 and 30. Preserve mode emitted no Target Priority action. The
    existing transient-`UNKNOWN` regressions remained green.
  - A later natural Tier 19 boundary at wave 4969 exercised the complete new
    Game Over capture and Retry path. The replacement process consumed the
    persisted `RUNNING/RETRY` direction, created one new lifecycle boundary,
    gold-boxed EHLS at wave 20 and EALS at wave 30, completed the startup
    assertion, and again emitted no Target Priority action.

## GC run initialization

- [x] Replace the tactical manual-Target-Priority variant with shared GC family
  profiles.
  - `gc_farm_t18` and `gc_farm_t19_experiment` are stable profile identities
    generated from one concrete `gc_farm` builder.
  - Preserve mode omits the Target Priority assertion, variable, rule, and
    action; enforce mode carries that profile's explicit order through the
    executor and keeps the startup gate closed until verification succeeds.
  - `gc` selects Tier 18. The former `gc_manual_target_priority` name remains a
    temporary compatibility alias to the Tier 19 profile without seeding a
    completion variable.
  - The observed Tier 19 experiment reached wave 2558, earned 872.38q coins,
    was killed by Scatter, and death-defied 12 times. No new configuration
    difference from Tier 18 was established, so the profile gained no
    speculative fields beyond the concrete session requirements below.

- [x] Implement a once-per-continuous-session GC preflight, always after the
  current run's EHLS/EALS startup gate:
  - Cards must use the fixed `GC` deck.
    The active `GC` preset now has a live-validated composite identity/selection
    template and canonical fixture. Guarded live dispatch now verifies it.
  - Workshop must use the `Farm` preset.
    The Workshop screen guard, fixed-slot `Farm` identity template, and
    green-selected versus cyan-inactive border classifier are backed by a live
    canonical fixture. Guarded Go Home/Workshop validation is integrated.
  - Bots must use the `Farm` preset.
    The Bots-screen guard and active `Farm` composite template are now backed by
    canonical live fixtures and visible-evidence Home navigation.
  - Guardian chips must have `Fetch`, `Summon`, and `Scout` equipped. Separate
    equipped-slot templates, validation, and guarded navigation are integrated.
  - Auto Pick Perks must be enabled. Positive checkbox evidence and guarded
    Perks navigation are live-validated.
  - Ultimate Weapons that should be active must be on; Golden Tower and Black
    Hole are permanent, so no sync enforcement with Death Wave is required.
    Read-only multi-position validation now verifies all nine profile-provided
    weapons and Spotlight missiles.
  - Record the validation in explicit session logs so uninterrupted automation
    does not repeat it every run. This is implemented and live-validated.
  - The current mismatch policy deliberately blocks the exclusive gate and
    logs exact evidence. Automated correction and Surrender remain disabled
    until each mutation path and its authority policy are implemented.
  - A post-Retry live pass on 2026-07-15 validated the GC Cards deck, both Farm
    presets, Fetch/Summon/Scout, Auto Pick Perks, all nine required Ultimate
    Weapons, and Spotlight missiles. It logged one complete structured session
    result and resumed the active battle without Surrender.
  - Repeating the route exposed a Home-render transition that made Event
    identity score 0.23 on a stale frame. Guarded visible navigation now
    recaptures and revalidates the primary state for every bounded retry; the
    repaired Event, Bots, Guild, Guardian, and Resume route passed live.
- [ ] Define the GC module preset and validate it during session preflight.
  - Inventory every equipped module slot and desired module name.
  - Tag the module shapes/icons with canonical module names and capture stable
    templates or another reliable visual representation.
  - If the module setup is wrong, surrender the run, correct it, and restart.
- [ ] Decide whether to validate perk bans and the Auto Pick Perk order during
  the session preflight. Keep automation-owned perk selection as a later option.
- [ ] Add the Damage Slider to new-GC-run initialization.
  - [x] Detect its current position/value before changing it. The persistent
    Damage panel, dedicated label-center open action, guarded dismissal, and OCR
    reader are fixture-backed. Live evidence on 2026-07-14 read `1E-22%`; the
    `94.80M` value beneath it is derived run damage and is intentionally ignored.
    Ordinary settled ADB screenshots are sufficient; the H.264 stream was only
    needed to diagnose the original upgrade-label tap offset.
  - Define the desired setting in strategy configuration rather than hardcoding
    it in the runtime. Confirm whether the observed `1E-22%` is the required GC
    value before adding configuration or adjustment behavior.
  - Verify the applied setting and make the operation safe to repeat.

## Runtime control

- [ ] Provide a convenient pause/resume interface so stopping the process with
  `Ctrl-C` is unnecessary.
  - Build on the existing control-file and `tools/automation_ctl.py` support.
  - [x] Make manual pause indefinite by default while supporting an explicit
    timed pause. The 2026-07-14 race came from an in-memory expiry resuming
    against a still-`PAUSED` control file; timed deadlines are now persisted in
    that authoritative file and expiry writes `RUNNING` before actions resume.
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
  - The 2026-07-14 active-battle and Home-with-Resume traversal is recorded in
    `docs/ui_state_traversal_2026-07-14.md`. Its safe/read-only screens now have
    explicit states and fixtures, including Wave/Perks, Settings/Lab/Modules
    subpages, upgrade details, Battle Heat/History, Ranking, Inbox, Themes,
    Vault, and Tournament. A full no-battle Home traversal remains pending.
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
- [ ] Preserve the working scheduled floating-gem (Bob) intercept and add a
  fresh on-screen `RUNNING` authorization check without delaying its cadence.
  - Live H.264 bursts identified Bob as the rotating square-with-diamond icon on
    an approximately 180-190 px circular orbit around `(540,480)`; the current
    blind point `(542,671)` is a proven bottom intercept.
  - Bob's game speed is static. Do not infer its speed from frame retrieval
    timestamps: buffering, skipped stream sequences, and manual labeling made
    the experimental timing appear variable.
  - The orphaned directional templates scored only about 0.25-0.50 and were
    commonly outranked by combat effects. The existing magenta-square heuristic
    detected only 5/69 positive frames across the gameplay annulus (1/69 in its
    historical crop). Neither is safe to restore as a single-frame detector.
  - A measured offline spike normalized white/magenta evidence around the
    expected annulus and fitted a constant-angular-velocity path. Roughly five
    seconds cleanly separated the positive burst from a same-run no-Bob burst
    and recovered the observed trajectory; shorter windows produced false
    tracks. This is optional detector research, not a prerequisite for the
    existing tap method.
  - The ephemeral source captures are documented in
    `docs/architecture_direction_2026-07-14.md`. The 69-frame `stream` directory
    is positive; the 112-frame `full_orbit` directory is only a noisy no-Bob
    negative. Promote reviewed fixtures into the repository before relying on
    either `/tmp` directory for a durable regression suite.
  - Have an app-owned frame observer publish a short-lived `RUNNING` lease. The
    tapper should check that lease in memory immediately before each tap and
    skip when it is stale or invalid, without moving the absolute monotonic tap
    schedule. Navigation, non-running evidence, pause, capture failure, and
    stream staleness must invalidate the lease.
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
- [ ] Add Event Mission and Guild contribution-chest claiming as separate,
  badge-triggered handlers after mapping their available/unavailable states.
  - On 2026-07-13 the in-run menu showed badges on Daily Missions (`2`), Event
    (`1`), and Guild (`1`). Treat a menu badge only as a reason to inspect; do
    not assume a specific claim target without fresh in-menu detection.
  - The Guild Members fixture contains the already-claimed 100 chest, a glowing
    250 chest, and locked 500/750 chests. Preserve it for claimability-template
    design; no chest was tapped during capture.
  - Event opened on Missions. Inventory the complete mission list and capture a
    positive claim control plus a post-claim negative before implementing taps.
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
  - Include a thread-safe UI-state observation snapshot and short-lived action
    lease so latency-sensitive scheduled actions can verify a fresh `RUNNING`
    state with an O(1) in-memory check.
  - Preserve absolute monotonic schedules across skipped actions; a stale guard
    should skip the current action rather than phase-shifting later attempts.

## Handler architecture

- [ ] Replace ad-hoc handler calls with a centralized handler registry and
  dispatcher.
  - Define a consistent handler interface (`should_run()` / `run()` or an
    equivalent protocol) before converting existing handlers.
  - Support registration by primary state, menu, secondary state, or overlay.
  - Preserve ordering and mutual-exclusion rules for handlers that can tap.
  - Integrate per-handler pause/resume with the global controls rather than
    creating a second control mechanism.
- [ ] Finish hardening the Game Over handler.
  - Make More Stats paging/capture failures recoverable rather than forcing a
    global WAIT for every capture problem.
  - [x] Keep polling the persistent control file while parked in Game Over
    WAIT, block terminal actions while paused, and exit cleanly on STOPPED.
  - [x] Add automated coverage for RETRY, WAIT/PAUSED, STOPPED, and HOME.
- [x] Live-validate the structured Game Over battle record at a natural
  boundary.
  - The primary path copies the full More Stats report through Android's
    clipboard service and writes versioned JSON plus a human-readable Markdown
    file under `logs/battles/`. Guarded scrolling OCR remains the fallback.
  - Routine screenshots are suppressed after a complete, confident capture;
    incomplete navigation, missing any of the 16 current sections, missing
    required rows, unparsed numeric values, or low confidence retain all source
    frames as explicit OCR evidence.
  - Every copied label/value row is retained without a fixed stat allowlist.
    Compact Game Stats-only values and derived fields include highest wave,
    generic hourly rates for numeric Currencies rows lacking one, combined
    Reroll Dice/hour, total module Shards/hour, base/ad coin shares, effective
    game speed, wave rate, seconds per wave, coins/cells per wave, estimated
    start time, and runtime wave error. Death Defies comes from the copied
    Counts section; the page's existing Cells Per Hour row is retained directly
    rather than duplicated.
  - Selected Perks are stored latest-selection-first. Blue rows are leveled;
    green and purple rows are single-instance. Dense overlapping capture keeps
    the order in which a newly leveled blue perk moves its complete row to the
    top.
  - A read-only 2026-07-15 Battle History traversal validated 16 sections, 145
    named rows, all 14 current Currencies rows, and 13 calculated currency
    rates; the result passed section, row, parse, and confidence validation.
    Historical three-frame artifacts prove why overlapping capture is required:
    they omit the middle of the long page.
  - The natural Tier 19 wave-4969 boundary copied 144 exact Stats rows, OCRed
    all 27 ordered perks, reconciled compact `3.00Q + 1.50Q = 4.49Q` coin
    evidence against the copied total, and produced a valid record with no
    warnings after reprocessing retained evidence. The same boundary validated
    WAIT-to-Retry, a fresh lifecycle, EHLS/EALS initialization, and Tier 19
    Target Priority preservation without Surrender.
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
