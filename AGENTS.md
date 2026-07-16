# TheTower Agent Instructions

Before doing any work, read [`docs/new_thread.md`](docs/new_thread.md) and
follow the applicable startup path defined there. A simple read-only source or
documentation question may use its lightweight path. If the task requires code
or documentation changes, complete the applicable
repository/development checklist and run proportionate automated validation.
Code-only work does not require live ADB access unless it includes process or
device interaction, live validation, runtime diagnosis, or claims about
volatile runtime state. Choose validation proportionate to the remaining
uncertainty: retained live fixtures may be sufficient, while live interaction
should be used when it materially resolves current-state, transition, timing,
or device-integration uncertainty and is safe within the task's authority.
Neither require nor defer live validation merely by default. If the task's
scope expands, complete the newly applicable checklist before proceeding. Do
not rely on a handoff's runtime facts until they have been verified against the
current process and device.

## Non-negotiable rules

- Run project Python through `.venv/bin/python`, including tests.
- Never Surrender a pre-existing, operator-owned, or automation farming battle.
  A battle deliberately started by the agent for a bounded test may be
  Surrendered only when the task author explicitly authorizes it and the agent
  has recorded that test-run ownership before starting it. Leaving through the
  verified Exit Battle → Go Home route is allowed only when the task authorizes
  it and the active run remains resumable.
- Before any live action, inspect the control file, lock/PID, ADB target,
  current screen, and recent `logs/actions.log` entries.
- Capture and detection may continue while paused, but pause blocks every
  strategy and handler action.
- Prefer repairing broken dependencies and authority boundaries over bypassing
  them with unguarded taps, seeded completion variables, or one-off aliases.
- Treat existing untracked files as user-owned unless their ownership and scope
  have been deliberately established. Do not delete, overwrite, stage, or
  silently incorporate them.
- Expect the operator and other agent threads to work in this shared workspace
  concurrently. Treat tracked or untracked changes outside the current task as
  owned by that parallel work; do not revert, overwrite, stage, or silently
  incorporate them. Recheck status and the target-file diff immediately before
  editing, staging, or committing. If a target changed since inspection, reread
  and reconcile it; unrelated changes are not a blocker, but overlapping or
  unclear ownership requires coordination with the user.
- Keep `YamlStrategy` and the runtime evaluator generic. Prefer compact source
  configuration plus explicit generated plans over strategy-name conditionals
  or duplicated expanded YAML.

## Documentation discipline

- Record newly observed runtime/tooling anomalies in
  [`docs/observed_issues.md`](docs/observed_issues.md), including evidence and
  whether they are confirmed, unresolved, or resolved.
- When an issue is fixed, add the fixing commit and regression-test location to
  its entry. Do not erase the original symptom; recurrence history is useful.
- Keep actionable work in `PENDING_DEVELOPMENT.md`. The issue ledger is evidence
  and history, not a second backlog.
- Every handoff must explicitly direct the next thread to `AGENTS.md` and
  `docs/new_thread.md`, then report only freshly inspected volatile state.
