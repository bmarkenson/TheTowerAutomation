# Tooling and Maintenance Backlog

This file contains active developer tooling, schema, maintenance, and process
work. Historical checked detail remains in the
[`2026-07-16 backlog snapshot`](history/PENDING_DEVELOPMENT_2026-07-16.md).

## Configuration and developer tooling

- [ ] Make Codex shell execution use the repository `.venv` by default instead
  of inheriting the SSH session's system `python` and user-level `pytest`.
  - Determine the supported workspace-level Codex configuration or checked-in
    runner that persists across sessions.
  - Until then, require `.venv/bin/python` and
    `.venv/bin/python -m pytest` explicitly and fail clearly if absent.
- [ ] Continue the full template audit begun on 2026-07-13.
  - Resolve or classify the recursive validator's dated orphan list.
  - Add fixture-based match verification so a present template is also proven
    current against a canonical screen.
  - Require the recursive validator in the normal test/checkpoint workflow.
- [ ] Build one recursive validator for clickmap and state-definition schema.
  - Validate entries, roles, regions, files, templates, taps, swipes,
    thresholds, and dangling YAML references.
  - Enforce or migrate toward one naming convention.
  - Detect drift and optionally emit state-definition stubs.
  - Consolidate or replace `test/clickmap_integrity.py` and
    `test/validate_state_defs.py`.
- [ ] Allow targeted editing of one clickmap entry without rewriting the entire
  document.
- [ ] Extract duplicated handler helpers only after the unused-code audit
  establishes their actual call sites.

## Codebase maintenance

- [ ] Audit the repository for unused or obsolete code, configuration, assets,
  generated files, tests, tools, and documentation.
  - The repository-local audit and reviewable removal proposal is recorded in
    [`../codebase_maintenance_audit_2026-07-26.md`](../codebase_maintenance_audit_2026-07-26.md).
    It found concentrated orchestration complexity rather than broad
    duplication, classified the named Cards paths, separated 24 active
    module-catalog templates from 20 asset-removal candidates, and identified
    the compatibility decisions still required before deletion.
  - Trace imports, runtime entry points, dynamic/YAML references, strategies,
    clickmap usage, and tests before classifying anything as dead.
  - Classify findings as active, generated, tooling, archival, or removable.
  - Produce a reviewable removal/archive proposal before moving or deleting.
  - Include legacy `.old` files and generated documentation.
  - Specifically verify the active status of `Cards:GCFarmEarly`,
    `Cards:GCFarmLate`, `cards:locked:*`, deck indicators, card navigation
    entries, and their templates.

## Development process

- [ ] Treat behavioral blockers as explicit decision points.
  - Stop state-changing actions, preserve evidence, and report the failed guard
    or assumption.
  - Present repair, redesign, defer, and workaround options with their safety
    and maintenance tradeoffs before choosing a behaviorally different path.
  - Never lower a guard, substitute a blind/manual action, or encode a game bug
    as permanent behavior merely to finish a test.
- [ ] Re-examine architecture when a capability exposes duplicated polling,
  sleeps, state ownership, or recovery logic. Prefer a measured design spike
  when it could simplify multiple pending features.
- [ ] Use incremental Git commits while iterating.
  - Keep each commit to one coherent, tested behavior or audit result.
  - Review staged files and exclude captures, logs, editor files, and unrelated
    work.
  - Do not preserve an implementation solely for compatibility when a simpler,
    safer, better-tested design can replace it.
