# Completed Tasks Log

This on-demand history records completed outcomes, commits, and validation. It
is not current policy, an active queue, or required startup reading; use the
canonical document linked by an entry for current behavior.

---

## 🧱 Refactor and Architecture

- Centralized all clickmap access through `get_clickmap()` in `core/clickmap_access.py`
- Removed legacy file `input_named.py` after migrating all usage to `clickmap_access`
- Migrated `clickmap.json` to `config/` and updated all references across tools and tests
- Renamed all clickmap variables to `clickmap` for consistency
- Removed `coords/` folder and redistributed:
  - `gesture_logger.py`, `tune_gesture.py` → `tools/`
  - `clickmap.json` → `config/`
  - `run_tune_gesture` → deleted (manual launch note)
- Refactored tools/crop_region.py and main.py to use get_and_save_screenshot from ss_capture (centralizing save logic)
- Updated tools/crop_region.py to correctly handle gesture logging (single click / swipe, then redraw window).  Also implemented scrolling within the crop window
- Centralized clickmap-backed matching in `core.matcher` around a structured
  `MatchResult`, cached template loading, and shared region resolution while
  preserving the detector and label compatibility profiles at their public APIs.
- Added a persisted Daily Gem scheduler keyed to UTC midnight, which is 17:00
  PDT and 16:00 PST. It invokes the existing Store handler without requiring a
  badge, defers until a safe Home/Running state, backs off failures, and records
  completed or confirmed-not-ready outcomes once per game day.
- Made the control file the sole authority for runtime pause state. Manual
  pauses are now indefinite and survive restarts until an explicit resume;
  removing the duplicate in-memory 15-minute expiry eliminates the window in
  which automation could resume while the persisted directive still said
  `PAUSED`.


---

## 🧪 Testing & Validation

- Verified no external references to `input_named.py`
- Confirmed no remaining hardcoded `coords/` paths after migration

### 2026-08-08 passive save-backed Perk timeline correction

- Commit `39f4a4d` removes normal in-battle Perks-panel timeline navigation.
  Stable top-bar transitions now request the existing shared passive save
  scheduler, while forced attachment evidence seeds the same monitor-owned
  exact prefix without reacquisition. App transfers detached worker evidence
  to the persisted timeline only on the serialized main thread and only while
  process, activity scope, target generation, and active-round identity still
  match.
- Each accepted timeline row retains exact oldest-first saved sequence, pick
  wave, semantic ID/key, and level-after. A later acquisition failure cannot
  erase that positive prefix, and a pending boundary never becomes negative
  evidence. Normal save-backed rows do not masquerade as UI calibration
  batches or open Perks to resolve an unknown ID.
- Game Over now has three Perk evidence routes. Proven post-exhaustion finality
  uses the exact saved inventory with no Perks navigation. A bound nonfinal
  prefix inspects only the newest terminal viewport and merges tail rows when
  saved recency and passive boundaries make that safe; repeated levels, counts,
  order, and missing markers remain explicit uncertainty. A missing, unbound,
  malformed, or round-conflicted prefix retains the complete terminal Perks
  traversal. Optional data failure remains subordinate to Wait/Retry/Home.
- The affected attachment, passive-scheduler, monitor, timeline, terminal, and
  battle-record slice passed 507 tests. The supported checkpoint passed
  compilation, state definitions, clickmap integrity with zero errors and the
  established 44 orphan candidates, and all 1,979 tests in 351.18 seconds. No
  live process, device, integration, promotion, or deployment action was
  performed; ordinary-boundary production confirmation remains in the runtime
  backlog.
- The combined candidate then merged deployed idle-Home gem authority through
  production tip `f809815` without a source-code conflict. The merged Perk,
  Home-gem, authority, and control-surface owner/caller slice passed 413 tests;
  the complete supported checkpoint passed compilation, state definitions,
  clickmap integrity with zero errors and the established 44 orphan candidates,
  and all 2,022 tests in 338.73 seconds. This integration performed no live,
  device, promotion, or deployment action.
- Exact committed `develop` candidate `7d2552b` repeated the complete supported
  checkpoint before promotion: compilation, state definitions, clickmap
  integrity with zero errors and the established 44 orphan candidates, and all
  2,022 tests passed in 339.32 seconds.
- Production advanced from `f809815` behind rollback tag
  `production-before-20260809T004403Z-f809815`. The control surface restarted
  as PID `3372261`; replacement automation PID `3372605` acquired the held
  `localhost:5555` lock, acknowledged the exact restored Enable request, and
  published fresh Home/New Battle evidence while terminal policy remained
  Home. No Start Battle, Attach to Battle, battle transition, or device input
  was issued during the smoke. A natural ordinary battle and Game Over remain
  required to confirm the deployed passive checkpoint and terminal-tail routes.

### 2026-08-08 idle-Home gem authority

- Commits `61a150f` and `58380f4` close the Better Control implementation gap
  that stranded a visible five-gem reward while Automation was Enabled at Home
  awaiting an explicit Start/Attach choice. The initial-intent hold now declares
  only the typed `home_ad_gem` auxiliary allowance when no immediate battle
  workflow exists. Every other collector plus Strategy, setup, navigation, and
  lifecycle work remains blocked.
- Global Pause, Stop, Take/Return Control, Setup Capture, interactive
  development, an active Start/Attach workflow, or any second hold overrides
  the exception. A fresh Home frame only schedules the attempt; the handler
  obtains a new visible-button match, synchronizes current control and operator
  ownership, and rechecks central typed authority at the final safe-tap
  boundary. A newly arrived Start/Attach request therefore cancels the claim.
  Runtime status exposes the hold-local and effective collector allowlists
  under the existing `better_control_model_v2` capability.
- The final focused authority/Home/control-model suites passed 234 tests and the
  broader owner/caller slice passed 515. Exact corrected candidate `58380f4`
  passed compilation, state definitions, clickmap integrity with zero errors
  and the established 44 orphan candidates, and all 2,011 tests in 338.55
  seconds. Exact promotion candidate `066e167` repeated that complete
  checkpoint with all 2,011 tests passing in 343.11 seconds.
- Production advanced from `36497dc` behind rollback tag
  `production-before-20260809T000743Z-36497dc`. Automation PID `3244540`
  stopped cleanly and released its `localhost:5555` lock; replacement PID
  `3334597` started Paused, acquired the lock, published fresh Home/New Battle
  evidence, and acknowledged the exact Pause and restored Enable requests.
  Runtime status exposed `home_ad_gem` as the sole allowed auxiliary collector
  while Strategy and lifecycle action authority remained blocked.
- The replacement naturally claimed the already-visible five-gem Home reward,
  recorded the verified tap and successful result, observed the overlay
  disappear, and stayed Home without a Start Battle intent or battle
  transition. No Surrender or manufactured test boundary was used. No Windows
  code changed; native Windows usability remains pending.

### 2026-08-08 recoverable runtime diagnostic reconciliation

- Commit `101054f` selectively ports the two independent logging refinements
  from stale mixed commit `e0fff88`. Permanent `strategy_profile` / `every_run`
  skips now render as expected `INFO` policy with their actual reason, while
  operator-selected one-run waivers retain `WARN` visibility. None of the
  superseded policy-derived ordinary Home auto-launch code was merged.
- A first incomplete raw or PNG screenshot remains rejected and retried, but
  that in-budget event and a successful second capture now remain `DEBUG`
  diagnostics. Two consecutive incomplete frames still reject the capture and
  emit the operator-facing `WARN`; screenshot action authority is unchanged.
  The August 8 recurrence analysis is retained under `ISSUE-2026-022`, whose
  compositor/transport source remains unresolved.
- The focused owner suites passed 96 tests and the broader owner/caller slice
  passed 148. The exact candidate passed compilation, state definitions,
  clickmap integrity with zero errors and the established 44 orphan
  candidates, and all 2,003 tests in 338.67 seconds.
- Production was fast-forwarded to `101054f` behind rollback tag
  `production-before-20260808T230345Z-c9f60e2`. Automation stopped cleanly at
  Home without input; replacement PID `3244540` acquired the held
  `localhost:5555` lock, acknowledged the exact restored Enable request, and
  published fresh Home/New Battle evidence. No Start Battle intent, battle
  transition, incomplete-frame event, profile preflight, or device input was
  manufactured for the smoke test.
- The smoke also confirmed the then-open Better Control gap in which
  `operator_workflow` excluded auxiliary gem collectors while idle Home awaited
  explicit Start/Attach. That diagnostic-only deployment did not change the
  decision; the later `61a150f`/`58380f4` outcome above closes it.

### 2026-08-08 Home launch-authority correction

- Commit `b36f878` prevents a stale No Strategy inventory pass from reclaiming
  an operator-opened Home panel. A route now requires fresh `RUNNING`, the
  active No Strategy observation, and lifecycle-owned evidence for that exact
  battle; Paused or later-observed Cards, Perks, Modules, Event, Guild, Target
  Priority, and Damage Adjuster panels grant no cleanup or navigation input.
- Commit `fad29e3` separates future terminal policy from managed Home launch
  authority. Automation Enabled, Strategy selection, prior lifecycle state,
  or selecting Continue at Home no longer runs setup, repairs navigation, or
  taps Battle/Resume. Explicit Start still runs normal new-run gates, and an
  ordinary Game Over under Continue still owns its direct Retry control.
- A terminal workflow that must return Home can carry one process-local,
  one-shot continuation only when Continue was already selected for that exact
  terminal boundary. The claim binds runtime/PID, target generation, activity
  scope, and state/policy request identities; it accepts only fresh New Battle
  and is invalidated by authority/policy changes, manual or workflow
  supersession, Resume Battle, binding changes, process replacement, or
  unexpected manual activity.
- Promotion-review commit `a9b7269` adds the final temporal barrier omitted by
  the first checkpoint. Save/setup completion, pre-handler dispatch, and the
  final verified tap all revalidate the original Start, Attach, or Return
  request identity and typed lifecycle authority. Replacement requests cannot
  inherit the prior setup or carried-save launch; manual/control supersession
  also clears a pending terminal continuation. The revised affected suite
  passed 478 tests.
- Before that promotion review, the affected Home/control/No Strategy/Game
  Over/Tournament/repair slice passed 461 tests. Its complete supported
  checkpoint passed compilation, state definitions, clickmap integrity with
  zero errors and the established 44 orphan candidates, and all 1,985 tests in
  459.15 seconds.
- Exact post-review candidate `9d7a541` then passed compilation, state
  definitions, clickmap integrity with zero errors and the established 44
  orphan candidates, and all 2,002 tests in 337.55 seconds. It was
  fast-forwarded through `develop` and production `main`; rollback tag
  `production-before-20260808T222617Z-e53c156` preserves the prior production
  boundary.
- The bounded production smoke replaced the control surface as PID `3203188`
  and automation as PID `3203767`. Revision 30 reported connected exact target
  `localhost:5555`; the replacement held its target lock, acknowledged the
  exact Enable request, and continued publishing fresh Home/New Battle
  observations. No Start Battle intent or battle input was issued, and the
  runtime explicitly logged that it was staying Home. Native Windows usability,
  a live policy-change-at-Home exercise, and a natural terminal-to-Home
  continuation remain in the Better Control backlog rather than being
  manufactured during deployment.

### 2026-08-08 attachment preflight round-invariant reuse

- Running-battle Strategy preflight now consumes all four facts already
  classified and bound as round-invariant by the guarded attachment save:
  Workshop preset, selected Bot preset, equipped Guardians, and equipped
  Modules. Exact configuration matches and complete eligible Module
  observations omit only their redundant UI sections.
- Cards, Auto Pick, Ultimate Weapons, Damage Slider, and Orb Distance retain
  their established UI authority. Missing or mismatched Bot, Guardian, or
  enforced Module facts retain the existing UI fallback; unresolved Workshop
  remains explicitly deferred without a Home route. Observation-only Module
  variations remain reported and never become repair authority.
- Focused navigation, Tournament, typed temporal, action-executor, and player-
  save reconciliation coverage passed 199 tests. The supported development
  checkpoint passed compilation, state definitions, clickmap integrity with
  zero errors and the established 44 orphan candidates, and all 1,971 tests in
  436.21 seconds. No live/device action was performed.
- Final integration commit `b5c7bd8` merges the feature with production tip
  `4ad383e`, including the deployed passive Perk timeline and idle-Home gem
  authority. The expanded owner/caller regression passed 736 tests. The exact
  integration candidate then passed compilation, state definitions, clickmap
  integrity with zero errors and the established 44 orphan candidates, and all
  2,025 tests in 339.26 seconds.
- Production advanced from `4ad383e` behind rollback tag
  `production-before-20260809T005925Z-4ad383e`. Automation PID `3372605`
  stopped cleanly at Home and released its `localhost:5555` lock. Replacement
  PID `3391293` acquired that exact lock, acknowledged its startup Pause and
  the exact restored Enable request, and published fresh Home/New Battle
  evidence while terminal policy remained Home.
- The bounded smoke issued no Start Battle, Attach to Battle, battle
  transition, or device input. It did not manufacture an active attachment to
  exercise the new selective preflight route; that behavior remains supported
  by the complete retained regression checkpoint.

### 2026-08-08 explicit preset local-copy authoring

- Commit `09515e2` adds revision-31 capability
  `strategy_authoring_preset_local_copy_v1` and the catalog-bound
  `materialize_loadout_preset` operation. Linux compares the exact displayed
  catalog fingerprint and reuses ordinary definition-snapshot resolution and
  normalization for Modules, Target Priority, and Orb Distance; the operation
  never writes, publishes, selects, activates, queues, or applies a Strategy or
  preset.
- The native authoring rows now expose **Edit a copy...** only for editable
  active preset selections. A meaningful dormant local draft receives explicit
  replace, retain, and cancel choices; response identity/shape validation is
  atomic, so stale catalogs, unknown presets, invalid normalization, missing
  capability, interruptions, and errors preserve both forms. Bundled read-only
  Strategies remain non-editable, while clones and custom Strategies use the
  same local-copy path.
- Module **Create variant...** is now honestly labelled **Duplicate preset...**
  and remains an immutable exact-copy preset operation. Module-only **Save as
  preset...** still submits the current edited local definition. Target
  Priority and Orb Distance gained no managed custom-preset catalogs.
- The focused authoring slice passed 125 Python tests and all 96 portable native
  authoring/compatibility tests. The complete supported checkpoint passed
  compilation, state-definition validation, clickmap integrity with zero
  errors and the established 44 orphan candidates, and all 1,973 tests in
  492.74 seconds.
- Linux lacks the `Microsoft.NET.Sdk.WindowsDesktop` targets needed to compile
  the complete WPF application; that expected host limitation does not affect
  the portable native suite. The expanded disposable-catalog Windows build and
  usability smoke remains pending. No live process or device interaction was
  performed.
- Final integration commit `4d480bf` merges the feature with production tip
  `ee4c861`, preserving both completion histories. The exact integrated
  candidate passed compilation, state-definition validation, clickmap
  integrity across 291 entries and 220 referenced templates with zero errors
  and the established 44 orphan candidates, all 2,030 Python tests in 343.27
  seconds, and all 96 portable native authoring tests.
- Production advanced from `ee4c861` behind rollback tag
  `production-before-20260809T013944Z-ee4c861`. Automation PID `3391293`
  stopped cleanly during the active battle and released its
  `localhost:5555` lock. The restarted control surface reported revision 31,
  `strategy_authoring_preset_local_copy_v1`, `preset_local_copy: true`, the
  `materialize_loadout_preset` operation, four module presets, and no catalog
  errors. The pending setup-capture review remained ready with its original
  preview fingerprint.
- Replacement automation PID `3453512` started Paused, acquired the exact
  target lock, and published fresh active-battle evidence. Explicit Attach
  used a guarded forced save, restored the source, and bound same-battle
  continuity; explicit active-battle adoption then restored
  `farm_t19_ad_assist`. Session preflight verified the active requirements,
  deferred only the normal `free_upgrade_locks` next-boundary check, returned
  through the resumable Home route, and entered steady Running state at wave
  2039 and game speed x6.3. No Surrender or new battle was issued.
- Follow-up guidance commit `3acb45a` makes a production promotion that changes
  any input to either native Windows executable incomplete until the supported
  workflow atomically publishes and verifies the complete two-executable
  package from the exact production checkout. Documentation-only and test-only
  changes do not activate that boundary.
- From `main == develop == 3acb45a`, `publish-linux.sh` replaced the stale
  package at `windows/TheTower.ControlSurface/publish/win-x64`. Publication
  completed at 2026-08-09 04:14:34 UTC with exactly the adjacent self-contained
  Windows x64 PE executables: `TheTower.ControlSurface.exe` is 72,358,911 bytes
  with SHA-256 `934c529b9e2772667e5d821ff847dce4b7e4ac439af20986aa331eed313c8e8d`,
  and `TheTower.TunnelHost.exe` is 35,172,086 bytes with SHA-256
  `aaf4c2de9b3b1b3ed41b3c136e29c238005e823580423201c412d79b523352ca`.
- Both projects compiled and the atomic publisher exited successfully. Restore
  also reported nonfatal `NU1900` diagnostics because the execution
  environment left NuGet's existing user HTTP cache read-only after confirming
  every project was already up to date. The compiled GUI payload contains
  `strategy_authoring_preset_local_copy_v1`, and all 96 portable native
  authoring/compatibility tests passed. Cross-publication does not execute WPF;
  the separate Windows-only lifecycle and visible usability smoke remain
  pending rather than being claimed here.

### 2026-08-08 save-to-UI fallback contract repair

- The combined candidate is based directly on completed Better Control tip
  `848c886`, so it includes that thread's unstaged-for-production repair without
  modifying its worktree or creating a competing implementation.
- Attach and Return now treat a missing, unsupported-revision, structurally
  incompatible, or unprojectable save as unusable data after safe source
  restoration. Attach binds guarded Battle History and keeps observation-only
  No Strategy monitoring Enabled; active Return runs Battle History and every
  active-Strategy UI check; Home New Return runs every supported Home check;
  and Game Over Return uses the full Game Stats/Perks/More Stats collector while
  preserving the selected terminal route and action authority.
- Typed reconciliation receipts bind exactly one process-local source: save
  acquisition provenance or runtime/target-generation/activity-scope UI
  provenance. They remain non-replayable. Source-restoration, process, owner,
  target, scope, and action-authority loss still block input, while a trusted
  mapped mismatch retains explicit review semantics.
- Setup capture remains the explicit save-only exception because the supported
  UI checkers cannot produce one coherent same-boundary authoring snapshot. Its
  unavailable result preserves ordinary UI monitoring and the documented
  authority outcome.
- The affected control/save/No Strategy/Home/terminal slice passed 622 tests.
  The complete supported checkpoint passed compilation, state definitions,
  clickmap integrity with zero errors and the established 44 orphan candidates,
  and all 1,968 tests in 340.15 seconds. No live/device action was performed by
  this checkpoint.
- Production was subsequently fast-forwarded through repair commit `d32b811`.
  The exact self-contained `win-x64` control-surface package rebuilt
  successfully, all 83 native tests passed, and rollback tag
  `production-before-20260808T162821Z-d6f6bb7` preserves the prior production
  boundary. The known read-only NuGet vulnerability-cache warning did not
  affect the package or test result.
- Shared-runtime deployment cleanly replaced both fixed services. Host-backed
  evidence showed control-surface PID `2710166` and automation PID `2710563`;
  revision-30 API evidence matched the automation service PID, reported the
  exact `localhost:5555` target connected, and acknowledged `RUNNING` with No
  Strategy and the safe auxiliary collectors available.
- The active-battle Attach smoke test used a compatible forced save mapped as
  `data-9-game-1101`, completed observation-only adoption, and then restored
  the operator's prior Enabled No Strategy monitoring state. It intentionally
  did not manufacture an incompatible save against the live battle, so the
  unusable-save UI branch is supported by the complete regression checkpoint
  rather than a forced production failure.
- Fresh production behavior then detected an ad gem at 09:33:37 PDT,
  dispatched its verified tap at 09:33:40, recorded collection at 09:33:44,
  observed the overlay removed at 09:33:53, and completed the bounded
  floating-gem scan at 09:33:57. No Surrender or test battle boundary was used.

### 2026-08-08 Better Control failure continuity

- Commit `e7dfb51` makes recoverable setup-capture, terminal-data,
  No Strategy post-run, Tournament dismissal, and configuration-repair
  failures preserve minimum automation continuity instead of manufacturing an
  indefinite Pause. A running-battle conflict uses the Strategy Gate so
  observation and explicitly safe gem collectors remain available; Game Over
  and Tournament terminal routes retry from fresh evidence with the selected
  policy and action authority unchanged.
- Control-surface revision 30 retains both version-1 capabilities and adds
  `save_backed_setup_capture_v2`. Capture receipts expose typed authority
  outcomes, terminal results reopen without hidden input, and a separate
  **Try capture again** action owns any new serialization. Source-restored
  mapping/projection/acquisition failures preserve prior authority; unproven
  source restoration and proved Home New contradictions still Pause safely.
- No Strategy now retains partial observations and explicit unresolved fields
  before releasing verified Home. An operator-authorized configuration-repair
  Surrender still remains exact and one-shot, but verified Home returns to
  normal repair and the independently selected terminal policy without an
  implicit Pause. Explicit Pause, Take Manual Control, Stop, and ambiguous
  input ownership remain zero-input boundaries.
- Merge commit `c10c9f8` incorporates production's typed attachment fix
  `e39a785` and deployment record `d6f6bb7`; this stage does not replace or
  duplicate the separately owned save-scope implementation.
- The affected Python suite passed 385 tests and all 83 native
  authoring/compatibility tests passed on Linux. The complete supported
  checkpoint passed compilation, state definitions, clickmap integrity with
  zero errors and the established 44 orphan candidates, and all 1,962 tests in
  340.57 seconds. No live/device or Windows runtime action was performed;
  native Windows usability and natural-boundary validation remain in the
  Better Control backlog.

### 2026-08-08 player-save revision compatibility deployment

- Commits `9ce79b9` and `b292779` add exact `data-9-game-1101` support for
  game `28.3.2`. A stable exact-target read retained all 739 version-1073
  fields and required array shapes and added only the unpublished integer
  counters `enemiesKilledThisWave` and `enemiesSpawnedThisWave`. Version 1073
  remains the semantic authority for 15 portable configuration checks and the
  runtime normalizer. Exact and unknown forward game versions are accepted
  only through the additive-root/required-array compatibility gate; each
  consumer still enforces its own type, ID, length, and shape contract.
  Structural drift, a new data version, or an incompatible field continues
  through the existing UI checker instead of stopping indefinitely. The
  version-derived Tournament generator remains exact-version and UI-required.
- The first bounded production smoke proved that mapping 1101 decoded and
  projected the live save, then exposed two attachment-handoff defects rather
  than a mapping failure. Fix-forward `543894b` preserves typed save evidence
  across an exact continuity-owned activity-scope rebind. Fix-forward
  `e39a785` permits that exact typed source-to-final transition through the
  ready, pre-adoption frame without weakening runtime, PID, ADB target,
  generation, battle-state, or unrelated-scope checks. It also removes the
  circular requirement that lifecycle adoption already be complete before
  the retained claim can authorize observation-only adoption.
- Exact candidate `e39a78552572a6ea71e281568e0f0d056ad6d2c7` passed the
  complete isolated checkpoint: compilation, state-definition validation,
  clickmap integrity with zero errors and the 44 established orphan
  candidates, and all 1,936 tests in 336.01 seconds. Production was stopped
  for each runtime-code boundary. The final promotion used annotated rollback
  tag `production-before-20260808T121511Z-543894b`; the two earlier bounded
  attempts retain rollback tags `production-before-20260808T114321Z-c6a2ff5`
  and `production-before-20260808T115858Z-b292779`.
- Replacement PID `2406590` acquired exact target `localhost:5555` under the
  selected `none` strategy. Workflow `b142e214ca6b424991478bdf0dba9f52`
  completed after a guarded forced serialization confirmed unchanged battle
  continuity with mapping `data-9-game-1101`, applied 11 save-backed No
  Strategy observations, and adopted the active battle. After explicit Enable,
  the UI fallback visited only unresolved `modules` and `damage_slider` fields.
  Auxiliary authority then collected the visible in-battle ad gem at 05:17:42
  PDT, the overlay disappeared, and the bounded floating-gem scan completed
  normally. Final fresh status was `RUNNING`, observation-only Strategy
  `none`, with no Pause, workflow hold, or stale authority. No Surrender,
  battle start, or terminal action was used.

### 2026-08-07 Home Perk repair resilience

- Commit `a5825db` replaces Ban Perks' single-frame, multi-action plan with a
  bounded one-action reconciliation loop over consecutive stable Selected
  snapshots. It removes extras before additions, enforces capacity, verifies
  semantic progress, and reuses the authoritative final readback.
- The same change adds the missing Chrono Field Duration Home semantic mapping
  that blocked Black Hole Duration, guarantees current save-mapped perk
  vocabulary coverage, keeps unknown-predecessor recovery local, and prevents
  an exhausted local Perk repair from replaying the complete Home setup.
- The initial 107 focused tests and complete isolated checkpoint passed; the
  latter covered compilation, state definitions, clickmap integrity with zero
  errors and the 44 established orphan candidates, and all 1,784 tests in
  325.58 seconds.
- Commit `6122503` was promoted under rollback tag
  `production-before-20260808T041513Z-7bb0b6e`. PID `1292147` stopped cleanly;
  replacement PID `1903959` acquired the exact `localhost:5555` owner/lock and
  produced a fresh Home observation. The no-waiver retry then proved the
  Black Hole/Chrono correction live before two ignored reverse swipes were
  mistaken for the Auto Pick list top during the later Coins Bonus repair. The
  coordinator Paused the resulting whole-Home retry, and PID `1903959`
  acknowledged the action-free hold.
- Fix-forward commit `f747515` makes a visible predecessor the required local
  scroll boundary, removes viewport-top authority from swap confirmation,
  restarts semantic plans at rank one, and types bounded exhaustion as
  non-retryable. All 110 focused tests and the complete isolated checkpoint
  passed, including all 1,787 tests in 336.81 seconds. The exact `develop`
  candidate at `e8d4add` repeated all 1,787 tests in 346.67 seconds, then was
  promoted under rollback tag
  `production-before-20260808T045617Z-6122503`. PID `1903959` stopped cleanly;
  replacement PID `1969498` acquired the exact `localhost:5555` lock and
  acknowledged the indefinite Pause. Its first fresh observation was
  `UNKNOWN/PAUSED`: a direct predeployment frame had unexpectedly shown the
  Android launcher, so no restoration, Resume, or other device input was sent.
  Production remained Paused pending a fresh operator-owned screen boundary;
  the deployment smoke passed, but live completion of the repair remained
  intentionally unclaimed.

### 2026-08-07 recoverable superseded feature retirement

- `c23fe67` splits feature retirement into integrated and explicitly
  superseded dispositions. Integrated tips retain the ancestry proof and safe
  `git branch -d` guard. A superseded local tip requires exact operator
  approval, inspected ownership and content, durable replacement history, and
  a verified annotated archive tag before its unlinked local branch may be
  force-deleted. Forced worktree removal, recursive deletion, remote deletion,
  protected refs, and deletion of the recovery tag remain outside that path.
- The documentation-only candidate passed compilation, state-definition
  validation, clickmap integrity with zero errors and the 44 established orphan
  candidates, and all 1,777 tests in 328.73 seconds. It was promoted without
  service or device action behind rollback tag
  `production-before-20260807T222100Z-e7df147`.
- The operator declared exact prototype tip
  `d1c3dece79f43ae044e1730110298779e30a1fb2` superseded by deployed shared-
  interface v2 tip `33a325b7b3792181b24f8d569135f5f12ac74c82` and approved its retirement.
  Its worktree had no tracked or nonignored changes; ignored content was limited
  to the development environment link, generated caches, empty scratch
  directories, and one zero-byte test lock. No matching remote branch existed.
- Annotated tag `archive/20260807-save-backed-perk-monitoring-d1c3dec` now
  preserves the exact prototype tip. Git removed the worktree without force,
  then deleted local branch `feature/save-backed-perk-monitoring`; `main`,
  `develop`, the deployed v2 branch/worktree, rollback tags, and every other
  feature worktree remained unchanged.

### 2026-08-07 operator-authorized bounded passive stream

- `735aa91` makes an explicit operator instruction sufficient authority for one
  task-bounded passive stream after live preflight. The exact target,
  no-control boundary, finite host and device lifetime, coexistence checks, and
  cleanup are canonical in the
  [passive-stream procedure](../operations/passive_stream.md). Routine and
  unattended capture remain production-owned, and the passive viewer grants no
  input, lease, navigation, or ADB connection-management authority.
- The policy separates transport evidence instead of treating the unsuccessful
  Android `screenrecord` experiment as proof against `scrcpy`. Retained action
  logs also show that headless scrcpy became current in three July 13 startup
  runs, including while frequent guarded production inputs continued.
- After fresh live preflight at 2026-08-07 14:20 PDT, one `scrcpy 3.3.1` viewer
  ran for 60 seconds against the exact production target with `--no-control`,
  `--no-audio`, 15 FPS, and a 2 Mbps video limit. Production published multiple
  newer complete 1080x1920 frames during and after the viewer; control-surface
  ADB evidence remained connected with zero failures and no warning, and no
  new capture or connection error appeared in the action log. The active battle
  continued without worker input or navigation.
- The operator directly observed the emulator-side FPS counter fall from its
  approximately 55–59 FPS baseline to 45 FPS during the stream. That counter is
  outside the captured Android framebuffer, so the production screenshots do
  not retain it; the direct observation establishes a real roughly 18–24%
  renderer impact rather than only expected 15-FPS viewer choppiness. The
  follow-up mitigation benchmark is retained in the
  [capture backlog](../backlog/state-and-detection.md#capture-and-action-architecture),
  and the procedure now starts with a lower-resolution profile and records
  viewer versus emulator impact separately. Optional x2 preparation caps a
  faster battle for general inspection, while x1 is reserved for close
  inspection; neither raises an already slower battle, and both remain
  separately authorized, restored control changes.
- Scrcpy reached its own time limit and exited successfully. The task-owned
  host process and device-side scrcpy server were both absent afterward, and a
  fresh complete production frame on the unchanged target confirmed cleanup.
  This validates bounded passive scrcpy coexistence without a capture or ADB
  failure for ordinary observation; it does not establish zero performance
  cost or close the separate app-owned low-latency frame/action-source backlog.

### 2026-08-07 player-save acquisition and freshness consolidation deployment

- The reviewed contract, acquisition foundation, boundary handoff, temporal-
  authority, and pure Perk-monitor commits remain a linear stack ending at
  `33a325b7b3792181b24f8d569135f5f12ac74c82`; frozen prototype
  `d1c3dece79f43ae044e1730110298779e30a1fb2` remains unchanged and unmerged.
  Merge commit `5e46e0594ba17953b85af3e274d763b9d7cddf77` preserves both prior
  `develop` commit `df184642181c51646f4ad4379aa6bc7ef772d92f` and the complete stack tip.
  The integrated source and canonical contracts were reviewed together, and
  the supported checkpoint compiled the repository,
  passed state-definition and clickmap validation with zero errors and the 44
  established orphan candidates, and passed all 1,777 tests in 327.69 seconds.
- Production commit `df184642181c51646f4ad4379aa6bc7ef772d92f` was proven
  ancestral to `5e46e0594ba17953b85af3e274d763b9d7cddf77`; every commit and the
  aggregate diff were reviewed, and unique annotated rollback tag
  `production-before-20260807T201303Z-df18464` was created at the former
  production commit. After the automation and control-surface services both
  stopped, production and `main` fast-forwarded to that exact integration
  commit at 2026-08-07 13:14 PDT. Replacement control-surface PID `1291663` and
  automation PID `1292147` were active, the exact `localhost:5555` runtime lock
  was held by PID `1292147`, ADB was freshly `device`, and the unpaused attached
  battle was `RUNNING` with no holds or Strategy Gate.
- Live preflight used fresh host-backed process, lock, target, and complete-
  screenshot evidence. The replacement first attached under an agent-owned
  Pause, where action authority remained blocked, and resumed only after the
  replacement boundary was proven. Activity Continuity failed closed on its
  first unverified restored-source attempt, then confirmed the unchanged
  completed tail on its bounded retry. The successful forced-attachment path
  emitted one shared save observation at revision `47927`, with a bound Tier
  17 round identity, a 37-pick initial complete Perk checkpoint, and guarded
  projections for 11 No Strategy fields. The collector identified its source
  as shared bundles; no second consumer acquisition or UI visit was used for
  those accepted fields. Its fresh mid-round session had no pre-round History
  baseline, so that independent audit comparison explicitly failed closed and
  made no terminal claim.
- The next unprompted passive interval completed at 2026-08-07 13:20 PDT. One
  scheduler acquisition emitted one `periodic_interval` audit receipt at save
  revision `47928`, preserved the exact same-round identity, and advanced the
  complete Perk prefix from 37 to 38 picks. The deployed fan-out passes that
  same immutable bundle through the Perk monitor and optional audit projector;
  no duplicate acquisition or consumer rejection was recorded.
- No natural Game Over, direct Retry, Tournament Results, or qualifying
  terminal-Perks boundary occurred during the deployment window. Their zero-
  reacquisition/zero-History and terminal-completeness claims remain explicit
  ordinary-boundary observations in the canonical
  [runtime and validation backlog](../backlog/runtime-and-validation.md); no
  battle was created, surrendered, accelerated, or otherwise manipulated for
  validation.

### 2026-08-07 replacement-state runbook clarification

- `b994f0a` clarifies
  [managed process replacement](../operations/process_control.md#process-replacement-and-terminal-recovery):
  an active-battle replacement uses an indefinite Pause only as a temporary
  handoff boundary. After the replacement proves its PID, target lock, startup,
  control acknowledgement, and fresh observation, the guarded reload restores
  the prior `RUNNING`, indefinite `PAUSED`, or unexpired timed Pause; expiry
  resolves to `RUNNING`, while a failed handoff remains `PAUSED`.
- Exact integration candidate `611bb25` passed compilation, state-definition
  validation, clickmap integrity with zero errors and the 44 established
  orphan candidates, and all 1,703 tests in 358.33 seconds. The
  documentation-only candidate was promoted without service or device action
  behind rollback tag `production-before-20260807T104757-4b176dd`.

### 2026-08-07 incremental Auto Pick repair scans

- Home Auto Pick repair now carries the one complete authoritative ranked
  prefix into a mutable planning order. It skips ranks already correct or made
  correct by an earlier insertion, scans forward only for a currently
  misplaced target, and inserts a verified rank-18/19 target into the
  top-17 prefix without expanding OCR authority into the unranked tail.
- The input boundary remains local and fresh: the physical viewport is
  re-anchored once after the initial read, every arrow tap still uniquely
  reacquires its row and proves one adjacent swap, confirmed-edge and moved-row
  traversal remain bounded, and a pre-input cache/context conflict performs at
  most two full semantic resynchronizations. The repair's final authoritative
  17-rank read-back is reused by its caller instead of scanning twice.
- A synthetic reproduction of the production-confirmed shape places Free
  Upgrade Chance, Inner Land Mines, and Damage at ranks 13, 18, and 19. The
  repair makes the required nine verified swaps while acquiring only those
  three targets, with no scans for the correct suffix. All 21 focused tests and
  135 adjacent Home, Perk, setup, navigation, and clickmap tests passed. The
  complete isolated checkpoint then compiled the repository, passed the state
  and clickmap validators with zero errors and the 44 established orphan
  candidates, and ran all 1,689 tests in 329.33 seconds. No process or device
  interaction was used for this repository change.
- Exact integration candidate `dd3a0fa` repeated compilation and both
  maintained validators with zero errors and the same 44 established orphan
  candidates, then passed all 1,703 tests in 328.22 seconds. It was promoted
  behind rollback tag `production-before-20260807T171220Z-9db59e1` after PID
  `425009` stopped cleanly. Replacement PID `1027338` acquired the held
  `localhost:5555` lock, acknowledged `PAUSED` with `HOME`, `farm_t18`, and
  x6.3 retained, and attached to the existing battle. After guarded Resume,
  its continuity check confirmed the latest completed battle was unchanged;
  a fresh unpaused observation then reported normal `RUNNING` operation at
  wave 9393 with no Strategy Action Gate. No Surrender was used.

### 2026-08-07 player-save temporal authority

- `feature/player-save-temporal-authority` adds typed temporal classes to the
  running-attachment projection. Its private binding covers exact mapping,
  target generation, source and final activity scopes, active-round identity,
  and capture time. Activity Continuity publishes facts only after persisting
  the final scope; App revalidates current process/target/scope before fan-out,
  and retained provenance is redacted.
- No Strategy now feeds save-backed Workshop preset, equipped Guardians,
  selected Bot preset, and equipped Modules into its actual
  `observed_run_configuration` as round-invariant facts. Identical claims
  merge, a complete same-round conflict becomes sticky `unavailable`, and
  partial Guardian/Module UI evidence cannot replace a complete save claim.
  Cards remain point-in-time, and Bot progression is separate from the selected
  preset.
- The same observation supplies Tournament's existing in-battle Workshop seam
  through a one-use carrier that rechecks process, target generation, scope,
  and active-battle ownership. A valid value avoids a second save read and any
  additional game-Home or Android lifecycle route; invalid evidence preserves
  the explicit Workshop deferral.
- Focused temporal, attachment, No Strategy, and Tournament regression passed
  201 tests. The supported development checkpoint compiled the repository,
  passed state-definition validation and clickmap integrity with zero errors
  and the established 44 orphan candidates, and passed all 1,737 tests in
  322.38 seconds. No live/device validation was needed.

### 2026-08-07 player-save terminal boundary handoff

- `feature/player-save-boundary-handoff` splits the terminal structural History
  transition from semantic completed-report projection. One natural terminal
  bundle now proves the append or capped rollover once and stages only a
  bounded, redacted, one-use payload in the activity-scope ledger; decoded and
  raw save data never persist there.
- Game Over → Home, Game Over → direct Retry, and Tournament Results → Home
  validate process session, exact target generation, source/destination scope,
  mapping, transition, and natural-boundary timing before adopting that tail as
  `latest_completed_battle`. Acceptance performs no second save read or Battle
  History navigation. Process, target, scope, shape, transition, or persistence
  failure preserves the existing forced-save, passive-poll, or UI fallback.
- An unknown `killedBy` or other semantic projection failure still opens the
  applicable More Stats route but no longer discards structural continuity.
  A `save_first` Home with neither handoff nor configuration requirements uses
  the guarded baseline-only forced serialization path. Tournament Results
  receives complete or explicitly unavailable conditions from the original
  bundle and no longer performs its prior duplicate read.
- Focused regression covers all three routes, one-use atomicity, redaction,
  process/target/scope rejection, structural/semantic independence, malformed
  provenance, baseline-only Home acquisition, and retained consumer fallback.
  The supported development checkpoint compiled the repository, passed state-
  definition validation and clickmap integrity with zero errors and the
  established 44 orphan candidates, and passed all 1,724 tests in 377.86
  seconds. No live/device validation was needed for this repository-only
  lifecycle consolidation.

### 2026-08-07 typed player-save acquisition foundation

- `feature/player-save-acquisition-foundation` adds the immutable typed
  acquisition bundle and `StablePlayerSaveAcquirer`. The owner serializes the
  complete exact-target operation, verifies target generation before and after
  a quiet two-identical-read transport, decodes in memory, immediately drops
  raw payload references, and exposes only sanitized failures and redacted
  provenance. It creates no global latest-snapshot cache.
- The guarded serializer retains background/restore and source/control policy;
  a forced bundle is published only after successful restoration and full
  revalidation. Home preflight, ordinary and forced-attachment History,
  natural terminal capture, passive audit, and standalone Tournament capture
  now use the shared owner without changing their UI-fallback or blocking
  policies. The local/ADB inspection tool remains an explicit offline path.
- One natural terminal bundle fans out to profile progression, completed-report,
  and Tournament-condition projectors without another pull. Focused tests cover
  global lock serialization, exact target/generation loss, redaction, malformed
  projection independence, restored-source ambiguity, and one-read/many-
  projector behavior.
- The focused migrated suite passed 173 tests. The supported development
  checkpoint compiled the repository, passed state-definition validation and
  clickmap integrity with zero errors and the established 44 orphan candidates,
  and passed all 1,712 tests in 322.50 seconds. No live/device validation was
  needed for this repository-only ownership consolidation.

### 2026-08-07 Utility Dissonance subtype deployment and confirmation

- `17e4e0c` separates localized purple Dissonance-family evidence from the
  validated white icon: star records Utility, sword records Attack, and an
  unrecognized contour remains generic Dissonance. Utility keeps the guarded
  Attack Damage Slider read available, and terminal classification preserves
  the subtype label.
- Exact integration candidate `7029456` passed compilation, state-definition
  validation, clickmap integrity with zero errors and the 44 established
  orphan candidates, and all 1,700 tests in 325.73 seconds. It was promoted
  behind rollback tag `production-before-20260807T075200Z-cfbad10`.
- The old automation process completed its in-flight Perk timeline operation,
  acknowledged Pause, and stopped cleanly. The shared control-surface service
  reloaded as PID `359659`; replacement automation PID `360077` acquired the
  held `localhost:5555` lock, acknowledged `PAUSED` / `NEXT_BATTLE` /
  `farm_t19`, published a fresh `EVENT/PAUSED` observation, and retained the
  normal future `auto_validate` policy. The screen had moved to Event/Bots
  without automation input during the maintenance window, so promotion did
  not tap or Resume that unowned navigation and never used Surrender.
- Production PID `360077` then confirmed the repaired boundary on an
  operator-started Tier 19 Utility run. Two complete running frames classified
  the white-star badge as `Utility Dissonance`; the guarded No Strategy
  inventory visited only the accessible Attack Damage Slider. After the
  operator's Surrender, `Battle20260807T011927-0700` recorded Game Over as
  Dissonance with high-confidence label `Utility Dissonance`, Tier 19, wave
  410, and Killed By `Surrender`. Byte-identical post-run save reads correlated
  active selector `3` with completed type `3` while leaving the raw enum outside
  runtime authority. `ISSUE-2026-032` is resolved; the exact metrics and
  projections are retained in the
  [confirmation evidence](../issues/evidence/utility-dissonance-confirmation-2026-08-07.md).

### 2026-08-06 confirmed scroll-edge and Auto Pick traversal repair

- Live Farm T19 retries showed Auto Pick making real ordering progress and
  then truncating its 17-row scan immediately after one accepted swipe left
  the viewport unchanged. The resulting predecessor, target-rank, and missing
  `Free Upgrade Chance` errors all failed closed before Battle input.
- `6837c18` generalizes edge inference across the shared scroll-to-edge,
  capture-scroll, and scroll-until-visible primitives: two consecutive stable
  post-swipe frames are required, and any movement resets confirmation. Perk
  page capture and the Auto Pick ranked scan, locator, and local reacquisition
  now use that shared traversal.
- Regression coverage reproduces one ignored swipe in all three primitives and
  at each affected Auto Pick boundary. The 29 focused tests, 148 adjacent
  tests, and complete isolated checkpoint passed; the checkpoint compiled the
  repository, passed maintained validators, and ran all 1,686 tests in 319.51
  seconds. `ISSUE-2026-031` records the diagnosis and production confirmation.
- Exact integration candidate `a8483f4` passed another complete 1,686-test
  checkpoint in 321.90 seconds and was promoted behind rollback tag
  `production-before-20260807T071511Z-538d4e2`. Replacement PID `292872`
  retained Farm T19, x2.0, and the original pending gate; its scoped retry
  repaired and authoritatively verified all 17 Auto Pick ranks without a
  waiver. The full Farm configuration then passed, Battle started, and steady
  state was confirmed at waves 200, 240, and 290 at x2.0 with no Strategy
  Action Gate. The expected boss-live Orb Distance path closed its greyed
  controls, waited for combat to advance, then completed at 30.00m.
- The successful repair also exposed a performance follow-up: it repeatedly
  returned to a confirmed top and rescanned the list for already-correct later
  ranks. That bounded but slow traversal is routed to the state/action backlog
  with its row-authority and final-read-back safeguards intact.

### 2026-08-06 Ban Perks Selected/Available boundary repair

- Live diagnosis of the blocked Tier 19 Farm transition showed that repair had
  restored the visible bans, but the verifier's supposed top frame was halfway
  down Available and included `Unlock a random ultimate weapon` as a selected
  row. The battle start failed closed and the operator-owned Pause was left
  unchanged.
- `5cab87a` lets edge scrolling require a caller-proven boundary, makes Ban
  navigation prove the complete outlined Selected Perks block, and restricts
  fixed-block extraction to outlined rows through six selections or Empty
  Slot. Generic callers retain their existing visual-stability behavior unless
  they opt into a required boundary.
- Regression coverage reproduces false visual stability, rejects six visible
  Available rows, preserves a temporary five-ban Empty Slot block, and scrolls
  until outlined Selected evidence appears. Read-only replay of retained real
  frames verified all three relevant states. The complete isolated checkpoint
  passed compilation, maintained validators, and all 1,680 tests in 322.42
  seconds; no development device input was required.
- `ISSUE-2026-030` is resolved. Exact integration candidate `65b8fc5` passed a
  second complete 1,680-test checkpoint and was promoted on 2026-08-06 behind
  rollback tag `production-before-20260807T042659Z-efc190c`. Replacement PID
  `104919` owns `localhost:5555` and reported a fresh Tier 19
  `HOME_SCREEN/PAUSED` with `farm_t19` acknowledged; the pre-existing pending
  gate and operator-owned Pause remained unchanged, and promotion sent no
  device input or battle start.

### 2026-08-06 paused Home manual-start continuity repair

- Live read-only diagnosis found that a battle then labeled Attack Dissonance
  by the purple-only detector manually started from paused Home and advanced
  normally while Activity Continuity retried its obsolete Home-only Battle
  History source. The badge was later established as Utility Dissonance in
  [`ISSUE-2026-032`](../issues/resolved-2026.md#utility-dissonance-star-was-labeled-as-attack).
  The safe retry loop sent no input, but its exclusive hold prevented the No
  Strategy inventory from starting.
- `8cf5548` makes a pending Home continuity route follow a passively observed
  `RUNNING` transition in the same activity scope, clear the obsolete Home
  control expectation, and continue only after action authority returns. The
  fix is generic to paused manual starts and does not add a No Strategy or
  Dissonance conditional.
- The regression proves paused Home setup, a paused manual battle start, zero
  History reads or inputs while Paused, one running-source baseline after
  Resume, preserved scope identity, and a single transition diagnostic. The
  focused continuity, Battle History, and startup suites passed 155 tests.
- The complete isolated checkpoint passed compilation, state-definition
  validation, clickmap integrity with zero errors and the 44 established
  orphan candidates, and all 1,665 tests in 324.86 seconds. Diagnosis did not
  tap, stop, reload, Surrender, or otherwise mutate the production battle; live
  confirmation remains routed through
  [`ISSUE-2026-027`](../issues/open-2026.md#paused-home-continuity-did-not-follow-a-manually-started-battle)
  after promotion. The combined candidate was deployed as `ab84a3c` on
  2026-08-06; replacement attachment was confirmed, but that boundary did not
  reproduce a paused Home manual start, so the issue remains open for one
  natural confirmation.

### 2026-08-06 save-only running attachment and lease replay correction

- Fresh live diagnosis after the paused-source repair found three follow-on
  defects: a missing running baseline still selected Battle History UI, the
  guarded attachment reader discarded normalized save configuration checks,
  and No Strategy unconditionally traversed every supported UI section. It
  also traced the reported abnormal lease warning to replay of an already
  released terminal directive after process replacement.
- `cde691b` makes every `save_first` running attachment save-only. A fresh
  structural tail records, compares, migrates, or conservatively replaces the
  scope; failure waits without opening Battle History. The same accepted
  exact-version snapshot supplies complete allowlisted observations to No
  Strategy, whose in-battle and post-run routes now visit only unresolved
  fields. The then-current purple-only detector labeled the badge Attack and
  therefore resolved Damage Slider without probing the Attack menu; the later
  Utility subtype correction is tracked in
  [`ISSUE-2026-032`](../issues/resolved-2026.md#utility-dissonance-star-was-labeled-as-attack).
- Terminal development directives are now recognized before former-runtime
  binding checks. Their original release result remains the sole outcome after
  restart; genuinely active leases that lose their runtime retain the abnormal
  warning and paired result.
- Focused save, continuity, No Strategy, lifecycle, and lease coverage passed
  392 tests. The complete isolated checkpoint passed compilation,
  state-definition validation, clickmap integrity with zero errors and the 44
  established orphan candidates, and all 1,676 tests in 323.36 seconds.
  Diagnosis added no device input, Surrender, lifecycle transition, or control
  mutation.
- Production candidate `ab84a3c` passed a fresh complete checkpoint of all
  1,676 tests in 328.97 seconds and was promoted behind annotated rollback tag
  `production-before-20260807T011012Z-d5ff68d`. A bounded replacement attached
  through one guarded save serialization, opened no Battle History UI, applied
  11 save-backed No Strategy fields, and visited only Modules. A privacy-safe
  read proved Modules was genuinely unresolved because of an unsupported
  primary `infoIndex`; that mapping remains in the validation backlog. The
  absence of a Damage Slider visit was later separated from this confirmation:
  the purple-only detector had mislabeled Utility Dissonance and suppressed an
  accessible read, as tracked in
  [`ISSUE-2026-032`](../issues/resolved-2026.md#utility-dissonance-star-was-labeled-as-attack).
- The same replacement preserved the previously released terminal lease
  without an abnormal warning, duplicate result, or directive rewrite.
  `ISSUE-2026-028` and `ISSUE-2026-029` are resolved with exact retained
  [promotion evidence](../issues/evidence/no-strategy-attachment-promotion-2026-08-06.md).

### 2026-08-06 post-promotion branch/worktree lifecycle cleanup

- A fresh audit classified all 25 local feature branches and linked worktrees
  by worktree cleanliness, ignored content, `main` integration, durable
  validation/evidence, and active ownership. With exact operator approval, 11
  qualifying worktrees were removed through `git worktree remove` and their
  local branches through `git branch -d`; four active or unintegrated pairs and
  ten ambiguous pairs were retained. The permanent `main` and `develop`
  checkouts, rollback tags, remote branches, and user-owned or required
  evidence were untouched.
- The production-promotion operation now owns the executable cleanup procedure
  and links the existing repository-topology contract. It requires fresh
  per-candidate inspection, exact approval, ancestry compatible with the safe
  branch-deletion guard, refusal-preserving Git commands, and final topology
  validation without adding another document or mandatory startup path.
- The complete isolated checkpoint passed compilation, state-definition
  validation, clickmap integrity with zero errors and the 44 established orphan
  candidates, and all 1,664 tests in 326.39 seconds. The changed local target
  and anchor resolved and `git diff --check` passed. No runtime process,
  service, ADB target, emulator, control state, or battle was inspected or
  changed.

### 2026-08-06 version-1073 raw save-field disposition audit

- `1d424f2` inventories all 739 exact `SaveLoad+PlayerData` decoded-root keys
  without retaining their values: 13 structural, 31 automation-gating, 51
  profile-observation, 34 private, 69 ignored with an explicit reason, and 541
  unknown. Exact categories, disjoint membership, declared count, and the
  canonical field-name hash are mapping-load invariants.
- Exact-version decoding now requires root-name equality with that manifest.
  Added or removed fields invalidate the shape and restore the existing UI
  fallback before mapped checks or profile values can be published. Private,
  ignored, and unknown root values remain absent from snapshots.
- Every one of the 12 normalized profile components now publishes its audit-row
  ID, structural evidence level, and provenance. This closes disposition and
  validation-metadata coverage without claiming names, formulas, caps, or
  effective values for still-indexed data.
- A stable read-only live import during the ordinary Tier 19 battle accepted
  version 1073, the exact root class and 739-field manifest, and all 12 profile
  components. It required no pause, Home transition, input, or retained raw
  save. The focused cross-consumer set passed all 244 tests; the complete
  isolated checkpoint passed compilation, state validation, clickmap integrity
  with zero errors and 44 known orphans, and all 1,654 tests.

### 2026-08-06 documentation context-reduction audit

- `4669547`, `860e747`, and `8132996` preserved narrow host-performance
  evidence and replaced the 6,138-word active issue ledger with a 1,500-word
  22-entry safety index plus conditionally loaded dossiers and separate
  resolved/unconfirmed history. All 2,194 cited aggregate rows and eight
  windows remained reproducible byte-for-byte.
- `2474858` removed completed work from active backlogs after preserving missing
  outcomes. At the audit base, the active root/domain backlogs plus issue ledger
  contained 15,234 words; the final routed set contains 6,627, a 56.5%
  reduction, with zero completed checkboxes in active files.
- `30ed51a` established the disposable outcome-coordinator policy.
  `a682ddc` reduced automatic startup from 2,422 to 1,231 words, replaced the
  12,478-word runtime monolith with a 420-word selector and 249–474-word
  operation chapters, and separated live preflight, action authority, and log
  semantics into conditional owners.
- `6e705e6` removed 60 stale duplicated API summaries, the obsolete spec
  bundle, all legacy web-chat prompts, both spec packers, six superseded plans,
  and two retired roadmaps. Player-save evidence, UI schema, YAML strategy, and
  template workflow guidance moved to explicit architecture, reference, and
  tooling owners. API summaries are now ephemeral, revision-labeled output
  only when explicitly requested; capability discovery uses current source,
  configuration, callers, and tests.
- Across the audit range, tracked Markdown fell from 109 files/170,909 words to
  48 files/153,775 words despite adding durable issue evidence and narrow
  operation chapters. `docs/modules/` now contains only this on-demand history.
  The separate source-capability discovery audit remains active, as does the
  exact Codex concurrency-setting follow-up until the installed parser accepts
  it.
- The final merged checkpoint passed compilation, state-definition validation,
  clickmap integrity with zero errors and the 44 established orphan candidates,
  and all 1,656 tests. All 278 local Markdown targets and 119 linked anchors
  across 48 files resolved, range whitespace checks passed, and current Codex
  configuration loaded under strict parsing. The documentation audit used no
  runtime process control, ADB command, device input, or volatile-state claim.

### 2026-08-06 restart-stable session-preflight reporting

- `4739cf3` upgrades the activity scope's nested session-preflight receipt to
  schema 2. It retains the existing normalized evidence projection in a
  run/strategy/configuration-bound schema-1 envelope after strict finite-JSON
  normalization and a 64 KiB cap. Atomic compare-and-set scope persistence is
  unchanged.
- A continuity-confirmed replacement process restores that projection only to
  terminal reporting. It does not seed completion, mismatch, waiver, repair,
  or action-authority variables. Malformed current receipts run the declared
  attachment checks; legacy schema-1 receipts keep compatibility while
  reporting their detailed evidence as unavailable. The snapshot is replaced,
  not merged with the attachment's Home-lock placeholder, remains available for
  a terminal-capture retry, and clears at the next battle or strategy boundary.
- Configured Game Over paths now omit `observed_run_configuration`, and record
  assembly defensively drops any empty observation. Real No Strategy snapshots
  remain separate from configured intent and continue to serialize normally.
- Focused logger, lifecycle, terminal, No Strategy, Tournament, and battle
  record coverage passed 179 tests. Compilation and whitespace checks passed,
  followed by all 1,656 repository tests. Retained completed-record inspection
  confirmed that a real full Farm projection is 5.8 KB with valid deferred
  Free Upgrade-lock evidence. Validation used no ADB input, process replacement,
  control mutation, or live battle action.

### 2026-08-06 bounded live production/development coordination validation

- With separate operator authorization, the completed
  [production/development coordination contract](../architecture/development_isolation.md)
  was exercised against the production-owned runtime and its exact ADB target.
  Preflight confirmed the control directive, host-backed runtime owner and
  lock, ADB connection, current screen, recent action log, and absence of a
  competing lease. The validation neither started nor Surrendered a battle and
  performed no terminal or lifecycle action.
- One no-input lease reached active acknowledgement and then expired normally.
  Production retained its suppressive hold until a fresh known running-battle
  observation, recorded terminal disposition `expired`, removed the hold, and
  restored normal authority in the same battle.
- A second lease was heartbeated and used by the lease-aware helper for exactly
  one non-retried tap on the already-selected Attack tab. A subsequent owned
  Pause made the lease unusable immediately, production recorded terminal
  disposition `revoked`, observations continued while every action class was
  blocked, and RUNNING was restored only after the unchanged Pause request ID
  and same battle identity were verified.
- A final no-input lease exercised explicit release. The release request made
  the lease inactive while production remained suppressive; a fresh
  post-release running-battle observation then recorded terminal disposition
  `released`, removed the hold, and restored normal authority. An earlier
  request made while the structured action-authority snapshot was stale was
  rejected with HTTP 409 without creating a lease, and was retried only after
  fresh matching ownership evidence appeared.
- The production action log contains the request, acknowledgement, input,
  Pause revocation, release-pending, and terminal-result records. No live defect
  or uncertain input occurred, so no runtime implementation changed. The
  repository-local coordination harness and its full checkpoint remain
  recorded in the
  [combined boundary completion](#2026-08-06-combined-productiondevelopment-coordination-boundary).

### 2026-08-06 combined production/development coordination boundary

- `b2d2811975b80957159fe9da28cf7ba0d70f429c` (integrated as `073bf05`) adds
  one deterministic repository-local harness for the completed
  development-coordination stack. Interrupted bootstrap recovery proves the
  prior `.venv` selection remains untouched until a verified completion marker
  and atomic replacement; a blocked frame writer proves a concurrent reader
  sees the complete prior PNG before replacement and the complete new PNG plus
  schema-1 sidecar afterward.
- The fake-runtime path crosses the actual control store, automation
  supervisor, runtime action-authority publisher, host-lock-derived control
  status, composite lease decision, lease-aware input helper, action log, and
  fake ADB subprocess boundary. It proves one-lease exclusion, suppressive
  hold before acknowledgement, background-input quiescence, exactly one
  exact-target input, Pause/Stop precedence without revival, heartbeat expiry,
  runtime/target/battle termination, stale and near-expiry rejection, and
  release remaining held through ambiguity until a fresh known observation.
- The new harness passed all 11 scenarios; the focused bootstrap, frame,
  lease, helper, control-surface, authority, watchdog, dispatcher, tap-safety,
  and ad-gem set passed all 239 tests. The exact implementation commit's full
  non-live checkpoint passed compilation, state-definition validation,
  clickmap integrity with zero errors and 44 established orphans, and all
  1,634 pytest tests. No repository-local production defect surfaced, so no
  production code changed. No live runtime, process, systemd unit, control
  socket, ADB server or device, emulator, production artifact, screen, log, or
  battle was inspected or changed; bounded live lease validation remains a
  separately operator-authorized outcome-coordinator step.

### 2026-08-06 save-first terminal battle reports

- `069b1d9` makes the causally bound exact-version player-save report primary
  at normal Game Over and Tournament Results. The one stable exact-target
  terminal read is shared with profile progression and available Tournament
  conditions instead of performing an additional save acquisition.
- Attachment requires the current process's bound activity scope, a compatible
  player-save baseline for that same scope, exactly one valid History append or
  capacity-30 rollover, an inactive save, a complete mapped semantic entry,
  matching normal/Tournament kind, and no contradiction from available compact
  terminal identity. Terminal-only starts, UI-sourced or missing baselines,
  unknown causes, changed shapes, stale or invalid transitions, handoffs, and
  mismatches retain the existing More Stats route.
- A successful save report supplies all 16 sections and 144 ordered rows with
  exact decimal source values and derivation/provenance metadata. Normal battle
  schema 6 and Tournament schema 4 preserve the established JSON/Markdown
  contracts. Compact Game Stats remains optional augmentation and cross-source
  evidence; Game Stats and Perks capture, Wait/Retry/Home behavior, and all
  terminal lifecycle inputs remain unchanged.
- The fallback still validates the complete clipboard report before guarded
  scrolling OCR. Regression coverage includes append, capped rollover,
  binding/source/tail/kind failures, one-read reuse, save-derived normal and
  Tournament persistence, optional compact OCR, contradiction fallback, and
  proof that More Stats is never tapped on the accepted save path.
- The focused player-save, App, normal battle, Tournament, schema, and handler
  suite passed all 412 tests. The complete isolated checkpoint passed
  compilation, state validation, clickmap integrity with zero errors and 44
  known orphans, and all 1,624 tests.

### 2026-08-06 lease-aware development input boundary correction

- `4d7af4b54ba00e387681e559cffe699bc3ca70bb` corrects the accepted
  development-side input helper so its final production-status check reserves
  the selected ADB subprocess timeout plus one second for server timestamp
  precision and one second for status-response/dispatch latency. Taps retain a
  5-second timeout; swipes use at least 5 seconds and otherwise their duration
  plus 2 seconds, capped by the valid 5000 ms gesture at a bounded 7-second
  timeout. The corresponding minimum lease windows are 7 and 9 seconds, with
  equality accepted.
- An insufficient final window rejects before mutating ADB input and directs
  the operator to heartbeat separately, wait for the renewed matching runtime
  acknowledgement, and invoke the non-replaying helper again. A changed lease,
  runtime, target, or acknowledged expiry between geometry acquisition and the
  final check still fails closed.
- The helper now consumes the production-owned composite lease `active` value
  as the canonical suppressive-authority decision. It retains only its own
  command bindings and structural checks: supported API/capability, RUNNING
  control, request/acknowledgement lease IDs and lifecycle states, matching
  runtime identity and exact target, and one valid matching acknowledged
  expiry. Duplicate acknowledgement-dictionary equality, authority-matrix and
  gate-age reconstruction, and direct `runtime.instances` policy were removed
  without changing the control surface's active calculation.
- The focused development-input, control-surface, geometry, screenshot,
  logger, and ADB-connection run passed all 150 tests. The complete non-live
  checkpoint passed compilation, state-definition validation, clickmap
  integrity with zero errors and 44 established orphans, and all 1,621 pytest
  tests. Changed local links/anchors and feature-range whitespace also passed.
  No live runtime, process, systemd unit, control socket, ADB server or target,
  emulator, production log, screen, or battle was inspected. Combined
  fake-runtime/fake-ADB coordination validation remains open, and a live lease
  remains separately authorized master work.

### 2026-08-05 completed-run profile progression snapshots

- `0075349cba5537fe4d6dff1b582185e2cd210174` adds a versioned,
  exact-save projection of 12 account components to every captured normal or
  Tournament terminal. It covers pack/ad bonuses; Bots; Cards; Enhancements;
  Guardian; Harmony/Power nodes; equipped Primary and Assist Modules; relics;
  Research; Tower, Background, and Menu Themes; Ultimate Weapons; and
  Workshop levels.
- Terminal capture uses two byte-identical reads from the exact target
  generation already owned by the runtime, sends no input, and fails open so
  Game Stats capture remains authoritative. The raw save, account identifiers,
  balances, purchase histories, Module GUIDs, and arbitrary inventory records
  are not retained.
- Normal battle schema 5 stores a top-level snapshot and an exact-path delta
  against the newest earlier complete, mapping-compatible normal battle. The
  first run establishes a baseline, and a partial capture is skipped when
  selecting the next baseline. Tournament schema 3 retains its terminal
  snapshot without affecting normal-battle comparisons.
- The projection preserves source fields and indices instead of inventing
  names, formulas, costs, or effective multipliers. Markdown reports Theme and
  relic counts plus exact changes; JSON retains the complete structural
  evidence and component fingerprints for later CPH/cell/survival analysis.
- The focused player-save, terminal-capture, battle, Tournament, and run-
  initialization suite passed all 216 tests. The complete isolated checkpoint
  passed compilation, state validation, clickmap integrity with zero errors
  and 44 known orphans, and all 1,612 tests. Changed whitespace and local
  documentation anchors also passed validation.
- One bounded read-only exact-target save normalized all 12 current components
  without retaining the raw save. No device input, battle lifecycle change,
  production code change, merge, deployment, or installation was performed;
  the first post-deployment normal run will intentionally establish the
  comparison baseline.

### 2026-08-05 save-first replacement-process activity continuity

- `f218619` makes an already-running replacement process prefer one guarded,
  fresh exact-target save over Battle History navigation when the retained
  activity scope has a compatible save baseline or independently comparable
  UI Tier, Wave, and Battle Date evidence. It preserves the scope for an
  unchanged tail and starts a conservative later-battle scope only for one
  valid append or capacity rollover.
- The shared Android-Home serialization path requires the same runtime,
  activity scope, lifecycle-owned active battle, target, and generation across
  stable pre-background and post-restore `RUNNING` observations. Ambiguous
  source, time, identity, transition, control, or restoration evidence fails
  closed to the guarded UI route. Correction `fddb855` additionally requires a
  positive independently normalized History bridge before a UI baseline may
  migrate to save evidence.
- Regression coverage is in `test/test_activity_continuity.py`,
  `test/test_player_save_history.py`, and
  `test/test_player_save_preflight.py`. The canonical contract is in
  `docs/architecture/player_save.md` and `docs/architecture/runtime.md`.

### 2026-08-05 save-first targeted repair reconciliation

- `b9c229a77d2fbc5efe16a7cdcb6681d469751a0b` separates global snapshot
  trust from each check's configuration disposition. A complete, validated,
  supported exact difference is now `save_mismatch`; unsupported, incomplete,
  unknown, stale, and forced-audit evidence remains ordinary `ui_required`.
  The complete plan is frozen before setup input.
- A trusted mismatch queues only its existing guarded UI path and supplies no
  mutation authority. The path must observe a current mismatch, perform its
  normal guarded repair, and verify the result. The repaired check is recorded
  as UI-verified, is never called save-confirmed, and is not inserted into save
  carry. Multiple mismatches retain independent queues.
- The motivating Cards repair now preserves accepted First Perk Choice, Ban
  Perks, Auto Pick order, and other Home decisions, so their Perks tabs remain
  closed. Verified Home, Target Priority, Poison Swamp Stun, Damage Slider, Orb
  Distance, and other independent UI-only repairs also preserve unrelated
  exact-next-battle carry.
- Authoritative UI that disagrees with a `save_match`, or that already matches
  a trusted saved mismatch before this coordinator repaired it, is a
  contradiction that invalidates the complete snapshot and fails closed.
  Acquisition, serialization, freshness, version/structure, target, boundary,
  context, control, requirement, launch, and first-`RUNNING` continuity
  failures retain global invalidation. Final consistency accepts mixed
  save-backed omissions and current UI proof while evaluating every supplied
  screen.
- Diagnostics expose only normalized trust, disposition, affected-check,
  repair, contradiction, and remaining-carry evidence. Raw save bytes, decoded
  roots, account identifiers, private fields, arbitrary history, and raw Module
  records remain unpublished.
- The focused affected suite passed all 223 tests, the broader configuration,
  initialization, carry, and First Perk normalization set passed all 414 tests,
  and the complete isolated checkpoint passed compilation, state definitions,
  clickmap integrity with zero errors and 44 known orphans, and all 1,495
  tests. Dependency locks and changed local documentation links/anchors also
  passed validation.
- This was strictly code-only work using fakes and retained fixtures. It did
  not inspect or interact with the production process, systemd, ADB, emulator,
  shared live frame, or battle; nothing was deployed, merged, rebased, pushed,
  or installed. Deployment and first ordinary-boundary observation remain
  coordinator work.

### 2026-08-05 observation-only Tournament Module save mapping

- `2dcde8bdd3717af93239a464901b77bf4578f366` makes version 1073 decode
  multiple cross-channel-validated values in each exact equipped slot instead
  of treating the original Farm value as the only possible value. The complete
  `tournament_standard` reference is save-mapped, including generator Primary
  Project Funding `43` and core Assist Harmony Conductor `39`.
- The operator-authorized current Tournament pairing also mapped armor Primary
  Anti-Cube Portal `20`, armor Assist Space Displacer `19`, and core Primary
  Dimension Core `38`. The operator withdrew an initial Magnetic Hook
  identification after the equipped detail confirmed Harmony Conductor, so
  Magnetic Hook remains an explicit future natural-calibration gap.
- Tournament remains `observe`. A complete supported save projection publishes
  `save_observation`, reports any difference from `tournament_standard`, and
  may omit duplicate Modules navigation; it never enforces the reference or
  authorizes equip, unequip, transfer, repair, lifecycle, or battle input.
  Enforced mismatch, unknown/unsupported values, partial/malformed structures,
  `force_ui`, and `comparison_audit` retain the full UI route.
- The mapping is narrowly slot/value-scoped. It claims no generic Module ID,
  inventory, rarity, level, star, effect, substat, GUID, or private-record
  semantics. Exact-next-battle carry binds only when the complete observed
  assignment agrees with the original accepted evidence.
- Focused save/preflight/Home/carry validation passed all 169 tests. The
  broader Tournament/strategy set passed all 240 tests, all generated strategy
  plans were byte-identical, and the complete isolated checkpoint passed
  compilation, state validation, clickmap integrity with zero errors and 44
  known orphans, and all 1,439 tests. All 42 local links across the six changed
  documents, including 27 anchors, resolved.
- This follow-up reused the prior accepted Farm evidence and did not repeat its
  broad campaign. It performed only the newly authorized bounded live pairing:
  stable exact-target save reads, read-only Modules overview/detail inspection,
  and verified return to the same Tournament. It connected the configured ADB
  target and inspected the live emulator, but did not inspect a shared live
  frame or change a loadout, battle lifecycle, automation process, systemd
  service, installed file, or production code. Deployment and ordinary
  save-first-boundary observation remain coordinator work.

### 2026-08-04 save-first fallback correction and History continuity

- `fe0c43fee8b2c013e13b89e85508e7555b377054` corrects the first deployed
  save-first boundary's unnecessary Auto Pick, Free Upgrade lock, Modules,
  Target Priority, and Battle History UI routes. The causes were a missing Perk
  ID plus false sentinel contract, an incorrect Target Priority permutation,
  exact-set lock reconciliation, unpublished Module slot values, and Activity
  Continuity running before the shared Home snapshot could publish its tail.
- Version 1073 now requires exactly 34 unique mapped Auto Pick IDs: an
  18-entry ranked prefix plus 16-entry unranked inventory tail, with no
  sentinel. ID `11` is `unlock_random_ultimate_weapon`; unknown IDs,
  duplicates, changed length, and changed membership remain fail-closed. The
  complete Target Priority map is `0=Closest (Default)`, `1=Basic`, `2=Fast`,
  `3=Tank`, `4=Ranged`, `5=Boss`, `6=In Spotlight`, `7=Protector`, `8=Elites`,
  and `9=Fleets`.
- Free Upgrade lists remain required subsets. Shockwave Size, Bounce Shot
  Targets, and Bounce Shot Range must be set; additional normalized locks such
  as Health are privacy-safe unmanaged evidence, do not trigger fallback, and
  are never unlocked. Missing requested locks and malformed arrays retain the
  complete UI check/repair path.
- Module authority is exact and value-scoped to Farm's Primary Amplifying
  Strike, Orbital Augment, Black Hole Digestor, and Multiverse Nexus plus
  Assist Being Annihilator, Anti-Cube Portal, Singularity Harness, and
  Dimension Core in their four typed slots. Unknown/partial structures and
  Tournament's Project Funding and Harmony Conductor retain the full UI path;
  rarity, levels, stars, effects, substats, inventory records, GUIDs, and
  private values remain unpublished.
- The authoritative Home snapshot now seeds a source-tagged structural History
  baseline without a second acquisition. Runtime-owned direct Retry uses fresh
  stable two-identical-read exact-target saves, passively polls an unchanged
  tail, accepts one append or capacity-30 rollover, and restores guarded UI only
  when acquisition/shape/transition failure remains safely bound. Unknown
  `killedBy` preserves structural continuity, UI/save fingerprints are never
  equated, and legacy schema-1 scopes migrate only through their known UI
  source. Attachment, terminal record construction, lifecycle authority, and
  collector receipts remain unchanged.
- Operator diagnostics now include mapping, completeness, support,
  disposition, reason, and normalized evidence; accepted Card Recharge Modes
  and Perk Bans no longer render as unavailable. The complete isolated
  checkpoint passed compilation, state validation, clickmap integrity with
  zero errors and 44 known orphans, and all 1,432 tests; the final focused set
  passed all 231 tests. Generated strategies were byte-identical, and all 42
  local links across the six changed documents, including 27 anchors, resolved.
- Accepted coordinator evidence was reused; no duplicate campaign or live
  validation was performed. This worker did not inspect or change a production
  process, systemd service, ADB server/target, emulator, shared live frame, or
  battle. Deployment and ordinary-boundary observation remain coordinator
  work.
- The next, separate Perk phase remains active backlog work: add a
  collector-independent normal-runtime stable-save cache, preserve exact saved
  pick waves, obtain a terminal stable prefix, and retain UI for unknown,
  unbound, audit, or unresolved-final evidence. It must not background an
  active battle by default; forced serialization requires explicit policy and
  preserved lifecycle/control authority.
### 2026-08-05 persistent managed ADB registration

- Commit `cd78104` moved managed TCP registration/reconnect ownership from the
  automation runtime into the long-lived Linux control service. The selected
  exact `localhost:PORT` now remains maintained while automation is stopped or
  replaced, and Stop/guarded replacement synchronously refresh registration
  after the old process exits.
- The managed unit explicitly selects observe-only runtime behavior. Direct
  `main.py` launches preserve their self-managed fallback, an outdated
  installed unit is rejected before a stopped managed start, and neither path
  uses a global daemon kill or guesses another endpoint. API status publishes
  connection owner, target, state, retry/warning details, last check, and
  configuration errors.
- Windows reverse-forward persistence, Linux registration persistence, and
  frame/input authority are now documented as separate layers. New-thread and
  runbook inspections include `adb_connection`, while exact `device` state
  remains insufficient without a supported fresh frame and runtime ownership.
- The same commit made the development checkpoint path-safe for feature names
  containing `adb` and scrubs the new managed-owner environment setting from
  isolated checkpoints.
- Focused ADB, process/control, initialization, and development-environment
  suites passed. The complete non-live checkpoint passed compile,
  state-definition validation, clickmap integrity with zero errors and 44
  existing orphans, and all 1,400 pytest tests. No live automation runtime,
  user-systemd service, ADB, emulator, game, or battle interaction was
  performed, and the changed units were not installed or deployed.

### 2026-08-04 save-first Home configuration preflight

- `9a006a00dadbb2d4104267ce85a1cd7b6c337e28` implemented the default
  `save_first` Home preflight without using the observation-only player-save
  collector. One exact-target coordinator owns the guarded Android-Home
  serialization boundary, the existing two-identical-read pull, in-memory
  decode, restored stable `NEW_BATTLE` proof, normalized privacy-safe
  dispositions, and safe distinction between UI fallback and blocked action
  authority. `force_ui` and `comparison_audit` retain the complete UI route.
- Version-1073 authority now covers Cards and recharge modes, Workshop, Bots,
  Guardians, independent First Perk Choice, Bans, the 18-entry ranked Auto
  Pick prefix over its structurally validated 16-entry inventory tail, and
  exactly the three current Farm Free Upgrade locks. Perk-capable Farm plans
  require `perk_wave_requirement`; Tournament declares no Perk requirement.
- Typed single-use carry may bind only to the exact runtime-owned next
  `NEW_BATTLE` and its first stable `RUNNING` boundary. It covers Auto Pick
  enabled `true`, a complete exact ten-ID Target Priority order, all nine
  primaries on, Spotlight Missiles on, Poison Swamp Stun in either calibrated
  polarity, and accepted Home sections. Every continuity break invalidates
  remaining evidence; the later targeted-repair correction above supersedes
  the original blanket first-repair invalidation so verified independent
  repairs preserve unrelated evidence. Actual UI observations still detect
  contradictions.
- The monolithic Ultimate Weapon check remains unvalidated; its supported
  value-scoped components fail independently. Mixed/off primaries and
  Spotlight Missiles off remain UI-required, while Orb Distance, Modules, and
  Damage Slider remain wholly UI-authoritative. Every existing UI audit,
  repair, verification, and fallback path is retained.
- Canonical Farm, GC Farm, and Tournament strategies regenerated
  byte-consistently. The focused affected suite passed all 232 tests. The
  complete non-live checkpoint passed compilation, state-definition
  validation, clickmap integrity with zero errors and 44 existing orphans, and
  all 1,385 pytest tests. All 39 local links across the five changed
  documents, including 26 anchors, resolved; cached and base-range whitespace
  checks passed.
- Prior accepted calibration was reused; no duplicate campaign or live
  validation was performed. No process, systemd service, ADB server or target,
  emulator, shared live frame, current battle, or installed runtime was
  inspected or changed. No merge, push, rebase, deployment, branch mutation,
  or worktree-topology action occurred.

### 2026-08-04 cooperative interactive-development lease

- `fba8c50151069c3ffae86ac8b1050094b8985330` implemented delivery step 3 of
  production/development coordination on the existing control directive,
  supervisor, structured action-gate status, and control-surface API. One
  bounded request can be acknowledged only by the freshly matched production
  runtime, PID, session, and ADB target after it installs the production-owned
  `external_development` hold at a safe coordination boundary.
- The hold leaves capture, detection, interpretation, and status active while
  denying every production input class, including auxiliary/background input,
  with no in-process owner bypass. Pause and Stop, heartbeat expiry, runtime or
  target replacement, battle boundaries, and natural Game Over terminate the
  lease. Release remains suppressive until a fresh post-release observation
  permits safe hold removal; ambiguous cleanup fails closed and stays visible.
- The version-1 API now exposes requested state separately from the structured
  runtime acknowledgement, returns HTTP 409 for a competing live request, and
  advertises `interactive_development_lease_v1` at server revision 26. Concise
  transition events use the existing action log without per-heartbeat noise.
- The initial focused authority, supervisor, application-control, API, and
  strategy-authoring run passed 132 tests. The complete non-live checkpoint
  passed compilation, state-definition validation, clickmap integrity with
  zero errors, and all 1,346 pytest tests; `git diff --check` also passed. No
  production file, environment, runtime process, systemd unit, ADB target, or
  emulator was inspected or changed. The lease-aware development ADB input
  helper remains the next delivery step and was not implemented here.
- The 2026-08-05 master-review correction
  `d70d5a340e350bc12471c8654f0b1301213bea96` closes two production-quiescence
  races. Watchdog restart and foreground recovery now retain a shared mutation
  guard from their final typed lifecycle check through completion, while hold
  installation waits on the same boundary. The blind floating-gem worker now
  dispatches synchronously, so its active state covers the complete tap and no
  queued tap can survive into an acknowledged lease.
- The post-correction watchdog, tap-dispatcher, ad-gem, interactive-lease,
  authority, application-control, and tap-safety run passed 122 tests. The
  complete non-live checkpoint again passed compilation, state-definition
  validation, clickmap integrity with zero errors, and all 1,357 pytest tests.
  No live or production inspection was performed.

### 2026-08-05 lease-aware development ADB input

- `b96531fd6c282132d33cba7418e5d41006255b31` implemented coordination
  delivery step 4 as a development-side module and thin CLI. One invocation
  accepts one canonical-coordinate tap or swipe, requires the caller's
  ordinary active lease ID, validates the complete production-owned composite
  status, establishes supported `1080x1920` or `720x1280` native geometry
  through one bounded exact-target screenshot, and rechecks the unchanged
  lease/runtime/target binding immediately before one finite-timeout input
  attempt.
- The helper never selects a default device, manages an ADB connection,
  requests or revives a lease, resumes automation, or retries uncertain input.
  It records one `ACTION`, the attempted `INPUT` with canonical/device
  coordinates and outcome detail, and one terminal `RESULT`. Its default audit
  destination is production's fixed `logs/actions.log`; an absolute-path
  override exists only for isolated tests.
- Focused helper, control-status, geometry, ADB, screenshot, and logging
  validation passed all 141 tests. The complete non-live checkpoint passed
  compilation, state-definition validation, clickmap integrity with zero
  errors and 44 established orphans, and all 1,529 pytest tests. All 31 local
  links across the six changed documents, including the new helper
  runbook anchor, resolved; staged and feature-range whitespace checks passed.
- No production checkout or environment, live runtime or process, systemd
  service, ADB server or target, emulator, current screen, production log, or
  battle was inspected or changed. Combined fake-runtime/fake-ADB coordination
  validation remains an open delivery item, and any bounded live lease remains
  separately authorized master coordination work.

### 2026-08-04 atomic shared latest production frame

- `dd44c0171c6dd1e5b0e5d090b7c08e5376e7ed3d` extended the existing screenshot
  capture/save boundary so every successful complete canonical frame is
  encoded before a task-owned sibling temporary file is atomically replaced
  over the destination. Encoding, temporary-write, and replacement failures
  preserve the prior PNG, and owned temporary files are cleaned after success
  or handled failure. Custom output paths retain support without acquiring an
  advisory sidecar.
- The default production publication remains checkout-relative and resolves to
  `/home/brianm/dev/python/TheTower/screenshots/latest.png`. Its independently
  atomic `/home/brianm/dev/python/TheTower/screenshots/latest.json` sidecar uses
  schema 1 fields `schema_version`, `captured_at`, `adb_target`,
  `native_width`, `native_height`, `canonical_width`, and `canonical_height`.
  `ScreenshotCaptureResult` now carries the UTC capture time, exact resolved
  target, and native geometry without another ADB call.
- The sidecar is advisory, may briefly lag the PNG, and grants no input or
  current-state authority. Sidecar failure preserves the valid in-memory frame
  and new PNG; capture failure leaves both prior artifacts untouched. The
  normal App call path acquired the behavior without an App change or new
  success-log noise when `log_capture=False`.
- All 19 focused screenshot-capture tests passed. The complete non-live
  checkpoint passed compilation, state-definition validation, clickmap
  integrity with zero errors, and all 1,326 pytest tests. Changed local
  Markdown links and anchors and the complete task-range whitespace check also
  passed. No production file or environment, runtime process, systemd unit,
  ADB target, emulator, merge, push, rebase, deployment, or worktree topology
  was inspected or changed.

### 2026-08-04 production/development coordination baseline

- `cdfca7a` established the trusted-single-user coordination model, canonical
  `main`/`develop`/feature-worktree topology, shared-workspace ownership rules,
  and compact active architecture while relocating the superseded
  high-assurance design to history.
- `9ddd952` established exact validated-candidate promotion, production smoke
  validation, recorded pre-deployment identity, and normal revert or
  fix-forward recovery without creating a second staging runtime. The current
  operational contract is canonical in `AGENTS.md`, `docs/new_thread.md`,
  `docs/runtime_operations.md`, and
  `docs/architecture/development_isolation.md`.

### 2026-08-04 compact trusted-user development bootstrap

- `39dacb17d8dc8aca4e6d96073d5cf88911e6d373` replaced the provisional
  high-assurance Phase-0 runner with the compact trusted-single-user contract.
  It retains the exact interpreter/platform declaration, grouped direct
  dependencies, hash-pinned deterministic locks, content-selected shared
  environments, one host-global writer lock, atomic worktree `.venv`
  selection, and the complete non-live checkpoint.
- The bootstrap now builds directly at the final schema-3 fingerprinted path
  and atomically writes a three-field completion marker only after locked
  installation, `pip check`, and content validation succeed. A later serialized
  builder may remove and rebuild only the exact marker-absent child. Completed
  valid environments are reused; completed invalid environments are reported
  without automatic mutation.
- Installed-file inventories, staged relocation and `RECORD` rewriting,
  whole-tree fsync/permissions, writable-environment rejection, adversarial
  no-follow checks, host-executable blockers, and the special Tesseract pytest
  plugin were removed. The checkpoint still isolates generated state but now
  runs the ordinary full collection with installed OCR tools available.
- All 19 focused development-environment tests passed. All three locks verified
  and regenerated byte-identically. Bootstrap recovered a deliberately empty
  marker-absent path from an absent worktree `.venv`, `status` passed, and a
  second bootstrap safely reused fingerprint
  `52fc6f62f302d9ed5f392ffb260e20d9b30cf98f4362cd240ef1569b69693ef7`.
  The earlier `776af549a562085644adb1b31d4c2d245f9d2a06caaad8cb52ce8c4712bba6b3`
  environment remained available.
- The final checkpoint passed compilation, state-definition validation,
  clickmap integrity with zero errors, and all 1,319 pytest tests with no OCR
  skips. No production environment or runtime process, systemd unit, ADB
  target, or emulator was inspected or changed; no merge, push, rebase, or
  worktree-topology action occurred.

### 2026-08-04 initial development-bootstrap prototype

- `0a17fef` implemented the initial Phase-0 prototype without
  reading packages from or mutating production's `.venv`. Exact CPython 3.12.3
  and Linux x86_64 configuration, the grouped direct dependency declaration,
  complete runtime/development locks, and the pinned bootstrap toolchain are
  tracked. The legacy standalone player-save requirement was migrated into the
  canonical `player-save` group.
- The standard-library entrypoint serializes builders beneath
  `$XDG_RUNTIME_DIR/thetower`, builds in a no-follow sibling stage, installs
  only checked lock artifacts, normalizes virtual-environment relocation,
  verifies a complete installed-file manifest, publishes an immutable
  content-addressed environment atomically, and replaces only the current
  worktree's ignored `.venv` symlink. Invalid final environments are rejected
  without in-place repair.
- The non-live checkpoint uses worktree-owned unique generated-state roots,
  blocks ADB and excluded host executables, and runs compilation, the maintained
  state/clickmap validators, and complete pytest collection. Host-prerequisite
  presence is reported by path lookup without execution; tests that actually
  require excluded Tesseract are explicit skips.
- Provisioning from an absent worktree `.venv` succeeded. After the lock
  headers were normalized, the resulting environment fingerprint was
  `776af549a562085644adb1b31d4c2d245f9d2a06caaad8cb52ce8c4712bba6b3`,
  and a second invocation safely reused it. The two-builder serialization test,
  all 19 focused bootstrap/runner tests, lock regeneration with byte-identical
  outputs, manifest/status checks, compilation, both static validators, and
  `git diff --check` passed. The final complete suite reported 1,276 passed and
  43 host-Tesseract skips. No runtime process, control state, systemd unit,
  ADB target, emulator, or volatile production state was inspected or changed.
- Later the same day, the operator clarified that TheTower is a trusted-
  single-user hobby project with no malicious-same-user or data-secrecy threat.
  The prototype remains a usable interim development entrypoint, but its
  immutable manifest, relocation, permission hardening, hostile-filesystem
  checks, and host-tool blocker are not production-promotion requirements and
  are scheduled for forward simplification. The current decision and retained
  outcomes are recorded in the
  [development coordination architecture](../architecture/development_isolation.md).

### 2026-08-03 fail-closed automatic player-save Perk-ID mapping

- A structurally valid unknown numeric Perk ID no longer has only a static
  failure path. The enabled observation-only collector now correlates the
  numeric save picks with newly accepted exact-wave Perk timeline batches,
  cancels static mappings, and resolves only unique allowlisted assignments.
  Ambiguity, low confidence, visibility gaps, interval aggregation, duplicate
  semantics, conflicts, and incomplete projections remain unavailable.
- The exact-version manifest remains authoritative and immutable. A learned
  mapping is written as a privacy-safe append-only component receipt before a
  collector-session overlay may restore the complete semantic projection. The
  overlay survives ordinary same-target Retry boundaries but not process or
  target-generation changes; restored UI checkpoints cannot replay evidence.
- The new route retains no display/OCR text, decoded save root, raw save,
  account data, arbitrary history, or pixels and grants no input, navigation,
  dispatch, lifecycle, attachment, record-construction, Strategy, or UI
  suppression authority. The 50-entry level array remains storage capacity;
  version 1073 currently has 33 observed numeric/semantic mappings rather than
  invented names for the 17 unobserved positions.
- Focused save, timeline, collector, App, Perk configuration, process, and
  single-instance validation passed 249 tests. The complete Python suite
  passed all 1,300 tests; compilation, manifest parsing, and whitespace checks
  also passed. No live process, control state, ADB target, emulator, or game
  interaction was needed or performed.

### 2026-08-03 direct-Retry player-save audit repair

- The first enabled ordinary Tier 19 collector sequence completed the core
  exact-Home, stable active-revision, natural terminal clearing/tail, and
  unchanged UI-pipeline validation. Its next direct Retry correctly failed
  closed, but revealed that the audit state machine retained the completed
  round identity and that seven legitimate version-1073 Perk IDs were absent
  from the mapping.
- `b137ea4` carries a valid terminal tail only into a tightly guarded
  same-process Retry, resets all old-round identity and Perk progression, and
  accepts the later active identity only after boundary, target, revision,
  source, identity, and tail-continuity checks. Process restarts remain
  isolated and terminal-only startup remains unbound.
- The same change maps the seven cross-channel-calibrated Perks and encodes the
  exact 18-ranked/16-unranked Auto Pick split so the inventory tail cannot be
  mistaken for priority order. The retained calibration contains only
  allowlisted Perk evidence; no raw save, decoded root, account identifier, or
  arbitrary history field is retained.
- Focused validation passed 134 tests and the complete Python suite passed all
  1,263 tests. The repair was deployed at a preserved natural Game Over
  boundary; normal `RUNNING / RETRY` operation resumed under the replacement
  process. Its first five-minute receipt accepted the new counter-232 identity
  at revision 46521/wave 290 with complete mapped Perks and the expected
  terminal-only `pre_round_baseline_unavailable` outcome. One passive ordinary
  direct-Retry receipt remains as rollout confirmation, not as an
  implementation prerequisite or special-test battle.

### 2026-08-03 managed custom Module presets and native previews

- Control-surface revision 25 adds
  `managed_custom_module_presets_v1`, authoritative rich details for every
  bundled and custom Module preset, and authenticated immutable save-as-new
  creation from either a selected preset or a profile-local definition. The
  revision-24 preset option and nested local-editor shapes remain additive and
  compatible.
- `config/loadouts/modules.yaml` remains immutable. One injected, server-owned
  custom store merges fixed-name operator files deterministically and enforces
  safe IDs, bounded no-follow reads, durable atomic creation, locking,
  deterministic crash recovery, collision/shadow rejection, and the existing
  exact eight-slot Module normalizer. Registry options, legacy summaries,
  resolution, publication, and preview metadata all use that merged catalog.
- Native Strategy Authoring now shows every selected Module preset's eight slot
  names and assigned Modules plus its bundled read-only or custom immutable
  lifecycle. **Create variant...** is available from bundled or custom
  selections, including read-only rows; **Save as preset...** uses the existing
  metadata-driven local fields. Successful editable-row creation explicitly
  selects the new preset while preserving the dormant local draft and ordinary
  Validate → Review → Publish boundary. Read-only/inactive rows retain their
  selection. Failure retains the complete draft and selections, and missing
  capability hides management controls.
- Custom-preset Base/Strategy publication, history comparison, restore-as-new,
  and plan loading retain normalized evidence and remain valid after the later
  catalog is unavailable. Preset creation cannot publish, select, or activate a
  Base or Strategy; APIs expose neither expanded plans nor filesystem paths.
- Focused authoring/storage/control-surface validation passed all 185 tests,
  the post-hardening Module store/API run passed all 42 tests, and the complete
  Python suite passed all 1,257 tests. The final portable native authoring suite
  passed all 69 tests, WPF static coverage passed all 13 tests, `git diff
  --check` passed, and Linux cross-publishing produced both standalone
  executables. No process, control state, ADB target, emulator, game, or Windows
  runtime was inspected or changed. The revision-25 package still requires the
  expanded visible disposable-catalog Windows smoke.

### 2026-08-02 observation-only natural-boundary save audit collector

- `V1073-RUNTIME-013` now provides a default-disabled CLI/environment opt-in,
  one nonblocking stable-read worker bound to the exact owned ADB target, and
  versioned append-only JSONL receipts with fresh runtime/collector session
  identities. The fail-closed state machine covers exact-Home baselines,
  same-identity revision and Perk deltas, terminal clearing, capacity-30 tail
  candidates, unknown semantic causes, duplicate suppression, and restart
  isolation.
- Receipt and pre-queue allowlists exclude raw saves/decoded roots, profile and
  account data, arbitrary history and More Stats rows, pixels, OCR, and raw
  exceptions. Confirmed visual events retain only approximate metadata and an
  optional relative image reference. Survival save checkpoints remain
  independently manifest-disabled under `V1073-RUNTIME-015`/`016`.
- The collector grants no input, navigation, lifecycle, dispatch, Strategy,
  attachment, record-construction, Perks-navigation, or UI-suppression
  authority. Existing terminal and UI evidence paths are unchanged. Focused
  validation passed 307 tests and the complete Python suite passed all 1,228
  tests. Validation was repository-local; no process, control state, ADB
  target, emulator, preserved terminal, or live battle was inspected or
  changed. The first explicitly enabled ordinary-battle receipt pass remains a
  master-owned live follow-up.

### 2026-08-02 profile-local loadout API and native editors

- Control-surface revision 24 adds the versioned
  `strategy_authoring_local_loadout_editors_v1` capability and validated,
  behavior-free nested metadata for schema-3 Modules, Target Priority, and Orb
  Distance local definitions. The pre-existing top-level preset metadata is
  unchanged, so revision-23 clients retain preset-only behavior and do not
  construct or reinterpret local definitions.
- The native WPF Strategy Authoring client now builds managed preset/local
  editors from server metadata: eight family-valid unique Module slots,
  complete unique ordered Target Priority membership, and exactly the three
  server-declared Orb Distance fields. Preset and local drafts survive form and
  Inherit/Override/Ignore transitions; Bases remain sparse and non-activatable,
  and Strategies retain their existing source semantics.
- Linux remains authoritative for normalization, validation, resolution,
  generated plans, retained definition and Base evidence, fingerprints,
  history comparison, restore-as-new, and publication. The GUI does not expose
  raw JSON, generated rules, paths, fingerprints, or executor actions, and
  publication remains separate from strategy selection and activation.
- Focused Python validation passed all 161 tests, the complete Python suite
  passed all 1,228 tests, the portable native authoring suite passed all 62
  tests, and the Linux WPF cross-publish completed successfully. No process,
  control state, ADB target, emulator, game, or Windows runtime was inspected
  or changed. The disposable-catalog Windows runtime smoke remains the next
  unchecked worker.
- Follow-up `7e4c7a2` replaces the Module editor's transient option-collection
  reset with server-ordered incremental reconciliation. Every refresh event
  now retains the field's selected object while continuing to exclude peer
  selections; null and undeclared choices still fail closed. The portable
  native suite passed all 63 tests, the 61 focused WPF/authoring/API Python
  tests passed, and Linux cross-publishing produced both executables. The
  2026-08-03 Windows attempt stopped before validation or publication, so the
  visible eight-slot disposable-catalog retest remains pending.

### 2026-08-02 profile-local loadout definition backend

- Sparse authoring schema 3 now gives Modules, Target Priority, and Orb
  Distance one exact preset-or-local value contract shared by Bases and
  Strategies. Existing authoritative normalizers enforce the complete
  eight-slot Module mapping and module families, the complete unique ordered
  target list, and the three normalized Attack Range/Extra Orb/Workshop
  distance fields. Shared presets remain supported.
- Effective resolution retains a fingerprinted definition snapshot; Orb
  snapshots also retain every range relationship consumed by the generated
  selection/preserve action. Immutable Base revisions store their definition
  resolution, and new Strategy publications embed that Base resolution plus
  every final effective snapshot. Current validation, semantic history review,
  and restore-as-new use retained evidence after a Base or shared preset is
  changed or removed.
- Schema-2 sources/publications remain exact compatibility evidence and are not
  rewritten. Any prospective schema-1/schema-2 edit upgrades to self-contained
  schema 3 before publication. The protected Farm builder preserves the exact
  bundled and retained preset plan structure while local definitions produce
  equivalent runtime requirements/actions with honest local provenance.
  Publication remains separate from activation, and expanded plans and paths
  remain redacted.
- This backend-only commit leaves the revision-23 API capabilities and the
  native preset editor unchanged, so installed preset-only clients remain
  safe. Additive API discovery, managed WPF preset/local editors, and Windows
  runtime smoke remain active follow-up work.
- The dedicated local-definition suite passed all 15 tests and the complete
  Python suite passed all 1,186 tests. `git diff --check` passed. Validation was
  repository-local: no process, control file, ADB target, emulator, game, or
  Windows runtime was inspected or changed, and the untracked operator-owned
  `playerInfo.dat` remained untouched and unstaged.

### 2026-08-02 fail-closed terminal run binding

- Commit `6a81605` prevents a terminal-only process restart from assigning the
  selected Strategy or restored process-local evidence to a battle that the
  current process never observed active in the settled activity scope.
  Unbound terminal records retain valid Game Stats, Perks, and More Stats while
  omitting configuration, wave/coin/speed samples, preflight evidence, Perk
  timeline, and survival activations; restored trackers are cleared and the
  warning plus versioned binding reason remain in JSON and Markdown.
- Focused validation passed 157 tests and the complete Python suite passed all
  1,171 tests. A bounded live replay on the preserved Tier 22 wave-751 Boss
  Game Over screen captured 11 Perk rows and all 144 More Stats rows into a
  valid `unknown` record with `strategy=null`, empty run configuration, and no
  stale Tier 19 timeline. The 49-batch checkpoint reset to zero. Automation
  remained active in `RUNNING / WAIT`; no Home, Retry, Surrender, or
  Tournament input occurred. The contaminated record pair was recoverably
  quarantined until 2026-09-01, the corrected record is the sole Battle History
  entry for this boundary, and `playerInfo.dat` remained untouched.

### 2026-08-02 immutable Strategy history and safe fallback

- Every validated custom Strategy publication now appends a complete immutable
  logical revision while atomically advancing the fixed latest-file runtime and
  older-client facade. A fingerprint-bound journal, immutable stages, history
  and latest directory syncs, explicit commit point, pre-commit rollback, and
  deterministic reopen reconciliation prevent truncation, phantom revisions,
  duplicate retry history, and post-commit cleanup ambiguity. History remains
  authoritative for version allocation after retirement, so a stable ID cannot
  silently restart a different lineage.
- Exact schema-1 and schema-2 latest publications and unambiguous retirement
  evidence are adopted idempotently without rewriting source evidence or
  inferring inheritance. Malformed, duplicate, conflicting, misnumbered,
  symlinked, or unknown history/transaction evidence is preserved and reported
  while a separately valid latest facade remains runtime-loadable.
- Revision 23 adds `strategy_revision_history_v1` while retaining every older
  endpoint and capability. New history endpoints return newest-first review
  summaries and individual redacted revisions; Linux owns semantic source,
  effective/provenance, Base snapshot, override/Ignore, generated-plan/rule-
  count, metadata, and current-validation comparisons. Expanded plans and
  filesystem paths never enter API responses.
- WPF adds a discoverable **History** window for active and retired custom
  lineages. Fingerprint-bound restore review uses the retained embedded Base and
  current trusted builder; explicit confirmation publishes historical intent as
  the next immutable revision. Preview/conflict writes nothing, the open draft
  is preserved, and publication never selects or activates a Strategy, restarts
  automation, changes Pause, or mutates runtime control.
- Focused Strategy store, authoring API, control-surface, and WPF coverage passed
  all 129 tests; the complete Python suite passed all 1,168 tests; and the
  portable native authoring suite passed all 53 tests. Linux cross-publishing
  produced both standalone Windows executables, with only the known read-only
  NuGet vulnerability-cache warnings, and `git diff --check` passed. No live
  process, control file, ADB target, emulator, or battle was inspected or
  changed, and the untracked operator-owned `playerInfo.dat` was not modified or
  staged.

### 2026-08-02 running-battle Strategy Action Gate

- The runtime now owns one typed four-class action-authority matrix for passive
  observation, explicitly allowlisted auxiliary collection, strategy actions,
  and lifecycle transitions. A terminal running-battle validation mismatch
  becomes a battle-scoped Strategy Gate without mutating Pause. Natural
  boundaries, validated retry/waiver/success, explicit active-strategy changes,
  and separately authorized repairs remain the only release transitions; the
  gate itself grants no Surrender, Exit Battle, restart, Go Home, or New Battle
  authority.
- Capture, detection, OCR/state/wave updates, activation tracking, passive
  evidence, and status continue under the gate. Daily Gem and mission reward
  routes retain their schedulers and limits, claim exclusive same-battle
  ownership before input, recheck screen/control/scope/authority at every
  dispatch, and retain only collector-owned cleanup after interruption. The
  in-battle ad-gem and floating-gem workflows use the same typed guard; a
  regression with an intentionally delayed 200 ms guard proves the one-second
  blind-tap cadence does not accumulate guard latency.
- Revision 22 adds `strategy_action_gate_v1` while retaining every older
  capability and endpoint. `/api/v1/status` serializes the fresh atomic
  PID/ADB-owned gate snapshot with explicit staleness behavior. WPF presents a
  separate amber Strategy Gate banner with reason, failed checks, and allowed
  collectors, while its Automation/Pause state remains unchanged.
- Focused runtime, preflight, reward, tap-safety, status, control-surface, API,
  compatibility, and WPF coverage passed all 295 tests. The complete Python
  suite passed all 1,142 tests. Linux cross-publishing produced both standalone
  Windows executables; only the sandbox's known read-only NuGet
  vulnerability-cache warnings were emitted.
- The operator reported on 2026-08-02 that the available phase-three Windows
  runtime smoke checks completed with no blocking issue reported. This was not
  exhaustive Windows validation. Development did not inspect or interact with
  a live process, control file, ADB target, emulator, or battle, and the
  untracked operator-owned `playerInfo.dat` was not modified or staged.

### 2026-08-02 save-first history-tail contract correction

- Runtime-save schema 2 now separates a privacy-safe structural identity and
  fingerprint for the newest source-ordered Battle History entry from the
  optional semantic 144-row completed-battle projection. Future unknown
  `killedBy` values preserve tail-change evidence while blocking semantic
  publication; malformed newest entries still fail both components closed.
- DateTime values retain their individual UTC/local kind and clock basis. The
  decoder no longer compares masked ticks across kinds, and exact newest-entry
  validation plus 30-entry rollover handling replaces the invalid whole-list
  chronology assumption.
- Version-1073 mappings now include cross-channel-proven `3=Boss`,
  `6=Vampire`, and `99=Surrender`. Surrender describes the terminal cause only
  and does not attribute its initiating actor. The revision-2 audit matrix does
  not promote terminal attachment, record publication, final-Perk authority,
  polling, or navigation suppression.
- The next slice is designed as an explicitly enabled, read-only
  natural-boundary audit collector. Its allowlisted receipts may capture stable
  identity/Perk/tail transitions, but cannot attach a completed entry, update a
  battle record, decide Perks navigation, send input, or suppress UI behavior.
- Synthetic player-save regressions and the focused battle-stats/Game Over
  suite passed all 82 tests; the complete repository suite passed all 1,106
  tests. Read-only diagnostics accepted the mixed-kind operator save and all
  three retained capped active snapshots. No raw save was copied, modified,
  staged, or committed, and no process, ADB target, emulator, or battle was
  inspected or changed.

### 2026-08-02 custom Strategy rename and recoverable deletion

- Commit `6a7e86f` makes custom Strategy renaming discoverable while retaining
  the existing reviewed publication boundary: only the display name changes,
  the stable ID remains fixed, and publication advances the logical version
  without selecting or activating the Strategy.
- The revision-21 `strategy_authoring_profile_lifecycle_v1` contract adds one
  allowlisted `retire_strategy` operation. It requires the source fingerprint
  loaded by the editor, refuses bundled/reserved or currently selected
  Strategies, and moves the exact publication into the server-owned
  recoverable archive under the existing catalog writer lock. Both new and
  legacy active catalogs refresh without exposing generated plans or accepting
  client paths. Managed history/restore remains in the safe-fallback backlog.
- WPF adds explicit **Rename Strategy** and confirmed **Delete Strategy...**
  affordances only for editable custom Strategies. The native README's
  disposable Windows smoke now covers opening the authoring window, rename
  round trips, cancellation, selected-Strategy refusal, archive-backed
  deletion, and non-activation. No Windows runtime was available, so that
  manual smoke remains pending rather than being claimed from compilation.
- Focused authoring/profile/control-surface coverage passed 75 tests; the
  portable native suite passed all 51 tests; and the complete Python suite
  passed all 1,112 tests. Linux cross-publishing produced both standalone
  Windows executables (with sandbox-only read-only NuGet vulnerability-cache
  warnings), and `git diff --check` passed. Legacy schema-1 tests now create
  disposable deterministic publications instead of reading the operator's
  mutable custom catalog. No live process, control file, ADB target, emulator,
  battle, operator profile publication, or `playerInfo.dat` was inspected or
  changed.

### 2026-08-01 save-first runtime normalization foundation

- This commit adds snapshot-schema-2 runtime evidence for exact mapping
  `data-9-game-1073`: privacy-safe capture/revision metadata, the guarded
  active-round identity tuple, exact ordered in-battle Perks with ID `0`
  normalized as Max Health, and a stable fingerprinted Battle History tail.
- The completed-history model validates the chronological capped list and exact
  148-field entry shape, then exposes only the mapped 16-section/144-row More
  Stats projection. Unknown versions, changed structures, unknown Perk or
  `killedBy` IDs, inconsistent Perk count/list/levels, and malformed history
  entries fail closed without publishing partial component evidence.
- The canonical runtime architecture and the consolidated version-1073
  revision-1 audit matrix keep new-round causality, five-minute freshness,
  post-run Perk retention, Game Over serialization/tail attachment, the full
  `killedBy` enum, coin-split augmentation, record construction, and navigation
  suppression as explicit later work. No `App` or handler dispatch changed;
  all UI readers, mutations, terminal controls, forced audits, and fallbacks
  remain intact.
- Tests use only synthetic decoded mappings and the existing UI report shape;
  no real save was added. The focused player-save, battle-stats, and Game Over
  suite passed 77 tests, and the complete repository suite passed all 1,101
  tests. Validation was repository-local and did not inspect or interact with
  a live process, control file, ADB target, emulator, or battle.

### 2026-08-01 specialized Strategy Authoring editors

- This commit advances Linux and the native client together to revision 20 and
  adds `strategy_authoring_specialized_editors_v1` without removing any prior
  capability or endpoint. Registry entries now serialize validated,
  behavior-free editor metadata and a normalized initial value; normalizers,
  resolution, generated-plan ownership, and runtime actions remain in Python.
- WPF now provides managed or explicitly fixed presentations for all nine
  registered editor families. Card mappings, exact and variable lists, Perk
  limits/order/dependencies, presets, constrained booleans, server-normalized
  percentages, and Ultimate Weapon toggles are metadata-driven. Dormant Ignore
  values and unknown retained Ultimate Weapon groups/fields round-trip without
  exposing raw JSON. Computed display bindings are explicitly `OneWay`, fixing
  the native runtime failure that occurred while opening Strategy Authoring;
  the view-model properties remain read-only.
- Focused Python authoring/profile/control-surface coverage passed 93 tests;
  the portable native view-model suite passed 50 tests across every editor and
  Base/Strategy source-state transition; the native project built with zero
  warnings; and the complete shared suite passed all 1,084 tests. Linux
  cross-publishing produced `TheTower.ControlSurface.exe` and
  `TheTower.TunnelHost.exe`. No Windows runtime was available, so the README's
  disposable-catalog smoke checklist—including actually opening Strategy
  Authoring—remains required. Validation did not inspect or change live
  process, control, ADB, emulator, battle, or operator profile state.
- Initial follow-up `10853ee` corrected the global ComboBox foreground after
  the operator reported black text on dark blue, but a second Windows
  screenshot proved property setters did not control the platform's disabled
  template chrome or disabled RadioButton labels. Commit `6e85c2c` now owns the
  complete ComboBox/ComboBoxItem templates and explicit enabled/disabled choice
  label foregrounds. Its focused suite passed all 50 tests, the native project
  built without warnings, and Linux cross-publishing again produced both
  expected executables; Windows visual confirmation remains in the smoke
  checklist.
- Follow-up `26c3a17` exposes the backend's reviewed first-Base attachment for
  an editable existing no-Base Strategy, including the legacy
  `farm_t19_custom` profile. The picker remains server-catalogued, publication
  is blocked until the exact semantic review is accepted, the Strategy ID is
  retained, and activation remains unchanged. Focused coverage passed 96
  tests, the portable C# suite passed 51 tests, the complete Python suite
  passed all 1,108 tests, and Linux cross-publishing produced both expected
  executables.

### 2026-08-01 versioned Tournament conditions and record attachment

- This commit adds the exact `data-9-game-1073` Legend Tournament generator.
  It reproduces the game's seeded `System.Random` and condition pools, emits
  stable IDs plus conventional aliases, and fails closed for unknown versions,
  unvalidated leagues, stale registry dates, or conflicting save identities.
  Tournaments 271–287 match all 16 operator-supplied historical rows and the
  live Tournament 287 Heat/Overheat inspection without a condition mismatch.
- Schema-version-2 Tournament records retain the complete normalized Heat and
  fixed Overheat identity inventory, event number, source version, and
  provenance. Terminal capture performs a bounded stable save read without UI
  input; missing evidence remains explicit and cannot invalidate or block the
  result. A duplicate result can be enriched without reopening detail controls.
- A dry-run-first explicit-UTC-date tool backfilled all six existing canonical
  Tournament JSON/Markdown pairs for events 283, 284, 285, and 287. The first
  apply reported six updates and no conflicts; the second reported all six
  unchanged. The operator-owned raw save remained untracked and was not copied
  into tests, logs, or committed evidence.
- Focused save, generator, result, and handler validation passed 52 tests. The
  full shared-worktree suite passed 1,074 tests; its six failures were confined
  to concurrently edited strategy-authoring/control-surface files outside this
  change.

### 2026-08-01 persistent per-user Windows tunnel host

- Commit `82ed42a` replaces GUI-owned OpenSSH processes with the on-demand,
  headless `TheTower.TunnelHost.exe`. A current-user SID-derived singleton and
  versioned `PipeOptions.CurrentUserOnly` named pipe let a reopened GUI recover
  desired and observed state, child PID, endpoint, retry/conflict state, and raw
  SSH diagnostics while desired API and ADB forwards survive GUI closure.
- The host keeps API and loopback-only ADB forwarding in independent
  supervisors, owns only the fixed `thetower-control-surface.service` SSH
  status/actions, persists validated configuration without desired state, and
  exits after a bounded idle period when no tunnel or GUI requires it. A
  kill-on-close Windows Job Object owns every SSH child; arbitrary pre-existing
  SSH processes are neither discovered nor adopted.
- The GUI handles protocol mismatch and confirmed companion replacement
  explicitly, without replaying tunnels. Publishing now stages and validates a
  complete two-executable package. There is no Windows service, login startup,
  tray UI, combined forward, BlueStacks control, or broader remote-command
  authority.
- All 17 protocol/core lifecycle tests, all 41 control-surface regressions, and
  the complete 1,043-test repository suite passed. Linux cross-publishing
  produced only `TheTower.ControlSurface.exe` and `TheTower.TunnelHost.exe`.
  Validation was code-only and did not inspect or change live process, control,
  service, ADB, emulator, or battle state; the documented WPF, Windows
  OpenSSH, access-token, Job Object, forced-exit, and logoff checks remain
  required on Windows.

### 2026-08-01 additive strategy-authoring API and editor shell

- This commit advances the Linux control surface and native client together to
  revision 19 with `strategy_authoring_v1`. The additive endpoint exposes
  separate Base and Strategy catalogs plus validate/publish operations for each
  and backend-computed rebase previews, while the revision-18 profile facade
  and its capabilities remain unchanged for older clients.
- Base publication appends an immutable revision under optimistic fingerprint
  protection. Strategy publication embeds the pinned Base snapshot but never
  activates it. Semantic review reports Base additions/removals/changes,
  inherited effective changes, stable local overrides and ignores, dependency
  or builder errors, source/effective diffs, provenance, rule count, and
  fingerprints without returning the expanded generated plan.
- The WPF Strategy Authoring shell groups Bases and Strategies, uses registry
  sections and capabilities for source-state rows, shows server-resolved values
  and provenance, filters active/all settings, supports safe simple and Perk
  controls, and preserves complex values through a read-only lossless fallback.
  New Strategies can pin a latest compatible Base; changing a published pin
  requires an explicit reviewed rebase. Review & Publish states that publication
  does not activate a Strategy, and stale conflicts retain the open draft.
- Focused authoring/profile/control-surface coverage passed 78 tests, the
  complete repository suite passed all 1,043 tests, and the Linux cross-publish
  produced the complete self-contained Windows package. Validation did not
  inspect or change the live process, control state, ADB target, emulator, or
  battle.

### 2026-08-01 card recharge save calibration

- Commit `0aa4df7` maps `demonModeAutomateToggle` and
  `nukeAutomateToggle` into the version-1073 `card_recharge_modes` check and
  adds that complete check to the candidate mapping's per-check validation
  allowlist. A fresh, complete matching snapshot can now produce `save_match`;
  changed types, mismatches, unverified freshness, and forced audits retain the
  existing UI fallback.
- Bounded no-battle testing independently produced Demon Mode
  `true -> false -> true` and Nuke `false -> true -> false` across app-pause
  serialization boundaries. `true` means auto-reactivate for both fields.
  `currentPreset`, Missile Barrage, and the other card's boolean remained
  unchanged during each mutation. Final UI evidence showed Demon Mode on
  auto-reactivate, Nuke ready after recharge, and Home at `NEW_BATTLE`.
- The live test exposed a valid 342-pixel post-toggle checkbox outline below
  the old 350-pixel cutoff. The detector now accepts a 300-pixel outline only
  when the card-detail identity and independent checkmark evidence also pass;
  a synthetic regression reproduces the observed variance.
- The complete remaining field matrix, evidence standard, profile-validation
  scope, audit-only adoption, incremental navigation suppression, snapshot
  invalidation, and scheduled-audit plan are maintained in
  [`player_save.md`](../architecture/player_save.md#complete-validation-program)
  and the active runtime backlog. The focused player-save/card suite passed 39
  tests, and the complete repository suite passed all 1,040 tests. The
  operator-owned raw save remained untracked and was not copied into
  repository evidence.

### 2026-08-01 backend strategy authoring model

- This commit implements the backend slice of the sparse strategy-authoring
  architecture: an immutable Farm setting registry, sparse versioned base
  revisions, sparse strategy sources, generic policy resolution with
  provenance, and schema-2 self-contained publications with source, base,
  resolution, and plan fingerprints.
- Strategy publications pin and embed the exact base snapshot, while later
  base revisions are append-only and do not propagate. The runtime loader
  validates and consumes only the embedded resolution and generated plan;
  bases remain non-activatable and publication remains separate from existing
  activation controls.
- Schema-1 custom profiles remain readable without rewrite and convert in
  memory to explicit local directives. Legacy `preserve` policies and durable
  skipped checks become explicit ignores, and matching values are never
  inferred as inherited. Repository Farm T18/T19 sources and the retained
  schema-1 custom publication regenerate their exact protected plans and run
  configuration through the shared builder.
- Focused authoring/profile/builder coverage passed 28 tests, the broader
  profile, run-initialization, control-surface, and Farm compatibility set
  passed 163 tests, and the complete repository suite passed all 1,027 tests.
  Validation was repository-local and did not inspect or change the live
  process, control state, ADB target, emulator, or battle.

### 2026-08-01 live player-save Perk calibration

- Commit `48f7f23` cross-validates the `data-9-game-1073` candidate mapping
  against game `28.3.1` UI evidence at one new-battle Home boundary. Cards,
  Workshop, Bots, First Perk, six Ban Perks, all 18 ranked Auto Pick rows,
  Guardians, and the three automation-managed Free Upgrade locks agreed with
  stable save reads.
- The comparison corrected the candidate perk IDs after Auto Pick rank 9 and
  mapped ID `21` to Swamp Radius. Ban observation now follows selected-tile
  outlines, including the dark green Swamp Radius row, while Auto Pick
  observation reads every category color only up to the Rankings Unlocked
  divider. The live audit pages replayed with six authoritative bans and 18
  authoritative ranks without warnings; only synthetic structural regressions
  were added to the repository.
- The mapping remains `candidate`: Target Priority, all possible Free Upgrade
  locks, Ultimate Weapon detail polarity, and unranked Auto Pick IDs remain
  pending, so the existing UI path is still required for every check. The live
  audit did not select a preset, change configuration, or start a battle.
- Focused Home/Perks validation passed 108 tests, and the complete repository
  suite passed all 1,011 tests.

### 2026-08-01 native GUI API-service and SSH health controls

- Commit `6660ac8` adds always-visible, independent status for the fixed Linux
  control API service, HTTP reachability, the Windows-local API SSH forward,
  and the ADB reverse-forward SSH process.
- The GUI can query, start, stop, or restart only
  `thetower-control-surface.service` through fixed bounded SSH commands. Stop
  and restart require confirmation, and neither action changes main automation,
  the emulator, or either SSH tunnel. API and ADB tunnels also have independent
  top-bar restart actions.
- Focused control-surface validation passed 40 tests and the Linux cross-publish
  produced the self-contained `win-x64` application. Validation did not inspect
  or change the live process, control state, ADB target, emulator, or battle.
- A per-user companion tunnel host remains an explicit follow-up; the current
  GUI still owns and closes both `ssh.exe` children.

### 2026-08-01 expanded GUI strategy profile editing

- Commit `f942a5d` advances the Linux control surface to revision 18 and adds
  durable custom-profile skips for Auto Pick enabled, Perk Bans, and Auto Pick
  priority. These profile-owned decisions are distinct from one-run waivers,
  participate in the generated strategy fingerprint, reapply at each run, and
  prevent corrective input to the skipped Perk controls.
- Custom publications now retain a complete Farm setup snapshot. The native
  editor adds zero-to-six Perk Ban selection plus add/remove/reorder controls
  for Auto Pick priority while preserving every setup value that does not yet
  have a dedicated control. Bundled plans remain byte-for-structure equivalent,
  and publishing still neither selects nor activates the custom profile.
- Runtime, builder, API, compatibility, and Windows-editor regressions passed
  with the complete 1,006-test repository suite. Linux cross-publishing also
  produced the self-contained Windows executable. Validation was
  repository-local and did not inspect or change the live process, control
  state, ADB target, emulator, or battle.

### 2026-08-01 restart-stable session configuration checks

- Commit `f5b137b` records a completed session-preflight receipt in the
  Current-run scope, bound to the selected strategy and an exact fingerprint
  of its session assertions, requirements, fallbacks, and generated gate
  rules.
- A replacement process reuses that receipt only after the Battle History
  continuity compare proves the persisted scope still represents the same
  battle. Missing or mismatched receipts, a later completed battle, unreadable
  History, or a failed scope compare retains the declared attachment checks.
  Reuse suppresses attached gate rules without fabricating volatile completion
  variables.
- Focused logger, continuity, startup-gate, and Tournament validation passed
  151 tests, and the complete repository suite passed all 1,002 tests.
  Validation was repository-local and did not inspect or change the live
  process, control state, ADB target, emulator, or battle.

### 2026-08-01 Tournament attachment gate release

- Commit `a8dda82` preserves the attached Tournament inventory pass as the
  first exclusive check, then admits the plan's explicit battle-only attached
  rules. Damage Slider can now be enforced at `100%`, Orb Distance can be
  enforced or safely preserved from authoritative Attack Range evidence, and
  the completed session gate releases normal handlers.
- Tournament ad-gem collection continues through the same handler as Farm. A
  visible ad gem starts one bounded 20-second floating-gem sweep; no independent
  continuous Tournament tapper was restored.
- Regressions cover the staged attachment plan and the main-loop transition
  from blocked validation to visible ad-gem dispatch. Focused validation passed
  182 tests, and the complete repository suite passed all 997 tests.
- A guarded live rollout replaced PID `3470028` with `3509151` in the same
  Tournament. The replacement verified Damage Slider `100%`, corrected and
  verified Orb Distance, completed session validation, collected the visible
  ad gem, and terminated the Farm-compatible floating-gem scan after its
  bounded 20 taps. The Tournament remained active and `RUNNING` throughout the
  completed handoff; it was not Surrendered or restarted.

### 2026-08-01 stale offline ADB transport classification

- Commit `0346a1b` closes the stopped-BlueStacks case where a still-open SSH
  reverse listener left `localhost:5555 offline` and `adb connect` misleadingly
  reported `already connected`.
- A reconnect attempt now refreshes only the selected TCP transport and must be
  followed by an exact-target `device` observation. Offline, unauthorized, and
  absent targets use the shared bounded outage schedule; recovery remains
  gated on a supported fresh screenshot.
- Regression coverage reproduces the success-hint/offline-state conflict,
  constrains disconnect/reconnect to one target, and verifies long paused
  outages suppress capture noise. The focused suite passed 24 tests, the
  broader runtime/control suite passed 214, and all 995 repository tests passed.
  The live operator-owned runtime remained paused and was not reloaded while
  its target was offline.

### 2026-08-01 game-speed OCR transition hardening

- Commit `852febf` requires two agreeing, directionally consistent readings
  after every game-speed input. One impossible `x3.0` read can no longer turn
  an `x5.0` ceiling probe into a false decrease, while matching progress reads
  may reach consensus across intermittent OCR gaps.
- Post-input OCR uncertainty is reported as deferred with raw diagnostic
  evidence instead of as a completed adjustment failure. A proven normal
  `x5.0` maximum now survives transient read failures, preventing redundant
  ceiling probes until a target or battle boundary resets that proof.
- The focused game-speed suite passed 26 tests and the complete repository
  suite passed all 993 tests. Live activation was intentionally deferred when
  fresh evidence showed operator-owned Pause and a Welcome Back / resume
  dialog instead of the active `RUNNING` state required by the guarded reload.

### 2026-07-31 versioned player-save observation channel

- Commit `174ce10` adds bounded gzip/NRBF decoding, exact
  `(dataVersion, versionNumber)` mapping selection, structural signatures, a
  redacted normalized profile snapshot, and per-check reconciliation that
  always names the existing UI implementation as its fallback.
- The first `data-9-game-1073` mapping remains `candidate`; even matching save
  values require the full UI audit. Unknown versions, shape changes, stale
  snapshots, mismatches, and explicitly unmapped settings fail closed to UI.
  Stable ADB acquisition requires two identical consecutive reads, and the raw
  operator save is never copied into repository evidence.
- Focused decoder, ADB transport, and capture validation passed 30 tests. The
  complete repository suite passed 988 tests. A read-only local inspection of
  the untracked operator sample confirmed the exact mapping and its five
  28-slot card-preset records; no process, device, control, or battle state was
  inspected or changed.

### 2026-08-01 incremental player-save trust and serialization boundary

- Commit `1fca2a8` replaces the all-or-nothing mapping maturity gate with an
  exact per-check validation allowlist. Candidate mappings may now supply an
  authoritative match only for an allowlisted check with complete evidence
  and an explicitly verified save-serialization boundary; every mismatch,
  incomplete value, unvalidated check, unverified-freshness pull, and forced
  audit still names the existing UI checker as its fallback.
- The first mapping validates Cards, Workshop, and Bots preset selection;
  First Perk; Ban Perks; and equipped Guardians. The overall mapping remains
  candidate. Auto Pick's unranked tail, Target Priority, all possible Free
  Upgrade locks, and the combined Ultimate Weapon check remain UI-required;
  confirmed Poison Swamp Stun polarity does not authorize the unresolved
  values in that combined check.
- Bounded live mutation established that visible Cards slots are stored as
  zero-based `currentPreset` indices and that Poison Swamp Stun uses the
  inverted `poisonSwampStunOff` boolean. Waiting and returning Home did not
  serialize the Cards change; an Android app pause did, without force-stop.
  Both settings were restored through the same flush boundary, and final
  evidence showed no-battle Home with Stun on and Tournament Cards selected.
- Focused player-save validation passed 18 tests, and the complete repository
  suite passed all 1,028 tests. The operator-owned raw save remained untracked
  and was not copied into repository evidence.

### 2026-07-31 constrained GUI Strategy Profile Builder

- Commit `f22d85d` adds a versioned custom-profile catalog shared by the Linux
  control service, managed-process configuration, control directives, and
  runtime strategy loader. Valid custom Farm publications contain their compact
  source and exact generated plan in one fingerprinted document beneath the
  fixed `config/strategies/custom` directory; advisory locking, stale-revision
  rejection, `fsync`, and atomic replacement protect concurrent publication.
- Linux server revision 17 adds the allowlisted strategy-profile catalog and
  validate/publish endpoints. The native WPF client now populates strategy
  selection dynamically and provides a Strategy Profiles window that can clone
  bundled Farm templates, edit Tier loadout policies, validate without writing,
  and publish without selecting or activating the result. Bundled profiles,
  shared Farm invariants, preset catalogs, Tournament policy, raw rules, and
  executor actions remain outside the editor's write surface.
- Regression coverage verifies catalog and preset exposure, normalization,
  atomic/versioned publication, stale-write conflicts, tamper exclusion,
  dynamic runtime loading, managed-service selection, control-file selection,
  HTTP response boundaries, and the publish-versus-activate separation. The
  complete repository suite passed 973 tests, and the Linux cross-publish
  produced the self-contained Windows executable successfully. Validation was
  repository-local and did not inspect or change the live process, ADB target,
  emulator, control state, or battle.

### 2026-07-31 Tournament Module reference observation

- Commit `6e69437` changes Tournament Modules from an enforced loadout to an
  observed `tournament_standard` reference. Every equipped slot still requires
  authoritative identity evidence, but a confident difference is named in the
  successful preflight result without changing the loadout or warning as an
  invariant failure.
- Enforced Tournament settings retain mismatch behavior, and missing or
  ambiguous Module identity remains incomplete evidence. Focused validation
  passed 98 tests and the complete repository suite passed 971 tests.
- The implementation audit confirmed that Module replacement preserves
  slot-owned levels through guarded transfers but does not capture, retain, or
  threshold-check the numeric equipped levels. Follow-up validation for Primary
  level 201+ and Assist level 195+ is recorded in the active runtime backlog.

### 2026-07-31 restart-stable Perk timeline catch-up

- Commit `07efc5a` atomically checkpoints Perk timeline progress beside the
  runtime control file and restores it only when the Current-run activity
  scope still identifies the same battle. A different scope establishes a
  fresh, non-attributing mid-battle baseline.
- Every scheduled observation now scans the Perk list newest-first until it
  reaches the first persisted family/value that has not changed. This captures
  an arbitrary number of distinct skipped selections, with a proven-bottom
  full-diff fallback when no unchanged row remains. Visibility or process gaps
  stay interval aggregates rather than receiving invented wave attribution;
  repeated upgrades to one leveled family are recoverable only as their net
  change.
- Regression coverage exercises same-scope restoration, persisted route
  ownership, scope-mismatch rejection, restart catch-up, arbitrary jumps,
  scanning past a changed former newest row, early scroll termination, and
  report rendering. The complete repository suite passed 958 tests; after the
  final review fixes, the focused timeline, scrolling, reporting, and run-
  initialization suite passed 149 tests. Validation was repository-local and
  changed no process, control, ADB, emulator, or battle state.

### 2026-07-31 automatic-Retry activity continuity

- Commit `2ce357d` starts the next Current-run scope immediately after a
  verified automatic Retry and persists the preceding completed-battle
  fingerprint as pending comparison evidence.
- After run initialization and session preflight, continuity polls the newest
  Battle History entry. A stale prior row releases normal battle actions and
  schedules another bounded poll; an advanced row becomes the baseline of the
  existing Retry scope instead of creating an attachment scope.
- Focused logger, Game Over, activity-continuity, and run-initialization
  coverage passed 129 tests, followed by all 950 repository tests. The ledger
  repair sent no game input and did not restart the battle; it restored the
  verified 07:23 Retry boundary while preserving the correct History identity
  and opaque scope ID.
- A guarded attached-battle reload then loaded the fix and restored `RUNNING`.
  The replacement runtime scrolled History to its proven top, recopied the
  unchanged Tier 19 wave-4903 entry, returned to battle, and preserved the
  repaired scope.

### 2026-07-31 Tournament Battle History rail matching

- Commit `df25656` widens only the running Battle History row's horizontal
  template region so it covers both the ordinary battle rail's right column
  and the Tournament rail's left column.
- A retained Tournament fixture now proves the exact `(909,696)` match center;
  28 focused Battle History and clickmap checks passed, followed by all 959
  repository tests.
- A guarded attached-battle reload preserved `RUNNING`, the Tournament
  strategy, and the existing activity scope. The live continuity pass matched
  the corrected control, proved the list top, recorded the latest completed
  Tier 19 wave-20 battle in that same scope, and returned to the active battle
  without Surrendering or restarting it.

### 2026-07-31 GUI-managed reverse ADB forwarding

- Commit `3ac1d88` preserves the Windows-local API forward and adds a separate
  GUI-owned OpenSSH process for
  `-R 127.0.0.1:<linux-port>:127.0.0.1:<windows-port>`.
- Windows BlueStacks and Linux-exposed ADB ports are separate persisted
  settings that both default to 5555, allowing distinct Linux loopback ports
  for several PCs. The managed runtime target remains an independent explicit
  setting.
- The Setup tab reports Windows TCP-listener presence separately from accepted
  remote forwarding, retains `ExitOnForwardFailure` diagnostics, isolates API
  control from ADB conflicts, pauses retry on bind/policy failures, and applies
  bounded reconnect backoff to other unexpected ADB-tunnel exits.
- `test/test_control_surface.py` passed 39 focused tests, and
  `windows/TheTower.ControlSurface/publish-linux.sh` successfully produced the
  self-contained Windows executable. Validation was repository-local; no live
  runtime, ADB target, or emulator was inspected or changed.

### 2026-07-31 coalesced ADB outage retries

- Commit `5548835` gives main-loop capture and the watchdog one thread-safe,
  target-keyed connection coordinator with bounded reconnect backoff.
- Known disconnection suppresses repeated screenshot commands and transport
  failure logs while control polling continues every two seconds. Persistent
  warnings remain rate-limited, and recovery is recorded once only after a
  supported fresh frame succeeds; connected corruption retains diagnostics.
- Explicit paused handoffs validate the requested target against its own
  connection state without discarding the former target's outage schedule.
- Regression coverage spans long Pause-plus-outage, concurrent callers,
  recovery, target switching, connected corruption, handoff, and watchdog Pause
  authority. All 946 repository tests passed; no live runtime or emulator
  change was performed.

### 2026-07-30 Demon Mode Intro Sprint activation guard

- Commit `4ac2fc0` makes the independently visible top-left Intro Sprint status
  veto Demon Mode disappearance until five consecutive clean status absences
  confirm that the sprint ended. Obscured or failed status matches reset that
  streak instead of being treated as absence.
- The tracker keeps the existing whole-region button matcher, so Demon Mode,
  Nuke, and Missile Barrage may reflow among their three slots without creating
  a fixed-position assumption.
- `test/test_battle_activation_tracker.py` covers the retained wave-480 false
  notice, the post-activation comparator, synthetic third-to-second-slot
  reflow, and intermittent status-match failures. All 938 repository tests,
  recursive clickmap integrity, and state-definition validation passed.
- Validation used retained fixtures only; no live battle, emulator, runtime, or
  control state was changed.

### 2026-07-30 paired-cycle EHLS/EALS startup

- Commit `b8229d5` removes the ineffective production H.264 warm-up and makes
  the guarded screenshot path the default for the short level-skip startup
  race.
- The fallback now keeps EHLS first while beginning a bounded EALS burst in
  the same feedback cycle. Independent four-tap EHLS and eight-tap EALS caps
  prevent capture starvation without serializing EALS behind an EHLS
  screenshot.
- The final authorized Tier 19 live run moved the first EALS purchase to wave
  1 at 4.89 seconds. EHLS confirmed Max at wave 10; EALS confirmed at wave 20
  after receiving inputs from wave 1, so the remaining timing reflects
  available in-run progression rather than startup idling.
- Every test restart used acknowledged `PAUSED / WAIT` control and manual
  Retry, so no Game Over handler work ran for the test boundary. Automation
  was restored to `RUNNING / RETRY`.
- `test/test_level_skip_initializer.py` covers the production capture path,
  paired-cycle order, reusable tap authority, and independent burst limits.
  All 922 repository tests passed.

### 2026-07-30 native control-surface layout redesign

- Commit `4c69f44` replaced the four-card scrolling rail with full-height
  Controls, Process, Setup, and Details tabs; persisted the selected tab,
  control-pane width, latest-battle height, and usable main/Battle History
  window placement; and added independently collapsible Previous Game Screen,
  Host Health, and latest-battle panels plus Reset Layout.
- The optional connection Token moved to Setup with explanatory tooltips and
  remains an unsaved in-memory bearer credential. Minimum-size constraints,
  off-screen recovery, and persisted layout state are covered in the native
  implementation; visual validation at minimum, default, and maximized Windows
  sizes remains active work.

### 2026-07-30 native Windows host-performance telemetry

- Commit `33b4687` adds one-second native Windows host and BlueStacks sampling
  on a below-normal-priority thread, a two-minute raw ring, approximately
  ten-second in-memory aggregates, and a bounded 24-hour local outage spool.
  The sample path launches no helper process and records its own duration plus
  total control-surface CPU for budget validation.
- Server revision 12 adds the bounded `POST /api/v1/host-performance` route and
  capability `host_performance_telemetry_v1`. SQLite aggregate UUID primary
  keys make reconnect retries idempotent; sample-time host, ADB port, UTC, and
  fresh activity-scope run identity remain distinct from ingest-time run
  context.
- The WPF status area now shows local host CPU, memory, clock, BlueStacks
  CPU/RAM/process count, and publication health even while the API is
  unavailable. The aggregation boundary is ready for later targeted PresentMon
  summaries without per-frame logging.
- Follow-up commit `34e014f` adds a locally persisted **Pause sampling** /
  **Resume sampling** control to the always-visible health strip. Pausing
  flushes the partial aggregate while leaving queued uploads active; resuming
  preserves host/session sequencing and leaves an explicit UTC gap. The left
  workspace row minima and weights were also rebalanced so every independently
  scrollable panel remains reachable at the declared minimum window size.
- All 897 repository tests passed and the self-contained Windows client
  cross-published successfully from Linux. Windows runtime measurement of the
  sub-0.5% CPU target remains a deployment validation; no emulator interaction
  was needed or performed for this code-only implementation.
- The follow-up passed all 898 repository tests and the standalone Windows
  client cross-published successfully from Linux. No live emulator interaction
  was needed or performed.
- Commit `7e56957` adds vendor-neutral Windows GPU Engine, Adapter Memory, and
  Process Memory collection through one persistent native PDH query with reused
  result buffers. Ten-second aggregates include host and BlueStacks GPU
  utilization/memory plus a bounded top-five list of competing processes;
  neither sampling nor attribution launches a helper process.
- Server revision 13 advertises `host_performance_gpu_v1` while continuing to
  accept CPU-only aggregates already present in older Windows outage spools.
  The WPF health strip adds a compact GPU row and exposes the incremental GPU
  counter duration for deployment-side CPU-budget measurement. PresentMon
  remains a separate future opt-in provider.
- The GPU follow-up passed all 902 repository tests and the standalone Windows
  client cross-published successfully from Linux. Actual Windows runtime
  measurement against the sub-0.5% target remains pending; no emulator
  interaction was needed or performed.

### 2026-07-29 Event Mission warning authority

- Commit `61caa78` prevents retained, claimed, advanced, or OCR-missed Event
  Mission rows from appearing as current stalled-progress warnings.
- Warnings now require the same tier's progress to be read in repeated
  observations, the row to be present in the latest complete inventory, and
  that inventory to be no more than one hour old. Unobserved wall-clock time
  cannot increase the reported incomplete or stalled interval.
- Progress-target changes reset tier age, and tracker schema version 2
  invalidates the stale version-1 cache that produced the reported
  `Login for 7 days — 6/7` warning alongside the later 10-day tier.
- `test/test_event_mission_tracker.py` covers single observations, stale and
  missing rows, claimed/advanced tiers, target changes, progress recovery,
  cooldown, and state migration. All 50 Event Mission/reward-handler tests and
  all 876 repository tests passed.
- The active automation was not restarted during its Home setup; the repair
  will load at the next safe process replacement.

### 2026-07-29 Home Battle History right-rail drift

- Commit `dba88f1` expands the Home Battle History template search from one
  fixed list position to the complete bounded right rail. The unchanged
  template and threshold now cover both seven- and eight-control Home layouts
  while retaining a template-derived tap center.
- The exact stopped Home frame is canonicalized as
  `test/fixtures/home_screen_eight_nav_controls_20260729.png`, and
  `test/test_battle_history.py` proves that the shifted icon remains
  authoritative.
- All 24 focused Battle History, matcher, clickmap-access, and
  clickmap-integrity checks passed, followed by all 870 repository tests. No
  live input or automation restart was required.

### 2026-07-30 Battle History joined-label OCR

- Commit `e58bad1` accepts OCR-omitted whitespace between the Tier/Wave labels
  and their numeric values in both newest-row and copied-detail evidence. It
  preserves numeric and exact-identity requirements instead of weakening the
  route to generic text presence.
- The previously ignored no-battle source frame is promoted to
  `test/fixtures/ui_state_20260714/no_battle_battle_history_20260719.png`;
  focused coverage also exercises a joined exact detail identity.
- All 16 focused continuity tests and all 904 repository tests passed. An
  operator-authorized live check then copied and fingerprinted the current
  Tier 19 wave-20 report and restored verified `HOME_SCREEN/NEW_BATTLE`.
  Automation remained `STOPPED` and the diagnostic target lock was released.

### 2026-07-30 Battle History top-edge proof

- Commit `29308ac` makes the newest-entry reader scroll Battle History to a
  proven stable top boundary before it validates or taps the first row. A
  retained list position can no longer silently redefine "latest" as "first
  visible."
- Every swipe verifies the Battle History screen and rechecks persistent input
  authority. A missing or unstable edge now fails closed and restores the
  source without selecting a row.
- The 39-test focused Battle History, scrolling, continuity, and clickmap set
  covers running, Home, interrupted-detail, Pause-between-swipes, and
  failed-edge paths. All 931 repository tests passed.
- Live post-repair interaction was intentionally omitted because the
  operator's next Tier 19 Retry battle was already running; diagnosis and
  validation did not alter it.

### 2026-07-29 Battle History-backed activity continuity

- Commit `2c4342d` fingerprints the newest copied in-game Battle History report
  before a Home launch and compares it whenever automation attaches to a
  running or resumable battle.
- An unchanged report preserves the existing Current run log scope. A changed
  report proves that a battle completed while automation was stopped and starts
  the new scope at the continuity `ACTION`, covering battles begun manually
  without automation. Unreadable identity after safe restoration fails toward
  a conservative new scope; unverified restoration blocks other inputs and
  retries.
- The guarded route is Pause-aware, restores Home or the running battle, and
  can recover when process replacement lands on its History list or detail.
  Its individual taps remain diagnostic beneath one operational `ACTION` and
  `RESULT`.
- Retained UI fixtures verify both navigation templates, latest-row/detail
  evidence, clipboard parsing, Home/running restoration, interrupted-route
  recovery, and persisted scope comparison. The complete repository suite
  passed 869 tests. The API and native client were unchanged, so no Windows
  publish was required.

### 2026-07-29 Bounded session-preflight repair retries

- Commit `fbdcd48` makes the Home-repair threshold explicit in the compact Farm
  profile and every generated Farm strategy: three matching authoritative
  session-preflight mismatches are required.
- Attempts one and two remain read-only, preserve the verified Home boundary,
  and retry after the existing cooldown. Success clears the count; a different
  failed-check set starts a new consecutive series.
- Only the exhausted third attempt can acquire the existing guarded
  Surrender/Home-repair authority. Automation-owned repair Game Over continues
  to bypass Perks/More Stats and battle-record persistence.
- The focused Farm/preflight selection passed 45 tests. The broad suite passed
  861 tests with four unrelated activity-continuity fixtures deselected while
  their concurrent implementation remained in progress.

### 2026-07-29 Perk schedule plausibility and ordered pause recovery

- Commit `1eb3cd0` prevents stable top-bar OCR artifacts such as `705` becoming
  `7705` from poisoning the Perk timeline. Scheduled pairs have a bounded lead,
  transitions require the armed boundary to be reached, and an implausibly
  distant armed value resynchronizes from stable valid evidence.
- Invalid schedule reads retry without panel input. Three consecutive invalid
  observations produce one persistent warning, and the next valid observation
  reports recovery without stopping the battle.
- A deferred post-PWR full snapshot now reverses the selected list's
  newest-first diff into chronological singleton batches when one distinct
  change matches each scheduled boundary. Repeated-family and count-mismatch
  cases remain explicit interval aggregates.
- The focused tracker and report suites passed 39 tests. The complete
  repository suite passed 852 tests.
- Live guarded attachment at wave 1902 established a full baseline, ignored
  transient `1972` → `72` OCR frames, and correctly recorded the next
  singleton selection at scheduled wave 1972 before restoring `RUNNING`.

### 2026-07-29 Perk timeline restart and modal recovery

- Commit `ce862cb` fixes the transferred-runtime incident in which a
  mid-battle Perk baseline crossed its scheduled wave during full-list capture,
  re-armed from stale progress, and later abandoned an open Perks panel after
  dispatching an unverified close.
- Full-list capture now refreshes the schedule from fresh panel frames and
  repeats once across a boundary. Panel restoration requires a freshly
  detected battle or terminal destination; failed transitions retain observer
  ownership and retry safely.
- Both incident paths have focused regressions. All 13 Perk timeline tests and
  236 surrounding integration tests passed. The repository suite passed 845
  sandbox-compatible tests plus its separately permitted loopback HTTP test,
  for 846 total.
- Guarded live recovery verified that the ADB transfer itself was healthy on
  `localhost:5565`, restored the naturally finished wave-3372 battle without
  exiting or Surrendering it, loaded the fixed runtime as PID 3210165, saved
  144 exact Stats rows and 27 selected Perks, and started the next Tier 19 run.
  Its first two timeline batches, scheduled for waves 191 and 429, both
  verified `close_state=RUNNING`; normal automation continued through wave
  470.

### 2026-07-28 action-log taxonomy migration

- Commits `5f7ef32`, `0620101`, `c975fa8`, `d35b8db`, `6b515d2`, and
  `110cd61` established one operator-facing `ACTION` and terminal `RESULT` per
  meaningful workflow while classifying individual taps, swipes, and presses
  as `INPUT` with paired diagnostic evidence. They migrated reward, terminal,
  in-battle setting, startup configuration, Golden Combo, Tournament,
  auto-return, and nested Ultimate Weapon workflows.
- `bd7dd23` separated current status and prior meaningful transition from the
  default Operational activity stream. `d98d67a` and `28b4a8a` moved ordinary
  scrolling, transient game-speed OCR misses, ADB retry detail, and Coins/min
  suffix repair out of warnings while retaining persistent rate-limited
  degradation and recovery records.
- `0b18f20` added mixed-stream audience regression coverage proving that
  Operational presents What/Why followed by Result, Diagnostics retains input
  and decision evidence, and All Levels preserves complete ordering. Final
  validation passed 817 sandbox-compatible tests plus the host-loopback
  transport test, and the native Windows publish completed successfully.

### 2026-07-28 restart-stable activity scope

- Commit `d8a1cda` makes automation startup reuse a valid Current run ledger
  instead of replacing it on every Python process start. A mid-battle
  stop/restart therefore retains the earlier activity from that same game run,
  while verified Home `NEW_BATTLE` preflight still starts the next scope.
- Regression coverage preserves the exact scope identity and log offset across
  attachment and verifies bootstrap behavior when no ledger exists. All 87
  logger and run-initialization tests and the two focused control-surface scope
  tests passed. The native client and API contract were unchanged, so no
  Windows publish was required.

### 2026-07-28 Daily Gem failure cleanup

- Commit `5cb852a` fixes the rollover Daily Gem incident in which Store inertia
  moved an already-matched claim button outside the tap verifier's vertical
  region. The failed live frame is now a canonical fixture; its button matches
  at greater than `0.99` in the corrected region.
- Claim dispatch consumes the screenshot that authorized the button and keeps
  its ordinary fresh retry. Any failure after Store navigation now retains
  evidence and attempts the verified route back to the originating battle or
  Home screen before publishing the terminal result, rather than relying on
  the generic 15-minute Return-to-Game timer.
- The old active runtime returned after 903 seconds, immediately retried the
  normal workflow, claimed the reward, and restored the Tier 19 battle. The
  repair passed 164 Daily Gem and surrounding integration tests plus 48
  clickmap/matcher/tap-safety tests.

### 2026-07-28 Cards traversal and pause-safe Perk tracking

- Commit `8c955d4` replaces the Cards inventory's fast 550-pixel forward jump
  with a slower overlapping 300-pixel gesture. Card recharge verification now
  detects the true top and bottom from settled screenshots, inspects every
  viewport, logs Demon/Nuke confidence, and retains the complete viewport set
  on failure.
- Exact replay of the reported Nuke-first sequence proved that the former
  gesture skipped the Demon row while its unchanged template still matched an
  intermediate viewport at `0.978`. The guarded active-battle check matched
  Demon at `0.996` and Nuke at `0.987`, reached the true top, returned through
  the verified in-battle route, and restored the agent-owned Pause to
  `RUNNING`.
- That live Pause crossed multiple automatic Perk selections and exposed a
  separate attribution defect. Commit `20b042d` now advances a deferred
  timeline request through newer stable progress tokens, forces a full
  snapshot after multiple boundaries, records the result as an interval
  aggregate without false per-wave attribution, and arms the next request from
  the newest observed token.
- The Cards suites passed 19 tests and the Perk tracker/report suites passed
  31. The repository suite passed 838 sandbox-compatible tests; the sole
  loopback HTTP test passed separately with host socket permission, for 839
  total.

### 2026-07-28 wave-addressed Perk selection timeline

- Automatic-Perk runs now read the compact top-bar
  `current wave / next Perk wave` control and require two stable observations
  before arming or reacting to a schedule change.
- Before Perk Wave Requirement reaches `-75%`, each changed schedule opens the
  guarded Perks panel, captures the complete selected list, and records its
  before/after diff as one simultaneous unordered batch at the original
  scheduled wave. The batch that reaches `-75%` still uses this complete path.
- Once a complete snapshot proves PWR is maxed, later schedule changes use the
  newest complete top row and record exactly one selection. Mid-battle process
  attachment first establishes a complete baseline and does not invent
  historical selection waves.
- Every panel open, close, and swipe rechecks persistent action authority.
  Pause may leave only the observer-owned panel route open; Resume continues or
  restores it, including the edge where capture completed immediately before
  Pause.
- Battle and Tournament runtime records retain the timeline, and Markdown
  renders scheduled wave, observed wave, level transitions, and the explicit
  within-batch ordering semantics. Successful action-log results name the
  selected perk or batch.
- Focused tracker, OCR, route-guard, pause-recovery, and rendering validation
  passed 47 tests. The complete suite passed 831 sandbox-compatible tests; its
  sole loopback-socket denial passed separately on the approved host path, for
  832 total.
- The explicitly owned Tier 19 live test observed the first top-bar transition
  scheduled for wave 191, completed its full panel capture at observed wave
  210, and logged the batch as recorded. User-authorized guarded cleanup
  Surrendered only that test battle and restored the stopped system to verified
  `HOME_SCREEN / NEW_BATTLE`.
- Implemented in commit `8685a79`. The same live pass exposed two unrelated
  pre-existing anomalies—repeated Demon Mode inventory misses and Stop waiting
  inside an in-progress Home setup. The
  [Demon Mode miss](../issues/resolved-2026.md#home-card-recharge-scan-repeatedly-missed-demon-mode-while-finding-nuke)
  is resolved; the Stop interruption defect remains in
  [open issue dossier](../issues/open-2026.md#stopped-control-could-not-interrupt-an-in-progress-home-setup-guard).

### 2026-07-27–28 Second Wind activation waves and transition evidence

- The run-scoped survival observer now records every confirmed Second Wind
  activation alongside the first Demon Mode activation and every Nuke
  activation.
- Second Wind arms only after both small tower wings are observed, but wing
  disappearance is no longer an activation trigger. The fixed white
  active-status glyph above Nuke is authoritative, while returning tower wings
  re-arm the observer after recharge.
- Paired clickmap templates were calibrated against retained quiet, busy,
  wings-present, and wings-absent 1080x1920 frames, then corrected against five
  fresh read-only live frames with the smaller tower rendering. A run that
  starts without visible wings, or does not equip Second Wind, cannot arm this
  detector.
- Each confirmed Second Wind, Demon Mode, or Nuke transition preserves an
  evidence frame under `screenshots/matches/`. Second Wind now retains the
  frame containing its short-lived active glyph. The event retains that
  evidence path, and the existing 30-day/size-limited runtime artifact policy
  bounds storage.
- Completed Battle and Tournament Markdown and the Windows Completed Battles
  view render every sequenced Second Wind wave. Schema 4 also records and
  displays the approximate re-arm wave as the sampled activation wave plus
  400; both values remain explicitly approximate because observation cadence
  may lag the actual trigger.
- Follow-up commit `58beb38` corrects a live-observed false positive: both
  wings are still required to arm the observer, while either visible wing now
  cancels pending activation confirmation. The exact one-wing-obscured frame
  is retained as a regression fixture.
- Follow-up commit `143e803` makes the active glyph authoritative. Promoted
  fixtures cover early, late, and heavily obscured countdown states and a
  known absent-icon false frame; missing wings alone cannot emit an event.
- Validation passed 797 sandbox-compatible tests plus the separately permitted
  loopback HTTP test, for 798 total. Recursive clickmap/template integrity
  passed, and the self-contained `win-x64` WPF publish completed successfully.

### 2026-07-26 completed-record discard and bounded runtime storage

- The Windows Completed Battles window now confirms and discards one exact
  selected Battle or Tournament. Authenticated server revision 8 moves the
  canonical JSON/Markdown pair into a metadata-backed quarantine instead of
  unlinking it immediately.
- Quarantined records are recoverable for 30 days by default. A six-hour
  control-server maintenance loop and ordinary history reads permanently purge
  only packages with valid metadata whose recorded deadline has passed;
  malformed packages fail closed.
- Runtime-owned `screenshots/matches`, post-run battle observations, and
  explicitly configured repository-local sample directories now receive
  six-hour age/size sweeps: 30 days and 1 GiB per tree by default. Canonical
  Battle/Tournament records, regression fixtures, unrelated screenshot trees,
  broad paths, and symlinked subtrees remain outside the cleanup boundary.
- `actions.log` and optional mission logs now retain a 16 MiB current file and
  five bounded backups by default, rotating before an atomic log group is
  appended. Environment and server options can override every retention limit.
- Validation passed 774 sandbox-compatible tests plus the separately permitted
  authenticated loopback HTTP test, for 775 total. The self-contained
  `win-x64` WPF publish also completed successfully. Implemented in commit
  `efec703`.

### 2026-07-26 codebase maintenance audit

- Commit `75d181c` recorded the repository-local audit and reviewable removal
  proposal in `docs/backlog/history/codebase_maintenance_audit_2026-07-26.md`. It traced
  imports, entry points, dynamic and YAML references, clickmap use, and tests,
  finding concentrated orchestration complexity rather than broad duplicate
  implementation.
- The audit classified the named Cards paths, separated 24 active
  Module-catalog templates from 20 asset-removal candidates, and identified
  compatibility decisions that must precede deletion. Those decisions remain
  active; the completed investigation itself no longer belongs in the work
  queue.

### 2026-07-26 protected development evidence

- Added a tracked, repository-relative protection manifest for generated
  screenshots and post-run observation directories used as durable development
  evidence. The initial entries cover the retained OCR, perk-configuration,
  guild-chest, and aborted-perk captures cited by current issue and resolution
  records.
- Both age and size pruning now exempt exact files, narrow wildcard families,
  and declared directory trees. Protection is classified before deletion; an
  absent, unreadable, or unsafe manifest fails the entire sweep closed.
- Regression coverage verifies exact, wildcard, and directory protection plus
  missing/invalid-manifest behavior. Validation passed 777 sandbox-compatible
  tests plus the separately permitted authenticated loopback HTTP test, for 778
  total. Implemented in commit `cc103d6`.

### 2026-07-26 Glass Cannon Auto Pick correction and run comparison

- The operator-supplied ranking replaced the short-lived survival-first order
  with the exact 16-slot Glass Cannon order. Ranks 9–16 are Orbs, Damage,
  Enemy Health / Tower Regen and Lifesteal, Enemy Speed / Enemy Damage,
  Ranged Distance / Ranged Damage, Boss Health / Boss Speed, Tower Damage /
  Boss Health, and Chain Lightning Damage. The planned next eight priorities
  are documented but remain non-enforcing until more slots are unlocked.
- All three compared Tier 19 records retained the prior 13-slot requirement.
  The wave-3001 / 1.06Q CPH run lacked Enemy Speed; the wave-3441 / 1.29Q CPH
  run lacked both Enemy Speed and Ranged Distance; and the wave-4799 /
  1.76Q CPH run had both. All three finished with Orbs +2, Damage x2.19, the
  global -55% enemy-health tradeoff, both boss tradeoffs, and Chain Lightning.
  This makes the two missing control perks a plausible causal contributor to
  the lower wave counts. Final records do not retain acquisition waves, so
  earlier delivery of the shared damage perks remains plausible but unproven.
- Home configuration OCR now recognizes both boss tradeoffs, the global
  enemy-health tradeoff, Bounce Shot, and Smart Missiles independently of
  their displayed values. The Farm profile, compatibility sources, all four
  generated plans, and the operator runbook agree exactly.
- Focused validation passed 125 tests. Repository-wide validation passed 763
  sandbox-compatible tests plus the separately permitted loopback HTTP test,
  for 764 total. Commit `dc36829` supersedes the Auto Pick order from
  `9a831d2`.

### 2026-07-26 16-slot Farm Auto Pick survival priority

- A guarded read-only Home inspection confirmed that the operator's three new
  Auto Pick Ranking levels exposed 16 priority rows. The appended rows were
  Enemy Speed / Enemy Damage, Ranged Distance / Ranged Damage, and the
  regen-hostile Enemy Health / Tower Regen and Lifesteal tradeoff. The panel
  was returned to verified Home under unchanged `RUNNING / WAIT`.
- The first eight economy and acceleration ranks remain unchanged. Ranks 9–14
  now prioritize Defense Percent, Max Health, Health Regen, the Health Regen /
  Max Health tradeoff, Enemy Speed / Enemy Damage, and Ranged Distance /
  Ranged Damage. Orbs and Damage occupy ranks 15–16; the enemy-health
  tradeoff is intentionally excluded.
- Semantic Home OCR now recognizes all six survival families independently of
  their current level values. The Farm profile, both compatibility sources,
  all four generated Farm plans, and the operator runbook carry the same
  16-item order.
- Focused validation passed 124 tests. Repository-wide validation passed 762
  sandbox-compatible tests plus the separately permitted loopback HTTP test,
  for 763 total. Implemented in commit `9a831d2`.

### 2026-07-26 Perk Wave Requirement OCR repair

- Completed Tier 19 comparison exposed that Tesseract could read the maxed
  `-75.00%` value as `-/5.00%`; generic cleanup then rendered the false
  `-5.00%` result.
- Label-specific normalization now restores the dropped `7` while preserving
  raw OCR evidence. The focused and related battle-record suites passed 36
  tests, and retained failed viewports reprocess as
  `perk_wave_requirement_75_00`.
- Implemented in commit `963c771`.

### 2026-07-26 collapsible Battle History and survival activation waves

- Confirmed that the selected `Battle20260725T210917-0700` record already
  retained Demon Mode at approximate wave 1973 and Nuke at waves 2683, 3027,
  and 3366, while the Windows Completed Battles parser displayed only the
  adjacent Coins/min runtime samples.
- The Battle stats tab now presents every report category as a collapsed tree
  node with a row count. Expanding a node reveals its stat/value children
  without repeating the section label on every row.
- `runtime.survival_ability_activations` now produces one Demon Mode
  first-activation child and every sequenced Nuke activation, including wave,
  detection time, and wave-OCR confidence.
- The self-contained `win-x64` WPF publish completed successfully. Diagnosis
  and implementation did not change the automation process, control state, or
  active battle. Implemented in commit `f876647`.
- Operator screenshot review exposed WPF's near-black inherited child
  foreground. Follow-up commit `f690302` makes the foreground explicit and
  presents expanded children as headed, bordered Stat/Value rows. A second
  screenshot exposed content-sized star columns; commit `a2ac376` replaces
  them with shared fixed-width columns so the header and every child row align.
  Each refreshed self-contained `win-x64` publish completed successfully.

### 2026-07-25 Coins/min ramp plausibility confirmation

- Commit `2c1bebd` changed the Coins/min plausibility gate from permanent
  comparison against a frozen baseline to cross-sample confirmation. It still
  rejects an isolated large change, but accepts a sustained ramp once the next
  candidate corroborates it.
- Display recovery now requires two consecutive missing `/min` observations,
  and a post-toggle lifetime total cannot be published as Coins/min.
- Read-only live evidence reproduced the failure after the expected zero-rate
  opening: `362T` was frozen while correct readings rose through `4.05q`,
  `7.52q`, `10.2q`, and later values. A fresh wave-2167 frame visibly showed
  `22.6q/min` and the diagnostic probe parsed `22.6q`.
- The focused Coins suite passed 8 tests, status/run-boundary integration
  passed 75, automation control and process coverage passed 79, and
  `git diff --check` passed. No battle or process action was performed.

### 2026-07-25 Boss-safe Orb Distance enforcement

- Repaired Distance Adjuster enforcement when a live Boss greys out the arrows
  while the panel's automatic pause prevents combat from clearing it. An
  unavailable arrow or unchanged verified tap now closes the panel, waits for
  the running wave to advance, and retries from fresh panel evidence in a
  bounded number of sessions.
- Propagated the runtime action guard into strategy execution so the
  between-session wait and every new panel open respect a newly applied
  operator pause.
- Changed `farm_t19_experiment` from Orb Distance `preserve` to range-selected
  `enforce`. Both Farm tiers now apply the configured pair for observed Range
  `30.00m` or `98.38m` and preserve any other readable experimental Range
  without Distance Adjuster input; Tier 19 Target Priority and Damage Slider
  remain preserved.
- Focused coverage passed 90 tests, broader integration coverage passed 152,
  and repository-wide validation passed 757 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 758 total.
- Implemented in commit `b01ebf9`.

### 2026-07-25 nonblocking Tournament observer mismatches

- Repaired the attached-Tournament session-preflight loop. A read-only
  mismatch now retains its failed checks and detailed evidence, completes the
  one-shot observer pass without a gate decision or waiver, and cannot re-arm
  the inventory rule.
- Required Farm/session gates keep their existing blocking, retry, fallback,
  and guarded-repair behavior. Tournament exact-match status also remains
  distinct: a mismatch records `completed=False` while allowing observation
  and terminal capture to continue.
- `test/test_tournament_observer.py` proves a `modules` mismatch remains
  recorded and cannot emit a second strategy action. Focused coverage passed
  108 tests; repository-wide validation passed 755 sandbox-compatible tests
  plus the separately permitted localhost HTTP test, for 756 total.
- Implemented in commit `53f0719`.

### 2026-07-25 offscreen Damage Slider localization

- Reproduced a mid-Tournament failure where Attack retained a scrolled
  viewport with Damage above the visible list. The opener had verified the
  Attack category but searched only that one frame.
- A failed current-frame match now falls back to the existing bounded,
  manifest-aware upgrade traversal. Each capture must remain
  `RUNNING/ATTACK_MENU`, the final Damage tap still requires its exact
  template, No Strategy retains its pause-aware action guard, and upgrade
  swipes receive operator-facing action records.
- Focused Damage, upgrade navigation, No Strategy, Orb Distance,
  initialization, and Tournament validation passed 169 tests. Diagnosis and
  repair sent no device input and did not restart the active automation.
- Implemented in commit `3abd62a`.

### 2026-07-25 Cards inventory swipe traversal

- Repaired the Card-recharge preflight's inventory reset after a failure left
  Cards at its bottom position. The old downward reset began above the
  inventory viewport, so it was ignored and the forward search could never
  return to Demon Mode.
- Both directions now drag between `y=1100` and `y=1650`, keeping the complete
  gesture inside the inventory viewport, and use the established 300 ms
  duration.
- Card search now checks both unresolved Cards at the initial position and
  after every upward or downward swipe. It validates whichever appears first
  and stops immediately after both pass instead of completing the reset or
  searching in a fixed Demon Mode/Nuke order.
- The exact clickmap geometry is covered by
  `test/test_card_swipe_geometry.py`. A reverse-order traversal regression
  validates Nuke first, reaches Demon Mode in one upward swipe, and proves
  there are no extra gestures. Focused Card and Home/Tournament caller
  validation passed 130 tests without device interaction.
- Implemented in commits `fea3242` and `ff1670a`.

### 2026-07-25 Demon Mode/Nuke recharge activation preflight

- Added strategy-owned recharge activation defaults for Farm and Tournament:
  Demon Mode automatically activates when its recharge completes, while Nuke
  becomes available but waits for manual activation.
- Home `NEW_BATTLE` setup now locates both Cards in inventory, opens the exact
  detail through a guarded long press, classifies the checkbox from retained
  live evidence, leaves matching states untouched, and corrects and re-verifies
  only authoritative mismatches. Missing or ambiguous evidence fails closed.
- Live observation confirmed both card details describe a 300-wave recharge,
  with Demon Mode checked and Nuke unchecked. The observation did not change
  either checkbox or start a battle.
- Focused strategy, Home-gate, clickmap, reporting, and control coverage passed
  191 tests. Repository-wide validation passed 751 sandbox-compatible tests
  plus the separately permitted localhost HTTP test, for 752 total.
  Implemented in commit `7e542f4`.

### 2026-07-25 Range-selected Orb Distance enforcement

- Reproduced the Tournament validation failure against its exact live Attack
  frame: the Range tile was correctly located and visibly showed `98.38m`, but
  raw OCR returned no text for the dim Max-state value. One bounded
  adaptive-contrast retry now reads that frame as `98.38m` at 86% confidence.
- Generated Farm and Tournament actions now carry every configured Orb
  Distance preset. The authoritative observed Range selects its matching
  Extra/Workshop pair. A readable Range outside the configured set is retained
  as an operator experiment and completes without opening or changing Distance
  Adjuster; unreadable Range evidence still fails closed.
- The failed one-shot validation retained ownership through timeout, Surrender,
  Game Over, and verified Home cleanup. No battle remained after diagnosis.
- Focused Orb Distance and strategy-builder coverage passed 105 tests.
  Repository-wide validation passed 734 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 735 total. Implemented in
  commit `3bc3ab4`.

### 2026-07-25 slot-level module replacement and exclusive-check skips

- Replaced Unequip-based Primary/Assist cycle handling with a verified
  same-family level-1 intermediate. Every occupied replacement now requires
  the game's level-transfer prompt, while filling a known empty recovery slot
  rejects an unexpected transfer prompt. The generated loadout remains generic
  and strategy-owned.
- Hardened inventory selection with aligned icon ranking, independent
  confidence and runner-up-margin authority, exact detail name/action/level
  checks, settled rarity-row reacquisition, and complete settled-overview
  validation.
- Repaired the interrupted Farm armor assignment live without losing either
  slot level: Anti-Cube Portal finished in armor Assist at level 194, Orbital
  Augment finished in armor Primary at level 201, and fresh overview evidence
  matched all eight configured Farm modules. Automation remained paused and no
  battle was started.
- Tournament exclusive validation now claims the staged, strategy-scoped
  one-run check waivers used by normal startup, so selecting **skip Modules**
  actually suppresses module work on that validation path.
- Focused regression coverage exercises direct transfers, level-1
  intermediates, unexpected prompts, settled filter rows, aligned candidates,
  confidence/margin refusal, and the exclusive-validation Module skip.
  Repository-wide validation passed 731 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 732 total. Implemented in
  commits `859351f`, `1121bff`, `983e1f0`, and `4edc809`.

### 2026-07-25 Farm Perk configuration enforcement

- Promoted Perk Bans and Auto Pick priority into strategy-owned Farm
  invariants. The canonical order includes Coin Trade-Off at priority 3 and is
  expanded into both current Farm plans and the retained GC aliases.
- Added Home `NEW_BATTLE` OCR and guarded repair. Ban changes use matched
  Selected Perks rows to remove extras and search Available rows only for
  missing required bans. Ban repair completes before Auto Pick opens; Auto
  Pick then inserts each declared perk into its exact rank through freshly
  verified upward moves. Ambiguous identity, missing rows, unchanged inputs,
  non-progress, and bounded-search exhaustion all fail closed before battle.
- Live Farm T19 testing detected and corrected an extra Coin Trade-Off ban.
  The first revision took the longer Available-list route and exposed that the
  blocking Home workflow did not consume Pause. The follow-up synchronizes
  persistent control before every setup input; Pause is action-free and Resume
  restores Home before a fresh pass.
- The Auto Pick live retry also exposed row coordinates captured while the
  list was still settling after a swipe. Actions now recapture and uniquely
  reacquire the semantic row immediately before input, then rebuild rank from
  the top and require exactly one-rank progress. Live validation moved Coin
  Trade-Off from rank 29 to rank 3 through 26 proven steps and passed the exact
  13-entry final comparison.
- Retained July 22 Farm screenshots verify all five configured bans and all
  thirteen priorities. Automated coverage exercises the missing Coin
  Trade-Off repair, direct selected-ban removal, Ban-before-Auto sequencing,
  Pause/Stop authority, pre-action row drift, exact rank progress, strategy
  expansion, Home-gate integration, and No Strategy compatibility.
- Repository-wide follow-up validation passed 725 sandbox-compatible tests
  plus the separately permitted localhost HTTP test, for 726 total.
- Implemented in commits `bafeff4`, `c4cb745`, and `227465b`.

### 2026-07-25 Farm module preflight visibility and transitions

- Isolated each Modules rarity verifier from adjacent rows, including the live
  `Mythic+`/`Ancestral` collision, and restored concise expected/observed result
  logs for every reached Home-preflight requirement.
- Hardened the Equip-to-role-prompt transition with a second OCR layout and one
  bounded retry that remains authorized only while the same verified
  Ancestral detail still offers `EQUIP`.
- Live Farm T19 validation corrected the remaining generator and core
  assignments, accepted their level transfers, matched all eight configured
  modules, completed every Home and session check without a waiver, and
  resumed normal battle handlers.
- Repository-wide validation passed 709 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 710 total.
- Implemented in commits `1629bb3` and `31e0191`.

### 2026-07-25 Range-bound Orb Distance presets

- Added named Orb Distance presets and enforced Tier 18 Farm at Attack Range
  `30.00m` with Extra `30.00m` / Workshop `39.00m`, and Tournament at Attack
  Range `98.38m` with Extra `87.16m` / Workshop `80.37m`.
- The battle-only controller requires authoritative Range and panel OCR,
  freshly matches every single arrow tap, verifies strict progress, and blocks
  strategy completion until both values match exactly and the panel closes
  back to the running side menu.
- Retained fixtures validate both Range values, the Distance Adjuster values,
  and every new tap target. Repository-wide validation passed 703
  sandbox-compatible tests plus the separately permitted localhost HTTP test,
  for 704 total. No live battle validation was performed.
- Implemented in commit `5448e82`.

### 2026-07-25 confirmed Tournament launch

- Added a durable, one-shot launch decision to a successful Tournament
  validation receipt. **Start Tournament** performs lightweight freshness and
  ownership checks without rerunning validation, claims the launch before
  input, and uses only verified Home New Battle, Tournament Open, and
  Tournament Battle controls.
- Added automatic and persistent browser/native prompts with **Start
  Tournament**, **Cancel launch**, and **Decide later**. The prompt reminds the
  operator to set Target Priorities for the current Tournament Battle
  Conditions when the battle begins; that setting remains manual.
- Pause, restart, owner mismatch, request supersession, timeout, wrong battle,
  and ambiguous navigation fail closed. Manual launch remains supported, a real
  Tournament never gains Surrender authority, and its normal EHLS/EALS
  initialization remains active.
- Repository-wide validation passed 684 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 685 total. Browser JavaScript
  syntax validation and standalone Windows-client publishing also passed. No
  live process or device interaction was used.
- Implemented in commit `0aea936`.

### 2026-07-25 Damage Slider operator log formatting

- Kept the internal Damage Slider target at `1E2` while formatting
  operator-facing target, comparison, and completion messages as `100%`.
- Focused validation passed 120 tests. No live process or device interaction
  was used.
- Implemented in commit `f4ae2b0`.

### 2026-07-25 one-shot Tournament validation

- Made each explicit Tournament selection or managed Start authorize one
  durable, fingerprint-bound validation request. After complete unwaived Home
  preflight, the same runtime atomically owns and starts one verified ordinary
  New Battle, enforces Damage Slider `100%`, validates Ultimate Weapons and
  Spotlight Missiles, and returns only that battle to Home.
- Ownership is checked before every terminal action. Restart, ADB-target
  change, Resume, Tournament identity, stale evidence, or another ambiguous
  boundary fails closed without inherited Surrender authority. Browser and
  native clients show pending, running, cleanup, ready, and failed results.
- The disposable validation battle does not toggle Auto Perks or seed upgrade
  completion. The manually started Tournament still runs normal EHLS/EALS
  initialization before settling into observer behavior.
- Focused validation passed 212 tests. Repository-wide validation passed 665
  sandbox-compatible tests plus the separately permitted localhost HTTP test,
  for 666 total. The standalone Windows client published successfully, and no
  live process or device interaction was used.
- Implemented in commit `edc53ea`.

### 2026-07-25 action-intent log headers

- Added a reusable operator-facing `ACTION` header that states what a guarded
  or multi-step workflow is beginning and why before its tap and swipe details.
- Adopted the header for level-skip initialization, Target Priority, Damage
  Slider, session preflight and Home repair, Daily Gem and mission rewards, and
  Game Over handling.
- Focused validation passed 139 tests. Repository-wide validation passed 649
  sandbox-compatible tests plus the separately permitted localhost HTTP test,
  for 650 total. No live process or device interaction was used.
- Implemented in commit `8bbd3eb`.

### 2026-07-25 Tournament Stun and Damage Slider preflight

- Added Poison Swamp Stun `on` to the Tournament Home contract. The guarded
  detail-panel correction now supports either required state while Farm remains
  Stun `off`.
- Added the battle-only Tournament Damage Slider requirement at `100%`.
  Session validation enforces it before scanning Ultimate Weapons, and Home
  evidence records the control as deferred rather than claiming it was checked.
- The remaining Tournament configuration checks that truly require a battle
  are Damage Slider plus the nine Ultimate Weapon primary toggles and Spotlight
  missiles. Game speed is maintained separately by its runtime handler.
- Regression and full repository validation passed 648 tests, including the
  separately permitted localhost HTTP test.
- Implemented in commit `534a221`.

### 2026-07-25 Tournament Guardian tap authority

- Added retained-fixture-backed Attack and Ally inventory targets so
  Tournament Home setup can replace Farm Guardian chips without falling back
  to forbidden coordinate-only taps.
- Regression coverage requires visible-target selection and validates both
  unequipped inventory cards against the retained Farm loadout. The focused
  Guardian, tap-safety, and clickmap suites initially passed all 58 tests.
- Follow-up `1e0c860` lets the same reconciler safely resume when a prior
  fail-closed replacement left Attack, Ally, Fetch, Summon, or Scout empty.
  Interrupted Attack and Ally cases raise the focused total to 59 passing
  tests.
- Implemented in commits `2bfb653` and `1e0c860`; live reload and gate retry
  remain pending.

### 2026-07-23 offscreen weekly mission chest

- Added a bounded horizontal weekly-chest traversal to Daily Missions. It
  normalizes the retained track position, searches with overlapping guarded
  swipes, and claims only from the fresh frame that exposes the available
  chest.
- Regression coverage verifies the swipe geometry, offscreen search, fresh
  claim authority, initially visible rewards, and Sunday hold/capacity policy.
  The focused suites passed 40 tests; the full suite passed 637 sandbox tests
  plus its separately permitted localhost-socket test.
- Live validation claimed the preserved offscreen chest, dismissed its reward
  reveal, converged at the far edge with `daily=1`, and then completed a Tier 18
  Farm startup and session preflight without waivers or failed checks.
- Implemented in commit `4554f7c`.

### 2026-07-13 headless template workflow

- Added a dry-run-first template tool that separates the exact asset crop from
  its runtime search region, validates both current match profiles, accepts
  positive and negative fixtures, and emits candidate, annotated, and JSON
  review artifacts without requiring a desktop session.
- Added guarded atomic commit support with explicit consent for replacements,
  shared assets, and dimension changes while preserving unrelated clickmap
  fields.
- Reproduced the Home Store-badge asset from its canonical fixture pixel for
  pixel and verified its 52x52 crop at the expected location within the
  distinct 64x66 runtime search region.

### 2026-07-13 test-log isolation

- Added a runtime-overridable primary action-log path and configured pytest to
  use a unique `/tmp/thetower-pytest-*` log before test modules are imported.
- Verified targeted and full test runs leave the live `logs/actions.log` size
  and modification time unchanged while retaining synthetic logs for diagnosis.

### 2026-07-13 live automation validation

- Added and live-verified `--adb-port` support with default port 5555.
- Made GC the default strategy and added an exclusive new-run startup gate.
- Live-verified the startup order: EHLS first, EALS second, then the
  session-scoped Target Priority check; both skip boxes were visibly `Max`.
- Split Exit Battle into guarded `Surrender` and `Go Home` actions; live-tested
  that Go Home preserves and resumes the same run and Surrender reaches Game
  Stats.
- Repaired Round Stats scrolling with source-screen guards and true-edge
  detection; live-tested the complete Game Over capture flow.
- Split home/in-run Store navigation, retained red-badge availability as the
  trigger, added a home red-badge region, and live-tested inactive Daily Gems as
  a normal not-ready result.
- Captured the 17:00 PDT new-day transition. Two Daily Missions appeared
  immediately, but the Store badge did not appear until Daily Missions was
  opened and closed; toggling the in-run menu alone did not refresh it.
- Confirmed the Store badge persisted from the running screen to Home and into
  a new run. Added a distinct Home badge template, refreshed the stale Home
  `Battle` template, and added a canonical Home fixture for both matches.
- Opened Cards through the live navigation template on ADB port 5565 and
  captured the active fixed `GC` preset. Added a composite template containing
  both its label and green selection border, validated it against two live
  positives and two non-Cards negatives, and added a canonical Cards fixture.
  Repointed the legacy GCFarm secondary states from generic slot-border crops
  to their full identity templates so an active `GC` deck is no longer falsely
  reported as `CARDS_GCFARM_EARLY`.
- Captured Event Bots with `Farm` active and Guild Guardian with `Fetch`,
  `Summon`, and `Scout` equipped. Added separate stable screen guards and
  configuration templates, plus an offline three-screen GC evidence validator.
  Rejected selected-tab templates after same-menu negatives scored 0.985-0.996;
  the workflow instead verifies stable target-screen content after navigation.
- Captured Event Missions and Guild Members as same-parent negative fixtures.
  The Guild frame preserves the unclaimed glowing 250 contribution chest; no
  reward or configuration control was tapped.
- Used guarded Go Home navigation without ending the active run and captured
  Workshop with `Farm` selected. Added stable Workshop/Farm identity templates
  and classified the selected preset by its green border rather than a
  high-correlation full-card template; the four cyan inactive neighbors provide
  same-frame negative evidence. No Workshop preset was changed.
- Opened the in-run Damage detail panel through the left-side label rather than
  the upgrade purchase offset and captured its persistent `Percent Of Enemy
  Health` selector at `1E-22%`. Added a primary panel state, stable guard,
  read-only OCR, and guarded open/dismiss actions using ordinary settled ADB
  screenshots. The changing `94.80M` derived damage is not used as state, and
  neither adjustment arrow was tapped.
- Fixed the Daily Gems handler so the active card's `FREE` price is not mistaken
  for a cooldown and a claim already visible at Store entry does not trigger a
  redundant top-and-back scroll. Live-verified the active in-run Store route,
  claim, ad skip, return to the running game, and badge clearance; the no-scroll
  entry path has automated coverage.
- Fixed the Daily Gems cooldown exit so `NOT_READY` is returned only after the
  handler taps Return to Game. Failure to find that control now fails the probe
  instead of recording an incomplete Store visit as a successful daily check.
  Live-verified the repaired path on 2026-07-14: the cooldown was detected,
  Return to Game matched and tapped, and the resulting state was `RUNNING`.
- Prevented scheduled Daily Gem probes from preempting transitional Home
  screens. The automatic path now waits for `RUNNING`; the retained rare
  Home-origin Store route returns through the bottom Home selection and verifies
  `HOME_SCREEN`, while the in-run route verifies `RUNNING`. Live validation on
  port 5565 detected a manually claimed gem's cooldown, returned to the battle,
  and persisted UTC day `2026-07-15` as `not_ready`.
- Replaced generic per-tick EHLS/EALS searches with a dedicated exclusive,
  state-driven initializer. It uses fast label templates and upgrade geometry,
  detects the rectangular gold `Max` border directly, supports either or both
  upgrades beginning gold boxed, and defers wave OCR until purchasing is done.
  Purchase taps continue independently of capture latency; a continuously
  drained H.264 stream supplies current verification frames, with guarded raw
  capture as fallback. In the final fresh live regression, EHLS gold boxed at
  wave 20 and EALS at wave 30 in both final fresh regressions. Human `touch`
  markers recorded the first EALS dispatch 0.285 and 0.472 seconds after EHLS
  became visibly gold (tap completion at 0.742 and 0.748 seconds). Completion
  waves, EALS first-tap wave/time, total elapsed time, tap count, and failure
  reason are recorded.
- Restored optional pause expiry without restoring the split-brain timer race.
  A plain control-file pause remains indefinite; `pause --minutes N` persists
  its deadline, the supervisor mirrors that deadline in memory, and expiry
  persists `RUNNING` before allowing automation actions to resume. A failed
  control-file write leaves the process paused.

### 2026-07-14 architecture safety foundation

- Separated battle lifecycle from visible UI navigation. `GAME_OVER` and a
  verified Home `NEW_BATTLE` control now end the observed battle identity;
  Home `RESUME_BATTLE`, unknown Home evidence, and transient unknown screens
  preserve it. The existing Home OCR/template evidence is shared by lifecycle
  handling and guarded Home actions. A live guarded Go Home at wave 3457
  exposed the stale historical Resume asset, while OCR classified `RESUME
  BATTLE` at 93.75 confidence. The refreshed template matched the live frame at
  1.000 and stayed below threshold on the canonical new-Battle fixture; its
  guarded visible tap returned to the same battle at wave 3468. Replaying those
  live observations through the new lifecycle emitted no second run start. A
  later genuine Home boundary repeatedly classified `NEW_BATTLE` at 96.0 OCR
  confidence while paused without activating initialization. Its guarded
  visible tap started exactly one gate; EHLS completed at wave 20 and EALS at
  wave 30.
- Added a non-blocking OS process lock keyed by ADB target. A second runtime for
  the same target exits before constructing `App`, while different target ports
  retain independent lock files.
- Separated legacy direct-`match_region` center resolution from runtime blind
  input authority. `get_click()` retains its historical center behavior for
  compatibility and tooling, while blind named `safe_tap` actions require an
  explicit `tap`. Broad scrolling `region_ref` windows continue to locate and
  tap the actual matched element; the four in-run menu navigation targets now
  declare their existing static coordinates explicitly.
- Repaired the paused exclusive startup gate. Initialization ownership now
  follows the active battle lifecycle across transient unknown frames, paused
  capture/detection/status reporting remains active without actions, and gate
  completion is logged only after the strategy assertion succeeds. The
  49-test architecture checkpoint and live paused/resumed validation both
  passed; the live level skips required zero purchase taps and Target Priority
  was verified before the gate released.

### 2026-07-15 GC strategy profiles

- Replaced the tactical `target_priority_checked=True` strategy variant with a
  concrete build-time GC family/profile model. `gc_farm_t18` enforces its
  explicit Target Priority order, while `gc_farm_t19_experiment` omits Target
  Priority from both the generated action rules and startup completion gate.
- Routed profile-provided orders through the shared action executor to the
  existing Target Priority enforcer. Failed verification leaves the gate
  incomplete; the successful session-scoped result persists across run
  boundaries.
- Retained `gc_manual_target_priority` only as a compatibility name resolving
  to the explicit Tier 19 generated profile, with no strategy-name conditional
  in the app and no seeded completion state.

### 2026-07-15 GC session preflight

- Added a generic post-initialization session gate and profile-carried GC
  requirements. Completion persists across run boundaries in one process;
  paused and transient-unknown observations cannot release or act through the
  gate.
- Added guarded read-only traversal and evidence for GC Cards, Farm Workshop,
  Farm Bots, Fetch/Summon/Scout Guardian chips, Auto Pick Perks, and all nine
  required Ultimate Weapons. Dedicated Perks-close and visible Home
  Event/Guild templates replaced broken generic/static dependencies.
- Live-validated a natural Tier 19 wave-2558 Game Over -> Retry boundary, EHLS
  then EALS at waves 20/30, preserve-mode Target Priority with no action, and a
  complete once-per-session preflight. Mismatches block and log evidence;
  automatic correction and Surrender remain disabled.

### 2026-07-15 Mission and Guild reward collection

- Added a bounded side-menu reward probe. The aggregate red/purple attention
  dot schedules inspection but never authorizes a reward tap; fixed Daily,
  Event, and Guild badge regions select panels, and every action requires a
  fresh parent-state check plus exact available artwork.
- Added distinct positive/negative evidence for Daily mission claims, weekly
  chests, Event mission claims, and Guild contribution chests. Claimed and
  locked chest artwork stays below threshold. Weekly/Guild reward reveals share
  the verified `SKIP` control; Event scanning uses screen-guarded bounded
  scrolling.
- Live-validated the full handler on a paused Tier 20 run at port 5565. It
  claimed three remaining Daily missions and one remaining Event reward,
  skipped Guild because only claimed/locked chests were present, logged
  `daily=3 event=1 guild=0`, and restored `RUNNING/MENU_CLOSED`. A second probe
  saw no relevant badges despite an unrelated Modules badge and performed no
  reward action. The battle continued naturally and was never surrendered.

### 2026-07-16 Ancestral module icon index

- Exhaustively reconciled the owned Ancestral inventory into 24 distinct
  icon/name pairs, six for each module family, including unequipped modules.
- Added a read-only JSON-backed equipped-module index with separate
  Primary/Assist geometry, Ancestral-green gating, confidence and runner-up
  separation, and non-authoritative unknown/ambiguous outcomes.
- Retained fixture evidence for the confirmed GC overview and Project Funding
  at both equipped scales. `test/test_module_icon_index.py` verifies all eight
  equipped identities, catalog completeness, scale normalization, and
  rejection of ambiguous, unreadable, and non-green evidence.

### 2026-07-16 GC module gate and guarded repair

- Added the exact eight-slot GC module mapping to both generated GC profiles
  and included Modules overview evidence in the read-only session preflight.
  Unknown, ambiguous, and non-Ancestral results block without authorizing
  Surrender or equipment changes; only confidently named wrong modules request
  the Home-only repair path.
- Added an app-owned stop → Game Over → Home setup → restart → fresh-preflight
  lifecycle. Module correction acts only at verified `NEW_BATTLE` Home, ranks
  the complete Ancestral inventory from normalized icon data, confirms the
  exact detail name plus Equip/Unequip action, and revalidates the complete
  overview after every transition. At this point the implementation
  deliberately declined level transfer; the 2026-07-23 correction below
  supersedes that behavior.
- Added completeness guards for module captures, complete-modal guards for
  detail OCR, and bounded rewind-to-top behavior for retained Module inventory
  and Event Bots scroll positions. Regression coverage exercises correct,
  wrong, swapped, uncertain, incomplete, transition, and retained-scroll cases.
- Live-validated the full lifecycle with an explicitly developer-owned Tier 18
  run: Project Funding was detected in the Black Hole Digestor slot while every
  other preflight requirement passed; automation Surrendered once, restored
  Black Hole Digestor at Home, restarted, completed EHLS/EALS at waves 20/30,
  and produced a fully valid fresh preflight with all eight expected modules.
  The post-validation developer-owned run was then Surrendered for cleanup and
  the device was left at `NEW_BATTLE` Home under persisted `PAUSED/HOME`.

### 2026-07-17 Poison Swamp Stun preflight correction

- Added profile-owned `Poison Swamp: stun: off` requirements to both GC
  profiles and restricted the compact strategy schema to that supported state.
- Added separate retained templates for the Poison Swamp detail title and the
  checked/empty Stun control. The guarded helper reacquires a complete UW frame,
  derives the detail action from the uniquely detected Poison Swamp tile,
  changes only verified `on` to `off`, reverifies the result, and returns to the
  UW menu. Unknown or incomplete evidence fails closed.
- Fixture and navigation regression tests cover on → off correction,
  already-off behavior, template separation, profile propagation, and the
  preflight evidence merge without ending or leaving the active battle.
- Live-calibrated both checkbox states on a preserved active run, restored Stun
  to off, and exercised the production already-off path at confidence `1.0`.
  The helper dismissed the detail and returned to `RUNNING/UW_MENU`; the
  existing battle was never Surrendered and automation was resumed afterward.

### 2026-07-17 Farm profile and loadout architecture

- Replaced GC as the public recurring-run profile with `farm`, `farm_t18`, and
  `farm_t19_experiment`; the former GC names remain compatibility aliases and
  the command-line default is now `farm`. Glass Cannon is retained only as a
  gameplay concept that can span Farm, Tournament, Milestone, and Dissonance
  purposes.
- Added one non-overridable Farm baseline for Cards, Workshop, Bots, Guardian,
  Auto Pick Perks, and Ultimate Weapon controls.
- Restricted per-Tier and experimental loadouts to Modules, Damage Slider, and
  Target Priority. Compact profiles must explicitly choose `enforce`,
  `observe`, or `preserve`; module and Target Priority presets resolve into the
  generated plan at build time. Damage Slider was initially preserve-only
  pending its guarded setter.
- Made module observation non-blocking, module preservation skip navigation,
  and Target Priority observation read-only. Generated plans carry the resolved
  configuration, and schema-version-2 battle records persist that snapshot.
- Added focused builder, alias, policy, preflight, action, strategy-isolation,
  and battle-record regression coverage. This architectural slice was validated
  offline and did not pause or interact with the active battle.

### 2026-07-17 Tier 18 Damage Slider initialization

- Extended the Farm loadout policy so Damage Slider `observe` and `enforce`
  modes resolve explicit percentages without strategy-name conditionals. Tier
  18 now enforces `1E-22%` during every new-run initialization; Tier 19 remains
  `preserve` for experimentation. The rule waits for the time-sensitive
  EHLS/EALS initialization before opening the Damage panel.
- Added guarded Attack-menu navigation and feedback control. The setter opens
  the freshly matched Damage detail, reacquires authoritative panel and OCR
  evidence before each explicit arrow tap, requires strict progress toward the
  requested value, verifies the final value, and restores
  `RUNNING/ATTACK_MENU`. Ambiguous, unchanged, or regressive feedback fails
  closed; wrong-sized or majority-black direct ADB frames cannot authorize the
  panel evidence.
- Reset each run's gate and structured observation from the strategy's declared
  defaults, and added fixture-backed normalization, navigation, adjustment,
  policy, generated-plan, executor, and reset regression coverage.
- Live validation on an explicitly developer-owned Tier 18 run observed the
  starting value at `100%`, enforced `1E-22%` in 24 strictly verified steps,
  independently re-observed the final value, and returned to
  `RUNNING/ATTACK_MENU` without Surrender.

### 2026-07-17 Farm Cards preset migration

- Fresh inspection found the in-game Cards preset already named `Farm` and
  selected at a verified no-battle Home boundary. Replaced the stale `GC`
  baseline value, clickmap identity, state evidence, and no-battle correction
  target with `Farm`.
- Retained complete live active and inactive Farm frames plus a dedicated Farm
  slot template. Regression coverage requires the Farm identity and separately
  measures its green selected border; the former GC frame is retained as a
  negative so old text cannot satisfy the new invariant.
- Temporarily selected Tournament only to capture the inactive Farm border,
  then verified Farm restored by pixel evidence and returned to `NEW_BATTLE`
  Home. Automation remained in the operator's pre-existing paused state.

### 2026-07-18 Tournament configuration validator

- Added a compact Tournament contract and read-only live validator for Cards
  `Tournament`, Workshop `Tourney`, Bots `Amplify`, Guardian
  `Attack`/`Ally`/`Scout`, all nine Ultimate Weapons, Spotlight missiles, and
  the eight-slot Tournament/Milestone module loadout. Perks are explicitly
  excluded because Tournament battles do not have Perks.
- Reused the shared profile-driven session evaluator and guarded navigation
  route. The CLI requires persisted `PAUSED` control and fresh
  `RUNNING/TOURNAMENT` evidence, never selects or equips anything, uses the
  active battle for Cards, Ultimate Weapons, Modules, Bots, and Guardians, and
  uses the verified Exit Battle → Go Home route only for Workshop. It resumes
  only from authoritative Tournament evidence.
- Added separate Tournament/Tourney/Amplify slot identities plus green-border
  selection checks, Attack/Ally equipped states, a fresh name-reconciled
  Tournament module overview, and positive/negative fixture coverage. Farm
  fixtures prove that visible inactive Tournament labels do not satisfy the
  Tournament contract.
- The initial route exposed the current `Tournament Heat` title as a missing
  `BATTLE_HEAT` variant. Added its dedicated state template and visible close
  control, and made guarded cleanup recognize and close that dialog.
- The optimized in-battle route then exposed a static
  `navigation.menu_guild` coordinate as the Tournament Heat control. A later
  Trophy-layout recurrence proved that no single coordinate is authoritative:
  all in-battle side-menu destinations plus Event/Guild tabs now require
  visible template matches and tap their observed bounding boxes.
- Added a generated passive Tournament strategy. It attempts validation once,
  records conclusive mismatch evidence without requesting repair, permits only
  ad gems and terminal-result handling, persists terminal `WAIT`, and
  suppresses coin-display, recovery, Home, and mission actions. Floating-gem
  collection remains the normal bounded sweep started by an ad-gem collection;
  it is not a continuous Tournament handler.
- Live validation passed every configured requirement on the active Tier 17+
  Tournament. Cards, Ultimate Weapons, Modules, Bots, and Guardians remained
  in-battle; only Workshop used the resumable Home route. The observer returned
  to the same battle without Surrender or configuration changes, collected an
  ad gem, and completed a later status interval with no non-gem action.
- Added the distinct `TOURNAMENT_RESULTS` state and a non-dismissing result
  handler. The live natural result was recorded as a valid 144-row exact
  Round Stats report with summary/detailed wave agreement, then restored to
  Tournament Stats and left in `WAIT`; `OK` was never tapped. Tournament tier
  values such as `17+` are now structured minimum integers. Recent matching
  valid records suppress duplicate capture after restart.
- Regression coverage is in `test/test_tournament_results.py`,
  `test/test_tournament_observer.py`, `test/test_mission_reward_handler.py`,
  `test/test_gc_preflight_navigation.py`, and `test/test_battle_stats.py`.
  The implementation fix is `592acad`; the focused suite passed 73 tests, the
  full suite passed 325 tests, and clickmap integrity reported no errors.

### 2026-07-18 Farm preflight evidence and degraded-handler recovery

- Changed cross-scroll Ultimate Weapon aggregation to preserve nested toggle
  evidence, including the Poison Swamp Stun state that exists only on its
  detail panel.
- Split active/repairable preflight exclusivity from a terminal blocked result.
  A conclusive non-repairable failure still blocks strategy and mission work,
  but bounded ad-gem handling remains available; terminal Game Over behavior
  continues through the battle lifecycle.
- Regression coverage reproduces the live multi-scroll overwrite and verifies
  the degraded handler boundary. The focused suites passed 107 tests and the
  full suite passed 328 tests.
- Live validation on the preserved Tier 18 battle completed every Farm
  preflight requirement with `Poison Swamp: primary=on, stun=off`, released
  normal handlers, and collected the ad gem stranded by the old gate. The
  implementation fix is `453c484`.

### 2026-07-19 no-battle coverage and generated Farm validation

- Implementation commit: `6ed3b6f`.
- Completed the safe no-battle Home/submenu traversal from authoritative
  `HOME_SCREEN` plus `NEW_BATTLE` evidence. The existing Workshop, Cards,
  Modules, Lab, Store, Daily Missions, Event, Guild, Tournament, Settings,
  Ranking, Themes, Inbox, Vault, and Battle History states continued to
  classify without changing claims, purchases, presets, loadouts, or settings.
- Added explicit fixture-backed coverage for the formerly `UNKNOWN` Home Perks
  configuration and Milestones screens. The uppercase Perks configuration now
  shares the `PERKS` primary state, while Milestones has a dedicated
  `MILESTONES` state. The observed Currencies popup and Android Exit Game
  confirmation also have explicit overlay states over their existing primary
  screens. Both primary rules were re-exercised successfully against the live
  screens after the change; the Exit action was never selected.
- Live-validated the generated `farm` plan in one fresh process and an
  explicitly agent-owned Tier 18 battle: Home-only Farm setup passed, EHLS and
  EALS completed at waves 20/30, Damage Slider verified `1E-22%`, Target
  Priority matched the complete resolved order, every session-preflight
  requirement passed, and normal handlers resumed.
- The authorized guarded cleanup Surrender reached Game Over, persisted the
  resolved Farm configuration in
  `logs/battles/Battle20260719T101126-0700.json`, and returned Home without
  starting another battle. The intentionally short report lacked `killed_by`
  and `health_regenerated`; the recoverable capture path retained OCR/source
  evidence, saved the incomplete record, and continued to Home instead of
  forcing global `WAIT`.
- Regression coverage is in `test/test_ui_state_coverage.py` and
  `test/test_game_over_handler.py`. The UI-state suite passed 39 tests,
  `test/test_clickmap_access.py` passed 5 tests, state-definition validation
  passed, recursive clickmap/template integrity reported no errors, and the
  full repository suite passed 332 tests.

### 2026-07-19 Home Daily/Event badge handling

- Implementation commit: `6ed3b6f`.
- Added separate fixture-backed Daily Mission and Event badge measurement for
  Home. The in-battle route retains its closed-menu attention probe and open
  side-menu badge regions; Home now dispatches directly through visible Daily
  Missions and Event navigation evidence. Home Guild handling remains excluded
  until positive badge evidence can distinguish an alert from its static red
  icon.
- The first live no-battle Event pass exposed a retained-tab authority defect:
  Event opened on Bots, but the shared `EVENT` parent state allowed that content
  to be scanned and misreported as four incomplete missions. The handler now
  explicitly selects the visible Missions tab and revalidates `EVENT` before
  any Event Mission scroll, claim match, or inventory capture.
- The corrected normal Home runtime selected Missions, claimed both available
  Event rewards with visible claim-button evidence, logged
  `daily=0 event=2 guild=0`, and returned to complete `HOME_SCREEN` plus
  `NEW_BATTLE`; the Event badge was then absent while the deferred Daily badge
  remained. No battle was started or surrendered.
- Sunday ordinary Daily claims remain banked below capacity, but authoritative
  `8/8 Missions` panel OCR now releases exactly two ordinary rewards so new
  missions have room to arrive. Ambiguous or low-confidence capacity evidence
  fails closed; weekly chests and Event rewards remain eligible without
  consuming the two-claim relief budget.
- Regression coverage is in `test/test_mission_reward_handler.py`: its 29 tests
  passed, and the full repository suite passed 341 tests.

### 2026-07-19 Home ad-gem collection

- Implementation commit: `6ed3b6f`.
- Registered the existing five-gem Home artwork as the explicit
  `HOME_AD_GEMS_AVAILABLE` overlay using tracked available and unavailable Home
  fixtures. Home dispatch collects it before Home handling can start or resume
  a battle.
- Added a Home-specific guarded collection path. It stops any prior bounded
  in-battle tapper, requires a fresh visible `buttons.claim_ad_gem:home` match,
  permits no blind fallback, and verifies that the control disappears. The
  in-battle ad-gem handler retains its existing floating-gem tapper behavior.
- The first live attempt failed closed because state detection's padded region
  found the control while the zero-padding action matcher could not. The shared
  Home control region now covers the observed geometry, and the semantic button
  label reuses the proven full-control template.
- Normal-runtime validation matched and tapped the Home claim at `(124,251)`.
  The gem balance increased from 3564 to 3569, the overlay and action match
  disappeared, and the UI remained `HOME_SCREEN` plus `NEW_BATTLE`; no battle
  was started or surrendered.
- Regression coverage is in `test/test_home_ad_gem.py`. The impacted focused
  suites passed 105 tests before live validation, the post-correction focused
  suites passed 13 tests, and the full repository suite passed 345 tests.

### 2026-07-20 720p emulator compatibility

- Implementation commit: `15b2b8e`.
- Added a centralized screen-geometry boundary that accepts native
  `1080x1920` and `720x1280` captures, records geometry per ADB target,
  normalizes frames into canonical vision space, and maps canonical taps and
  swipes back to native device pixels.
- Calibrated affected Upgrade and Game Over evidence without replacing exact
  visible-action requirements. Retained fixture round trips and a live 720p
  terminal capture verified state detection, 24 ordered perks, and all 144
  clipboard Stats rows.
- Focused geometry, capture, state, clickmap, and Game Over validation passed
  72 tests.

### 2026-07-20 structured battle records and classification

- Implementation commit: `78b37d5`.
- Made structured Battle/Tournament records the canonical completed-run
  artifact and classified Farm, Tournament, and Milestone from strategy plus
  terminal evidence. Reports include resolved settings, ordered perks,
  derived rates, and bounded Coins/min progression; previous-wave lookup now
  reads the records rather than routine terminal screenshots.
- Extended the shared case-sensitive Tower-number scale through named
  magnitudes and `aa` onward. Focused record, classification, and Tournament
  validation passed 33 tests.

### 2026-07-20 managed native Windows control surface

- Implementation commit: `dd1b0f7`.
- Added a loopback versioned Linux API, fixed systemd user-service lifecycle,
  authoritative control acknowledgements, process/PID evidence, persisted
  strategy and ADB settings, guarded paused live-target handoff, and a
  next-run startup-gate policy for attaching to an existing battle.
- Added the self-contained WPF client with an owned passwordless OpenSSH
  tunnel, resizable operational layout, active-state highlighting, current and
  completed battle telemetry, report filters, runtime evidence, independent
  activity filtering, and selectable/copyable log rows. The browser client
  remains available as a fallback.
- The full Python suite passed 431 tests, and the Linux publisher produced a
  self-contained `win-x64` executable with Microsoft's WindowsDesktop SDK.

- Follow-up `86b5cec` added local UTF-8 CSV export, boundary-aware Strategy
  queue/replace/cancel and active-run adoption, exact Strategy filtering, and
  locally persisted main/Battle History window placement. It also kept
  current, pending, and acknowledged Strategy state distinct across the Linux
  API and native client. Unconfirmed mutex, filter-input, and compatibility
  repairs remain routed through their issue dossiers and live-validation tasks.

### 2026-07-21 startup gates and operator evidence

- Commit `e14999c` shortened Event Mission traversal to overlapping viewports;
  its focused handler suite passed 31 tests.
- Commit `5c6519a` generalized upgrade scanning to explicit column regions and
  repaired full-height Workshop Free Upgrade lock detection using a retained
  Shockwave fixture; its focused suite passed 13 tests.
- Commit `372cff3` separated concise operator `ACTION`/`STATUS` entries from
  paired diagnostic detail, made queued-tap success reporting authoritative,
  and defaulted browser/native activity to operational levels. Its focused
  validation passed 87 tests, including the separately permitted socket test.
- Commit `4ab91eb` replaced broad Force Continue with requirement-scoped gate
  decisions, a Farm Flame fallback, and optional strategy-aware Configure Run
  dialogs and CLI controls. The full Python suite passed 482 tests, and the
  repository-root Linux publisher produced the self-contained WPF executable.
- Commit `ef41ab9` made verified Home `NEW_BATTLE` setup the sole authority for
  all three Farm Free Upgrade locks, carried its evidence into session and run
  reports, and recorded missing attachment evidence as non-blocking
  `unavailable_deferred` until the next real boundary. Active preflight retains
  every unrelated requirement without invoking the lock scanner. Validation
  passed 484 sandbox tests plus the separately permitted localhost-socket test,
  for 485 total.
- Commit `5cd9efe` added explicit mid-run strategy adoption after fresh active
  battle evidence. It updates normal behavior and completed-run Farm identity
  without a restart, preserves default next-boundary queueing, and defers
  run-initialization, session-preflight, and Home-only gates until the next
  genuine boundary. The full suite passed 492 sandbox tests plus the separately
  permitted localhost-socket test, for 493 total; the repository-root Linux
  publisher also completed successfully.
- Commit `88b603c` replaced command-like strategy buttons and the adoption
  checkbox with a strategy dropdown and explicit queue/adopt actions. The
  client preserves an unsent choice across status refreshes, distinguishes
  selected/current/pending identity, and disables requests that would be
  no-ops. The repository-root Linux publisher completed successfully.
- Commit `2c06a66` made client/server compatibility explicit through Linux API
  revision and capability metadata. The Windows client disables unsupported
  adoption and, only after confirmation, can restart the one fixed Linux
  control-surface unit over its validated SSH destination before verifying the
  capability after reconnection. Focused validation passed 55 sandbox tests
  plus the separately permitted localhost HTTP test, for 56 total; the
  repository-root Linux publisher also completed successfully.
- Commit `ef8df58` generalized that compatibility decision. The Windows client
  now evaluates its expected API version, minimum Linux server revision, and
  required capability set; revision mismatch alone exposes the same generic,
  confirmed recovery path, and reconnection must satisfy the complete contract.
  Focused validation passed 15 sandbox tests plus the separately permitted
  localhost HTTP test, for 16 total; the repository-root Linux publisher also
  completed successfully.

### 2026-07-22 Workshop retained-mode recovery

- Commit `1505ec7` made the no-battle Free Upgrade lock gate recover when
  Workshop opens on its retained Enhance mode. It selects the explicit Upgrade
  control, reacquires Workshop evidence, and then navigates to the required
  Attack or Defense upgrade category.
- A focused simulator regression starts in Enhance and verifies the navigation
  order. The no-battle integration suites passed 53 tests; the full suite
  passed 493 sandbox tests plus the separately permitted localhost-socket test,
  for 494 total.

### 2026-07-22 Home-boundary preflight and runtime responsiveness

- Commit `dacb715` moved every persistent Farm check available from Home
  `NEW_BATTLE` into complete no-battle setup: Cards, Workshop and Free Upgrade
  locks, Bots, Guardians, Modules, and Target Priority. Serialized
  screen-derived evidence now satisfies the corresponding session-preflight
  requirements, so a newly started battle checks only Auto Pick Perks and
  Ultimate Weapons instead of returning Home. Existing-battle attachments
  retain the guarded compatibility route. The focused suite passed 130 tests.
- Commit `152d3be` anchored Guild reward-badge measurement to the matched Guild
  icon. A retained positive frame and same-layout negative prove badge
  detection when an active Tournament Trophy displaces Guild; the reward
  handler suite passed 32 tests.
- Commit `e0b246f` changed Damage Slider enforcement to batch only exact
  power-of-ten exponent gaps, reacquire settled OCR evidence afterward, and
  recompute dropped steps. Unknown sequences retain single-step feedback and
  partial dispatch failures stop after verification. Damage Slider and run
  initialization validation passed 87 tests.
- Repository-wide validation passed 502 sandbox tests plus the separately
  permitted localhost-socket test, for 503 total.

### 2026-07-22 Tournament boundary preflight and attachment advisories

- Commit `ea7e548` added a corrective Tournament setup at verified Home
  `NEW_BATTLE`. It selects Tournament Cards, Tourney Workshop, Amplify Bots,
  Attack/Ally/Scout Guardians, and Tournament Modules, retains their boundary
  evidence for the Ultimate Weapon-only in-battle check, and deliberately
  leaves Tournament entry manual.
- Tournament attachment now runs its declared read-only preflight instead of
  suppressing it with other startup gates. Authoritative mismatches publish a
  non-blocking browser/native decision with pause, retry, and scoped-continuation
  choices while natural terminal capture remains active.
- Completed Tournament identity follows the distinct Tournament Results screen,
  and terminal-observed Tier is retained independently of strategy identity. A
  no-strategy standard Game Over reports its Tier while remaining `unknown`.
- Repository-wide validation passed 514 sandbox tests plus the separately
  permitted localhost HTTP test, for 515 total. Browser JavaScript syntax and
  the repository-root Linux WPF publisher also completed successfully. No live
  device interaction was used for this code-only change.

### 2026-07-22 Guarded active-battle automation reload

- Commit `3216fb9` added **Reload automation for current battle** to the native
  and browser control surfaces. The fixed automation unit is replaced without
  persisting ordinary `STOPPED`: the existing runtime first acknowledges Pause
  and publishes a fresh `RUNNING` observation, then the replacement must prove
  a distinct MainPID, matching held ADB lock, one-launch `next_run` policy,
  Pause consumption, and its first status before prior control intent returns.
- The configured cold-start policy is restored immediately after the attached
  launch environment is copied. Launch or verification failure remains paused;
  initial owner/precondition rejection changes nothing. Repeated same-state
  directives now have unique identities, are acknowledged by the runtime, and
  force a fresh next-frame status sample without authorizing actions.
- Repository-wide validation passed 523 sandbox tests plus the separately
  permitted localhost HTTP test, for 524 total. Browser JavaScript syntax and
  the repository-root Linux WPF publisher also completed successfully.
- Live validation on 2026-07-22 reloaded the active No Strategy Attack
  Dissonance battle at wave 3314. The original PID acknowledged Pause and
  exited cleanly; a distinct MainPID acquired the refreshed ADB lock, attached
  once with `next_run`, acknowledged Pause, and restored `RUNNING` at wave 3315
  while the configured policy returned to `immediate`. Gate re-arming under a
  strategy that actually declares gates remains in the runtime backlog.

### 2026-07-22 No Strategy observed-run inventory

- Commit `28faa29` made No Strategy a two-phase observation profile without
  adding configured intent or strategy action authority. It passively records
  actual selected presets, Guardian chips, Modules, Target Priority, Damage
  Slider, Auto Pick state, and Ultimate Weapon toggles when their screens are
  visible. Missing fields remain explicit rather than inheriting Farm or
  Tournament values.
- A localized purple sword badge beside Tier records Attack Dissonance identity
  and supports high-confidence `dissonance` classification at standard Game
  Over. Schema-version-3 battle records keep this and every other actual value
  under `observed_run_configuration`, separate from `run_configuration`, with
  field source, phase, confidence, and timestamp.
- Natural No Strategy Game Over now forces full structured capture and Home.
  Verified `NEW_BATTLE` owns a read-only inspection of the three supported Free
  Upgrade locks, then holds Cards until the operator opens Perks configuration.
  First Perk, Ban Perks, and Auto Pick tabs are guarded, fully scrolled, OCRed
  in selected-row order, and backed by retained page images; uncertain results
  stay raw instead of becoming invented settings. The same battle JSON and
  Markdown are updated before normal Home/start handling is released.
- Repository-wide validation passed 543 sandbox tests plus the separately
  permitted localhost HTTP test, for 544 total. The current ignored frame read
  1,071 badge-purple pixels against a 500-pixel threshold; eight retained
  `RUNNING` fixtures were negative.
- Commit `d79153c` records the completed natural Game Over validation for
  `Battle20260722T185119-0700`: Attack Dissonance identity and the complete
  observed-run inventory were retained, Home finalization captured the ordered
  Perks configuration, and the held boundary released through normal handling.

### 2026-07-22 automatic No Strategy configuration traversal

- Commit `9285979` replaced operator-presented configuration screens with an
  automation-owned, read-only in-battle pass. It verifies source and
  destination states while
  visiting Cards, in-battle Perks, every bounded Ultimate Weapon viewport,
  Modules, Event Bots, Guild Guardians, and Target Priority, then restores the
  battle. Damage Slider is read when Attack is accessible and is explicitly
  unavailable on Attack Dissonance rather than probing its disabled menu.
- Post-run capture now records the Workshop preset with the read-only Free
  Upgrade lock pass, opens Cards, expands the Home menu, independently verifies
  the retained Perks item region, and opens/captures Perks configuration without
  operator input. The verified `NEW_BATTLE` boundary remains held until all
  three tabs are captured and the same record is updated.
- Both phases synchronize Pause before every input. A mid-pass Pause sends no
  cleanup action; Resume restores a known read-only screen or verified Home and
  retries the current stage. Focused traversal, pause, terminal-state, visual-
  guard, app-stage, observer, record-rendering, and clickmap tests cover the new
  authority boundaries.
- The first live pass exposed two fail-closed navigation/state defects.
  Commit `4565ab4` avoids tapping an already-selected battle menu, and commit
  `d26f633` makes underlying `RUNNING` evidence yield to a specific modal such
  as Perks. Repository-wide validation passed 555 sandbox-compatible tests;
  the single localhost HTTP test passed separately with socket permission, for
  556 total.
- Live validation on the active Tier 18 Attack Dissonance run attached PID
  `3899024` with startup gates deferred, recovered from the retained Perks
  screen, and completed Cards, Perks, bounded Ultimate Weapon scrolling,
  Modules, Event Bots, Guild Guardians, and Target Priority at 17:26:49. It
  returned to `RUNNING` at wave 4120 with the target lock held and every current
  control acknowledged. No configuration control, Home, Exit Battle, or
  Surrender action was used. The future cold-start policy was restored to
  `immediate`; natural Game Over/post-run validation remains pending.

### 2026-07-22 selected-strategy Home setup and Tier 19 start

- Commit `9ebfabc` made the selected native-client strategy part of the
  managed process-start transaction. The control and managed environment now
  contain that strategy before systemd launch, with revision/capability
  compatibility preventing an older server from accepting the dependent
  action.
- Live Home setup exposed and resolved four fail-closed transition/evidence
  gaps: `9f030a8` waits for Guardian inventory after an emptied slot,
  `c942b8a` fills an exact missing Scout slot, `f6a6def` tracks animated
  equipped-module icons within a bounded neighborhood, and `c8b90da` waits for
  a tapped module detail before OCR. All eight Farm modules were corrected and
  authoritatively revalidated before Battle.
- Commit `32cfdbc` added the missing in-run Auto Pick Perks correction. It acts
  only on verified disabled evidence in the Perks panel and requires fresh
  enabled evidence. Live evidence rose from zero to 1,850 green pixels; the
  complete retried preflight passed with no failed checks and released normal
  Farm strategy actions.
- Repository-wide validation passed 590 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 591 total. The earlier
  selected-strategy change also published the self-contained Windows client
  successfully on Linux.
- Final live validation atomically started `farm_t19_experiment`, completed
  Cards, Workshop, Free Upgrade locks, Bots, Fetch/Summon/Scout, and the exact
  Modules loadout before the 22:01:03 Battle tap. The resulting Tier 19 run
  completed EHLS/EALS initialization, corrected Poison Swamp Stun and Auto Pick
  Perks, completed session preflight at 22:10:48, and remained under normal
  `RUNNING` automation.

### 2026-07-23 verified tap authority and Target Priority boundary

- Commit `d410b61` removed the nonexistent Home Target Priority route. Complete
  Home setup now retains explicit `battle_only_control` evidence, which cannot
  satisfy the gate; the generated `RUNNING` action remains the sole owner of
  Target Priority observation or enforcement.
- `safe_tap` now fails closed for every coordinate or matchless named runtime
  target unless the caller supplies complete current-frame evidence, a bounded
  target region, and a target-specific verifier. Template-backed names always
  rematch before dispatch and cannot fall back to configured coordinates.
- Added retained-evidence templates for Home navigation, in-battle Target
  Priority, Perks, Workshop modes, Exit Battle, Damage Slider arrows, and the
  missing Scout inventory control. Dynamic upgrade, module, Perks, Ultimate
  Weapon, buy-quantity, and dialog actions now reidentify their exact target or
  authoritative containing control before tapping.
- Commit `d410b61` initially stopped level-skip taps during capture and limited
  one stream frame to one purchase. The urgency-specific entry below records
  the operator-directed replacement of that short-lived constraint. The
  bounded moving-gem sweep remains the only allowlisted blind runtime tapper;
  unchecked gesture taps are isolated to explicit operator tooling.
- Clickmap and state-definition validation passed. Repository-wide validation
  passed 614 sandbox-compatible tests plus the separately permitted localhost
  HTTP test, for 615 total. No live process or device interaction was used.

### 2026-07-23 urgent initial-frame purchase authority

- Commit `5b9f0a2` added an explicit reusable mode to `TapVerification`. It
  evaluates one complete, target-specific initial frame and caches that verdict
  for a caller-owned bounded sequence; static audit coverage limits the mode to
  `core/level_skip_initializer.py` and `core/damage_adjuster.py`.
- EHLS/EALS again continues purchase taps while a raw screenshot is in flight
  and reuses an unchanged live-stream frame until a newer result frame arrives.
  The first frame must still verify `RUNNING/UTILITY_MENU`, the exact target
  box, and a non-Max state.
- Damage Slider now carries the authoritative panel frame with its OCR reading,
  matches the required direction arrow once, and reuses that matched point for
  the exact bounded batch. Settled OCR and strict-progress checks still run
  after each batch.
- Clickmap and state-definition validation passed. Repository-wide validation
  passed 616 sandbox-compatible tests plus the separately permitted localhost
  HTTP test, for 617 total. No live process or device interaction was used.

### 2026-07-23 battle speed and Home-owned Poison Stun

- Commit `6d5f331` added a global battle-only game-speed guard. It verifies the
  localized value and plus control, discovers the current maximum by observed
  progress, and periodically restores a slowdown. Farm explicitly withholds
  its action authority until both EHLS and EALS are complete.
- Commit `b19dfce` moved Poison Swamp Stun to verified no-battle Home Workshop
  setup. The live source locator uses the Ultimate Upgrades heading and exact
  Poison Swamp title to derive an isolated icon target, while session preflight
  consumes the fresh Stun proof and still requires the in-battle primary
  toggle. Attachments retain the guarded battle fallback.
- The same checkpoint repaired Perks startup navigation by replacing dynamic
  progress digits with a stable, bounded bar-edge verifier. The exact
  `80 / 191` failure frame is retained as a regression.
- Bounded live inspection ran only while automation was stopped, measured Stun
  `off`, and restored verified Home `NEW_BATTLE` without changing the control
  or starting a battle. Repository-wide validation passed 630
  sandbox-compatible tests; the one localhost HTTP test passed separately with
  socket permission, for 631 total.

### 2026-07-23 game-speed maximum-probe correction

- Commit `1f6385a` replaced the original no-effect ceiling probe with the
  operator-confirmed normal maximum. Authoritative `x5.0` and perk-raised
  `x6.3` readings dispatch no input; a lower value receives bounded verified
  `+` taps only until the reading reaches at least `x5.0`.
- Stable satisfied readings remain checked every 30 seconds but no longer
  repeat the same no-op log entry. Failures, changed readings, and actual
  corrective taps remain visible.
- `test/test_game_speed.py` covers zero-input handling at `x5.0` and `x6.3`,
  bounded restoration from below `x5.0`, no-progress failure, Pause authority,
  battle-only scope, EHLS/EALS priority, and stable-log suppression.
  Repository-wide validation passed 641 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 642 total.
- A guarded active-battle reload attached the replacement without replaying
  startup/session gates and restored the prior control intent. The replacement
  reported `taps=0` and `target_satisfied` at `x6.3`; the following complete
  guard interval produced no speed input or repeated no-op log.

### 2026-07-23 module level-transfer preservation

- Commit `2a2d00b` replaced the shared module repair's hard-coded decline action
  with a verified acceptance of every presented level-transfer dialog. The same
  path owns both Primary and Assist replacements, and correction now stops if
  the acceptance cannot be authorized or the dialog does not dismiss.
- The correction preserves the role-based level allocation during module
  changes. At the reported progression boundary, the operator's expected state
  was level 201 for every Primary and the highest available levels for Assist
  modules (then approximately 193–194); those progression-dependent values are
  operational evidence rather than hard-coded policy.
- Regression coverage exercises the complete Equip → role selection → transfer
  acceptance sequence independently for Primary and Assist, the failed-accept
  path, and removal of the old decline clickmap action. Repository-wide
  validation passed 634 sandbox-compatible tests plus the separately permitted
  localhost HTTP test, for 635 total. No live module replacement was performed
  during validation.

### 2026-07-26 Home evidence and repair-Surrender boundary

- Commit `71f7327` prevents a later Cards inventory/detail frame from replacing
  the authoritative preset-selection frame in retained Home evidence.
  Contradictory combined evidence now fails before Battle instead of acquiring
  in-battle Home-repair authority.
- Recoverable Home setup failures rerun the complete guarded workflow from a
  fresh Home capture, with three attempts total before publishing a blocking
  gate decision. Interruptions and unsupported configurations do not loop.
- An automation-owned configuration-repair Surrender now uses Game Over only
  as a guarded Home transition. It bypasses Perks/More Stats and battle-record
  capture; natural endings retain the ordinary terminal pipeline.
- Focused Home, preflight, control, Tournament, and Game Over validation passed
  190 tests. Repository-wide validation passed 768 sandbox-compatible tests
  plus the separately permitted localhost HTTP test, for 769 total.
- Guarded live activation replaced PID `3793479` with `3842234`, attached to
  the same Tier 19 battle with `next_run` semantics, restored the configured
  `immediate` cold-start policy and `RUNNING` intent, and advanced from wave
  1852 to wave 1879 without replaying Home/session gates or repair.

### 2026-07-26 Auto Pick boundary and repair authority

- Commit `e13e498` makes the visible `Rankings Unlocked` divider an
  authoritative end to Home Auto Pick rank capture. An OCR omission above that
  boundary now yields incomplete evidence instead of borrowing an unranked row
  and fabricating a complete mismatch.
- Only a complete, recognized capture can authorize an Auto Pick reorder.
  Real mismatches skip their already-verified prefix and no longer repeat a
  second full-list lookup for an already-confirmed rank; guarded row
  reacquisition, exact one-rank progress, and final full-list verification
  remain required.
- Home Perks, no-battle setup, and run initialization validation passed 131
  tests. The divider detector also matched the retained incident frame and all
  four protected historical Auto Pick capture sets without device
  interaction.

---

## 📘 Documentation

- 2026-07-16: Split the active backlog by domain, separated open anomalies from
  resolved operational history, and extracted current runtime architecture from
  the dated review handoff. Added an on-demand handoff template that excludes
  stale runtime and validation claims plus an on-demand maintenance guide for
  future lifecycle updates. Preserved the complete pre-split backlog and
  architecture narrative under dated history paths.
- Created `core/input_policy.md` to document dual-path tap architecture
- Updated `README_UPLOAD.md` with summary of input tap architecture and assistant behavior
- Updated `PROJECT_SCOPE.md` to reflect dual-path tap architecture, overlay support, and tap handler split
