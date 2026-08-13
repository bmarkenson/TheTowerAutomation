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
  branches. A clean feature tip is the normal promotion candidate; use a
  temporary integration branch only when several reviewed feature tips must
  ship together. Promotion ownership includes publishing the exact successful
  `main` tip to `origin/main` by an explicit fast-forward refspec unless the
  operator withholds remote publication; it never includes tags or temporary
  refs. A documentation-only outcome gives its coordinator standing ownership
  to promote and publish the exact validated candidate and retire its clean
  integrated branch/worktree unless the operator withholds that closure;
  follow
  [documentation maintenance](docs/documentation_maintenance.md) and the
  [production procedure](docs/operations/production_promotion.md). Every other
  `main` update remains operator or explicitly assigned promotion-owner work.
- The operator-confirmed save-mapping fast lane documented in
  [development isolation](docs/architecture/development_isolation.md) is the
  sole application-owned exception: it may create one allowlisted child of
  current `main` under the private save-mapping staging ref. It never moves
  `main` or changes the production index or worktree. It grants no agent,
  feature, or client general permission to bypass feature branches or promote
  production.
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
- An explicit operator instruction may authorize one bounded on-demand passive
  stream after live preflight. Follow
  [`docs/operations/passive_stream.md`](docs/operations/passive_stream.md): use
  the exact target, disable control, keep cleanup attached, and grant no input
  or ADB connection-management authority.
- Never Surrender a pre-existing or operator-owned battle for a test boundary.
  Exceptional owned-test and runtime-repair authority exists only under
  [`docs/live_action_authority.md`](docs/live_action_authority.md); ambiguity
  fails closed.
- Pause blocks every strategy and handler action while capture and detection
  may continue. Reconcile an agent-owned work Pause when finished, restoring
  `RUNNING` only after fresh evidence proves the Pause is still agent-owned.

## Outcome coordination

Use one disposable outcome coordinator per coherent result. Delegate only when
at least two substantial independent subtasks can proceed in parallel, to no
more than three direct subagents; descendants require explicit operator
authorization. In a shared checkout the coordinator is the sole writer.
Parallel writers require separate feature worktrees and explicit ownership.
Repository artifacts, not chats, hold durable state; use the checkpoint,
evidence-summary, handoff, and closure procedure in `docs/new_thread.md`.

## Documentation routing

Use [`docs/documentation_maintenance.md`](docs/documentation_maintenance.md)
for tracked guidance or lifecycle changes. `PENDING_DEVELOPMENT.md` and its
domain backlogs own actionable work; `docs/observed_issues.md` is a compact
issue router, with evidence and history under `docs/issues/`. A handoff is
needed only when responsibility moves to another top-level chat and must follow
[`docs/handoff_template.md`](docs/handoff_template.md) with freshly inspected
volatile state only.
