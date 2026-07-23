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
| Action authority | Recheck guards and issue a template-matched or target-verified action. |
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
explicit self-contained strategy plan. Modules are checked from verified Home
`NEW_BATTLE` before Battle may start. A guarded module replacement accepts
every presented level-transfer dialog for both Primary and Assist roles so the
slot's existing level follows the incoming module; an unverified transfer
prompt fails closed. Target Priority is not a Home control: its Home-boundary
evidence remains explicitly deferred, and its policy is checked from the
verified in-battle side menu after the new run reaches `RUNNING`. Damage Slider
`observe` and `enforce` policies resolve an explicit percentage; Tier 18
enforces `1E-22%` during every new-run initialization after the time-sensitive
EHLS/EALS setup. `YamlStrategy` exposes the plan's resolved `run_configuration`
generically, and Game Over records copy that snapshot into the versioned battle
JSON. Runtime code does not inherit configuration or branch on a Farm strategy
name.

Game speed is a global battle-only invariant. A periodic guard requires
authoritative `RUNNING` evidence, reads the localized speed value and visible
plus glyph, and sends verified `+` taps until one produces no increase. This
discovers the current perk-dependent ceiling instead of hard-coding `x5.0` or
`x6.3`. Farm defers the guard while either urgent EHLS/EALS purchase remains
incomplete; attachment and non-Farm profiles may correct speed as soon as
their runtime policy grants the handler action authority. Pause is rechecked
before every speed tap.

## Tournament observer profile

`Tournament` is a passive, single-battle observer profile. Its generated plan
owns the Tournament Cards, Tourney Workshop, Amplify Bots, Attack/Ally/Scout
Guardians, Tournament/Milestone modules, all nine Ultimate Weapons, and
Spotlight missiles. Tournament battles have no Perks, so Perks are not part of
this contract.

At verified Home `NEW_BATTLE`, the profile's no-battle route selects Tournament
Cards, Tourney Workshop, Amplify Bots, Attack/Ally/Scout Guardians, and the
Tournament module loadout. The evidence is retained for session preflight, and
the runtime deliberately leaves Tournament entry to the operator instead of
pressing the normal Battle control. Session preflight then checks only Ultimate
Weapons from the active Tournament.

Without Home boundary evidence, including attachment to an already-running
Tournament, the guarded read-only compatibility route inspects Cards, Ultimate
Weapons, Modules, Bots, and Guardians in battle. Workshop is the only check
that takes resumable Exit Battle → Go Home. The route never selects or equips a
setting and must verify that Resume returns to the same Tournament.

After one conclusive validation attempt, the Tournament runtime policy grants
action authority only to ad-gem collection and terminal-result handling. An ad
gem starts the same bounded floating-gem sweep used by normal battles; the
Tournament policy does not run an independent or continuous floating-gem
handler. A configuration mismatch is retained as session evidence but cannot
request repair or block result capture. Attached-run mismatches publish a
non-blocking operator decision: pause for manual changes, retry with fresh
evidence, or continue observation with a run-scoped waiver for the displayed
check. The profile does not buy upgrades, Surrender, auto-return Home, enter a
Tournament, or start a normal battle.

In-battle side-menu destinations and Event/Guild tabs require visible template
matches and tap the matched bounding box. Their static coordinates are not
action authority; this is required because the Tournament Trophy control moves
the Guild button to a different grid cell. Guild reward-badge measurement uses
that same fresh Guild match as its crop anchor, so the displaced Tournament
layout remains observable without broadening the color detector.

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
remain insufficient evidence to distinguish Tournament from Milestone. The Tier
copied or OCRed from terminal stats is stored as observed evidence independently
of strategy identity. Thus an unconfigured standard Game Over can report its
Tier while remaining `unknown` rather than fabricating Farm or Milestone type.

## No Strategy observation profile

`No Strategy` supplies no configured run intent and owns no upgrade actions,
startup initialization, or session-preflight gate. It is nevertheless an
observation profile: after an active `RUNNING` frame establishes the battle, it
owns one exclusive, guarded read-only traversal of Cards, Bots, Guardians,
Modules, Target Priority, Damage Slider when accessible, Perks, and Ultimate
Weapons. Every action is source-state guarded, every destination is verified,
and Pause is synchronized before each input. Workshop remains a Home-boundary
observation. Missing screens remain explicitly `not_observed`; authoritatively
inaccessible controls are recorded as `unavailable` with a reason. Values are
never copied from a Farm or Tournament profile. This evidence is stored under
`observed_run_configuration`, separately from the configured-intent
`run_configuration` field.

The fixed purple sword badge next to Tier is localized Attack Dissonance
identity evidence. Standard Game Over plus that badge supports a high-confidence
`dissonance` classification; Tier without the badge still remains `unknown`.
The collector does not probe the disabled Attack menu or treat a failed action
as identity evidence; on Attack Dissonance it records Damage Slider as
unavailable because that control cannot be inspected during the run.

Home-only facts use a second phase after natural completion. No Strategy forces
full structured Game Over capture and the Home terminal action, even if the
process was launched with fast Game Over capture. At verified Home
`NEW_BATTLE`, the runtime reads the three currently supported Free Upgrade lock
details with `enforce=False`, so checkbox state is observed but never changed.
It records the Workshop preset, returns Home, opens Cards, expands the Home
menu, independently verifies its Perks item, and opens the configuration panel
itself. The runtime selects the read-only First Perk, Ban Perks, and Auto Pick
tabs, scrolls each to its verified edge, OCRs the dark selected rows
independently of the brighter available rows, closes the panel, and revalidates
Home `NEW_BATTLE`. The same battle record is atomically updated after the lock
phase and again after Perks capture. Each field retains its source, confidence,
phase, and observation timestamp; uncertain parsing retains raw page images
instead of manufacturing a structured value.

Pause continues to block every inventory input. An interrupted pass resumes
from a known read-only screen or restores verified Home before retrying its
stage. Game Over `WAIT` must first receive an actionable direction; No Strategy
then overrides Retry to Home so a new battle cannot start before its Home-only
evidence is attached.

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
  resolved configured intent, separately sourced observed run configuration,
  observed preflight/runtime evidence, and derived metrics. Consumers must read
  these fields instead of relying on a terminal screenshot.
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
- Template-backed actions always rematch immediately before dispatch and use
  the actual matched bounding box.
- Moving elements may continue to use match-region centers after a fresh match.
- Static coordinates and dynamically calculated points are never action
  authority by themselves. A non-template runtime tap requires a complete
  current frame, a bounded target region containing the point, and a
  target-specific predicate that reidentifies what will be tapped. Legacy
  direct-region center lookup remains compatibility/tooling geometry only.
- Reusable initial-frame authority is limited to two bounded, time-critical
  purchase blocks. EHLS/EALS may keep tapping one verified upgrade box while a
  raw capture is in flight or until the live stream advances. Damage Slider may
  match one direction arrow and reuse that point for the exact computed batch.
  Both paths reacquire authoritative result evidence after the batch; the
  reusable-authority API is statically allowlisted to those two modules.
- The bounded in-battle floating-gem sweep remains the explicit blind runtime
  exception: the moving gem cannot be reacquired reliably, and its dedicated
  tapper acts only while automation remains `RUNNING`. Operator-invoked gesture
  tuning may use its separately named unchecked tooling path with a recorded
  reason.
- Every ordinary live action requires fresh source-state evidence immediately
  before the input. A bounded urgent block requires fresh evidence before
  issuing its reusable authority. Transition frames and unrelated stale
  screenshots cannot authorize actions.
- Farm configuration is inspection-first and profile-driven. A setting may be
  corrected during the active run only when the profile explicitly owns that
  setting and its runtime contract declares the transition safe. Session
  preflight consumes fresh Home proof that Poison Swamp Stun is `off`. Complete
  Home setup selects Workshop Ultimate Upgrades, OCR-localizes the Poison Swamp
  card, opens only its isolated non-purchase icon, corrects a verified `on`
  checkbox, and returns to Workshop. An attached run without boundary proof
  retains the guarded in-battle compatibility route, which returns to
  `RUNNING/UW_MENU` without Surrender or a Home transition. New-run
  initialization owns Damage Slider enforcement: it requires
  `RUNNING/ATTACK_MENU`, opens the freshly matched Damage control, and uses
  authoritative panel and percentage evidence to compute a bounded
  same-direction batch only for an exact power-of-ten exponent gap. The
  direction arrow is matched once on that same evidence frame and remains
  authoritative for the computed batch. The runtime then reacquires settled
  OCR evidence, recomputes any remaining gap, requires strict progress and a
  verified final value, and returns to `RUNNING/ATTACK_MENU`. Unknown sequences
  fall back to single-step feedback; unknown or incomplete evidence remains
  blocked.
- Complete no-battle setup owns every supported profile check available from
  verified Home `NEW_BATTLE`: Cards, Workshop and its Free Upgrade locks,
  Poison Swamp Stun, Bots, Guardians, and Modules. It retains screen-derived
  configuration evidence for session preflight, which consumes that boundary
  proof and checks only battle-only settings instead of leaving the newly
  started run to repeat Home checks. Target Priority records
  `battle_only_control` at Home and remains unsatisfied until the generated
  `RUNNING` action observes or enforces it; there is no Home Target Priority
  tap. Attaching to an existing battle without boundary proof retains the
  guarded read-only compatibility route. Home-only Free Upgrade locks remain
  deferred there: they record `unavailable_deferred` without a pass, failure,
  Home repair, or Surrender request; Poison Swamp Stun falls back to its guarded
  in-battle detail check; Home `RESUME_BATTLE` preserves the attachment, and
  the Home-owned gates rearm at the next genuine `NEW_BATTLE` boundary.
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
  suppresses plan rules tagged `run_initialization` or `session_preflight`,
  except an explicitly declared observer-only attachment check such as
  Tournament preflight.
  It does not seed their completion variables. Game Over, Tournament Results,
  or Home `NEW_BATTLE` arms the gates, and the next `RUNNING` observation emits
  the normal run-start hooks. Home `RESUME_BATTLE` and transient Unknown states
  preserve the attachment.
- Explicit mid-run strategy adoption uses the same attachment boundary. Fresh
  `RUNNING` or Home `RESUME_BATTLE` evidence may replace normal strategy
  behavior and report identity without a restart, but run initialization,
  session preflight, and Home-only checks stay deferred, except for an
  explicitly declared observer-only attachment check such as Tournament
  preflight. A request encountered at Home `NEW_BATTLE` follows normal boundary
  replacement instead and runs the complete startup-gate sequence.
- Process replacement must verify the existing owner and safe UI boundary,
  then verify the replacement PID, refreshed lock, startup log, control
  consumption, and first state report.
- The guarded active-battle reload makes that contract executable. A refreshed
  same-state Pause directive causes the current runtime to acknowledge intent
  and force its next captured frame into the status stream. Only a fresh
  `RUNNING` result from the systemd MainPID's held ADB lock may cross the stop
  boundary. The replacement launches once with `next_run`; the persistent
  next-start policy is restored immediately after systemd copies its launch
  environment. Normal control intent returns only after the replacement proves
  its distinct PID, lock, attached startup, Pause consumption, and first
  observation. Any failure after Pause preparation begins remains paused.
- Remote lifecycle control is limited to the configured
  `thetower-automation.service` systemd user unit. A start crosses the process
  boundary under persisted `PAUSED` and may publish `RUNNING` only after the
  unit is active. A stop persists `STOPPED` before systemd signals the unit;
  guarded active-battle reload retains `PAUSED` across its stop/start boundary.
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
