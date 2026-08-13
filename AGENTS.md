# TheTower Agent Instructions

Before doing any work, read [`docs/new_thread.md`](docs/new_thread.md) and use
its smallest applicable startup path. If scope expands, complete the newly
applicable path before acting or making a current-state claim.

## Universal safeguards

TheTower is a trusted-single-user hobby project. Protect against mistakes,
concurrent work, stale state, partial writes, and recoverable failures—not a
malicious same-account user. Do not add authentication, cryptographic audit,
hostile-worktree defenses, or similar security machinery unless the operator
changes this model.

- Production's `.venv` is production-owned. Development worktrees must use the
  supported bootstrap in `docs/new_thread.md` and must never execute, copy,
  link, or mutate production's environment. Run project Python and tests only
  through the selected `.venv/bin/python`.
- `main` is production and implementation belongs on temporary feature
  branches. Use temporary integration only when several reviewed feature tips
  must ship together. A promotion owner follows the
  [production procedure](docs/operations/production_promotion.md) through exact
  fast-forward, `origin/main` publication, and default retirement of clean
  integrated temporary work. Documentation-only coordinators have standing
  promotion ownership; every other `main` update requires the operator or an
  explicitly assigned promotion owner.
- The operator-confirmed save-mapping fast lane documented in
  [development isolation](docs/architecture/development_isolation.md) is the
  only application-owned feature-branch exception. It may stage one allowlisted
  child of current `main`, but never moves `main` or grants a general bypass.
- Treat unrelated tracked and untracked changes as another participant's work.
  Do not overwrite, delete, stage, or incorporate them. Recheck status and each
  target diff immediately before editing, staging, or committing; reconcile a
  changed target, and stop only for overlapping or unclear ownership.
- Before adding a function, class, module, command, configuration/schema path,
  or workflow, search the relevant source, configuration, callers, and tests.
  Reuse or extend the existing owner when its contract and boundary fit.
- Repair dependency and authority boundaries instead of bypassing them with
  unguarded input, seeded completion state, or one-off aliases.
- For modified input workflows, follow the
  [action-log contract](docs/action_log_contract.md): one `ACTION`/`RESULT`
  pair, individual `INPUT` with diagnostic detail, and `WARN` only for
  persistent operator-relevant degradation.
- Keep `YamlStrategy` and its evaluator generic; express variation in compact
  source configuration and generated plans, not strategy-name conditionals.

## Live safeguards

- Complete [`docs/live_preflight.md`](docs/live_preflight.md) before process or
  device interaction, live validation or diagnosis, or any claim about
  volatile runtime state. A handoff or old screenshot is not current evidence.
- A sandbox-negative PID, process, systemd, socket, or ADB result is not proof
  of host absence. Use the relevant
  [sandbox boundary](docs/sandbox_boundaries.md) and host-backed evidence.
- Use bounded, exact-target ADB commands. Never use sandbox `adb connect`,
  `start-server`, or `kill-server` as an availability probe.
- Never Surrender a pre-existing or operator-owned battle for a test boundary.
  Exceptional owned-test and runtime-repair authority exists only under
  [`docs/live_action_authority.md`](docs/live_action_authority.md); ambiguity
  fails closed.
- Pause blocks every strategy and handler action while capture and detection
  may continue. Reconcile an agent-owned work Pause when finished, restoring
  `RUNNING` only after fresh evidence proves the Pause is still agent-owned.

## Outcome coordination

Follow the disposable-coordinator and durable-state rules in
`docs/new_thread.md`. One writer owns a checkout; parallel writers require
separate feature worktrees.

## Documentation routing

Use [`docs/documentation_maintenance.md`](docs/documentation_maintenance.md)
for guidance or lifecycle changes. Backlogs own work, `docs/observed_issues.md`
routes active issues, and a top-level-chat transfer uses the delta-only
[`handoff template`](docs/handoff_template.md).
