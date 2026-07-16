# Pending Development

This is the canonical index for actionable TheTower work. Detailed tasks are
kept in small domain backlogs so a development thread can load only the area it
is changing. Search this index first, then read the linked domain file.

## Current priorities

1. Resolve the active runtime and capture validation gates in
   [`docs/backlog/runtime-and-validation.md`](docs/backlog/runtime-and-validation.md#current-validation-gates).
2. Continue the GC initialization work, beginning with module evidence and
   configuration, in
   [`docs/backlog/runtime-and-validation.md`](docs/backlog/runtime-and-validation.md#gc-run-initialization).
3. Continue UI-state coverage and recovery work in
   [`docs/backlog/state-and-detection.md`](docs/backlog/state-and-detection.md#state-coverage-and-recovery).

## Active domain backlogs

- [`docs/backlog/runtime-and-validation.md`](docs/backlog/runtime-and-validation.md)
  — current validation gates, GC initialization, runtime control, and operator
  controls.
- [`docs/backlog/state-and-detection.md`](docs/backlog/state-and-detection.md)
  — state coverage, detection policy, frame capture, action authority, and
  related validation.
- [`docs/backlog/handlers.md`](docs/backlog/handlers.md) — handler dispatch and
  remaining Game Over hardening.
- [`docs/backlog/tooling-and-maintenance.md`](docs/backlog/tooling-and-maintenance.md)
  — developer tooling, schema validation, repository maintenance, and process
  improvements.

## History and maintenance

- Follow
  [`docs/documentation_maintenance.md`](docs/documentation_maintenance.md)
  when adding, completing, moving, or archiving work.
- Move completed outcomes to
  [`docs/modules/completed_tasks_log.md`](docs/modules/completed_tasks_log.md),
  retaining commit and validation evidence where applicable.
- Move fixed anomalies from [`docs/observed_issues.md`](docs/observed_issues.md)
  to the resolved issue archive after recording the fix and regression.
- The complete pre-split backlog is preserved in
  [`docs/backlog/history/PENDING_DEVELOPMENT_2026-07-16.md`](docs/backlog/history/PENDING_DEVELOPMENT_2026-07-16.md).
- The retired `docs/modules/ROADMAP.md` and
  `docs/modules/roadmap_priorities.md` remain historical snapshots.
