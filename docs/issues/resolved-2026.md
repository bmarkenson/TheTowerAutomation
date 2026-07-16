# Resolved Issues and Operational History — 2026

This archive preserves resolved symptoms, evidence, fixes, regression links,
and dated operational lessons. It is historical evidence, not an active
backlog. Current anomalies live in [`../observed_issues.md`](../observed_issues.md),
and actionable work lives in
[`../../PENDING_DEVELOPMENT.md`](../../PENDING_DEVELOPMENT.md).

## Resolved issues

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
