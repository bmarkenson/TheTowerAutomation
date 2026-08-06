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

## Project trust model

TheTower is a trusted-single-user hobby project. The operator and Codex worker
threads are cooperative participants running under the same account. Design
safeguards for mistakes, concurrent work, stale runtime state, partial writes,
and recoverable failures—not for malicious same-user behavior or data secrecy.
Production screenshots, logs, and other runtime artifacts may be read or copied
for development when the normal ownership and live-action rules are followed.
Do not add authentication protocols, secret-token machinery, cryptographic
audit, hostile-worktree defenses, or similar security complexity unless the
operator explicitly changes this threat model. Locks, leases, and ordinary
permissions in this repository are coordination tools, not security boundaries.

## Non-negotiable rules

- Production's `.venv` is production-owned. A development worktree must use
  the supported bootstrap route in [`docs/new_thread.md`](docs/new_thread.md)
  when its ignored `.venv` is absent or mismatched; it must never execute,
  copy, symlink, or mutate production's environment.
- `main` is production, `develop` is integration, and workers commit only to
  feature branches. Only the master updates `develop` or promotes to `main`,
  following the
  [production promotion procedure](docs/runtime_operations.md#production-promotion-and-rollback).
- Once the checkout's supported `.venv` is selected, run all project Python
  through `.venv/bin/python`, including tests.
- Follow [`docs/sandbox_boundaries.md`](docs/sandbox_boundaries.md) for host
  process, PID-lock, user-systemd, ADB, local-socket, and long-lived-process
  checks. Sandbox `ps`, `/proc`, `pgrep`, `kill -0`, or `systemctl --user`
  failures cannot prove that a host process is absent or that a lock is stale;
  use host-backed API or approved-host process evidence plus the OS lock.
- Run ADB reads as bounded, exact-target commands. A normal sandbox may be used
  for `get-state` or capture only when the current session explicitly provides
  working project loopback access. Never use sandbox `adb connect`,
  `start-server`, or `kill-server` as an availability probe; connection
  management and any retry after an invocation-environment failure belong on
  the approved host path. Such a failure is not evidence that the host ADB
  server, tunnel, emulator, or target is unavailable.
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
- A worker directly delegated by an active master coordination thread reports
  its owned commits, validation, and remaining uncertainty back to that master.
  It does not choose the next task, draft a prompt for another worker, or tell
  the operator to move between threads unless the master explicitly requests a
  formal handoff.
- When work really is being transferred to an independent thread, the handoff
  must follow `docs/handoff_template.md`, direct that thread to follow the
  automatically loaded `AGENTS.md` and read `docs/new_thread.md`, and report
  only freshly inspected volatile state.
