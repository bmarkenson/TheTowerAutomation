# Runtime, Validation, and Farm Backlog

This file contains active work only. Before live work, follow `AGENTS.md`,
[`../new_thread.md`](../new_thread.md), complete
[`../live_preflight.md`](../live_preflight.md), and load only the selected
[`operation`](../runtime_operations.md). Historical checked-item detail remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Current validation gates

- [ ] Cross-validate each current exact player-save mapping against fresh UI
  inventory from the same version, and add a new exact candidate whenever the
  game reports a different identity. Execute the complete
  [versioned audit matrix and rollout sequence](../architecture/player_save.md#versioned-audit-matrix-data-9-game-1073--revision-4).
  Promote only fully cross-validated fields; retain scheduled audits and every
  existing UI checker as the fallback for incompatible versions, shape
  changes, stale data, mismatches, and unmapped settings.
  - [ ] Independently validate the version-1101 Tournament generator before
    enabling it, and calibrate the two new per-wave enemy counters only if they
    gain a consumer. Retain scheduled UI audits; any semantic discrepancy in an
    inherited check removes it from the compatibility allowlist immediately.
  - [ ] Complete implementation and ordinary-boundary validation of the
    [typed acquisition and temporal-authority contract](../architecture/player_save.md#acquisition-provenance-and-temporal-authority)
    through one outcome coordinator and sequential stacked feature branches.
    The deployed Perk phase uses the shared typed interface; its superseded
    private prototype path is not runtime authority and must not be
    reconstructed or merged.
    - [x] **Foundation — `feature/player-save-acquisition-foundation`.** Add
      the typed exact-target acquisition owner/result, normalized provenance,
      and focused lock/binding/privacy/failure tests. Migrate the guarded
      serializer, Home preflight, History reader, terminal capture, passive
      audit, and standalone Tournament reader without changing each consumer's
      fallback policy. Preserve the explicit offline import path. Replace
      free-form acquisition strings and the runtime freshness boolean only
      after all migrated callers carry typed evidence.
      Implemented with one immutable bundle and acquirer-owned locked
      transaction. Forced publication remains restoration-gated; terminal and
      passive failures remain nonblocking; History and Home preserve their
      established blocking/UI-fallback distinctions. The offline importer
      remains separate, and focused regression proves exact binding,
      redaction, one-read/many-projector fan-out, projection independence, and
      restoration ambiguity. The supported development checkpoint passed all
      1,712 tests in 322.50 seconds with state-definition and clickmap
      validation clean (the established 44 orphan candidates, zero errors).
    - [x] **Lifecycle — `feature/player-save-boundary-handoff`.** Project the
      structural terminal transition once and persist a normalized one-use
      handoff for Game Over → Home, Game Over → direct Retry, and Tournament
      Results → Home. Reuse it without another read or History UI; when a
      `save_first` Home baseline has neither a handoff nor configuration
      requirements, obtain one guarded forced bundle instead of opening
      History. Keep semantic-report failure independent from structural
      continuity, eliminate the Tournament handler's second conditions read,
      and preserve every current blocking/fail-open failure class.
      Implemented with atomic activity-scope publication/consumption and exact
      process, target-generation, source-scope, mapping, transition, and timing
      validation. Game Over → Home, direct Retry, and Tournament Results → Home
      reuse the terminal tail with zero reads/navigation; invalid handoffs keep
      the established fallback. Structural success survives semantic report
      failure, baseline-only `save_first` Home uses the guarded forced owner,
      and Tournament Results receives explicit conditions without reacquiring.
      The supported development checkpoint passed all 1,724 tests in 377.86
      seconds with state-definition and clickmap validation clean (the
      established 44 orphan candidates, zero errors).
    - [x] **Temporal loadout — `feature/player-save-temporal-authority`.** Add
      temporal metadata and fact-specific merge rules to attachment
      projections. Treat Workshop preset, equipped Guardians, selected Bot
      preset, and equipped Modules as round-invariant after exact round
      binding; keep Cards point-in-time and Bot progression separate. Feed
      those classified facts into No Strategy's actual loadout, and bind the
      active Tournament Workshop preset without game-Home or Android-Home
      input. Same-round invariant conflicts fail closed rather than using the
      newest value. Implemented with typed per-fact temporal classes and a
      private exact mapping/target-generation/final-scope/round binding that is
      published only after continuity persistence. No Strategy's actual
      loadout receives the four round invariants with sticky conflict handling;
      Cards retain point-in-time capture provenance and Bot progression remains
      separate. Tournament consumes the bound Workshop fact once, revalidating
      target and scope without a second read or Home route. Focused regression
      passed 201 tests; the supported development checkpoint passed all 1,737
      tests in 322.38 seconds with state-definition and clickmap validation
      clean (the established 44 orphan candidates, zero errors).
    - [x] **Perks — `feature/save-backed-perk-monitoring-v2`.** Restart from
      the shared interface and selectively port the pure monitor/domain tests
      from `d1c3dec`. A normal scheduler independent of collector opt-in
      consumes shared passive bundles; forced attachment and natural terminal
      bundles fan out to the same monitor without another pull. Saved picks are
      a monotonic same-round positive prefix, never proof that no later pick
      exists. Open terminal Perks unless the bound exhaustion/final-prefix and
      terminal-clear rules prove completeness; preserve compact Game Stats,
      More Stats fallback, and every UI-owned lifecycle action.
      Implemented as a pure typed-bundle monitor plus a normal passive
      scheduler. The same immutable passive object reaches monitoring and the
      optional audit projector; forced attachment and natural terminal bundles
      use the same fan-out without reacquisition. Stable `View Perks` evidence,
      a later nonempty same-round checkpoint, and a still-later bound natural
      clear are all required before terminal Perks navigation is omitted.
      Cleared fields never become inventory, `saveRevision` never establishes
      freshness, and malformed, failed, lagging, regressed, reordered,
      conflicted, rebound, empty, or terminal-only evidence retains the exact
      UI fallback. Focused regression passed 301 tests.
    - [x] Run the supported development checkpoint for the Perk phase and
      retain its result with the phase commit. Automated coverage includes
      one-read/many-projector identity, target/scope handoff rejection,
      restoration ambiguity, structural/semantic independence, temporal and
      prefix conflicts, malformed projections, and every consumer fallback.
      The checkpoint passed all 1,774 tests in 330.07 seconds with
      state-definition and clickmap validation clean (the established 44
      orphan candidates, zero errors).
    - [x] Integrate and deploy the preserved linear stack. Merge commit
      `5e46e0594ba17953b85af3e274d763b9d7cddf77` retains current `develop`
      parent `df184642181c51646f4ad4379aa6bc7ef772d92f` and stack-tip parent
      `33a325b7b3792181b24f8d569135f5f12ac74c82`; the aggregate integration
      passed the supported checkpoint with
      all 1,777 tests in 327.69 seconds, state-definition validation, and
      clickmap validation with zero errors and the established 44 orphan
      candidates. That exact merge commit was fast-forwarded to production at
      2026-08-07 13:14 PDT after both affected services stopped, behind the
      annotated rollback tag
      `production-before-20260807T201303Z-df18464`. Replacement control-
      surface PID `1291663` and automation PID `1292147` were active with the
      automation lock held on exact target `localhost:5555`; the target was
      freshly `device`, control and runtime both acknowledged `RUNNING`, and a
      fresh unpaused observation had no holds or Strategy Gate.
    - [ ] Complete post-deployment validation only at ordinary natural
      boundaries; do not create, surrender, accelerate, or otherwise alter a
      battle for evidence.
      - [x] The 2026-08-07 13:15–13:18 PDT guarded replacement proved Pause
        blocked actions, then restored the prior `RUNNING` intent only after
        the replacement PID, exact lock/target, acknowledgements, and a fresh
        attached-battle observation were valid. The first continuity attempt
        failed closed on an unverified restored-source boundary; the bounded
        retry confirmed the unchanged terminal tail and published no
        unverified evidence.
      - [x] The successful forced-attachment boundary produced one shared
        typed acquisition record at save revision `47927` (the owner's bounded
        two-identical-read stability transaction, not a second consumer pull).
        That bundle supplied a 37-pick initial complete Perk checkpoint and 11
        guarded No Strategy fields, while the observation-only audit receipt
        recorded `forced_running_attachment`; no consumer reacquisition or UI
        fallback was used for those accepted fields. Because the fresh audit
        session had no pre-round History baseline, it explicitly failed that
        comparison closed and made no terminal claim.
      - [x] The next normal passive interval completed at 2026-08-07 13:20 PDT.
        Its single scheduler acquisition produced one `periodic_interval`
        receipt at save revision `47928`, retained the exact same-round binding,
        and advanced the complete Perk prefix from 37 to 38 picks. The deployed
        scheduler passes that one immutable object through the Perk monitor and
        enabled optional audit projector; no second acquisition or consumer
        rejection was recorded.
      - [ ] Confirm valid Game Over → Home and Game Over → direct Retry each
        perform zero second save acquisition and zero Battle History
        navigation.
      - [ ] Confirm Tournament Results → Home reuses its valid terminal handoff
        without reacquisition.
      - [ ] Confirm one natural-terminal bundle fans out without duplicate
        pulls and that terminal Perks navigation is omitted only after bound
        exhaustion, a later nonempty final prefix with exact round binding,
        and a later natural terminal clear prove completeness. No qualifying
        natural terminal boundary occurred during the deployment window, so
        every terminal UI fallback remains authoritative.
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
  - [ ] Extend the version-1073 Module loadout mapping for the primary
    `infoIndex` observed during the 2026-08-06 No Strategy attachment. The
    current privacy-safe projection fails closed with
    `unsupported primary module infoIndex`, so Modules correctly remains a UI
    fallback. Identify the exact new index from bounded raw evidence, map it to
    the independently verified Module identity, retain unknown-index failure,
    and regress the full attachment path so a complete save skips Module UI.
    See the
    [promotion evidence](../issues/evidence/no-strategy-attachment-promotion-2026-08-06.md).
  - [ ] Add any future normal-runtime consumer only through the shared typed
    acquisition owner and only after its own matrix evidence is complete. The
    optional `V1073-RUNTIME-013` audit collector may consume a passive bundle
    but is not an acquisition service or an authority source. Every consumer
    must preserve current-process terminal binding and its own temporal,
    fallback, and action-authority rules.
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
    - [ ] Continue routing incompatible versions and shapes, unknown IDs or
      values, unsupported Module requests, and Damage Slider through UI. A
      unique exact-evidence mapping attempt may use the existing fail-closed
      resolver; ambiguity/conflict remains UI-only, and the observation
      collector never supplies preflight authority.
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

- [ ] Live-confirm the deployed paused Home manual-start repair from `8cf5548`:
  from verified `NEW_BATTLE`, keep Pause through
  the manual start, require passive `RUNNING`, then Resume. Confirm one guarded
  save records or validates the last completed battle without Battle History
  UI, releases No Strategy without a retry loop, and opens only genuinely
  unresolved configuration sections. Require zero input while Paused; an
  Attack sword must not probe its disabled menu, while a Utility star must
  leave the accessible Attack Damage Slider in the inventory plan. The generic
  replacement attachment and terminal-lease replay were confirmed during
  promotion of `ab84a3c`; track only
  [ISSUE-2026-027](../issues/open-2026.md#paused-home-continuity-did-not-follow-a-manually-started-battle).
- [ ] Close an owned exclusive-validation `cleanup` receipt when the same
  runtime has already proved that its validation battle reached Game Over and
  later observes `RUNNING` before verified Home cleanup. Fail closed, release
  action authority, and perform no Retry, Surrender, or other recovery input,
  as recorded in
  [open issue dossier](../issues/open-2026.md#owned-validation-cleanup-survived-a-later-running-battle-transition).
- [x] Make `STOPPED` interrupt an in-progress Home setup without another device
  input, as recorded in
  [open issue dossier](../issues/open-2026.md#stopped-control-could-not-interrupt-an-in-progress-home-setup-guard).
  Pause, Stop, and Take Manual Control now yield the synchronous route at its
  first denied input without cleanup. Observation and acknowledgement resume
  on the next heartbeat; only a later same-owner Enable can attempt one bounded
  Home restoration, while Stop or changed workflow ownership discards it.
- [ ] Detect likely manual player activity and automatically yield tap authority.
  - Treat unexpected Go Home/manual navigation during an active run as operator
    activity rather than an error to undo immediately.
  - Pause while screens continue changing or recent external input is evident.
  - After a configurable static grace period, warn before offering or performing
    a guarded return to the running battle.
  - Make the grace period interruptible and extendable through CLI/GUI controls.

- [ ] **Better Control Model:** redesign the operator controls around
  independent process lifecycle, automation action authority, observed
  game/battle state, Strategy scope, and terminal policy. Do not use one
  **Paused/Running** or **Next Battle** choice to imply more than one of those
  dimensions.
  - Implementation checkpoint (2026-08-07, feature branch):
    - [x] Linux status/API/runtime plus native, browser, and CLI clients expose
      the five independent dimensions under server revision 29 and capabilities
      `better_control_model_v2` and `save_backed_setup_capture_v1`, while
      retaining additive `better_control_model_v1`. Start/Stop Automation,
      exact Start Battle,
      exact Attach to Battle, future terminal policy, and Take/Return Control
      have durable requested/acknowledged/error state. State and terminal-policy
      acknowledgements are correlated by exact request identity rather than
      same-value timestamps. Stale, mismatched,
      unavailable, busy, pending, rejected, interrupted, acknowledged, and
      no-op paths have repository regressions.
    - [x] Managed Start launches Paused with no implicit battle workflow.
      Start Battle revalidates exact runtime/target/scope/Home New Battle
      evidence and enters normal new-run gates. Home does not toggle action
      authority, and active-battle → Home Resume Battle activity yields through
      an indefinite manual-control Pause rather than competing for input.
      Verified Home input is recorded as `action_dispatched` and remains under
      an exclusive workflow hold until battle adoption, interruption, or a
      bounded failure.
    - [x] Tournament Results capture retains `WAIT`; Continue and Home use the
      verified OK-to-Home dismissal owner after persisting the result, Pause on
      failed navigation, and keep dismissal separate from the future
      next-battle policy.
    - [x] Attach and Return consume production's typed player-save acquisition
      paradigm. Active/resumable paths use one guarded exact-target forced
      serialization, mandatory active-round identity, final activity-scope
      binding, and process-local typed claims. Home New Return uses the normal
      Home serialization; Game Over Return uses the bound natural acquisition.
      Cached/passive reads cannot satisfy a current-save claim, save receipts
      precede any allowlisted unresolved-field UI fallback, and a loss after
      backgrounding terminates the exact workflow with Automation Paused.
      Home New Return also terminalizes a blocked or incomplete refresh once,
      rather than repeating lifecycle input on a later heartbeat.
      Attach becomes observation-only before any later explicit active-battle
      Strategy adoption. Adoption grants no Surrender authority.
    - [x] Take/Return Control includes manual Surrender disposition without a
      Surrender button: minimal save-backed excluded recording is the default,
      with explicit opt-in to full terminal collection. Strategy repair can
      Surrender only through the exact one-shot runtime gate option; it records
      the nonrepresentative outcome before verified Home, Pauses, and does not
      start another battle.
    - [x] **Capture current setup as…** performs a guarded fresh serialization,
      projects through existing Strategy/local-loadout/Module-preset owners,
      preserves unresolved fields, requires a fingerprinted captured-versus-
      Base review for Strategy drafts, and saves only new inactive artifacts.
      A trusted-mismatch active-battle Return can expose its exact retained
      forced acquisition to Capture without another input; that path remains
      Paused and does not resolve Return Control.
      The optional Base is comparison-only. Captured Strategy source is durable
      and reopenable with its own fingerprinted origin, semantic difference,
      and unresolved review through Linux API, two-step CLI, and the native
      authoring catalog. A failed ready receipt retries from the process-local
      result without another serialization; normal Linux validation/publication
      remains separate.
    - [ ] Windows runtime usability smoke and natural-boundary live game
      validation remain pending. The WPF project cross-builds on Linux and its
      82 native authoring/compatibility tests pass, but no Windows runtime or
      live/device action is claimed by this checkpoint.
      The supported development checkpoint passes all 1,904 tests in 335.03
      seconds, state-definition validation, and clickmap validation with zero
      errors and the established 44 orphan candidates.
  - Integration hardening checkpoint (2026-08-08, feature branch after merging
    production `08745f5`):
    - [x] Home setup yields immediately on Pause, Stop, or Take Manual Control
      instead of waiting inside the input guard. No cleanup input occurs at the
      denied boundary. A process-local pending recovery is exact-owner,
      runtime, target-generation, and activity-scope bound; one later Enable
      may restore Home, while owner/binding change discards it and a failed
      enabled recovery Pauses and terminalizes without repetition.
    - [x] Home Return preserves a bounded nonretryable Perk-repair outcome as
      `awaiting_manual_correction`, including the failed check, reason,
      retryability, and forced-save receipt. It does not serialize or open UI
      again until explicit Enable after the operator's correction; that retry
      discards the former private claim and requests a new save.
    - [x] Setup Capture consumes production's complete Perk vocabulary;
      `chrono_field_duration` is covered by a captured, semantic-diffed,
      saveable, nonactivating Strategy-draft regression.
    - [x] Focused Better Control/Home/Save/Capture validation passed 217 tests;
      the broader affected slice passed 479 tests; all 82 native
      authoring/compatibility tests passed; and Linux cross-publishing produced
      both self-contained Windows executables. The supported development
      checkpoint passed all 1,924 tests in 330.30 seconds, state-definition
      validation, and clickmap validation with zero errors and the established
      44 orphan candidates.
  - Production deployment checkpoint (2026-08-08):
    - [x] Exact integration candidate `030ad4a` repeated the complete supported
      checkpoint: all 1,924 tests passed in 329.79 seconds, state definitions
      passed, and clickmap validation reported zero errors and the established
      44 orphan candidates.
    - [x] Production advanced from `08745f5` under rollback tag
      `production-before-20260808T083546Z-08745f5`. Replacement control-surface
      PID `2162885` served API revision 29, capabilities
      `better_control_model_v2` and `save_backed_setup_capture_v1`, and the new
      browser assets. The automation service remained STOPPED/inactive, its
      target locks remained released, and no device input was sent.
    - [x] The complete self-contained Windows package was atomically rebuilt at
      `windows/TheTower.ControlSurface/publish/win-x64`, including both
      `TheTower.ControlSurface.exe` and `TheTower.TunnelHost.exe`.
  - Compatible-save repair checkpoint (2026-08-08, feature branch based on
    `b292779`):
    - [x] Commit `f7c569c` accepts the effective compatible-v1101 runtime and
      per-check authoring authority. The real synthetic v1101 decode now passes
      runtime battle binding and the strict capture-preview validator; fields
      outside the inherited allowlist remain unresolved and saving still grants
      no selection, publication, queue, application, or input authority.
    - [x] Unsupported/incompatible mappings, absent runtime projection, and
      incomplete round identity report Capture `unavailable` without entering
      authoring or UI fallback. A proved opposite round fact remains `failed`.
      Every post-background outcome persisted Automation Paused at this
      checkpoint; revision 30's failure-continuity checkpoint below supersedes
      that over-broad authority outcome.
    - [x] Enable at Home cannot inherit ordinary Home serialization/setup,
      legacy auto-start, or one-shot Tournament-validation launch while the
      initial Start Battle/Attach to Battle intent is unresolved. Stale
      acknowledged Start state also cannot dispatch without the matching
      in-process MissionManager authorization; explicit Start still owns normal
      gates and Tournament validation through lifecycle adoption.
    - [x] Focused Home/Better-Control/player-save/capture/Tournament validation
      passed 305 tests. The complete supported checkpoint passed all 1,946
      tests in 357.16 seconds, state-definition validation, and clickmap
      validation with zero errors and the established 44 orphan candidates.
      All 82 native authoring/compatibility tests passed on Linux; NuGet's
      read-only vulnerability-cache warning did not affect build or execution.
    - [ ] Deploy this repair through the production procedure only after
      separate authorization, then retry setup capture from a natural safe
      boundary. No live/device or Windows runtime validation is claimed here.
    - [ ] Confirm the native Windows usability flow and the Better Control
      transitions at natural Home, resumable/active-battle, manual-control, and
      terminal boundaries before closing this outcome.
  - Failure-continuity checkpoint (2026-08-08, feature branch after merging
    production `d6f6bb7`):
    - [x] Merge commit `c10c9f8` incorporates production's corrected typed
      attachment adoption (`e39a785`) without adding a competing save-scope
      implementation. Exact PID, target/generation, activity-scope,
      active-round, and source-restoration checks remain authoritative.
    - [x] Commit `e7dfb51` advances the server to revision 30, retains
      `save_backed_setup_capture_v1`, and adds
      `save_backed_setup_capture_v2`. Capture terminal receipts now distinguish
      preserved, continuity-gated, safety-paused, and already-paused authority;
      reopening a terminal result is inspect-only and **Try capture again** is
      separate explicit intent.
    - [x] Source-restored mapping/projection/acquisition failures preserve the
      prior authority. An active/resumable battle contradiction uses a Strategy
      Gate so observation and allowlisted gem collection continue. A proved
      Home New contradiction or inability to prove source restoration after
      attempted lifecycle input still Pauses. Atomic ready/terminal receipt
      retry retains the exact process-local result without repeating
      serialization or changing authority.
    - [x] Game Over data collection is best effort and cannot suppress the
      selected Home/Retry route. Failed Game Over and Tournament Results
      navigation stays pending for fresh-evidence retry without global Pause.
      No Strategy persists partial/unresolved evidence and releases verified
      Home; configuration-repair failure remains gated/degraded, and an
      explicitly authorized repair Surrender returns to ordinary Home repair
      and future policy without an implicit Pause.
    - [x] The merged affected Python suite passes 385 tests and all 83 native
      authoring/compatibility tests pass on Linux. NuGet's read-only
      vulnerability-cache warning does not affect build or test execution.
    - [x] The complete supported development checkpoint passed compilation,
      state-definition validation, clickmap validation with zero errors and the
      established 44 orphan candidates, and all 1,962 tests in 340.57 seconds.
    - [ ] Native Windows usability and natural Home/active/terminal boundary
      validation remain pending; no live/device behavior is claimed by this
      checkpoint.
  - UI-fallback contract repair checkpoint (2026-08-08, feature branch based
    directly on the completed Better Control tip `848c886`):
    - [x] Better Control's typed reconciliation receipt now accepts exactly one
      process-local authority source: a forced/natural save acquisition or a
      runtime/target-generation/activity-scope-bound UI fallback. The durable
      receipt remains diagnostic and cannot replay either source.
    - [x] Missing, unsupported-revision, structurally incompatible, and
      unprojectable saves automatically select the complete supported UI route
      after safe source restoration. Active/resumable Attach uses Battle
      History and remains observation-only with No Strategy monitoring
      Enabled; running Return additionally runs every active-Strategy UI check;
      Home New Return runs every Home configuration check; and Game Over Return
      uses the full Game Stats/Perks/More Stats collector without suppressing
      the selected terminal route.
    - [x] Source-restoration, process, target, owner, scope, and action-authority
      loss still block input. A trusted mapped mismatch remains valid evidence
      with its explicit review semantics. Save-backed setup capture remains the
      explicit exception because no supported UI route can create one coherent
      authoring snapshot; an unavailable capture preserves ordinary UI
      monitoring and the documented authority outcome.
    - [x] The broader affected control, save, No Strategy, run-initialization,
      Home-setup, and terminal slice passes 622 tests. The complete supported
      checkpoint passed compilation, state-definition validation, clickmap
      validation with zero errors and the established 44 orphan candidates,
      and all 1,968 tests in 340.15 seconds. Production promotion evidence
      remains pending.
  - Begin with a command/transition matrix covering stopped and live services;
    acknowledged automation paused and enabled; Home New Battle and Resume
    Battle, active battle, Game Over, and Tournament Results; and current,
    pending, and startup-default Strategies. Make illegal, unavailable, pending,
    and no-op requests visibly distinct. Preserve directive/acknowledgement and
    owner/freshness checks rather than deriving authority from GUI state.
  - Separate **Start automation** and **Stop automation** from the battle
    workflow. Provide explicit **Start battle** intent only at a verified new-run
    Home boundary and explicit **Attach to battle** intent only for a verified
    active or resumable battle. Starting a new battle runs its normal gates;
    attachment preserves the existing battle identity and first attempts fresh
    save evidence. Safely restored unusable save data selects the supported UI
    continuity and discovery route; an unsafe owner/source/binding boundary
    blocks input. Reject a mismatched intent without silently choosing the other
    workflow.
  - Present post-terminal behavior separately as **When this battle ends** (or
    equally unambiguous final wording): continue automatically, wait at the
    terminal boundary, or return/stay Home. The existing `NEXT_BATTLE`, `WAIT`,
    and `HOME` values may remain a compatible runtime representation, but an
    immediate battle command must not be labelled as that future policy.
  - Rename or qualify the current control labels so automation **Paused** means
    zero automated device input while observation may continue, and automation
    **Enabled** means the runtime may exercise its guarded action authority; it
    does not assert that the game itself is in `RUNNING`. Home does not
    implicitly require either state. Document which passive observations,
    explicit operator-approved maintenance requests, and automatic actions are
    allowed in every state.
  - Add a first-class **Take manual control** / **Return control** workflow.
    Taking control must obtain and acknowledge an indefinite automation Pause
    before inviting manual game changes. Returning control must refresh
    observation, reconcile the same/new battle boundary and relevant
    configuration, and resume only by explicit operator intent. Unexpected
    manual activity while automation is enabled must yield through the existing
    manual-activity safety outcome rather than competing for input. Manual run
    termination must use the report-disposition outcome in the agreed sequence
    below without turning that declaration into Surrender authority.
  - Audit every path that claims to read a *new* or *current* save. Such a path
    must invoke the approved serialization/refresh operation, bind stable reads
    to the exact target and battle/session evidence, and report when Pause or
    another authority boundary prevents refresh. A cached save may be consumed
    only under an explicit age/identity contract and must never be described as
    newly requested. Add regressions for attachment and return-from-manual-control
    so save validation precedes any configuration UI fallback.
  - Offer a save-backed **Capture current setup as...** authoring workflow so
    manual loadout changes can become a named managed preset or custom Strategy
    draft without hand-editing Strategy source. Inventory and extend the existing
    preset/local editor owners instead of creating a parallel loadout schema;
    retain unresolved fields explicitly, show the captured-versus-base diff,
    validate through normal Linux authority, and never select, activate, or
    apply the result merely because it was saved.
  - Version any changed API model with a named capability and update native,
    browser, CLI, architecture, and operator guidance together. Follow the
    [action-log contract](../action_log_contract.md) for each resulting input
    workflow. Cover the transition matrix with server and client regressions,
    then run a Windows usability smoke; live game validation must use natural
    safe boundaries and must never Surrender an operator-owned battle.
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
   - Prefer evidence-based inference. A causally bound terminal save whose
     mapped `killedBy` value is `Surrender` identifies a surrendered run;
     runtime-owned validation or repair receipts must distinguish their own
     Surrenders from an operator action. Manual-control state or an unexpected
     terminal screen alone is not sufficient evidence of Surrender.
   - Offer an optional exact-run declaration such as **I ended this run
     manually — exclude it from analytics** before or after the terminal
     boundary. It records operator intent and may satisfy otherwise ambiguous
     attribution, but sends no game input, grants no Surrender authority, is
     consumed by only the bound activity/run, and fails closed rather than
     applying to a later battle.
   - When surrender or manual-abort disposition is known before full terminal
     collection, require the fresh save/boundary evidence needed to identify
     the completed run, retain a minimal durable record and provenance, and
     skip stats UI plus optional enrichment. A later declaration reclassifies
     only the exact completed record; it never deletes evidence.
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
