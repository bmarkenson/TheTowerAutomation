# Starting a New Development Thread

This is the canonical entrypoint for every new TheTower development thread.
Handoffs should reference this document instead of reproducing all stable
operational guidance from memory.

## Required reading order

1. [`../AGENTS.md`](../AGENTS.md) — automatic safety and development rules.
2. [`runtime_operations.md`](runtime_operations.md) — ADB, process, control,
   live-action, and troubleshooting procedures.
3. [`observed_issues.md`](observed_issues.md) — unresolved anomalies and
   recurrence history.
4. [`../PENDING_DEVELOPMENT.md`](../PENDING_DEVELOPMENT.md) — current priorities.
5. [`architecture_direction_2026-07-14.md`](architecture_direction_2026-07-14.md)
   and [`ui_state_traversal_2026-07-14.md`](ui_state_traversal_2026-07-14.md) when
   the task touches runtime architecture or UI coverage.
6. The current working-tree status/diff and recent commits.

## Mandatory fresh inspection

Do not assume that the process, PID, ADB port, control state, battle state,
pause state, current screen, wave, strategy, or last handoff observation is
still current. Before live work, inspect:

```bash
git status --short
git log -6 --oneline
sed -n '1,160p' logs/automation_ctl.json
sed -n '1,160p' logs/automation-localhost_5555.lock
tail -120 logs/actions.log
timeout 8s adb -s localhost:5555 get-state
```

Confirm the lock PID against the host process table and take a read-only current
screenshot when screen state matters. Adjust the target if the lock, process,
or handoff identifies a different ADB port.

If a natural Game Over appears during inspection, preserve it. Pause or stop
automation when needed to prevent an unintended terminal action, then pivot to
the Game Over work that is already in scope. Never use Surrender to create a
test boundary.

## What belongs in a handoff

A handoff should be short and should contain only facts not already maintained
by the documents above:

- the latest deliberate commit and validation results;
- tracked and user-owned untracked working-tree state;
- freshly verified control state, lock/PID, ADB target, screen/battle state,
  active strategy, and launch flags;
- incomplete work that is not yet represented in the backlog;
- retained evidence paths that must not be discarded.

Volatile state belongs in the handoff, not as a permanent claim in this file.
If the next thread discovers that the handoff is stale, the fresh inspection
wins and the discrepancy should be recorded only if it reveals a recurring
issue.

## Minimal prompt for the next thread

```text
Continue TheTower development in /home/brianm/dev/python/TheTower.

Read and follow AGENTS.md and docs/new_thread.md first. Then inspect the current
working tree, recent commits, runtime control/lock/PID, ADB connection, current
screen, and recent actions.log before taking any live action.

The remainder of this handoff contains only freshly observed volatile state and
the current task focus.
```
