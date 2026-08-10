# Active Observed Issue Index

This index owns active lifecycle and routing, not full evidence. Read every
global hazard before live work, then load the matching
[`issues/open-2026.md`](issues/open-2026.md) dossier only on its listed
condition. Actionable work remains in linked backlogs.

Unreproduced reports are
[`issues/unconfirmed-2026.md`](issues/unconfirmed-2026.md); fixes are in
[`issues/resolved-2026.md`](issues/resolved-2026.md). Neither establishes
current runtime state.

## Global live-preflight hazards

### Owned validation cleanup survived a later running-battle transition

**Stable ID:** `ISSUE-2026-001` · **Lifecycle:** `confirmed_unresolved`

- A cleanup receipt kept exclusive authority after its
  claimed battle reached Game Over and a different battle appeared; after that
  sequence, fail closed, release the receipt, and perform no recovery input.
- Load the [dossier](issues/open-2026.md#owned-validation-cleanup-survived-a-later-running-battle-transition)
  before claiming, cleaning, replacing, or recovering an exclusive-validation
  battle. Next: prove the same-runtime later-`RUNNING` closure path without
  Retry or Surrender; [runtime backlog](backlog/runtime-and-validation.md#runtime-control).

### Stopped control could not interrupt an in-progress Home setup guard

**Stable ID:** `ISSUE-2026-004` · **Lifecycle:** `repair_awaiting_confirmation`

- `STOPPED` was acknowledged while an unbounded Home input
  guard retained the process and lock. The implemented repair makes Pause,
  Stop, and authority handoff yield without cleanup input.
- Load the [dossier](issues/open-2026.md#stopped-control-could-not-interrupt-an-in-progress-home-setup-guard)
  before changing or diagnosing Home-setup Stop/Pause behavior or when this
  wait recurs. Next: confirm the deployed Stop interruption and lock release at
  a natural Home setup boundary; [runtime backlog](backlog/runtime-and-validation.md#runtime-control).

### Direct ADB screenshots intermittently returned incomplete black frames

**Stable ID:** `ISSUE-2026-022` · **Lifecycle:** `source_unresolved`

- Valid-sized PNGs sometimes contained actionable strips
  amid black pixels; an incomplete frame is never state or action authority,
  and capture/state/control checks must fail closed until a fresh complete frame.
- Load the [dossier](issues/open-2026.md#direct-adb-screenshots-intermittently-returned-incomplete-black-frames)
  before direct-capture authority work or on a matching frame. Next: retain the
  original corrupted bytes and identify the source; [validation backlog](backlog/runtime-and-validation.md#current-validation-gates).

## Domain-specific unresolved issues

### T19 Farm retained near-normal game-clock speed while entity throughput collapsed

**Stable ID:** `ISSUE-2026-002` · **Lifecycle:** `confirmed_unresolved`

- Two T19 runs kept near-normal effective speed but
  processed far fewer enemies and combos; host scheduling is an inference, so
  do not change strategy or speed policy from this evidence alone.
- Load the [dossier](issues/open-2026.md#t19-farm-retained-near-normal-game-clock-speed-while-entity-throughput-collapsed)
  for a throughput recurrence, T19 causal analysis, or host-correlation work.
  Next: pair exact loadout/locks with host counters and targeted frame summaries;
  see the [runtime backlog](backlog/runtime-and-validation.md).

### Windows performance telemetry exceeded its client CPU budget

**Stable ID:** `ISSUE-2026-003` · **Lifecycle:** `confirmed_unresolved`

- Passive client CPU exceeded the `<0.5%` target,
  especially under contention; preserve evidence cadence until sampler, UI,
  serialization, and other client costs are separately attributed.
- Load the [dossier](issues/open-2026.md#windows-performance-telemetry-exceeded-its-client-cpu-budget)
  before profiling or changing telemetry coverage/cadence. Next: profile clean
  and contended cases against the retained windows; [runtime backlog](backlog/runtime-and-validation.md#runtime-control).

### Home Poison Swamp Stun verification transiently timed out after its source tap

**Stable ID:** `ISSUE-2026-005` · **Lifecycle:** `unresolved_pending_recurrence_evidence`

- One authorized source tap yielded only `unknown`
  post-tap evidence although Retry verified Stun off; the gate must still block
  Battle start without a waiver.
- Load the [dossier](issues/open-2026.md#home-poison-swamp-stun-verification-transiently-timed-out-after-its-source-tap)
  on recurrence or before changing this verifier. Next: retain the final frame
  and detail/off/on confidences; [validation backlog](backlog/runtime-and-validation.md#current-validation-gates).

### Game Stats OCR dropped a coin-value decimal

**Stable ID:** `ISSUE-2026-007` · **Lifecycle:** `open_unresolved`

- Coin-split OCR inflated or stripped values that
  disagreed with copied totals; keep contradictory records invalid and preserve
  their source evidence rather than silently repairing them.
- Load the [dossier](issues/open-2026.md#game-stats-ocr-dropped-a-coin-value-decimal)
  for coin-split repair, affected analytics, or a recurrence. Next: reproduce
  both retained frames and add regressions; [validation backlog](backlog/runtime-and-validation.md#current-validation-gates).

### Event Mission claim appeared to repeat the complete list after one claim

**Stable ID:** `ISSUE-2026-009` · **Lifecycle:** `open_unresolved`

- An uncorrelated observation looked like a full
  rescan after one claim; do not alter the termination rule until logs,
  captures, and dispatch ownership distinguish convergence from redundancy.
- Load the [dossier](issues/open-2026.md#event-mission-claim-appeared-to-repeat-the-complete-list-after-one-claim)
  for a recurrence or Event Mission scan-flow change. Next: retain the complete
  correlated sequence; [validation backlog](backlog/runtime-and-validation.md#current-validation-gates).

### Native top bar retained a running directive after automation stopped

**Stable ID:** `ISSUE-2026-012` · **Lifecycle:** `open_unresolved`

- The primary top bar could imply that automation was
  running after process evidence became inactive; keep saved next-start intent
  distinct from authoritative process disposition.
- Load the [dossier](issues/open-2026.md#native-top-bar-retained-a-running-directive-after-automation-stopped)
  before changing runtime status or the top bar. Next: implement and verify an
  unambiguous stopped presentation; [operator-control backlog](backlog/runtime-and-validation.md#agreed-operator-control-sequence).

### Coins/min visual OCR failed to recognize q or Q

**Stable ID:** `ISSUE-2026-024` · **Lifecycle:** `open_unresolved`

- A later operator report says visual OCR still
  missed case-sensitive `q`/`Q`; keep the plausibility guard and do not conflate
  this missing crop with the resolved parser/cleanup defect.
- Load the [dossier](issues/open-2026.md#coinsmin-visual-ocr-failed-to-recognize-q-or-q)
  on recurrence or before Coins/min OCR changes. Next: retain the crop,
  preprocessing variants, candidates/confidence, and surrounding readings; [detection backlog](backlog/state-and-detection.md#detection-architecture).

### Wave OCR dropped the leading digits from wave 1180

**Stable ID:** `ISSUE-2026-018` · **Lifecycle:** `open_unresolved`

- One frame replaced wave 1070 with 80 before 1270;
  reject or separately confirm rollbacks without restoring stale fixed-rate,
  digit-width, or ceiling assumptions.
- Load the [dossier](issues/open-2026.md#wave-ocr-dropped-the-leading-digits-from-wave-1180)
  for a rollback recurrence or wave-consumption change. Next: retain the rejected
  frame, crop, candidates, support, and confidence; [detection backlog](backlog/state-and-detection.md#detection-architecture).

### Tier 18 Farm ended at wave 2644 without completed session preflight

**Stable ID:** `ISSUE-2026-020` · **Lifecycle:** `open_unresolved`

- A short T18 run lacked completed session-preflight
  evidence and crossed an automation outage; do not attribute its death to
  resolution, loadout, Perks, or RNG from this record alone.
- Load the [dossier](issues/open-2026.md#tier-18-farm-ended-at-wave-2644-without-completed-session-preflight)
  before causal use of this run or on a similar outcome. Next: compare a clean,
  fully validated 720p start; [validation backlog](backlog/runtime-and-validation.md#current-validation-gates).

### Automation owner exited without a clean shutdown record

**Stable ID:** `ISSUE-2026-023` · **Lifecycle:** `open_unresolved`

- Two owners vanished without structured shutdown and
  left stale-looking lock metadata; on a matching recurrence, Pause and verify
  lock/process/screen evidence before any restart or input.
- Load the [dossier](issues/open-2026.md#automation-owner-exited-without-a-clean-shutdown-record)
  before recovering such an exit or investigating its cause. Next: distinguish
  wrapper termination, crash, and manual activity; [validation backlog](backlog/runtime-and-validation.md#current-validation-gates).

## Repairs awaiting confirmation

### Farm Bot preset switch required more Event medals than were available

**Stable ID:** `ISSUE-2026-008` · **Lifecycle:** `repair_awaiting_confirmation`

- Farm selection hit a verified insufficient-medals
  dialog; any fallback may waive only `bots_preset`, never unrelated gates or a
  battle boundary.
- Load the [dossier](issues/open-2026.md#farm-bot-preset-switch-required-more-event-medals-than-were-available)
  only for the actual rejection path, recurrence, or fallback change. Next:
  exercise the scoped Flame decision; [validation backlog](backlog/runtime-and-validation.md#current-validation-gates).

### Native strategy selection did not report acceptance or live disposition

**Stable ID:** `ISSUE-2026-010` · **Lifecycle:** `repair_awaiting_confirmation`

- Native selection lacked accepted/current/pending feedback, and a later
  same-ID publication was incorrectly treated as the definition already
  loaded. Preserve failed choices; genuine active-process dropdown changes and
  successful active-process publication/restore must queue next-boundary use
  automatically, while stopped publication/restore updates only the visible
  Start selection and active-battle adoption stays explicit, definition-aware,
  and boundary-aware.
- Load the [dossier](issues/open-2026.md#native-strategy-selection-did-not-report-acceptance-or-live-disposition)
  for native strategy confirmation or recurrence. Next: verify same-ID
  publication/reload, automatic dropdown queueing, conditional retry,
  explicit active adoption, feedback, and Pause preservation on Windows;
  [operator-control backlog](backlog/runtime-and-validation.md#agreed-operator-control-sequence).

### Windows client could not identify or reload a stale Linux control service

**Stable ID:** `ISSUE-2026-011` · **Lifecycle:** `repair_awaiting_confirmation`

- A stale API could silently lack client-required
  capabilities; recovery may restart only the fixed control-surface service and
  must verify the complete contract without touching automation.
- Load the [dossier](issues/open-2026.md#windows-client-could-not-identify-or-reload-a-stale-linux-control-service)
  for compatibility/reload work or recurrence. Next: verify the prominent
  warning and reconnect flow on Windows; [operator-control backlog](backlog/runtime-and-validation.md#agreed-operator-control-sequence).

### A second native-client launch produced a misleading runtime prompt

**Stable ID:** `ISSUE-2026-013` · **Lifecycle:** `repair_awaiting_confirmation`

- A second SMB launch showed a runtime prompt while a
  client already ran; single-instance handling must foreground the first client
  without creating a second managed process.
- Load the [dossier](issues/open-2026.md#a-second-native-client-launch-produced-a-misleading-runtime-prompt)
  for the exact SMB path or packaging recurrence. Next: confirm the mutex guard
  is reached before any prompt; [runtime backlog](backlog/runtime-and-validation.md#runtime-control).

### Battle History filter dropdowns required repeated clicks

**Stable ID:** `ISSUE-2026-014` · **Lifecycle:** `repair_awaiting_confirmation`

- Filter popups were unreliable and one deferred
  collection mutation crashed window opening; never mutate a collection view
  while refresh is deferred.
- Load the [dossier](issues/open-2026.md#battle-history-filter-dropdowns-required-repeated-clicks)
  for filter/input recurrence or Windows confirmation. Next: verify one-click
  mouse/keyboard behavior across refreshes; [operator-control backlog](backlog/runtime-and-validation.md#agreed-operator-control-sequence).

### Live ADB target move could not be applied by a paused runtime

**Stable ID:** `ISSUE-2026-017` · **Lifecycle:** `repair_awaiting_confirmation`

- Saving a port did not move a running process, and
  lost capture delayed Pause acknowledgement; live handoff requires acknowledged
  indefinite Pause, new-target validation, and failure rollback to the old target.
- Load the [dossier](issues/open-2026.md#live-adb-target-move-could-not-be-applied-by-a-paused-runtime)
  before a real move, recurrence, or handoff change. Next: deploy and verify one
  emulator move; [operator-control backlog](backlog/runtime-and-validation.md#agreed-operator-control-sequence).

### Saved GUI ADB port was ignored by an outdated installed systemd unit

**Stable ID:** `ISSUE-2026-019` · **Lifecycle:** `repair_awaiting_confirmation`

- An installed unit omitted its environment file and
  used port 5555 despite saved 5565; reject deployment mismatch rather than
  accepting an undeliverable port.
- Load the [dossier](issues/open-2026.md#saved-gui-adb-port-was-ignored-by-an-outdated-installed-systemd-unit)
  for managed-port deployment or recurrence. Next: confirm the configured target
  at a managed start; [runtime backlog](backlog/runtime-and-validation.md#runtime-control).

### Native control polling reset pending mode selections and delayed activity

**Stable ID:** `ISSUE-2026-021` · **Lifecycle:** `repair_awaiting_confirmation`

- Status polling overwrote an unsent mode and coupled
  activity to slower requests; preserve local edits and field-specific
  acknowledgements while refreshing activity independently.
- Load the [dossier](issues/open-2026.md#native-control-polling-reset-pending-mode-selections-and-delayed-activity)
  for Windows confirmation or recurrence. Next: verify mode retention and timely
  activity in the rebuilt client; [operator-control backlog](backlog/runtime-and-validation.md#agreed-operator-control-sequence).

### Paused Home continuity did not follow a manually started battle

**Stable ID:** `ISSUE-2026-027` · **Lifecycle:** `repair_awaiting_confirmation`

- A manual `NEW_BATTLE -> RUNNING` transition left the
  pending Home History baseline retrying its obsolete source and blocked No
  Strategy inventory, although Pause correctly suppressed every input.
- Load the [dossier](issues/open-2026.md#paused-home-continuity-did-not-follow-a-manually-started-battle)
  before changing paused manual-start continuity or on recurrence. Next:
  observe one natural paused manual start under deployed `ab84a3c`; the
  replacement attachment and lease replay follow-ons are resolved in
  [2026 history](issues/resolved-2026.md#running-attachment-used-battle-history-and-repeated-save-backed-configuration-ui).
  [Runtime backlog](backlog/runtime-and-validation.md#runtime-control).
