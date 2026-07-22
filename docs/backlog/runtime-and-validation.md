# Runtime, Validation, and Farm Backlog

This file contains active work only. Before live work, follow `AGENTS.md`,
[`../new_thread.md`](../new_thread.md), and the complete
[`../runtime_operations.md`](../runtime_operations.md) runbook. Historical
checked-item detail remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Current validation gates

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

## Farm session preflight

- [ ] Decide whether session preflight should validate perk bans and Auto Pick
  Perk order. Keep automation-owned perk selection as a later option.

## Runtime control

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
   - [x] Emit operator-facing `ACTION` and `STATUS` summaries for intent and
     outcome, with paired `DEBUG` detail where coordinates, matches, retries,
     or `TAP_SAFE` evidence remains useful.
   - [x] Retain both forms in the complete log, default Recent Activity to the
     operational view, and keep diagnostics available through filters.
     Commit `372cff3` implements the paired operator/diagnostic log stream.
   - [ ] On Windows, verify that the default view remains concise during normal
     automation, `Diagnostics` exposes the paired status/input evidence, and
     `All levels` preserves the complete ordering.
   - [ ] Before a guarded or multi-step operation, log a human-readable intent
     summary describing what automation is trying to accomplish and why, not
     only the individual actions it performs.
   - [ ] Give every startup, session-preflight, and recovery check a concise
     human-readable result that includes the requirement, expected and observed
     state, and final disposition such as passed, failed, waived, or fallback.
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
    - [ ] Allow the fixed operational panels to be moved, resized, collapsed,
      or hidden, and persist the panel arrangement and splitter positions.
    - Preserve minimum usable sizes for safety-critical controls and provide a
      Reset Layout action.
11. [ ] Make the optional connection Token field self-explanatory and advanced.
    Explain that it is an in-memory bearer credential for an explicitly
    authenticated adapter or reverse proxy and should remain blank for the
    normal loopback SSH-tunnel configuration.
