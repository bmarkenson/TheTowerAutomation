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

## Production and development isolation delivery

Implement the canonical
[production and development isolation contract](../architecture/development_isolation.md)
as separately reviewable phases. Do not expose a later capability before every
earlier authority and compatibility dependency it names is deployed.

- [x] Phase 0 — make development reproducible without production's `.venv`.
  - `0a17fef` added exact CPython/platform configuration, one grouped direct
    dependency declaration, complete hash-checked runtime/development locks,
    and the pinned lock/bootstrap toolchain.
  - The serialized content-addressed bootstrap publishes only relocated,
    manifested, immutable environments; the non-live checkpoint isolates
    bytecode, pytest/coverage caches, logs, screenshots, custom configuration,
    and scratch state while blocking runtime/ADB/host-tool execution.
- [ ] Phase 1 — establish source and observation identity.
  - Implement dirty-worktree registration/fingerprinting and the separate
    service, runtime, target, battle, frame-source, frame-sequence, lease, and
    source namespaces, including the separately named production action-catalog
    revision/digest.
  - Add atomic immutable frame bundles, status invalidation, reader
    verification, and bounded retention before any broker input path.
- [ ] Phase 2 — extend the production control service as the passive broker.
  - Add the role-separated external-client and runtime-peer Unix sockets,
    service epoch, host-global coordinator, additive status/capability surface,
    durable audit ledger, CLI read path, and broker-coalesced
    direct-read/capture policy.
  - Establish the runtime-initiated persistent peer with `SO_PEERCRED`,
    MainPID/session/target-lock authentication, target-generation binding,
    protocol/capability negotiation, ordered framing, bounded queues,
    heartbeat/status, backpressure, and disconnect/replay handling.
  - Keep all connection management and device input prohibited.
- [ ] Phase 3 — implement external yield without external input.
  - Add the suppressive external-development hold, exact runtime
    acknowledgement, request state machine, fairness, deadlines, heartbeat,
    token redaction, revocation, and restart reconciliation.
  - Route yield, acknowledgement, revocation, cleanup disposition, and shutdown
    through the runtime-peer channel; add the wakeable App-owned mailbox and
    shared production action serializer while keeping input commands disabled.
  - Prove Pause precedence and that neither `AuxiliaryRouteLease` nor existing
    exclusive-validation receipts can satisfy a development lease.
- [ ] Phase 4 — add the production-mediated input gateway.
  - Add the production-installed, runtime-published semantic action catalog
    with stable action IDs, revisions/digests, bounded parameters, fixed guards
    and postconditions, dependency digests, and disabled/allowlisted rollout.
  - Implement ordered idempotent input-command/result messages, request- and
    dispatch-time catalog/source/frame/owner checks, response-loss replay,
    action-log and durable-audit pairing, running-battle and Home capability
    bounds, and fail-closed cleanup.
- [ ] Phase 5 — implement lifecycle continuation and owned validation.
  - Return natural Game Over authority to production, service eligible Home
    requests, and issue only a fresh next-battle token after initialization and
    session preflight.
  - Add the separate operator-authorized development-validation receipt,
    ordinary-battle claim, Tournament exclusion, and exact cleanup ownership.
- [ ] Phase 6 — complete integration and promotion hardening.
  - Run the contract's unit, fake-clock, fake-runtime/fake-ADB, peer-auth,
    framing/ordering, disconnect/replay, backpressure, action-catalog,
    retained-frame, API, concurrency, crash/restart, source-drift, Pause, and
    terminal/Home matrix.
  - Enforce worker-only feature commits, master-owned develop integration and
    conflict resolution, clean/resolved checkout gates, reviewed exact range,
    main ancestry, fast-forward-only exact-candidate promotion while production
    remains on main, and emergency-hotfix back-integration.
  - After those gates pass, update the startup/operations guidance and perform
    only the separately authorized live-validation sequence before production
    promotion.

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
