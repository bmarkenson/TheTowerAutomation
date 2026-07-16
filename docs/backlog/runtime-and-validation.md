# Runtime, Validation, and GC Backlog

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

## GC run initialization

- [ ] Define the GC module preset and validate it during session preflight.
  Established evidence:
  - The 2026-07-15 Tournament/Milestone loadout is explicitly negative GC
    evidence: Primary = Amplifying Strike, Project Funding, Orbital Augment,
    Dimension Core; Assist = Being Annihilator, Singularity Harness,
    Anti-Cube Portal, Harmony Conductor.
  - The user-confirmed normal GC loadout captured at the 2026-07-16 natural
    post-wave-8803 boundary is: Primary = Amplifying Strike, Black Hole
    Digestor, Orbital Augment, Multiverse Nexus; Assist = Being Annihilator,
    Singularity Harness, Anti-Cube Portal, Dimension Core. The overview and all
    eight detail panels are retained under
    `screenshots/module_inventory_2026-07-16/`.
  Remaining work:
  - Build the smallest evidence-supported, data-driven index that identifies a
    module from stable icon artwork rather than hardcoding the observed name at
    each equipped-slot position.
  - Use equipped-slot geometry to report Primary/Assist placement separately
    from identity. Return confidence plus explicit unknown/ambiguous results
    instead of guessing.
  - Measure the effect of rarity borders, equipped glow, levels, surrounding
    artwork, and incomplete/black frames before choosing templates, features,
    or a compact YAML/JSON catalog representation.
  - Add regression fixtures/tests covering both confirmed loadouts and
    meaningful negative or ambiguous cases.
  - Keep the first implementation read-only: do not change, equip, unequip,
    level, merge, shatter, or purchase modules.
  - Any future correction is separate work and may act only at a verified
    no-battle boundary. Existing and operator-owned battles remain protected by
    `AGENTS.md`; a bounded agent-owned test battle requires its explicit
    Surrender authorization.
- [ ] Decide whether session preflight should validate perk bans and Auto Pick
  Perk order. Keep automation-owned perk selection as a later option.
- [ ] Add the Damage Slider to new-GC-run initialization.
  - Read-only detection, guarded panel navigation, and OCR are fixture-backed;
    live evidence read `1E-22%`, while the changing `94.80M` value is derived
    damage and intentionally ignored.
  - Confirm the desired GC value before encoding policy.
  - Define it in strategy configuration rather than hardcoding runtime logic,
    verify the applied value, and make adjustment safe to repeat.

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
