# Documentation Maintenance

Read this file only when changing tracked project guidance, moving information
between active and historical documents, or adding a new documentation
category. Do not rely on inference to choose a destination: use the ownership
and transition rules below.

## Canonical ownership

| Information | Canonical location |
| --- | --- |
| Always-on repository safety and development rules | `AGENTS.md` |
| Task-based startup and live-inspection routing | `docs/new_thread.md` |
| Handoff content and freshness format | `docs/handoff_template.md` |
| Sandbox-versus-host evidence and execution fallbacks | `docs/sandbox_boundaries.md` |
| Stable live process, control, ADB, and action procedure | `docs/runtime_operations.md` |
| Current runtime architecture and layer boundaries | `docs/architecture/runtime.md` |
| Current priorities and domain routing | `PENDING_DEVELOPMENT.md` |
| Detailed active work | The relevant `docs/backlog/*.md` domain file |
| Open runtime or tooling anomalies | `docs/observed_issues.md` |
| Resolved anomaly history | The applicable archive under `docs/issues/` |
| Completed implementation outcomes | `docs/modules/completed_tasks_log.md` |
| Dated investigations, superseded plans, and preserved evidence | A clearly labeled history file |

Keep `AGENTS.md` compact because Codex loads it automatically. Put detailed,
task-specific procedures in a linked document and route to that document only
when its condition applies.

## Lifecycle transitions

### Actionable work

- Add a new task to exactly one domain backlog. Update the root backlog only if
  the domain index or current priority order changes.
- Keep established evidence with an active task only when it constrains the
  remaining implementation. Move general completion narrative out of active
  files.
- When work finishes, remove the active item and record its outcome, commit,
  and validation in the completed-task log. If it fixed an observed issue,
  complete the issue transition as well.

### Issues

- Record a new anomaly in `docs/observed_issues.md` with date, symptom,
  evidence, safety response, and current status. Put repair work in the
  appropriate backlog rather than using the issue ledger as a second backlog.
- When durable documentation relies on generated evidence under a runtime
  retention root, promote it to a canonical regression fixture or add a narrow
  repository-relative entry to `config/protected_artifacts.txt` in the same
  change. A documentation link alone does not exempt a file from cleanup.
- When fixed, retain the original symptom, add cause, resolution, fixing
  commit, and regression location, then move the entire entry to the archive
  for the resolution year. Add that archive to `docs/issues/README.md` when
  creating a new yearly file.

### Architecture and operations

- Update `docs/architecture/runtime.md` when the current architectural contract
  changes. Preserve substantial investigation or superseded reasoning in a
  dated history file and link it from the current document when provenance is
  useful.
- Treat history files and dated backlog snapshots as immutable evidence. Only
  correct factual transcription errors, repair links, or add an archive banner;
  never add current policy or new tasks to them.
- Update `docs/runtime_operations.md` when a stable live procedure or authority
  boundary changes. Record the originating anomaly separately when recurrence
  evidence remains useful.

### Handoffs

- Update `docs/handoff_template.md` when handoff fields or freshness rules
  change. Handoffs themselves contain only task-specific facts not maintained
  by the canonical documents above.

## Consistency and validation

For every documentation change:

1. Recheck `git status` and staged/unstaged target-file diffs immediately before
   editing, staging, or committing.
2. Search the repository for every moved or renamed path and update current
   inbound references. Historical snapshots may retain old path text when it is
   part of the preserved record, but their navigation banners must point to the
   current location.
3. Verify every changed local Markdown link and heading anchor.
4. Confirm that all active tasks and open issues remain represented before
   archiving or deleting material.
5. Confirm that generated evidence used by changed durable documentation is
   either outside the runtime cleanup boundary or represented narrowly in
   `config/protected_artifacts.txt`.
6. Run `git diff --check` and any repository tests needed when documentation is
   generated, executable, schema-defining, or coupled to behavior.
7. Do not copy volatile runtime facts into durable documentation. Runtime state
   belongs only in a freshly inspected handoff or current diagnostic report.

Prefer one canonical statement plus links over duplicated guidance. If two
documents appear to own the same fact, repair the ownership boundary as part of
the change.
