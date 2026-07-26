# Starting a New Development Thread

This is the canonical entrypoint for every new TheTower development thread.
Codex automatically loads the applicable `AGENTS.md` before its first turn, so
do not reopen that file merely to begin a task. Reread it only when it may have
changed after the session started or when the task edits it. Handoffs should
reference this document instead of reproducing stable operational guidance.

## Shared-workspace changes

The automatically loaded `AGENTS.md` owns the non-negotiable concurrency and
file-ownership rules. For any repository change:

- Treat status and diff output as a snapshot, not a stable ownership record.
- Recheck `git status` and the staged and unstaged diff for each target file
  immediately before editing, staging, or committing. Reread a target that
  changed since it was last inspected and preserve compatible concurrent edits.
- Continue around unrelated changes. Stop and coordinate only when edits overlap
  or ownership is unclear.

## Lightweight read-only questions

A simple explanatory question may use this reduced startup path when its answer
can be established from repository source or documentation and the task requires
no file changes, tests, runtime diagnosis, claims about volatile state, or
process/device interaction:

1. Inspect only the directly relevant source, documentation, dependencies, and
   callers needed to answer accurately.
2. Skip working-tree, backlog, issue-ledger, and runtime inspection unless they
   are directly relevant to the question.
3. Skip the repository-change and live-runtime checklists below.

If the task expands beyond those limits, complete the full applicable startup
checklist before making changes or relying on runtime state.

## Repository or documentation changes

Before editing:

1. Inspect the working tree and recent commits.
2. Inspect staged and unstaged changes for every target file, then recheck them
   immediately before editing, staging, or committing.
3. Read the directly relevant source, callers, tests, documentation, and only
   the task-specific references identified below.
4. Preserve unrelated tracked and untracked work.
5. After a coherent behavior is validated, review and selectively stage only
   its owned files or hunks, then commit it before beginning the next coherent
   task. Do not accumulate several verified changes merely because the shared
   worktree contains unrelated modifications; report a real ownership or
   validation blocker if one prevents the commit.

Run automated validation proportionate to the remaining uncertainty. Code-only
work does not require ADB access or inspection of a live automation process.
Complete the live-runtime path before any process/device interaction, live
validation, runtime diagnosis, or claim about volatile runtime state.

Canonical screenshots and retained live fixtures are sufficient when they
directly exercise the behavior under test and no current-state, transition,
timing, or device-integration property remains unresolved. Use live validation
when it would materially resolve repository-local uncertainty and is safe within
the task's authority. When useful live evidence is unavailable, unsafe, or not
authorized, record that validation as pending. Never describe behavior as
live-validated unless the applicable inspection and validation occurred.

## Task-specific references

Read these only when their condition applies:

- [`runtime_operations.md`](runtime_operations.md): read the relevant sections
  when changing runtime, control, process, or ADB behavior; read the complete
  runbook before process/device interaction, live validation, or runtime
  diagnosis.
- [`observed_issues.md`](observed_issues.md): read `Open` before live work or
  runtime diagnosis. Read matching entries in
  [`issues/`](issues/README.md) only when investigating a recurrence or
  historical operational anomaly.
- [`../PENDING_DEVELOPMENT.md`](../PENDING_DEVELOPMENT.md): use the active index
  to select the one domain backlog relevant to planned work. Do not load other
  domains or the historical snapshot.
- [`architecture/runtime.md`](architecture/runtime.md): read the relevant
  decision sections when changing runtime architecture. Consult the linked
  dated history only when its evidence or decision provenance matters.
- [`game_strategy.md`](game_strategy.md): read when interpreting battle results
  or changing a Farm Tier, build archetype, perk priority, Damage Slider,
  Spotlight economy, Target Priority, Dissonant Boost, Heat, or Overheat
  assumption.
- [`ui_state_traversal_2026-07-14.md`](ui_state_traversal_2026-07-14.md): read
  the relevant sections when changing UI-state coverage or traversal.
- [`../windows/TheTower.ControlSurface/README.md`](../windows/TheTower.ControlSurface/README.md):
  read the publish section when changing the native WPF client. Linux
  cross-publishing is supported; from the repository root, validate with
  `windows/TheTower.ControlSurface/publish-linux.sh` instead of treating a
  direct `dotnet build` failure as proof that the Windows client cannot be
  built on Linux.

## Live runtime or device work

This path is mandatory before process/device interaction, live validation,
runtime diagnosis, or claims about volatile runtime state. First read the
complete runtime runbook and the `Open` section of the issue ledger. Then
perform the fresh inspection below. This is not a prerequisite for code-only
edits whose validation is entirely repository-local.

Do not assume that the process, PID, ADB port, control state, battle state,
pause state, current screen, wave, strategy, or last handoff observation is
still current. Before live work, inspect:

```bash
git status --short
git log -6 --oneline
.venv/bin/python tools/automation_ctl.py status
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
the Game Over work that is already in scope. Never Surrender a pre-existing or
operator-owned battle merely to create a development test boundary. A bounded
agent-owned test battle may be Surrendered only under the ownership and
authorization rule in `AGENTS.md`; an implemented runtime configuration-gate
recovery has its own narrower authority there.

## Preparing a handoff

Only when preparing or reviewing a handoff, read and follow
[`handoff_template.md`](handoff_template.md). It defines the ready-to-use
minimal format and the conditions under which repository, validation, or fresh
runtime state belongs in a handoff.
