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
`Farm` Cards, Workshop, and Bots presets; Demon Mode automatic recharge
activation and Nuke manual-after-recharge activation; Shockwave Size, Bounce
Shot Targets, and Bounce Shot Range Free Upgrade locks; Guardian chips; Auto
Pick Perks; and Ultimate Weapon controls. Compact Tier profiles cannot override
those invariants.

Only Modules, Damage Slider, Orb Distance, and Target Priority vary by Tier or
experiment. Every compact Farm profile names all four and assigns one of these
policies:

| Policy | Runtime contract |
| --- | --- |
| `enforce` | Inspect and require the resolved value; a mismatch blocks unless an explicit safe repair contract owns the transition. |
| `observe` | Require authoritative observation and record the resolved reference and evidence; confident differences do not block or change the setting. |
| `preserve` | Do not inspect or change the setting. |

Tournament assigns `observe` to Modules with `tournament_standard` as its
reference. A confidently identified difference is an experimental variation,
not a failed invariant: it is named in the successful preflight result and
retained with the run. Missing or ambiguous slot identity remains incomplete
evidence. Tournament Cards, Workshop, Bots, Guardians, and Ultimate Weapon
controls remain enforced settings.

Named module, Orb Distance, and Target Priority presets are resolved at build
time into an explicit self-contained strategy plan. Modules are checked from
verified Home `NEW_BATTLE` before Battle may start. Module replacement treats
level as slot-owned state: every occupied replacement requires a verified
level transfer, and a Primary/Assist cycle is resolved through a verified
level-1 same-family intermediate instead of Unequip. The numeric equipped
levels visible on the overview are not currently OCRed, threshold-checked, or
retained in preflight/battle evidence. Inventory candidates require an
aligned icon match with configured confidence and margin plus exact detail
name/action/level evidence; unexpected transfer prompts and unsettled
overviews fail closed. Target Priority and Orb Distance are not Home controls:
their Home-boundary evidence remains explicitly deferred, and their policies
are checked after the new run reaches `RUNNING`. Each generated Orb Distance
action carries the complete configured preset set. The authoritative observed
Attack Range selects its matching Extra/Workshop pair; a readable Range
outside that set is preserved as an operator experiment without opening
Distance Adjuster. Unreadable Range evidence still blocks. Tier 18 Farm binds
Range `30.00m` to Extra `30.00m` and Workshop `39.00m`; Tier 18 and Tier 19
Farm both enforce the observed configured Range pair. If a
freshly matched arrow is unavailable or one verified tap leaves its value
unchanged, the runtime closes Distance Adjuster so its automatic pause no
longer freezes combat, waits for the running wave to advance, and retries from
fresh panel evidence. The wait and every new panel session recheck runtime
action authority. Damage Slider `observe` and `enforce` policies resolve an
explicit percentage; Tier 18 enforces `1E-22%` during every new-run
initialization after the time-sensitive EHLS/EALS setup. `YamlStrategy`
exposes the plan's resolved `run_configuration` generically, and Game Over
records copy that snapshot into the versioned battle JSON. Runtime code does
not inherit configuration or branch on a Farm strategy name.

### Player-save observation channel

`playerInfo.dat` is a read-only configuration observation channel, not action
authority. A decoder is selected only by an exact `(dataVersion,
versionNumber)` tuple and must also pass that mapping's root-class, required
field, and array-length signature. Unknown tuples, changed signatures,
incomplete mappings, stale snapshots, and save/profile differences all route
the affected setting through its existing UI check.

Mappings have an explicit `candidate` or `validated` maturity. Candidate
mappings always require a full UI audit, even when the save agrees with the
profile. Promotion to `validated` requires comparison with authoritative UI
evidence from the same game version. A validated exact save match may
eventually avoid that check's navigation, but scheduled UI audits remain
available and the UI implementation is retained as the permanent fallback.
Any automation repair is still verified through fresh UI evidence; a save does
not authorize a tap or prove the immediate result of one.

ADB acquisition requires two identical consecutive reads before decoding. The
container size, gzip integrity, NRBF root, exact version identity, and
structural signature are validated before mapped values are published. Reports
retain only the source hash, version metadata, mapped configuration, and a
redacted profile summary; account identifiers and the raw save are not copied
into runtime evidence. The component contract and version-update procedure are
in [`../modules/player_save_import.md`](../modules/player_save_import.md).

Game speed is a global battle-only invariant with persistent operator intent
independent of strategy and ADB target. Numeric selections from `x0.0` through
`x6.0` are exact targets. `x6.3` has `maximum_available` semantics because the
same visible `+` control can stop at `x5.0` without the Game Speed perk or
`x6.3` with it. Merely reading `x5.0` does not prove maximum: the guard sends
one verified `+` input and re-reads. No change at `x5.0` confirms the current
ceiling; a perk-enabled control advances toward `x6.3`. Once the no-perk
ceiling is confirmed, that proof is retained while the control remains at
maximum because the game automatically advances it when the perk raises the
ceiling. The periodic guard requires authoritative `RUNNING` evidence, reads
the localized value, and independently verifies the visible plus or minus
glyph before selecting a direction. Every tap is followed by settled fresh
OCR; an unexpected unchanged value, wrong-direction transition, crossed exact
target, missing control, Pause, or target change fails closed. Farm defers the
guard while either urgent EHLS/EALS purchase remains incomplete; attachment
and non-Farm profiles may correct speed as soon as their runtime policy grants
handler action authority. The completed record stores the current target,
target semantics, and per-battle target timeline separately from derived
effective game speed. Each periodic status frame also reads the visible speed
without sending input. That observation is published separately from the
target and retained alongside the contemporaneous Coins/min sample. A direct
manual change in the game is therefore observable drift, not a new directive:
the periodic guard restores the selected target. Selecting a new target in the
control surface re-arms the guard immediately and records its approximate wave
in the target timeline.

Automatic-Perk profiles maintain a run-scoped selection timeline from the
compact `current wave / next Perk wave` control. A scheduled pair is usable
only when the next wave is later than the current wave and no more than 250
waves ahead; a boundary transition also requires the displayed current wave to
have reached the previously armed wave. Implausible OCR is ignored and retried
without panel input. Three consecutive invalid pairs produce one persistent
warning, while the next valid pair records recovery. A previously armed value
that is now implausibly far ahead is discarded and re-armed from stable valid
evidence instead of holding the observer indefinitely.

Every selection check scans the selected list newest-first until the first row
whose family and displayed value still match the persisted snapshot. Rows
above that unchanged boundary are the complete changed prefix; the usual
single-selection case therefore finishes in the top viewport. If no unchanged
row remains because every prior row changed, the same guarded traversal
continues to the bottom and uses a complete-list diff. Before Perk Wave
Requirement reaches `-75%`, one boundary's changed prefix remains a
simultaneous unordered cascade. After `-75%`, complete boundary observations
can use newest-first order to reconstruct chronological singleton batches when
there is exactly one distinct change per boundary. A repeated leveled family,
an unseen boundary, or any other count mismatch remains an explicit interval
aggregate without invented per-wave attribution.

The tracker atomically checkpoints its selected-family snapshot, batches,
armed progress, pending capture, and owned Perks-panel route beside the active
control file. The checkpoint is keyed to the durable Current-run activity
identity. A process replacement restores state only when that identity still
matches and treats the outage as an unobserved top-bar interval until stable
progress is confirmed. A different or unreadable identity starts an unknown
mid-battle baseline instead of importing another battle's Perks. Pause
continues to block every panel input while stable top-bar observations update
the pending boundary set.

## Tournament exclusive validation and observer profile

`Tournament` owns a one-shot exclusive validation before it becomes a passive
single-battle observer. Its generated plan declares Tournament Cards, Demon
Mode automatic recharge activation, Nuke manual-after-recharge activation,
Tourney Workshop, Amplify Bots, Attack/Ally/Scout Guardians,
Tournament/Milestone modules, Poison Swamp Stun `on`, Damage Slider `100%`, the
Range `98.38m` Orb Distance pair Extra `87.16m` / Workshop `80.37m`, all nine
Ultimate Weapons, and Spotlight Missiles. Tournament battles have no Perks, so
Perks and Auto Perks are outside this contract.

Every explicit Tournament selection or managed process Start creates a durable
validation request tied to the strategy request identity and the complete
generated-plan fingerprint. A pending request is one-use authorization, not a
recurring strategy permission. An unattended restart cannot recreate it. The
receipt records `pending`, `claimed`, `running`, `cleanup`, and terminal result
states, with the runtime ID, PID, ADB target, and deadline attached before the
first battle input.

At verified Home `NEW_BATTLE`, the profile first completes every declared
no-battle check: Tournament Cards, the Demon Mode/Nuke recharge activation
modes, Tourney Workshop, Amplify Bots, Attack/Ally/Scout Guardians, the
Tournament module loadout, and Poison Swamp Stun `on`. Damage Slider, Orb
Distance, and Ultimate Weapon enablement remain explicitly deferred because
their authoritative controls are battle-only.
Exclusive validation claims staged one-run waivers tied to the same Tournament
strategy request before Home setup, so a configured check skip applies to this
path as well. An unwaived failed Home check consumes the request with its reason
and never starts a battle. Once Home preflight is complete, the runtime
atomically claims the matching receipt and then uses a fresh verified control
to start exactly one ordinary `NEW_BATTLE`. It rejects Home `RESUME_BATTLE` and
never opens the Tournament screen or starts a Tournament battle.

The disposable ordinary battle bypasses EHLS/EALS initialization without
seeding either completion flag. It does not toggle Auto Perks. Session
preflight enforces Damage Slider `100%`, reads Attack Range, enforces the
matching configured Orb Distance pair for `30.00m` or `98.38m`, preserves any
other readable experimental Range, then verifies all configured Ultimate
Weapon primary toggles and Spotlight Missiles. A
conclusive pass or failure, or the bounded timeout, moves the same receipt to
cleanup before any terminal input. Surrender is allowed only while the current
runtime/ADB owner still matches and fresh `RUNNING` evidence excludes
Tournament identity. Cleanup must reach Game Over and verified Home
`NEW_BATTLE` before the receipt reports ready or the failure reason. Process
replacement, owner mismatch, Tournament identity, a resumed/pre-existing
battle, or an ambiguous transition fails closed without inheriting Surrender
authority.

After a ready result, the control surface publishes a one-shot operator launch
prompt tied to that exact ready receipt and configuration fingerprint. The
prompt reminds the operator to set Target Priorities for the displayed
Tournament Battle Conditions when the battle begins; those controls are
in-battle, and their selection is not yet automated or included in validation.
**Decide later** closes the prompt without changing the receipt. **Cancel
launch** consumes only the automatic launch offer while retaining the
successful validation result. **Start Tournament** is explicit authorization
for the matching live runtime to enter and start one Tournament battle.

Start confirmation does not repeat Home or session validation. The API first
checks that the matching receipt and configuration are still current, the
runtime is active and has acknowledged `RUNNING`, and a fresh observation is
Home or the Tournament entry screen. The runtime then checks those facts
against a fresh screenshot and atomically claims the launch before its first
input. From Home it taps only a verified `NEW_BATTLE` control followed by the
OCR-confirmed Tournament `OPEN` control; from a verified Tournament entry it
continues directly. It starts the battle only through the OCR-confirmed
Tournament `BATTLE` control. Ownership, Pause, and current-request identity are
rechecked before every tap. Timeout, process replacement, owner mismatch,
supersession, unexpected battle identity, or any ambiguous transition fails
closed without further input. If the operator starts the Tournament manually
while the offer is pending, the runtime consumes the offer as a manual start
and continues observing normally.

The genuine Tournament run performs the standard run-initialization route at
its fresh boundary, maxing EHLS first and EALS second. It then retains the
validation battle's session evidence and becomes passive except for game-speed
maintenance, ad-gem collection, and terminal-result handling. An ad gem starts
the same bounded floating-gem sweep used by normal battles; the profile does
not run an independent or continuous floating-gem handler. No Tournament
battle gains validation-battle Surrender authority.

Automatic validation of an already-running Tournament remains observer-only
and does not use the exclusive validation receipt. Without Home boundary
evidence, it inspects Cards, Ultimate Weapons, Modules, Bots, and Guardians in
battle. Workshop is the only check that takes resumable Exit Battle → Go Home.
The automatic attachment path suppresses the profile's attachment-time
configuration actions, never selects a Home preset or equips a loadout, and
must verify that Resume returns to the same Tournament. The separate guarded
process-reload workflow retains its explicit `next_run` compatibility policy;
it is not the user-facing validation choice.
A mismatch is retained as session evidence but cannot request Home repair or
block result capture. Observation-only mismatches complete the one-shot pass
without an operator decision or run-scoped waiver; they cannot make the
inventory traversal repeat. This attachment path never gains Surrender
authority.

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
- Completed records remain durable until the operator explicitly discards one.
  Discard moves the exact JSON/Markdown pair into an expiring quarantine rather
  than unlinking it immediately. The control service permanently purges a
  validated quarantine package after its recorded deadline; the default
  retention period is 30 days.
- Generated failure/OCR screenshots and post-run observation pages are bounded
  independently from canonical records. The runtime prunes files older than 30
  days and then the oldest files above 1 GiB per owned evidence directory,
  running once at startup and every six hours. Symlinked trees, canonical
  regression fixtures, and repository-relative development evidence named by
  `config/protected_artifacts.txt` are outside deletion authority. Protection
  is resolved before a sweep begins; a missing or invalid manifest fails closed
  without touching any retention root.
- `actions.log` is size-rotated before an atomic log group is appended. The
  default keeps a 16 MiB current log plus five numbered backups; an already
  oversized log contributes only its most recent complete lines to the first
  bounded backup.

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
- Orb Distance enforcement first locates the Attack Range tile and requires
  authoritative OCR, with one adaptive-threshold retry for the dim value on a
  Maxed tile. The observed Range selects a matching entry from the complete
  generated preset set. A readable unconfigured Range records
  `unconfigured_range_preserved` and completes without Distance Adjuster input;
  unreadable evidence remains blocked. For a configured Range, the runtime
  opens the freshly matched in-run Distance Adjuster, OCRs both values, and
  matches each direction arrow immediately before one tap. Every step
  reacquires the panel, requires the selected row to move strictly closer to
  its target, and stops on unknown, unchanged, cycling, or non-progressing
  evidence. An unavailable arrow or unchanged value closes the automatically
  pausing panel, waits with combat running until the wave advances, and
  retries in a bounded number of fresh panel sessions; runtime action
  authority is rechecked throughout the between-session wait. Success requires
  both exact values and verified return to the running side menu.
- Complete no-battle setup owns every supported profile check available from
  verified Home `NEW_BATTLE`: Cards and the declared Demon Mode/Nuke recharge
  activation modes, Workshop and its Free Upgrade locks, strategy-declared
  Perk Bans and Auto Pick priority, Poison Swamp Stun, Bots, Guardians, and
  Modules. Card recharge traversal checks both unresolved Cards on the initial
  inventory frame and after every bounded upward or downward swipe, validates
  whichever is visible in any order, and stops without another swipe as soon as
  both have authoritative evidence. Inspection opens each exact inventory card
  through a verified long press, requires the matching detail identity and an
  authoritative checkbox state, changes only a mismatched checkbox, rechecks
  the requested state, and returns to the Cards inventory. Missing cards and
  ambiguous details fail closed. Perk configuration is changed only when the
  selected strategy declares both semantic lists. Ban repair completes before
  Auto Pick: extra selections are removed from the fixed Selected Perks block,
  while only missing required bans search the Available list. Each Ban toggle
  and Auto Pick move recaptures the panel immediately before input, uniquely
  reacquires the same semantic row at its settled coordinates, and requires
  strict transition evidence. Auto Pick then rebuilds semantic rank from the
  top and requires exactly one-rank upward progress after every tap. A final
  exact comparison remains mandatory; ambiguous OCR, an unavailable perk, or
  non-progress blocks New Battle. Persistent control is synchronized before
  every Home setup tap or swipe. Pause holds the workflow action-free, and
  Resume restores verified Home before a fresh setup pass. The setup retains
  screen-derived configuration evidence for session preflight, which consumes
  that boundary proof and checks only battle-only settings instead of leaving
  the newly started run to repeat Home checks.
  Target Priority records
  `battle_only_control` at Home and remains unsatisfied until the generated
  `RUNNING` action observes or enforces it; there is no Home Target Priority
  tap. Attaching to an existing battle without boundary proof retains the
  guarded read-only compatibility route. Home-only Free Upgrade locks remain
  deferred there: they record `unavailable_deferred` without a pass, failure,
  Home repair, or Surrender request; Poison Swamp Stun falls back to its guarded
  in-battle detail check; Home `RESUME_BATTLE` preserves the attachment, and
  the Home-owned gates rearm at the next genuine `NEW_BATTLE` boundary.
- A confident Home-only mismatch does not immediately authorize repair. The
  generated Farm plan declares a three-attempt threshold; the read-only
  session preflight retries after its existing cooldown while the failed-check
  identity is unchanged. Success clears the series, and changed failure
  identity restarts it. Only an exhausted series may request one app-owned
  stop/repair/restart sequence. Ambiguous or unknown module identity and other
  non-Home repair classes remain blocked. The matcher reports evidence but
  never directly authorizes an equipment action.
- A guarded configuration repair must reach verified Home `NEW_BATTLE`, use
  fresh detail/name and action guards for module changes, reapply the complete
  profile-owned no-battle setup, start the next battle, and require fresh
  session preflight evidence before normal handlers regain authority.

## Process and control ownership

- The persistent control file is authoritative operator intent.
- A non-blocking OS lock keyed by ADB target prevents competing runtimes from
  acting on the same device. Its metadata is `held` with an owner PID while
  acquired and is rewritten to `released` with no PID on a clean release. A
  crash can leave `held` metadata after the OS lock disappears, so metadata
  alone is not proof that its PID is still alive.
- Pause blocks every strategy and handler action while allowing observation and
  status reporting.
- Control synchronization precedes capture, so an ADB outage cannot prevent a
  Pause acknowledgement or a paused target-handoff request. The watchdog may
  retry connectivity while paused but may not foreground or restart the game.
- Frame capture and the watchdog share one thread-safe, target-keyed ADB
  connection coordinator. A known disconnection suppresses screenshot commands
  and repeated low-level failure entries while reconnect attempts follow a
  bounded schedule; the main loop continues its short control-poll cadence.
  Persistent degradation produces transition/reminder warnings, and recovery
  is complete only after a supported fresh frame succeeds. Malformed captures
  while transport remains connected retain their normal diagnostics.
- The native Windows control surface owns API local forwarding and ADB reverse
  forwarding as separate OpenSSH processes. The ADB process requests only
  `127.0.0.1:<linux-port>` and targets the independently configured Windows
  BlueStacks listener. Windows-listener presence and accepted SSH forwarding
  are separate evidence; neither proves a current emulator screen or runtime
  owner. `ExitOnForwardFailure` preserves bind/policy diagnostics. A forwarding
  conflict pauses automatic ADB reconnect, while other unexpected exits use
  bounded backoff without disturbing the API process. The Windows listener
  port, Linux-exposed per-PC port, and managed runtime ADB target remain
  explicit independent settings even when all three normally use 5555.
- An acknowledged paused runtime may migrate its localhost ADB target without
  process replacement. It acquires the new per-target lock first, temporarily
  selects that endpoint, requires successful connection and supported capture,
  then releases the old lock and acknowledges the directive. Failure restores
  the previous target, its independent reconnect state, and Pause. Existing
  mission, strategy, and gate state stays in memory throughout.
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
- Current-run activity continuity is verified independently of process and
  strategy attachment. Each Home `NEW_BATTLE` scope records a fingerprint of
  the newest copied in-game Battle History report before launch. A replacement
  process compares that persisted baseline at `RUNNING`, Home
  `RESUME_BATTLE`, or a Battle History screen left open by an interrupted
  inspection. Equality preserves the scope; a changed report creates a new
  scope whose log boundary includes the continuity action. A readable identity
  is persisted with a run-ID compare-and-set so a stale inspection cannot
  overwrite a newer lifecycle boundary.
- Battle History continuity inspection has exclusive input authority while
  pending. Pause is checked before each input, all initialization, preflight,
  handler, and blind-tapper paths remain blocked, and restoration to the source
  battle or Home screen is required. If identity cannot be read after safe
  restoration, attachment fails toward a conservative new scope; if
  restoration itself is unverified, the route retries without releasing other
  inputs.
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
