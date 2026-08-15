# Runtime, Validation, and Farm Backlog

This file contains active work only. Before live work, follow `AGENTS.md`,
[`../new_thread.md`](../new_thread.md), complete
[`../live_preflight.md`](../live_preflight.md), and load only the selected
[`operation`](../runtime_operations.md). Completed outcomes belong in the
[`completed-task log`](../modules/completed_tasks_log.md) and Git history, not
as checked checkpoint narrative here.

## Current validation gates

- [ ] Cross-validate each current exact player-save mapping against fresh UI
  inventory from the same version, and add a new exact candidate whenever the
  game reports a different identity. Execute the complete
  [versioned audit matrix and rollout sequence](../architecture/player_save.md#versioned-audit-matrix-data-9-game-1073--revision-4).
  Promote only fully cross-validated fields; retain scheduled audits and every
  existing UI checker as the fallback for incompatible versions, shape
  changes, stale data, mismatches, and unmapped settings.
  - [ ] At the next naturally occurring unknown Home or terminal mapping
    candidate, validate its receipt path and integrate an accepted value into
    every required canonical owner and exact structural mirror. The only
    retained Module receipt was confirmed at attachment, integrated in
    `dbe3d34`, and accepted by later fresh decodes; there is no unresolved
    current receipt. Do not create or end a battle for this evidence.
  - [ ] At the next ordinary operator-authorized Start from Home, confirm the
    deployed `f01e050` per-check dispositions accept the matching Farm save and
    omit Modules, Damage Slider, Orb Distance, Target Priority, Auto Pick, and
    the other supported duplicate UI routes. The deployment smoke already
    proved a fresh Home `NEW_BATTLE` boundary under acknowledged Pause and sent
    no input; do not override Pause or manufacture a battle solely for this
    confirmation.
  - [ ] At the next ordinary operator-authorized Start from Home, confirm the
    deployed `59c86a5` same-family Module authority omits duplicate Modules UI.
    When a naturally occurring empty slot is available, confirm its explicit
    nil normalizes to `empty` without navigation; do not empty/rearrange Modules
    or start a battle solely for this observation. Unknown IDs/names,
    family/structure failures, duplicate installed names, ambiguous visual
    absence, and mismatches must retain the complete UI route.
  - [ ] Live-confirm deployed production commit `95bd630` for the exact Assist
    assignment-member repair in
    [ISSUE-2026-048](../issues/open-2026.md#assist-module-assignments-always-fell-back-to-modules-ui).
    One fresh ordinary Home or terminal boundary must publish all eight Module
    assignments from the save and omit Modules UI. Do not change or empty a
    Module solely to manufacture the confirmation.
  - [ ] Live-confirm deployed production commit `95bd630` for the Tournament
    mapping-observation callback repair in
    [ISSUE-2026-049](../issues/open-2026.md#tournament-session-preflight-rejected-its-mapping-observation-callback).
    The next ordinary attached or owned-validation Tournament preflight must
    complete without an unexpected-keyword failure and retain its read-only
    mapping observations. Do not start or attach a battle solely for this
    confirmation.
  - [ ] Add a guarded operator revoke/reacquire workflow for a locally
    confirmed Module identity. Acquire Pause or otherwise stop new-battle input,
    revoke with generation/document-fingerprint compare-and-swap, close mapping
    observation windows, invalidate only affected Module evidence, force a
    fresh decode, and resume only after the new effective mapping fingerprint
    is reconciled. The store-level append-only revoke/CAS/capacity contract is
    implemented; no current UI grants this authority.
  - [ ] Independently validate the version-1101 Tournament generator before
    enabling it, and calibrate the two new per-wave enemy counters only if they
    gain a consumer. Retain scheduled UI audits; any semantic discrepancy in an
    inherited check removes it from the compatibility allowlist immediately.
  - [ ] Complete the remaining natural-boundary validation of the
    [typed acquisition and temporal-authority contract](../architecture/player_save.md#acquisition-provenance-and-temporal-authority).
    The deployed shared typed interface is authoritative; do not reconstruct
    its superseded prototype.
    - [ ] Confirm one replacement-process attachment whose forced save also
      contains authoritative Free Upgrade-lock evidence opens none of the
      configuration UI. Regression coverage now requires the non-authoritative
      attachment placeholder to yield to a complete exact-bound lock fact while
      genuine absence remains deferred; retain this item for one natural
      post-deployment confirmation. A retained 2026-08-13 replacement
      attachment already used the bound save for Cards, Workshop, Bots,
      Guardians, Modules, Perk configuration, Target Priority, Auto Pick, and
      Ultimate Weapons without opening those screens, but correctly deferred
      Free Upgrade locks because that attachment had no authoritative lock fact.
      Separately confirm authoritative Module, Free Upgrade-lock, and Perk Auto
      Pick-order mismatches are each logged once and remain report-only without
      configuration UI or Home-repair authority. An intentionally unparseable
      Module fact must use Modules UI and apply the same report-only result when
      that fallback is fully observed.
    - [ ] Confirm a natural Tournament Results → Home transition reuses its
      valid terminal handoff without reacquisition. The retained 2026-08-08
      Tournament Results interval stayed Paused and moved to Tournament Screen,
      so it did not exercise this path.
  - [ ] On or after 2026-09-04, review whether the default-disabled,
    campaign-only `V1073-RUNTIME-013` temporal auditor still justifies its App
    hooks, manifest/schema, state machine, tests, and procedure. Count concrete
    investigations where its cross-boundary receipts were uniquely useful
    beyond normal consumers, `comparison_audit`, and targeted mapping
    calibration. Decide whether to retain it for named campaigns, simplify it,
    or remove it. Do not enable it merely for this review. If retained, decide
    whether the unconfirmed direct-Retry rollover or `V1073-RUNTIME-015`/`016`
    warrants a bounded campaign.
  - [ ] Extend the semantic runtime capability registry with independently failing
    active-upgrade, survival-ability, and remaining allowlisted live-tally
    claims. The capability whose authority originates at version 1101 already
    publishes the 29 validated economy, progress, and coin-source counters
    documented in the
    [versioned audit matrix](../architecture/player_save.md#versioned-audit-addition-data-9-game-1101);
    exact 1073 remains unavailable, while unknown additive forward revisions
    may inherit only those declared claims. For upgrades,
    map all three current-level arrays to their Workshop baselines and versioned
    caps so a gold-box claim is explicit rather than inferred from magnitude.
    Damage, resource, upgrade, survival, and other unvalidated candidates remain
    unavailable. Prioritize only values that replace an existing
    OCR/navigation route or reconcile a terminal record, and keep the arbitrary
    decoded root private.
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
    routes, including game speed, buy quantities, Card activity, and
    UW/Bot/Guardian cooldowns. Rank them by navigation/OCR cost and staleness
    tolerance, then validate and adopt one independent claim at a time.
    Enforcement, mutation confirmation, and transition authority remain
    visual.
  - [ ] Add any future normal-runtime consumer only through the shared typed
    acquisition owner and only after its own matrix evidence is complete. The
    campaign-only `V1073-RUNTIME-013` auditor may consume a passive bundle when
    explicitly enabled but is not an acquisition service or an authority
    source. Every consumer
    must preserve current-process terminal binding and its own temporal,
    fallback, and action-authority rules.
  - [ ] Use natural UI fallbacks and explicit/periodic `comparison_audit` runs
    for future bounded normalized candidates. Candidates never self-promote; mapping
    promotion remains a reviewed code/documentation change.
    - [ ] Extend Damage Slider and Orb Distance only when a natural UI fallback
      supplies one unique exact normalized value/tuple and the reviewed mapping
      workflow accepts it. Current Farm/Tournament values are save-backed;
      incompatible versions or shapes and unknown/unsupported values retain the
      complete UI route. Do not infer neighboring slider values, an Orb formula,
      or a tolerance from the calibrated points.
    - [ ] Before supporting mixed Ultimate Weapon primaries, validate one
      weapon index's off/on polarity at a time with normalized
      before/change/restore evidence. Validate Spotlight Missiles off through
      one explicitly authorized reversible transition. Do not enumerate every
      boolean combination.
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
  A second first-attempt timeout followed by a successful complete retry was
  recorded on 2026-08-12, again with a dynamically relocated source, but it
  still retained no failing frame or per-poll confidences. Instrument the next
  attempt to retain the final frame and individual detail/off/on match
  confidences, then distinguish a missed detail-open tap from unsettled
  Workshop scroll geometry or a detail-template miss before changing retry or
  stabilization behavior.
- [ ] Confirm normal gate re-arming after explicit **Stop Automation**, **Start
  Automation**, and a separate matching battle intent at the next authoritative
  boundary under a Strategy that declares gates; No Strategy itself has none.
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
  authoritative selected-Perk evidence. Keep `YamlStrategy` and its executor
  strategy-agnostic; configuration and the generated plan own the behavior.
  - Preserve the existing static `value` as the initial target. Optional
    ordered stages declare an ID, `enabled`, `perks_all` and/or
    `perks_any`, and a normalized target. Only positive selected-Perk evidence
    may trigger a stage; the last matching enabled stage wins.
  - Support `enforce`, `observe`, and `disabled` transition modes.
    `observe` records the target without input, while `disabled` retains the
    initial value without transition observation. Reject duplicate IDs, empty
    conditions, invalid modes or booleans, malformed perk lists, and invalid
    percentages through the existing normalizer.
  - Treat the Tier 19 `1E-18%` → `enemy_health_tradeoff` → `1E-19%`
    schedule as a proposed experiment, not approved configuration. Two
    2026-07-31 runs showed that the perk-gated target did not by itself prevent
    an early death; choose the next hypothesis before enabling it. If only the
    safer initial cap changes, change configuration rather than adding
    Tier-specific code.
  - `PerkTimelineObserver` owns canonical selected-family evidence. Evaluate
    only after initialization and preflight, reset at each authoritative battle
    boundary, and on restart obtain an authoritative baseline and jump directly
    to the current effective stage. Incomplete evidence takes no action, and
    Pause or any action-authority block still prevents input.
  - Reuse `damage_slider_configure` with independent run-scoped completion and
    result variables. Already-matching targets complete idempotently; failed
    enforcement retries on a bounded cooldown and preserves the normal
    `ACTION`/`RESULT` and per-input logging contract.
  - Persist the declared schedule and each observed or applied transition,
    including stage, trigger families and waves when known, old/intended/final
    values, mode, attempts, success, and reason. A mid-battle attach must
    distinguish reconstructed state from an observed transition.
  - Cover normalization, legacy static profiles, stage selection, restart,
    reset, Pause/authority, idempotence, retry, observation mode, logs, records,
    and unchanged Tier 18/static plans. Use retained fixtures for development;
    live validation waits for a natural safe run and never Surrenders a battle
    to manufacture the transition.

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
- [ ] Live-confirm deployed production commit `95bd630` for atomic Start Battle
  Strategy selection from
  [ISSUE-2026-047](../issues/open-2026.md#start-battle-replaced-a-newer-no-strategy-selection-with-stale-tournament-state).
  At an explicitly authorized natural Home boundary, select No Strategy and
  immediately Start. Confirm the workflow, activity entry, and acknowledgement
  retain `none`, with no Tournament setup or exclusive-validation receipt.
- [ ] Checkpoint and promote current-main cross-owner hardening candidate
  `f3cf804`, then live-confirm the resulting production commit for the
  exclusive-validation typed-authority repair recorded in
  [ISSUE-2026-046](../issues/open-2026.md#exclusive-validation-denied-its-own-strategy-and-cleanup-input)
  and the same-family later-battle release in
  [ISSUE-2026-001](../issues/open-2026.md#owned-validation-cleanup-survived-a-later-running-battle-transition).
  Use only a newly and explicitly authorized ordinary validation battle at a
  natural safe boundary. Confirm each declared phase,
  exactly owned Surrender when required, verified Home cleanup and authority
  release, plus Pause or operator-workflow interruption before the next input.
  Do not manufacture the historical later-`RUNNING` transition; if it recurs
  naturally, confirm the old receipt fails and releases without any recovery
  input before the successor is adopted.
- [ ] Confirm the deployed `STOPPED` interruption at a natural Home setup
  boundary: Pause, Stop, or Take Manual Control must yield before another
  device input, and only a later same-owner Enable may attempt one bounded
  restoration. Track the remaining confirmation in the
  [open issue dossier](../issues/open-2026.md#stopped-control-could-not-interrupt-an-in-progress-home-setup-guard).
- [ ] Detect likely manual player activity and automatically yield tap authority.
  - Treat unexpected Go Home/manual navigation during an active run as operator
    activity rather than an error to undo immediately.
  - Pause while screens continue changing or recent external input is evident.
  - After a configurable static grace period, warn before offering or performing
    a guarded return to the running battle.
  - Make the grace period interruptible and extendable through CLI/GUI controls.

- [ ] Complete the remaining Better Control acceptance against the current
  [control-surface architecture](../architecture/control_surface.md) and
  [Windows lifecycle checklist](../../windows/TheTower.ControlSurface/README.md#windows-only-lifecycle-validation).
  Implementation and promotion history are retained in the
  [completed-task log](../modules/completed_tasks_log.md).
  - On Windows, exercise the separate process lifecycle, action authority,
    observed battle state, Strategy scope, terminal policy, Start/Attach, and
    Take/Return Control states. Requests must show exact pending,
    acknowledged, rejected, unavailable, and no-op outcomes without deriving
    authority from GUI state.
  - Retained 2026-08-13 evidence closes the basic Home New save-backed Return
    path. Manual-control workflow `42e7985ea9f3414ca23017d059dfbe40`
    acknowledged Take Control during an active battle, accepted its natural
    terminal save and zero-read History handoff, then completed Return from
    Home New with all 14 mapped checks resolved and no unresolved or UI-
    required check. The completed ledger records
    `home_save_reconciliation_complete`; this did not exercise the original
    mismatch or any Return-specific UI fallback.
  - Retained 2026-08-08 Home evidence separately proves a partial save fallback:
    an unsupported Module value selected `ui_required`, ran the complete
    Modules UI repair, recorded `ui_verified_repair`, and completed Home
    configuration. This ordinary Home receipt does not prove that Return can
    complete from a wholly unusable save.
  - At natural active/resumable, Return-specific unusable-save Home or Game
    Over, and Tournament Results boundaries, confirm the remaining save/UI
    reconciliation paths. Observe the original Return Control mismatch or
    degraded Game Over/Home-first repair without manufacturing a boundary.
  - Run the revision-37 long-output and rotation checks for every indicator,
    plus authoritative current/pending/startup Strategy rendering and
    dirty/retry/same-ID/stopped/active-adoption behavior. Track the known
    symptom in
    [ISSUE-2026-038](../issues/open-2026.md#long-action-log-retention-made-current-controls-appear-pending).

- [ ] Finish the remaining native Windows GUI control-surface work described in
  [`../architecture/control_surface.md`](../architecture/control_surface.md).
  - [ ] Attribute and reduce Windows control-surface CPU use for passive
    performance collection. Retained 2026-07-30/31 aggregates measured
    approximately `0.83%` process CPU clean and `1.9%` under contention,
    exceeding the `<0.5%` non-frame telemetry target. Profile sampler work
    separately from UI and other process activity, retain complete evidence
    cadence, and validate both clean and contended cases. Include the dedicated
    threshold-triggered process-attribution scan-duration metric, confirm the
    dormant path adds no second process enumeration, and distinguish scan cost
    from the already measured total client CPU. Track evidence in
    [`ISSUE-2026-003`](../issues/open-2026.md#windows-performance-telemetry-exceeded-its-client-cpu-budget).
  - [ ] Validate and calibrate the default-off BlueStacks degradation recovery
    on Windows. Verify the installed `HD-Player.exe`, instance name from a
    BlueStacks-created shortcut, exact `bluestacks.conf` instance/port mapping,
    and listener owner before using it. Confirm revision-41 Diagnostics shows
    current handles/threads and exact-PID low-water/recent/ratio/delta/window
    evidence, and that the same BlueStacks lifetime continues across a GUI-only
    restart. First exercise the confirmed **Restart BlueStacks…** command with
    automatic recovery disabled; then retain one detector decision. For both,
    confirm the durable pre-hold target, target-edit lock, client-close
    reconciliation, sampler-baseline reset, and end-to-end Welcome Back
    catch-up. Exercise the End run/New Battle branch only at an explicitly
    authorized boundary.
    Compare false-positive/false-negative behavior with exact run/configuration
    and exact-listener-lifetime cross-run host telemetry before changing the trigger
    thresholds. This completes mitigation validation but does not by itself
    resolve the cause tracked in
    [`ISSUE-2026-002`](../issues/open-2026.md#t19-farm-retained-near-normal-game-clock-speed-while-entity-throughput-collapsed).
    The first revision-41 operator attempt identified the installed `Pie64`
    mapping but could not bind its listener because module-level process
    inspection returned Access Denied even from an elevated client. Commit
    `b087989` replaced module enumeration with limited-information native
    path/start-time reads. The repeated operator test then bound and replaced
    the exact listener, reconnected ADB, handled Welcome Back/Resume, held the
    replay through the old high-water, and released normally. It also
    exposed one Linux gap: BlueStacks Home was `1920x1080`, so the portrait
    capture guard prevented the package launcher until the operator opened only
    The Tower. Commit `7ce123c` now routes that typed exact-target landscape
    boundary solely to the bounded package launch. The next operator restart
    confirmed hands-free package launch and Welcome Back Resume, then observed
    a fifty-wave rollback despite Intro Sprint not being confirmed. The runtime
    now keeps that replay out of progression accounting while allowing its
    independently guarded in-battle gem, daily-gem, and reward collectors.
    GUI-close reconciliation, sampler-baseline/session continuity, one detector
    decision, and the explicitly authorized fallback branch remain outstanding.
    The retained 2026-08-10/11 six-battle x2 cohort is a positive severe-
    contention calibration window: `ds` occupied every host sample, host load
    was sharply elevated, and x2 did not guarantee survival. It does not meet
    the original near-normal-speed throughput trigger and has no uncontended
    x2 control. Retain one same-configuration clean x2 control before changing
    speed policy or treating this cohort as detector-threshold proof.
  - [ ] Confirm that a second launch from the SMB publish path reaches the
    single-instance guard without showing a host/runtime prompt or creating a
    second client, as tracked in
    [`ISSUE-2026-013`](../issues/open-2026.md#a-second-native-client-launch-produced-a-misleading-runtime-prompt).

### Agreed operator-control sequence

The operator selected the native dashboard redesign in item 9 as the current
implementation target on 2026-08-08. The remaining order is provisional and
can change as operator use supplies better evidence.

1. [ ] Verify the repaired Battle History filter input on Windows: the window
   opens without terminating the app, one click opens all three combo boxes,
   each popup remains usable across independent refreshes, and mouse and
   keyboard selection work normally. Track confirmation in
   [`ISSUE-2026-014`](../issues/open-2026.md#battle-history-filter-dropdowns-required-repeated-clicks).
2. [ ] Publish a structured atomic runtime-status snapshot and revise the
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
3. [ ] Extend concise human-readable requirement results to in-battle
   session-preflight and recovery checks, including expected state, observed
   state, and final disposition such as passed, failed, waived, or fallback.
4. [ ] Publish and display Peak Coins/min for the active and completed run.
   - Maintain the active peak within an authoritative run boundary and expose
     it through the runtime snapshot; after a mid-battle attach, label a peak
     that cannot be recovered as `since attach`.
   - Derive the completed peak from accepted `coin_rate_samples` and include it
     in the battle report. Do not label Coins/min multiplied by 60 as realized
     Coins/hour.
5. [ ] Generalize the guarded return-to-game timer to known recoverable panels,
   including Wave Stats (`WAVE_PANEL`).
   - Validate an authoritative close control and post-action return to the
     running battle for each supported panel instead of assuming the existing
     Return to Game strip is present.
   - Run timers only while control is `RUNNING`; Pause must block action. Log
     timer start, cancellation, expiry, target, and outcome, and expose the
     countdown or Pause block in the runtime snapshot and GUI.
   - Add return-now, extend, and cancel only as explicit runtime directives
     with freshness and ownership checks.
6. [ ] Verify boundary-aware Strategy changes on Windows. During an active
   process, one genuine dropdown selection must submit the existing normal
   next-boundary `set_strategy` request exactly once; polling and other
   programmatic selection changes must submit nothing. Accepted requests clear
   dirty state, while rejection or transport failure retains the visible dirty
   choice and exposes one retry affordance without enabling request storms.
   Selecting Current must replace a different pending Strategy, while an
   already-current selection with no pending request and an already-queued
   next-boundary selection remain no-ops. **Switch this battle** remains a
   separate explicit fresh-evidence active-adoption request. A stopped process
   still uses the visible selection on Start; saving a startup default without
   starting remains a separate explicit action. While the process is active,
   successful Strategy publication and restore-as-new must automatically follow
   with the same next-boundary request, including for a same-ID revision; when
   stopped, they update only the visible Start selection. Base publication does
   not send a process request. Confirm
   immediate sending/accepted/queued/failed feedback, current/pending display,
   dirty/failed selection retention across polling, stale-server warning and
   explicit reload, active-battle adoption, and an acknowledged paused Workshop
   application without changing Pause. Track confirmation in
   [`ISSUE-2026-010`](../issues/open-2026.md#native-strategy-selection-did-not-report-acceptance-or-live-disposition)
   and
   [`ISSUE-2026-011`](../issues/open-2026.md#windows-client-could-not-identify-or-reload-a-stale-linux-control-service).
7. [ ] Rework Battle History filters after the input defect is fixed and their
   real behavior can be evaluated.
   - Prioritize useful distinctions such as type, Tier, strategy, outcome,
     quality, and date range. Retain wave range only if operator use justifies
     it, otherwise move it to an advanced view or remove it.
   - Make filter semantics clear when the client has loaded only a bounded
     newest-record page.
8. [ ] Define and implement report disposition for short, interrupted,
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
9. [ ] Complete Windows acceptance for the implemented native dashboard
   redesign. Current behavior and authority live in the
   [control-surface architecture](../architecture/control_surface.md);
   implementation and publication history live in the
   [completed-task log](../modules/completed_tasks_log.md).
   - Exercise keyboard, mouse, focus, access keys, and visual layout at the
     `1120x720` minimum, approximately `1300x1000` preferred size,
     `1500x940` default, and maximized, at 100% and 125% scaling. Overview
     must not structurally scroll at preferred/default/maximized sizes, and no
     primary value or action may collide, clip, or become ambiguous.
   - Verify that polling preserves focus, open popups, dirty edits, failed
     Strategy selections, and list/scroll position while Activity retains its
     independent refresh. Confirm the full-width Activity, Perks, and System
     surfaces and the compact Overview retain all current authority
     distinctions.
   - Re-run the outstanding Better Control, Strategy selection/publication,
     incompatibility-banner, blank-token loopback SSH, Setup placement, and
     History-input usability checks at natural safe boundaries. Include
     same-ID publication, stopped/active behavior, failure/retry, and the
     separately explicit active-adoption path.
   - Confirm the redesign adds no passive CPU regression. Defer drag-to-reorder,
     floating panes, and extensive per-card hiding until operator use
     demonstrates a concrete need.
