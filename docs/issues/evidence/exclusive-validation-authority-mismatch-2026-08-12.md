# Exclusive-Validation Authority Mismatch — 2026-08-12

This narrow dated extract supports `ISSUE-2026-041`. It records historical
production behavior and repository analysis; it is not a claim about current
process, device, battle, or control state.

## Bounded production sequence

The source was production-generated
`/home/brianm/dev/python/TheTower/logs/actions.log`. It was read without writes
on 2026-08-12. Only the records needed to establish the owned validation
boundary and absence of validation/Home input are retained here.

From the production repository root, the exact read-only extraction was:

```bash
LC_ALL=C sed -n -e '82882,82914p' -e '82949,82989p' logs/actions.log
```

The first line window covers the Poison Swamp attempt and bounded retry from
00:53:19 through 00:54:14 PDT. The second covers validation setup completion
through the clean service stop from 00:54:51 through 01:00:37 PDT. The table
below retains only the fields needed for this issue: local timestamp, action
class, receipt identity, screen/wave/speed observation, and disposition.

| Local timestamp (PDT) | Retained record |
| --- | --- |
| 00:54:51 | Home-only Tournament setup completed; the runtime announced one-shot ordinary validation and request `d4b75e7204e2401c99276134e0d91137`. |
| 00:54:54 | The verified Home `NEW_BATTLE` control was tapped after the durable ownership claim. |
| 00:55:02 | The claimed ordinary battle reached `RUNNING`; the runtime announced Damage Slider and Ultimate Weapon validation active and blocked normal handlers. |
| 00:55:03–00:59:16 | Status advanced from wave 1 to wave 290 at x5.0. No validation `INPUT` was recorded. |
| 00:59:54 | The five-minute deadline moved the receipt to failed cleanup. |
| 00:59:56–01:00:09 | The owned cleanup opened Menu, opened Exit Battle, verified the dialog, and tapped Surrender. |
| 01:00:12–01:00:20 | The owned battle reached and was freshly observed at `GAME_OVER`. No verified Home `INPUT` followed. |
| 01:00:37 | Automation received `KeyboardInterrupt` and exited cleanly; the control surface recorded the service stop. |

This sequence establishes the observed free-running battle and missing Game
Over cleanup dispatch. It does not by itself establish the internal authority
cause, and it does not attribute the final service stop to automation.

The earlier Poison Swamp verifier recurrence is deliberately not treated as
the cause. At 00:53:19 its first source tap timed out with `unknown`; the normal
complete retry began at 00:54:07 and successfully set and verified Stun `on` by
00:54:14. That separate unresolved detector behavior remains `ISSUE-2026-005`.

## Repository cause

At production base `5c94827518e491a0e6d03d550f82c9fa16e0152b`, the main
loop installed `AuthorityHold.EXCLUSIVE_VALIDATION` for receipt states
`claimed`, `running`, and `cleanup`. Its session-preflight strategy dispatch
still requested `AuthorityHold.SESSION_PREFLIGHT`. The typed matrix grants an
owner only when every non-external hold has that exact label, so the strategy
tick was denied. At Game Over the same exclusive hold denied generic lifecycle
dispatch, and the main loop had no exclusive-validation lifecycle-owner branch.

Strict typed matching entered in
`aad0fb4efc13c9ed0203208e457217304156ac7b` on 2026-08-02. The timestamps in
`ISSUE-2026-001`—claim, approximately five-minute timeout Surrender, then Game
Over without verified Home cleanup—are consistent with both defects, but that
historical inference does not explain its later unlogged `RUNNING` transition.

## Regression boundary

Main-routing regressions traverse actual `App.run` heartbeats. Successive owned
`RUNNING` heartbeats must dispatch Damage Slider, Orb Distance, and session
preflight under `EXCLUSIVE_VALIDATION`; a deadline-expired heartbeat must issue
exactly one owned Surrender, and the following `GAME_OVER` heartbeat must finish
verified Home cleanup under the same owner. If its first result write fails, a
following Home heartbeat must retry only persistence and then release the
boundary before a queued Start, without repeating the terminal tap. A failed
Surrender result write must retry from a later running heartbeat without further
input. A conclusive Surrender followed by failed Home cleanup and then a later
`RUNNING` frame must quarantine that transition, persist a failed old receipt,
consume its Game Over/activity boundary without another input, and adopt the
successor only on a subsequent heartbeat. A confirmed-launch Home heartbeat
must install `EXCLUSIVE_VALIDATION` before dispatch, ordinary attached preflight
must retain `SESSION_PREFLIGHT`, and a paused manually started successor must
not be adopted until the old result finalizes. Separate regressions cover
interactive-development lease admission, failed-Home retention, same-runtime
fresh-Home recovery, confirmed-launch finalization ordering, new-Strategy
ordering, target-handoff deferral, launch conflict denial, transient
validation/launch ownership rereads, terminal Setup Capture admission, initial
validation-launch takeover, asynchronous guard transport, final nested-input
guard evaluation, and interruption by a newly accepted Setup Capture workflow.
They also retain one-frame validation/launch start proof across Pause,
continuity recapture, transient receipt writes, and queued active-battle
Strategy replacement; bind confirmed launch to its validated ADB target;
consume natural terminal proof before mission observation; close proven Game
Over cleanup from later Running, resumable Home, Tournament Results, or
Workshop without input; and keep an inconclusive-Surrender battle under a
suppressive no-input hold until a genuine terminal/no-battle boundary.
Post-dispatch timeout and supersession matrices require `UNKNOWN` and
Home-control `UNKNOWN` to retain that hold; only exact Home, terminal,
Workshop, or Tournament-entry evidence releases it.

The feature-worktree checkpoint passed compilation, state/clickmap validation,
and all 2,430 repository tests in 378.10 seconds. This is repository evidence
only; deployment and explicitly authorized live confirmation remain pending.
