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
- `main` is production; implementation uses temporary feature branches and
  temporary integration only for intentional combined outcomes. The
  [outcome coordinator](docs/new_thread.md#outcome-coordination) owns promotion
  and closure by default under the
  [production procedure](docs/operations/production_promotion.md). Its private
  ref serializes the mutable transaction. Other threads may continue candidate
  work and validation but must not mutate production, services, artifacts,
  `origin/main`, or its cleanup topology. Contenders wait, refresh, retest, and
  retry; contention or another unfinished closure guard is not completion.
- The reviewed or machine-verified save-mapping fast lane documented in
  [development isolation](docs/architecture/development_isolation.md) is the
  only application-owned repository-change exception. It may create and
  consume one allowlisted canonical-mapping child of clean current `main`: it
  uses the global promotion-owner ref, an exact rollback tag, fast-forward-only
  local promotion, non-forcing `origin/main` publication, and durable retry.
  It never changes another path, publishes a larger enclosing outcome, moves
  `main` backward, controls services or the device, or grants a general bypass.
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
  device interaction, live validation, diagnosis that depends on current or
  changing runtime state, or any claim about volatile runtime state. Historical
  production artifacts used only as retained evidence do not trigger live
  preflight and do not prove current state.
- A sandbox-negative PID, process, systemd, socket, or ADB result is not proof
  of host absence. Use the relevant
  [sandbox boundary](docs/sandbox_boundaries.md) and host-backed evidence.
- Use bounded, exact-target ADB commands. Never use sandbox `adb connect`,
  `start-server`, or `kill-server` as an availability probe.
- Never Surrender a pre-existing or operator-owned battle for a test boundary.
  Exceptional owned-test and runtime-repair authority exists only under
  [`docs/live_action_authority.md`](docs/live_action_authority.md); ambiguity
  fails closed.
- Pause blocks every ordinary strategy, handler, recovery, and game-navigation
  action while capture, detection, save analysis, and record persistence may
  continue. One durable per-Pause policy may additionally authorize one paired
  Android Home/launcher-restore save serialization at each freshly proven
  `GAME_OVER` or `TOURNAMENT_RESULTS` boundary, and only when the initial
  stable terminal save still reports an active round. It never applies during
  a battle, never authorizes terminal UI input, and a strict Pause disables it.
  Before an agent changes a live `RUNNING` runtime to `PAUSED`
  or `STOPPED` for its work, retain the prior control request and owner. That
  work is not complete until the replacement/current runtime is restored to
  `RUNNING` after fresh evidence proves the control boundary is still
  agent-owned and no newer operator, manual-control, safety, target, or screen
  condition forbids restoration. An agent-created Pause remains agent-owned
  across a later Stop, process replacement, or nested piece of the same work;
  none of those boundaries rebases the restoration posture to `PAUSED`. Never
  restore over a pre-existing or newer operator Pause; leave an explicit
  blocked handoff instead of silently leaving an agent-owned Pause behind.

## Outcome coordination

Follow the disposable-coordinator and durable-state rules in
`docs/new_thread.md`. One writer owns a checkout; parallel writers require
separate feature worktrees.

## Documentation routing

Use [`docs/documentation_maintenance.md`](docs/documentation_maintenance.md)
for guidance or lifecycle changes. Backlogs own work, `docs/observed_issues.md`
routes active issues, and a top-level-chat transfer uses the delta-only
[`handoff template`](docs/handoff_template.md).
