# Starting a New TheTower Thread

Codex automatically loads `AGENTS.md`; do not reopen it merely to start. Every
TheTower thread reads this router and chooses the smallest applicable path,
adding another only if scope expands.

## Choose the startup path

- **Read-only question:** inspect relevant current source, configuration,
  callers, tests, or documentation. Skip other preflight unless needed.
- **Repository change, project Python, or tests:** complete the repository and
  development-environment sections below.
- **Live work:** before process/device interaction, runtime diagnosis, live
  validation, or a volatile-state claim, complete
  [`live_preflight.md`](live_preflight.md). Repository-local code changes do
  not require live access by default.
- **Documentation or lifecycle change:** also read
  [`documentation_maintenance.md`](documentation_maintenance.md).
- **Handoff:** read [`handoff_template.md`](handoff_template.md) only when
  preparing or reviewing one.

## Outcome coordination

Use one outcome coordinator per feature, fix, or milestone, then archive it at
completion. Keep tightly coupled work in the coordinator. When at least two
substantial independent subtasks can run in parallel, spawn one batch of at
most three direct subagents; descendants require operator authorization. Wait
for the batch and synthesize it without repeated steering or serial fan-out.

Use separate top-level chats only for genuinely independent writing outcomes.

In a shared checkout, subagents explore, analyze, validate, or review; the
coordinator alone writes. Independent writers need separate feature branches
and worktrees with non-overlapping ownership. Ask workers for concise findings,
exact file/symbol references, validation, risks, blockers, and recommendations
rather than raw logs.

`PENDING_DEVELOPMENT.md`, domain backlogs, canonical guidance, commits, and
validation evidence—not chat history—carry durable state. Before compaction or
coordinator replacement could jeopardize continuity, checkpoint once in the
owning artifact; use a handoff only when another top-level chat must continue.
On completion, validate, commit, update the owning documentation, and close
subagents. A documentation-only coordinator follows the automatic closure
routed by [`documentation_maintenance.md`](documentation_maintenance.md);
that owner and the production procedure define the exact promotion,
publication, and retirement steps. Report that the coordinator is ready to
archive only after its applicable closure is complete or safely retained with
the blocker recorded.

## Repository-change preflight

1. Verify the checkout, branch, working tree, and recent commits. Implementation
   belongs in an assigned feature worktree, not production `main`.
2. Inspect staged and unstaged changes for every target. Preserve unrelated
   work and recheck each target immediately before editing, staging, and
   committing.
3. Read the directly relevant source, configuration, callers, tests, and
   canonical documentation. Search for existing ownership before creating a
   parallel capability.
4. Make one coherent change; review and stage only owned files or hunks,
   validate, and commit before beginning another result.
5. If a guard or assumption blocks the outcome, stop state-changing work,
   preserve evidence, and present repair, redesign, defer, and workaround
   options. Do not weaken the guard or substitute blind action to finish.

## Development Python environment

Never run a development bootstrap in
`/home/brianm/dev/python/TheTower` or use its production-owned `.venv` from
another worktree. In a new feature or integration worktree, invoke bootstrap
directly; it creates or reuses the fingerprinted environment and selects it for
that worktree. Do not run `status` first merely to decide whether bootstrap is
needed, or immediately afterward to reconfirm a successful bootstrap. Use:

```bash
/usr/bin/python3.12 tools/development.py bootstrap
```

After `.venv` exists, run every project command through it. `status` is a
read-only diagnostic for an intentionally non-mutating inspection or a failed
selection, not a prerequisite or follow-up for `bootstrap` or `checkpoint`:

```bash
.venv/bin/python tools/development.py status
.venv/bin/python tools/development.py bootstrap
.venv/bin/python tools/development.py checkpoint
```

`checkpoint` is the complete repository gate, not the default validation after
each edit, feature commit, reconciliation, or promotion. Use focused tests while
the candidate can still change. Run the complete gate only when the candidate
class in the [production procedure](operations/production_promotion.md#choose-the-candidate-gate)
requires it, once per unchanged validation-dependency boundary. The production
procedure defines how the final exact candidate reuses a result when later
commit or ref movement changes none of that check's inputs.

Do not install packages ad hoc. The bootstrap's fingerprint, lock, completion,
and isolation contract is in
[`architecture/development_isolation.md`](architecture/development_isolation.md#development-python-environment).

## Validation and completion

Choose automated validation proportionate to remaining uncertainty. Retained
fixtures are sufficient when they exercise the behavior and no current-state,
transition, timing, or integration uncertainty remains. Use live validation
only when it materially resolves such uncertainty and is safe and authorized;
otherwise record it as pending. Never call behavior live-validated without the
applicable fresh inspection and test.

## Task-specific references

Load only the matching owner:

- [`PENDING_DEVELOPMENT.md`](../PENDING_DEVELOPMENT.md): selecting or
  reprioritizing work; use a supplied domain backlog directly when already
  known.
- [`runtime_operations.md`](runtime_operations.md): select one named operator
  procedure; never load every operation merely because work is live.
- [`sandbox_boundaries.md`](sandbox_boundaries.md): the relevant section for
  host-process, lock, systemd, ADB, socket, or long-lived-process evidence.
- [`architecture/runtime.md`](architecture/runtime.md): the relevant runtime
  component or authority boundary.
- [`architecture/player_save.md`](architecture/player_save.md): player-save
  mapping, evidence, privacy, or fallback behavior.
- [`reference/ui_detection_schema.md`](reference/ui_detection_schema.md) and
  [`tooling/template_workflow.md`](tooling/template_workflow.md): clickmap,
  state-definition, or template work; load the workflow only for assets.
- [`reference/yaml_strategy.md`](reference/yaml_strategy.md): strategy source,
  generated plan, conditions, or gate fields.
- [`observed_issues.md`](observed_issues.md): global hazards before live work,
  then only a task-matching active entry and its conditionally linked dossier.
- [`game_strategy.md`](game_strategy.md): Farm/Tournament strategy assumptions,
  Tier tradeoffs, perks, Damage Slider, Target Priority, or Heat analysis.
- [`ui_state_traversal_2026-07-14.md`](ui_state_traversal_2026-07-14.md): UI
  state coverage or traversal.
- [`../windows/TheTower.ControlSurface/README.md`](../windows/TheTower.ControlSurface/README.md):
  native client publishing, operation, or Windows-only validation.

When a task expands, stop before the new class of action and complete its path.
