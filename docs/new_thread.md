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

Use one disposable coordinator per coherent outcome. Delegate only when at
least two substantial independent tasks can proceed in parallel, to at most
three direct subagents; descendants need operator authorization. One writer
owns a checkout, and parallel writers need separate feature worktrees.

Repository artifacts—not chat—carry durable state. Checkpoint there before a
handoff or coordinator replacement, and use a handoff only when another
top-level chat continues the work. Read-only work ends with its evidence.
Repository-changing work ends with a validated commit and updated owner.
Promotion owners also publish and retire clean integrated temporary work under
the [production procedure](operations/production_promotion.md).

## Repository-change preflight

1. Confirm the assigned feature worktree, branch, and target diffs.
2. Read the relevant owner, source, callers, and tests; extend an existing
   capability when its boundary fits.
3. Make one coherent change, stage only owned hunks, validate, and commit it.
4. If a guard blocks the outcome, follow its documented wait or recovery path;
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
