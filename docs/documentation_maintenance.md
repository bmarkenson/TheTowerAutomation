# Documentation Maintenance

Read this file only when changing tracked guidance, moving information between
active and historical documents, or adding a documentation category. Keep one
canonical statement and route other readers to it.

## Canonical ownership

| Information | Canonical owner |
| --- | --- |
| Always-on repository safeguards | `AGENTS.md` |
| Startup paths and outcome coordination | `docs/new_thread.md` |
| Pre-action live inspection | `docs/live_preflight.md` |
| Input, test-battle, Surrender, Exit Battle, and Pause authority | `docs/live_action_authority.md` |
| Operator-facing action-log semantics | `docs/action_log_contract.md` |
| Live-procedure selection | `docs/runtime_operations.md` |
| One live operating workflow | The matching `docs/operations/*.md` chapter |
| Sandbox-versus-host evidence | `docs/sandbox_boundaries.md` |
| Current runtime contracts and boundaries | `docs/architecture/*.md` |
| Handoff fields and freshness | `docs/handoff_template.md` |
| Priorities and domain routing | `PENDING_DEVELOPMENT.md` |
| Detailed active work | One matching `docs/backlog/*.md` file |
| Active issue classification and routing | `docs/observed_issues.md` |
| Issue dossiers, history, and durable evidence | `docs/issues/` as routed by its `README.md` |
| Completed implementation outcomes | `docs/modules/completed_tasks_log.md` |
| Superseded reasoning or dated investigations | A clearly labeled history file |

Keep automatically loaded `AGENTS.md` compact. Put conditional detail in its
owner and expose one link with an exact load condition.

## Lifecycle rules

### Work and completion

- Put an actionable task in exactly one domain backlog. Change the root backlog
  only when domain routing or priority changes.
- Keep evidence with an active task only while it constrains remaining work.
- At completion, remove the active item and record the outcome, commit, and
  validation in the completed-task log. Complete any related issue transition.
  Completed narrative is on-demand history, never active required reading.

### Issues and evidence

Follow [`issues/README.md`](issues/README.md) for stable IDs, active-index and
dossier fields, resolution and unconfirmed transitions, lifecycle splits, and
durable evidence. The issue index is a safety router, not a backlog or full
evidence ledger. Link actionable work to exactly one domain backlog.

When a cited generated artifact is subject to rolling cleanup, retain only the
needed rows, fields, units, query window, and read-only extraction method under
`docs/issues/evidence/`; prefer a regression fixture when tests need it. If an
artifact must remain under a runtime cleanup root, add its narrow path to
`config/protected_artifacts.txt` in the same change. A Markdown link alone does
not protect it.

### Architecture, operations, and history

- Put current contracts in architecture documents and current procedures in
  the matching operation chapter. `runtime_operations.md` remains a selector
  and compatibility router, not a second runbook.
- Preserve substantial investigation or superseded reasoning in dated history
  only when provenance remains useful. History and dated backlog snapshots are
  immutable except for factual transcription fixes, repaired links, and archive
  banners; never add current policy or new tasks to them.
- A handoff contains only task-specific facts absent from canonical documents.
  Change the template only when its fields or freshness contract must change.

## Validation

For each documentation change:

1. Recheck status and every target diff immediately before editing, staging,
   and committing; preserve unrelated work.
2. Search all current inbound references to a moved path or heading. Historical
   prose may retain old path text, but its navigation must reach the owner.
3. Verify every changed local Markdown target and anchor.
4. Account for every active task or open issue before deleting or archiving it.
5. Verify that changed durable evidence is tracked, fixture-owned, or narrowly
   protected as described above.
6. Run `git diff --check` plus proportionate tests for generated, executable,
   schema-defining, or behavior-coupled documentation.

Never put volatile runtime facts in durable guidance. They belong only in a
freshly inspected diagnostic report or conditional handoff section.
