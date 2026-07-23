# Resolved Issues and Operational History — 2026

This archive preserves resolved symptoms, evidence, fixes, regression links,
and dated operational lessons. It is historical evidence, not an active
backlog. Current anomalies live in [`../observed_issues.md`](../observed_issues.md),
and actionable work lives in
[`../../PENDING_DEVELOPMENT.md`](../../PENDING_DEVELOPMENT.md).

## Resolved issues

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
  separately permitted localhost HTTP test, for 642 total. The active service
  was not reloaded during code validation.
- **Fixed by:** `1f6385a`.

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
- **Runbook:** See
  [`../runtime_operations.md`](../runtime_operations.md#adb-access).
