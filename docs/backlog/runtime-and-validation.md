# Runtime, Validation, and Farm Backlog

This file contains active work only. Before live work, follow `AGENTS.md`,
[`../new_thread.md`](../new_thread.md), and the complete
[`../runtime_operations.md`](../runtime_operations.md) runbook. Historical
checked-item detail remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Current validation gates

- [ ] Cross-validate the `data-9-game-1073` player-save mapping against fresh
  UI inventory from the same version, and add a new exact candidate mapping if
  the current game reports a different identity. Execute the complete
  [field matrix and rollout sequence](../modules/player_save_import.md#complete-validation-program).
  Promote only fully cross-validated fields; retain scheduled audits and every
  existing UI checker as the fallback for unknown versions, shape changes,
  stale data, mismatches, and unmapped settings.
  - [x] On game `28.3.1` / version code `1073`, cross-validate Cards,
    Workshop, Bots, First Perk, Ban Perks, the complete 18-row ranked Auto Pick
    block, Guardians, and the three automation-managed Free Upgrade locks.
    Correct the candidate perk-ID labels exposed by the same-boundary compare
    and retain the mapping as `candidate`.
  - [x] Causally validate `currentPreset`, Poison Swamp Stun, and both card
    recharge booleans through isolated app-pause flushes and restorations.
    Allowlist the complete Cards preset and recharge-mode checks while keeping
    the overall mapping `candidate`.
  - [ ] Add the versioned raw-field disposition manifest and explicit
    validation status/provenance for every normalized profile group. Keep
    private, ignored, and unknown fields unpublished.
  - [ ] Cross-validate Auto Pick enabled and full ranked-order semantics,
    Target Priority, every supported Free Upgrade lock, every Ultimate Weapon
    primary/detail polarity, Modules, Damage Slider, and Orb Distance according
    to the matrix. Do not manufacture cost-bearing Bot or Module changes.
  - [ ] Integrate the proven flush plus stable pull into preflight in audit-only
    mode, run a clean forced audit for each resolved Farm/Tournament
    configuration fingerprint, and retain normalized discrepancy evidence.
  - [ ] Enable per-check navigation suppression incrementally. Invalidate the
    snapshot after the first UI repair, preserve visual post-action
    verification, and force audits on version/fingerprint changes,
    discrepancies, repairs, and a configurable periodic cadence.
- [ ] Capture the numeric level of every equipped Module from authoritative
  overview evidence, retain it with preflight and completed-run records, and
  surface threshold violations without confusing an intentional Tournament
  identity variation with a level problem. The current requirement is Primary
  slots at level 201 or higher and Assist slots at level 195 or higher. Existing
  level-transfer guards preserve slot-owned levels during replacement but do
  not OCR or validate the numeric values.
- [ ] Diagnose the transient Home Poison Swamp Stun verification timeout
  recorded in
  [`../observed_issues.md`](../observed_issues.md#home-poison-swamp-stun-verification-transiently-timed-out-after-its-source-tap).
  Retain the failing frame and individual detail/off/on match confidences on
  recurrence, then distinguish a missed detail-open tap from unsettled
  Workshop scroll geometry or a detail-template miss before changing retry or
  stabilization behavior.
- [x] Finish live validation of the No Strategy two-phase inventory at the next
  natural Game Over. Do not Surrender or manufacture the boundary.
  - [x] On the active Tier 18 Attack Dissonance battle, confirm automatic
    Cards/Perks/UW/Modules/Bots/Guardians/Target Priority traversal, Attack
    Dissonance Damage Slider unavailability, and guarded return to the battle.
    The repaired pass completed at 17:26:49 and returned to `RUNNING` at wave
    4120 under PID `3899024`.
  - [x] At natural Game Over, confirm Attack Dissonance identity, the full
    terminal record, forced verified Home `NEW_BATTLE`, read-only Workshop/Free
    Upgrade lock pass, independently guarded automatic Perks opening, complete
    First Perk/Ban/Auto Pick capture, update of the same battle record, and
    release of the next-battle path. The natural Tier 18 boundary saved
    `Battle20260722T185119-0700` at 18:52:25. Reload recovery retained that
    record through the Home-only work; the completed lock and Perks evidence
    was finalized back into the same record at 19:37:34, and WAIT continued to
    hold verified `NEW_BATTLE` Home.
- [ ] Complete live validation of guarded **Reload automation for current
  battle**.
  - [x] On the active No Strategy Attack Dissonance battle at wave 3314, the
    original PID acknowledged Pause and published a fresh `RUNNING` frame; PID
    `3787794` exited cleanly; replacement PID `3846802` acquired the refreshed
    localhost:5555 lock, launched once with `next_run`, acknowledged Pause,
    attached at wave 3315 without gates, and restored the prior `RUNNING`
    intent. The configured cold-start policy returned to `immediate`.
  - [ ] Confirm normal gate re-arming at the following authoritative run
    boundary. This remaining check requires the next strategy to declare gates;
    No Strategy itself has none.
- [ ] Diagnose the unexpectedly early Tier 18 Farm ending recorded in
  [`../observed_issues.md`](../observed_issues.md#tier-18-farm-ended-at-wave-2644-without-completed-session-preflight).
  Reproduce a clean, fully validated Farm start at 720p before attributing the
  result to resolution, loadout, preflight, or perk ordering.
- [ ] Diagnose the unclean runtime-owner exits recorded in
  [`../observed_issues.md`](../observed_issues.md#automation-owner-exited-without-a-clean-shutdown-record).
  The owners disappeared without a clean-shutdown record and left stale locks.
  Keep control `PAUSED` while distinguishing execution-session termination,
  an unlogged crash, and manual-player activity before restarting automation.
- [ ] Diagnose the intermittent incomplete ADB screenshot frames recorded in
  [`../observed_issues.md`](../observed_issues.md#direct-adb-screenshots-intermittently-returned-incomplete-black-frames).
  - [x] Close the action-authority gap. A partial frame retaining narrow Game
    Over strips still matched both state evidence and the visible Home control,
    so capture now retries once and shared state/action boundaries reject
    incomplete frames. Regression coverage is in
    `test/test_incomplete_frame_authority.py` and `test/test_ss_capture.py`.
  - [ ] Diagnose the compositor or transport source. The ignored retained
    files no longer reproduce the recorded corruption: all four currently decode
    as complete, and the Home failed/retry pair is byte-identical.
- [ ] Live-revalidate the distinct Home Store-badge template at the next daily
  availability. The fixture is canonical, but the badge cleared before that
  template could be exercised live. The in-run badge, Store navigation, active
  claim, ad skip, return-to-game, and inactive cooldown paths are separately
  live-verified.
- [ ] Live-validate the once-per-UTC-day Daily Gem Store probe across the next
  game-day boundary. Confirm that direct Store navigation claims the gem when
  the initial badge is absent and that persisted completion suppresses a second
  probe after restart.
- [ ] Verify the operator's apparent Event Mission sequence: scan the complete
  list, claim one reward, return to the top, then scan the complete list again.
  Correlate logs, retained captures, and handler control flow before accepting
  that sequence as fact. Distinguish an intentional claim-all convergence pass,
  a separate inventory pass, or another dispatch from redundant post-claim
  scanning; log the continuation reason and stop promptly once fresh evidence
  proves that no additional claim is available.
- [ ] Diagnose the dropped Game Stats coin decimal retained for
  `Battle20260722T202039-0700`. The visible base value was `1.82T`, but OCR
  produced `182T`; the resulting split disagreed with the copied `2.72T` total,
  correctly invalidated record quality, and retained source screenshots.

## Tournament Battle Condition evidence

- [ ] Capture each Tournament's Battle Conditions into its structured run
  record without depending on `thetower.lol`. Use the canonical alias mapping
  and source-precedence rules in
  [`../game_strategy.md`](../game_strategy.md#tournament-battle-condition-abbreviations).
  - [ ] After the concurrent player-save calibration is complete, determine
    whether `playerInfo.dat` contains the current Tournament's condition set.
    Accept it only through an exact-version, structurally validated mapping
    with condition IDs, polarity, and semantics cross-checked against the same
    Tournament's UI and a verified save-serialization boundary. Do not retain
    or log unmapped raw save fields.
  - [ ] Preserve a read-only UI fallback that inventories both Heat and
    Overheat tabs from a verified Tournament Heat panel, scrolls to both ends,
    deduplicates overlapping rows, and captures each displayed name, level,
    effective description, and activation wave where present. The retained
    `active_tournament_heat_20260718.png` fixture supplies initial positive
    evidence but is not a complete scroll sequence.
  - [ ] Normalize known conditions to stable IDs while retaining the exact
    display text and provenance (`player_save` or `tournament_heat_ui`), game
    version, and capture time. Unknown conditions must remain losslessly
    reportable instead of being dropped or guessed.
  - [ ] Bind the observation to the matching Tournament run/session and merge
    it into the eventual `Tournament*.json` record. A missing or incomplete
    condition inventory must remain explicit and nonblocking; it must not risk
    Tournament launch, interfere with an active run, or prevent terminal-result
    capture.
  - [ ] Add retained-fixture and synthetic-record coverage for alias
    normalization, complete-scroll merging, unknown conditions, provenance,
    attached-run capture, and terminal-record merge behavior.

## Strategy-driven Damage Slider schedule

- [ ] Add a generic, profile-declared Damage Slider schedule driven by
  authoritative selected-Perk evidence. Keep `YamlStrategy` and the action
  executor strategy-agnostic; the Tier 19 behavior must come entirely from the
  compact Farm source and its generated plan.

### Proposed Tier 19 experiment

- Enforce `1E-18%` as the initial Tier 19 Farm value.
- Once the selected-Perks timeline authoritatively contains
  `enemy_health_tradeoff` (the −55% enemy-health tradeoff), enforce `1E-19%`.
- If that perk never appears, retain `1E-18%` for the entire battle.
- If retained results show that `1E-18%` is still unsafe, change only the
  configured initial value to `1E-17%`; do not add Tier-specific runtime code.
- Do not add a fixed-wave transition to this experiment. Its question is
  whether the safer initial cap should remain until the required perk appears.
- Treat this exact schedule as proposed rather than approved for implementation.
  On 2026-07-31, one `1E-19%` run survived to wave 4,534 and another ended at
  wave 2,053 despite having both the −44% enemy-speed and −55% enemy-health
  tradeoffs before wave 1,540. The proposed transition would therefore not
  have prevented the observed early death. Retain the generic capability, but
  choose the first configured schedule only after deciding what hypothesis the
  next experiment should isolate.

### Compact source schema

Keep the existing static `value` as the initial value for backward
compatibility and make transitions optional:

```yaml
loadout:
  damage_slider:
    mode: enforce
    value: "1e-18"
    transitions:
      mode: enforce
      stages:
        - id: enemy_health_55
          enabled: true
          when:
            perks_all:
              - enemy_health_tradeoff
          value: "1e-19"
```

`transitions.mode` has three explicit behaviors:

- `enforce`: evaluate stages and apply the selected target.
- `observe`: evaluate and record the target that would have applied without
  changing the slider. This is the preferred reproducible transition-bypass
  mode for an A/B test.
- `disabled`: retain the initial value and perform no transition observation
  or input.

Each stage also has `enabled: true|false` so one candidate can be skipped
without deleting or reordering the remaining experiment. The builder must
normalize all percentages through the existing Damage Slider normalizer and
reject duplicate IDs, empty conditions, invalid modes, non-boolean `enabled`,
and malformed perk lists.

### Multiple perks and multiple stages

Support any number of ordered stages. A stage condition may declare
`perks_all`, `perks_any`, or both:

- Every family in `perks_all` must be selected.
- At least one family in `perks_any` must be selected.
- When both are present, both requirements apply.
- Only positive selected-Perk evidence is supported initially. Do not trigger
  from the apparent absence of a perk because an incomplete or delayed
  observation could make that unsafe.

The effective target is the initial value overridden by the **last matching
enabled stage** in source order. This state-derived rule permits multiple
transitions, combinations, and alternatives while remaining deterministic. It
also allows a restarted runtime to jump directly to the correct current target
instead of replaying obsolete intermediate taps. Later stages do not require
earlier stages to have fired; their declared conditions are authoritative.

An illustrative extension, not part of the initial Tier 19 experiment, is:

```yaml
stages:
  - id: first_survival_perk
    enabled: true
    when:
      perks_any:
        - enemy_health_tradeoff
        - enemy_speed_tradeoff
    value: "1e-19"
  - id: combined_survival_perks
    enabled: true
    when:
      perks_all:
        - enemy_health_tradeoff
        - enemy_speed_tradeoff
    value: "1e-20"
```

### Runtime observation and action contract

- `PerkTimelineObserver` remains the authority for selected families. Publish
  its current selected-family set only after a source-complete baseline or an
  accepted timeline update. Use canonical family IDs, never display-text
  substring matching in the strategy evaluator.
- Add generic `perks_all` / `perks_any` conditions to `YamlStrategy`, backed by
  the published runtime fact. Do not add a strategy-name or Tier conditional.
- Generate run-scoped effective-stage, completion, and observation state.
  Reset it at every authoritative new-battle boundary.
- Evaluate transitions only after initialization and session preflight have
  completed. Capture may identify the perk while paused, but Pause and every
  existing action-authority block must prevent the slider input until Resume.
- Reuse `damage_slider_configure`. Let generated actions name their result and
  completion variables so a transition cannot overwrite the initial
  `damage_slider_checked` gate. An enforce failure remains incomplete and
  retries with a bounded cooldown; an already-matching target completes
  idempotently.
- Let the action supply operator-facing context so its `ACTION` explains which
  stage and perk condition changed the target. Preserve the existing paired
  terminal `RESULT` and detailed input logs.
- On a cold restart or mid-battle attach, obtain an authoritative selected-Perk
  baseline, compute the last matching stage, observe the current slider, and
  enforce only the resulting target. If the baseline is incomplete, take no
  transition action.

### Battle evidence

- Extend declared `run_configuration.loadout.damage_slider` with the normalized
  initial value, transition mode, ordered stages, and enabled states. Preserve
  compatibility with records that contain only `mode` and `value`.
- Add run-time Damage Slider control evidence containing the effective stage,
  trigger families, scheduled and observed perk waves where available,
  previous and intended targets, mode, attempt time, observed initial/final
  values, step count, success, and reason.
- Persist transition evidence when it happens rather than only at terminal
  capture, so a runtime restart cannot erase the experiment history. A
  mid-battle attach must distinguish reconstructed current state from an
  observed transition whose original wave is unavailable.
- Display the actual transition sequence in completed-battle output. Analytics
  must group by the declared schedule and applied sequence rather than treating
  every scheduled run as a static initial-value run.

### Required validation

- Builder tests for normalization, legacy static profiles, ordered stages,
  all/any conditions, disabled stages, and all transition modes.
- Evaluator tests proving last-match-wins selection, no action before
  authoritative perk evidence, direct restart catch-up, new-run reset, and
  pause/action-authority blocking.
- Executor tests for independent result variables, idempotent already-matched
  completion, bounded failure retry, observe mode, and contextual logging.
- Battle-record tests for declared versus observed values and retained
  transition evidence across restart.
- A generated-plan regression for the exact Tier 19 `1E-18%` →
  `enemy_health_tradeoff` → `1E-19%` experiment, plus proof that Tier 18 and
  static Damage Slider profiles remain unchanged.
- Retained fixtures are sufficient for automated development. Perform live
  validation only at a natural safe run and never Surrender an operator-owned
  battle to manufacture the transition.

## Runtime control

- [ ] Make `STOPPED` interrupt an in-progress Home setup without another device
  input, as recorded in
  [`../observed_issues.md`](../observed_issues.md#stopped-control-could-not-interrupt-an-in-progress-home-setup-guard).
  Preserve the current Pause behavior, but let Stop unwind the guarded route
  and release the runtime lock without requiring `KeyboardInterrupt`.
- [ ] Finish the operator-control lifecycle.
  - [x] Provide convenient CLI, browser, and native Windows pause/resume
    interfaces over the authoritative control file so stopping the process
    with `Ctrl-C` is unnecessary.
  - [x] Make operator intent, runtime acknowledgement, and stale observation
    visibly distinct.
  - [ ] Ensure detected manual input automatically yields automation authority.
- [ ] Detect likely manual player activity and automatically yield tap authority.
  - Treat unexpected Go Home/manual navigation during an active run as operator
    activity rather than an error to undo immediately.
  - Pause while screens continue changing or recent external input is evident.
  - After a configurable static grace period, warn before offering or performing
    a guarded return to the running battle.
  - Make the grace period interruptible and extendable through CLI/GUI controls.
- [ ] Complete the native Windows GUI control surface described in
  [`../architecture/control_surface.md`](../architecture/control_surface.md).
  - [x] Serve a responsive Windows-browser client from a loopback Linux
    adapter over the same authoritative controls used by the CLI.
  - [x] Add a self-contained native WPF client that can own the passwordless
    OpenSSH tunnel.
  - [ ] Move SSH ownership into a single-instance per-user companion host so
    independently controlled API and ADB forwards can survive GUI exit. Use a
    current-user-only versioned named pipe for control/status and retain full
    reconnect, conflict, and last-exit diagnostics; do not detach unmanaged
    `ssh.exe` children. Start on demand first, with start-at-login and tray UI as
    later optional decisions.
  - [x] Add the first constrained Strategy Profile Builder: dynamically list
    bundled and custom profiles, clone/edit Farm Tier loadout policies, validate
    through the shared builder, atomically publish source plus generated plan,
    and keep activation as a separate existing strategy-lifecycle action.
  - [x] Add durable custom-profile skips for Auto Pick enabled, Perk Bans, and
    Auto Pick priority; add managed Ban and ordered Auto Pick editors; and
    round-trip the complete Farm setup so unexposed settings are preserved.
  - [ ] Add specialized value editors for every registered setting, including
    remaining compact Farm controls and profile-local structured values where
    justified. Keep generated rules and executor actions protected rather than
    exposing them as ordinary form data; treat any future raw-rule mode as a
    separately reviewed advanced feature.
  - [ ] Refine running-battle validation into an explicit strategy gate so
    observation and allowlisted independent collectors can continue while
    strategy and lifecycle actions are blocked. Validate this authority split
    before newly editable settings rely on running-battle enforcement.
  - [ ] Add profile duplication/retirement workflows after the source and base
    revision model is stable, preserving immutable bundled templates and
    atomic/stale-write publication protections.
  - [x] Show directive/acknowledgement separately from primary state, menu,
    overlays, run mode, pause status, runtime evidence, activity, and completed
    battle records.
  - [x] Provide indefinite/timed pause, resume, mode, and fixed-systemd-service
    start/stop controls through an allowlisted versioned API.
  - [x] Allow a stopped managed runtime to select and persist its localhost ADB
    port for the next service start.
  - [x] Allow an acknowledged paused runtime to hand off to a different
    localhost ADB port without replacing the process or recreating strategy
    startup/session gates. Retain Pause and the former target on validation
    failure.
  - [x] Allow a stopped managed start to attach to an existing battle without
    replaying startup/session gates, then re-arm those gates at the next
    authoritative run boundary without seeding completion state.
  - [ ] Attribute and reduce Windows control-surface CPU use for passive
    performance collection. Retained 2026-07-30/31 aggregates measured
    approximately `0.82%` process CPU clean and `1.9%` under contention,
    exceeding the `<0.5%` non-frame telemetry target. Profile sampler work
    separately from UI and other process activity, retain complete evidence
    cadence, and validate both clean and contended cases.
  - [x] Add a guarded active-battle automation reload that pauses and obtains a
    fresh runtime-owned `RUNNING` observation, replaces the fixed systemd unit
    once with attachment semantics, verifies the new PID/lock/startup/control/
    observation evidence, restores the configured cold-start policy, and
    restores prior control intent only after readiness succeeds.
  - [x] Merge Battle and Tournament history; classify Farm, Tournament, and
    Milestone from strategy plus terminal evidence; and filter by type, Tier,
    wave range, strategy, and capture quality.
  - [x] Show Coins/hour and Cells/hour in the report banner, plus captured
    perks, resolved settings, and observed preflight evidence.
  - [x] Export the currently filtered completed-battle summaries as a local
    UTF-8 CSV without expanding the Linux API authority surface.
  - [x] Make structured completed records the canonical end-of-battle artifact:
    include bounded Coins/min progression, Game Stats-only fields, derived
    values, and runtime evidence; retain terminal screenshots only for problems.
  - [ ] Validate single-instance behavior on Windows.
    - [x] Add a per-session instance mutex and have a repeated launch restore and
      foreground, or at least flash, the existing main window.
    - [ ] Confirm that a second launch from the SMB publish path reaches the
      guard without showing a host/runtime prompt and creates no second client.

### Agreed operator-control sequence

The first implementation target is Battle History filter reliability. The
remaining order is provisional and can change as operator use supplies better
evidence.

1. [ ] Stabilize Battle History filter input.
   - [x] Do not clear and repopulate an unchanged record collection on every
     poll. Apply genuine changes only while the filter menus are closed, retain
     the selected battle by ID, and defer an update while any filter menu is
     open.
   - [x] Label the post-Strategy combo box as `Quality`, enlarge all three
     combo-box hit targets, and retain native keyboard text search and
     accessibility names.
   - [x] Contain Battle History construction and initial-data failures at the
     button boundary so an error is reported without terminating the main app.
   - [x] Publish the self-contained Windows application successfully from
     Linux after the change.
   - [ ] On Windows, verify that the window opens without terminating the app,
     a single click opens all three combo boxes, each popup remains usable across
     multiple independent refreshes, and mouse and keyboard selection work
     normally.
2. [ ] Preserve an ineligible live ADB-port edit as an explicit draft instead
   of silently replacing it with the active port.
   - Explain that a live switch requires an acknowledged indefinite Pause and
     has not yet been applied.
   - Reset the draft only after a successful switch or an explicit operator
     revert; keep the existing validation and rollback behavior.
3. [ ] Publish a structured atomic runtime-status snapshot and revise the
   normal Status presentation around operator-relevant fields.
   - Make directive, acknowledgement, process evidence, run state, wave,
     current Coins/min, and pause/recovery state authoritative API fields so
     the GUI no longer derives live status primarily from action-log text.
   - Keep menu, secondary-state, overlay, match, and similar detector detail in
     diagnostic telemetry rather than the default Status line.
   - Make the top bar reflect that automation is stopped from authoritative
     process evidence instead of leaving a stale running state visible.
   - Include the active running strategy in the top bar, keeping a queued
     next-boundary strategy visibly distinct.
   - Include elapsed run time and an expected run duration derived from
     representative prior runs. Define the comparison cohort and exclude
     configuration repairs, surrendered runs, and other non-representative
     outcomes from the estimate.
4. [ ] Separate concise operational activity from diagnostic detail without
   discarding either record.
   - [x] The original split emitted operator-facing `ACTION` and `STATUS`
     summaries for intent and outcome, with paired `DEBUG` detail where
     coordinates, matches, retries, or `TAP_SAFE` evidence remained useful.
   - [x] Retain both forms in the complete log, default Recent Activity to the
     operational view, and keep diagnostics available through filters.
     Commit `372cff3` implements the paired operator/diagnostic log stream.
   - [x] Before a guarded or multi-step operation, log a human-readable intent
     summary describing what automation is trying to accomplish and why, not
     only the individual actions it performs. Commit `8bbd3eb` adds the shared
     intent-header helper and adopts it across the primary session, setup,
     reward, and terminal workflows.
   - [x] Complete the next logging-taxonomy migration.
     - [x] Define `ACTION` as one What/Why workflow notice, `RESULT` as its
       terminal outcome, and `INPUT` as an individual device action. Preserve
       status as a separate snapshot stream, reserve warnings for persistent
       operator-relevant degradation, and add the logger primitives and
       regression coverage.
     - [x] Migrate centralized tap, swipe, and press emitters from `ACTION` to
       `INPUT` while retaining paired `DEBUG` evidence.
     - [x] Pair operator-meaningful workflows with exactly one `ACTION` and one
       terminal `RESULT`; downgrade nested implementation notices. Commits
       `5f7ef32`, `0620101`, `c975fa8`, `d35b8db`, `6b515d2`, and `110cd61`
       migrate reward, terminal, in-battle setting, startup configuration,
       Golden Combo, Tournament, auto-return, and nested Ultimate workflows.
     - [x] Remove `STATUS` and general `INFO` from the default Operational
       activity levels. Present the latest status and prior meaningful
       transition separately while retaining complete status history. Commit
       `bd7dd23` updates the Linux status adapter plus the browser and native
       clients.
     - [x] Audit recurring warnings in focused domain batches. Low-level
       helpers should return structured outcomes; workflow owners decide when
       persistent impact warrants a transition-based, rate-limited warning.
       Commits `d98d67a` and `28b4a8a` move ordinary scrolling, transient
       game-speed OCR misses, ADB retry detail, and Coins/min suffix repair out
       of the warning stream while preserving persistent, rate-limited ADB and
       game-speed degradation notices plus recovery records.
     - [x] Verify that Operational reads as What/Why followed by Result,
       Diagnostics preserves input and decision evidence, and All Levels
       preserves complete ordering. Commit `0b18f20` adds the mixed-stream
       audience regression. The final validation passed 817 sandbox-compatible
       tests plus the host-loopback transport test, and the native Windows
       publish completed successfully.
   - [ ] Give every startup, session-preflight, and recovery check a concise
     human-readable result that includes the requirement, expected and observed
     state, and final disposition such as passed, failed, waived, or fallback.
     - [x] Home preflight now emits concise passed, failed, waived, and deferred
       results for every reached requirement. Commit `1629bb3`; live Farm T19
       validation completed the full Home pass on 2026-07-25.
     - [ ] Extend the same result contract to in-battle session-preflight and
       recovery checks.
5. [ ] Publish and display Peak Coins/min for the active and completed run.
   - Maintain the active peak within an authoritative run boundary and expose
     it through the runtime snapshot; after a mid-battle attach, label a peak
     that cannot be recovered as `since attach`.
   - Derive the completed peak from accepted `coin_rate_samples` and include it
     in the battle report. Do not label Coins/min multiplied by 60 as realized
     Coins/hour.
6. [ ] Generalize the guarded return-to-game timer to known recoverable panels,
   including Wave Stats (`WAVE_PANEL`).
   - Validate an authoritative close control and post-action return to the
     running battle for each supported panel instead of assuming the existing
     Return to Game strip is present.
   - Run timers only while control is `RUNNING`; Pause must block action. Log
     timer start, cancellation, expiry, target, and outcome, and expose the
     countdown or Pause block in the runtime snapshot and GUI.
   - Add return-now, extend, and cancel only as explicit runtime directives
     with freshness and ownership checks.
7. [ ] Support boundary-aware strategy changes without restarting automation.
   - [x] Allow an active run to queue, replace, or cancel a pending strategy while
     showing current strategy, pending strategy, and acknowledgement separately.
   - [x] Finalize the current report with the current strategy, then apply the
     pending strategy after the terminal boundary and before the next run's
     first actionable observation.
   - [x] Apply immediately when an authoritative no-battle Home boundary is
     already established, including while paused for manual tournament setup;
     do not treat a resumable Home battle as a new-run boundary.
   - [x] Add an explicit active-battle adoption mode. After fresh `RUNNING` or
     resumable-Home evidence, change normal behavior and completed-run strategy
     identity without restarting, while deferring every new-run gate until the
     next genuine boundary. Preserve boundary replacement when `NEW_BATTLE` is
     observed first (`5cd9efe`).
   - [x] Replace the command-like strategy buttons and adoption checkbox with a
     strategy dropdown plus explicit queue/adopt actions. Preserve unsent
     selection across refreshes and disable no-op adoption of the displayed
     Current strategy (`88b603c`).
   - [x] Advertise Linux API/revision/capability metadata, evaluate a generic
     compiled client compatibility contract, disable dependent actions when
     the server is stale, and offer a confirmed fixed-unit SSH restart that
     verifies the whole contract after reconnection without touching main
     automation (`2c06a66`, `ef8df58`).
   - [ ] On Windows, verify immediate accepted-request feedback, current/pending
     display, dropdown selection retention, pending replacement/cancellation
     during a battle, stale-server warning and explicit reload, active-battle
     adoption, and an acknowledged paused Workshop application without
     changing Pause.
8. [ ] Rework Battle History filters after the input defect is fixed and their
   real behavior can be evaluated.
   - [x] Populate the Strategy dropdown with `All` plus distinct strategies
     from the currently loaded records, use exact matching, and preserve the
     selected or applied strategy across refreshes.
   - Prioritize useful distinctions such as type, Tier, strategy, outcome,
     quality, and date range. Retain wave range only if operator use justifies
     it, otherwise move it to an advanced view or remove it.
   - Make filter semantics clear when the client has loaded only a bounded
     newest-record page.
9. [ ] Define and implement report disposition for short, interrupted,
   configuration-repair, surrendered, and manually aborted battles.
   - [x] Treat an automation-owned configuration-repair Surrender as a control
     transition rather than a completed battle: bypass Perks/More Stats and
     battle-record capture, then take the guarded Home route.
   - Classify these outcomes first and exclude non-representative runs from the
     normal history and analytics by default without erasing evidence.
   - If operator use still requires permanent discard, expose only a confirmed,
     audited exact-record operation through the versioned API. Never add
     arbitrary path or file-deletion authority and never delete automatically.
10. [ ] Add configurable dashboard layout, covering panel placement and size
    as well as splitter and top-level window geometry.
    - [x] Persist the main and Battle History window positions, sizes, and
      maximized states locally; reject unusable off-screen placement and never
      reopen minimized.
    - [x] Replace the four-card scrolling rail with full-height Controls,
      Process, Setup, and Details tabs. Persist the selected tab, control-pane
      width, and latest-battle height.
    - [x] Make Previous Game Screen, Host Health, and the latest-battle summary
      independently collapsible, persist their state, preserve minimum pane
      sizes, and provide a Reset Layout action.
    - [ ] Validate the revised layout on Windows at the minimum, default, and
      maximized window sizes. Based on operator use, decide whether arbitrary
      drag-to-reorder, hiding, or floating panes would add enough value beyond
      the tabbed and collapsible layout.
11. [ ] Make the optional connection Token field self-explanatory and advanced.
    - [x] Move it to Setup, label it optional, and explain in tooltips that it
      is an in-memory bearer credential which is never saved.
    - [ ] Confirm on Windows that the tooltip and Setup placement make clear
      that the field is only for an explicitly authenticated adapter or reverse
      proxy and should remain blank for the normal loopback SSH tunnel.
