# Open Observed Issues

This is the active ledger for unresolved runtime and tooling anomalies. It is
evidence and recurrence tracking, not a second backlog; actionable work belongs
in [`../PENDING_DEVELOPMENT.md`](../PENDING_DEVELOPMENT.md).

When an issue is fixed, retain its original symptom and evidence, add the
fixing commit and regression-test location, and move the complete entry to
[`issues/resolved-2026.md`](issues/resolved-2026.md). Consult that archive only
for a matching recurrence or historical investigation.

## Open

### Game-speed maximum probing could lower an already-correct speed

- **Observed:** 2026-07-23 after the operator reported a problem with the new
  battle-only game-speed guard.
- **Symptom:** The guard sends a `+` tap approximately every 30 seconds even
  when the visible speed is already the normal `x5.0` maximum or the
  Game-Speed-perk `x6.3` maximum. Some `x5.0` probes lowered the observed speed
  to `x3.0` instead of leaving it unchanged.
- **Evidence:** `logs/actions.log` records `initial=5.0 final=3.0` with
  `reason=speed_decreased_after_plus` at 07:27:02, 08:40:31, and 10:30:25.
  From 13:04 through the current 14:35 observation, it records a verified but
  ineffective tap at `x6.3` roughly every 30 seconds. Source inspection
  confirms that `maximize_game_speed` defines the ceiling as the first tap
  producing no change, and `GameSpeedGuard` repeats that probe after every
  30-second success interval.
- **Safety response:** Diagnosis used fresh control, owner-process, ADB, and
  log inspection only. The live PID remained under the operator's existing
  `RUNNING` intent; no Pause, tap, restart, Surrender, or code reload was
  performed.
- **Status:** Cause confirmed. The operator established that `x5.0` is already
  sufficient before the perk and does not need later probing; the perk raises
  the visible speed to `x6.3` itself. The guard should accept any authoritative
  reading at or above `x5.0` without input and send bounded `+` taps only below
  `x5.0`.

### Home Poison Swamp Stun verification transiently timed out after its source tap

- **Observed:** 2026-07-23 during the authorized Tier 18 Farm Home setup.
- **Symptom:** The first Home verifier attempt authoritatively localized and
  tapped the Poison Swamp Workshop icon at `(155,1273)`, but all post-tap
  observations remained `unknown`; the `ultimate_weapons` gate blocked. A
  normal Retry repeated the complete setup, tapped the dynamically relocated
  icon at `(155,1181)`, and verified Stun `off` without changing it.
- **Evidence:** `logs/actions.log` records the first tap at 10:15:07, the
  `timed out verifying Poison Swamp Stun (unknown)` failure at 10:15:31, and
  the successful retry from 10:16:53 through 10:16:58. The verifier currently
  collapses detail-overlay, Stun-off, and Stun-on template results into
  `unknown` at timeout and retains neither their individual confidences nor the
  final failing frame.
- **Safety response:** The failed check blocked Battle start. Retry used the
  normal requirement path and created no waiver. The retry and complete
  in-battle session preflight both verified Stun `off`.
- **Status:** The Home source icon and detail checkbox are available; the
  successful unchanged retry disproves a missing-control explanation. Exact
  first-attempt cause is unresolved. Unsettled Workshop scroll geometry is a
  credible inference from the different dynamic tap positions, but current
  evidence cannot distinguish a missed detail-open tap from a detail/checkbox
  template miss. On recurrence, retain the final frame and per-poll
  `detail_visible`, detail confidence, off confidence, and on confidence before
  changing retry or stabilization policy. The active investigation is in
  [`backlog/runtime-and-validation.md`](backlog/runtime-and-validation.md#current-validation-gates).

### Startup gate dialog was reported without visible Retry or Bypass choices

- **Observed:** Operator report on 2026-07-23 while the blocking
  `free_upgrade_locks` decision was pending.
- **Symptom:** The browser dialog said that the Startup Gate needed direction,
  but the operator initially could not see a Retry or Bypass choice and
  wondered whether an older GUI was running.
- **Evidence:** The persisted directive created at 02:20:55 contained both
  `retry` and `bypass_once` options. At 02:48:28 the action log records that
  the current control surface resolved the same request with `retry`, and the
  runtime consumed it at 02:48:33. Static assets are served with `no-cache` and
  API status with `no-store`, so available evidence does not establish an
  older browser bundle.
- **Safety response:** No option was chosen during diagnosis. The later Retry
  used the normal requirement path and did not create a waiver.
- **Status:** Unconfirmed transient display problem. The backend choices and
  end-to-end resolution path are covered by
  `test/test_automation_control.py` and `test/test_control_surface.py`. If it
  recurs, retain a browser screenshot and viewport dimensions before
  refreshing so the rendering failure can be distinguished from stale content
  or an off-screen dialog.

### Game Stats OCR dropped a coin-value decimal

- **Observed:** 2026-07-22 while recovering the completed Tier 19 Attack
  Dissonance record after a terminal-state collision.
- **Symptom:** The visible base coins value was `1.82T`, but repaired Game Stats
  OCR recorded `182T`. That could not reconcile with `907.75B` ad coins and the
  copied `2.72T` total.
- **Evidence:** `Battle20260722T202039-0700` is retained with invalid quality
  and warning `Repaired Game Stats coin split disagrees with copied total`.
  The source Game Stats and three Perks screenshots were retained under
  `screenshots/matches/Game20260722_202039_*_OCR_EVIDENCE.png`.
- **Safety response:** The parser did not silently accept or repair the
  disagreement; it retained evidence and excluded the record from valid
  analytics.
- **Status:** Open. Reproduce the decimal-loss path against the retained Game
  Stats frame and add a regression before changing number repair. The active
  task is in
  [`backlog/runtime-and-validation.md`](backlog/runtime-and-validation.md#current-validation-gates).

### Farm Bot preset switch required more Event medals than were available

- **Observed:** 2026-07-20 during the same authorized fresh Tier 18 Farm
  startup validation.
- **Symptom:** After all three Free Upgrade locks passed, no-battle setup could
  not select the required Farm Bot preset and reported
  `preset did not become selected: indicators.bots:farm_slot`.
- **Evidence:** A fresh paused frame showed Flame selected, 115 available Event
  medals, and the game's `NOT ENOUGH MEDALS` dialog stating that 240 medals
  were required to switch presets. The Farm profile has no fallback and
  explicitly requires `bots_preset: Farm`.
- **Safety response:** The runtime acknowledged an indefinite Pause. The dialog
  was dismissed on the verified `localhost:5565` target, the no-battle UI was
  returned to Home, and the repaired runtime was reloaded under that Pause.
  After the operator explicitly authorized one Flame run, the runtime repeated
  the complete setup, confirmed the same exact rejection, recovered Home, and
  started that one battle without Surrendering any run.
- **Status:** This is a real unmet Farm invariant rather than lock-detector
  ambiguity. Commit `4ab91eb` adds a profile-declared **Continue with Flame for
  this run** fallback and requirement-scoped decision handling in the CLI,
  browser, and native Windows app. The fallback waives only `bots_preset` and
  preserves all unrelated session checks. The earlier authorized live pass
  confirmed exact dialog recognition and guarded Home recovery, but used the
  superseded broad override; keep this issue open until the new scoped fallback
  is exercised against the actual insufficient-medals path.

### Event Mission claim appeared to repeat the complete list after one claim

- **Observed:** Uncertain operator observation reported on 2026-07-21.
- **Symptom:** The handler appeared to scan the complete Event Mission list,
  claim one reward, return to the top, and scan the complete list again.
- **Evidence:** The sequence has not been correlated with retained logs or
  captures. The visual observation could represent an intentional claim-all
  convergence pass, the separate inventory pass, another scheduler dispatch,
  or redundant post-claim searching.
- **Safety response:** No runtime or device action was taken while recording
  the observation.
- **Status:** Open. Correlate logs and retained captures with handler control
  flow before changing the termination rule. The active investigation is in
  [`backlog/runtime-and-validation.md`](backlog/runtime-and-validation.md#current-validation-gates).

### Native strategy selection did not report acceptance or live disposition

- **Observed:** 2026-07-20 in the native Windows control surface while the
  managed runtime was active.
- **Symptom:** Selecting a value under **Next start strategy** produced no
  visible accepted-request result. The controls were disabled for an active
  runtime, and the client could only highlight the managed environment value;
  it could not distinguish the current runtime strategy from a pending one.
- **Evidence:** Static inspection confirmed that the API response already
  included `request.accepted`, but the WPF response model discarded `request`.
  The Linux endpoint also rejected every active strategy change and only wrote
  the next-start environment when the process was inactive.
- **Safety response:** The repair was implemented and automatically validated
  before any live change. After a fresh inspection confirmed acknowledged
  indefinite Pause and authoritative no-battle Workshop, the two fixed Linux
  services were restarted to load it. Pause and `localhost:5555` were
  preserved, the emulator was not tapped, and no battle was started or altered.
- **Status:** Commit `86b5cec` adds an allowlisted, versioned strategy
  directive; boundary-aware runtime adoption; current/pending acknowledgement;
  immediate WPF acceptance feedback; and focused regression coverage. A Linux
  self-contained WPF publish succeeds, and the restarted runtime freshly
  reports `WORKSHOP/PAUSED` under the replacement PID. Keep this issue open
  until the operator verifies the native behavior on Windows.
- **Recurrence:** On 2026-07-21, the operator found that a highlighted Current
  strategy made the adoption checkbox ambiguous: the checkbox only modified a
  later strategy-button click, while clicking the already-highlighted strategy
  did not read as an intentional action. Commit `88b603c` replaces the strategy
  buttons and checkbox with one dropdown plus explicit queue/adopt actions,
  preserves an unsent selection across refreshes, and disables no-op adoption
  of Current. The self-contained Linux publish succeeds. Diagnosis and repair
  used only read-only process/device inspection; Windows confirmation remains
  pending.

### Windows client could not identify or reload a stale Linux control service

- **Observed:** 2026-07-21 after main automation was restarted during an
  existing battle but the independent Linux control-surface service was not.
- **Symptom:** The current automation process loaded active-battle strategy
  adoption, while the reachable Linux API omitted `strategy_apply_mode` and
  had no way to tell the Windows client that it predated that feature. Opening
  the client could therefore expose an adoption interaction the server would
  silently reduce to boundary queueing, and recovery required a manual Linux
  service restart.
- **Evidence:** Read-only inspection found the live automation owner started at
  13:05 on the current code, while `thetower-control-surface.service` had
  remained active since 21:26 the previous day. Its status response lacked the
  new strategy apply-mode field even though it acknowledged Farm and reported
  the attached active battle normally.
- **Safety response:** Diagnosis used control/status reads, host PID checks, and
  one read-only device capture. The Linux service, main automation, control
  directives, and active battle were not changed.
- **Status:** Commit `2c06a66` adds server revision/capability metadata and a
  Windows compatibility banner that disables unsupported adoption. With
  confirmation, the client can run only the fixed
  `systemctl --user restart thetower-control-surface.service` command through
  its validated SSH destination. Follow-up commit `ef8df58` makes the decision
  feature-independent: the Windows client now requires its expected API
  version, a minimum server revision, and its required capability set, and the
  restart path verifies that complete contract after reconnection. Future
  client dependencies must advance the server revision and compiled client
  minimum together. The restart reloads installed code but does not deploy an
  update. Focused validation for the original repair passed 55 sandbox tests
  plus the separately permitted localhost HTTP test; the follow-up passed 15
  sandbox tests plus that one localhost test. Both self-contained Linux
  publishes succeeded. Keep this issue open until the generic warning, fixed
  restart, and reconnect verification are exercised from Windows.

### Native top bar retained a running directive after automation stopped

- **Observed:** Reported by the operator on 2026-07-21 in the native Windows
  control surface.
- **Symptom:** The top bar did not clearly change to stopped when automation
  stopped, leaving a misleading running indication.
- **Evidence:** Static inspection shows that the primary `DIRECTIVE` field is
  always rendered from the persisted control state, even when authoritative
  service/runtime evidence says the process is inactive. The inactive
  disposition appears only in smaller detail text below the top bar.
- **Safety response:** Diagnosis was repository-local; no process, control, or
  device state was changed.
- **Status:** Open. Make stopped process state unambiguous in the top bar while
  preserving the saved next-start directive as a separate concept. The active
  task is part of the structured runtime-status/top-bar work in
  [`backlog/runtime-and-validation.md`](backlog/runtime-and-validation.md#agreed-operator-control-sequence).

### A second native-client launch produced a misleading runtime prompt

- **Observed:** 2026-07-20 while launching the self-contained native Windows
  control surface directly from its SMB publish path.
- **Symptom:** A launch displayed a request to install the .NET Desktop Runtime
  even though the selected executable was the approximately 69 MiB
  self-contained artifact and another control-surface process was already
  running unnoticed.
- **Evidence:** The operator identified the existing first process. The selected
  SMB file matched the expected self-contained artifact size. A clean publish
  to a new Linux directory was byte-for-byte identical to the normal publish,
  and static inspection confirmed that the bundle contains the Desktop runtime
  payload. The exact reason the second host invocation showed the runtime prompt
  rather than starting another WPF process remains unconfirmed.
- **Safety response:** Diagnosis and repair are Windows-client-only; no Linux
  automation control, process, ADB target, or emulator state was changed.
- **Status:** Commit `86b5cec` uses a per-session named mutex. A second
  managed launch restores and foregrounds the existing main window, falling back
  to a taskbar flash or an informational message. Keep this issue open until the
  operator verifies the exact SMB second-launch path; a prompt that occurs before
  managed startup would require a packaging-level guard instead.

### Battle History filter dropdowns required repeated clicks

- **Observed:** 2026-07-20 in the native Windows control surface.
- **Symptom:** The Type and capture-quality dropdowns opened only
  intermittently; the operator sometimes had to click repeatedly and in
  different parts of a control before its popup appeared. The first working-tree
  repair then caused the complete app to terminate when the operator selected
  **Open battle history...**.
- **Evidence:** Operator observation confirms the Windows symptom. Static
  inspection found that the main window polled completed battles every five
  seconds and the history window cleared, repopulated, and refreshed its entire
  observable collection for every response, including unchanged responses.
  That unnecessary UI churn was a credible contributor, but the exact causal
  interaction has not been reproduced outside the operator's Windows session.
  The crashing revision had also replaced the previously working initial list
  population with new in-place collection reconciliation using a deferred WPF
  collection-view refresh.
- **Safety response:** Investigation and repair were client-only. No automation
  control, process, ADB target, or emulator state was changed.
- **Status:** Commit `86b5cec` removes the new in-place
  reconciliation, retains the previously working population path only for
  changed responses, and defers updates while a filter popup is open. It also
  contains window-construction failures at the button boundary, enlarges the
  hit targets, and explicitly labels the Quality filter. The self-contained
  application publishes successfully on Linux. Keep this issue open until the
  operator verifies that the window opens without terminating the app and that
  one-click mouse and keyboard behavior survives repeated live refreshes.
- **Recurrence:** The later Strategy-dropdown change accidentally restored the
  in-place reconciliation with `_battleView.DeferRefresh()`. Selecting **Open
  battle history...** then produced the caught WPF error `Cannot change or
  check the contents or Current position of CollectionView while Refresh is
  being deferred.` The deferred collection-view mutation has been removed
  again, the known-working changed-response population path is restored, and
  the self-contained Linux publish succeeds. Windows confirmation remains
  pending.

### Open in-battle side menu suppressed Mission reward scheduling

- **Observed:** 2026-07-20 at 13:01 during an active Tier 18 Farm battle.
- **Symptom:** The in-battle Mission control displayed a red badge with four
  pending rewards, but the running automation made no Mission reward probe or
  claim attempt.
- **Evidence:** Fresh control, owner-PID, ADB, screenshot, and action-log
  inspection confirmed a live `RUNNING` process on `localhost:5555`. The frame
  was authoritatively classified as `RUNNING/MENU_OPEN`; the open-menu badge
  detector reported Daily Missions available, while the scheduler's
  closed-menu attention-dot detector correctly reported false. Static tracing
  found that orchestration always used the closed-menu detector even though
  the reward handler already accepts a verified open side menu.
- **Safety response:** Diagnosis used read-only capture and process inspection.
  The active battle was not paused, tapped, restarted, exited, or Surrendered.
- **Status:** Cause confirmed and commit `2b4315d` selects badge
  evidence according to the verified `MENU_OPEN`/`MENU_CLOSED` overlay.
  Regression coverage is in `test/test_mission_reward_handler.py`. Keep this
  issue open until the updated runtime claims a reward from an open in-battle
  menu.

### Coins/min OCR dropped the magnitude suffix from quadrillion readings

- **Observed:** 2026-07-20 at 09:23:29 during an active Farm battle, with an
  earlier matching event at 08:47:41.
- **Symptom:** The runtime logged an implausible drop from `36.7q` to `37.19`
  (roughly one quadrillion-fold) even though the adjacent accepted readings
  progressed normally from `36.7q` at 09:22:28 to `40.7q` at 09:24:33.
- **Evidence:** `logs/actions.log` preserves the three consecutive status
  samples and the rejected OCR confidence of 75.0. The parsed bare value is
  consistent with OCR omitting the trailing `q`, making the intended reading
  `37.19q`; the earlier event similarly read `28.19` between quadrillion-scale
  samples. Static inspection also found that live parsing stopped at `Q` even
  though battle reports already support the game's later case-sensitive
  suffixes, and `/min` cleanup could consume an uppercase `M` suffix.
- **Safety response:** The existing plausibility guard retained the prior
  trusted `36.7q` rate, and no gameplay action depends on the rejected value.
- **Status:** Fixed in `dd1b0f7`. The live parser reuses the complete
  case-sensitive Tower-number scale, preserves `M` before `/min`, and recovers
  a missing suffix only when the reconstructed rate fits the same
  scale-independent plausibility window. Regression coverage is in
  `test/test_coin_detector.py`. Keep this issue open until the repair is
  observed in the running automation.
- **Recurrence:** On 2026-07-21 the operator reported that Coins OCR still
  fails to recognize `q` or `Q`. No new retained crop has yet been correlated
  with the report, so the next investigation must preserve the crop and OCR
  candidates and distinguish suffix detection failure from parser handling.

### Live ADB target move could not be applied by a paused runtime

- **Observed:** 2026-07-20 after BlueStacks was moved while the managed
  automation process was still running.
- **Symptom:** Saving the new GUI port changed only the next-start environment
  file. Pause/Resume did not recreate the automation process, so its
  process-local `ADB_DEVICE` and target lock remained on the former port.
  After that target became unreachable, the main loop also failed to
  acknowledge `PAUSED` because it synchronized control only after a successful
  screenshot.
- **Evidence:** Fresh control, systemd, lock, and log inspection found one live
  owner locked to `localhost:5565`, an empty `adb devices` result, repeated
  capture failures, and no current Pause acknowledgement. The watchdog then
  treated failed ADB process queries as evidence that the game was absent and
  attempted its restart path even though no device was reachable.
- **Safety response:** The operator-owned run remains under the persisted Pause;
  no Resume, Surrender, process replacement, or device input was performed.
- **Status:** Fixed in `dd1b0f7`. Control now synchronizes before capture, the
  watchdog fails closed on lost ADB and remains action-free while paused, and
  an acknowledged paused runtime can perform a guarded live-target handoff.
  The handoff acquires the new target lock, connects and validates a screenshot,
  then releases the old lock; failure retains the old target and Pause.
  Regression coverage is in
  `test/test_automation_control.py`, `test/test_automation_process.py`,
  `test/test_single_instance.py`, and `test/test_watchdog.py`. Keep this issue
  open until the updated Linux runtime/API and Windows client are deployed and
  a real emulator move is verified.

### Wave OCR dropped the leading digits from wave 1180

- **Observed:** 2026-07-19 at 20:40:18 during an active Tier 18 battle.
- **Symptom:** The runtime reported wave 80 between wave 1070 at 20:39:11 and
  wave 1270 at 20:41:19. The surrounding progression and retained suffix make
  wave 1180 the expected reading.
- **Evidence:** `logs/actions.log` and
  `logs/coins_per_min_20260719-202523.csv` retain the three consecutive status
  observations. A wave result requires agreement from at least two processed
  versions of the same configured crop, so the accepted 80 was a correlated
  omission across variants rather than a lone OCR candidate. The app then
  replaced its last accepted wave with every non-null per-frame result. The
  source frame was not retained because wave sample capture was not enabled.
- **Safety response:** No runtime action was logged around the bad observation,
  and the next status recovered to 1270 without intervention. Diagnosis used
  read-only process, ADB-state, and screenshot inspection; no game action was
  taken.
- **Status:** Unresolved. The exact visual trigger cannot be reconstructed
  without the source frame. Any repair must reject or separately confirm a
  single-frame rollback without restoring the stale fixed-rate hint that was
  removed in `b945118`, and should retain evidence automatically when the
  continuity check rejects a result.

### Saved GUI ADB port was ignored by an outdated installed systemd unit

- **Observed:** 2026-07-19 after the Windows control surface saved port `5565`
  and started the managed automation service.
- **Symptom:** Recent Activity still logged `ADB target = localhost:5555` and
  attempted `adb connect localhost:5555` even though the GUI and API reported
  that `localhost:5565` had been configured.
- **Evidence:** The API audit recorded two successful `5565` configuration
  requests, and `~/.config/thetower/automation-adb.env` contained
  `THETOWER_ADB_PORT=5565`. The installed automation unit lacked its
  `EnvironmentFile` directive, while the checked-in unit contained it, so
  `main.py` received no managed port and used its documented `5555` default.
- **Safety response:** The failed process was completely stopped. The installed
  unit was replaced while inactive and systemd was reloaded; automation was not
  restarted. A direct `adb connect localhost:5565` then succeeded and
  `get-state` returned `device`.
- **Status:** The installed unit now advertises the correct managed environment
  file. Commit `dd1b0f7` makes the API expose and reject this deployment
  mismatch rather than silently accepting a port it cannot deliver, and the
  Windows runtime detail shows the installed-unit evidence. Keep this issue
  open until the next managed start confirms `ADB target = localhost:5565`.

### Tier 18 Farm ended at wave 2644 without completed session preflight

- **Observed:** 2026-07-19 while live-validating the 720p compatibility fix.
  The run ended at wave 2644 even though its prior highest wave was 9355 and the
  two preceding complete Tier 18 Farm records ended at waves 9137 and 9355.
- **Symptom:** The Game Stats screen reported `Killed By Scatter` and 14 Death
  Defies after only 1h 14m 45s of real battle time.
- **Evidence:** The battle initialized EHLS/EALS and target priority normally,
  but session preflight timed out at wave 80 after reaching
  `UPGRADE_DETAIL`; its evidence never became valid. The process was stopped
  shortly afterward. Its later 18:49 restart rejected every 720p frame until
  the resolution work, so no subsequent runtime validation existed. The valid
  record `Battle20260719T194741-0700` captured 24 perks versus 28 in each of the
  preceding long runs; its defensive perks were also less developed, but that
  may be an effect of the early ending rather than its cause.
- **Safety response:** Retry was not selected. The fixed detector captured a
  complete 144-row report and parked the runtime on Game Over with mode `WAIT`.
- **Status:** Exact gameplay cause unresolved. The evidence establishes that
  the battle ran without a completed configuration preflight and through a long
  automation outage; it does not distinguish an unverified loadout from perk
  ordering/RNG or another gameplay cause.

### Native control polling reset pending mode selections and delayed activity

- **Observed:** 2026-07-19 in the Windows control surface. The operator reported
  that selecting `HOME` repeatedly returned to `RETRY`, and Recent Activity
  appeared substantially behind the live action log.
- **Symptom:** A five-second status refresh assigned the server's current mode
  back into the editable combo box, so it could replace a locally selected mode
  before **Set mode** was clicked. The same refresh awaited status, the complete
  battle list, and activity together before rendering any of them, coupling log
  visibility to the slowest request.
- **Evidence:** Static inspection found the unconditional selection assignment
  in `MainWindow.RenderStatus` and the three-request `Task.WhenAll` in its shared
  refresh path. The Linux mode writer itself preserves `HOME`; the regression in
  `test/test_control_surface.py` verifies it remains `HOME` on a later status
  read. A related acknowledgement bug compared an unchanged mode against a
  later state-update timestamp, which could falsely display it as pending.
- **Safety response:** No direct taps or process actions were inferred from the
  display. The persistent control file remained authoritative throughout.
- **Status:** Fixed in `dd1b0f7`. Mode controls apply immediately, use
  field-specific acknowledgement timestamps, and activity has an independent
  one-second refresh with server-side level filters. The WPF client also holds
  selected rows long enough to copy them. The full 431-test Python suite passes
  and the self-contained Windows application publishes on Linux; keep this
  issue open until the operator verifies the updated client on Windows.

### Direct ADB screenshots intermittently returned incomplete black frames

- **Observed:** 2026-07-16 while preserving a natural Game Over boundary and
  again before navigating from no-battle Home to Modules.
- **Symptom:** A direct `adb exec-out screencap -p` returned a valid-sized PNG
  with only small strips of the real UI rendered and nearly all remaining
  pixels black. An immediate repeated capture rendered the complete unchanged
  screen both times.
- **Evidence:** The failed/retry pairs are retained under
  `screenshots/adb_incomplete_frames_2026-07-16/`. A 2026-07-19 reinspection
  found that the current files no longer reproduce the symptom: all four
  decode as complete, and the Home failed/retry pair is byte-identical.
- **Safety response:** Neither incomplete frame was accepted as action
  authority. Navigation waited for a complete repeated frame and a fresh
  project state detection.
- **Status:** Source unresolved. A retained-fixture reconstruction confirmed
  that narrow rendered strips can preserve actionable Game Over state and Home
  control matches. Commits `194e383` and `15b2b8e` close the safety gap: direct
  ADB capture rejects majority-black frames and retries once, while semantic
  state and visible-control action matching independently fail closed.
  Regression coverage is in `test/test_incomplete_frame_authority.py` and
  `test/test_ss_capture.py`. Keep the issue open until the missing original
  corrupted evidence is recaptured or its loss is otherwise resolved.
- **Recurrence:** On 2026-07-20, the first direct verification capture after
  dismissing the insufficient-medals dialog was again a valid-sized,
  majority-black partial frame. It was rejected as action authority; the
  immediate retry rendered the complete unchanged dialog.

### Automation owner exited without a clean shutdown record

- **Observed:** 2026-07-15 while refreshing the final thread handoff.
- **Symptom:** The unified runtime session ended with exit code 1 after its last
  status output, host PID `2933074` no longer existed, and the lock remained on
  disk. `actions.log` ended during a floating-gem sequence at 11:59 PDT without
  `KeyboardInterrupt`, a traceback, or `Exited cleanly`.
- **Concurrent UI evidence:** A fresh screenshot showed the Home Workshop
  `Farm` screen even though the last runtime status claimed `RUNNING/UW_MENU`.
  No automation navigation action explaining that transition was logged.
- **Safety response:** The persistent control file was set to `PAUSED`. No
  navigation, Resume, process restart, or Surrender followed. The current
  battle/resume state is unverified.
- **Status:** Unresolved. Before restarting, determine whether the owner was
  terminated by the execution-session lifetime, crashed outside the structured
  logger, or coincided with manual player activity. Treat the lock as stale and
  the current Workshop screen as authoritative.
- **Recurrence:** A later port-5565 owner (`3342552`) stopped after its final
  22:45:39 state line without a shutdown record and left its lock behind. The
  immediately preceding Game Over handler failure had already navigated to
  no-battle Home. The user subsequently confirmed that the process had been
  killed, but whether that kill was manual or execution-wrapper lifecycle
  remains unestablished.
