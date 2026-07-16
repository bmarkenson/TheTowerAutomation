# Runtime Architecture

This document is the current source of truth for TheTower runtime boundaries.
It records stable decisions, not task priority or volatile process state. The
originating review and live evidence are preserved in the
[`2026-07-14 architecture history`](history/architecture_direction_2026-07-14.md),
while future work is tracked in [`../../PENDING_DEVELOPMENT.md`](../../PENDING_DEVELOPMENT.md).

## Layer boundaries

The clickmap is a declarative catalog of UI facts, not the runtime control
plane. It may own template identity, thresholds, search and action geometry,
roles, and shared regions. It must not own current UI state, battle identity,
action ordering, pause semantics, retries, handler ownership, or recovery
policy.

| Layer | Responsibility |
| --- | --- |
| Capture/observation | Produce fresh frames with sequence and timing metadata. |
| Matching/clickmap | Describe and locate visible UI evidence. |
| Semantic state | Interpret evidence as primary state, overlays, and lifecycle events. |
| Orchestration/policy | Decide which component may act and in what order. |
| Action authority | Recheck guards and issue a visible or explicit static action. |
| Feature handlers | Implement one bounded behavior through the shared layers. |

Keep `YamlStrategy` and its evaluator generic. Strategy-specific behavior should
come from compact configuration and explicit generated plans, not strategy-name
conditionals or duplicated expanded YAML.

## State and battle lifecycle

- Visible navigation and battle lifecycle are separate. Home
  `RESUME_BATTLE` preserves the current battle identity; `GAME_OVER` or a
  verified Home `NEW_BATTLE` ends it.
- Lifecycle and guarded Home actions share one Home classifier. A handler must
  not infer a new run independently from navigation alone.
- Transient `UNKNOWN` observations preserve an owned, incomplete startup gate.
  Initialization completion depends on the strategy assertion, not merely the
  current primary screen.
- While paused or exclusively gated, capture, detection, lifecycle observation,
  and read-only status reporting continue. Strategy, handler, mission,
  recovery, and blind-tapper actions remain blocked.

## Matching and action authority

- A broad search region is evidence geometry, not permission to tap its center.
- Visibility-aware actions use the actual matched bounding box.
- Moving elements may continue to use match-region centers after a fresh match.
- Static blind actions require explicit action geometry. Legacy direct-region
  center lookup may remain for compatibility and tooling but must not acquire
  runtime action authority implicitly.
- Every live action requires fresh source-state evidence immediately before the
  input. Transition frames and stale screenshots cannot authorize actions.

## Process and control ownership

- The persistent control file is authoritative operator intent.
- A non-blocking lock keyed by ADB target prevents competing runtimes from
  acting on the same device. A lock is evidence of a former owner, not proof
  that its PID is still alive.
- Pause blocks every strategy and handler action while allowing observation and
  status reporting.
- Process replacement must verify the existing owner and safe UI boundary,
  then verify the replacement PID, refreshed lock, startup log, control
  consumption, and first state report.

## Planned evolution

An app-owned frame source and short-lived UI-state action lease are the intended
direction for multi-frame decisions and latency-sensitive scheduled actions.
The observer should publish frame sequence, observation time, state, and
invalidation state; an action should make an O(1) freshness check immediately
before input. Navigation, non-running evidence, pause, capture failure, and
staleness invalidate the lease.

This is a planned package, not authority to add a second competing
`screenrecord` process or bypass current screenshot guards. Requirements and
benchmarks live in
[`../backlog/state-and-detection.md`](../backlog/state-and-detection.md#capture-and-action-architecture).
