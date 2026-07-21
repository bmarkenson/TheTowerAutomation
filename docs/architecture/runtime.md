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

## Farm profiles and loadouts

`Farm` is the recurring automated run profile. Glass Cannon describes a
gameplay style, not a runtime profile: it may also be used for tournaments,
milestones, or some Dissonance runs, so it does not own Farm configuration.
Legacy `gc*` strategy names remain aliases during migration.

The Farm baseline owns settings that must be true for every Farm run: the
`Farm` Cards, Workshop, and Bots presets; Shockwave Size, Bounce Shot Targets,
and Bounce Shot Range Free Upgrade locks; Guardian chips; Auto Pick Perks; and
Ultimate Weapon controls. Compact Tier profiles cannot override those
invariants.

Only Modules, Damage Slider, and Target Priority vary by Tier or experiment.
Every compact Farm profile names all three and assigns one of these policies:

| Policy | Runtime contract |
| --- | --- |
| `enforce` | Inspect and require the resolved value; a mismatch blocks unless an explicit safe repair contract owns the transition. |
| `observe` | Inspect and record the resolved expectation and evidence without blocking or changing the setting. |
| `preserve` | Do not inspect or change the setting. |

Named module and Target Priority presets are resolved at build time into an
explicit self-contained strategy plan. Damage Slider `observe` and `enforce`
policies resolve an explicit percentage; Tier 18 enforces `1E-22%` during every
new-run initialization after the time-sensitive EHLS/EALS setup. `YamlStrategy`
exposes the plan's resolved
`run_configuration` generically, and Game Over records copy that snapshot into
the versioned battle JSON. Runtime code does not inherit configuration or
branch on a Farm strategy name.

## Tournament observer profile

`Tournament` is a passive, single-battle observer profile. Its generated plan
owns the Tournament Cards, Tourney Workshop, Amplify Bots, Attack/Ally/Scout
Guardians, Tournament/Milestone modules, all nine Ultimate Weapons, and
Spotlight missiles. Tournament battles have no Perks, so Perks are not part of
this contract.

The read-only route inspects Cards, Ultimate Weapons, Modules, Bots, and
Guardians from the active battle. Workshop is the only check that takes the
resumable Exit Battle → Go Home path. The route never selects or equips a
setting and must verify that Resume returns to the same Tournament.

After one conclusive validation attempt, the Tournament runtime policy grants
action authority only to ad-gem collection and terminal-result handling. An ad
gem starts the same bounded floating-gem sweep used by normal battles; the
Tournament policy does not run an independent or continuous floating-gem
handler. A configuration mismatch is retained as session evidence but cannot
request repair or block result capture. The profile does not buy upgrades,
Surrender, auto-return Home, or start another battle.

In-battle side-menu destinations and Event/Guild tabs require visible template
matches and tap the matched bounding box. Their static coordinates are not
action authority; this is required because the Tournament Trophy control moves
the Guild button to a different grid cell.

Natural Tournament completion is the distinct `TOURNAMENT_RESULTS` terminal
state. Its handler OCRs league, wave, rank, death source, and the displayed coin
split; opens `MORE STATS`, copies the exact Round Stats report, and restores the
summary using only visible matched controls. It writes JSON and Markdown under
`logs/tournaments`, skips a recent matching valid record after process restart,
and deliberately has no authority to press the terminal `OK` button. The
resolved run configuration and validation evidence are included in the record,
and control remains in `WAIT` for operator direction.

Completed-run classification preserves that terminal distinction. A detected
`TOURNAMENT_RESULTS` record is Tournament. A standard `GAME_OVER` record under
the shared Tournament/Milestone profile is Milestone, while Farm
strategy/profile identity marks Farm. Shared Tournament settings by themselves
remain insufficient evidence to distinguish Tournament from Milestone.

## State and battle lifecycle

- Visible navigation and battle lifecycle are separate. Home
  `RESUME_BATTLE` preserves the current battle identity; `GAME_OVER`,
  `TOURNAMENT_RESULTS`, or a verified Home `NEW_BATTLE` ends it.
- Lifecycle and guarded Home actions share one Home classifier. A handler must
  not infer a new run independently from navigation alone.
- `HOME_AD_GEMS_AVAILABLE` schedules the five-gem Home claim before Home can
  start or resume a battle. The handler requires a fresh visible button match,
  verifies dismissal, and never starts the in-battle floating-gem tapper.
- Transient `UNKNOWN` observations preserve an owned, incomplete startup gate.
  Initialization completion depends on the strategy assertion, not merely the
  current primary screen.
- While paused or exclusively gated, capture, detection, lifecycle observation,
  and read-only status reporting continue. Strategy, handler, mission,
  recovery, and blind-tapper actions remain blocked.

## Completed-run records and evidence

- Structured Battle/Tournament JSON is the canonical completed-run artifact;
  Markdown and the control-surface report are views over that same record.
- The record retains copied Stats rows, compact Game Stats-only fields, perks,
  resolved configuration, observed preflight/runtime evidence, and derived
  metrics. Consumers must read these fields instead of relying on a terminal
  screenshot.
- Periodic valid Coins/min readings are stored as bounded numeric samples with
  their timestamp, wave, and OCR confidence, then attached to the completed
  record. The runtime does not maintain a separate per-run Coins CSV or toggle
  the live display to collect scheduled lifetime-total snapshots.
- Routine terminal screenshots are not durable artifacts. The handlers retain
  source frames only when capture, parsing, persistence, or validation fails or
  remains uncertain. Historical screenshots may remain as evidence, but new
  code must not require them; previous-wave lookup uses structured records.

## Matching and action authority

- Direct ADB capture accepts supported native `1080x1920` and `720x1280`
  framebuffers, records the source geometry, and normalizes them to the
  canonical `1080x1920` vision coordinate space. Unsupported or majority-black
  compositor frames are rejected, with one immediate fresh attempt for an
  incomplete frame. Semantic state detection and visibility-aware action
  matching repeat the completeness check so injected or retained incomplete
  frames cannot bypass the capture boundary.
- Runtime taps and swipes remain canonical until the centralized ADB input
  boundary, which scales and clamps them to the last verified native geometry
  for that ADB target. Capture therefore establishes geometry before action.
- A broad search region is evidence geometry, not permission to tap its center.
- Visibility-aware actions use the actual matched bounding box.
- Moving elements may continue to use match-region centers after a fresh match.
- Static blind actions require explicit action geometry. Legacy direct-region
  center lookup may remain for compatibility and tooling but must not acquire
  runtime action authority implicitly.
- Every live action requires fresh source-state evidence immediately before the
  input. Transition frames and stale screenshots cannot authorize actions.
- Farm configuration is inspection-first and profile-driven. A setting may be
  corrected during the active run only when the profile explicitly owns that
  setting and its runtime contract declares the transition safe. Session
  preflight owns verified Poison Swamp Stun `on` → `off`: it opens the detected
  Poison Swamp detail, taps the freshly matched checked control, verifies
  `off`, and returns to `RUNNING/UW_MENU` without Surrender or a Home
  transition. New-run initialization owns Damage Slider enforcement: it
  requires `RUNNING/ATTACK_MENU`, opens the freshly matched Damage control,
  reacquires authoritative panel and percentage evidence before every explicit
  arrow tap, requires strict progress and a verified final value, and returns
  to `RUNNING/ATTACK_MENU`. Unknown or incomplete evidence remains blocked.
- Farm session preflight still reaches the resumable Home Workshop to validate
  the unrelated Workshop preset, but it never invokes the Free Upgrade lock
  scanner. Shockwave Size, Bounce Shot Targets, and Bounce Shot Range are
  inspected and enforced only by complete no-battle setup after verified Home
  `NEW_BATTLE` evidence and before Battle may start. That boundary-owned proof
  is retained in session evidence and completed-run reporting. Attaching to an
  existing battle without such proof records `unavailable_deferred` without a
  pass, failure, Home repair, or Surrender request; Home `RESUME_BATTLE`
  preserves the attachment, and the lock gate rearms at the next genuine
  `NEW_BATTLE` boundary.
- Confident mismatches on Home-only configuration may request one app-owned
  stop/repair/restart sequence; ambiguous or unknown module identity and other
  non-Home repair classes remain blocked. The matcher reports evidence but
  never directly authorizes an equipment action.
- A guarded configuration repair must reach verified Home `NEW_BATTLE`, use
  fresh detail/name and action guards for module changes, reapply the complete
  profile-owned no-battle setup, start the next battle, and require fresh
  session preflight evidence before normal handlers regain authority.

## Process and control ownership

- The persistent control file is authoritative operator intent.
- A non-blocking lock keyed by ADB target prevents competing runtimes from
  acting on the same device. A lock is evidence of a former owner, not proof
  that its PID is still alive.
- Pause blocks every strategy and handler action while allowing observation and
  status reporting.
- Control synchronization precedes capture, so an ADB outage cannot prevent a
  Pause acknowledgement or a paused target-handoff request. The watchdog may
  retry connectivity while paused but may not foreground or restart the game.
- An acknowledged paused runtime may migrate its localhost ADB target without
  process replacement. It acquires the new per-target lock first, temporarily
  selects that endpoint, requires successful connection and supported capture,
  then releases the old lock and acknowledges the directive. Failure restores
  the previous target and retains Pause. Existing mission, strategy, and gate
  state stays in memory throughout.
- Process startup has an explicit gate policy. `immediate` retains the normal
  behavior in which the first observed active battle is a new-run boundary.
  `next_run` adopts the first active/resumable battle and structurally
  suppresses plan rules tagged `run_initialization` or `session_preflight`.
  It does not seed their completion variables. Game Over, Tournament Results,
  or Home `NEW_BATTLE` arms the gates, and the next `RUNNING` observation emits
  the normal run-start hooks. Home `RESUME_BATTLE` and transient Unknown states
  preserve the attachment.
- Explicit mid-run strategy adoption uses the same attachment boundary. Fresh
  `RUNNING` or Home `RESUME_BATTLE` evidence may replace normal strategy
  behavior and report identity without a restart, but run initialization,
  session preflight, and Home-only checks stay deferred. A request encountered
  at Home `NEW_BATTLE` follows normal boundary replacement instead and runs the
  complete startup-gate sequence.
- Process replacement must verify the existing owner and safe UI boundary,
  then verify the replacement PID, refreshed lock, startup log, control
  consumption, and first state report.
- Remote lifecycle control is limited to the configured
  `thetower-automation.service` systemd user unit. A start crosses the process
  boundary under persisted `PAUSED` and may publish `RUNNING` only after the
  unit is active. A stop persists `STOPPED` before systemd signals the unit.
  A stopped request may persist one validated localhost ADB TCP port and one
  validated startup-gate policy for the next start; an acknowledged paused
  runtime may apply that same restricted port as a live target handoff. Remote
  requests cannot supply a PID, unit name, executable, host, path, or shell
  command.

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
