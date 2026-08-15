# Start Battle Strategy Linearization Failure — 2026-08-14

This narrow dated extract supports `ISSUE-2026-047`. It records historical
production behavior and repository analysis; it is not a claim about current
process, device, battle, or control state.

## Bounded production sequence

The source was production-generated
`/home/brianm/dev/python/TheTower/logs/actions.log`. It was read without writes
on 2026-08-14. The reproducible read-only extraction was:

```bash
LC_ALL=C sed -n -e '111526,111590p' -e '111636,111645p' logs/actions.log
```

| Local timestamp (PDT) | Retained record |
| --- | --- |
| 20:16:33 | The control surface queued Strategy `none` for the next run boundary. |
| 20:16:38 | The control surface accepted explicit Start Battle intent. |
| 20:16:39 | The runtime instead acknowledged Strategy `tournament`. |
| 20:16:51–20:17:04 | Tournament Home preflight accepted save-backed checks, fell back to UI for Modules and Orb Distance, and completed setup. |
| 20:17:04–20:17:14 | The runtime claimed receipt `a47a7abe…`, dispatched ordinary `NEW_BATTLE`, and adopted the disposable Tournament validation battle. |
| 20:17:14–20:22:12 | The battle advanced while the exclusive validation gate logged active but emitted no battle-only validation input. |
| 20:22:12–20:22:30 | Timeout cleanup opened the exit route, Surrendered the owned battle, and proved Game Over. |
| 20:27:56 | The runtime passively observed verified Home `NEW_BATTLE`. |
| 20:27:57–20:28:01 | A second `none` selection followed immediately by Start Battle was again replaced by `tournament`. |
| 20:28:05 | The second workflow was rejected for a changed activity scope before Battle input; no second battle launched. |

The first Modules UI visit therefore belonged to Tournament Home preflight,
not No Strategy. The second recurrence establishes that the strategy rewrite
was deterministic at the same selection/Start ordering rather than a one-off
operator-selection ambiguity.

## Repository cause

The control surface built availability and workflow evidence from one fresh
runtime status snapshot. That snapshot also supplied
`strategy_scope.startup_default` to `request_battle_workflow()`. Immediately
after the durable `none` write, the runtime publication could still report its
previous `tournament` acknowledgement. Start Battle then passed that stale
value into the control-store transaction, where it replaced the newer durable
selection and re-armed Tournament exclusive validation.

The repaired Start path uses runtime status only for availability and exact
workflow evidence. It leaves the strategy argument empty so the directive
store snapshots its own accepted selection while holding the same write lock.
Attach retains its older empty-store fallback without allowing it to override
a newer accepted selection. The activity audit now names the Strategy bound to
the workflow.

## Regression boundary

The regression publishes a fresh runtime-owned Home observation whose startup
scope still says `tournament`, durably selects `none` without publishing a new
runtime heartbeat, and immediately requests Start Battle. The resulting
control and workflow must both bind `none`, retain only cancelled historical
Tournament validation, create no pending Tournament receipt, and log the
bound Strategy explicitly.
