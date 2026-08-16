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
- [ ] Add the project Codex spawned-agent concurrency guard when the supported
  local configuration parser accepts the current schema key.
  - Exact intended setting: `[agents]` with
    `max_concurrent_threads_per_session = 3`.
  - This is a concurrency guard, not a total-usage budget. Until it is
    compatible, the three-direct-subagent invariant in `AGENTS.md` remains
    authoritative.
- [ ] Continue the
  [full template audit begun on 2026-07-13](../template_audit_2026-07-13.md)
  through the
  [current template workflow](../tooling/template_workflow.md).
  - Resolve or classify the recursive validator's dated orphan list.
  - Add fixture-based match verification so a present template is also proven
    current against a canonical screen.
  - Require the recursive validator in the normal test/checkpoint workflow.
- [ ] Build one recursive validator for the
  [clickmap and state-definition schema](../reference/ui_detection_schema.md).
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

## Source and documentation discovery

- [ ] Audit source capability and ownership discovery after retiring the legacy
  API summaries.
  - Verify representative feature work finds existing functions, classes,
    modules, commands, configuration/schema paths, workflows, callers, and
    tests through current-source search rather than generated summaries.
  - Classify the six remaining generation-era `AUTO-SPEC`/`spec_legend`
    docstring blocks in `core/floating_button_detector.py`, `label_tapper.py`,
    `run_state.py`, `state_detector.py`, `tap_dispatcher.py`, and `watchdog.py`;
    retain source documentation only when it improves current ownership
    discovery.
  - Add tooling only for a demonstrated discovery gap; keep the universal
    reuse-first safeguard canonical in `AGENTS.md`.

## Runtime artifact retention and archival

- [ ] Archive the finite legacy `logs/coins_per_min_*.csv` collection now that
  structured completed-run records own current Coins/min history.
  - Re-inventory the files and preserve their original names and contents in a
    lossless compressed archive outside automatic runtime retention.
  - Document recovery, verify archive readability plus file and data-row
    counts, and remove the individual CSVs only after the verified archive
    provides a recoverable boundary.
- [ ] Constrain configured wave/coin sample retention roots to dedicated
  repository-local diagnostic directories.
  - Reject the repository root, broad ancestors, canonical record/evidence
    locations, path aliases, and overlapping roots that could enroll unrelated
    durable data in automatic deletion.
  - Preserve intentional custom sample directories and cover accepted and
    rejected boundaries with focused tests and operator-facing errors.
- [ ] Make every executed generated-artifact retention sweep auditable,
  including successful no-op sweeps.
  - Emit one concise summary at startup or the rate-limited interval, not on
    every heartbeat check, with the policy, examined roots, removed and
    protected totals, and errors.
  - Consolidate existing deletion/error reporting and cover no-op, deletion,
    protection, rate-limiting, and error outcomes without log noise.
- [ ] Audit retained screenshots for reusable development-validation evidence
  and promote each selected frame into version-controlled `test/fixtures/`.
  - Select only evidence that reproduces a detector, OCR, navigation,
    lifecycle, or regression contract; do not preserve every automatic OCR or
    failure capture.
  - Add or update a focused test that consumes each promoted fixture, preserve
    concise provenance, and leave source production artifacts untouched.
  - Verify all existing fixtures are tracked and every durable documentation
    reference that remains inside a cleanup root is narrowly protected.

## Codebase maintenance

- [ ] Resolve the compatibility and removal decisions from the
  [2026-07-26 codebase maintenance audit](history/codebase_maintenance_audit_2026-07-26.md).
  - Trace imports, runtime entry points, dynamic/YAML references, strategies,
    clickmap usage, and tests before classifying anything as dead.
  - Classify findings as active, generated, tooling, archival, or removable.
  - Produce a reviewable removal/archive proposal before moving or deleting.
  - Resolve legacy `.old` files, generated documentation, the 20 identified
    asset-removal candidates, and the active status of `Cards:GCFarmEarly`,
    `Cards:GCFarmLate`, `cards:locked:*`, deck indicators, card navigation
    entries, and their templates.
