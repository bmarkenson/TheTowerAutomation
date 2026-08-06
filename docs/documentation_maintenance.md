# Documentation Maintenance

Read this file only when changing tracked project guidance, moving information
between active and historical documents, or adding a new documentation
category. Do not rely on inference to choose a destination: use the ownership
and transition rules below.

## Canonical ownership

| Information | Canonical location |
| --- | --- |
| Always-on repository safety and development rules | `AGENTS.md` |
| Task-based startup, outcome coordination, and live-inspection routing | `docs/new_thread.md` |
| Handoff content and freshness format | `docs/handoff_template.md` |
| Sandbox-versus-host evidence and execution fallbacks | `docs/sandbox_boundaries.md` |
| Stable live process, control, ADB, and action procedure | `docs/runtime_operations.md` |
| Current runtime architecture and layer boundaries | `docs/architecture/runtime.md` |
| Current priorities and domain routing | `PENDING_DEVELOPMENT.md` |
| Detailed active work | The relevant `docs/backlog/*.md` domain file |
| Compact active issue classification and routing | `docs/observed_issues.md` |
| Full open issue evidence dossiers | `docs/issues/open-YYYY.md` |
| Resolved issue history | The applicable `docs/issues/resolved-YYYY.md` archive |
| Unconfirmed, non-actionable issue history | The applicable `docs/issues/unconfirmed-YYYY.md` file |
| Narrow durable issue evidence | `docs/issues/evidence/` |
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

- Assign every issue a stable ID. Put its compact lifecycle status, one-sentence
  symptom or hazard, required safety behavior, dossier-load condition, next
  evidence requirement, full-dossier link, and backlog link in
  `docs/observed_issues.md`. Keep the index as routing rather than a second
  evidence ledger or backlog.
- Put the complete dated observation, evidence, safety response, analysis,
  implementation status, recurrences, commits, regression coverage, and
  unresolved requirements in the applicable `docs/issues/open-YYYY.md`
  dossier. Link actionable work to exactly one owning domain backlog.
- Move an unreproduced, non-actionable historical report to the applicable
  `docs/issues/unconfirmed-YYYY.md` file without calling it resolved. Preserve
  the complete report, negative and later-success evidence, tests, and the
  exact evidence required on recurrence; remove it from active routing until a
  matching recurrence makes it actionable.
- When fixed, retain the original symptom, add cause, resolution, fixing
  commit, and regression location, then move the complete dossier to the
  resolution year's archive and remove its active-index entry. When one report
  splits into resolved and open causes, give each lifecycle its own stable ID
  and cross-link both records.
- When durable issue documentation depends on generated evidence under rolling
  runtime retention, export only the cited rows/fields and their definitions,
  exact query windows, units, and read-only extraction method into
  `docs/issues/evidence/`. Do not copy the complete production artifact. A
  canonical regression fixture is preferable when behavior tests need the
  evidence; otherwise the narrow tracked extract is the durable source.
- For generated evidence that must remain under a runtime cleanup root rather
  than the tracked evidence directory, add the narrow repository-relative path
  to `config/protected_artifacts.txt` in the same change. A documentation link
  alone does not exempt a runtime artifact from cleanup.
- Add every new yearly dossier/history file and durable evidence category to
  `docs/issues/README.md`.

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
   tracked narrowly under `docs/issues/evidence/`, promoted to a canonical
   fixture, or—only when it must stay under a runtime cleanup root—represented
   narrowly in `config/protected_artifacts.txt`.
6. Run `git diff --check` and any repository tests needed when documentation is
   generated, executable, schema-defining, or coupled to behavior.
7. Do not copy volatile runtime facts into durable documentation. Runtime state
   belongs only in a freshly inspected handoff or current diagnostic report.

Prefer one canonical statement plus links over duplicated guidance. If two
documents appear to own the same fact, repair the ownership boundary as part of
the change.
