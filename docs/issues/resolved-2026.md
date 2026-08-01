# Resolved Issues and Operational History — 2026

This archive preserves resolved symptoms, evidence, fixes, regression links,
and dated operational lessons. It is historical evidence, not an active
backlog. Current anomalies live in [`../observed_issues.md`](../observed_issues.md),
and actionable work lives in
[`../../PENDING_DEVELOPMENT.md`](../../PENDING_DEVELOPMENT.md).

## Resolved issues

### Tournament attachment preflight stranded the enabled ad-gem handler

- **Observed:** 2026-08-01 after the managed runtime attached to an active
  Tier 17+ Tournament on `localhost:5555` with Tournament selected and attached
  validation enabled.
- **Symptom:** The Tournament inventory pass completed successfully, but a
  visible five-gem claim remained on screen while repeated runtime observations
  continued to report `AD_GEMS_AVAILABLE`. No ad-gem action or result followed.
- **Evidence:** `logs/actions.log` records the successful attached validation at
  02:19:56, then adds `AD_GEMS_AVAILABLE` at 02:26:51 and still reports it at
  02:30:56. The expected final app-level `Validation complete` transition never
  appears. Static tracing confirmed that the generated attached-only rule set
  `gc_session_preflight_attempted` and `gc_session_preflight_completed`, while
  Tournament completion also required `damage_slider_checked` and
  `orb_distance_checked`.
- **Safety response:** Diagnosis used control, owner/lock, ADB-state, action-log,
  source, and one read-only screenshot inspection. Before rollout, the active
  Tournament was not manually tapped, exited, or Surrendered. Activation used
  the guarded current-battle reload, which paused the old owner, verified the
  attached replacement, and restored `RUNNING` without restarting the battle.
- **Cause:** While startup gates were deferred and
  `attached_validation_requested` was true, `YamlStrategy.tick` admitted only
  the generated `attached_validation_only` rule. Its conclusive inventory
  result could not release the gate because the otherwise explicit
  `run_when_attached` Damage Slider and Orb Distance rules were filtered out,
  leaving the app to suppress normal handlers as though validation were still
  navigating.
- **Resolution:** The attached-only inventory pass keeps exclusive authority
  until it reaches a conclusive result. The evaluator then admits explicitly
  declared `run_when_attached` battle-only rules, allowing Tournament to enforce
  Damage Slider `100%`, enforce or safely preserve Orb Distance according to
  authoritative Attack Range, and close the session gate. Ad-gem collection
  then uses the same handler as Farm, which starts one bounded 20-second
  floating-gem sweep; Tournament still has no independent continuous tapper.
- **Regression:**
  `test/test_run_initialization.py::DeferredStartupGateTests::test_attached_tournament_validation_stages_observer_then_battle_controls`
  covers the complete staged plan, and
  `test/test_tournament_observer.py::test_tournament_main_loop_collects_ad_gem_after_attached_gate_releases`
  proves the runtime releases the visible ad-gem handler. Existing
  `test/test_home_ad_gem.py` coverage retains the bounded sweep contract.
- **Validation:** The focused Tournament, initialization, validation, Orb
  Distance, Damage Slider, ad-gem, and builder suites passed 182 tests. The
  complete repository suite passed all 997 tests. A guarded live reload then
  replaced PID `3470028` with `3509151` in the same Tournament. The replacement
  verified Damage Slider `100%`, changed and verified Orb Distance to Extra
  `87.16m` / Workshop `80.37m` for Attack Range `98.38m`, completed the session
  inventory pass, collected the visible ad gem, and ended its floating-gem
  sweep after exactly 20 taps in 20 seconds. Control remained `RUNNING`; no
  Surrender or battle restart occurred.
- **Fixed by:** `a8dda82`.

### Game-speed verification trusted inconsistent OCR and forgot a proven ceiling

- **Observed:** 2026-07-31 during a Tier 17+ Tournament battle and again on
  2026-08-01 after a Tier 19 Farm target change from exact `x4.0` to maximum
  available.
- **Symptom:** A no-change `+` ceiling probe from the correct `x5.0` speed
  reported `final=3.0` and `reason=speed_decreased_after_plus`. On the later
  run, the first `+` advanced `x4.0` to `x4.5`, but all bounded post-tap OCR
  reads failed and the action was reported as failed before a later retry
  reached and confirmed `x5.0`. Transient OCR failures also discarded the
  earlier `x5.0` ceiling proof and caused otherwise unnecessary repeat probes.
- **Evidence:** The first log sequence contains an isolated `x3.0` status
  bracketed by `x5.0` observations without a speed input, then the false
  post-`+` decrease at 20:07:43 and a correct `x5.0` confirmation 14 seconds
  later. The recurrence records a `+` from `x4.0` at 01:32:09, four failed
  post-tap reads, later authoritative `x4.5`, and recovery to `x5.0` at
  01:33:05. Fresh read-only captures after both incidents visibly and
  programmatically read `x5.0`. This recurred after the maximum-target selector
  deliberately restored active `x5.0` ceiling probes; it is distinct from the
  obsolete repeated-probe policy fixed by `1f6385a`.
- **Safety response:** Diagnosis used control, owner-process, ADB, screenshot,
  log, and source inspection without sending device input. Before live
  activation, fresh evidence showed operator-owned `PAUSED` control and a
  Welcome Back / resume dialog rather than an active `RUNNING` frame, so the
  guarded reload was not attempted and the pause and battle were left intact.
- **Cause:** Post-tap settling accepted the first numerically different OCR
  reading, including a directionally impossible decrease after `+`. Invalid
  OCR reset same-value stabilization instead of allowing bounded agreement
  across gaps. Separately, the guard inferred a proven normal ceiling only
  from its immediately previous result, so any transient read failure erased
  that proof.
- **Resolution:** Post-tap verification now requires two agreeing,
  directionally consistent readings within the bounded capture window.
  Impossible readings are ignored and logged with raw text and confidence;
  partial evidence without consensus remains invalid. Unreadable post-input
  outcomes are reported as deferred rather than falsely failed. The guard
  retains a proven `x5.0` maximum through transient OCR failures and clears it
  only on a target or battle boundary.
- **Regression:** `test/test_game_speed.py` covers the observed false `x3.0`
  after `+`, matching progress readings separated by OCR gaps, deferred
  post-input verification, and maximum-ceiling retention across an OCR
  failure.
- **Validation:** The focused game-speed suite passed 26 tests. The complete
  repository suite passed all 993 tests. Live activation remains pending until
  the same runtime is again safely observable as an active battle.
- **Fixed by:** `852febf`.

### Perk timeline restart and hidden-UI gaps lost or misattributed selections

- **Observed:** 2026-07-31 during repository-local review of same-battle
  reattachment and deferred Perk-selection checks.
- **Symptom:** Replacing the process discarded the in-memory Perk baseline and
  batches. Separately, when another UI hid the top-bar progress across several
  selections, the next visible token could arm only the oldest inferred
  boundary while the newest-row optimization attributed the latest changed
  Perk to that wave without recording the missing interval.
- **Evidence:** A deterministic tracker simulation advanced directly from an
  armed wave 142 boundary to the wave 184/next-226 progress token. The old
  request retained scheduled wave 142, captured only the newest row, and
  emitted no incomplete-interval warning. Source inspection also confirmed
  that application construction always created a new observer with no durable
  baseline or batch state.
- **Safety response:** Diagnosis and validation were repository-local. No
  runtime process, control directive, ADB target, emulator, or battle was
  inspected or changed.
- **Cause:** Perk progress was process-scoped, and the latest-row capture
  contract assumed at most one selection unless the tracker had observed every
  intermediate top-bar token while the battle UI was visible.
- **Resolution:** Commit `07efc5a` adds an atomic, schema-validated checkpoint
  keyed to the existing Current-run activity scope, including pending capture
  and owned-route state. Every selection check now scans newest-first to the
  first confidently recognized persisted family/value that is unchanged and
  falls back to the proven list bottom if none exists. Process and visibility
  gaps remain explicitly incomplete interval aggregates; exact per-wave
  reconstruction is allowed only when all boundaries were observed and each
  post-PWR change is distinct. Repeated upgrades to one family retain only
  their final net level and are not assigned invented waves.
- **Regression:** `test/test_perk_timeline.py` covers scanning past a changed
  former newest row, arbitrary hidden selections, repeated-family ambiguity,
  same-scope restart, route ownership, restart catch-up, and scope mismatch.
  `test/test_scrolling.py` covers caller-proven early termination, and
  `test/test_battle_stats.py` covers incomplete-interval rendering.
- **Validation:** All 958 repository tests passed during the change. After
  final review fixes, the focused timeline, scrolling, reporting, and run-
  initialization suite passed all 149 tests.
- **Fixed by:** `07efc5a`.

### Known ADB outage flooded the complete action log

- **Observed:** 2026-07-31 after the operator requested indefinite Pause at
  `08:28:18` PDT and stopped the BlueStacks emulator on `localhost:5555`.
- **Symptom:** From the first empty capture at `08:28:31` through the diagnostic
  cutoff at `11:28:02`, the runtime wrote 22,116 outage-related lines and made
  5,699 logged connection attempts, or approximately one every 1.9 seconds.
  The useful persistent-outage warning correctly appeared after three failures
  and then approximately every five minutes, but it was buried under per-cycle
  capture and reconnect detail.
- **Cause:** The Linux automation runtime remained alive and correctly retained
  `PAUSED`; stopping the native Windows GUI does not stop that runtime.
  `App._capture_frame()` first called the screenshot path, whose empty result
  logged an `ERROR`; it then called the reconnect helper, which logged attempt
  and failure `DEBUG` entries; and finally logged a capture `FAIL` before a
  two-second delay. The independent 30-second watchdog called the same helper.
  The shared warning counter rate-limited `WARN`, but it neither coalesced the
  lower-level entries nor serialized reconnect scheduling across callers.
- **Safety response:** Pause blocked strategy, handler, and watchdog inputs.
  The outage produced no emulator input and did not cause the runtime to infer
  that the game process was absent. The failure was excessive logging and
  reconnect frequency, not action-authority leakage.
- **Resolution:** Commit `5548835` adds one process-shared, thread-safe
  coordinator with independent state per target and bounded reconnect backoff.
  Known disconnection now skips screenshot commands and per-cycle transport
  errors while the main loop keeps its two-second control polling. Persistent
  degradation retains the initial warning and five-minute reminders; one
  recovery `RESULT` follows the first supported fresh frame. Connected capture
  corruption still emits its diagnostic and terminal failure evidence. An
  explicit paused target handoff validates its new target independently and
  retains the old target and schedule on failure.
- **Regression:** `test/test_adb_connection.py` covers long outages, bounded
  backoff, concurrent callers, supported-frame recovery, and target switching.
  `test/test_app_control_sync.py` covers known-outage capture suppression,
  connected corruption, two-second control polling, and explicit handoff.
  `test/test_ss_capture.py` covers structured empty/malformed outcomes, and
  `test/test_watchdog.py` retains paused action-authority coverage.
- **Validation:** All 946 repository tests passed. Validation was repository-
  local; the live paused runtime and unavailable emulator were not changed.
- **Recurrence:** On 2026-08-01 the operator again requested indefinite Pause
  and intentionally stopped BlueStacks, this time while the GUI-owned SSH
  reverse listener remained open on Linux `localhost:5555`. ADB retained both
  `emulator-5554 offline` and `localhost:5555 offline`. Beginning immediately
  after the 01:34:15 Pause acknowledgement, the runtime again emitted two empty-
  capture `ERROR` entries and one terminal `FAIL` approximately every three
  seconds. A direct exact-target `get-state` returned `device offline`; no input
  followed Pause.
- **Recurrence cause:** The strict device-list check correctly rejected the
  `offline` row, but the fallback `adb connect` returned an `already connected`
  hint for that stale transport. The coordinator accepted the command text as
  success without rechecking target state, so it misrouted an ordinary outage
  through the intentionally diagnostic connected-capture-corruption path.
- **Follow-up resolution:** Commit `0346a1b` refreshes only the selected TCP
  transport with target-specific `adb disconnect`/`adb connect`, then requires
  a fresh exact-target `device` observation regardless of command output.
  Offline, unauthorized, and absent targets now enter the existing bounded,
  quiet outage schedule. Recovery still requires a supported fresh frame.
- **Follow-up regression:**
  `test/test_adb_connection.py::test_reported_connect_success_requires_post_connect_device_state`
  covers the misleading success hint and
  `test/test_adb_connection.py::test_tcp_reconnect_refreshes_only_the_selected_target`
  constrains refresh scope. The long paused-outage integration test in
  `test/test_app_control_sync.py` now uses that exact stale-transport shape.
- **Follow-up validation:** The focused outage suite passed 24 tests, the
  broader runtime/control suite passed 214 tests, and all 995 repository tests
  passed. The operator-owned runtime remained paused and was not reloaded while
  its target was offline.
- **Fixed by:** `5548835`, follow-up `0346a1b`.

### Demon Mode tracker falsely treated its disabled Intro Sprint button as absent

- **Observed:** 2026-07-30 during the Tier 19 Farm battle later retained as
  `Battle20260730T204649-0700`.
- **Symptom:** `logs/actions.log` reported a first Demon Mode activation at
  approximately wave 490 and retained
  `screenshots/matches/SurvivalActivation20260730T200505-0700_demon_mode_01_first_absent.png`.
  The evidence frame is visibly still in Intro Sprint at wave 480 and still
  shows the disabled Demon Mode button in the third floating-button position.
  Activating Demon Mode would itself have ended Intro Sprint.
- **Evidence:** Replaying the matcher against the retained frame localizes the
  Demon Mode button at `(335,807,126,85)` with confidence `0.783562`, narrowly
  below the tracker's custom `0.800` presence threshold; Nuke remains a
  positive match at `0.949987`. In the post-activation comparator
  `screenshots/matches/SurvivalActivation20260730T150618-0700_demon_mode_01_first_absent.png`,
  the rectangular button is absent after the floating controls reflow and the
  best non-button candidate scores `0.506201`. The terminal battle report
  records one eventual Demon Mode use, but the false first event disarmed
  further tracking, so its actual activation wave cannot be recovered.
- **Safety response:** Diagnosis and validation replayed retained images only.
  They did not change a battle, emulator, runtime, or control state.
- **Cause:** Button disappearance was the tracker's only Demon Mode activation
  authority. It did not consult the noisy but independently visible top-left
  Intro Sprint status, so two below-threshold button matches could report an
  impossible activation while Intro Sprint was still active. Floating buttons
  can reflow when any of Demon Mode, Nuke, or Missile Barrage disappears, so
  fixed-slot continuity was not a valid repair.
- **Resolution:** The tracker now matches the top-left Intro Sprint status and,
  once observed, vetoes Demon Mode disappearance until five consecutive clean
  status absences confirm its exit. A failed or obscured status match resets
  that absence streak. The normal two-frame button-disappearance confirmation
  then runs without anchoring Demon Mode to a fixed slot.
- **Regression:** `test/test_battle_activation_tracker.py` covers the retained
  wave-480 false-positive and post-activation crops, a third-to-second-slot
  reflow, noisy status matching, and intermittent status-match failures.
- **Validation:** All 938 repository tests passed. The recursive clickmap
  integrity check and state-definition validation also passed.
- **Fixed by:** `4ac2fc0`.

### EHLS/EALS startup race delayed the first EALS purchase

- **Observed:** 2026-07-30 during repeated authorized Tier 19 Farm startup
  tests after additional Enemy Health and Attack Level Skip Workshop levels
  were purchased.
- **Symptom:** The initializer did not begin EALS until wave 20 in the first
  failing run. Disabling an ineffective live-stream warm-up moved the first
  EALS tap to wave 10, but it arrived only just before the wave transition and
  final confirmation still lagged.
- **Evidence:** The H.264 frame stream never became live during the short
  startup window and always fell back after contending with guarded captures
  and inputs. The screenshot fallback then waited for EHLS feedback before
  beginning EALS, while its original unbounded EALS tap loop could delay the
  feedback capture itself.
- **Safety response:** Every test battle was explicitly owned and authorized
  by the operator. Automation acknowledged `PAUSED` and `WAIT` before each
  Surrender, Retry was dispatched manually from a fresh Game Over frame, and
  `RUNNING / RETRY` was restored only after a fresh running-battle frame. The
  ordinary Game Over handler did not capture stats or choose a route for these
  test transitions.
- **Cause:** The optional stream warm-up added contention without producing
  frames on the active emulator. After fallback, EHLS and EALS used serialized
  feedback cycles, and EALS had no tap cap during a blocking screenshot.
- **Resolution:** Guarded screenshots are now the production default. Each
  fallback feedback cycle sends the bounded EHLS burst first, immediately
  begins a separately bounded EALS burst, and uses that EALS-time screenshot
  as feedback for both upgrades. The injectable live-stream path remains for
  tests and future environments where it actually becomes live.
- **Regression:** `test/test_level_skip_initializer.py` covers the production
  screenshot default, EHLS-before-EALS paired-cycle ordering, reusable tap
  authority during capture, and independent EHLS/EALS burst limits.
- **Validation:** All 922 repository tests passed. In the final live run, the
  first EALS purchase moved from the earlier wave-10 edge to wave 1 at 4.89
  seconds; EHLS confirmed Max at wave 10. EALS still confirmed at wave 20
  despite receiving inputs from wave 1, identifying remaining progression as
  an in-run cash/level limit rather than the startup race.
- **Fixed by:** `b8229d5`.

### Home Perk close accepted a transient UNKNOWN battle control

- **Observed:** 2026-07-29 during Farm Tier 19 Home setup after updating the
  shared 17-slot Auto Pick Perks order.
- **Symptom:** Startup stopped after three setup attempts at
  `perk_configuration (home_setup)` with
  `Perk configuration requires NEW_BATTLE, got UNKNOWN`, even though the game
  had returned Home.
- **Evidence:** After the Inner Land Mines move, the first post-input scan
  still reported rank 17, while the next attempt found the intended order
  without another Perk input. On two later attempts, the first frame after
  closing Perks was classified as `HOME_SCREEN` while battle-control OCR was
  still `UNKNOWN`; repeated runtime observations 7–12 seconds later reported
  `NEW_BATTLE`. A fresh diagnostic screenshot also authoritatively detected
  Home with `NEW_BATTLE`.
- **Safety response:** The failed requirement was never bypassed. Diagnosis
  remained read-only until the runtime was persistently Paused and had
  acknowledged both the pause and Home. The process was then replaced cleanly
  under that pause, the exact requirement was retried, and the prior RUNNING
  state was restored.
- **Cause:** Perk close returned on the first `HOME_SCREEN` frame and then
  required `NEW_BATTLE` from a single battle-control observation, before that
  portion of the Home UI had settled. Perk-order repair likewise treated its
  first unchanged post-input scan as terminal even when the tap was still
  taking effect.
- **Resolution:** Perk close now waits for the combined authoritative
  `HOME_SCREEN` and `NEW_BATTLE` boundary, retrying only `UNKNOWN` and still
  failing closed on a conflicting known control. An apparently unchanged
  post-input rank gets one delayed, read-only confirmation scan without
  issuing a duplicate input.
- **Regression:** `test/test_home_perk_configuration.py` covers delayed rank
  progress and Home appearing with `UNKNOWN` before `NEW_BATTLE`.
- **Validation:** All 47 focused Home Perk and no-battle setup tests passed.
  The complete repository suite passed 878 tests.
- **Rollout:** Automation PID 761905 was replaced by PID 783606 under the
  acknowledged pause. The unwaived retry passed Perks at 20:18:11, completed
  every Home requirement at 20:20:18, started the battle, and verified every
  session-preflight requirement at 20:23:27.
- **Fixed by:** `8ea1961`.

### Event Mission warnings treated stale rows as current stalled missions

- **Observed:** Operator report on 2026-07-29 after automation emitted eleven
  `[EVENT_MISSION_WARNING]` entries at startup.
- **Symptom:** The warnings presented old mission names and progress as if they
  still described incomplete missions. For example, the tracker warned
  `Login for 7 days — 6/7` while its same persisted state already contained the
  later `Login for 10 days — 8/10` tier.
- **Evidence:** The 19:57 warning batch used rows last seen between July 20 and
  July 25. The last accepted inventory was at July 28 00:01, after two Event
  rewards had been claimed, and contained only the two remaining readable
  rows. Static tracing confirmed that `_claim_event_rewards` claims available
  rows before `_record_event_inventory` captures the final list.
  `EventMissionTracker.record_inventory` deliberately preserved every prior
  row absent from a later complete OCR inventory, while `due_warnings`
  calculated stall duration from wall-clock time since the last changed value
  without requiring a later unchanged observation, a recent `last_seen_at`, or
  presence in the latest inventory. A claimed or OCR-missed row could
  therefore warn indefinitely until the Event boundary.
- **Safety response:** Diagnosis used control, lock/PID, action-log, persisted
  tracker, source, and read-only screenshot inspection. It did not Pause,
  restart, navigate, exit, Surrender, or otherwise alter the active runtime or
  device flow.
- **Cause:** The tracker treated elapsed time without another observation as
  evidence that progress remained unchanged. Its conservative retention of
  OCR-missed rows had no separate warning-authority state, and a fuzzy-matched
  mission whose progress target advanced could inherit the preceding tier's
  age.
- **Resolution:** A complete inventory now revokes warning authority from
  every retained row before granting it only to rows whose progress was
  actually read in that inventory. Warnings require the row to be present in
  the latest inventory, that inventory to be no more than one hour old, and
  repeated unchanged observations spanning the stall threshold. Incomplete
  and stalled durations are measured between observations rather than extended
  by unobserved wall-clock time. A changed progress target starts a new tier,
  and the clearer warning text describes the observed interval. Tracker schema
  version 2 invalidates existing version-1 stale cache data on process load.
- **Regression:** `test/test_event_mission_tracker.py` covers a mission seen
  only once, stale latest inventory, a row absent from a later complete OCR
  inventory, the stale `Login for 7 days` tier beside `Login for 10 days`,
  target advancement, progress recovery, warning cooldown, and version-1
  migration.
- **Validation:** All 50 Event Mission tracker and Mission reward handler tests
  passed. The complete repository suite passed 876 tests.
- **Rollout:** The repair was committed without restarting the active
  automation during its Home setup. It will load at the next safe process
  replacement.
- **Fixed by:** `61caa78`.

### Session preflight requested Home repair after one transient mismatch

- **Observed:** 2026-07-29 while reviewing the operator-reported Farm
  configuration-repair Surrender.
- **Symptom:** One authoritative-looking session-preflight mismatch could
  immediately request the profile-owned Surrender/Home-repair sequence. A
  transient read therefore had no opportunity to recover before the active
  battle was ended.
- **Evidence:** Static inspection found that the mismatch branch in
  `core/action_executor.py` copied `repairable` directly into
  `gc_session_preflight_repair_required` and immediately cleared the retained
  no-battle setup completion. The main loop then claimed the repair and called
  the guarded Surrender path. The earlier 2026-07-26 incident preserved in
  this archive demonstrates that transition at wave 130.
- **Safety response:** The currently observed battle was not used to reproduce
  the destructive boundary. No diagnostic Exit or Surrender was sent.
- **Cause:** Complete Home setup had a three-attempt policy, but in-battle
  session preflight had no corresponding retry state or profile-declared
  threshold.
- **Resolution:** Every Farm profile now declares three matching repairable
  mismatch attempts. Attempts one and two retain the Home proof, keep repair
  authority false, and re-arm the read-only validation action after its
  existing 30-second cooldown. A successful result clears the count, and a
  different failed-check set restarts it at one. Only attempt three can request
  the existing guarded Home repair. The already-implemented repair terminal
  policy remains unchanged: an automation-owned repair Surrender skips
  Perks/More Stats and battle-record persistence, then returns Home.
- **Regression:** `test/test_run_initialization.py` covers retry state,
  transient recovery, changed-failure reset, three-attempt exhaustion, the
  single owned Surrender claim, and repair Game Over capture suppression.
- **Validation:** 45 focused Farm/preflight tests passed. A broad repository
  run passed 861 tests with four unrelated in-progress activity-continuity
  fixtures deselected.
- **Fixed by:** `fbdcd48`.

### Implausible Perk schedule OCR poisoned the armed wave and stalled the timeline

- **Observed:** 2026-07-29 during an active Tier 19 Farm battle.
- **Symptom:** The timeline recorded the Perk scheduled for wave `705` while
  the independent battle-wave observation was still `690`, then stopped
  reacting to normal later boundaries. It eventually resumed only when another
  OCR outlier exceeded the poisoned value and recorded a selection against
  impossible scheduled wave `7705` at observed wave `1530`.
- **Evidence:** `logs/actions.log` records the wave-705 action at 05:44:39 with
  `observed_wave=690`, no Perk actions while ordinary status advanced through
  waves 720–1490, and the fake wave-7705 action at 05:53:04. The natural Game
  Over record `Battle20260729T055447-0700` preserves both invalid batches.
  Static inspection found that `measure_perk_progress()` accepted the first
  and last one-to-six-digit OCR groups without checking their relationship,
  while `PerkTimelineTracker` accepted any larger next-wave token and armed
  directly from it.
- **Safety response:** Diagnosis used read-only process, control, ADB, log, and
  screenshot inspection. No diagnostic Pause, restart, panel input, Exit, or
  Surrender was sent. The battle ended naturally, completed its ordinary
  terminal capture, and started a distinct new run.
- **Cause:** A separator artifact could be concatenated with the real scheduled
  wave, turning a value such as `705` into `7705`. Two confirmation frames did
  not protect against a stable OCR artifact. The tracker had no maximum
  schedule lead, did not require the displayed current wave to reach the armed
  boundary, and had no way to discard an already-poisoned armed value.
- **Resolution:** Scheduled pairs are now usable only when the next wave is
  later and no more than 250 waves ahead. A transition also requires the
  displayed current wave to have reached the armed boundary. Invalid pairs
  remain read-only retries, emit a persistent warning after three consecutive
  observations, and log recovery on the next valid pair. An implausibly distant
  armed value is discarded and re-armed from stable valid evidence.
  Pause-spanning post-PWR full snapshots now also use the selected list's
  authoritative newest-first order to reconstruct chronological singleton
  batches when one distinct change matches each boundary; ambiguous repeated
  families remain interval aggregates.
- **Regression:** `test/test_perk_timeline.py` covers the `690 / 7705` OCR
  rejection, boundary-crossing requirement, poisoned-state resynchronization,
  no-input persistent retry, exact newest-first post-PWR reconstruction, and
  repeated-family fallback. `test/test_battle_stats.py` covers both
  reconstructed and aggregate Markdown semantics.
- **Validation:** All 39 focused Perk timeline and battle-report tests passed.
  The complete repository suite passed 852 tests.
- **Live rollout:** A guarded attached reload replaced PID `16016` with
  `73436` at wave 1902 without running startup/session gates. The replacement
  established a five-page mid-battle baseline, rejected animated OCR frames
  that read the real scheduled wave `1972` as `72`, and then recorded
  `Chrono Field Duration +5s` against scheduled wave `1972` at observed wave
  1975. It restored `RUNNING`, armed a plausible next schedule, and emitted no
  Exit, Surrender, repair, or terminal event.
- **Fixed by:** `1eb3cd0`.

### Perk timeline restart catch-up lost panel ownership and stalled the battle

- **Observed:** 2026-07-28 through 2026-07-29 after the emulator and managed
  runtime moved from the previous ADB endpoint to `localhost:5565`.
- **Symptom:** The replacement runtime attached near wave 2425, repeatedly
  recorded automatic Perk selections against the preceding scheduled wave,
  and eventually remained on the Perks screen from 22:19 until manual recovery
  after 01:31. The game ended at wave 3372 behind that screen, but the runtime
  continued to report `PERKS` and could not enter its Game Over pipeline.
- **Evidence:** The managed environment, persistent control, runtime lock, and
  device all agreed on `localhost:5565`; the old 5555 lock was released, so
  the ADB transfer was not the failure. `logs/actions.log` shows a mid-battle
  baseline crossing scheduled wave 2430 and re-arming from stale progress. At
  22:19:57 it dispatched the Perks close for scheduled wave 3280 and reported
  `x4.06 Health Regen` recorded, while the next and every later state remained
  `PERKS`. A fresh retained-screen inspection showed wave 3372/3450 behind the
  panel. Closing the verified panel under Pause revealed the authoritative
  wave-3372 Game Over screen.
- **Safety response:** The active target, control file, PID/lock, current
  screen, and recent action log were inspected before input. Automation was
  indefinitely paused and its acknowledgement was verified. One
  template-authorized close restored the terminal screen; the battle was
  neither exited nor Surrendered. The old process was then replaced while
  paused at Game Over.
- **Cause:** Baseline capture armed the tracker from the schedule token seen
  before a multi-frame full-list scan, even when the scheduled wave passed
  during that scan. Separately, dispatching a close tap was treated as route
  restoration without observing a destination state. The tracker cleared its
  ownership flag, leaving the still-open Perks panel with no handler authorized
  to close it.
- **Resolution:** Baseline and full-list captures refresh the top-bar schedule
  from fresh panel frames and repeat once when a boundary crosses during the
  capture, producing a temporally consistent snapshot and current re-arm
  point. Perks close now succeeds only after a fresh frame proves `RUNNING`,
  `GAME_OVER`, or `TOURNAMENT_RESULTS`; otherwise the observer retains route
  ownership and retries the guarded restoration on its next pass.
- **Regression:** `test/test_perk_timeline.py::
  test_mid_battle_baseline_repeats_after_crossing_scheduled_wave` proves the
  baseline catch-up and current re-arm behavior.
  `test_observer_retains_route_ownership_until_close_transition_is_verified`
  proves that an unconfirmed close cannot orphan the modal.
- **Validation:** All 13 Perk timeline tests and 236 focused tracker, battle,
  control, initialization, no-strategy, and Tournament tests passed. The
  complete repository suite passed 845 sandbox-compatible tests plus its
  separately permitted loopback HTTP test, for 846 total. The replacement
  runtime acquired the `localhost:5565` lock as PID 3210165, processed the
  preserved Game Over screen, saved 144 exact Stats rows and 27 selected
  Perks, and started a new Tier 19 run. Its first two timeline captures
  recorded the selections scheduled for waves 191 and 429; both verified
  `close_state=RUNNING`, and normal automation continued through wave 470.
- **Fixed by:** `ce862cb`.

### Automation restart cleared the native Current run activity view

- **Observed:** 2026-07-28 in the native Windows control surface after stopping
  and restarting automation during an existing game run.
- **Symptom:** Recent Activity was scoped to entries written after the restart,
  hiding the earlier activity from the same battle as though a new game run
  had begun.
- **Evidence:** Static inspection found that `App.__init__()` unconditionally
  called `start_activity_scope(reason="automation_started")`. That replaced
  `logs/activity_scope.json` on every Python process start even though the
  game-run boundary had not changed.
- **Safety response:** Diagnosis and validation were repository-local. No
  automation process, control directive, ADB target, or game state was changed.
- **Cause:** Scope ownership was incorrectly shared by the Python process
  lifecycle and the game lifecycle. The native client correctly followed the
  ledger it received; the runtime had moved that ledger's boundary.
- **Resolution:** Automation startup now ensures that a valid activity scope
  exists and reuses one already present. Verified Home `NEW_BATTLE` preflight
  remains the deliberate replacement boundary, so its Home setup and launched
  battle stay together.
- **Regression:** `test/test_logger.py::
  test_ensure_activity_scope_preserves_existing_boundary` proves that restart
  attachment retains the exact run ID, timestamp, source-file identity, and
  byte offset without adding another scope marker.
- **Validation:** All 87 logger and run-initialization tests passed, followed
  by the two focused current-run control-surface tests. No native rebuild was
  required because the client behavior and wire contract did not change.
- **Fixed by:** `d8a1cda`.

### A manually started battle could inherit the preceding Current run scope

- **Observed:** 2026-07-29 during design review of mid-battle process
  attachment.
- **Symptom:** Reusing the activity ledger across process restarts correctly
  preserved a same-battle attachment, but it could not distinguish that case
  from a later battle started manually while automation was stopped. The
  native Current run view could therefore retain the preceding battle's
  boundary.
- **Evidence:** Repository inspection showed that the ledger contained only a
  process-independent log boundary. The game already exposed an authoritative
  latest-completed-battle report through Battle History's Copy control, but no
  runtime path persisted or compared it.
- **Safety response:** Implementation and validation used retained screenshots,
  a retained copied report, and fake guarded input dispatch. No live process,
  control directive, ADB target, or battle was changed.
- **Cause:** Process continuity had a durable scope identifier but no durable
  game-history identity capable of proving whether a battle completed during
  the automation gap.
- **Resolution:** Home `NEW_BATTLE` scope creation now records a fingerprint of
  the latest copied Battle History report before launch. Attachment compares
  that baseline: equality preserves the scope, a changed report starts the new
  scope at the continuity action, and an unreadable result after safe
  restoration starts conservatively. The exclusive route is Pause-aware,
  blocks competing inputs, restores its source, and resumes safely if process
  replacement lands on Battle History itself.
- **Regression:** `test/test_battle_history.py` covers clipboard parsing,
  retained navigation/detail evidence, Home/running restoration, Pause, and
  interrupted-route recovery. `test/test_activity_continuity.py` covers
  unchanged, changed, unavailable, Home-baseline, and persisted-scope outcomes.
  `test/test_logger.py` covers compare-and-set identity writes and scope
  boundaries that include the continuity `ACTION`.
- **Validation:** The complete repository suite passed 869 tests. The client
  and activity API contract were unchanged, so no native publish was required.
- **Fixed by:** `2c4342d`.

### Extra Home right-rail control shifted Battle History outside its match region

- **Observed:** 2026-07-29 while starting a Tier 19 Farm run from verified
  `NEW_BATTLE` Home.
- **Symptom:** The new continuity workflow twice rejected the visible Battle
  History control at confidence `0.26`, reported that its baseline could not be
  recorded, and continued into Home setup.
- **Evidence:** `logs/actions.log` records both
  `navigation.battle_history_home` match failures from 18:11:37 through
  18:11:40. The read-only source frame is retained as
  `test/fixtures/home_screen_eight_nav_controls_20260729.png`: Battle History
  appears at `y=867`, below the older fixture's `y=770` position.
- **Safety response:** The failed read restored the unchanged Home source and
  sent no unguarded input. The operator stopped automation before setup acted;
  diagnosis used the stopped screen and read-only captures.
- **Cause:** A new eighth Home right-rail control shifted Battle History down
  97 pixels. Its fixed `y=740..890` search band no longer contained the
  complete 84-pixel template, so the best in-band candidate was only `0.26`.
- **Resolution:** The unchanged template and `0.90` threshold now search the
  complete bounded Home right rail. That region matches the shifted live icon
  at confidence `0.99` and still matches the older seven-control fixture.
- **Regression:** `test/test_battle_history.py::
  test_home_history_navigation_allows_an_extra_right_rail_control` verifies
  the retained eight-control Home frame and its dynamic tap center.
- **Validation:** All 24 focused Battle History, matcher, clickmap-access, and
  clickmap-integrity checks passed, followed by all 870 repository tests.
- **Fixed by:** `dba88f1`.

### Battle History row verifier rejected joined OCR labels

- **Observed:** 2026-07-29 after the Home Battle History navigation-region
  repair, with recurrences through 2026-07-30.
- **Symptom:** The continuity route successfully matched and opened Battle
  History, then reported `latest Battle History row was not verified`, returned
  Home, and continued without recording a baseline.
- **Evidence:** `logs/actions.log` records the post-navigation failures at
  18:28, 19:57, and 20:16 on 2026-07-29 and 17:43 on 2026-07-30. The promoted
  no-battle History fixture produces `Tier18 Wave130`; the verifier required
  literal spaces after both labels. The current live list similarly produced
  `Tier19 Wave 20`.
- **Safety response:** Every failed route used its verified Return to Game
  control and restored `HOME_SCREEN/NEW_BATTLE`. The operator stopped
  automation before diagnosis. The authorized post-repair live check held the
  target lock, retained `STOPPED`, copied Tier 19 wave 20, and restored verified
  Home without starting a battle.
- **Cause:** OCR can join a label to its numeric value. The verifier normalized
  punctuation but still searched for literal `TIER ` and `WAVE ` substrings,
  so valid `Tier18` and `Wave130` evidence failed.
- **Resolution:** Shared Tier/Wave evidence now permits omitted whitespace
  while still requiring a numeric value. The same rule verifies the visible
  list row and, with exact expected values, unchanged copied-report detail.
- **Regression:** `test/test_battle_history.py::
  test_retained_no_battle_history_row_allows_joined_ocr_labels` exercises the
  promoted frame, and
  `test_history_detail_allows_joined_ocr_identity_labels` covers exact copied
  Tier/Wave identity.
- **Validation:** All 16 focused Battle History and activity-continuity tests
  passed, followed by all 904 repository tests. The authorized live route
  matched `Tier19 Wave 20`, copied and fingerprinted Tier 19 wave 20, and
  restored verified `HOME_SCREEN/NEW_BATTLE` while control remained `STOPPED`.
- **Fixed by:** `e58bad1`.

### Battle History continuity check selected the first visible row without proving list top

- **Observed:** 2026-07-30 during a live Tier 19 Farm Retry sequence on
  `localhost:5555`, immediately after a natural Game Over and Retry.
- **Symptom:** The continuity check opened Battle History and selected the
  first visible row without scrolling the retained list to its newest/top
  boundary. It therefore treated an older completed battle as the latest one
  and restarted the Current run activity scope from the wrong identity.
- **Evidence:** `logs/actions.log` records the current battle being saved as
  `Battle20260730T204649-0700.json` at 20:47:21, followed by Battle History
  navigation and a direct fixed-row tap at 20:47:50 with no intervening swipe.
  At 20:48:03 the copied entry was logged as `Jul 29, 2026 13:56`, Tier 19,
  wave 1405, instead of the battle that had just completed on July 30.
- **Safety response:** Diagnosis used the action log and the already retained
  list capture. The newly running Retry battle was not paused, navigated,
  exited, or otherwise changed. Repair validation used fake guarded input and
  retained fixtures only.
- **Cause:** `read_latest_completed_battle()` verified and tapped a fixed
  first-row region as soon as the Battle History list appeared. The route had
  no scroll-to-top gesture or authoritative top-edge proof, so a retained
  scroll position silently redefined "latest" as "first visible."
- **Resolution:** The list route now performs a bounded, screen-guarded
  downward swipe loop and requires stable-edge evidence before validating or
  tapping the first row. It rechecks persistent input authority before every
  swipe and fails closed, restoring the source without selecting any row, if
  the top cannot be proven.
- **Regression:** `test/test_battle_history.py` verifies the top gesture occurs
  before the row tap for running, Home, and interrupted-detail routes; checks
  Pause between swipes; and verifies failed edge detection restores the source
  without a row tap. The focused Battle History, scrolling, continuity, and
  clickmap set passes 39 tests.
- **Validation:** The complete repository suite passes 931 tests. Live
  post-repair interaction was intentionally omitted because a new operator
  battle was already running.
- **Post-fix recovery:** The first process restart after the repair correctly
  reached the History top and copied the July 30 20:46 Tier 19 wave-2210
  completion, but the previously persisted July 29 wave-1405 baseline made the
  same running battle appear new at 21:50. The atomic activity ledger was
  repaired without device input by restoring its original 20:47 log boundary
  while retaining the correct wave-2210 identity and current opaque scope ID.
  The Current-run API then returned the complete activity from 20:47 onward,
  and the live battle continued normally.
- **Fixed by:** `29308ac`.

### Automatic Retry did not establish or baseline the next Current run

- **Observed:** 2026-07-31 after automation restarted during the Tier 19 Farm
  battle that followed the `Jul 31, 2026 07:22` completion.
- **Symptom:** Battle History correctly identified that wave-4903 completion as
  the newest entry, but continuity treated the 16:03 process attachment as a
  new battle boundary. The native Current-run activity therefore omitted the
  first approximately eight hours of the battle automation itself had started.
- **Evidence:** `logs/actions.log` records the natural Game Over and saved
  `Battle20260731T072302-0700.json`, a verified Retry tap at 07:23:43, completed
  terminal handling at 07:23:46, and the new battle's initialization beginning
  at 07:23:52. No `[RUN_SCOPE]` entry accompanied Retry. At 16:03 the repaired
  top-edge reader copied Tier 19 wave 4903 with fingerprint `d200b4d9…`, then
  created `battle_history_changed_on_attachment` scope
  `10f325810b944300b38cf3f81df96398` at that attachment instead.
- **Safety response:** Diagnosis and implementation did not pause, navigate,
  exit, Surrender, restart, or otherwise alter the active battle. After fresh
  control, lock, API, ADB, log, and screenshot inspection confirmed the same
  run at Tier 19 wave 3450, only the ignored activity ledger was repaired. Its
  opaque scope ID and authoritative wave-4903 identity were preserved while
  the boundary moved to the successful 07:23 Retry; a `/api/v1/activity`
  current-run read confirmed the corrected 07:23:43 start.
- **Cause:** The Game Over Retry route finalized strategy lifecycle state and
  tapped Retry, but Current-run scope creation was owned only by verified Home
  `NEW_BATTLE` and later process-attachment continuity. The runtime also had no
  persisted state distinguishing a not-yet-published post-Retry History row
  from an authoritative new baseline.
- **Resolution:** A successful verified Retry now creates the next scope
  immediately and retains the previous completed-battle identity as pending
  comparison evidence. After run initialization and session preflight finish,
  continuity polls Battle History. A still-unchanged newest row schedules
  another 15-second poll without blocking normal battle actions or replacing
  the Retry scope; the first advanced row completes that same scope's baseline.
  The pending comparison survives process replacement.
- **Regression:** `test/test_game_over_handler.py` proves the scope callback
  follows a successful Retry tap. `test/test_logger.py` covers Retry-scope
  metadata and completion. `test/test_activity_continuity.py` covers startup
  deferral, stale-row rejection, bounded repolling, same-scope preservation,
  and advanced-row recording. `test/test_run_initialization.py` verifies app
  wiring.
- **Validation:** The 129 focused logger, Game Over, activity-continuity, and
  run-initialization tests passed, followed by all 950 repository tests.
- **Rollout:** The guarded attached-battle reload replaced PID 2908012 with
  PID 2958817 under an acknowledged Pause, loaded `next_run` attachment
  semantics, reacquired the same `localhost:5555` lock, and restored `RUNNING`.
  Its live continuity pass reached the proven History top, recopied the same
  July 31 Tier 19 wave-4903 identity, returned to the active battle, and logged
  `scope_preserved` for the repaired 07:23 scope.
- **Fixed by:** `2ce357d`.

### Tournament side rail placed Battle History outside its running match region

- **Observed:** 2026-07-31 after attaching automation to an active Tournament
  battle on `localhost:5555`.
- **Symptom:** Attached-battle continuity opened the Tournament utility rail
  but twice failed to find the visible Battle History control at confidence
  `0.19`. It therefore recorded a conservative new activity scope with reason
  `battle_history_unavailable_on_attachment` instead of reading the latest
  completed battle.
- **Evidence:** The live Tournament frame and retained fixture
  `test/fixtures/running_menu_tournament_trophy_20260718.png` place Battle
  History at `(909,696)` in the rail's left column. The unchanged template
  matches that control at confidence `0.999998`, but the former bounded search
  region began at `x=950` and excluded it.
- **Safety response:** Diagnosis first used logs, read-only captures, and the
  retained fixture. Rollout used the guarded attached-battle reload, preserved
  `RUNNING`, the Tournament strategy, and the existing activity scope, and did
  not Surrender or restart the battle. The live continuity route used only its
  normal verified History navigation and returned to the active battle.
- **Cause:** The running-battle clickmap assumed the ordinary battle rail's
  right-column History placement. During a Tournament, the trophy control
  changes the two-column rail layout and moves History left of that narrow
  search band.
- **Resolution:** The History row's bounded search region now spans both rail
  columns while retaining the same vertical band, template, and confidence
  threshold. This covers ordinary and Tournament layouts without widening
  authority to unrelated rows.
- **Regression:** `test/test_battle_history.py::
  test_tournament_history_navigation_allows_the_left_rail_column` verifies the
  retained Tournament rail and exact dynamic match center. Existing ordinary
  running and Home navigation coverage remains unchanged.
- **Validation:** All 28 focused Battle History, clickmap-access, and clickmap-
  integrity tests passed, followed by all 959 repository tests. After the
  guarded reload replaced PID 3112026 with PID 3121145, the live route matched
  `(909,696)`, proved the History top, copied the latest completed Tier 19
  wave-20 battle, recorded it in the existing scope
  `aeaae687886148c496a99c3cd4bbe8db`, and restored the active Tournament.
- **Fixed by:** `df25656`.

### Daily Gem claim drift escaped its match region and left battle in Store

- **Observed:** 2026-07-28 during the active Tier 19 Farm battle's rollover
  Daily Gem check.
- **Symptom:** The handler scrolled until the free reward was visibly
  claimable, but its subsequent verified tap rejected the same button at
  `0.40` against the `0.90` threshold. The handler reported failure without
  leaving Store, so the battle remained behind the Store view until the
  independent 15-minute Return-to-Game timer fired.
- **Evidence:** The retained pre-tap frame matched
  `buttons.claim_daily_gems` at `0.996` with the template top at y=1139.
  Store inertia moved the button to y=1112 before the fresh tap capture, while
  the tap match region began at y=1131 and used no padding. The clipped
  candidate scored `0.400`. `logs/actions.log` records the failure at
  17:01:24, automatic Return to Game after 903 seconds at 17:16:35, the
  immediate retry, and a successful claim plus verified battle restoration at
  17:17:11. The failed frame is promoted as
  `test/fixtures/store_daily_gem_claim_drifted_20260728.png`.
- **Safety response:** Inspection of the active process, persistent control,
  ADB target, logs, and Store screen was read-only. No diagnostic input was
  sent. The existing supervisor used the visible, template-verified
  Return-to-Game control; the retry then used the normal Daily Gem workflow.
  The battle was never exited or Surrendered.
- **Cause:** `scroll_until_visible()` returned authoritative claim evidence,
  but the handler discarded it when tapping and captured the still-moving
  Store again. The narrow vertical region excluded the button after a 27-pixel
  kinetic shift. Separately, Store-open failure branches emitted their terminal
  failure immediately instead of attempting the already-supported verified
  route back to their source state.
- **Resolution:** The claim region now covers the observed kinetic range, and
  the tap consumes the screenshot that authorized the claim while retaining a
  fresh retry. Every failure after Store navigation now retains its evidence,
  attempts the verified source-specific return, and only then emits the
  terminal result; a failed cleanup is explicit in that result.
- **Regression:** `test/test_daily_gem_handler.py::
  test_drifted_live_claim_stays_inside_authoritative_match_region` exercises
  the promoted live failure.
  `test_failed_claim_restores_running_source_before_terminal_result` proves
  that successful cleanup precedes the result, and
  `test_failed_claim_reports_failed_source_cleanup` preserves an explicit
  fail-closed outcome.
- **Validation:** All 16 Daily Gem handler tests passed. The broader Daily Gem
  scheduler, Home/no-strategy/run-initialization/Tournament integrations passed
  164 tests, and the clickmap, matcher, and tap-safety suites passed 48 tests.
  The active runtime recovered and claimed the reward before this repair was
  loaded; it was not restarted onto the commit during the battle.
- **Fixed by:** `5cb852a`.

### Paused perk tracking attributed an interval of selections to stale waves

- **Observed:** 2026-07-28 during the user-authorized Cards scrolling
  validation in an ongoing Tier 19 Farm battle.
- **Symptom:** Automation acknowledged `PAUSED` at wave 670 and correctly sent
  no handler input while the battle continued to wave 880. The Perk timeline
  observer retained its first pending transition without advancing that
  request as later stable top-bar tokens arrived. On Resume, it assigned an
  11-change full-list diff to scheduled wave 665, then assigned the latest row
  to stale scheduled wave 760 while the battle was already near wave 950.
- **Evidence:** `logs/actions.log` records `RUNNING/PAUSED` at waves 670, 780,
  and 880; the guarded Cards route between 16:51:41 and 16:52:17; the Resume
  acknowledgement at 16:52:22; the 11-selection wave-665 batch at 16:52:43;
  and the next wave-760 singleton at observed wave 950. This is direct live
  evidence that a pause can span more than one automatic Perk selection.
- **Safety response:** The diagnostic route checked the persistent Pause
  request before every input, returned through the verified in-battle control,
  and rechecked ownership before Resume. It never exited or Surrendered the
  battle. Normal runtime input remained blocked throughout the acknowledged
  pause.
- **Cause:** `PerkTimelineTracker.observe()` returned the existing pending
  request after token confirmation without updating its `progress_after`.
  Later selection boundaries were therefore neither represented in the batch
  nor used to re-arm the tracker after its deferred panel capture. A
  post-PWR request could also retain `latest` mode even though multiple
  selections had accumulated.
- **Resolution:** Pending requests now advance on every newer stable progress
  token, retain each observed scheduled boundary, and re-arm from the newest
  token after capture. Crossing more than one boundary forces a full selected
  list and records the diff as an explicit `interval_aggregate`, because
  individual changes cannot be assigned honestly to one scheduled wave. The
  battle Markdown renderer exposes the aggregate boundaries and observed-wave
  span instead of presenting the changes as simultaneous.
- **Regression:** `test/test_perk_timeline.py::
  test_paused_observer_coalesces_boundaries_and_arms_latest_progress` covers
  multiple transitions while actions are blocked and proves that the next
  request uses the newest armed wave.
  `test_deferred_post_pwr_singleton_falls_back_to_full_interval_snapshot`
  covers the post-PWR `latest` fallback.
  `test/test_battle_stats.py::
  test_render_perk_selection_timeline_marks_pause_interval_aggregates` covers
  durable report semantics.
- **Validation:** The focused tracker/report suites passed 31 tests. The
  repository suite passed 838 sandbox-compatible tests; its sole loopback HTTP
  test passed separately on the approved host path, for 839 total. The active
  runtime was not restarted onto this change during the battle.
- **Fixed by:** `20b042d`.

### Home card-recharge scan repeatedly missed Demon Mode while finding Nuke

- **Observed:** 2026-07-28 during the authorized Tier 19 perk-timeline live
  validation.
- **Symptom:** Three complete Home setup attempts found Nuke, opened its detail,
  and verified `ready_after_recharge`, but each bounded inventory traversal
  ended with `Demon Mode Card was not found in inventory`.
- **Evidence:** `logs/actions.log` records the three failures at 15:08:27,
  15:09:23, and 15:10:18. Each attempt returned to Home before retrying. The
  third pass includes six guarded `goto_next:cards_inventory` swipes after its
  top-edge traversal, while the Nuke detail evidence remained authoritative.
  Exact replay of that traversal began Nuke-first (`0.9999`) with only a
  clipped Demon match (`0.701`), reached the true top, jumped past the Demon
  row on its first forward swipe (`0.574`), and reached the bottom on its
  second. A separate intermediate viewport matched the unchanged Demon
  template at `0.978`, excluding a template/localization failure.
- **Safety response:** No battle started on failed evidence. For the disposable
  test only, the built-in gate decision recorded a one-run
  `card_recharge_modes` waiver at 15:10:58; all unrelated Home and in-battle
  requirements still passed before the explicitly owned battle began. The
  later scrolling validation used an agent-owned Pause, returned through the
  verified in-battle control, resumed only while that Pause was still owned,
  and never exited or Surrendered the battle.
- **Cause:** The forward Cards gesture moved 550 pixels in 300 ms. From the
  true top it could pass completely over the Demon row before the next
  screenshot, while the fixed swipe counts kept repeating at the bottom.
  Failure output retained neither the traversed frames nor per-card match
  confidence, leaving the miss ambiguous.
- **Resolution:** The forward gesture now moves 300 pixels over 600 ms so
  adjacent screenshots overlap. Traversal first reaches the actual top and
  then searches to the actual bottom using settled-frame edge detection
  instead of fixed blind phases. Every viewport logs Demon/Nuke confidence,
  and a failed scan retains all inspected frames under one timestamped evidence
  directory.
- **Regression:** `test/test_card_recharge_modes.py::
  test_nuke_first_scan_reaches_top_edge_then_overlaps_to_demon` reproduces the
  failing Nuke-first entry position.
  `test_missing_card_retains_each_inspected_viewport` covers diagnostic
  retention, and `test/test_card_swipe_geometry.py` locks the overlapping
  gesture.
- **Validation:** The focused Cards suites passed 19 tests. Offline replay
  distinguished the missed viewport from the healthy template. During the
  active-battle guarded validation, Cards matched Demon Mode at `0.996` and
  Nuke at `0.987`, reached the true top in three swipes, and returned to
  `RUNNING`. The repository suite passed 838 sandbox-compatible tests plus the
  separately permitted loopback HTTP test, for 839 total.
- **Fixed by:** `8c955d4`.

### One obscured tower wing produced false Second Wind activations

- **Observed:** 2026-07-28 after the operator reported one false Second Wind
  activation during the active Tier 19 Farm run.
- **Symptom:** Automation recorded Second Wind at approximately wave 2219 even
  though the tower wings remained visibly available and no activation had
  occurred.
- **Evidence:** The automatically retained first-transition frame showed both
  wings still rendered. The left template scored `0.908` and matched; a battle
  effect reduced the right template to `0.515`, below its `0.600` threshold.
  The durable regression crop is
  `test/fixtures/second_wind_one_wing_occluded_20260728.png`.
- **Safety response:** Diagnosis used the control file, current lock, action
  log, and saved runtime evidence read-only. The active battle, automation
  process, control directive, and emulator were not changed or restarted.
- **Cause:** The tracker used `all(matches)` as its complete definition of
  wings present. It correctly required both wings to arm, but after arming it
  also treated either single-wing template miss as an absent frame. Four
  observations with one side obscured could therefore confirm a false
  activation.
- **Resolution:** Commit `58beb38` first kept both wings as the arming
  requirement while making either visible wing cancel a pending disappearance.
  Commit `143e803` then removed wing disappearance as an activation signal
  entirely. The wings now establish availability and re-arm state; only the
  fixed Second Wind active-status glyph above Nuke records an activation.
- **Regression:** `test/test_battle_activation_tracker.py` repeats the retained
  one-wing-obscured frame and verifies that it produces neither an activation
  nor an evidence capture. Promoted active-icon fixtures cover early, late, and
  battle-obscured countdown appearances plus a known absent-icon false frame.
  The tracker tests also prove that missing wings without the active glyph do
  not emit an event, a persistent glyph emits once, and returning wings permit
  a later activation.
- **Validation:** The focused visual, tracker, report, no-strategy, and
  clickmap suites passed 52 tests. The repository suite passed 797
  sandbox-compatible tests plus the separately permitted loopback HTTP test,
  for 798 total. Recursive clickmap/template integrity, `git diff --check`, and
  the self-contained `win-x64` WPF publish passed.
- **Fixed by:** `58beb38`, superseded and hardened by `143e803`.

### Battle History tree children inherited an unreadable foreground

- **Observed:** 2026-07-26 in an operator screenshot of the newly published
  collapsible Battle stats tree.
- **Symptom:** Expanded section names rendered correctly in cyan, but their
  child stat labels were nearly black against the dark panel. The first
  table-style revision then sized each row independently, producing staggered
  columns instead of one aligned table.
- **Evidence:** The initial tree template explicitly colored section names and
  values but left the `TreeView`, `TreeViewItem`, and child-name foregrounds to
  WPF inheritance. The screenshot showed the resulting dark default foreground
  on the `#111827` report background. A follow-up screenshot showed that star
  columns inside content-sized row templates were ratios of each individual
  row's width.
- **Safety response:** The correction was code-only and did not interact with
  the automation process, control state, battle, or emulator.
- **Cause:** WPF's tree item/template boundary did not preserve the Window's
  light foreground for the child-name `TextBlock`, and unconstrained star
  columns did not establish a common width across separate child templates.
- **Resolution:** The tree, item containers, and child names now declare the
  light foreground explicitly. Expanded sections also show Stat/Value headings
  and table-style rows with a contrasting background, cell divider, row
  separator, padding, and a higher-contrast value color. Header and child grids
  now share fixed 240/480-unit columns, so every row uses the same table edges.
- **Regression:** The contrast and table structure are defined in
  `windows/TheTower.ControlSurface/BattleHistoryWindow.xaml` and compiled by
  the checked-in Linux publish script.
- **Validation:** `windows/TheTower.ControlSurface/publish-linux.sh` completed
  a self-contained `win-x64` publish successfully. Visual confirmation remains
  for the Windows operator after replacing and restarting the client.
- **Fixed by:** `f690302`, followed by the shared-column correction in
  `a2ac376`.

### Completed Battles omitted survival ability activation waves

- **Observed:** 2026-07-26 when the operator inspected the newest Farm record
  in the native Windows Completed Battles window.
- **Symptom:** The Battle stats tab ended with Coins/min progression and showed
  no Demon Mode or Nuke activation waves, leading to concern that automation
  had not been restarted after the tracker was added.
- **Evidence:** The selected `Battle20260725T210917-0700` Markdown report
  contained its **Survival ability activations** section, and its JSON retained
  the complete `runtime.survival_ability_activations` object. The live
  automation owner had started after the tracking commits and the action log
  contained matching runtime events. Static client inspection found explicit
  handling for `coin_rate_samples` but no read of the adjacent survival
  activation object.
- **Safety response:** Diagnosis was read-only. The automation process,
  persistent control, ADB target, active battle, and emulator were not changed.
- **Cause:** `BattleHistoryWindow` constructed its Battle stats rows directly
  from selected JSON fields and never added survival activation events. The
  same flat grid also repeated the section name for every row, making long
  sections difficult to navigate.
- **Resolution:** The client now creates explicit Demon Mode and sequenced Nuke
  rows with approximate wave, detection time, and wave confidence. Battle
  stats are grouped into collapsed tree parents with row counts and expandable
  stat/value children.
- **Regression:** The parser contract is implemented in
  `windows/TheTower.ControlSurface/BattleHistoryWindow.xaml.cs`, and its
  corresponding hierarchical templates are compiled from
  `BattleHistoryWindow.xaml` by the checked-in Linux publish script.
- **Validation:** `windows/TheTower.ControlSurface/publish-linux.sh` completed
  a self-contained `win-x64` publish successfully. WPF visual confirmation
  remains for the Windows operator after copying the new executable.
- **Fixed by:** `f876647`.

### Coins/min ramp remained frozen at its first nonzero reading

- **Observed:** 2026-07-25 during an active Tier 19 Farm battle.
- **Symptom:** Coins/min correctly remained zero through approximately wave
  1800, then began rising rapidly. The runtime accepted `362T` as the first
  nonzero rate but repeatedly reported every later real reading as an
  implausible jump, leaving the published value frozen at `362T`.
- **Evidence:** `logs/actions.log` records zero at waves 1490, 1600, and 1720,
  followed by `362T` at wave 1803. It then records rejected readings of
  `4.05q`, `7.52q`, `10.2q`, and later values through `21.8q`. A fresh
  read-only frame at wave 2167 visibly showed `22.6q/min`; the diagnostic
  Coins probe parsed the same frame as `22.6q` with both the suffix and
  `/min` marker present.
- **Safety response:** Diagnosis used read-only process, control, log, ADB, and
  screenshot inspection. The active battle was not paused, restarted, exited,
  or Surrendered, and no device input was sent.
- **Cause:** The plausibility gate compared every low-confidence large change
  only with the last accepted value. Once the first post-zero rate became the
  baseline, a legitimate fast ramp could exceed the factor limit on every
  sample, so the baseline never advanced and the gate could never recover.
  A single missed `/min` marker could also trigger an immediate display toggle;
  the post-toggle lifetime total could then be published transiently as a rate.
- **Resolution:** The gate now holds the first implausible rate change and
  accepts the next reading when two consecutive candidates confirm a sustained
  change within the normal scale-independent plausibility window. A candidate
  that returns near the trusted baseline clears the pending change. Display
  recovery now requires two missing `/min` observations, and a post-toggle
  reading without `/min` retains the last trusted rate.
- **Regression:** `test/test_coin_detector.py` covers the exact
  `0 → 362T → 4.05q → 7.52q → 10.2q` progression, an isolated jump that must
  remain rejected, two-sample display-toggle debounce, and rejection of a
  post-toggle lifetime total as Coins/min.
- **Validation:** The focused Coins suite passed 8 tests; status/run-boundary
  integration passed 75 tests; automation control and process coverage passed
  79 tests. `git diff --check` passed.
- **Fixed by:** `2c1bebd`.

### Boss presence disabled the automatically paused Distance Adjuster

- **Observed:** Operator report on 2026-07-25 while Orb Distance enforcement
  was attempting to change a live run.
- **Symptom:** Distance Adjuster opened while a Boss was on the field. Opening
  the panel paused combat, while the Boss's presence greyed out the distance
  arrows, so the requested value could not change and the Boss could not clear
  while the panel remained open.
- **Evidence:** The operator identified both game behaviors.
  `logs/actions.log` independently records a Tournament enforcement attempt at
  16:58:07 with swapped values, one requested step, and
  `reason=value_did_not_change`; a later attempt at 16:58:39 observed the
  correct pair.
- **Safety response:** Diagnosis and implementation used source, retained
  fixtures, generated plans, and historical logs only. No live device input,
  process change, battle exit, or Surrender was performed.
- **Cause:** The handler kept the automatically pausing panel open while
  waiting for a verified value transition. An unavailable arrow or unchanged
  value ended that panel session, but only the outer 30-second strategy
  cooldown could initiate another attempt; the handler did not deliberately
  resume combat and synchronize its retry to battle progress.
- **Resolution:** An unavailable arrow or unchanged value now closes Distance
  Adjuster, verifies the running side menu, waits with combat active until the
  wave advances, and retries through a bounded number of fresh panel sessions.
  The runtime action guard is propagated into strategy execution and rechecked
  throughout the between-session wait and before every new panel open. The
  experimental Tier 19 Farm profile now also runs the same range-selected
  enforcement: observed configured Ranges `30.00m` and `98.38m` select their
  respective pairs, while any other readable Range remains preserved without
  panel input.
- **Regression:** `test/test_orb_distance.py` covers close, wave advance,
  reopen, and successful fresh-evidence correction after an unchanged tap.
  `test/test_run_initialization.py` covers Tier 19 generation, run-gate
  ownership, and continued Target Priority preservation.
- **Validation:** Focused Orb Distance and initialization coverage passed 90
  tests; the broader integration set passed 152 tests. Repository-wide
  validation passed 757 sandbox-compatible tests plus the separately permitted
  localhost HTTP test, for 758 total.
- **Fixed by:** `b01ebf9`.

### Tournament observer repeated session preflight after a mismatch acknowledgement

- **Observed:** 2026-07-25 after the operator attached automation to a
  Tournament already in progress.
- **Symptom:** The one-shot observer pass found that the configured armor
  Modules were assigned to the opposite Primary/Assist roles. Although the
  mismatch was labeled non-blocking and **Continue observing** was selected,
  automation immediately restarted the complete Cards, Ultimate Weapons,
  Modules, Bots, Guardians, and Workshop traversal.
- **Evidence:** `logs/actions.log` records the first configuration inventory
  from 16:58:47 through the `modules` mismatch at 17:01:03, the consumed
  `continue_observing` decision at 17:01:29, and a second
  `validate_tournament_session_preflight` rule firing at the same timestamp.
  The persisted decision was `blocking: false`, but the generic waiver path
  cleared `gc_session_preflight_attempted`, which was the Tournament plan's
  one-pass completion assertion.
- **Safety response:** Diagnosis inspected the control file, stale lock owner,
  action log, ADB target, and one current screenshot read-only. The automation
  process was already stopped; the live Tournament was not paused, tapped,
  exited, restarted, or Surrendered.
- **Cause:** Observer mismatches reused the startup gate-decision and run-scoped
  waiver machinery. A `continue_observing` waiver correctly re-armed a normal
  required preflight so remaining checks could run, but that behavior was
  incompatible with the Tournament observer's conclusive one-shot
  `gc_session_preflight_attempted` contract.
- **Resolution:** `mismatch_policy: notify` now means record-only completion.
  Failed-check and detailed screen evidence remain available, while the
  mismatch sets no blocked or repair state and publishes no gate decision.
  The one-pass marker remains set, so the generated Tournament rule cannot
  repeat. Required Farm mismatches retain the existing blocking, retry,
  fallback, and repair semantics.
- **Regression:** `test/test_tournament_observer.py` injects a `modules`
  mismatch, verifies retained failure evidence and exact-match
  `completed=False`, proves the observer is not blocked or repairable, and
  confirms that the strategy emits no subsequent action.
- **Validation:** Focused Tournament, initialization, and control coverage
  passed 108 tests. Repository-wide validation passed 755 sandbox-compatible
  tests plus the separately permitted localhost HTTP test, for 756 total.
- **Fixed by:** `53f0719`.

### Damage Slider opener searched only the retained Attack viewport

- **Observed:** 2026-07-25 after the operator attached automation to a
  Tournament already at wave 1931.
- **Symptom:** The Tournament session preflight repeatedly reported
  `panel_not_verified` even though the operator had already set the Damage
  Slider to `100%` manually. The problem was not enforcement: automation could
  not find the Damage tile needed to open the panel and verify the value.
- **Evidence:** `logs/actions.log` records repeated
  `buttons.damage_adjuster:attack` failures from 16:49:26 through 16:54:26 at
  confidence `0.86`, below the configured `0.90` guard. A read-only live frame
  at wave 1959 authoritatively showed `RUNNING/ATTACK_MENU` with Range and
  Damage/Meter fully visible while Damage was above the viewport. The upgrade
  detector identified Range at Attack-left manifest index 2. Once the retained
  menu position later exposed Damage, the unchanged runtime successfully
  opened and verified `100%` at 16:55:09.
- **Safety response:** Diagnosis used control/process reads, one read-only ADB
  capture, retained fixtures, and source tracing. No tap, swipe, Damage Slider
  change, process restart, battle exit, or Surrender was issued.
- **Cause:** `ensure_upgrade_menu("attack")` verified only the selected
  category and intentionally retained its scroll position.
  `open_damage_adjuster()` then template-matched Damage only in that current
  frame, so an inherited mid-list Attack viewport failed closed without trying
  the existing manifest-aware upgrade traversal.
- **Resolution:** After the current-frame label guard misses, the opener now
  uses the bounded upgrade finder to search for Damage. Every search capture
  must still verify `RUNNING/ATTACK_MENU`; it stops if the screen changes,
  never attempts a purchase, and requires the exact Damage template again
  before the panel tap. The No Strategy inventory threads its pause-aware swipe
  guard through this path, and upgrade-list swipes now produce paired
  operator-facing `ACTION`/`DEBUG` records.
- **Regression:** `test/test_damage_adjuster.py` simulates Damage above the
  current viewport, proves one upward search swipe and exact-label reacquisition,
  and verifies failure when Attack changes during the search.
  `test/test_upgrade_navigation.py` verifies that upgrade swipes are recorded
  before dispatch. Damage, upgrade navigation, No Strategy, Orb Distance,
  initialization, and Tournament-focused validation passed 169 tests.
- **Fixed by:** `3abd62a`.
- **Follow-up (2026-07-30):** The bounded fallback was working, but its expected
  initial miss still appeared as an operator warning. `logs/actions.log`
  recorded five `0.85`/`0.86` current-viewport misses from 19:23 through
  19:59; each was followed by upgrade-list search, an exact Damage match, and
  successful `1E-19%` verification. `safe_tap()` logged every exhausted
  template probe as `WARN` even when its caller owned a fallback or terminal
  workflow result. Commit `325f31e` adds a template-failure log-level option
  that preserves `WARN` by default and lets handled probes remain `DEBUG`.
  Damage Slider search, Home Battle/Resume alternatives, guarded navigation
  retries, and Perk Timeline retries now use the diagnostic path; tap authority
  and unsafe verification warnings are unchanged. Regression coverage is in
  `test/test_clickmap_access.py`, `test/test_damage_adjuster.py`,
  `test/test_home_screen_handler.py`, `test/test_gc_preflight_navigation.py`,
  and `test/test_perk_timeline.py`. Repository-wide validation passed 929
  tests.

### Auto Pick repair used a row position captured during scroll settling

- **Observed:** 2026-07-25 during two authorized live Tier 19 Farm Home
  preflight retries.
- **Symptom:** The first retry tapped Coin Trade-Off's matched up control, then
  blocked because the row at the assumed post-move position had another
  identity. A stronger rank-based retry proved that Coin remained at rank 29
  after the tap instead of advancing to rank 28.
- **Evidence:** `logs/actions.log` records the failures at 11:33:24 and
  11:45:32. The paused inspection located Coin with a stale OCR center near
  `y=1421`, while a screenshot captured after the list settled placed the same
  row at `y=1343`. The failed retry tapped `y=1467`; the repaired retry
  reacquired and tapped Coin at `y=1339`. It then proved all 26 single-rank
  moves from rank 29 to the Farm target at rank 3 and reported the exact
  13-entry order at 12:10:43.
- **Safety response:** Both failed attempts published a blocking
  `perk_configuration` decision, closed Perks, and returned to verified Home
  without starting a battle. The runtime was paused before the bounded panel
  inspection.
- **Cause:** OCR of the long Auto Pick list was authoritative for identity but
  expensive. A frame captured 0.8 seconds after the last swipe could still
  represent a coasting list; by the time OCR completed and the input was
  dispatched, its row center was stale. The original postcondition also
  assumed a one-rank move had a fixed 172-pixel displacement.
- **Resolution:** Every Ban or Auto Pick action now captures the panel again
  immediately before input, uniquely reacquires the same semantic perk, and
  derives the tap coordinate from that action-authority frame. After every
  Auto Pick up-arrow input, repair scrolls from the top and requires the perk's
  authoritative semantic rank to improve by exactly one before another input.
  The exact final prefix comparison remains mandatory.
- **Regression:** `test/test_home_perk_configuration.py` covers 78-pixel
  pre-action row drift, viewport reflow, exact one-rank progress, unchanged
  input refusal, and final order repair.
- **Validation:** Repository-wide validation passed 725 sandbox-compatible
  tests plus the separately permitted localhost HTTP test, for 726 total. The
  live retry restored Coin Trade-Off from rank 29 to rank 3 and passed both
  strategy-owned Perk checks.
- **Fixed by:** `227465b`.

### Home Perk repair searched unnecessarily and continued after Pause

- **Observed:** 2026-07-25 during the authorized live Tier 19 Farm Home
  preflight.
- **Symptom:** The operator saw Coin Trade-Off remain selected while setup
  switched from Ban Perks to Auto Pick and then back to Ban Perks. At 11:14:03
  the persistent control was set to `PAUSED`, but the blocking repair continued
  Available-list swipes and dispatched the eventual deselection at 11:14:24.
- **Evidence:** `logs/actions.log` records the initial Ban and Auto Pick
  inventory, seven downward Ban-list swipes, the single guarded
  `perk_ban_toggle` input, the Pause request interval, and clean managed stop at
  11:14:31. A fresh stopped-screen capture showed the resulting exact five
  selected Farm bans; Coin Trade-Off was no longer selected.
- **Safety response:** No battle had started. Once the continued actions were
  confirmed, the managed service was stopped and left on the Home Perks panel.
  No manual Perk tap or battle action followed.
- **Cause:** The first implementation inventoried both tabs before repairing
  either one and treated extra and missing bans alike, so it searched the
  complete Available list for an already-visible selected extra. The complete
  blocking Home setup also received raw input functions instead of the
  runtime's persistent-control action guard.
- **Resolution:** Ban repair now completes before Auto Pick. It removes an
  authoritative extra directly from the fixed Selected Perks block and scans
  Available rows only for a missing required ban. Every Home setup tap and
  swipe now synchronizes control; Pause waits without cleanup input, and Resume
  restores verified Home before a fresh setup attempt. `STOPPED` no longer
  passes the shared runtime action guard.
- **Regression:** `test/test_home_perk_configuration.py` covers direct selected
  removal and Ban-before-Auto order. `test/test_gc_no_battle_setup.py` covers
  action-free Pause, Home restoration, and suppression of a false startup-gate
  decision.
- **Validation:** Repository-wide validation passed 723 sandbox-compatible
  tests plus the separately permitted localhost HTTP test, for 724 total.
- **Fixed by:** `c4cb745`.

### Farm module preflight rejected Ancestral and could lose the Equip transition

- **Observed:** 2026-07-25 during the Farm Tier 19 experiment Home preflight;
  the same shared module-correction path also serves Farm Tier 18.
- **Symptom:** Home setup opened the Modules rarity panel, selected `None`, and
  then blocked `modules` with `failed to select Ancestral rarity` even though
  the Ancestral row and its configured checkbox tap target were visibly
  present. The same preflight pass emitted navigation and tap activity but no
  concise result for each completed requirement.
- **Evidence:** `logs/actions.log` records the original rejection at 05:59:06
  and the bounded paused reproduction at 06:04:44. The retained live panel
  showed Ancestral at the configured `(850,1475)` tap. The verifier's former
  OCR crop spanned both `Mythic+` and `Ancestral`; single-line OCR returned
  `— im` at confidence 21.5, while a label-only crop returned `Ancestral` at
  confidence 95.0. The first repaired Retry then dispatched a verified Black
  Hole Digestor Equip input at 06:21:00 without observing the role prompt; a
  guarded paused reproduction retained the stable prompt and read it at
  confidence 94.7.
- **Safety response:** Each failure remained behind the requirement-scoped
  gate at verified Tier 19 `NEW_BATTLE` Home. Bounded reproduction and reload
  work used acknowledged pauses and restored verified Home before resuming.
  Battle began only after the complete Home pass; no battle was exited or
  Surrendered.
- **Cause:** The Ancestral single-line OCR region included the adjacent
  `Mythic+` row. Home preflight had no per-requirement result emitter, so only
  input mechanics and terminal setup messages appeared. The module Equip
  transition also allowed one dropped or unobserved input to consume the
  complete prompt timeout, and prompt recognition depended on one OCR layout.
- **Resolution:** Rarity options now use non-overlapping label crops. Home
  preflight logs concise expected, observed, and passed/deferred/waived/failed
  dispositions for every reached check. Equip waits use two OCR layouts and
  retry once only when fresh evidence still shows the same exact Ancestral
  module detail with the `EQUIP` action; all other transitions continue to fail
  closed.
- **Regression:** `test/test_gc_module_loadout.py` covers the isolated
  Ancestral crop, sparse-layout role-prompt OCR, and the bounded dropped-Equip
  retry. `test/test_gc_no_battle_setup.py` covers concise success, failure, and
  one-run-waiver results. Repository validation passed 709 sandbox-compatible
  tests plus the separately permitted localhost HTTP test, for 710 total.
- **Live validation:** Managed PID `2729993` corrected Multiverse Nexus to
  core-primary and Dimension Core to core-assist, accepted both level
  transfers, restored All Rarities, and logged `Modules passed` with all 8
  assignments matched. The normal Tier 19 Battle then completed session
  preflight with every retained Home check, Auto Pick, all eight Modules, and
  all configured Ultimate Weapons valid. Normal handlers resumed.
- **Fixed by:** `1629bb3`, `31e0191`.

### Tournament preflight omitted Poison Swamp Stun and Damage Slider

- **Observed:** 2026-07-25 while reviewing which Tournament requirements
  actually need an in-battle validation run.
- **Symptom:** The Tournament contract required Poison Swamp's primary toggle
  but omitted its Stun control, even though Tournament requires Stun `on`.
  Damage Slider was also absent even though Tournament requires `100%`.
- **Evidence:** Static tracing showed that Home setup already reaches Poison
  Swamp through Workshop Ultimate Upgrades, but its guarded helper and boundary
  evidence accepted only Farm's Stun `off`. Damage Slider was implemented only
  in Farm run initialization and is available exclusively from the in-battle
  Attack menu.
- **Safety response:** No battle, Surrender, Tournament screen, or live device
  action was used for this repair.
- **Cause:** The original Tournament profile modeled only primary Ultimate
  Weapon toggles plus Spotlight missiles. The reusable Poison Swamp correction
  was hard-coded to Farm's desired state, and the Tournament builder had no
  Damage Slider gate.
- **Resolution:** Tournament now declares Poison Swamp Stun `on` and enforces it
  during Home preflight with fresh detail-panel evidence. The guarded helper
  supports either required state, preserving Farm's Stun `off`. Tournament also
  declares Damage Slider `100%`; Home evidence marks it
  `battle_only_control`, and session validation must enforce it successfully
  before inspecting Ultimate Weapons or announcing readiness.
- **Regression:** Poison Swamp tests cover both toggle directions and the
  retained live templates. Home, navigation, compact-profile, generated-plan,
  and observer tests require Tournament Stun `on`, Damage Slider `100%`, and
  slider-before-UW sequencing. Repository validation passed 647
  sandbox-compatible tests plus the separately permitted localhost HTTP test,
  for 648 total.
- **Fixed by:** `534a221`.

### Tournament Home idle state spammed an ambiguous policy message

- **Observed:** 2026-07-25 after Tournament Home pre-flight completed at a
  verified no-battle boundary.
- **Symptom:** `Detected HOME_SCREEN. Evaluating Home policy.` repeated about
  every seven seconds with no completion or next-step message. The repetition
  made the intentionally passive Tournament strategy look stuck even though it
  had completed every available Home check and had no Home handler capable of
  starting a battle.
- **Evidence:** `logs/actions.log` records
  `[GC_NO_BATTLE] Profile Home settings verified/corrected before Battle` at
  00:25:50, followed by uninterrupted `Home control=NEW_BATTLE` and
  `Evaluating Home policy` pairs until the authorized validation battle began
  at 00:32:58. Fresh control, owner, ADB, and screenshot evidence confirmed
  ordinary Tier 18 Home rather than Tournament or an active battle.
- **Safety response:** No automatic Battle or Tournament action followed the
  completed Home pass. A separately authorized, ownership-recorded Tier 18
  validation battle ran the Tournament session checks, passed, used guarded
  Surrender, and returned to verified `NEW_BATTLE` Home.
- **Cause:** The Home dispatcher emitted its generic INFO message on every
  polling frame. It did not distinguish passive Tournament Home pre-flight,
  pending battle-only checks, or a completed validation session.
- **Resolution:** Home policy reporting is now transition-deduplicated.
  Passive Tournament Home emits one explicit state: Home checks pending, Home
  checks complete with in-battle checks pending, or
  `TOURNAMENT_READY` with a manual-start-only instruction. The Tournament
  profile still has no Home handler and cannot start Battle or Tournament.
- **Regression:** `test/test_gc_no_battle_setup.py::
  test_tournament_home_policy_reports_changed_readiness_without_heartbeat`
  requires two identical Home observations to emit only once and requires a
  changed, successful session result to announce manual Tournament readiness.
  Repository validation passed 645 sandbox-compatible tests plus the separate
  localhost HTTP test, for 646 total.
- **Follow-up:** The Tournament profile still described
  `auto_pick_perks: false` even though Tournament has no Perks. The existing
  navigator skipped the Perks panel for that value, but retaining it as a
  desired run setting could authorize a future consumer to disable Auto Pick
  unnecessarily. Tournament now omits Auto Pick from its source invariants,
  generated requirements, action payload, and recorded run configuration; its
  profile loader rejects either Auto Pick state as inapplicable. The generic
  preflight navigator accepts an omitted requirement without opening Perks,
  while Farm continues to require and enforce Auto Pick enabled.
- **Follow-up regression:** `test/test_tournament_preflight.py` and
  `test/test_tournament_observer.py` require complete omission from the compact
  and generated contracts. `test/test_gc_preflight_navigation.py` requires an
  omitted requirement to send no Perks navigation or control action.
- **Fixed by:** `f9c68b6`; Auto Pick follow-up `0a726b7`.

### Tournament Guardian Ally selection was rejected as unverified

- **Observed:** 2026-07-25 during an operator-requested Tournament pre-flight
  from no-battle Home.
- **Symptom:** Home setup removed the equipped Summon chip, then blocked the
  battle at `guardian_chips` with
  `Guardian inventory target missing: buttons.guardian:ally_inventory`.
- **Evidence:** `logs/actions.log` records verified Guardian navigation,
  verified removal of Summon, and
  `TAP_SAFE refused unverified target buttons.guardian:ally_inventory at
  (540,1230)`. Static inspection found that Fetch, Summon, and Scout inventory
  controls had templates, while the Tournament-only Attack and Ally
  replacements retained coordinate-only clickmap entries. The Tournament unit
  router mocked those coordinate taps as successful and therefore did not
  exercise the runtime safety boundary.
- **Safety response:** Read-only control, lock-owner, ADB, screenshot, and log
  inspection confirmed that the gate recovered to no-battle Home and did not
  start a battle. The live process was not paused, reloaded, retried, or given
  device input during the repair.
- **Cause:** Commit `d410b61` correctly made runtime coordinate-only taps fail
  closed without target-specific authority, but the Tournament Guardian
  Attack/Ally path was not migrated to visible template-backed selection.
- **Resolution:** Attack and Ally inventory cards now have bounded
  retained-fixture-backed templates, and Tournament setup selects both through
  the same guarded visible-target path as Fetch and Summon.
- **Regression:** `test/test_tap_safety.py` matches both unequipped Tournament
  targets against the retained Farm Guardian loadout, while
  `test/test_gc_no_battle_setup.py` requires Tournament correction to use
  visible Attack and Ally actions. The focused Guardian, tap-safety, and
  clickmap suites pass all 58 tests.
- **Recurrence:** On the first repaired retry, the new process detected Attack
  and Scout but no Ally or Summon. The original failed attempt had already
  removed Summon before its Ally selection was rejected. Home setup then
  blocked without tapping Ally because its reconciler supported
  `Summon → Ally` replacement but not an already-empty Ally slot.
- **Follow-up resolution:** Guardian reconciliation now treats each supported
  chip category as either already correct, occupied by its known replacement
  source, or empty. An empty slot is filled only after settled Guardian-screen
  evidence and a fresh match of the exact requested inventory card, followed
  by authoritative equipped-state verification. The same recovery covers
  interrupted Attack, Ally, Fetch, Summon, and missing Scout selections.
- **Follow-up regression:** `test/test_gc_no_battle_setup.py` removes Attack
  and Ally independently from an otherwise-correct Tournament loadout and
  requires each pre-flight to resume through its visible inventory target. The
  focused Guardian, tap-safety, and clickmap suites pass all 59 tests.
- **Fixed by:** `2bfb653` and follow-up `1e0c860`.
- **Live validation:** The replacement runtime completed Tournament Home setup
  at 00:25:50. An explicitly agent-owned Tier 18 validation battle then
  authoritatively detected Attack, Ally, and Scout together, passed every
  Tournament session requirement at 00:33:26, and was Surrendered through the
  guarded route. Cleanup returned to verified `NEW_BATTLE` Home. No Tournament
  UI or Tournament start action was used.

### Offscreen weekly mission chest was skipped

- **Observed:** 2026-07-23 at a natural no-battle Home reward opportunity after
  the operator reported that the available weekly chest was outside the visible
  Daily Missions viewport.
- **Symptom:** The runtime detected the Home Daily Missions badge, opened Daily
  Missions, claimed zero rewards, returned Home, and continued into Home setup
  without finding the weekly chest.
- **Evidence:** `logs/actions.log` records the badge-triggered probe from
  09:40:51 through 09:40:58, including `daily=True`, verified Daily Missions
  navigation, and `Claim summary: daily=0 event=0 guild=0`. Static inspection
  confirmed that `_claim_daily_rewards()` checked
  `buttons.claim_weekly_mission_chest` only in the current screenshot; unlike
  the Event Mission path, it did not normalize the horizontal track or perform
  a bounded scroll search.
- **Safety response:** The operator had already stopped the managed process.
  Diagnosis briefly restarted it under an acknowledged indefinite Pause to
  verify the fresh PID, target lock, and `CARDS` state; no emulator input was
  sent, and the original `STOPPED` service/control state was restored.
- **Cause:** Daily Missions retains the weekly-chest track's horizontal
  position. The claim loop assumed every available milestone chest was in the
  initial viewport, so an offscreen chest looked identical to no available
  weekly reward.
- **Resolution:** The handler now checks the entry frame, normalizes the
  weekly track to its first edge, and searches right with bounded overlapping
  swipes. Every swipe verifies the Daily Missions identity before and after,
  and the claim tap uses the fresh frame that exposed the chest. A complete
  edge search without a match remains an ordinary no-reward result.
- **Regression:** `test/test_mission_reward_handler.py` covers the horizontal
  gesture contract, first-edge normalization, offscreen discovery, and
  fresh-frame claim authority while retaining visible-chest and Sunday claim
  policy coverage. The focused handler/scrolling suites passed 40 tests. The
  complete suite passed 637 sandbox tests plus the separately permitted
  localhost-socket test, for 638 total.
- **Fixed by:** `4554f7c`.
- **Live validation:** At 10:13 the repaired runtime verified Daily Missions,
  normalized the track, exposed the chest with one rightward search step,
  matched and claimed it, dismissed the reward reveal, proved no second chest
  at the far edge, and reported `daily=1`. It then completed Tier 18 Farm Home
  setup and in-battle session preflight with no waivers or failed checks.

### Home startup attempted Target Priority and runtime taps could bypass visibility

- **Observed:** Reported by the operator on 2026-07-23 during startup.
- **Symptom:** Automation attempted to check Target Priority from Home even
  though that control exists only in the in-battle side menu. The failing path
  also exposed a broader safety defect: runtime callers could pass
  `require_visible=False`, and `safe_tap` would dispatch a configured point
  without identifying the target at that point.
- **Evidence:** Static inspection found the newly added
  `navigation.home_target_priority` point at `(1025,620)` and the Home setup
  call that treated it as a destination. The same audit found unchecked
  coordinate and static-name calls across navigation, configuration, upgrade,
  module, Perks, and dialog paths. Retained Home evidence rejects the real
  in-battle Target Priority template.
- **Safety response:** Diagnosis and repair were repository-local. No process
  or device interaction was used, and no claim was made about volatile runtime
  state.
- **Cause:** The Home-boundary consolidation incorrectly assumed Target
  Priority was Home-accessible. Independently, the shared tap helper treated a
  caller's visibility-bypass flag and configured geometry as sufficient action
  authority.
- **Resolution:** Home setup now records Target Priority as
  `battle_only_control`; that evidence cannot satisfy either the enforce or
  observe gate, so the generated `RUNNING` action remains responsible for the
  check. Runtime `safe_tap` has no visibility/fallback bypass: template names
  rematch before dispatch, while coordinates and matchless names require a
  complete frame, a bounded target region, and target-specific verification.
  Static controls used by runtime received retained-evidence templates or
  explicit visual guards. The bounded floating-gem sweep remains the
  allowlisted runtime exception, and unchecked gesture taps are isolated to an
  explicitly named operator-tooling API.
- **Regression:** `test/test_tap_safety.py` audits runtime tap authority,
  validates the new templates against retained frames, and proves the Target
  Priority template rejects Home. `test/test_gc_no_battle_setup.py`,
  `test/test_run_initialization.py`, and `test/test_target_priority.py` cover
  the deferred boundary and in-battle ownership. Domain tests cover guarded
  coordinate actions. Follow-up regressions limit reusable frame authority to
  the two urgent purchase modules and exercise repeated level-skip and Damage
  Slider taps from one initial frame.
- **Fixed by:** `d410b61`.
- **Follow-up:** The operator identified EHLS/EALS and Damage Slider purchases
  as urgency-sensitive exceptions where a target verified on the initial frame
  must be assumed stable for the bounded sequence. Commit `5b9f0a2` restores
  level-skip taps during blocking capture and permits the Damage Slider to
  match its arrow once per computed batch. The reusable-authority API caches
  that initial verdict and is statically allowlisted to only those two modules;
  all other runtime targets retain per-action verification.

### Farm preflight repeated Home-accessible checks after Battle start

- **Observed:** Reported by the operator on 2026-07-22 while reviewing a new
  Farm run boundary.
- **Symptom:** The no-battle route checked Workshop locks and then started the
  battle. New-run initialization checked Damage Slider and Target Priority;
  session preflight subsequently used Exit Battle → Go Home to inspect Cards,
  Workshop, Modules, Bots, and Guardians even though all of those settings were
  available at the original `NEW_BATTLE` Home boundary.
- **Evidence:** Fresh control, owner-PID, ADB-target, current-screen, and action
  log inspection confirmed the exact ordering: Home lock checks, Battle start,
  Damage Slider, Target Priority, Cards/Perks/UW/Modules/Event/Guild, then Exit
  Battle → Go Home → Workshop before resuming the same run.
- **Safety response:** Runtime inspection was read-only. The active
  operator-owned battle was not paused, navigated, exited, or Surrendered.
- **Cause:** Complete no-battle setup retained only lock evidence. Session
  preflight therefore repeated its historical persistent-configuration route,
  while Target Priority remained a separate in-battle initialization action.
- **Resolution:** Complete no-battle setup now verifies Cards, Workshop and its
  locks, Bots, Guardians, Modules, and Target Priority from verified Home
  `NEW_BATTLE`. It retains serialized screen evidence, seeds the Target
  Priority gate, and session preflight consumes that proof before checking only
  battle-only Auto Pick Perks and Ultimate Weapons. Attachments without
  boundary proof retain the guarded compatibility route.
- **Regression:** `test/test_gc_no_battle_setup.py`,
  `test/test_gc_preflight_navigation.py`,
  `test/test_gc_preflight_templates.py`, `test/test_run_initialization.py`,
  `test/test_target_priority.py`, and `test/test_strategy_builder_cli.py` cover
  Home ownership, evidence round trips and consumption, gate seeding, static
  Home Target Priority navigation, and generated-plan consistency. The focused
  suite passed 130 tests.
- **Fixed by:** `dacb715`.

### Tournament menu displacement hid an available Guild chest badge

- **Observed:** 2026-07-22 during fresh read-only inspection of an active
  Tournament-era Farm battle.
- **Symptom:** The open side menu visibly showed a purple Guild badge, but
  `measure_menu_reward_badges()` returned `guild_chests=False`; reward-probe
  logs consistently reported `guild=False`.
- **Evidence:** The fresh retained frame matches Guild at `(1015,693)` with the
  badge immediately above-left, while the detector still cropped the normal
  Guild slot at `(852,542,50,55)`. Logged eligible probes otherwise followed
  the intended 30-minute cooldown, so probe frequency was not the detection
  failure.
- **Safety response:** The current frame and logs were captured read-only. No
  reward control, menu destination, or other device action was invoked.
- **Cause:** Badge color measurement used an absolute crop for the normal menu
  grid even though the existing navigation matcher already accounted for the
  Tournament Trophy moving Guild down and right.
- **Resolution:** Guild badge measurement now anchors its narrow color crop to
  the freshly matched Guild icon. Daily and Event retain their existing fixed
  regions, and the Guild action still requires its independent visible match.
- **Regression:** `test/test_mission_reward_handler.py` uses
  `running_menu_tournament_guild_badge_20260722.png` as positive displaced
  evidence and `running_menu_tournament_trophy_20260718.png` as a same-layout
  negative. Its 32 tests passed.
- **Fixed by:** `152d3be`.

### Damage Slider captured and OCRed after every predictable step

- **Observed:** Reported by the operator on 2026-07-22 and confirmed in the
  current run's action log.
- **Symptom:** Enforcing `100%` → `1E-22%` issued the expected 24 decrease
  taps but captured and OCRed a new screen after every tap, making a
  deterministic run-boundary adjustment unnecessarily slow.
- **Evidence:** The action log recorded all 24 one-power-of-ten transitions and
  their per-step capture/verification cycle. Repository tracing confirmed the
  enforcer reacquired authoritative percentage evidence before each arrow tap.
- **Safety response:** Diagnosis used retained logs and fixtures only. The
  active battle and its Damage control were not touched.
- **Cause:** The feedback loop did not encode the slider's observed
  power-of-ten step model, so it could not calculate a known remaining gap.
- **Resolution:** An authoritative current/target pair that consists of exact
  powers of ten now authorizes one bounded same-direction exponent-gap batch,
  followed by settled OCR verification. Dropped steps are recomputed from the
  stable intermediate value, partial dispatch failures stop after
  verification, and unknown sequences retain single-step feedback.
- **Regression:** `test/test_damage_adjuster.py` covers the live-observed
  24-tap gap with one post-batch read, smaller exact batches, dropped-step
  recovery, partial dispatch failure, unknown-sequence fallback, strict
  progress, and final dismissal. Its 16 tests passed.
- **Fixed by:** `e0b246f`.

### Workshop lock gate stayed on the retained Enhance mode

- **Observed:** Reported by the operator on 2026-07-22 while checking the
  Shockwave Size and Bounce Shot locks before a battle.
- **Symptom:** Workshop restores its last open Upgrade/Enhance mode. When that
  mode was Enhance, the no-battle lock gate selected an Attack or Defense
  category without first returning to Upgrade, so it could not reach the
  required upgrade cards.
- **Evidence:** Static tracing showed that an unrecognized Workshop heading
  went directly to `navigation.workshop:attack` or
  `navigation.workshop:defense`. The retained no-battle Workshop fixture
  confirms that Upgrade/Enhance is a separate mode row above those category
  controls, and the focused UI simulator reproduced the retained Enhance
  state.
- **Safety response:** Diagnosis and validation were repository-local. No
  automation process or device was inspected or changed.
- **Cause:** The lock scanner treated Attack/Defense as complete Workshop
  navigation and assumed the Upgrade mode was already selected.
- **Resolution:** The clickmap now owns an explicit Workshop Upgrade action.
  When the required upgrade heading is absent, the scanner selects Upgrade,
  reacquires an unobscured Workshop frame, and then selects the required
  Attack or Defense category.
- **Regression:** `test/test_free_upgrade_locks.py` starts the Workshop
  simulator in Enhance mode and requires the Upgrade action to precede the
  Defense category action. The no-battle integration suites passed 53 tests;
  the full suite passed 493 sandbox tests plus the separately permitted
  localhost-socket test, for 494 total.
- **Fixed by:** `1505ec7`.

### Farm session preflight repeated the Home-only Free Upgrade lock gate

- **Observed:** Reported by the operator on 2026-07-21 after Shockwave Size
  lock detection failed during a running Farm battle.
- **Symptom:** Session preflight attempted to validate the Shockwave Size lock
  after the battle had started, although the Free Upgrade lock controls are
  authoritative only at the no-battle run boundary.
- **Evidence:** Static tracing confirmed that `run_read_only_gc_preflight()`
  left the active battle through guarded Go Home, verified the resumable Home
  state, opened Workshop, and invoked
  `inspect_free_upgrade_locks(enforce=False)`. Missing or invalid evidence then
  made `free_upgrade_locks_valid` false and could request a no-battle repair.
  The earlier full-viewport repair addressed scanning at a real no-battle
  boundary but did not correct this ownership and timing defect.
- **Safety response:** Diagnosis made no process or device changes and relied
  on the operator report plus repository-local tracing.
- **Cause:** Lock inspection participated in both complete no-battle setup and
  active session preflight, so the active route treated missing Home-only
  controls as new authority instead of consuming boundary-owned evidence.
- **Resolution:** Complete no-battle setup remains the only lock scanner and
  enforces all three Farm locks after verified Home `NEW_BATTLE` evidence.
  Active session preflight consumes retained boundary proof without scanning
  or requesting lock repair. A battle attachment without proof records the
  check as `unavailable_deferred`, and only a later genuine `NEW_BATTLE`
  boundary rearms it; `RESUME_BATTLE` does not.
- **Regression:** `test/test_gc_no_battle_setup.py`,
  `test/test_gc_preflight_navigation.py`,
  `test/test_gc_preflight_templates.py`, and
  `test/test_run_initialization.py` cover enforced setup, scanner exclusion,
  evidence retention and completed-run reporting, mismatch blocking, deferred
  attachment, and next-boundary rearming. The retained full-Workshop fixture
  coverage remains in `test/test_free_upgrade_locks.py`.
- **Validation:** 484 sandbox tests passed plus the separately permitted
  localhost-socket test, for 485 total.
- **Fixed by:** `ef41ab9`.

### Farm startup gate skipped visible Workshop upgrades above the battle viewport

- **Observed:** 2026-07-20 while starting a fresh Tier 18 Farm run from
  no-battle Home.
- **Symptom:** The Free Upgrade lock gate repeatedly blocked Battle start with
  `could not locate Shockwave Size in Workshop defense`, even though Shockwave
  Size was visibly centered in the Workshop list.
- **Evidence:** Fresh control, host owner-PID, `localhost:5565` ADB, screenshot,
  and action-log inspection confirmed an acknowledged no-battle Home boundary
  and four matching failures. The retained failure frame places Shockwave Size
  at y=740--986, while `detect_visible_boxes()` used the in-battle upgrade
  region beginning at y=1253 and detected only Wall Health/Wall Rebuild.
- **Safety response:** The runtime was allowed to finish its active guarded
  Home handler, then acknowledged an agent-owned indefinite Pause. No battle
  was started, exited, or Surrendered during diagnosis.
- **Cause:** The shared upgrade scanner owned only the lower in-battle upgrade
  viewport, not the taller Workshop list, and its post-scroll reconfirmation
  rejected a row that settled more than 24 pixels from its first position.
- **Resolution:** The scanner accepts explicit per-column regions, the Workshop
  gate scans y=490--1615, and reconfirmation retries fresh frames and uses the
  freshly detected row position.
- **Live validation:** Shockwave Size, Bounce Shot Targets, and Bounce Shot
  Range were all located and authoritatively measured as checked at the
  no-battle Home boundary.
- **Regression:** `test/test_free_upgrade_locks.py` covers the retained
  `shockwave_size_visible_workshop_20260720.png` frame and an 80-pixel settling
  shift.
- **Fixed by:** `5c6519a`.

### One-battle Force Continue bypassed Auto Pick Perks validation

- **Observed:** 2026-07-20 in the authorized Flame Bot-preset exception run.
- **Symptom:** The run completed at wave 1850 with no recognized selected
  perks, consistent with the operator's observation that Auto Pick Perks had
  remained disabled. The Farm profile required `auto_pick_perks: true`, so the
  normal session preflight should have rejected the run before normal handlers
  resumed.
- **Evidence:** Battle record
  `logs/battles/Battle20260720T215056-0700.json` contains forced-continue
  evidence only through the Home-side Workshop lock checks, with no Auto Pick
  Perks observation, followed by an empty selected-perks list. The action log
  has no `[SESSION_PREFLIGHT]` pass between Force Continue entering the battle
  at 21:23:13 and completing at Game Over at 21:51:15. Source inspection found
  `MissionManager.apply_force_continue_override()` setting both
  `gc_session_preflight_attempted` and `gc_session_preflight_completed` true;
  the former regression test explicitly required that broad bypass.
- **Cause:** Force Continue was implemented as completion of the entire session
  preflight, although the accepted exception concerned only the unavailable
  Farm Bot preset. That suppressed independent in-battle validation including
  Auto Pick Perks.
- **Safety response:** Diagnosis was read-only. The automation owner was
  already stopped/failed, its control state was `PAUSED`, and no current ADB
  target was reachable; no device or process action was attempted.
- **Resolution:** The broad override was replaced by a requirement-scoped
  decision shared by the runtime, CLI, browser client, and native Windows app.
  Retry, one-check bypass, and profile-declared fallbacks re-run the applicable
  boundary with fresh evidence. A Bot-preset waiver cannot complete or waive
  the independent session preflight.
- **Regression:** `test/test_gc_preflight_templates.py` proves that disabled
  Auto Pick Perks still rejects a run under a Bot-only waiver. Directive,
  prompt, API, Home retry, and run-reset coverage is in
  `test/test_automation_control.py`, `test/test_control_surface.py`, and
  `test/test_gc_no_battle_setup.py`.
- **Fixed by:** `4ab91eb`.

### Event Mission claim search skipped past the claimable row

- **Observed:** 2026-07-20 during a badge-triggered Mission reward probe in an
  active Farm battle.
- **Symptom:** The handler claimed the available Daily Mission reward but
  reported zero Event Mission claims even though the Event badge was present.
  The operator observed that the Event list scrolled too far.
- **Evidence:** Fresh control, owner-PID, ADB, screenshot, and action-log
  inspection found that the Event route reached the top, then traversed the
  complete list in three `650`-pixel, `260`-ms swipes without matching the
  claim control. The immediately following inventory pass found all four
  incomplete mission rows and reached the bottom successfully, confirming the
  route and list identity while reproducing the overly coarse traversal.
- **Safety response:** Initial diagnosis was read-only while the current device
  showed an operator-open Perks screen. After the operator explicitly
  authorized a bounded live validation, a host process check confirmed the
  owner, the runtime acknowledged an agent-owned Pause, and the active battle
  was not exited or Surrendered. The handler returned to the verified battle
  and closed the menu before the pause was restored to `RUNNING`; the live
  process acknowledged the resume.
- **Cause:** Consecutive 650-pixel downward gestures did not preserve enough
  viewport overlap to guarantee that a claim control remained visible in a
  sampled frame.
- **Resolution:** The Event downward search gesture is 250 pixels, matching the
  overlapping traversal used by Perks and module inventory.
- **Live validation:** The first short step exposed the claim control, all four
  available Event rewards were claimed, the remaining short steps reached the
  list edge, and the closed-menu reward dot cleared.
- **Regression:** `test/test_mission_reward_handler.py` asserts the exact short
  overlapping gesture.
- **Fixed by:** `e14999c`.

### Ubuntu .NET SDK omitted the WindowsDesktop cross-build SDK

- **Observed:** 2026-07-20 while publishing the native Windows control surface
  on Ubuntu 24.04.
- **Symptom:** `dotnet publish` failed with `MSB4019` because
  `Microsoft.NET.Sdk.WindowsDesktop.targets` did not exist below the selected
  SDK's `Sdks` directory.
- **Evidence:** `dotnet --info` selected Canonical SDK `8.0.129` under
  `/usr/lib/dotnet`; its SDK catalog had no
  `Microsoft.NET.Sdk.WindowsDesktop`. Microsoft's official SDK `8.0.423`,
  installed side-by-side with `dotnet-install.sh`, contained the missing SDK
  and successfully produced a 64-bit single-file Windows GUI executable.
- **Safety response:** The Ubuntu package was not removed or overwritten. The
  Microsoft SDK was installed to a separate user-local directory, and the
  first validation publish used isolated temporary restore/output paths.
- **Cause:** Ubuntu's Canonical SDK package did not ship the WindowsDesktop SDK
  targets required for a WPF cross-build.
- **Resolution:** The checked-in Linux publisher selects a side-by-side
  Microsoft SDK, verifies that WindowsDesktop targets exist before invoking
  `dotnet publish`, and documents the non-admin installation.
- **Regression:** `windows/TheTower.ControlSurface/publish-linux.sh` performed
  a successful self-contained `win-x64` publish during final validation.
- **Fixed by:** `dd1b0f7`.

### BlueStacks 720p resolution stopped screenshot capture

- **Observed:** 2026-07-19 after BlueStacks was changed from `1080x1920` to
  `720x1280` for performance troubleshooting.
- **Symptom:** Every PNG capture was rejected as an unsupported emulator
  resolution, so the automation could neither synchronize the control file nor
  detect the active battle.
- **Evidence:** `logs/actions.log` repeatedly reported `Unsupported emulator
  resolution 720x1280; expected 1080x1920` from 18:49 until the managed process
  was stopped under a persistent pause. A fresh native capture measured
  `720x1280`; after normalization, live detection resolved `RUNNING`,
  `ATTACK_MENU`, the current wave, and the expected overlays.
- **Safety response:** The active operator-owned battle was not surrendered.
  The control state was persisted as `PAUSED` before process replacement, and
  the replacement remained action-blocked while capture and input geometry
  were validated.
- **Cause:** Capture required an exact `1080x1920` framebuffer, while input
  sent canonical coordinates directly to the emulator without a native-size
  mapping boundary.
- **Resolution:** Capture accepts `1080x1920` and `720x1280`, records the native
  target geometry, and normalizes frames into canonical vision space. The
  centralized tap/swipe boundary maps canonical actions back to native pixels;
  affected evidence thresholds and Game Over fallback detection were calibrated
  against 720p observations.
- **Live validation:** Retained state fixtures passed after a 720p round trip,
  and the managed handler captured 24 perks plus all 144 clipboard Stats rows
  from the live 720p emulator.
- **Regression:** `test/test_screen_geometry.py`, `test/test_ss_capture.py`,
  `test/test_ui_state_coverage.py`, and `test/test_game_over_handler.py` cover
  geometry conversion, capture normalization, detection, and terminal fallback.
- **Fixed by:** `15b2b8e`.

### Farm preflight discarded verified Poison Swamp Stun evidence and stranded safe handlers

- **Observed:** 2026-07-18 during the first complete `farm_t18` session
  preflight after restarting from Tournament Results.
- **Symptom:** The guarded detail path logged `Poison Swamp Stun verified off`,
  but the final aggregate reported `stun=off (actual=missing)`. Every other
  requirement passed, yet the session remained blocked while its Tier 18
  battle advanced. An ad-gem claim detected at 06:42 PDT remained visible for
  more than twenty minutes because the terminal mismatch was treated as an
  actively navigating preflight.
- **Safety response:** The failed owner made no further strategy or handler
  taps. It was persisted `PAUSED`, confirmed paused, interrupted cleanly, and
  replaced against the same resumable battle. No Surrender occurred.
- **Cause:** Each UW scroll position was merged into the accumulated evidence
  with a shallow outer `dict.update()`. A later primary-only Poison Swamp
  observation replaced the earlier nested mapping and erased the detail-only
  `stun=off` result. Separately, the runtime used one `session_preflight_pending`
  condition for both active navigation and a terminal non-repairable mismatch,
  so its exclusive action gate also suppressed bounded ad-gem handling.
- **Resolution:** UW observations now merge per label and per toggle. The
  mission manager distinguishes a terminally blocked mismatch from an owned
  Home-repair transition; strategy and mission actions remain blocked, while
  the bounded ad-gem handler stays available. Active validation and repairable
  mismatch navigation retain exclusive tap authority, and Game Over continues
  through the existing terminal lifecycle.
- **Live validation:** The replacement owner resumed the same Tier 18 battle,
  reverified Stun off, completed every session requirement with final
  Poison Swamp evidence `primary=on, stun=off`, released the preflight gate,
  collected the previously stranded ad gem, and observed its overlay disappear.
- **Regression:** `test/test_gc_preflight_navigation.py` reproduces a later
  primary-only Poison Swamp observation after detail verification;
  `test/test_run_initialization.py` covers terminal-block classification and
  proves that only the ad-gem path is released while strategy, mission,
  recovery, and general primary handlers remain blocked. The full suite passed
  328 tests.
- **Fixed by:** `453c484`.

### Tournament preflight opened Tournament Heat instead of the intended section

- **Observed:** 2026-07-18 during guarded read-only validation of an active
  Tier 17+ Tournament.
- **Symptom:** The first shared Farm route attempted to open Perks, but a
  Tournament has no Perks and the same action opened Tournament Heat. After
  Perks was removed from the Tournament contract, the optimized in-battle
  route later tapped `navigation.menu_guild` at `(910, 589)`, opened Tournament
  Heat again, and timed out waiting for `GUILD`.
- **Safety response:** Both passes failed closed. The newly recognized Heat
  dialog was closed through visible evidence, the runtime returned to the
  active battle, and no Surrender, preset selection, equipment change, or
  Home repair occurred.
- **Cause:** The generic session route assumed every profile had Perks, and the
  historical Guild coordinate addressed whichever control occupied that grid
  cell rather than proving the Guild button was visible.
- **Recurrence:** Later on 2026-07-18, the Tournament Trophy control shifted
  Guild from the no-Trophy location `(910, 589)` to `(1015, 694)`. The interim
  fixed coordinate happened to work only for the Trophy layout and exposed the
  same missing action-authority boundary.
- **Resolution:** Tournament profiles explicitly omit Perks. Tournament Heat
  has a dedicated semantic state and visible close control, and cleanup knows
  how to restore the battle from it. Daily Missions, Modules, Event, Guild,
  Event Bots, and Guild Members/Guardian navigation now require exact visible
  button templates and tap the match itself. The Guild template searches both
  layouts and resolves the actual location on each frame.
- **Live validation:** The corrected route opened Cards, Ultimate Weapons,
  Modules, Bots, and Guardians in-battle, used Home only for Workshop, passed
  every Tournament requirement, and resumed the same active battle.
- **Regression:** `test/test_gc_preflight_navigation.py` covers profiles
  without Perks and proves side-menu/tabs use visible taps;
  `test/test_mission_reward_handler.py` matches Guild at both no-Trophy and
  Trophy positions; and `test/test_ui_state_coverage.py` covers Tournament
  Heat detection.
- **Fixed by:** `4f2ee00` for the initial Tournament route and `592acad` for
  the layout-independent visible action boundary.

### Tournament observer continuously ran the floating-gem tapper

- **Observed:** 2026-07-18 during the first passive Tournament observer run.
- **Symptom:** `logs/actions.log` showed a new 20-second blind floating-gem
  sequence starting immediately after each previous sequence ended, even when
  no ad gem had just been collected.
- **Safety response:** The observer owner was stopped cleanly before the
  natural Tournament end. The bounded tapper stopped with it, and no replacement
  process was launched until the continuous authority was removed.
- **Cause:** The generated Tournament policy named `floating_gem` as an enabled
  handler, and the app interpreted that opt-in as authority to restart a
  background tapper whenever its prior 20-second run ended.
- **Resolution:** Tournament enables only `ad_gem` and terminal-result handling.
  The app may suspend/resume an already running bounded sweep across a control
  pause, but only the ad-gem handler can start a new sweep.
- **Regression:** `test/test_tournament_observer.py` proves a running
  Tournament frame does not start the tapper and retains the existing visible
  ad-gem behavior.
- **Fixed by:** `592acad`.

### Natural Tournament completion was not detected or tracked

- **Observed:** 2026-07-18 at the preserved natural end of a Tier 17+
  Tournament.
- **Symptom:** The visible `TOURNAMENT STATS` dialog was classified `UNKNOWN`.
  The normal `GAME_OVER` handler was not applicable because it expects Perks,
  standard Game Stats fields, and Retry/Home controls, while Tournament offers
  `MORE STATS` and `OK`. The copied detail report also used the legitimate tier
  value `17+`, which the shared parser initially flagged as unparsed.
- **Safety response:** Automation was stopped and control set to `WAIT`. The
  summary was retained; only template-matched `MORE STATS`, `COPY`, and `CLOSE`
  controls were exercised. `OK` was not tapped.
- **Cause:** State definitions had only the normal Game Stats terminal
  indicator, no Tournament result schema/handler existed, and structured
  Round Stats accepted ordinary numeric tiers but not a minimum tier suffix.
- **Resolution:** `TOURNAMENT_RESULTS` is a distinct terminal lifecycle state.
  Its handler persists summary OCR plus the exact copied Round Stats report,
  verifies summary/detailed wave identity, restores the summary, remains in
  `WAIT`, and suppresses recent matching valid duplicates. Numeric values such
  as `17+` parse as `minimum_integer` without weakening other numeric checks.
- **Live validation:** The preserved result produced a valid 144-row record
  with 16 sections and matching summary/detailed wave evidence, then restored
  the Tournament Stats dialog without dismissing it.
- **Regression:** `test/test_tournament_results.py` covers state separation,
  summary OCR, visible controls, persistence, identity, and duplicate
  suppression; `test/test_battle_stats.py` covers `17+`; and
  `test/test_run_initialization.py` covers the Tournament lifecycle boundary.
- **Fixed by:** `592acad`.

### Generic close template did not recognize the Modules detail panel

- **Observed:** 2026-07-16 while opening the first equipped module at the
  natural post-wave-8803 no-battle boundary.
- **Symptom:** Being Annihilator opened correctly, but
  `buttons.close_generic` scored 0.5309 in its configured region and 0.7439
  across the full screen, below its 0.85 threshold. The post-open guard stopped
  the inspection with the detail panel still visible.
- **Cause:** The generic crop includes border context from another modal and is
  not reliable evidence for the Modules detail close control.
- **Resolution:** The generic threshold was not lowered. Modules navigation,
  all eight fixed equipped-slot positions, and the module-detail close control
  now have explicit action geometry. Live inspection required a fresh
  `MODULES` state plus OCR `Equipped:` detail evidence before using the
  dedicated close action.
- **Live validation:** All eight GC module panels opened and closed under the
  guards, and the final Modules-to-Home navigation verified `HOME_SCREEN`.
- **Regression:** `test/test_clickmap_access.py` fixes the explicit geometry
  contract for Modules navigation, equipped slots, and detail close.
- **Fixed by:** `a5dc2a8`.

### Day-length Game Time forced clipboard battle capture into invalid OCR fallback

- **Observed:** 2026-07-16 on the natural Tier 18 wave-8803 Game Over boundary.
- **Symptom:** The copied report contained all 144 exact rows, but validation
  rejected `Game Time = 1d 16h 10m 28s` as unparsed. The handler therefore
  used its guarded OCR fallback, whose initial record missed `Counts`,
  `Health Regenerated`, and `Game Time`, and could not parse
  `Damage > Death Wave`.
- **Evidence:** `logs/actions.log` from 10:14:45–10:15:20 PDT and retained
  fallback source frames named `Game20260716_101413_*_OCR_EVIDENCE.png`.
- **Cause:** `parse_duration_seconds()` accepted hours, minutes, and seconds
  but not the game's day component.
- **Resolution:** Compact durations now accept days. The already-copied report,
  retained Game Stats frame, and 27 valid ordered perks rebuilt the same
  `Battle20260716T101413-0700` record as a valid clipboard-backed record with
  no warnings.
- **Regression:** `test/test_battle_stats.py` covers
  `1d 16h 10m 28s` without changing the existing hour-only behavior.
- **Fixed by:** `3b0d986`.

### Preset identity templates falsely claimed inactive GC presets were active

- **Observed:** 2026-07-15 at a natural no-battle boundary on port 5565.
- **Symptom:** Cards visibly had `Tournament` selected while
  `CARDS_GC_ACTIVE` matched the cyan inactive `GC` slot at confidence 0.9377.
  Bots visibly had `Amplify` selected while `BOTS_FARM_ACTIVE` matched the cyan
  inactive `Farm` slot at confidence 0.9402.
- **Cause:** Each composite template was reliable evidence for the named slot's
  identity but insufficient evidence for its green selected border.
- **Resolution:** Cards and Bots now publish `*_SLOT` identity states. The same
  explicit green-versus-cyan border classifier used by Workshop synthesizes an
  active preflight claim only when the named slot is actually selected. A
  verified `NEW_BATTLE` Home route can correct the supported GC presets and
  known Guardian loadout before allowing Battle to start; unknown layouts fail
  closed.
- **Live validation:** Workshop changed Tourney → Farm, Cards Tournament → GC,
  Bots Amplify → Farm, and Guardians Attack/Ally/Scout →
  Fetch/Summon/Scout. A complete second pass made no preset mutation and
  returned to no-battle Home.
- **Regression:** `test/test_gc_preflight_templates.py` retains both inactive
  screens; `test/test_gc_no_battle_setup.py` covers mutation authority,
  idempotence, unknown configuration, known Guardian replacements, and Battle
  start blocking.
- **Fixed by:** `5238497`.

### Blind Game Over Perks action navigated to Home and lost the battle record

- **Observed:** 2026-07-15 on the natural Tier 20 Game Over session
  `Game20260715_224503`.
- **Symptom:** The handler logged a blind `buttons.perks:game_over` tap at
  `(720, 1034)`, never verified the Perks panel, captured two invalid rows, then
  failed to close Perks. The retained abort frame was already the no-battle
  Tier-selection Home screen, so neither More Stats nor a battle record was
  captured.
- **Evidence:** `logs/actions.log` from 22:45:03–22:45:27 and
  `screenshots/matches/Game20260715_224503_ABORT_Close_Perks.png`.
- **Cause:** A historical static coordinate retained action authority from only
  the parent Game Stats indicator. The handler did not require visible Perks
  button artwork or verify the destination before scrolling/closing.
- **Resolution:** The Perks action now requires an exact Game Stats button
  template and then bounded Perks-panel evidence. Missing button or panel is a
  recoverable incomplete-Perks result only when Game Stats is still visible;
  no blind fallback remains.
- **Regression:** `test/test_game_over_handler.py` covers the real button
  fixture, Home negative, missing-button recovery, and missing-panel recovery.
- **Fixed by:** `5238497`.

### Runtime wave status remained stale during an active battle

- **Observed:** 2026-07-15 after restarting automation during an existing Tier
  19 run.
- **Symptom:** EHLS/EALS verification observed wave 1260 and subsequent status
  reports remained at wave 1300 for more than ten minutes, while a fresh
  read-only screenshot showed the live battle at wave 1986.
- **Context:** Primary state remained `RUNNING`; the UW menu was open and other
  automation continued normally.
- **Impact:** Status output and runtime `last_wave` context were stale even
  though the battle was progressing. The exact final Battle Report remained
  independently available from the clipboard at Game Over.
- **Evidence:** `logs/actions.log` around 11:34–11:51 PDT and the handoff-thread
  screenshot `/tmp/thetower_handoff_current.png` (ephemeral). A later retained
  Tier 20 frame read wave 3502 from a clean detector state but reproduced wave
  1300 when the old hint was seeded 14 or 60 minutes earlier.
- **Cause:** The process-global OCR hint assumed 10 waves/minute, treated more
  than 30 waves/minute plus a small tolerance as implausible, and returned the
  old value when current OCR exceeded that model. The live run could progress
  faster, so the hint could never catch up. The app and status reporter also
  mutated the same hint independently.
- **Resolution:** Wave OCR is stateless and selects only per-frame values
  reproduced by at least two preprocessing variants. Lone outliers, tied
  candidates, and disagreeing quick/heavy consensus return no observation.
  Status reporting reuses the app's observation instead of running a second
  state-mutating read. Progression rate, previous-wave, digit-width, and fixed
  wave-ceiling assumptions were removed.
- **Validation:** All six quick variants read the retained Tier 20 frame as
  3502 at confidence 87–92; the revised detector returned 3502 at aggregate
  confidence 90. The full suite passed with 201 tests.
- **Regression:** `test/test_wave_detector.py` covers consensus, isolated
  high-confidence outliers, tied ambiguity, heavy fallback/disagreement, and
  values above the former ceiling.
- **Fixed by:** `b945118`.

### Game Over WAIT blocked persistent control polling

- **Observed:** 2026-07-15 on a natural Tier 19 Game Over at wave 4969.
- **Symptom:** Changing the control file from `WAIT` to `RETRY` had no effect
  because the handler's private wait loop blocked the main supervisor that
  normally consumed control directives.
- **Resolution:** The Game Over wait now accepts a control-sync callback, polls
  persistent directives, blocks terminal actions while paused, and exits on
  `STOPPED`.
- **Regression:** `test/test_game_over_handler.py` covers WAIT/PAUSED, STOPPED,
  RETRY, and HOME behavior.
- **Fixed by:** `ce536a2`.

### GC preflight acted on incompletely rendered Home evidence

- **Observed:** 2026-07-15 while repeating the post-Retry session preflight.
- **Symptom:** Workshop → Home was classified before the destination had fully
  rendered; Event identity then failed repeatedly at confidence 0.23. The
  cleanup path safely resumed the battle and retried without Surrender.
- **Disproved hypothesis:** The Event template did not depend on its red badge;
  it scored 1.00 against a no-badge active-battle Home fixture.
- **Resolution:** Guarded visible navigation now captures and revalidates the
  primary state on every bounded retry. Home Event/Guild transitions receive a
  bounded settle window without weakening the visible-evidence requirement.
- **Regression:** `test/test_gc_preflight_navigation.py` verifies fresh-state
  recapture across delayed rendering. The complete route subsequently passed
  live.
- **Fixed by:** `ce536a2`.

### Compact Game Stats coin suffixes were misread as zeroes

- **Observed:** 2026-07-15 on the wave-4969 Game Stats dialog.
- **Symptom:** Tesseract read `3.00Q`, `1.50Q`, and `4.49Q` as values resembling
  `3000`, `1500`, and `4.490` because the coin icon/suffix interfered with OCR.
- **Resolution:** The exact copied Battle Report total supplies the
  case-sensitive suffix. Base/ad values are repaired only when their sum
  reconciles to the copied total within a narrow tolerance.
- **Regression:** `test/test_battle_stats.py` covers suffix reconciliation and
  failure to reconcile.
- **Fixed by:** `ce536a2`.

### Initial ordered-Perks OCR retained a low-confidence top row

- **Observed:** 2026-07-15 during the first live structured capture.
- **Symptom:** The top purple perk was recognized below the validation
  threshold, causing source evidence to be retained.
- **Resolution:** Perk text crops are enlarged threefold before OCR; retained
  evidence reprocessed into 27 classified, ordered perks with no warnings.
- **Regression:** `test/test_battle_perks.py` covers ordering, deduplication,
  colors, instance models, and validation.
- **Fixed by:** `ce536a2`.

### Strategy builder compatibility wrapper dropped the repository interpreter

- **Observed:** 2026-07-17 while regenerating the GC profiles after adding the
  Poison Swamp Stun requirement.
- **Symptom:** `.venv/bin/python tools/strategy/build_strategy.py ...` used
  `os.execvp` to launch the underlying executable by its `env python3` shebang,
  so the build failed with `No module named 'cv2'` despite `cv2` being installed
  in the repository virtual environment.
- **Evidence:** Directly invoking
  `.venv/bin/python tools/strategy_builders/build_strategy.py ...` succeeded
  against the same source and environment.
- **Safety response:** No runtime or device action depended on the failed build;
  generated profiles were not accepted until regeneration and tests passed.
- **Cause:** The compatibility wrapper executed the builder file directly, so
  its environment shebang selected `python3` from `PATH` instead of preserving
  the interpreter used to invoke the wrapper.
- **Resolution:** The wrapper now re-executes the underlying builder with
  `sys.executable` and therefore retains the repository virtual environment.
- **Regression:** `test/test_strategy_builder_cli.py` invokes the compatibility
  wrapper as a subprocess, requires a successful Farm plan build, and inspects
  the generated configuration.
- **Fixed by:** `5c6fb25`; dedicated regression coverage added by `36c340f`.

### Retained scroll positions hid GC preflight and module inventory evidence

- **Observed:** 2026-07-16 during the bounded live GC module-gate validation.
- **Symptom:** The Ancestral inventory search reopened at its retained bottom
  position and incorrectly concluded that Project Funding was absent. Later,
  GC preflight opened the remembered Event Bots tab below its preset header;
  the parent `EVENT` state was valid, but `EVENT_BOTS_SCREEN` and the Farm
  preset evidence remained offscreen and preflight safely retried. Final
  cleanup also left Home vertically offset, moving the Event and Guild identity
  icons above their original narrow match regions.
- **Evidence:** `logs/actions.log` records the fail-closed Project Funding
  search at 15:44–15:45, the partial-detail retry at 15:49–15:51, three guarded
  Event Bots preflight failures at 15:58–16:02, and the successful repaired
  lifecycle at 16:10–16:15. Reviewed module frames remain under
  `screenshots/module_inventory_2026-07-16/`; the scrolled Home regression frame
  is `test/fixtures/gc_module_gate_20260716/home_scrolled_new_battle.png`.
- **Safety response:** No module action followed either failed search. The
  developer-owned battle remained resumable while preflight cleanup returned
  to `RUNNING`; automation was paused before diagnostic navigation.
- **Cause:** The game retained scroll positions across visits, while the
  inventory/preflight paths assumed their identity controls would reopen at the
  top; narrow Home identity regions also assumed the default vertical offset.
- **Resolution:** Module inventory and Event Bots navigation perform bounded,
  visually verified rewinds and require complete module-detail evidence;
  widened Home identity regions cover the retained scrolled layout.
- **Regression:** `test/test_gc_module_loadout.py`,
  `test/test_gc_preflight_navigation.py`, and
  `test/test_gc_no_battle_setup.py` cover retained scroll and complete-detail
  authority; `test/test_gc_preflight_templates.py` covers scrolled Home.
- **Fixed by:** `5c6fb25`.

### Guild chest probe retained Guardian and excluded the 750 chest slot

- **Observed:** 2026-07-17 during a bounded live Guild chest collection test.
- **Symptom:** The reward sweep detected a Guild menu badge, opened Guild, and
  returned with `guild=0`. A fresh guarded visit showed that Guild had retained
  its Guardian tab, while the handler assumed Members was already selected.
  After selecting Members, the glowing 750-contribution chest still failed the
  broad match because its 90-pixel template did not fit inside the right edge
  of the declared search region; the best in-region candidate was a claimed
  500 chest at `0.737` confidence.
- **Evidence:** `logs/actions.log` records the badge-triggered zero-claim pass
  at 00:16–00:17 and the paused validation at 00:30–00:38. The retained
  Guardian, live glowing 750 chest, and post-claim Members frames are
  `screenshots/matches/guild_chest_guardian_retained_20260717.png`,
  `screenshots/matches/guild_chest_750_available_20260717.png`, and
  `screenshots/matches/guild_chests_claimed_20260717.png`.
- **Safety response:** The live owner consumed persistent `PAUSED` before
  diagnostic navigation. Every tap used repeated complete frames plus fresh
  state or matched-control evidence; the existing battle was not ended and was
  returned to its resumable in-battle screen.
- **Cause:** Guild retained its last selected tab, but the collector assumed
  Members was already active; the chest match region also ended before a full
  template could fit over the rightmost 750-contribution slot.
- **Resolution:** Guild collection explicitly reselects Members and waits for
  the panel to settle before matching; the chest region includes the complete
  rightmost slot.
- **Regression:** `test/test_mission_reward_handler.py` covers retained-tab
  reselection and a complete template match in the 750-contribution slot.
- **Fixed by:** `5c6fb25`.

### Event reward probe scanned the retained Bots tab as Event Missions

- **Observed:** 2026-07-19 during a badge-triggered reward probe from no-battle
  Home.
- **Symptom:** The probe opened the Event parent screen, found no Event Mission
  claim control, and recorded four apparently incomplete rows even though two
  Event Mission rewards were claimable. The user correctly rejected that
  conclusion.
- **Cause:** Event retains its last-selected child tab. The prior live probe
  entered Event while Bots was selected, but the reward handler treated the
  shared `EVENT` parent state as sufficient authority to scan mission content.
- **Resolution:** The handler now selects the visible
  `navigation.event:missions_tab` control and revalidates `EVENT` before
  scrolling, matching, claiming, or inventorying.
- **Regression:** `test/test_mission_reward_handler.py` covers retained-Bots-tab
  navigation evidence and the explicit tab-selection sequence.
- **Live validation:** The normal Home runtime selected Missions at 11:29:18,
  claimed two Event rewards at 11:29:23 and 11:29:26, logged
  `daily=0 event=2 guild=0`, and returned to complete `HOME_SCREEN` with the
  Event badge cleared. No battle was started or surrendered.
- **Fixed by:** `6ed3b6f`.

### Home ad-gem detection could not authorize its corresponding tap

- **Observed:** 2026-07-19 during the first normal-runtime validation of Home
  ad-gem collection.
- **Symptom:** State detection repeatedly reported
  `HOME_AD_GEMS_AVAILABLE`, but the handler's fresh `is_visible` guard rejected
  the same control and performed no tap.
- **Cause:** State detection expands configured regions by 12 pixels, while the
  action matcher deliberately uses zero padding. The stored Home control region
  began 12 pixels to the right of the observed artwork, so only the detection
  matcher could reach it.
- **Safety response:** The guard failed closed, automation was paused, and the
  first process was stopped without tapping. No blind coordinate or reduced
  threshold was substituted.
- **Resolution:** Both Home labels now search a bounded region containing the
  observed geometry, and the semantic button label reuses the proven
  full-control template.
- **Regression:** `test/test_home_ad_gem.py` exercises positive and negative
  fixtures plus the zero-padding action matcher.
- **Live validation:** The restarted normal runtime visibly matched and tapped
  `buttons.claim_ad_gem:home` at `(124,251)`, increased gems from 3564 to 3569,
  verified the overlay disappeared, and remained on Home without starting a
  battle.
- **Fixed by:** `6ed3b6f`.

### No Strategy inventory collapsed an already-selected upgrade menu

- **Observed:** 2026-07-22 during the first live automatic No Strategy
  inventory pass on the active Tier 18 Attack Dissonance battle.
- **Symptom:** After Cards and Perks, the route tapped the already-selected
  Ultimate Weapons tab. The game collapsed the upgrade panel into Cinematic
  Mode; destination verification failed closed and scheduled a retry.
- **Evidence:** `logs/actions.log` records the UW tap at 17:15:09 and the
  guarded timeout at 17:15:22 with `menu=None`,
  `secondary=['CINEMATIC_MODE']`, and `MENU_OPEN`. The live frame showed the
  selected UW tab and collapsed upgrade panel.
- **Safety response:** The failed route changed no configuration and used no
  Home, Exit Battle, or Surrender control.
- **Cause:** The shared selector always tapped a visible destination tab before
  checking whether that menu was already selected.
- **Resolution:** Menu selection now accepts fresh `RUNNING` plus the requested
  menu as success without sending a tap.
- **Regression:**
  `test/test_gc_preflight_navigation.py::test_running_menu_selection_does_not_collapse_already_selected_menu`.
- **Fixed by:** `4565ab4`.

### Cinematic battle evidence competed with the open Perks modal

- **Observed:** 2026-07-22 during the retry of the same live No Strategy
  inventory.
- **Symptom:** The exact Perks-panel match coexisted with the Cinematic wall
  icon left visible behind the modal. State detection raised
  `Multiple primary states matched: RUNNING and PERKS`; the pass failed closed,
  and the following heartbeat exited the fixed systemd unit.
- **Evidence:** `logs/actions.log` records the Perks open at 17:18:05, guarded
  failure at 17:18:10, and process exit at 17:18:12. The fresh failure frame
  matched `indicators.perks_panel` at `1.0` and
  `indicators.cinematic_wall_icon` at `0.983`.
- **Safety response:** The service was not automatically restarted. The device
  remained on Perks in the resumable battle, and the stale lock was unheld.
- **Cause:** `RUNNING` was modeled as an ordinary primary even though its
  controls can remain visible as background evidence behind a specific modal.
- **Resolution:** State detection now distinguishes background-primary evidence
  from ordinary and fallback primaries. Specific modal states win, while a bare
  cinematic battle still resolves to `RUNNING`.
- **Regression:** `test/test_ui_state_coverage.py` covers both precedence and
  background-only fallback; the retained live frame classified as `PERKS` with
  Cinematic Mode secondary before recovery.
- **Live validation:** Replacement PID `3899024` attached once with
  `next_run`, restored the stranded Perks screen, completed the full automatic
  inventory at 17:26:49, and returned to `RUNNING` at wave 4120. The future
  cold-start policy was restored to `immediate`.
- **Fixed by:** `d26f633`.

### Strict in-run Perk selection rejected an alternate valid Perks panel

- **Observed:** 2026-07-22 during the first live strict-whitelist selection on
  a Tier 19 Attack Dissonance battle after an attached-process reload.
- **Symptom:** State detection authoritatively classified the open screen as
  `PERKS`, but the selector's additional `indicators.perks_panel` check rejected
  it. The selector failed closed, selected nothing, closed Perks, and returned
  to the resumable battle.
- **Evidence:** `logs/actions.log` records the guarded open at 20:05:44, the
  refusal at 20:05:46, and the verified close. The preceding No Strategy
  inventory had left a layout that was valid through the alternative
  `indicators.perks_configuration` state key.
- **Cause:** The generic `PERKS` primary state deliberately accepts both the
  normal panel and configuration layout, while the selector redundantly
  required only one of those two templates. A second issue made the longer
  read-only No Strategy inventory run before the pending, time-sensitive Perk
  route after every process replacement.
- **Resolution:** The selector accepts the authoritative `PERKS` primary and
  independently requires the localized choice header, readable disabled Auto
  Pick control, confident fixed-row OCR, and a fresh same-row semantic-family
  reconfirmation before tapping. Pending strict selection now precedes the
  restart-reset in-battle inventory.
- **Regression:** `test/test_run_perk_selector.py` covers strict whitelist
  derivation, all four fixed rows, fresh choice confirmation, no-match refusal,
  and Auto Pick-enabled refusal. The related No Strategy and application tests
  remain covered by `test/test_no_strategy_app.py`,
  `test/test_no_strategy_inventory.py`, and `test/test_app_control_sync.py`.
- **Live validation:** At 20:11:16–20:15:53, replacement PID `4056693`
  selected two accumulated batches totaling 16 choices. Every recorded family
  belonged to the prior Tier 18 run's 22-family whitelist; Auto Pick remained
  off. The terminal Perks inventory later captured 14 ordered families after
  leveled-family deduplication. Both selector routes closed Perks and released
  the battle before lower-priority work.
- **Fixed by:** `fc18b07` and `6fd56fc`; initial strict selector `71852d9`.

### Game Over terminal modal collided with its underlying Event screen

- **Observed:** 2026-07-22 when the Tier 19 battle ended while Event Mission
  inventory was open.
- **Symptom:** The frame matched both `GAME_OVER` and `EVENT`; the detector
  raised `Multiple primary states matched`, and PID `4056693` exited without a
  battle record. On recovery, the first Perks close also failed to restore Game
  Stats before the handler looked for More Stats.
- **Evidence:** `logs/actions.log` records the collision at 20:16:44 and the
  first guarded More Stats refusal at 20:20:24. The retained live frame showed
  Game Stats for Tier 19 wave 1329 layered over Event Missions.
- **Safety response:** The runtime failed closed. No Retry, Home, or new-battle
  action occurred until the terminal frame and stopped owner were freshly
  verified. The first failed capture persisted no incomplete battle record.
- **Resolution:** `GAME_OVER` and `TOURNAMENT_RESULTS` are terminal primaries
  that take precedence over an underlying ordinary screen while multiple
  ordinary or terminal peers remain errors. Game Over Perks capture now waits
  for visible Game Stats and retries the exact Perks close once if the panel is
  still present.
- **Regression:** `test/test_ui_state_coverage.py` covers terminal-over-Event
  precedence and retained ordinary-primary conflict rejection;
  `test/test_game_over_handler.py` covers close retry and restored Game Stats.
- **Live validation:** PID `4065253` classified the same live frame as
  `GAME_OVER`, saved `Battle20260722T202039-0700`, followed the verified Home
  control, and completed the Home lock and Perks configuration inventory at
  20:26:38.
- **Fixed by:** `a0dfd22` and `bda0d66`.

### Mid-run reload lost No Strategy Attack Dissonance identity

- **Observed:** 2026-07-22 after multiple guarded process replacements during
  the active Tier 19 run.
- **Symptom:** The terminal record initially classified the run as Unknown even
  though the live sword badge had been observed before replacement and the
  immediate post-run selected Workshop preset read `Attack Disso` at 92.0 OCR
  confidence.
- **Cause:** No Strategy's passive badge observation was in process memory. A
  replacement could attach safely to the battle but did not retain that
  already-observed identity through the later terminal record.
- **Resolution:** The observer records an immediately captured post-run Attack
  Dissonance Workshop preset as run identity, and historical/current
  classification has the same evidence-based fallback for records finalized by
  an older process.
- **Regression:** `test/test_no_strategy_observer.py` covers post-run identity
  restoration; `test/test_battle_classification.py` covers high-confidence
  classification and the distinct evidence signal.
- **Live validation:** `Battle20260722T202039-0700` now reports high-confidence
  Attack Dissonance from Tier 19 Game Over plus the post-run selected preset.
- **Fixed by:** `b22e41d` and `6986c63`.

### Recovery reopened an unfinished duplicate after its canonical record finalized

- **Observed:** 2026-07-22 after restarting the WAIT-held Home runtime on the
  latest code.
- **Symptom:** Recovery selected old unfinished duplicate
  `Battle20260722T190745-0700` even though equivalent canonical terminal record
  `Battle20260722T185119-0700` had already finalized its post-run evidence.
- **Cause:** Candidate selection filtered finalized records before comparing
  terminal fingerprints, so it could not see that an unfinished duplicate's
  terminal boundary was already complete elsewhere.
- **Resolution:** Recovery first indexes recent finalized terminal
  fingerprints and suppresses matching unfinished duplicates before selecting
  the best remaining pending record.
- **Regression:**
  `test/test_no_strategy_post_run.py::test_finalized_terminal_capture_suppresses_unfinished_duplicate`.
- **Fixed by:** `0603591`.

### Post-run Perks close outran the settling Home battle control

- **Observed:** 2026-07-22 while the WAIT-held runtime retried an old duplicate
  post-run inventory.
- **Symptom:** Closing the Home Perks configuration produced a valid
  `HOME_SCREEN` frame before the Home battle control was readable. The pass
  immediately rejected `UNKNOWN`, retried the complete inventory 60 seconds
  later, and failed the same way a second time.
- **Evidence:** `logs/actions.log` records Perks closes at 20:35:05 and
  20:38:07, followed by `requires NEW_BATTLE, observed UNKNOWN`; subsequent
  fresh frames on both attempts authoritatively read `NEW_BATTLE` Home.
- **Safety response:** The runtime remained in WAIT and did not start a battle.
  The incomplete pass did not finalize or overwrite the duplicate record.
- **Cause:** Navigation waited for the primary Home state but performed only
  one immediate read of the independently authoritative battle control.
- **Resolution:** Every post-run transition back to Home now waits for the
  combined `HOME_SCREEN` plus `NEW_BATTLE` boundary. It remains read-only while
  the control settles and fails closed on a bounded timeout.
- **Regression:**
  `test/test_no_strategy_post_run.py::test_perk_configuration_capture_records_all_tabs_as_raw_evidence`
  exercises the observed `UNKNOWN` then `NEW_BATTLE` transition.
- **Fixed by:** `f6f12ba`.

### Selected strategy was not applied when starting automation

- **Observed:** 2026-07-22 while the operator started automation from
  no-battle Home with a Farm strategy visibly selected in the native Windows
  client.
- **Symptom:** Automation entered a battle without performing the selected
  strategy's Home-only prerequisites and changes.
- **Evidence:** Fresh control, owner-PID, ADB, screenshot, and action-log
  inspection showed that the managed start launched `strategy=none` with
  immediate startup gates, then tapped Battle from verified Home and began the
  No Strategy inventory. The persistent control and managed environment still
  contained the older `none` value.
- **Safety response:** The live owner acknowledged indefinite Pause during its
  read-only inventory. No cleanup input was sent, and the operator explicitly
  authorized ending and replacing this battle for bounded testing.
- **Cause:** The native client retained the changed dropdown only as local UI
  state; its Start request sent run state and gate policy but not the selected
  strategy.
- **Resolution:** Process start now validates and atomically persists the
  visible strategy to both managed environment and control state before service
  launch. The API advertises the new capability and the native client disables
  dependent starts against an older server.
- **Regression:**
  `test/test_automation_process.py::test_start_persists_selected_strategy_before_process_reaches_home`
  covers the ordering and authoritative Pause boundary.
- **Live validation:** The final managed start reported
  `farm_t19_experiment` in its request, process environment, control state, and
  first strategy log before PID `4158098` reached Home. Home setup logged
  complete at 22:01:00 before the Battle tap at 22:01:03.
- **Fixed by:** `9ebfabc`.

### Guardian replacement tap raced the emptied-slot transition

- **Observed:** 2026-07-22 during live validation of a Farm T19 Experimental
  start from no-battle Home.
- **Symptom:** Home setup removed the equipped Attack chip and requested the
  visible Fetch inventory chip, but Fetch did not become equipped. The startup
  gate returned Home and blocked the battle at `guardian_chips`.
- **Evidence:** The action log records Attack removal at 21:07:04, the Fetch
  request at 21:07:06, and the authoritative timeout. A fresh paused Guardian
  frame showed the first slot empty, Ally still equipped, and Fetch visibly
  available in inventory. Repeating the guarded visible-target tap after the
  transition was stable equipped Fetch immediately.
- **Safety response:** The gate did not bypass the mismatch or start Battle.
  Automation acknowledged Pause before manual inspection.
- **Cause:** The selector acted on the earliest empty-slot transition frame
  without reacquiring settled inventory evidence.
- **Resolution:** Guardian replacement waits for a bounded settle and
  reacquires fresh Guardian evidence before selecting the inventory chip.
- **Regression:** `test/test_gc_no_battle_setup.py` reproduces a selector that
  rejects the transition-frame tap.
- **Live validation:** The repaired runtime selected Fetch and Summon only
  after the emptied slots settled; the final Home pass verified both before
  continuing to Modules.
- **Fixed by:** `9f030a8`.

### Farm Home setup could not fill a missing Scout Guardian slot

- **Observed:** 2026-07-22 after the Guardian transition repair selected Fetch
  and Summon during the requested Farm T19 Experimental start.
- **Symptom:** The Guardian gate still blocked Battle because Scout was absent
  from the third unlocked slot.
- **Evidence:** Fresh paused Guardian evidence showed Fetch and Ally equipped,
  the third slot empty, and Scout available in inventory. After Ally was
  replaced with Summon, only `GUARDIAN_SCOUT_EQUIPPED` remained missing.
- **Safety response:** The gate remained at no-battle Home and did not bypass
  the missing requirement or tap Battle.
- **Cause:** The repair supported swapping the Farm/Tournament chip pairs but
  assumed Scout was already equipped.
- **Resolution:** The exact lone-Scout mismatch uses a guarded explicit Scout
  inventory target, then requires authoritative equipped evidence.
- **Regression:** `test/test_gc_no_battle_setup.py` covers the empty third-slot
  configuration; `test/test_clickmap_access.py` covers its geometry.
- **Live validation:** The repaired runtime selected Scout at 21:23:18 and
  advanced to Modules. The final Home pass verified Fetch, Summon, and Scout
  before Battle.
- **Fixed by:** `c942b8a`.

### Animated Modules overview displaced equipped icons from fixed crops

- **Observed:** 2026-07-22 during the requested Farm T19 Experimental start
  from no-battle Home.
- **Symptom:** The Modules gate blocked Battle because `cannon_assist` was
  `unknown` and `generator_primary` was `ambiguous`.
- **Evidence:** Two Home passes produced the same failure. Fresh paused details
  identified Amplifying Strike and Galaxy Compressor. On the retained
  overview, their correct correlations rose from `0.118` and `0.314` to above
  `0.9` when translated only a few pixels with the animated icons.
- **Safety response:** The gate remained at no-battle Home and did not waive
  Modules or tap Battle.
- **Cause:** Identity correlation used one fixed crop center even though the
  equipped icon art animates within its overview frame.
- **Resolution:** Each configured slot searches a bounded six-pixel
  neighborhood while retaining Ancestral-frame, minimum-confidence, and
  competing-candidate margin requirements.
- **Regression:** `test/test_module_icon_index.py` covers a six-pixel shifted
  overview plus unchanged unknown and ambiguity rejection.
- **Live validation:** The repaired live overview confidently identified all
  eight initial modules, corrected the seven authoritative mismatches, and
  later revalidated all eight requested modules with large margins.
- **Fixed by:** `f6a6def`.

### Module inventory accepted stale detail evidence after a candidate tap

- **Observed:** 2026-07-22 while the repaired Modules overview corrected the
  requested Farm T19 Experimental loadout.
- **Symptom:** Seven slots were corrected, but the gate reported that
  Ancestral Dimension Core was not found after reviewing all 16 named
  candidates.
- **Evidence:** The failed pass ranked the real Dimension Core first at
  `(941, 1295)` with score `0.513`, then immediately closed its detail. Fresh
  paused evidence showed the Ancestral card there; its settled detail read
  `Dimension Core` and `Equip`. The repeated settled lookup selected it first.
- **Safety response:** Every rejected candidate was closed without Equip, the
  gate returned no-battle Home, and Battle remained blocked.
- **Cause:** Candidate validation could accept the earliest complete-looking
  detail capture before the newly tapped card settled.
- **Resolution:** Inventory selection waits a bounded settle before
  authoritative detail OCR.
- **Regression:**
  `test/test_gc_module_loadout.py::test_inventory_candidate_waits_for_fresh_detail_before_ocr`.
- **Live validation:** The repaired runtime selected Dimension Core first at
  22:00:18, equipped it as Assist, restored the All Rarities filter, and
  revalidated the complete eight-slot loadout.
- **Fixed by:** `c8b90da`.

### Required Auto Pick Perks mismatch blocked the active Farm preflight

- **Observed:** 2026-07-22 in the requested Tier 19 Farm T19 Experimental
  battle after Home-only setup completed.
- **Symptom:** Session preflight read Auto Pick as disabled, closed Perks
  unchanged, and terminally blocked normal strategy and handler actions.
- **Evidence:** Preflight reported a valid Auto Pick region with zero enabled
  green pixels. The same pass verified every retained Home setting, all eight
  Modules, and all Ultimate Weapons after correcting Poison Swamp Stun.
- **Safety response:** The exclusive gate remained active; no waiver, Home
  repair Surrender, or further strategy action was sent.
- **Cause:** Navigation measured the required in-run control but had no guarded
  correction path.
- **Resolution:** Preflight toggles Auto Pick only after disabled evidence on a
  verified Perks screen and requires fresh enabled evidence before closing.
- **Regression:** `test/test_gc_preflight_navigation.py` covers correction,
  no-op behavior when already enabled, and fail-closed navigation;
  `test/test_gc_preflight_templates.py` covers the control geometry.
- **Live validation:** The guarded helper changed the live checkbox from zero
  to 1,850 green pixels while paused. Retried preflight measured 1,804 pixels,
  completed with no failed checks at 22:10:48, and released normal strategy
  actions at 22:10:57.
- **Fixed by:** `32cfdbc`.

### Home Shockwave lock tap verifier used the battle viewport

- **Observed:** 2026-07-23 at verified Home `NEW_BATTLE` while retrying the
  Farm Tier 18 startup setup.
- **Symptom:** Setup selected Workshop Defense but raised a blocking
  `free_upgrade_locks` gate with
  `detail tap failed for Shockwave Size`.
- **Evidence:** The action log records successful verified navigation through
  Cards, Home, Workshop, and Defense, followed by
  `TAP_SAFE target check rejected workshop_detail:shockwave_size`. On a fresh
  read-only frame of the same Home Workshop layout, the full Workshop scan
  found Shockwave Size at `(26,1061,511,246)` with 95% OCR confidence. The
  verifier's default scan found only the lower Land Mine row.
- **Safety response:** The verifier refused the uncertain tap, setup returned
  Home, and the startup gate kept Battle blocked. Diagnosis captured current
  state read-only; it did not choose a gate option, change the lock, or start a
  battle.
- **Cause:** The verified-tap retrofit rechecked the dynamic tile with
  `detect_visible_boxes()` but omitted the full-height Home Workshop column
  regions used by both initial location and immediate reconfirmation. The
  detector therefore fell back to its lower in-battle viewport and could not
  see Shockwave Size on the unchanged authoritative frame.
- **Resolution:** The tap verifier now reuses the same full Home Workshop
  column regions as location and reconfirmation.
- **Regression:**
  `test/test_free_upgrade_locks.py::test_detail_tap_verifier_reuses_full_home_workshop_scan_regions`
  executes the actual `TapVerification` predicate and requires the full
  Workshop scan contract.
- **Validation:** The focused lock and no-battle suites passed 38 tests. The
  full suite passed 617 sandbox tests plus the separately permitted
  loopback-socket test, for 618 total.
- **Fixed by:** `c04dd86`.

### Battle speed could remain at x1.0

- **Observed:** Operator report on 2026-07-23 during the Tier 18 startup run.
- **Symptom:** Game speed was visibly `x1.0` even though active battles should
  always run at the maximum available speed.
- **Evidence:** The retained frame
  `test/fixtures/open_perks_dynamic_progress_20260723.png` shows the active
  battle at `x1.0`. Runtime inspection found no component that measured or
  restored the battle speed control.
- **Safety response:** Automation was stopped and the operator later ended the
  battle. Diagnosis did not tap the speed control or start another battle.
- **Cause:** Maximum speed was an operator expectation but had no runtime
  owner.
- **Resolution:** A global battle-only guard OCRs the localized speed value,
  verifies the visible plus glyph, and walks the control upward until a
  verified tap produces no change. It checks periodically to restore later
  slowdowns and discovers the current perk-dependent ceiling dynamically.
  Farm defers all speed taps until both EHLS and EALS are complete, preserving
  their urgent purchase priority; Pause is rechecked before every tap. The
  no-effect ceiling probe was later superseded by `1f6385a` after the separate
  recurrence below established that probing `x5.0` could lower it.
- **Regression:** `test/test_game_speed.py` covers OCR, battle-only authority,
  ceiling discovery, periodic restoration, Pause, Home rejection, Tournament
  policy, and EHLS/EALS priority.
- **Validation:** Repository-wide validation passed 630 sandbox-compatible
  tests; the one localhost HTTP test passed separately with socket permission,
  for 631 total.
- **Fixed by:** `6d5f331`.

### Poison Swamp Stun waited until battle despite a Home control

- **Observed:** Operator report and bounded live inspection on 2026-07-23 at
  verified no-battle Home.
- **Symptom:** Farm treated Poison Swamp Stun as an in-battle-only correction,
  adding avoidable work after Battle even though the same detail control is
  available from Home.
- **Evidence:** Verified Home navigation through Workshop and its green
  Ultimate Upgrades category exposed the full-width Poison Swamp card. Tapping
  its isolated icon opened the existing detail overlay, whose retained
  templates authoritatively measured Stun `off`. The exact source frame is
  retained as
  `test/fixtures/poison_swamp_workshop_home_20260723.png`.
- **Safety response:** Automation was stopped. Inspection used guarded,
  verified navigation only, did not change Stun, and returned the device to
  verified Home `NEW_BATTLE`.
- **Cause:** The original exception was implemented against only the
  two-column in-battle Ultimate Weapon menu; Home setup had no owner for the
  Workshop Ultimate Upgrades category or its different card geometry.
- **Resolution:** Complete no-battle setup now selects the verified Workshop
  Ultimate Upgrades category, OCR-localizes exactly one Poison Swamp title,
  opens only the isolated icon region, verifies/corrects Stun, and retains
  `NEW_BATTLE` evidence. Session preflight merges that proof with the still
  required in-battle primary-toggle observation and does not reopen Stun.
  Attached runs without fresh Home proof keep the guarded battle fallback.
- **Regression:** `test/test_poison_swamp_stun.py` covers the retained Home
  source, exact geometry, guarded correction, and battle compatibility;
  `test/test_gc_no_battle_setup.py` covers Home ownership; and
  `test/test_gc_preflight_navigation.py` covers boundary-evidence consumption
  without losing the primary toggle.
- **Validation:** The focused Home/preflight suite passed 90 tests. The final
  repository-wide validation passed 630 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 631 total.
- **Fixed by:** `b19dfce`.

### Perks opener matched changing progress text

- **Observed:** 2026-07-23 during the Tier 18 session preflight.
- **Symptom:** The exclusive validation gate retried four times but could not
  open Perks, reporting template confidence between `0.60` and `0.62`.
- **Evidence:** `logs/actions.log` records failures from 02:52:37 through
  02:54:11. The retained failing frame shows `80 / 191`, while the tap
  template included an earlier numeric progress value. The full template scored
  below threshold on that exact frame even though the Perks bar was plainly
  visible.
- **Safety response:** Every uncertain tap failed closed. The operator stopped
  automation; diagnosis used only the retained screenshot.
- **Cause:** The verifier treated dynamic progress digits and fill width as
  part of the stable identity of the Perks bar.
- **Resolution:** The clickmap verifier now matches a tightly bounded stable
  right-edge frame segment while preserving the explicit center tap.
- **Regression:** `test/test_tap_safety.py` requires
  `navigation.open_perks` to match both the older retained frame and
  `test/fixtures/open_perks_dynamic_progress_20260723.png`.
- **Validation:** The exact failing frame passes the repaired verifier. The
  final repository-wide validation passed 630 sandbox-compatible tests plus
  the separately permitted localhost HTTP test, for 631 total.
- **Fixed by:** `b19dfce`.

### Module repair explicitly declined level transfer

- **Observed:** 2026-07-23 after the operator found a sudden, repeatable Tier 18
  progression collapse.
- **Symptom:** Runs that normally reached approximately wave 9,000 began dying
  near wave 1,800 even though the configured module identities were correct.
  Inspection found that automated replacements had not preserved the levels of
  either Primary or Assist modules. At this progression boundary, every Primary
  should be level 201 and Assist modules should be maximized below that level
  (then approximately 193–194).
- **Evidence:** Seven consecutive Tier 18 records ended between waves 1,832 and
  1,849, while reviewed healthy Tier 18 records ended between waves 9,137 and
  9,779. `logs/actions.log` records the old decline action after Assist
  replacements of Being Annihilator, Anti-Cube Portal, and Dimension Core and
  Primary replacements of Black Hole Digestor and Multiverse Nexus.
- **Safety response:** The operator stopped automation and returned the game to
  no-battle Home. Diagnosis and repository validation made no further device
  actions or module replacements.
- **Cause:** The single `_equip_inventory_module` path used by both roles
  hard-coded `buttons.module:decline_level_transfer`, tapping the dialog's left
  option after every replacement. Identity-only preflight evidence still
  reported a valid loadout because it did not measure module levels.
- **Resolution:** Every presented transfer dialog now authorizes the right-side
  accept action from fresh prompt evidence and waits for the Modules overview
  before continuing. This applies to Primary and Assist replacements, removes
  the old decline clickmap action, and fails closed when acceptance cannot be
  verified.
- **Regression:** `test/test_gc_module_loadout.py` covers the complete Primary
  and Assist replacement paths, failed transfer acceptance, and removal of the
  decline action.
- **Validation:** The focused module/clickmap/tap-safety suite passed 46 tests.
  Repository-wide validation passed 634 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 635 total. Live replacement
  was intentionally not repeated because it would disturb the operator's
  repaired loadout.
- **Fixed by:** `2a2d00b`.

### Game-speed maximum probing could lower an already-correct speed

- **Observed:** 2026-07-23 after the operator reported a problem with the new
  battle-only game-speed guard.
- **Symptom:** The guard sent a `+` tap approximately every 30 seconds even
  when the visible speed was already the normal `x5.0` maximum or the
  Game-Speed-perk `x6.3` maximum. Some `x5.0` probes lowered the observed speed
  to `x3.0` instead of leaving it unchanged.
- **Evidence:** `logs/actions.log` records `initial=5.0 final=3.0` with
  `reason=speed_decreased_after_plus` at 07:27:02, 08:40:31, and 10:30:25.
  From 13:04 through the 14:35 observation, it records a verified but
  ineffective tap at `x6.3` roughly every 30 seconds. Source inspection
  confirmed that `maximize_game_speed` defined the ceiling as the first tap
  producing no change, and `GameSpeedGuard` repeated that probe after every
  30-second success interval.
- **Safety response:** Diagnosis used fresh control, owner-process, ADB, and
  log inspection only. The live PID remained under the operator's existing
  `RUNNING` intent; no Pause, tap, restart, Surrender, or code reload was
  performed.
- **Cause:** The original implementation tried to discover a dynamic ceiling
  by tapping until the value stopped increasing. The operator established that
  `x5.0` is already sufficient before the perk and does not need later probing;
  the perk raises the visible value to `x6.3` itself.
- **Resolution:** Any authoritative reading at or above `x5.0` is now satisfied
  without input. Lower readings receive bounded verified `+` taps only until
  they reach at least `x5.0`; a below-target tap that produces no increase is a
  failure rather than proof of a ceiling. Repeated stable no-op log entries are
  suppressed while periodic read-only checking continues.
- **Regression:** `test/test_game_speed.py` covers zero-input `x5.0` and `x6.3`
  readings, restoration from below `x5.0`, below-target no-progress failure,
  and stable no-op log suppression.
- **Validation:** The focused game-speed suite passed 11 tests.
  Repository-wide validation passed 641 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 642 total. A guarded
  active-battle reload then attached the replacement without replaying gates
  and restored the prior control intent. Its first `x6.3` check reported
  `taps=0` and `target_satisfied`; the following complete guard interval
  produced no speed input or repeated no-op log.
- **Fixed by:** `1f6385a`.

### Tournament exclusive validation ignored the configured Modules skip

- **Observed:** 2026-07-25 when the operator had explicitly selected a
  one-run Module skip for Tournament validation.
- **Symptom:** Exclusive validation entered Modules and began changing the
  loadout anyway, making the runtime's behavior appear unrelated to the
  selected check policy.
- **Evidence:** Source inspection found that the exclusive-validation branch
  skipped `_claim_proactive_gate_waivers()` and then explicitly replaced the
  Home-setup waiver map with an empty mapping. The strategy-scoped skip was
  therefore present in control state but unavailable to the setup evaluator.
- **Safety response:** The operator stopped the running battle. Automation was
  paused at no-battle Home before bounded diagnosis or repair; no replacement
  battle was started.
- **Cause:** Exclusive validation had a special-case waiver exclusion even
  though proactive skips are already bound to the exact strategy request and
  consumed for only one run.
- **Resolution:** Exclusive validation now claims the same strategy-scoped
  proactive waivers before Home setup and passes the claimed map into both
  setup and retained preflight evidence. Unwaived checks retain their existing
  blocking behavior.
- **Regression:**
  `test/test_tournament_validation.py::test_exclusive_validation_claims_configured_module_skip`
  configures a Tournament Module skip and requires it to be claimed, passed to
  Home setup, retained, and consumed.
- **Fixed by:** `859351f`.

### Primary/Assist module cycle resolution discarded slot levels

- **Observed:** 2026-07-25 during Farm Home setup after an armor Primary/Assist
  reassignment was required.
- **Symptom:** The correction Unequipped Orbital Augment from armor Assist,
  leaving the slot empty and the module in inventory at level 194. Anti-Cube
  Portal remained armor Primary at level 201. The search later reviewed a
  weakly matched Space Displacer candidate while looking for Anti-Cube Portal.
- **Evidence:** The action log records the interrupted correction and the
  bounded recovery's verified transfers. Fresh final overview evidence matched
  Anti-Cube Portal in armor Assist with level 194 and Orbital Augment in armor
  Primary with level 201, alongside the other six configured Farm modules.
- **Safety response:** Automation was paused before recovery. Candidate detail
  verification rejected Space Displacer without equipping it. Every recovery
  input rechecked persistent Pause and the live ADB lock owner; no battle was
  started.
- **Cause:** Direct replacements accepted level transfer, but cycle resolution
  still used Unequip to free a role. That separated the outgoing module from
  the slot level instead of preserving both role levels. Inventory matching
  also used one fixed-center crop, so small icon displacement could produce a
  misleading best candidate.
- **Resolution:** Occupied replacements require a verified transfer prompt.
  Role cycles use a verified level-1 module of the same family as an
  intermediate, moving each configured module into its destination through
  level transfer and never using Unequip. Empty-slot recovery rejects an
  unexpected prompt. Inventory classification aligns nearby crops, enforces
  separate confidence and runner-up margins, confirms exact detail
  name/action/level evidence, and reacquires settled rarity rows.
- **Regression:** `test/test_gc_module_loadout.py` covers direct replacements,
  intermediate cycle planning, level-1 temporary selection, missing and
  unexpected transfer prompts, and settled filter traversal.
  `test/test_module_icon_index.py` covers aligned inventory candidates and
  authority thresholds.
- **Live validation:** A bounded no-battle recovery transferred armor Assist
  level 194 through Negative Mass Projector, moved Orbital Augment into armor
  Primary with level 201, then transferred Anti-Cube Portal into armor Assist
  with level 194. The final evaluator authoritatively matched all eight Farm
  module identities. Automation remained paused.
- **Fixed by:** `859351f`, `1121bff`, `983e1f0`, and `4edc809`.

### Dim Max-state Range prevented Orb Distance validation from opening

- **Observed:** 2026-07-25 during an automation-owned ordinary battle for
  one-shot Tournament validation.
- **Symptom:** Orb Distance enforcement repeatedly reported
  `range_not_authoritative` and never opened Distance Adjuster even though the
  Attack menu visibly showed Range `98.38m`.
- **Evidence:** `logs/actions.log` records repeated empty Range OCR from
  15:16:45 through 15:20:52 with no Distance Adjuster tap. The exact live frame
  located the Range tile at `(26, 1400, 511, 246)`. Its original OCR crop
  returned no tokens; adaptive local-contrast isolation read `98.38m` at 86%
  confidence.
- **Safety response:** The runtime sent no uncertain Distance Adjuster input.
  Its exclusive-validation receipt retained complete cleanup ownership,
  Surrendered only that disposable ordinary battle after timeout, passed
  through Game Over, and returned to verified Home `NEW_BATTLE`.
- **Cause:** The Range reader passed a tall, unprocessed tile crop to OCR. The
  Maxed value was rendered in dim gray, so the correct tile detector result
  still produced an empty value reading.
- **Resolution:** A failed direct value read now receives one bounded adaptive-
  threshold OCR retry. Generated actions also carry the complete Orb Distance
  preset set: the observed authoritative Range selects its matching preset,
  while a readable Range outside the set records
  `unconfigured_range_preserved` and passes without opening or changing
  Distance Adjuster. Truly unreadable Range evidence continues to fail closed.
- **Regression:** `test/test_orb_distance.py` covers the adaptive Max-value
  retry, configured-Range preset selection, unconfigured-Range preservation
  without any panel or tap, and propagation of the preset set through the
  action executor. `test/test_run_initialization.py`,
  `test/test_tournament_preflight.py`, and
  `test/test_tournament_observer.py` cover the self-contained generated plans.
- **Validation:** The focused Orb Distance and builder suite passed 105 tests.
  Repository-wide validation passed 734 sandbox-compatible tests plus the
  separately permitted localhost HTTP test, for 735 total.
- **Fixed by:** `3bc3ab4`.

### Cards inventory reset dragged outside the scrollable inventory

- **Observed:** 2026-07-25 during Tournament Home preflight after the Cards
  inventory had been left at its bottom position.
- **Symptom:** Automation issued three go-to-top swipes and six forward search
  swipes, but never found Demon Mode and blocked the
  `card_recharge_modes` gate.
- **Evidence:** `logs/actions.log` records the complete failed traversal from
  16:03:05 through 16:03:38. The retained top, Demon Mode, and Nuke inventory
  frames show that the inventory viewport begins below approximately
  `y=1000`; the old go-to-top gesture began at `(540,900)`, in the fixed Active
  Cards area. A prior bounded inspection proved that a drag beginning inside
  the inventory could advance from Demon Mode to Nuke.
- **Safety response:** The failed preflight returned through verified Home
  navigation and blocked Tournament start. No battle was started or altered.
- **Cause:** The reset gesture began outside the inventory's scrollable
  viewport, so it could not move a Cards screen retained at the bottom. The
  subsequent forward search correctly dragged farther down, which also could
  not reach Demon Mode from that position.
- **Resolution:** Both directions now use the same path wholly inside the
  inventory viewport, from `y=1100` to `y=1650` or its inverse, with the
  established 300 ms input duration.
- **Regression:** `test/test_card_swipe_geometry.py` fixes both clickmap
  gestures to the verified in-viewport geometry. The focused Card recharge and
  clickmap suites passed 26 tests; JSON parsing and `git diff --check` also
  passed.
- **Fixed by:** `fea3242`.
- **Follow-up:** Commit `ff1670a` checks both unresolved Cards on the initial
  frame and after each swipe, validates them in whichever order they appear,
  and stops immediately once both pass. The reverse-order regression starts at
  Nuke, validates it first, reaches Demon Mode with one upward swipe, and
  requires no additional reset or forward-search gesture. Focused Card and
  Home/Tournament caller validation passed 130 tests without device
  interaction.

### Perk Wave Requirement OCR dropped the max-level leading digit

- **Observed:** 2026-07-26 while comparing the completed Tier 19 Farm run with
  the two runs immediately before it.
- **Symptom:** Two historical Selected Perks records rendered `Perk wave
  requirement -5.00%`, incorrectly suggesting that the perk had not been
  maxed and initially distorting the run comparison.
- **Evidence:** The retained
  `Game20260725_210917_perks_5_OCR_EVIDENCE.png` and adjacent viewport visibly
  show `-75.00%`. Their overlapping OCR observations were
  `-/5.00%` at 93.5% confidence and `- 75.00%` at 89.8% confidence; the former
  won confidence selection after generic slash removal. The completed
  `Battle20260726T004643-0700` record independently read the same max value as
  `-75.00%`.
- **Safety response:** Perk Wave Requirement was excluded as a causal
  difference once the operator challenged the value. Historical battle files
  were not rewritten; their raw OCR remains available as provenance.
- **Cause:** Tesseract sometimes recognizes the font's leading `7` as `/`.
  Generic cleanup removed that slash, so `-/5.00%` became `-5.00%`.
- **Resolution:** Perk normalization now repairs the label-specific
  `-/5.00%` artifact to `-75.00%` before generic slash cleanup, while retaining
  the original raw text and merging overlapping observations normally.
- **Regression:** `test/test_battle_perks.py` covers both `-/75.00%` and the
  dropped-leading-digit `-/5.00%` form. Reprocessing the retained failed
  viewports produces the semantic key `perk_wave_requirement_75_00`.
- **Fixed by:** `963c771`.

### Stale Home Cards evidence caused an unnecessary repair Surrender

- **Observed:** 2026-07-26 during the first Tier 19 Farm run after Home setup.
- **Symptom:** Home setup reported the Farm Cards preset as passed, but session
  preflight later classified `cards_deck` as a Home-repairable failure,
  Surrendered the active wave-130 Farm battle, and then ran the ordinary Game
  Over Perks/More Stats capture. The resulting
  `Battle20260726T031635-0700.json` record was invalid repair-run evidence.
- **Evidence:** `logs/actions.log` records the Cards pass before the 03:14
  battle start, the wave-130 repair Surrender, and the subsequent Home repair
  finding the Farm Cards preset already active without changing it. The
  retained post-recharge Cards frame contained the Farm slot but not the
  selected-preset indicator; the earlier authoritative preset frame contained
  both.
- **Safety response:** No Cards, Perks, Workshop, Bots, Guardians, Modules, or
  Ultimate Weapon setting was changed by the post-Surrender repair. The
  following Tier 19 battle completed every declared Home and in-battle check
  with `failed_checks: []` and resumed normal handling.
- **Cause:** The final Home configuration aggregate reused the Cards frame
  returned after scanning recharge-mode details rather than the frame that had
  authoritatively verified the preset. Session preflight trusted that retained
  aggregate, interpreted its missing selected indicator as a real mismatch,
  and acquired the profile-declared repair-Surrender authority. The Game Over
  caller forced a Home return for repair but did not suppress ordinary terminal
  capture.
- **Resolution:** Home setup now retains the exact authoritative frame for each
  persistent section and rejects contradictory aggregate evidence before
  Battle. Ordinary setup failures receive up to three complete attempts from
  fresh Home captures before a gate decision blocks. An automation-owned
  repair Surrender now skips Perks/More Stats and battle-record capture while
  preserving the guarded return-to-Home transition.
- **Regression:** `test/test_gc_no_battle_setup.py` covers authoritative preset
  frame retention, contradictory and waived aggregate evidence, successful
  transient retry, three-attempt exhaustion, and fallback after exhaustion.
  `test/test_run_initialization.py::
  test_home_repair_game_over_skips_surrendered_battle_capture` covers terminal
  capture suppression and forced Home routing.
- **Validation:** Focused Home, preflight, control, Tournament, and Game Over
  suites passed 190 tests. Repository-wide validation passed 768
  sandbox-compatible tests plus the separately permitted localhost HTTP test,
  for 769 total.
- **Live activation:** The guarded current-battle reload replaced PID `3793479`
  with PID `3842234`, attached with `next_run` gate semantics, restored the
  configured `immediate` cold-start policy and `RUNNING` control, and advanced
  the unchanged Tier 19 battle from wave 1852 before replacement to wave 1879
  afterward. No Home/session gate or repair action replayed.
- **Fixed by:** `71f7327`.

### Expanded Recent Activity stopped refreshing across log rotation

- **Observed:** Operator report on 2026-07-26 after using the native Windows
  Recent Activity row-detail view.
- **Symptom:** Recent Activity continued showing the pre-rotation rows after
  `actions.log` rotated instead of following entries in the replacement file.
- **Evidence:** The activity API already reopened `actions.log` for every
  request, but the native client deliberately skipped every render while any
  row remained selected. Double-clicking a row selected it to hold the expanded
  detail, so the obsolete selection suppressed the replacement file as well as
  ordinary appends.
- **Safety response:** Diagnosis and validation were repository-local. No
  automation process, device, control directive, or battle was changed.
- **Cause:** Selection preservation had no boundary for a renamed/recreated log
  file or for a selected entry that had left the current API tail.
- **Resolution:** Activity responses now include the identity of the file
  actually read. The Windows client keeps an available selection stable during
  ordinary refreshes, but clears it and renders current activity when that file
  identity changes. Entry matching provides the same recovery when talking to
  an older server or when a selected entry expires from the bounded tail.
- **Regression:** `test/test_control_surface.py::
  test_activity_reports_replacement_log_identity_after_rotation` replaces the
  active log and verifies both a new source identity and the replacement
  entries.
- **Validation:** All 23 control-surface tests and all four logger tests passed.
  Linux cross-publishing produced the self-contained Windows executable.
- **Fixed by:** `06bda52`.

### Incomplete Auto Pick OCR triggered a long no-op Home repair scan

- **Observed:** 2026-07-26 after a natural Tier 19 Farm Game Over followed the
  operator-selected Home route.
- **Symptom:** The completed battle's terminal capture succeeded, but the
  following Home preflight reported an Auto Pick order mismatch and repeatedly
  scrolled from the top of the Perks list. It remained in that scan until the
  runtime was paused.
- **Evidence:** `logs/actions.log` records 28 ordered perks captured at
  17:50:52, the 144-row battle record saved at 17:50:59, and verified Home
  `NEW_BATTLE` at 17:51:11. The later repair intent began at 17:52:33 and
  issued 70 top/forward swipes without one
  `home_preflight:auto_pick_move_up` tap before Pause was acknowledged at
  17:57:53. A fresh paused frame showed the expected ranks 13–16 immediately
  above the visible `16 Rankings Unlocked` divider.
- **Safety response:** Control was set to `PAUSED` and the runtime acknowledged
  that Home setup input was blocked. No Perk arrow was tapped, no battle was
  started or Surrendered, and later troubleshooting used only the retained
  frame, logs, source, tests, and existing protected captures.
- **Cause:** Ranked-order extraction stopped once it had accumulated the
  configured number of unique OCR rows but did not recognize the visual
  `Rankings Unlocked` boundary. A missed ranked row could therefore be replaced
  by the first recognized unranked row below the divider and look like a
  complete mismatch. Home enforcement also treated a value difference from an
  incomplete capture as repair authority. Once repair began, it redundantly
  rescanned the correct prefix and repeated a full rank lookup after every
  already-satisfied row.
- **Resolution:** Auto Pick extraction now recognizes the paired divider rules,
  excludes rows below them, and stops an incomplete capture at that boundary.
  Incomplete or unrecognized evidence cannot authorize a repair. A real,
  authoritative mismatch skips its already-verified prefix and removes the
  redundant second lookup while retaining fresh row reacquisition, exact
  one-rank progress checks, and final full-list verification.
- **Regression:** `test/test_perk_configuration.py` covers paired-divider
  recognition and proves that a missed ranked row is not filled from below the
  boundary. `test/test_home_perk_configuration.py` covers fail-closed
  incomplete evidence and the bounded scan count for a real mismatch.
- **Validation:** The Home Perks, complete no-battle setup, and run
  initialization suites passed 131 tests. Offline checks recognized the
  divider in the retained live frame and in all four protected historical
  Auto Pick capture sets. No post-fix device interaction or runtime activation
  was performed.
- **Fixed by:** `e13e498`.

## Operational lessons

### A detached child may not survive the agent execution wrapper

- **Observed:** 2026-07-15 while replacing the paused port-5565 runtime after
  live Mission reward validation.
- **Symptom:** `nohup .venv/bin/python main.py --adb-port 5565 --strategy none
  ... &` returned exit code 0, but no process survived, the lock retained the
  old owner's metadata, and neither `actions.log` nor the redirected output
  recorded startup.
- **Verification:** Host process inspection found no replacement. Starting the
  same command in a persistent host PTY immediately acquired a new lock,
  consumed `PAUSED / RETRY`, connected to port 5565, and reported the active
  Tier 20 run.
- **Lesson:** A successful shell launch result is not process-start evidence.
  Verify the host PID, refreshed lock metadata, startup log, control
  consumption, and first state report together; use a persistent execution
  session when detached children are reaped by the wrapper.

### An isolated ADB-daemon startup failure is not target inaccessibility

- **Observed:** 2026-07-15 while refreshing handoff state.
- **Symptom:** One isolated command attempted to start its own ADB daemon and
  failed to install the smartsocket listener. This was incorrectly generalized
  into a claim that the sandbox could not reach ADB.
- **Verification:** The approved host execution path immediately returned
  `device` for `timeout 8s adb -s localhost:5555 get-state` and successfully
  captured the current screen.
- **Lesson:** Report the failed invocation precisely. Test the known host path
  with a bounded command before diagnosing ADB or the emulator as unavailable.
- **Prevention:** The project now selects a workspace permission profile with
  explicit loopback access. Startup instructions branch on the current
  session's declared network capability: network-restricted sessions skip the
  known-failing isolated probe, while sessions with unknown capability retry
  an environment-level failure immediately through approved host execution
  without pausing merely to narrate the fallback.
- **Validation:** `codex --strict-config doctor --summary --no-color` loaded the
  project configuration and reported restricted filesystem access with network
  enabled.
- **Hardened by:** `394451e`.
- **Runbook:** See
  [`../runtime_operations.md`](../runtime_operations.md#adb-access).

### A clean lock release must not retain active-looking owner metadata

- **Observed:** 2026-07-27 while reviewing the normal stop-and-inspect path.
- **Symptom:** The OS lock had been released cleanly, but the persistent lock
  file still named the former PID. A following thread therefore had to treat
  an ordinary clean stop as a possibly stale process until it performed
  additional confirmation.
- **Cause:** `SingleInstanceLock.release()` unlocked and closed the file
  without rewriting the acquisition metadata.
- **Resolution:** A held lock now records `state: held`. Before releasing the
  OS lock, a clean shutdown rewrites the same file as `state: released`, clears
  `pid`, and records `released_at`. The control-surface runtime evidence
  exposes both metadata state and release time. The file remains in place so
  lock ownership never races across different inodes.
- **Regression:** `test/test_single_instance.py` covers the clean-release
  marker and subsequent reacquisition; `test/test_control_surface.py` covers
  released metadata in runtime evidence. The full suite passed 788 tests.
- **Fixed by:** `394451e`.
