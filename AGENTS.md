# TheTower Agent Instructions

Before doing any work, read [`docs/new_thread.md`](docs/new_thread.md) and
follow its startup inspection checklist. Do not rely on a handoff's runtime
facts until they have been verified against the current process and device.

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
