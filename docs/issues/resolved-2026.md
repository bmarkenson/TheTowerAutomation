# Resolved Issues and Operational History — 2026

This archive preserves resolved symptoms, evidence, fixes, regression links,
and dated operational lessons. It is historical evidence, not an active
backlog. Current anomalies live in [`../observed_issues.md`](../observed_issues.md),
and actionable work lives in
[`../../PENDING_DEVELOPMENT.md`](../../PENDING_DEVELOPMENT.md).

## Resolved issues

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
