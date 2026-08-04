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

## Production and development coordination delivery

Implement the canonical
[production and development coordination contract](../architecture/development_isolation.md)
as small, separately reviewable changes. The project is trusted-single-user;
these controls prevent accidents and stale actions rather than defend against a
malicious same-account process.

- [x] Establish the Git and documentation baseline.
  - Keep production on `main` at its existing path, use the sibling `develop`
    integration worktree, and give each worker a temporary feature worktree.
  - Workers commit only feature changes; the master owns integration,
    authority-sensitive work, validation, and exact fast-forward promotion.
- [ ] Simplify the initial development-bootstrap prototype before production
  promotion.
  - Keep the tracked interpreter/dependency declarations, pinned locks,
    content-selected development environment, one writer lock, worktree-local
    `.venv` symlink, and full non-live checkpoint.
  - Replace the immutable installed-file manifest, staged-environment
    relocation and `RECORD` rewriting, whole-tree fsync/permission scheme,
    hostile-symlink/no-follow checks, and host-tool blocking with a compact
    completion-marker bootstrap and ordinary interrupted-build recovery.
  - Run the complete repository-local suite normally, including installed
    Tesseract/OCR coverage; continue to fake ADB unless a thread deliberately
    enters the live-runtime path.
- [ ] Make production screenshots convenient development evidence.
  - Permit workers to read or copy existing production screenshots and other
    generated artifacts. Historical files do not claim current runtime state.
  - Publish or expose one atomically replaced complete latest PNG with minimal
    capture/target/geometry metadata. Do not build immutable bundle trees,
    receipt machinery, or cryptographic frame identity.
  - Document bounded exact-target `get-state`, screenshot, and other
    non-mutating ADB reads after the live startup inspection. They require no
    interactive lease; production continues to own connection management and
    long-lived capture.
- [ ] Add one cooperative interactive lease and production hold.
  - Extend the existing control surface/directive path rather than adding an
    authenticated runtime-peer protocol or third daemon.
  - Allow at most one active lease with an operator-readable owner, ordinary
    lease ID, exact target, fresh runtime evidence, acknowledgement,
    heartbeat, expiry, and release time. Returning `busy` is sufficient.
  - Install a distinct suppressive `external_development` hold before
    acknowledgement. Operator Pause/Stop, runtime replacement, target change,
    heartbeat loss, and battle boundaries end input authority.
- [ ] Add a small lease-aware exact-target ADB input helper.
  - Recheck the active acknowledged lease, target, and expiry before each
    bounded tap or swipe; use the existing action log for intent and result.
  - Never automatically replay uncertain input. Do not add secret tokens,
    complete source fingerprints, a semantic action catalog, a custom runtime
    mailbox, or an ordered replay protocol.
- [ ] Validate the combined coordination boundary.
  - Cover bootstrap recovery, atomic frame replacement, one-lease exclusion,
    production acknowledgement, Pause/Stop precedence, expiry, runtime/target
    and battle boundaries, stale helper rejection, and clean release with unit
    and fake-runtime/fake-ADB tests.
  - The master may then schedule one separately inspected bounded live lease.
    Add Home queues, suspended continuation, or automated owned-validation
    battles only when a concrete test requires them.

Source attestation, peer authentication, capability negotiation, secret-token
security, hash-chained audit, fairness queues, automatic cross-battle renewal,
and hostile-same-user filesystem defenses are explicit non-goals unless the
operator later changes the project threat model.

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
