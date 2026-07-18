# Runtime, Validation, and Farm Backlog

This file contains active work only. Before live work, follow `AGENTS.md`,
[`../new_thread.md`](../new_thread.md), and the complete
[`../runtime_operations.md`](../runtime_operations.md) runbook. Historical
checked-item detail remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Current validation gates

- [ ] Diagnose the unclean runtime-owner exits recorded in
  [`../observed_issues.md`](../observed_issues.md#automation-owner-exited-without-a-clean-shutdown-record).
  The owners disappeared without a clean-shutdown record and left stale locks.
  Keep control `PAUSED` while distinguishing execution-session termination,
  an unlogged crash, and manual-player activity before restarting automation.
- [ ] Diagnose the intermittent incomplete ADB screenshot frames recorded in
  [`../observed_issues.md`](../observed_issues.md#direct-adb-screenshots-intermittently-returned-incomplete-black-frames).
  Determine whether current state templates always reject the mostly-black
  frames. Add an explicit completeness/freshness guard before action authority
  if partial compositor frames can match actionable evidence.
- [ ] Live-revalidate the distinct Home Store-badge template at the next daily
  availability. The fixture is canonical, but the badge cleared before that
  template could be exercised live. The in-run badge, Store navigation, active
  claim, ad skip, return-to-game, and inactive cooldown paths are separately
  live-verified.
- [ ] Live-validate the once-per-UTC-day Daily Gem Store probe across the next
  game-day boundary. Confirm that direct Store navigation claims the gem when
  the initial badge is absent and that persisted completion suppresses a second
  probe after restart.

## Farm run initialization

- [ ] Live-validate the complete generated `farm` new-run sequence at the next
  natural new-battle boundary: time-sensitive EHLS/EALS initialization, Tier 18
  Damage Slider enforcement at `1E-22%`, Target Priority enforcement, complete
  session preflight, and release to normal automation. Use the actual `farm`
  strategy rather than direct helper calls, and confirm the resolved run
  configuration is present in runtime/battle evidence. Do not Surrender the
  current developer-owned Tier 18 validation run merely to create the boundary.
- [ ] Decide whether session preflight should validate perk bans and Auto Pick
  Perk order. Keep automation-owned perk selection as a later option.

## Runtime control

- [ ] Provide a convenient pause/resume interface so stopping the process with
  `Ctrl-C` is unnecessary.
  - Build on the control file and `tools/automation_ctl.py`.
  - Make pause state obvious and ensure manual input cannot race automation.
  - Support extending or cancelling pending recovery timers. Indefinite and
    persisted timed pauses are already implemented.
- [ ] Detect likely manual player activity and automatically yield tap authority.
  - Treat unexpected Go Home/manual navigation during an active run as operator
    activity rather than an error to undo immediately.
  - Pause while screens continue changing or recent external input is evident.
  - After a configurable static grace period, warn before offering or performing
    a guarded return to the running battle.
  - Make the grace period interruptible and extendable through CLI/GUI controls.
- [ ] Create a small GUI control window.
  - Show primary state, menu, overlays, run mode, and pause status.
  - Provide pause, resume, return-now, and extend-recovery controls.
  - Keep it as a thin client over the same controls used by the CLI.
