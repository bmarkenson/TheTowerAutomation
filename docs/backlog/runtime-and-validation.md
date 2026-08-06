# Runtime, Validation, and Farm Backlog

This file contains active work only. Before live work, follow `AGENTS.md`,
[`../new_thread.md`](../new_thread.md), complete
[`../live_preflight.md`](../live_preflight.md), and load only the selected
[`operation`](../runtime_operations.md). Historical checked-item detail remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Current validation gates

- [ ] Cross-validate the `data-9-game-1073` player-save mapping against fresh
  UI inventory from the same version, and add a new exact candidate mapping if
  the current game reports a different identity. Execute the complete
  [versioned audit matrix and rollout sequence](../architecture/player_save.md#versioned-audit-matrix-data-9-game-1073--revision-4).
  Promote only fully cross-validated fields; retain scheduled audits and every
  existing UI checker as the fallback for unknown versions, shape changes,
  stale data, mismatches, and unmapped settings.
  - [ ] Confirm the deployed direct-Retry repair on the next ordinary
    `GAME_OVER -> RUNNING` pair. The first continuation exposed old-identity
    retention and seven unmapped Perk IDs, fixed by `b137ea4`. The fresh
    session accepted counter 232 at revision `46521`, wave 290, without
    inheriting a terminal-only pre-round baseline; the next same-process Retry
    should emit `terminal_retry_baseline_carried` and accept the first
    advancing revision under the new identity. Observe normal farming rather
    than creating a special battle.
  - [ ] Extend the exact-version runtime projection with independently failing
    active-upgrade, survival-ability, and allowlisted live-tally components.
    For upgrades, map all three current-level arrays to their Workshop
    baselines and versioned caps so a gold-box claim is explicit rather than
    inferred from magnitude. For live tallies, prioritize values that replace
    an existing OCR/navigation route or reconcile a terminal record; keep the
    arbitrary decoded root private.
  - [ ] Causally calibrate Demon Mode, Nuke, and Second Wind active-round
    fields across natural activation, recharge, repeated activation, Game
    Over, and clearing. Establish use-count polarity, sentinel values, timer
    units, recharge lengths, and whether each countdown/timeout yields an exact
    activation wave or only bounds it between stable save waves. Validate each
    ability independently and do not infer a complete history from one late
    snapshot.
  - [ ] Merge stable same-round save checkpoints with the existing passive
    visual activation tracker. Count deltas establish event intervals; a
    matching visual transition may refine an interval without double counting.
    Cache the newest complete active snapshot across post-run clearing, retain
    confirmed screenshot-derived events after its saved wave through Game
    Over, and reconcile the merged events with terminal Battle History counts.
    Missing timing remains unknown/bounded; conflicts force the full UI audit.
  - [ ] Inventory checkpoint candidates that could replace other observation
    routes, including game speed, buy quantities, Damage Slider, Orb Distance,
    Card activity, and UW/Bot/Guardian cooldowns. Rank them by navigation/OCR
    cost and staleness tolerance, then validate and adopt one independent claim
    at a time. Enforcement, mutation confirmation, and transition authority
    remain visual.
  - [ ] Bind exact-version Workshop-preset save evidence to an already-active
    Tournament attachment without game-Home or Android-Home input. Require a
    stable read, exact target/version, selected Strategy fingerprint, and
    authoritative active-run identity; stale, ambiguous, or unsupported saves
    leave `workshop_preset` explicitly deferred while the in-battle checks
    continue. This must not broaden attachment authority or reintroduce Exit
    Battle → Go Home → Resume Battle.
  - [ ] Add any other normal-runtime consumer of the observation-only polling
    and same-round audit cache only after its own matrix evidence is complete.
    The terminal report consumer is independent of that cache and uses its own
    bound activity scope plus same-source tail proof. Perks-navigation
    decisions, Strategy facts, lifecycle changes, and further UI suppression
    remain outside `V1073-RUNTIME-013` and must preserve the current-process
    terminal-binding rule.
  - [ ] Replace the in-battle Perk UI timeline with a normal-runtime save
    checkpoint cache independent of collector opt-in. Consume naturally
    serialized stable revisions under the exact round identity; preserve the
    exact saved `PerkPick` wave even when the checkpoint arrives later; and
    obtain a terminal stable save to close the final prefix. Retain UI for an
    unknown ID, acquisition/continuity failure, explicit audit, or unresolved
    final state. Do not background an active battle merely to accelerate a
    Perk checkpoint. The separate `save_first` replacement-process continuity
    boundary is the only current forced active-battle serialization policy and
    grants this cache no authority. This is a separate implementation phase,
    not part of the continuity fix.
  - [ ] Make terminal Perks navigation conditional after the normal-runtime
    same-round checkpoint cache proves the complete final prefix. Save-derived
    normal and Tournament reports are implemented independently with guarded
    history-tail attachment. Preserve passive compact Game Stats OCR, the More
    Stats UI fallback, and every verified
    Wait/Retry/Home/mutation/transition control.
  - [ ] Use natural UI fallbacks and explicit/periodic `comparison_audit` runs
    for future privacy-safe candidates. Candidates never self-promote; mapping
    promotion remains a reviewed code/documentation change.
    - [ ] During an ordinary Farm T18 start, compare its distinct complete
      Target Priority order with the mapped save sequence as generic
      serialization-order confirmation only. The accepted complete Farm
      permutation is already sufficient for the current mapping; do not test
      all permutations.
    - [ ] Extend exact-slot Module values only through future natural paired
      evidence. Magnetic Hook remains unmapped after the operator withdrew its
      identification; an unknown ID, unsupported requested name, or partial
      structure must retain the complete Modules UI route. Do not infer rarity,
      levels, stars, effects, substats, GUIDs, or inventory semantics.
    - [ ] Before supporting mixed Ultimate Weapon primaries, validate one
      weapon index's off/on polarity at a time with normalized
      before/change/restore evidence. Validate Spotlight Missiles off through
      one explicitly authorized reversible transition. Do not enumerate every
      boolean combination.
    - [ ] Prefer a natural Farm `30.00m / 30.00m / 39.00m` → Tournament
      `98.38m / 87.16m / 80.37m` → Farm Orb Distance sequence. Pair UI and
      guarded stable saves to establish field identity, units/rounding,
      selected-preset versus derived semantics, serialization timing, Home
      versus active behavior, and restoration. Only if natural transitions are
      insufficient may a later coordinator authorize one owned bounded
      calibration.
    - [ ] Continue routing unknown versions, shapes, IDs, values, unsupported
      Module requests, and Damage Slider through UI. A unique exact-evidence mapping attempt may use
      the existing fail-closed resolver; ambiguity/conflict remains UI-only,
      and the observation collector never supplies preflight authority.
- [ ] Capture the numeric level of every equipped Module from authoritative
  overview evidence, retain it with preflight and completed-run records, and
  surface threshold violations without confusing an intentional Tournament
  identity variation with a level problem. The current requirement is Primary
  slots at level 201 or higher and Assist slots at level 195 or higher. Existing
  level-transfer guards preserve slot-owned levels during replacement but do
  not OCR or validate the numeric values.
- [ ] Diagnose the transient Home Poison Swamp Stun verification timeout
  recorded in
  [open issue dossier](../issues/open-2026.md#home-poison-swamp-stun-verification-transiently-timed-out-after-its-source-tap).
  Retain the failing frame and individual detail/off/on match confidences on
  recurrence, then distinguish a missed detail-open tap from unsettled
  Workshop scroll geometry or a detail-template miss before changing retry or
  stabilization behavior.
- [ ] Confirm normal gate re-arming after guarded **Reload automation for
  current battle** at the next authoritative boundary under a Strategy that
  declares gates; No Strategy itself has none.
- [ ] Diagnose the unexpectedly early Tier 18 Farm ending recorded in
  [open issue dossier](../issues/open-2026.md#tier-18-farm-ended-at-wave-2644-without-completed-session-preflight).
  Reproduce a clean, fully validated Farm start at 720p before attributing the
  result to resolution, loadout, preflight, or perk ordering.
- [ ] Diagnose the unclean runtime-owner exits recorded in
  [open issue dossier](../issues/open-2026.md#automation-owner-exited-without-a-clean-shutdown-record).
  The owners disappeared without a clean-shutdown record and left stale locks.
  Keep control `PAUSED` while distinguishing execution-session termination,
  an unlogged crash, and manual-player activity before restarting automation.
- [ ] Diagnose the compositor or transport source of the intermittent
  incomplete ADB screenshots recorded in the
  [open issue dossier](../issues/open-2026.md#direct-adb-screenshots-intermittently-returned-incomplete-black-frames).
  The ignored retained files no longer reproduce the corruption: all four
  currently decode as complete, and the Home failed/retry pair is
  byte-identical.
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
  proves that no additional claim is available. Track the investigation in
  [`ISSUE-2026-009`](../issues/open-2026.md#event-mission-claim-appeared-to-repeat-the-complete-list-after-one-claim).
- [ ] Diagnose the dropped Game Stats coin decimal retained for
  `Battle20260722T202039-0700`. The visible base value was `1.82T`, but OCR
  produced `182T`; the resulting split disagreed with the copied `2.72T` total,
  correctly invalidated record quality, and retained source screenshots. Track
  evidence in
  [`ISSUE-2026-007`](../issues/open-2026.md#game-stats-ocr-dropped-a-coin-value-decimal).

## Tournament Battle Condition evidence

- [ ] Capture each Tournament's Battle Conditions into its structured run
  record without depending on `thetower.lol`. Use the canonical alias mapping
  and source-precedence rules in
  [`../game_strategy.md`](../game_strategy.md#tournament-battle-condition-abbreviations).
  - [ ] Preserve a read-only UI fallback that inventories both Heat and
    Overheat tabs from a verified Tournament Heat panel, scrolls to both ends,
    deduplicates overlapping rows, and captures each displayed name, level,
    effective description, and activation wave where present. The retained
    `active_tournament_heat_20260718.png` fixture supplies initial positive
    evidence but is not a complete scroll sequence.
  - [ ] Complete cross-source normalization. The versioned save path now emits
    stable IDs, names, aliases, provenance, game version, capture time, and
    explicit fallback state. The remaining UI reader must retain exact display
    text and unknown conditions losslessly instead of dropping or guessing.
  - [ ] Finish retained-fixture and synthetic-record coverage. Generator,
    version/league failure, post-run identity, provenance, attached-run,
    duplicate enrichment, terminal merge, and idempotent historical backfill
    are covered; complete-scroll merging and unknown UI conditions remain.

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

- [ ] Promote and live-confirm the paused Home manual-start continuity repair
  from `8cf5548`: from verified `NEW_BATTLE`, keep Pause through the manual
  start, require passive `RUNNING`, then Resume and confirm one running-source
  Battle History baseline releases No Strategy inventory without a retry loop
  or any input while Paused. Track the original failure in
  [ISSUE-2026-027](../issues/open-2026.md#paused-home-continuity-did-not-follow-a-manually-started-battle).
- [ ] Close an owned exclusive-validation `cleanup` receipt when the same
  runtime has already proved that its validation battle reached Game Over and
  later observes `RUNNING` before verified Home cleanup. Fail closed, release
  action authority, and perform no Retry, Surrender, or other recovery input,
  as recorded in
  [open issue dossier](../issues/open-2026.md#owned-validation-cleanup-survived-a-later-running-battle-transition).
- [ ] Make `STOPPED` interrupt an in-progress Home setup without another device
  input, as recorded in
  [open issue dossier](../issues/open-2026.md#stopped-control-could-not-interrupt-an-in-progress-home-setup-guard).
  Preserve the current Pause behavior, but let Stop unwind the guarded route
  and release the runtime lock without requiring `KeyboardInterrupt`.
- [ ] Detect likely manual player activity and automatically yield tap authority.
  - Treat unexpected Go Home/manual navigation during an active run as operator
    activity rather than an error to undo immediately.
  - Pause while screens continue changing or recent external input is evident.
  - After a configurable static grace period, warn before offering or performing
    a guarded return to the running battle.
  - Make the grace period interruptible and extendable through CLI/GUI controls.
- [ ] Finish the remaining native Windows GUI control-surface work described in
  [`../architecture/control_surface.md`](../architecture/control_surface.md).
  - [ ] Run the disposable-catalog Windows runtime smoke for Base and Strategy
    preset/local editing, inheritance/override/Ignore, validation errors,
    complete Module preset previews, bundled/custom labels, Create variant,
    Save as preset, catalog refresh/explicit selection, collision/failure
    retention, review/publish, history comparison, and restore-as-new. Make
    only narrow repairs supported by that evidence; creation and publication
    must not activate automation. Revalidate all eight Module slots with the
    revision-25 package after changing a Module choice and after creating each
    kind of custom preset; `7e4c7a2` fixed the earlier blank-selection defect.
  - [ ] Attribute and reduce Windows control-surface CPU use for passive
    performance collection. Retained 2026-07-30/31 aggregates measured
    approximately `0.83%` process CPU clean and `1.9%` under contention,
    exceeding the `<0.5%` non-frame telemetry target. Profile sampler work
    separately from UI and other process activity, retain complete evidence
    cadence, and validate both clean and contended cases. Track evidence in
    [`ISSUE-2026-003`](../issues/open-2026.md#windows-performance-telemetry-exceeded-its-client-cpu-budget).
  - [ ] Confirm that a second launch from the SMB publish path reaches the
    single-instance guard without showing a host/runtime prompt or creating a
    second client, as tracked in
    [`ISSUE-2026-013`](../issues/open-2026.md#a-second-native-client-launch-produced-a-misleading-runtime-prompt).

### Agreed operator-control sequence

The first implementation target is Battle History filter reliability. The
remaining order is provisional and can change as operator use supplies better
evidence.

1. [ ] Verify the repaired Battle History filter input on Windows: the window
   opens without terminating the app, one click opens all three combo boxes,
   each popup remains usable across independent refreshes, and mouse and
   keyboard selection work normally. Track confirmation in
   [`ISSUE-2026-014`](../issues/open-2026.md#battle-history-filter-dropdowns-required-repeated-clicks).
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
   - Resolve the stale-running top-bar defect tracked in
     [`ISSUE-2026-012`](../issues/open-2026.md#native-top-bar-retained-a-running-directive-after-automation-stopped).
4. [ ] Extend concise human-readable requirement results to in-battle
   session-preflight and recovery checks, including expected state, observed
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
7. [ ] Verify boundary-aware Strategy changes on Windows: immediate accepted-
   request feedback, current/pending display, selection retention, pending
   replacement/cancellation, stale-server warning and explicit reload,
   active-battle adoption, and an acknowledged paused Workshop application
   without changing Pause. Track confirmation in
   [`ISSUE-2026-010`](../issues/open-2026.md#native-strategy-selection-did-not-report-acceptance-or-live-disposition)
   and
   [`ISSUE-2026-011`](../issues/open-2026.md#windows-client-could-not-identify-or-reload-a-stale-linux-control-service).
8. [ ] Rework Battle History filters after the input defect is fixed and their
   real behavior can be evaluated.
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
10. [ ] Validate the revised dashboard layout on Windows at minimum, default,
    and maximized sizes. Based on operator use, decide whether drag-to-reorder,
    hiding, or floating panes add enough value beyond the tabbed and
    collapsible layout.
11. [ ] Confirm on Windows that the optional Token tooltip and Setup placement
    make clear that it is only for an explicitly authenticated adapter or
    reverse proxy and should remain blank for the normal loopback SSH tunnel.
