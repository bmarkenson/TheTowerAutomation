# Open Observed Issues

This is the active ledger for unresolved runtime and tooling anomalies. It is
evidence and recurrence tracking, not a second backlog; actionable work belongs
in [`../PENDING_DEVELOPMENT.md`](../PENDING_DEVELOPMENT.md).

When an issue is fixed, retain its original symptom and evidence, add the
fixing commit and regression-test location, and move the complete entry to
[`issues/resolved-2026.md`](issues/resolved-2026.md). Consult that archive only
for a matching recurrence or historical investigation.

## Open

### Direct ADB screenshots intermittently returned incomplete black frames

- **Observed:** 2026-07-16 while preserving a natural Game Over boundary and
  again before navigating from no-battle Home to Modules.
- **Symptom:** A direct `adb exec-out screencap -p` returned a valid-sized PNG
  with only small strips of the real UI rendered and nearly all remaining
  pixels black. An immediate repeated capture rendered the complete unchanged
  screen both times.
- **Evidence:** The failed/retry pairs are retained under
  `screenshots/adb_incomplete_frames_2026-07-16/`.
- **Safety response:** Neither incomplete frame was accepted as action
  authority. Navigation waited for a complete repeated frame and a fresh
  project state detection.
- **Status:** Unresolved. Determine whether this is an emulator compositor race,
  an ADB capture/transport issue, or another source of partial frames. Verify
  whether actionable templates always fail closed and add an explicit
  completeness/freshness guard if they do not. The GC module correction and
  Damage Slider control paths now reject wrong-sized or majority-black frames
  before their actions; the latter is covered in
  `test/test_damage_adjuster.py`. The shared capture/action boundary remains
  unresolved.

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
