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
- The trusted project permission profile enables loopback access to the
  established host ADB server. Choose the ADB execution path from the current
  session's declared permissions: use the normal workspace sandbox when
  network is enabled; when the session explicitly declares network restricted,
  skip the known-failing isolated probe and use approved host execution. If the
  capability is not stated, try one bounded sandbox command and retry it
  immediately through approved host execution on an environment-level network
  failure. Treat that failure as an invocation-environment failure, not proof
  that the device is unavailable, and do not interrupt the workflow merely to
  narrate the fallback.
- Never Surrender a pre-existing or operator-owned battle merely to create a
  development test boundary. A battle deliberately started by the agent for a
  bounded test may be Surrendered only when the task author explicitly
  authorizes it and the agent records that test-run ownership before starting
  it. Runtime automation may Surrender only through an implemented,
  profile-declared recovery that authoritatively detects a failed Home-only
  configuration gate and owns the complete Home repair, restart, and
  revalidation sequence. The only validation-only exception is one ordinary
  `NEW_BATTLE` that a profile-declared exclusive-validation receipt claims
  atomically before the verified Home tap. Only that same live runtime and ADB
  target may Surrender it, only while fresh evidence still excludes Tournament
  identity, and only as part of its bounded return-to-Home cleanup. A crash,
  owner mismatch, resumed battle, or ambiguous identity fails closed without
  Surrender. Leaving through the verified Exit Battle → Go Home route is
  allowed only when the task authorizes it and the active run remains
  resumable.
- Before any live action, inspect the control file, lock/PID, ADB target,
  current screen, and recent `logs/actions.log` entries.
- Capture and detection may continue while paused, but pause blocks every
  strategy and handler action.
- If an agent sets a running battle to `PAUSED` to complete bounded work,
  restore `RUNNING` when that work is complete after rechecking live state and
  confirming that the pause is still agent-owned. Do not leave an agent-owned
  work pause behind as a handoff state.
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
- Before adding a helper or utility, search the existing code and callers for
  identical or closely related behavior. Reuse or extend an existing function
  when doing so preserves clear ownership and semantics; create a new helper
  only when adapting existing code would distort its contract or architectural
  boundary.
- Write operator-facing logs for comprehension, not just internal mechanics:
  state what automation is doing and why. Before a guarded or multi-step input
  workflow, emit one `ACTION` through `log_action_intent(...)` before its first
  input and one terminal `RESULT`. Record individual taps and swipes as `INPUT`
  with coordinates, matches, and retries in paired `DEBUG` detail. Reserve
  `WARN` for persistent, operator-relevant degradation rather than expected
  negative searches or ordinary retries. During the staged logging migration,
  untouched legacy workflows and input emitters may not yet conform; new or
  modified logging must follow the target contract rather than extending the
  legacy pattern. Follow the action-log contract in
  [`docs/runtime_operations.md`](docs/runtime_operations.md#action-log-contract).
- Keep `YamlStrategy` and the runtime evaluator generic. Prefer compact source
  configuration plus explicit generated plans over strategy-name conditionals
  or duplicated expanded YAML.

## Documentation discipline

- Before changing documentation structure or moving information between active
  and historical files, read and follow
  [`docs/documentation_maintenance.md`](docs/documentation_maintenance.md).
- Record newly observed runtime/tooling anomalies in
  [`docs/observed_issues.md`](docs/observed_issues.md), including evidence and
  whether they are confirmed, unresolved, or resolved.
- When an issue is fixed, add the fixing commit and regression-test location,
  then move the complete entry to the applicable archive under `docs/issues/`.
  Do not erase the original symptom; recurrence history is useful.
- Keep actionable work in `PENDING_DEVELOPMENT.md`. The issue ledger is evidence
  and history, not a second backlog.
- Every handoff must follow `docs/handoff_template.md`, direct the next thread
  to follow the automatically loaded `AGENTS.md` and read
  `docs/new_thread.md`, and report only freshly inspected volatile state.
