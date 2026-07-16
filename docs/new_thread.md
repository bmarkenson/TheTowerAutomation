# Starting a New Development Thread

This is the canonical entrypoint for every new TheTower development thread.
Handoffs should reference this document instead of reproducing all stable
operational guidance from memory.

## Shared-workspace concurrency

The operator and multiple agent threads may modify this working tree at the same
time. A status or diff is a snapshot, and newly appearing changes are not by
themselves an anomaly or a reason to stop unrelated work.

- Treat every change not deliberately made for the current task as owned by the
  operator or another thread.
- Recheck `git status` and the staged and unstaged diff for each target file
  immediately before editing, staging, or committing. Reread a target that
  changed since it was last inspected and preserve compatible concurrent edits.
- Stage only explicitly owned paths or hunks. Never revert, overwrite, stage,
  commit, or silently incorporate parallel work.
- Continue around unrelated changes. If edits overlap or ownership is unclear,
  stop changing that file and coordinate with the user.
- In the handoff, report only changes owned by the current thread and identify
  parallel work separately when it materially affects the next task.

## Lightweight read-only questions

A simple explanatory question may use this reduced startup path when its answer
can be established from repository source or documentation and the task requires
no file changes, tests, runtime diagnosis, claims about volatile state, or
process/device interaction:

1. Read `AGENTS.md` and this file.
2. Inspect only the directly relevant source, documentation, dependencies, and
   callers needed to answer accurately.
3. Skip the remaining required reading, working-tree inspection, and runtime
   inspection below.

If the task expands beyond those limits, complete the full applicable startup
checklist before making changes or relying on runtime state.

## Code-only development

Code or documentation changes and automated tests do not qualify for the
lightweight path. Complete the applicable required reading below, inspect the
working tree and recent commits, preserve concurrent work, and run automated
validation proportionate to the change.

Code-only development does not by itself require ADB access or inspection of a
live automation process. Complete the mandatory runtime inspection before any
process/device interaction, live validation, runtime diagnosis, or claim about
volatile runtime state.

Choose validation proportionate to the remaining uncertainty. Canonical
screenshots and retained live fixtures are sufficient when they directly
exercise the behavior under test and no current-state, transition, timing, or
device-integration property remains unresolved. Use live validation when it
would materially resolve uncertainty that repository-local evidence cannot and
the interaction is safe within the task's authority. Neither require nor defer
live validation merely by default.

When live validation would add material evidence but the relevant state is
unavailable, the interaction would be unsafe or disruptive, or the task does
not authorize it, record that validation as pending. Do not describe behavior
as live-validated unless the applicable runtime inspection and validation were
actually completed.

## Required reading order

For work outside the lightweight read-only path:

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

This section is mandatory before process/device interaction, live validation,
runtime diagnosis, or claims about volatile runtime state. It is not a
prerequisite for code-only edits whose validation is entirely repository-local.

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
