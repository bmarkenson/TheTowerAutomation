# Open Observed Issues

This is the active ledger for unresolved runtime and tooling anomalies. It is
evidence and recurrence tracking, not a second backlog; actionable work belongs
in [`../PENDING_DEVELOPMENT.md`](../PENDING_DEVELOPMENT.md).

When an issue is fixed, retain its original symptom and evidence, add the
fixing commit and regression-test location, and move the complete entry to
[`issues/resolved-2026.md`](issues/resolved-2026.md). Consult that archive only
for a matching recurrence or historical investigation.

## Open

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
- **Status:** Resolved in the working tree by re-executing the underlying
  builder with `sys.executable`. Keep this entry open until a fixing commit
  exists, then add that commit and move the entry to `issues/resolved-2026.md`.

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
- **Status:** Resolved in the working tree by bounded, visually verified
  rewind-to-top steps, complete module-detail readiness checks, and regression
  tests in `test/test_gc_module_loadout.py`,
  `test/test_gc_preflight_navigation.py`, and
  `test/test_gc_no_battle_setup.py`; widened Home identity regions are covered
  in `test/test_gc_preflight_templates.py`. Keep this entry open until a fixing
  commit exists, then add that commit and move the entry to
  `issues/resolved-2026.md`.

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
- **Status:** Resolved in the working tree by explicitly reselecting Members,
  waiting for the Guild panel to settle, and widening the chest evidence region
  to include the rightmost slot. Regression coverage is in
  `test/test_mission_reward_handler.py`. Keep this entry open until a fixing
  commit exists, then add that commit and move the entry to
  `issues/resolved-2026.md`.

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
