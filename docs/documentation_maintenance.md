# Documentation Maintenance

Read this only when changing tracked guidance, moving active/history content,
or adding a documentation category. Keep one canonical statement and links.

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
| Player-save mapping, evidence matrix, and fallback contract | `docs/architecture/player_save.md` |
| Clickmap and state-definition schema | `docs/reference/ui_detection_schema.md` |
| YAML strategy plan shape and authoring route | `docs/reference/yaml_strategy.md` |
| Headless template creation and review | `docs/tooling/template_workflow.md` |
| Handoff fields and freshness | `docs/handoff_template.md` |
| Priorities and domain routing | `PENDING_DEVELOPMENT.md` |
| Detailed active work | One matching `docs/backlog/*.md` file |
| Active issue classification and routing | `docs/observed_issues.md` |
| Issue dossiers, history, and durable evidence | `docs/issues/` as routed by its `README.md` |
| Concise completed-outcome index and non-issue completion records | `docs/modules/completed_tasks_log.md` |
| Superseded reasoning or dated investigations | A clearly labeled history file |

Do not track or routinely load API summaries. Generate an explicitly requested
one from source into `/tmp` with its revision; discover capabilities in
source, configuration, callers, and tests.

## Adding documentation

Before adding a rule, section, or tracked file:

- Search the ownership table and current guidance; extend the existing owner
  unless the subject has a distinct contract or lifecycle.
- Create a file only for an independently useful load or lifecycle boundary,
  not for size or temporary convenience alone.
- Name its audience and exact load condition, then link it from the nearest
  conditional router. Never add default reading merely for discoverability.
- Keep `AGENTS.md` and `new_thread.md` universally necessary, measure any
  default-path increase, and leave task detail on demand.
- Classify the content as current guidance, active work, issue evidence,
  completion, or history, and define its transition when no longer current.

Keep one canonical statement and link to it elsewhere. Add an ownership-table
row only for a genuinely new information class.

## Lifecycle rules

### Work and completion

- Put an actionable task in exactly one domain backlog. Change the root backlog
  only when domain routing or priority changes.
- Keep active-task evidence only while it constrains remaining work.
- At completion, remove the active item and create one durable outcome record.
  For non-issue work, the completed-task log owns its concise outcome, commits,
  and validation. For an issue fix, the resolved dossier owns the detail; add
  only a concise completed-task link and complete the issue transition.
- A commit that adds or corrects the completion record belongs to the outcome
  it records and needs no recursive completion entry. The production
  procedure's narrow
  [completion-record exception](operations/production_promotion.md#completion-record-exception)
  covers an otherwise final code candidate followed only by that record.

Completed narrative is on-demand history, never active required reading.

### Automatic documentation closure

An outcome is documentation-only when its aggregate candidate changes tracked
guidance, planning, issue, completion, or history artifacts but no source,
tests, configuration, generated output, runtime-read asset, dependency or unit
input, or native-package input. Behavior-coupled documentation still receives
its affected validation; running a test does not change the candidate class.

Unless the operator requests a draft, review-only result, retained branch, or
no promotion, the documentation coordinator owns the complete closure defined
by the
[documentation-only production boundary](operations/production_promotion.md#promote-one-exact-candidate)
and [integrated retirement](operations/production_promotion.md#integrated-feature-or-integration-branch)
procedure. The standing authority covers local exact fast-forward promotion
without a rollback tag or runtime action, exact-`main` publication, and
non-force retirement of only the coordinator's exact clean integrated pair.
An explicit no-publication instruction withholds only the remote step.

Scope expansion beyond documentation ends this standing authority. A dirty or
changed candidate or production checkout, local non-fast-forward, unique or
ambiguous branch content, active or unclear ownership, required evidence in
ignored files, or refused cleanup retains the exact branch/worktree and is
reported; never force the automatic path. An explicit remote-publication
withhold, unexpected remote non-fast-forward, known nonpublishable content, or
network/authentication failure leaves the local promotion intact and is
reported without force-pushing or rewriting history. Such a remote condition
alone does not make an otherwise clean integrated pair unique or block its
automatic retirement.

### Issues and evidence

Follow [`issues/README.md`](issues/README.md) for stable IDs, active-index and
dossier fields, resolution and unconfirmed transitions, lifecycle splits, and
durable evidence. The issue index is a safety router, not a backlog or full
evidence ledger. Link actionable work to exactly one domain backlog.

When cited generated evidence rolls off, retain only needed rows, fields,
units, query window, and read-only extraction method under
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

First apply the repository-change preflight and immediate-before-mutation
rechecks in [`new_thread.md`](new_thread.md#repository-change-preflight). The
documentation-specific additions are:

1. Search all current inbound references to a moved path or heading. Historical
   prose may retain old path text, but its navigation must reach the owner.
2. Verify every changed local Markdown target and anchor.
3. Account for every active task or open issue before deleting or archiving it.
4. Verify changed durable evidence is tracked, fixture-owned, or narrowly
   protected.
5. For every new current document, verify one intentional inbound route, its
   stated load condition, and the absence of a new mandatory-reading cycle.
6. Run `git diff --check` plus proportionate tests for generated, executable,
   schema-defining, or behavior-coupled documentation.

These checks form the documentation candidate gate; an unchanged exact
candidate does not need a second copy of them after promotion or cleanup.

Never put volatile runtime facts in durable guidance. They belong only in a
freshly inspected diagnostic report or conditional handoff section.
