# Exclusive-Validation Authority Mismatch — 2026-08-12

This narrow dated extract supports `ISSUE-2026-046`. It records historical
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

## 2026-08-15 recurrence

The same production implementation repeated the defect during the next
operator-requested one-shot Tournament validation. The source was the same
production `logs/actions.log`, read without writes on 2026-08-15. The bounded
read-only extraction selected lines 112305–112348.

| Local timestamp (PDT) | Retained record |
| --- | --- |
| 01:26:38 | The runtime created ordinary-validation request `8f9a5bf1e0de4d67afc35d851dd3ec3c`. |
| 01:26:41 | The verified Home `NEW_BATTLE` input was dispatched after the durable claim. |
| 01:26:48 | The owned battle reached `RUNNING`; the runtime announced active Damage Slider and Ultimate Weapon validation. |
| 01:26:48–01:31:37 | The battle advanced to wave 330 at x5.0 with no validation `INPUT`. |
| 01:31:44–01:31:57 | The five-minute timeout opened Menu and Exit Battle, then dispatched exactly one owned Surrender. |
| 01:32:00 | The owned battle reached Game Over. No verified Home cleanup input followed. |
| 01:32:47–01:32:53 | Operator navigation changed the observed screen from Game Over to Modules and then Workshop; these transitions had no automation `INPUT` row. |

This recurrence confirms that the 2026-08-12 authority and cleanup defects
were still deployed. The operator's later manual navigation is not evidence
that automation completed cleanup.
