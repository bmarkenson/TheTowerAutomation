# Starting a New TheTower Thread

Codex automatically loads `AGENTS.md`; do not reopen it merely to start. Every
TheTower thread reads this router and chooses the smallest applicable path,
adding another only if scope expands.

## Choose the startup path

- **Read-only or retained-evidence work:** inspect relevant current source,
  configuration, callers, tests, documentation, closed battle records,
  historical log ranges, or retained telemetry and fixtures. Historical
  production artifacts do not require live preflight when used only as
  retained evidence, and they do not prove current runtime state.
- **Repository change, project Python, or tests:** complete the repository and
  development-environment sections below.
- **Live work:** before process/device interaction, diagnosis that depends on
  current or changing runtime evidence, live validation, or a volatile-state
  claim, complete
  [`live_preflight.md`](live_preflight.md). Repository-local changes do not
  require live access by default.
- **Documentation or lifecycle change:** also read
  [`documentation_maintenance.md`](documentation_maintenance.md).
- **Handoff:** read [`handoff_template.md`](handoff_template.md) only when
  preparing or reviewing one.

## Outcome coordination

Use one disposable coordinator per coherent outcome. Delegate only when at
least two substantial independent tasks can proceed in parallel, to at most
three direct subagents; descendants need operator authorization. One writer
owns a checkout, and parallel writers need separate feature worktrees.

Repository artifacts—not chat—carry durable state. Checkpoint there before a
handoff or coordinator replacement, and use a handoff only when another
top-level chat continues the work. Read-only work ends with its evidence.
Unless the operator requests a draft/review-only result or no promotion, or
assigns another owner, each repository-changing coordinator owns final
production promotion and follows the
[production procedure](operations/production_promotion.md) through applicable
deployment and smoke, `origin/main` publication, and clean integrated temporary-
work retirement. A no-publication or retained-work request narrows only its
named step. A guard leaves the outcome pending, not complete.

## Repository-change preflight

1. Confirm the assigned feature worktree, branch, and target diffs.
2. Read the relevant owner, source, callers, and tests; extend an existing
   capability when its boundary fits.
3. Make one coherent change and use focused validation while the candidate can
   still change.
4. Recheck the target diff, stage only owned hunks, and commit the exact
   candidate before running its final promotion gate. When the gate result must
   be added afterward, put only the concise completion record in the immediately
   following commit under the production procedure's
   [completion-record exception](operations/production_promotion.md#completion-record-exception).
5. If a guard blocks the outcome, follow its documented wait or recovery path;
   never weaken it or claim completion.

## Development Python environment

Never run a development bootstrap in
`/home/brianm/dev/python/TheTower` or use its production-owned `.venv` from
another worktree. In a feature or integration worktree, run bootstrap once; it
creates or selects the fingerprinted environment:

```bash
/usr/bin/python3.12 tools/development.py bootstrap
```

Run project Python through `.venv/bin/python`. `status` diagnoses a selection;
it does not bracket successful commands. Use focused tests while work can
change, and run `.venv/bin/python tools/development.py checkpoint` only when the
[production procedure](operations/production_promotion.md#choose-the-candidate-gate)
requires the complete gate.

A complete checkpoint against a mutable or uncommitted working tree is
development evidence, not the final exact-candidate gate. Finish source, tests,
configuration, generated inputs, and other gate inputs; commit exact candidate
`V`; verify its tracked worktree is clean; then run the checkpoint at `V`.
Do not add or change a test assertion after that gate and expect a later mixed
commit to qualify for the completion-record exception. Fold such a change into
a new exact candidate and apply the gate selected by the production procedure.

Tests must write intentional logs, screenshots, control files, and failure
evidence to pytest temporary directories or the checkpoint's isolated generated
root, never to ignored runtime-evidence paths in the feature worktree. Inspect
non-cache ignored output before freezing the candidate and repair a leaking test
while the worktree still has an owner; do not defer discovery until retirement.

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

| Task | Owner |
| --- | --- |
| Select or reprioritize work | [`PENDING_DEVELOPMENT.md`](../PENDING_DEVELOPMENT.md), or the already-supplied domain backlog |
| Select one live operation | [`runtime_operations.md`](runtime_operations.md) |
| Interpret host process, lock, systemd, ADB, socket, or wrapper evidence | Relevant [`sandbox boundary`](sandbox_boundaries.md) |
| Runtime contract or authority | Relevant [`runtime architecture`](architecture/runtime.md) section |
| Player-save mapping, evidence, privacy, or fallback | [`player-save architecture`](architecture/player_save.md) |
| Clickmap, state definition, or template asset | [`UI schema`](reference/ui_detection_schema.md), plus the [`template workflow`](tooling/template_workflow.md) only for assets |
| YAML strategy source or plan | [`YAML strategy reference`](reference/yaml_strategy.md) |
| Active issue or live hazard | [`observed issue router`](observed_issues.md), then only a matching dossier |
| Farm/Tournament assumptions and tradeoffs | [`game strategy`](game_strategy.md) |
| UI state coverage | [`UI traversal`](ui_state_traversal_2026-07-14.md) |
| Native client operation or publication | [`Windows client guide`](../windows/TheTower.ControlSurface/README.md) |

When a task expands, stop before the new class of action and complete its path.
