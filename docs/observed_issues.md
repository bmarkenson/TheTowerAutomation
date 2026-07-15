# Observed Issues and Operational Lessons

This tracked ledger preserves symptoms that may recur across development
threads. It is not a replacement for `PENDING_DEVELOPMENT.md`: unresolved work
still belongs in the backlog, while this file records evidence, diagnosis, and
the resolution trail.

The ledger begins with issues reconstructed from the 2026-07-15 development
thread and is not exhaustive for earlier project history.

## Open

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

### Runtime wave status can remain stale during an active battle

- **Observed:** 2026-07-15 after restarting automation during an existing Tier
  19 run.
- **Symptom:** EHLS/EALS verification observed wave 1260 and subsequent status
  reports remained at wave 1300 for more than ten minutes, while a fresh
  read-only screenshot showed the live battle at wave 1986.
- **Context:** Primary state remained `RUNNING`; the UW menu was open and other
  automation continued normally.
- **Impact:** Status output and runtime `last_wave` context may be stale even
  though the battle is progressing. The exact final Battle Report remains
  independently available from the clipboard at Game Over.
- **Evidence:** `logs/actions.log` around 11:34–11:51 PDT and the handoff-thread
  screenshot `/tmp/thetower_handoff_current.png` (ephemeral).
- **Status:** Unresolved. Reproduce and inspect the wave detector's update and
  monotonic-hint behavior non-destructively before changing it.

## Resolved

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
- **Runbook:** See [`runtime_operations.md`](runtime_operations.md#adb-access).
