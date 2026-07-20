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
  - [ ] Support extending or cancelling pending recovery timers. Indefinite and
    persisted timed pauses are already implemented.
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
  - [x] Merge Battle and Tournament history; classify Farm, Tournament, and
    Milestone from strategy plus terminal evidence; and filter by type, Tier,
    wave range, strategy, and capture quality.
  - [x] Show Coins/hour and Cells/hour in the report banner, plus captured
    perks, resolved settings, and observed preflight evidence.
  - [x] Make structured completed records the canonical end-of-battle artifact:
    include bounded Coins/min progression, Game Stats-only fields, derived
    values, and runtime evidence; retain terminal screenshots only for problems.
  - [ ] Publish a structured atomic runtime-status snapshot so the GUI does not
    use action-log parsing as its primary live-status source.
  - [ ] Add return-now and recovery extension/cancellation only through explicit
    runtime directives with freshness and ownership checks.
