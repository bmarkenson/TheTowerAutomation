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
policy. Field and classification details are in the
[UI detection schema](../reference/ui_detection_schema.md).

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
conditionals or duplicated expanded YAML. Use the
[YAML strategy reference](../reference/yaml_strategy.md) for plan ownership.

## Farm profiles and loadouts

This section describes the current runtime inputs and compatibility format.
The target GUI authoring model—including sparse versioned bases, explicit
inherit/override/ignore semantics, reviewed rebasing, and self-contained
publications—is defined in
[`strategy_authoring.md`](strategy_authoring.md). Until that model is migrated,
the baseline and complete-snapshot behavior below remains authoritative.

`Farm` is the recurring automated run profile. Glass Cannon describes a
gameplay style, not a runtime profile: it may also be used for tournaments,
milestones, or some Dissonance runs, so it does not own Farm configuration.
Legacy `gc*` strategy names remain aliases during migration.

The Farm baseline supplies the defaults for every Farm run: the `Farm` Cards,
Workshop, and Bots presets; Demon Mode automatic recharge activation and Nuke
manual-after-recharge activation; Shockwave Size, Bounce Shot Targets, and
Bounce Shot Range Free Upgrade locks; Guardian chips; Auto Pick Perks; an
independent First Perk Choice of `perk_wave_requirement`; Perk Bans and
priority; and Ultimate Weapon controls. First Perk Choice is not inferred from
the first Auto Pick row and can change without changing that order. Tournament
declares no Perk requirement. A custom compact profile
publishes a complete `setup.settings` snapshot over those defaults so edits do
not depend on a later baseline change. The shared builder still validates each
value against implemented runtime authority before it can publish a generated
plan.

Custom profiles may also declare profile-owned `setup.skipped_checks`. Unlike
an operator's one-run waiver, this is durable policy, participates in the
strategy fingerprint, is re-applied at every boundary, and is retained in the
resolved run configuration. The initial allowlist is deliberately limited to
`auto_pick_perks`, `perk_bans`, and `perk_auto_pick_order`. A skipped Perk
control receives no corrective input; skipping both semantic lists also avoids
opening the Home Perks configuration screen. The configured values remain in
the profile so removing the skip restores enforcement without reconstructing
the lists.

Tier loadout variation is expressed through Modules, Damage Slider, Orb
Distance, and Target Priority. Every compact Farm profile names all four and
assigns one of these policies:

| Policy | Runtime contract |
| --- | --- |
| `enforce` | Inspect and require the resolved value. Repair a mismatch immediately when the current boundary already makes that safe; otherwise flag the exact difference and continue degraded. |
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
Attack Range selects its matching Extra/Workshop pair. That observation may
come from the guarded UI or from one exact-version save calculation using the
active total Workshop level, selected/researched Range lab, live Range Card,
Cannon Module bonuses, native binary32 compression, and display formatting.
Save evidence is consumable here only with `current_active_round` scope and a
maxed current Range level; incomplete, mutable, out-of-round, malformed, or
forward-version evidence retains the UI. A readable Range
outside that set is preserved as an operator experiment without opening
Distance Adjuster. Unreadable Range evidence skips that adjustment and is
retained as degraded validation evidence. Tier 18 Farm binds
Range `30.00m` to Extra `30.00m` and Workshop `39.00m`; Tier 18 and Tier 19
Farm both enforce the observed configured Range pair. If a
freshly matched arrow is unavailable or one verified tap leaves its value
unchanged, the runtime closes Distance Adjuster so its automatic pause no
longer freezes combat, waits for the running wave to advance, and retries from
fresh panel evidence. The wait and every new panel session recheck runtime
action authority. Damage Slider `observe` and `enforce` policies resolve an
explicit percentage; Tier 18 enforces `1E-22%` during every new-run
initialization after the time-sensitive EHLS/EALS setup. While either Farm
level-skip completion flag remains false, the same priority hold defers
other battle-bound work. An already gold-boxed pair therefore retires the
priority immediately. `YamlStrategy` exposes the plan's resolved
`run_configuration`
generically, and Game Over records copy that snapshot into the versioned battle
JSON. Runtime code does not inherit configuration or branch on a Farm strategy
name.

### Player-save observation channel

`playerInfo.dat` is a read-only configuration observation channel, not action
authority. A decoder is selected only by an exact `(dataVersion,
versionNumber)` tuple and must also pass that mapping's root-class, required
field, and array-length signature. Unknown tuples, changed signatures,
incomplete mappings, and stale snapshots are non-authoritative and route the
affected setting through its existing UI check. A complete, validated, exact
save/profile difference is instead a trusted mismatch: it queues only that
check's guarded UI verification/repair path.

Mappings have an explicit `candidate` or `validated` maturity and an exact
per-check validation allowlist. A candidate mapping may supply authority only
for an allowlisted check whose complete evidence matches the profile after an
explicitly proven save-serialization boundary. Unvalidated checks remain in
full UI audit even when their candidate values happen to agree. Whole-mapping
promotion requires every published complete check to meet the same standard.
Scheduled UI audits remain available, and every UI implementation is retained
as the permanent fallback.

The implemented Home-preflight decision is per check:

```text
verified NEW_BATTLE -> proven app-pause flush -> stable exact-version pull
    global trust failure                 -> invalidate the snapshot; use UI or continue degraded
    complete + allowlisted + exact match -> accept the saved state; skip that UI route
    complete + allowlisted + mismatch    -> queue only that check's existing UI path
                                               |-- UI also mismatches: guarded repair + UI verify
                                               `-- UI already matches: contradiction; invalidate
    unsupported/incomplete/forced audit  -> run that check's existing UI path
```

`PlayerSavePreflightCoordinator` owns that decision at an ordinary exact Home
boundary. The default `save_first` policy records the current runtime,
preflight operation, exact ADB target and generation, selected strategy,
and complete resolved configuration fingerprint; verifies Home `NEW_BATTLE`;
honors action authority; backgrounds the app to Android Home; uses the existing
two-identical-read pull; decodes only in memory; restores the app; and requires
the same ownership plus two stable Home `NEW_BATTLE` frames. `force_ui` skips
the save lifecycle, while `comparison_audit` retains normalized comparison
evidence but deliberately suppresses no UI route. These policy modes are
strategy runtime policy, not observation-collector switches.

One snapshot reconciles every requested eligible check atomically, but each
decision remains independent. Exact matches may omit redundant Home
observation for Cards, recharge modes, Workshop, the required three-lock Farm
subset, Bots, independent First Perk Choice, Bans, ranked Auto Pick prefix,
Guardians, an exact enforced Farm Module loadout, a complete mapped
observation-only Tournament loadout, and Poison Swamp Stun. The
34-entry Auto Pick field must contain every mapped Perk ID exactly once,
including `11=unlock_random_ultimate_weapon`; no sentinel is present. Only its
first 18 entries are ranked. The 16-entry inventory tail is never compared as
priority, and a configured list may be a shorter required prefix. Unknown IDs,
duplicates, changed length, or changed membership restore UI.

Free Upgrade locks retain their strategy-declared required-subset contract:
every requested lock must be set, but additional normalized locks are
unmanaged evidence, do not invalidate the match, and never authorize an
unlock. The accepted extra `Health` bit is therefore reported but left alone.
Malformed arrays, non-booleans, unknown requested indices, or a missing
required lock retain the complete existing UI validation/repair path.

The monolithic Ultimate Weapon check remains unvalidated. Its normalized
components fail independently: Poison Swamp Stun supports both calibrated
inverted boolean polarities; primaries support only the exact value-scoped
state in which all nine weapons are present, unlocked, and on; Spotlight
Missiles supports only the exact unlocked/on state. A mixed/off primary request,
Spotlight Missiles off, malformed structure, or unsupported value restores only
the applicable shared-screen UI work, where any component actually observed
can still contradict carried evidence. Module decoding separates a canonical
global `infoIndex -> (name, family)` identity catalog from the exact structural
Primary/Assist slot map; it is not a generic inventory map. The catalog names
all 24 current Modules—six per family—under the
[player-save mapping owner](player_save.md#current-status). Its explicit
`canonical_global_same_family` authority permits any canonical name in either
exact slot of its family. Its separate explicit-nil contract represents an
uninstalled Primary or unlocked Assist assignment as canonical `empty`.
Farm's four Primary and four typed Assist assignments remain enforced. The
Tournament reference is also fully mapped:
Primary Amplifying Strike (`45`), Orbital Augment (`46`), Project Funding
(`43`), and Dimension Core (`38`); Assist Being Annihilator (`9`), Anti-Cube
Portal (`20`), Singularity Harness (`30`), and Harmony Conductor (`39`). A
same-run stable-save/UI pairing additionally established armor Primary
Anti-Cube Portal (`20`) and armor Assist Space Displacer (`19`).

Exact slot, family, role, canonical name-or-empty assignment, unlocked Assist
state, and complete structure must agree before save evidence can replace
Modules observation. Installed names must be unique; `empty` may repeat.
Tournament's `observe` policy records a fully decoded difference from
`tournament_standard` as `save_observation`; it neither fails the gate nor
authorizes a repair. An `enforce` policy still requires exact equality.
A canonical value in either same-family role may match without another
placement campaign. A genuinely unknown ID produces identity-review evidence
at a complete, exact, pre-mutation UI observation and retains the full route.
A later local confirmation makes only that identity available for diagnostics;
same-family authority begins only after canonical integration. Unknown names,
cross-family values, duplicate installed names, enforced mismatches, and
malformed or partial structures likewise retain UI. An overview classified only
as `not_ancestral` is not empty evidence because it may contain a lower-rarity
Module. These facts do not map rarity, levels, stars, effects, substats,
inventory semantics, GUIDs, or private record values. Orb Distance and Damage
Slider retain their independent exact-value authority.

Save-backed Home, carried session evidence, and No Strategy inventory reporting
all consume the same normalized eight-slot assignment. On a verified boundary,
an exact match or a complete `observe` result therefore needs no Modules
navigation, including explicit empty slots. UI remains exceptional for save
acquisition/version/shape failure, an unknown identity, an enforced mismatch,
or explicit `force_ui`/comparison audit. The repair path may causally unequip a
freshly reidentified occupied slot when the requirement is `empty`; it does not
guess that an initially non-Ancestral-looking slot is empty.

Inventory fallback first checks the normal fixed grid, then—only when no
authoritative target match exists—searches variable vertical centers left by
inertial scrolling. The requested icon must still win the complete catalog's
confidence/margin comparison, retain Ancestral frame evidence, and open an
exact name/rarity/action detail before any equip input.

Session-only accepted decisions become typed, single-use carry across either
the exact next runtime-owned Home `NEW_BATTLE` launch or the exact same-process
successor of one natural Game Over -> direct Retry transition. The current
carry covers Cards, Workshop, Bot and Guardian selections, Free Upgrade locks,
Modules, Auto Pick enabled `true`, a complete exact ten-ID Target Priority
order, Damage Slider, Orb Distance, the all-nine-primary-on aggregate,
Spotlight-Missiles-on, Poison Swamp Stun, and the other exact Home sections used
by the later consistency check.
The version-1073 Target Priority map is `0=Closest (Default)`, `1=Basic`,
`2=Fast`, `3=Tank`, `4=Ranged`, `5=Boss`, `6=In Spotlight`, `7=Protector`,
`8=Elites`, and `9=Fleets`; complete membership, uniqueness, and ordered policy
comparison remain mandatory.

Home carry requires the same runtime/preflight operation, target generation,
strategy/configuration fingerprint, exact `NEW_BATTLE` control,
and a verified authorized dispatch. No dispatch leaves it pending. An unstable
first `RUNNING` frame defers binding until a later stable frame from the same
transition. Binding is observation, not input, so initialization holds and the
`WAIT` terminal policy do not reject it. Direct Retry instead requires a typed
complete natural Game Over acquisition naming the same runtime, predecessor
active-round identity, target, and generation, plus an inactive terminal save.
The terminal bundle is reused for configuration/report projections; the
successor's first stable `RUNNING` boundary forces a new save and binds its own
active-round identity.

Pause still blocks every input and suspends unconsumed carry so later checks
need fresh save or UI evidence, but it does not quarantine the underlying
snapshot. Stop/process restart, attachment or competing workflow, manual or
ambiguous launch, target/context/configuration change, a wrong transition, or
a later unrelated battle discards that transition's carry. A changed
requirement routes only that check to UI; unsupported or incomplete evidence
already does the same. WAIT by itself does neither. A read-only UI verification
preserves unrelated carry. An actual configuration repair removes only the
affected check from carry and closes pre-action mapping correlation before
dispatch; unrelated accepted facts remain authoritative.

A pre-action snapshot never confirms the result of an input. The reconciliation
plan is frozen before setup input as independent accepted matches/observations,
trusted mismatches, and non-authoritative UI requirements. A trusted mismatch
selects only its existing UI path; that path must independently observe the
current value, establish a mismatch before mutation, remove that check from
carry, close pre-action mapping correlation, repair under its normal guards,
and verify the result. The repaired check is `ui_verified`, is not reclassified
as save-confirmed, and is not added to save carry. Home setup preserves
unrelated accepted decisions rather than reopening their UI sections.
Independently UI-verified sections remain available only through explicit
per-section UI provenance.

Acquisition, serialization, freshness, version/structure, ownership, and safe
source-restoration failures retain their established whole-boundary block or
all-check UI fallback. After a trusted snapshot exists, a failed transition
binding discards applicability without claiming that the snapshot is corrupt,
and a requirement-specific failure downgrades only that check. Authoritative
UI that contradicts a `save_match`, or UI that already matches a trusted saved
mismatch before this coordinator repaired it, is different: it quarantines the
whole snapshot and fails closed. Final consistency may combine unchanged
sections proven by accepted save evidence with repaired/inspected sections
proven by current UI evidence. A supplied UI screen is always evaluated;
missing screenshots are accepted only for the exact section/component carrying
bound `save_match` provenance, or `save_observation` solely for
observation-policy Modules. No disposition authorizes a tap, repair, lifecycle
transition, battle start, attachment, terminal binding, dispatch, or strategy
action.

ADB acquisition requires two identical consecutive reads before decoding. The
container size, gzip integrity, and object NRBF root are whole-document gates.
Observed version identity and manifest drift are provenance/diagnostics;
individual semantic claims validate their own dependencies before publication.
Preflight evidence retains only a redacted source fingerprint, observed version
and mapping metadata, normalized allowlisted decisions, the configuration
fingerprint, and redacted session/target-generation provenance. Account
identifiers, raw saves, decoded roots, arbitrary history, private values, and
raw exception text are not copied into preflight evidence. The component
contract and version-update procedure are
in [`player_save.md`](player_save.md).

#### Single-boundary acquisition fan-out

The completed acquisition stack has replaced the runtime's separately
composed save reads with the typed acquisition bundle defined in
[`player_save.md`](player_save.md#acquisition-provenance-and-temporal-authority).
`PlayerSaveParser` is the global in-process parse/projection API. `App` owns one
stateless parser and injects it into one `StablePlayerSaveAcquirer`, which owns
locking, exact target/generation checks, quiet stable transport, one decode,
root/byte disposal, timing, and redacted failure provenance. Forced
serialization, terminal projection, and the passive scheduler must receive
that shared acquirer or an already acquired bundle; they cannot silently
create an owner. Audit and metric consumers never request a read. One terminal
bundle feeds progression, one structural History transition, the candidate
semantic report, the Perk monitor, optional audit, and Tournament conditions.
The report transition may be staged in the activity-log ledger, but that
metadata never establishes a battle boundary or authorizes input. Typed
actual-loadout merge and the shared save-backed Perk monitor are implemented
in the later stacked phases.

The target flow is one acquisition per coherent boundary and any number of
independent projections:

| Boundary | Acquisition or reuse | Consumers | Failure policy |
| --- | --- | --- | --- |
| Home `NEW_BATTLE` before Start/Return | Perform one guarded `forced_serialization`, even when the configuration requirement set is empty; a report handoff may also be consumed from the same boundary. | The inactive runtime projection authorizes clearing retained battle identity; the same bundle supplies configuration and report projections. | An inactive proof is mandatory before Start. A safely restored transient failure is bounded and retryable; restoration, ownership, target, context, or control ambiguity blocks later input. |
| First stable `RUNNING` after Start, Retry, Enable, or Attach | Perform one guarded `forced_serialization` and compare its exact `ActiveRoundIdentity` with the durable battle-identity record. Home Resume is a two-proof route: force once before the Resume tap to identify its target, then rearm and force again on the first stable `RUNNING` frame before adoption. | `SAME_BATTLE` restores eligible identity-bound state; `LATER_BATTLE` discards old battle-local state and adopts the successor; the bundle also supplies actual-loadout, Perk-prefix, metric, audit, and report projections. | There is no History/UI substitute for battle identity. A safely restored transient failure is bounded and retryable; an active source without identity remains input-blocked. Restoration, owner, target, control, or uncertain-input ambiguity is catastrophic and may Pause. |
| `GAME_OVER` or `TOURNAMENT_RESULTS` | One lifecycle-bound `natural_boundary` bundle. | Profile progression, structural terminal transition, semantic completed report, Perk-window closure, optional audit projection, and Tournament conditions. | Projection or acquisition failure remains nonblocking and preserves the applicable Game Stats, Perks, or More Stats UI fallback. |
| Periodic passive observation | Every 300 seconds, the scheduler attempts one `passive_stable_read` against the current exact process/target/battle binding. The cadence is independent of forced serialization and prompt checkpoints. | Perks, active-run metrics, and optional audit receipts consume any newly serialized positive evidence; unchanged checkpoints are harmless. | An absent binding skips the read. Never treat the timer or a stable pull as proof that the game wrote recently, background the game, claim freshness/absence, or authorize input. |
| Perk selection or exhaustion checkpoint | The stable Perk top-bar observer may request one coalesced `passive_stable_read` without changing the periodic deadline. It does not force the game to serialize. | The Perk monitor benefits from lower observation latency; active-run metrics and optional audit receipts consume the same read-only bundle without requesting another read. | Drop or record the observation; never background the game, claim freshness/absence, or authorize input. |

The terminal structural projector validates the newest tail once. A successful
append or capacity rollover may become a normalized, one-use reporting handoff
in `activity_scope.json`, carrying only source/mapping identity and redacted
process, log-scope, target-generation, and terminal-boundary provenance. It
never persists the decoded snapshot or raw save. A missing, rotated, or
unwritable activity scope can discard this best-effort report metadata, but it
cannot block Home, Retry, Start, Attach, Return, save acquisition, or input.

Structural and semantic terminal outcomes remain independent. An unknown
`killedBy` or incomplete More Stats mapping still forces the report UI, but it
does not discard a structurally proven next-scope History baseline. Conversely,
a terminal bundle never satisfies the next battle's current-configuration
preflight: configuration may change after the terminal and still requires a
forced Home boundary.

No consumer reacquires data already represented by the bundle. In particular,
the Tournament Results handler receives either complete or explicitly
unavailable conditions from the terminal projection instead of performing a
second save read. The Perk and active-run metric monitors consume periodic and
Perk-requested passive, already-forced attachment, and natural terminal
bundles. The optional audit collector also projects those shared objects and is
neither an acquisition service nor an authority source.

`PlayerSaveObservationContext` is the neutral process/target/battle-identity
binding for passive fan-out; its activity-scope field is presentation metadata
only. The scheduler owns an independent periodic deadline and accepts only Perk
selection and exhaustion as prompt request reasons. It rechecks the
authoritative context—including ADB target generation and active-round
identity—after acquisition and before publication.
Each subscriber is
exception-isolated so a Perk projection failure cannot suppress metric or audit
consumers of the same object. The parser and acquirer retain no process-global
"latest snapshot" cache; every coherent boundary owns its explicit bundle.
The separate control-surface server does not pull or parse saves and no HTTP GET
triggers ADB work. Existing battle-detail APIs expose only persisted normalized
`active_run_metrics` after the runtime has established their authority.

`ActivityHistoryReporter` owns only this best-effort handoff publication and
validation; `utils.logger` owns bounded JSON detachment and atomic log-scope
mutation. The reporter has no acquirer, UI route, action hold, or lifecycle
decision. It causes zero save reads and zero Battle History navigation.

`activity_scope_run_id` is mutable log/report/presentation metadata. It never
grants or invalidates action, lifecycle, lease, save-fact, or battle-continuity
authority. Fresh UI/control evidence, exact runtime and target ownership, an
operation ID for the short pre-identity interval, and forced-save
`ActiveRoundIdentity` own those decisions. Whenever runtime needs current save
evidence it forces serialization immediately; periodic and explicit Perk
checkpoint reads remain opportunistic observations with unknown write lag.

#### Save-first active-round and terminal evidence

The normalized runtime-save model is a bounded semantic-evidence boundary
inside the global parser. Runtime projection schema 3 exposes only allowlisted
claims from legacy mappings and resolved semantic capabilities; it never
publishes the decoded root or an arbitrary `BattleHistoryEntry`. Whole-parse
failure is limited to unreadable/unstable transport, bounded container or NRBF
failure, non-object roots, and invalid checked-in registries. `saveRevision`,
round state, wave, Perks, active tallies, structural History, completed rows,
and terminal tally facts report independent status. A malformed shared scalar
therefore removes only its transitive dependents. An unknown Perk ID cannot
publish a partial inventory, while an unknown `killedBy` blocks only cause/full
report semantics and preserves structural tail-change and unrelated terminal
metric evidence. The same authoritative Home snapshot now also supplies
an inactive-round proof plus any structural reporting baseline; it is not
acquired a second time.

For an active save, the guarded identity is exactly
`(versionNumber, currentTier, roundsStartedThisTier[currentTier], roundSeed)`.
It is accompanied by `roundActiveBool`, `currentWave`, `saveRevision`, capture
time, source fingerprint, and bounded container metadata. Only the selected
tier-counter element is an identity dependency; appended or malformed
non-current elements do not erase it. `currentWave` is a separate claim, so a
malformed wave removes wave-derived rates without discarding cumulative totals,
time rates, or the round identity. The identity has a canonical fingerprint.
The authorized Tier 22 natural boundary proved that a
known Home state preceded a new seed and per-tier counter, then that the exact
identity stayed stable while revisions and waves advanced through the last
active snapshot. `BattleIdentityCoordinator` forces serialization and makes
this tuple the only durable same-battle key. `BattleIdentityStore` atomically
records the exact active identity, or a forced inactive Home proof, and
classifies a later active read as first, same, or later. Completed History,
elapsed time, visual activity, and log scope never substitute for that result.

Structural History identity schema 2 hashes only mapping/schema, Battle Date,
Tier, wave, and battle kind. Optional `gameTime`, `realTime`, and `killedBy`
leaves remain available to their own semantic consumers but cannot manufacture
an append or capped rollover when they are corrected or become available later.
A terminal structural handoff checks ordinary/Tournament kind before
publication and again before consumption.

The in-battle Perk projection requires exact agreement among the 50-entry
`perkLevel` array, `perksPickedCount`, and every ordered `PerkPick(wave, perk)`
entry. It publishes canonical Perk IDs, selection waves, level-after values,
and a stable snapshot fingerprint. Perk ID `0` is `max_health` (Max Health).
The 50 positions are storage capacity, not evidence that every index names a
possible Perk; the exact-version table currently maps all 34 defined semantic
IDs, including ID `11` as `unlock_random_ultimate_weapon`.
Changed entry shape/class, non-monotonic waves, or any count/list/level
inconsistency publishes no Perk snapshot. A structurally consistent unknown ID
retains an unpublished numeric calibration projection for the audit sidecar, but it
does not appear in the public runtime dictionary and does not create a partial
semantic snapshot. An inactive zero/empty projection is explicitly `cleared`;
the normal monitor retains the newest complete same-round positive prefix and
treats the clear only as closure from an exact natural terminal. At the Tier 22
boundary, the last complete active snapshot contained 15 internally consistent
ordered picks that exactly represented the terminal UI's 11 collapsed rows,
and the immediate stable post-death projection was inactive/cleared. The
implemented navigation decision remains fail-closed: it requires exact
process/target/`ActiveRoundIdentity` binding, stable high-confidence `View Perks`
exhaustion evidence, a nonempty complete checkpoint captured afterward whose
saved wave includes the exhaustion wave, and a later bound natural Game Over
clear. Normal in-battle timeline collection now uses those exact monitor
prefixes and never opens Perks: stable top-bar transitions merely request a
coalesced passive checkpoint, including while action authority is Paused. If
Game Over lacks finality but retains a bound non-conflicted prefix, terminal
collection reads only the newest Perks viewport and merges the provable tail;
ambiguous levels, repeat counts, order, or a missing saved-recency marker remain
explicitly unresolved. Only absent, unbound, malformed, or round-conflicted
prefix evidence takes the complete terminal Perks traversal.

The same boundary resolves the
`thetower.player_save.active_run_tallies.v1` semantic capability from the
version-1101 authority provider. It publishes 29 cross-channel-validated
cumulative leaf claims grouped for presentation as economy, progress, and coin
sources. Exact 1073 predates the provider and remains unavailable. Unknown
forward revisions inherit the capability only through its declared
`additive_dependencies` policy; extra fields remain unpublished, and legacy
Perk/configuration/profile claims do not inherit by implication. An inactive
save publishes only the inactive disposition, never stale or cleared tally
values. A capability-only data-lineage resolution retains a narrower
source-ordered terminal tail identity (battle date, Tier, wave, and kind) so
the monitor can prove its own active-baseline-to-natural-terminal transition;
that identity is not exposed as legacy History or lifecycle authority. The
allowlist and retained evidence are in
[`player_save.md`](player_save.md#2026-08-12-version-1101-active-tally-audit).

`ActiveRunMetricMonitor` consumes the same typed stable bundle already acquired
for the Perk monitor and optional campaign auditor; it never requests a save
read or sends input. It binds each accepted component to process, ADB target
generation, active-round identity, mapping, and audit ID. Activity scope may be
carried as report metadata, but it does not participate in equality or authority.
`saveRevision` remains diagnostic only. Capture order, source identity, wave,
and nondecreasing cumulative values own monotonic acceptance. Every direct leaf
retains its own definition and latest valid timed baseline. A malformed or
regressed leaf conflicts only that timeline and its derived dependents; sibling
leaves continue, and a later recovery computes its interval from the latest
prior checkpoint that contained the required leaf/time evidence. Component
status is an aggregate presentation, not an authority unit.

Every economy checkpoint records whole-run CPH, cells/hour, cash/hour,
waves/hour, and effective speed from the cumulative tallies and real/game time.
A later same-round checkpoint also records those rates over the exact interval;
coin-source checkpoints likewise record distinct whole-run and interval rates
for every published source. These realized save rates are separate from OCR
`coin_rate_samples`: the displayed Coins/min value is never multiplied by 60
and relabeled as realized CPH.

The causally bound natural terminal reuses the existing terminal bundle and
checks terminal kind plus exact process/target/round/window provenance.
Activity scope remains optional report provenance only.
Every expected terminal-linked leaf is matched, missing, or conflicted—even if
that leaf was malformed at every active checkpoint. Unrelated malformed cause
or time leaves do not erase terminal totals; time/wave-dependent rates alone
become unavailable. Valid claims retain both whole-run terminal rates and the
final checkpoint-to-terminal interval. The resulting `active_run_metrics` object is
stored in normal and Tournament completed JSON and rendered in Markdown and the
Windows Battle History detail view. All of it is observation-only and grants
no lifecycle, navigation, Strategy, or action authority.

In-battle Attack, Defense, and Utility levels are stored separately from their
Workshop baselines. The save does not carry a literal gold-box flag; a
normalized `maxed` claim therefore requires a versioned index and maximum-level
table and publishes the current level, Workshop baseline, and round delta with
that claim. An unknown index, special level rule, or cap makes the complete
upgrade component unavailable rather than guessing from a large value.

Survival abilities are checkpoint state, not an event log. Demon Mode, Nuke,
and Second Wind each expose round counts plus candidate active, cooldown,
recharge-wave, and effect-timeout fields. Those fields may establish that an
activation occurred and, after causal calibration, may identify the latest
activation wave from a countdown or absolute-wave relationship. A single late
snapshot cannot by itself reconstruct every earlier activation. Exact waves
must never be inferred until the versioned mapping proves the field units,
sentinels, reset behavior, recharge length, and serialization timing.

The history component accepts the game's source-ordered list of at most 30
entries and allowlist-validates the required members of its newest entry while
ignoring unpublished additions. UTC and local
.NET DateTime ticks use different clock bases, so they are normalized
individually and never compared across kinds; source order owns the tail. A
bounded structural identity/fingerprint uses only battle date kind/ticks,
tier, wave, and Tournament identity. Optional game/real time and numeric
`killedBy` remain normalized leaves outside that causal fingerprint, so a
correction cannot masquerade as a new tail. The identity is independent from
the canonical 16-section, 144-row More Stats projection and its semantic
fingerprint. The two hourly rows and effect-active percentages are explicit
derivations. `adGemsThisRound` supplies Ad Gems; base/ad coins are absent.

Cross-channel-validated cause values are `1=Fast`, `2=Tank`, `3=Boss`,
`6=Vampire`, `8=Scatter`, and `99=Surrender`. Surrender is a display value for
the terminal cause and carries no claim about whether the operator or
authorized automation initiated it. A future unknown numeric value remains a
normalized non-causal leaf outside structural tail identity; it cannot drive a
rollover/change decision, and the semantic completed entry remains unavailable
so terminal capture stays on UI evidence.

The same Tier 22 audit changed its known pre-battle capped-tail baseline to a
Tier 22, wave 751, Boss candidate whose complete 144-row projection agreed with
the terminal UI. This validates the natural-boundary causality and projection
used by the guarded terminal-report consumer below; the observation alone does
not attach an arbitrary terminal or relax current-process binding. The dated
evidence and exact row-level promotions are recorded in
[`player_save.md`](player_save.md#2026-08-02-tier-22-natural-boundary-audit).

Runtime adoption proceeds in bounded vertical slices with these ownership
rules:

1. The normal-runtime Perk monitor consumes periodic and explicit
   Perk-checkpoint revisions without navigation or input and independently of
   collector opt-in. It binds each
   complete checkpoint to the exact process, target generation, mapping, and
   active identity. Activity scope is optional presentation metadata.
2. Battle identity is bound only by a forced serialization. Terminal report
   attachment is separate: it requires a bound terminal, matching canonical
   round identity, and a compatible save-sourced pre-terminal tail baseline.
3. Perk evidence advances only from a later identical complete prefix or a
   strict extension carrying that same identity; a predating, different-round,
   incomplete, regressed, or reordered snapshot cannot prove current/final
   completeness. The saved `PerkPick` wave remains the exact event wave even
   when the stable revision is observed later. `saveRevision` is diagnostic,
   not temporal authority.
4. Upgrade, survival, and active-tally components advance independently. A
   malformed or unvalidated ability timer cannot erase valid Perks, upgrade
   evidence, or a valid tally component, and none grants UI action authority.
5. The Perk monitor retains the newest complete same-round prefix across
   post-run clearing. The active-run metric monitor rejects inactive values,
   retains bounded active component timelines, and reconciles the last values
   only against a bound natural terminal. Future upgrade and survival owners
   must establish their own independent retention rules.
6. Stable save checkpoints and passive visual events merge monotonically. A
   count increase establishes that one or more activations occurred in the
   half-open interval `(prior saved wave, current saved wave]`. It produces an
   exact wave only when a calibrated timer relation or a matching visual
   transition supports one; otherwise the record retains the interval, count
   delta, and provenance without distributing multiple events across invented
   waves.
7. The visual activation tracker remains active. Its confirmed transition
   event and first evidence frame can fill the tail after the last stable
   active save, survive a save-field clear at Game Over, and refine a
   save-derived interval. It cannot reduce a save count or cause the same
   activation to be counted twice.
8. Normal and Tournament completed records are built from a newly serialized
   history entry only after the pre-boundary structural tail advances by one
   valid append or capacity-30 rollover, the semantic entry is complete, the
   save is inactive, terminal kind matches, and available compact terminal
   identity does not contradict the bound round.
9. Terminal Battle History counts reconcile the cached save checkpoints and
   visual tail. Missing event timing remains explicitly unknown or bounded;
   count disagreement, impossible ordering, or an unbound snapshot forces the
   full UI audit rather than fabricating events.
10. One passive compact Game Stats capture remains for optional base/ad coin
    split augmentation. Its absence never invalidates otherwise authoritative
    save-derived battle stats.
11. The lifecycle's existing terminal stable read closes the Perk checkpoint
    window through the same natural-boundary bundle; the monitor performs no
    read. Game Over skips the Perks panel only when stable exhaustion, a later
    nonempty checkpoint, exact round binding, and the later terminal clear prove
    final completeness. Every other case opens the established panel route.
12. Game Stats and Perks remain passive terminal evidence. More Stats remains
    the guarded fallback for every missing, unknown, stale, changed,
    inconsistent, or unbound save claim; clipboard validation and guarded OCR
    retain their existing precedence on that route.
13. Wait, Retry, Home, every setting mutation, post-action verification, and
    terminal transition confirmation remain owned by verified UI controls.

The Perk-timeline phase is implemented without backgrounding an active battle
to accelerate a checkpoint. A stable Perk selection or exhaustion event may
request a prompt passive read, while an independent 300-second timer consumes
naturally serialized evidence without claiming when it was written. The
separate `save_first` Current-run attachment boundary may briefly use Android
Home only for a replacement process already at `RUNNING`; it preserves process,
operation, target generation, active-round identity, control, source
restoration, and lifecycle authority and grants no broader save consumer that
permission. A log-scope rotation during the transaction is observational and
cannot invalidate it.

Save-tail causality does not relax the independent current-process
`runtime.run_binding` boundary. A process that starts only on a terminal remains
unbound and cannot inherit Strategy, run configuration, Perk history, survival
events, or other process-local evidence. The save-derived terminal attachment
identifies the completed round only through its own guarded evidence; it cannot
manufacture active-process continuity.

The parser/runtime normalization foundation itself does not poll, cache, or
bind a process. The application composition root owns explicit acquisition:
the Perk monitor may request a checkpoint at a stable selection/exhaustion
boundary, active-run metrics may consume that same bundle, and the terminal
attachment below reuses one
stable terminal read for global profile progression and, only after same-run
tail proof, Battle History record construction. The implemented audit sidecar remains campaign-only,
observation-only, and not an authority source. Every additional normal-runtime
runtime/history claim remains gated by the versioned audit matrix in
[`player_save.md`](player_save.md#versioned-audit-matrix-data-9-game-1073--revision-4).

##### Implemented terminal save attachment

At a normal or Tournament terminal, `App` performs one fail-open stable read
from the exact target generation already owned by its `AdbTargetSession`. It
does not background the game or send input. The same decoded snapshot supplies
profile progression, Tournament conditions when available, and the candidate
completed report; no terminal consumer performs a second save read.

The report is accepted only for a current-process `bound` terminal whose
canonical active-round identity matches the retained run binding, whose
pre-terminal baseline came from the same player-save history contract, and
whose newest tail is exactly one valid append or capped rollover beyond that
baseline. The terminal save must be
inactive, the exact-version semantic entry must expose all 16 sections and 144
rows with a mapped cause, and normal-versus-Tournament identity must match.
Available compact Game Stats identity is a contradiction check and optional
augmentation; missing compact values do not invalidate a complete save report.
On success the handler persists the save-derived record without opening More
Stats. A target handoff, unsupported mapping, malformed component, unbound
terminal, absent or UI-sourced baseline, invalid tail transition, unknown
cause, kind mismatch, or compact-identity contradiction follows the existing
More Stats clipboard and guarded-OCR fallback. Game Stats and Perks capture,
Wait/Retry/Home behavior, and every lifecycle action remain UI-owned.

The exact-version profile manifest retains allowlisted primitive ownership,
level, selected-preset, equipped-Module, Bot, Theme, relic, Research, Workshop,
Enhancement, Ultimate Weapon, Guardian, and Harmony vectors. Every component
has its own source fields, structural completeness, and fingerprint. Unknown
indices remain indices: this consumer does not assign effects, calculate
effective multipliers, enforce a setting, or grant UI authority. Account IDs,
balances, Module GUIDs/inventory records, purchase histories, and the raw save
are excluded.

The profile projection is placed at top-level `profile_progression`, outside
`runtime`, because global profile state is not process-local battle evidence.
It may therefore survive a terminal-only start without weakening
`runtime.run_binding`: Strategy, configured intent, Perk/activation timelines,
and other process-local evidence remain omitted. Normal battle persistence
compares the snapshot to the newest earlier normal battle containing compatible
evidence and stores an exact-path `profile_progression_delta`; the first such
record declares a missing baseline. Tournament records retain the snapshot but
do not alter normal-battle baseline selection.

##### Implemented, campaign-only natural-boundary temporal auditor

`V1073-RUNTIME-013` is a default-disabled, observation-only temporal auditor
for short, named diagnostic campaigns. It can answer questions such as whether
an already-understood normalized Perk prefix advanced monotonically and cleared
at the next natural terminal boundary, or whether a structural history tail
changed after a known Home baseline. No subsystem reads its receipts, and the
runtime does not use them to make a decision. A human inspects them after the
campaign. It is not a save-acquisition service, an unknown-field discovery
system, or a normal-runtime evidence consumer.

When a campaign is enabled, the normal App supplies typed bundles already
acquired by a forced lifecycle transaction, a natural terminal boundary, or an
explicit stable Perk selection/exhaustion checkpoint. The auditor has no
independent acquisition, timer, or scheduler. A slow or failed projection
cannot delay the App heartbeat. The auditor never pauses or backgrounds the
game, navigates, taps, dispatches a handler, changes a lifecycle decision, or
suppresses UI. A target-generation change discards the supplied result.
Capture and detection continue to feed it while global Pause blocks actions.

Compatibility is granted by the decoder, not by matching the manifest's game
version literally. The decoder must resolve a supported, shape-valid exact or
explicitly compatible mapping and emit a normalized runtime projection with
the manifest's audit-matrix capability. A root that is merely parseable is
rejected. The manifest mapping and game version identify the evidence authority
from which that capability originated; every receipt records the actual
mapping and game version observed. The first accepted
`(mapping, audit matrix, game version)` tuple is pinned for the collector
session. A later tuple change fails closed instead of merging evidence across a
decoder or game-version handoff.

Each collector start creates new runtime and collector session identities and
appends canonical JSONL records without reading or rewriting an earlier
session. Its bounded normalized-evidence schema retains only:

- audit/mapping/schema/session IDs, safe reason codes, timestamps, revision,
  and source fingerprint;
- active identity and wave, including the per-tier counter value;
- complete Perk status/count/fingerprint, reconstructable same-identity pick
  deltas, and the last complete checkpoint across terminal clearing;
- unique same-wave Perk-ID calibration receipts containing only numeric ID,
  semantic key, wave/level, confidence, evidence fingerprint, and explicit
  collector-session scope;
- structural tail status/identity/fingerprint, count/capacity, and semantic
  completed-entry status/fingerprint; and
- passive boundary labels, observation-latency bounds, and strict metadata from
  already-confirmed visual activation events, with an optional relative
  evidence-image reference.

The optional normalized-component path is separately manifest-gated. The
committed survival component remains disabled because
`V1073-RUNTIME-015`/`016` have not promoted its polarity, counters, timers, or
merge semantics; no survival state or exact activation wave is guessed. A
disabled or rejected optional component cannot erase a valid core receipt.
Visual waves are explicitly approximate observations, never exact activation
waves.

Unknown-field discovery remains a separate, targeted mapping-calibration
workflow that gathers purpose-specific evidence. The auditor is not a raw
dataset for that work. Its legacy UI Perk resolver is narrower: an explicit UI
owner may supply a stripped calibration batch only after independently
accepting a complete exact selection boundary. Display text, OCR output,
colors, pixels, restored checkpoints, and the normal save-backed timeline never
cross this queue. The resolver restores a semantic projection only when exact
wave correspondence and singleton constraint propagation make every needed
assignment unique, each semantic family is already allowlisted at at least 80%
confidence, and the bounded calibration receipt is successfully appended.
Ordinary observation does not open Perks to create such evidence; an
unknown ID therefore remains unavailable and preserves terminal fallback.
Ambiguous or conflicting evidence stays unavailable, and the static mapping
manifest is never rewritten.

The collector begins with a shared pre-round structural-tail baseline when one
is available, records an already-acquired active identity, projects later Perk
checkpoint bundles, and observes the shared natural terminal transition. It
records the last complete same-round Perks, the first inactive/cleared save,
and any structural tail change—including 30-entry rollover—as candidates. It
may calculate candidate tier/wave/time agreement, but it cannot call that entry
attached, update a battle record, decide whether to open Perks, or suppress any
UI route. Raw saves, decoded roots, account identifiers, arbitrary history
fields, screenshot pixels, and OCR text are outside its retained schema. Those
limits keep the diagnostic log compact, reviewable, and decoupled from decoder
internals; they are not an authentication or adversarial-security boundary.
The existing visual activation tracker continues to retain only its confirmed
first-transition evidence frames under the ordinary evidence policy; the audit
receipt stores event metadata and an optional evidence reference, not the
image. The passive compact Game Stats capture remains a separate optional
base/ad coin-split augmentation.

For a same-process direct Retry, a valid Game Over tail candidate may become
the next round's structural baseline. The later `RUNNING` projection is
accepted as a rollover only when the passive boundary time advances, target
ownership is unchanged, revision/source evidence advances, the active identity
changes, and its current tail exactly equals the carried terminal tail. The
collector then clears all old identity and Perk progression before observing
the new round. It never carries this evidence across a process session, and a
process that starts on Game Over remains terminal-only and unbound.
Perk-ID overlays have a narrower but compatible lifetime: accepted semantics
survive UI correlation-window resets and ordinary direct Retries on the same
owned target generation, while per-round UI batches do not. A target handoff,
generation change, collector restart, or exact conflict discards the overlay.

Past Tier 22 and Tier 19 campaigns established the core natural-round behavior
and exposed direct-Retry identity retention plus missing Perk IDs; the reviewed
repair remains in normal code. That history does not justify ambient
collection. The auditor stays off until a future investigation names a
specific question and finish condition. Upgrade, survival-ability, and other
candidate components remain independently unavailable until their own matrix
rows are promoted, and they do not gate valid core receipts. The separate
terminal consumer makes a causally bound report primary while Game Stats and
Perks remain passive evidence, More Stats remains the guarded fallback, and
continuity, terminal binding, Strategy, attachment, record construction,
Perks-navigation decisions, UI suppression, and lifecycle authority remain
unchanged. A terminal-only process remains unbound and cannot inherit Strategy
or process-local evidence.

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
single-battle observer. The immutable bundled default declares Tournament
Cards, Demon
Mode automatic recharge activation, Nuke manual-after-recharge activation,
Tourney Workshop, Amplify Bots, Attack/Ally/Scout Guardians,
Tournament/Milestone modules, Poison Swamp Stun `on`, Damage Slider `100%`, the
Range `98.38m` Orb Distance pair Extra `87.16m` / Workshop `80.37m`, all nine
Ultimate Weapons, and Spotlight Missiles. Tournament battles have no Perks, so
Perks and Auto Perks are outside this contract.

Strategy Authoring exposes the bundled Tournament source for inspection and
cloning while keeping it immutable. A custom Tournament-family variant may
replace only its Module and Orb Distance directives, using either retained
preset snapshots or profile-local definitions. `enforce` changes the setting
at its existing authorized boundary, `observe` only compares and reports, and
`ignore` preserves it. Every other Tournament requirement and every generated
validation, launch, initialization, handler, and attachment rule remains the
same protected builder output. Tournament variants remain tierless and enter
the same fingerprinted exclusive-validation ledger and generic main-loop owner
path as the bundled profile.

Every explicit Tournament selection or managed process Start creates a durable
validation request tied to the strategy request identity and the complete
generated-plan fingerprint. A pending request is one-use authorization, not a
recurring strategy permission. An unattended restart cannot recreate it. The
receipt records `pending`, `claimed`, `running`, `cleanup`, and terminal result
states, with the runtime ID, PID, ADB target, and deadline attached before the
first battle input.

That authorization exists only before a Tournament begins. Fresh
`RUNNING` plus Tournament identity cancels an unclaimed pending request as
obsolete, whether the runtime attached to the battle or observed a manual
start. `TOURNAMENT_RESULTS` repeats the cancellation as a terminal fail-safe.
The cancelled result is non-actionable: it cannot carry validation work across
the completed Tournament into the next Home boundary. An attached ordinary
battle does not cancel the request, because validation may still run at the
following verified Home `NEW_BATTLE` before any Tournament begins.

At verified Home `NEW_BATTLE`, the profile first completes every declared
no-battle check: Tournament Cards, the Demon Mode/Nuke recharge activation
modes, Tourney Workshop, Amplify Bots, Attack/Ally/Scout Guardians, the
variant's enforced or observed module loadout, and Poison Swamp Stun `on`.
An ignored Module directive performs no Module check or change. Damage Slider,
Orb Distance, and Ultimate Weapon enablement remain explicitly deferred
because their authoritative controls are battle-only.
Exclusive validation claims staged one-run waivers tied to the same Tournament
strategy request before Home setup, so a configured check skip applies to this
path as well. An unwaived failed Home check consumes the request with its reason
and never starts a battle. Once Home preflight is complete, the runtime
atomically claims the matching receipt and then uses a fresh verified control
to start exactly one ordinary `NEW_BATTLE`. It rejects Home `RESUME_BATTLE` and
never opens the Tournament screen or starts a Tournament battle. A conclusive
authorized dispatch advances the same single-use save carrier as an ordinary
Home launch before RUNNING can bind it. Complete carried Cards, Workshop,
Bots, Guardians, Modules, and other supported session facts suppress their
duplicate UI routes; only a per-check mismatch, unsupported/incomplete fact,
policy-forced audit, or invalidated transition may select those fallbacks.

The disposable ordinary battle bypasses EHLS/EALS initialization without
seeding either completion flag. It does not toggle Auto Perks. Session
preflight enforces Damage Slider `100%`, reads Attack Range, enforces the
matching configured Orb Distance relationship when that directive is
enforced, only observes it when requested, or omits the check when ignored;
the bundled relationships remain `30.00m` and `98.38m`, and any other readable
experimental Range is preserved. It then verifies all configured Ultimate
Weapon primary toggles and Spotlight Missiles. A
conclusive pass or failure, or the bounded timeout, moves the same receipt to
cleanup before any terminal input. Surrender is allowed only while the current
runtime/ADB owner still matches and fresh `RUNNING` evidence excludes
Tournament identity. Cleanup must reach Game Over and verified Home
`NEW_BATTLE` before a normal receipt reports ready or its failure reason.
Successful guarded Surrender retains exact process-local Game Over proof. If
the result write fails after verified Home, that cleanup proof retains
`EXCLUSIVE_VALIDATION`; later heartbeats retry only persistence and never repeat
the Home tap. The same owning runtime can also recover from a fresh verified
Home `NEW_BATTLE` frame when its first cleanup observation was missed. If Home
cleanup fails and a later `RUNNING` frame appears, that fresh frame cannot be
trusted as the old battle: the runtime performs no further input, persists a
failed old receipt, consumes its proven Game Over lifecycle boundary, rotates
the report segment, and permits only a subsequent forced save identity to
authorize adoption of the successor. Process replacement cannot inherit that
receipt: owner mismatch
fails it closed without input. The mission boundary and pending Strategy
release only after the result is durable. A queued successor Start or confirmed
Tournament launch waits behind that receipt-only finalization. Capture and
detection remain live during Pause, but workflow synchronization, mission
observation, run adoption, and battle-identity reconciliation are quarantined
while the exact terminal claim remains. If the operator manually starts a successor
during that Pause, Resume finalizes the old boundary before a later heartbeat
adopts the successor. A requested ADB target handoff is likewise deferred until
active validation, terminal finalization, or an awaiting/requested/claimed
confirmed launch releases its current target. The ready launch receipt also
retains the ADB target on which validation completed: a different-target
runtime retires an unclaimed prompt without input, and neither automatic claim
nor manual-start observation can transplant it to that target. Tournament
identity, a resumed/pre-existing battle, or an ambiguous transition fails
closed without inheriting Surrender authority.

A fresh validation or confirmed-launch battle boundary is process-local proof
that must survive its single observation frame. The runtime records that proof
immediately after lifecycle adoption and before any other route can recapture.
If the claimed-validation `running` write or the launch-result
write is temporarily unavailable, the exact receipt remains the sole typed
owner and later heartbeats retry only that write; they do not reinterpret the
absence of a second fresh boundary as failure. Pause preserves the proof while
denying the write and every input. A retained validation start seen later at
Home `RESUME_BATTLE` is advanced only far enough to persist a no-input failure
and release validation mode. If an owned running validation instead reaches
fresh Game Over, verified Home `NEW_BATTLE`, Tournament Results, or Workshop
before guarded Surrender—or Tournament entry is the first fresh authoritative
no-battle frame after dropped terminal observations—that proof is retained
before any fallible ownership/write step and before mission or successor
observation. Resume can then finish the exact receipt and old lifecycle
boundary without touching a successor.

If guarded Surrender does not conclusively reach Game Over, the runtime stages
a distinct exact `release_without_cleanup` result. It never retries Surrender,
Home, or another battle action. A failed result write is retried from later
heartbeats under `EXCLUSIVE_VALIDATION`; once durable, validation-battle mode is
released without calling Game Over hooks or applying a next-boundary Strategy
against the still-running battle. Run initialization and session preflight stay
deferred for that battle's remainder, and a distinct suppressive
`EXCLUSIVE_OWNERSHIP` hold denies strategy, handler, workflow, battle-identity
reconciliation, background, target-handoff, and Strategy-replacement input.
The hold releases
only on fresh Game Over, Tournament Results, Workshop, Tournament entry, or
verified Home `NEW_BATTLE`; Home `RESUME_BATTLE`, `RUNNING`, `UNKNOWN`,
incomplete Home classification, and every other non-authoritative screen
retain it. Workshop and Tournament-entry release explicitly retire the old
mission lifecycle so the next `RUNNING` frame is adopted once as a successor;
a queued next-boundary Strategy is applied before that adoption. The genuine
terminal/fresh-Home boundary then rearms the next run normally.

The verified `NEW_BATTLE` launch composes the newly claimed receipt with the
typed owner that authorized its Home route: `OPERATOR_WORKFLOW` for an explicit
Start workflow, otherwise `EXCLUSIVE_VALIDATION`. It rechecks both at the final
tap. After the owned battle boundary, `EXCLUSIVE_VALIDATION` drives every
battle-only strategy tick, timeout Surrender, Game Over cleanup, and confirmed
Tournament launch, including proof-backed cleanup-result persistence; ordinary
attached preflight continues to use
`SESSION_PREFLIGHT`. Exact receipt-only finalization precedes successor
lifecycle adoption. During an input-capable phase, operator-workflow,
`BATTLE_IDENTITY`, or unresolved-ownership holds take priority and therefore
stop validation before its next final input. Passive capture preserves the
preceding durable hold until fresh detection selects the next owner. The
control surface rejects a new interactive-development lease while any
non-external typed hold is published, so an external lease cannot interleave
with and strand a terminal validation claim. Setup Capture is unavailable
while validation owns the runtime. A successor operator workflow may remain
queued, but a retained terminal claim does not merge that future owner into
its final Home/result guard; Pause and Stop still deny globally. A failed fresh
durable-ownership reread also cannot make cached validation or confirmed-launch
work disappear: the exact local identity retains suppressive
`EXCLUSIVE_OWNERSHIP`, blocks ordinary dispatch and ADB handoff, and becomes
actionable again only after a fresh exact-owner read or a durable orphan
transition. Strategy replacement after finalization preserves the already
consumed Home/Game Over marker, so the next Home frame does not rotate the same
activity boundary twice.

The known Free Ticket blocking-primary state borrows no ambient recovery
authority. It may run one bounded Claim transaction only for the exact durable
source that dispatched the obscured launch: `OPERATOR_WORKFLOW` for Start or a
retained terminal continuation, `EMULATOR_MAINTENANCE` for replacement-battle
recovery, or `EXCLUSIVE_VALIDATION` for an ordinary validation or confirmed
Tournament launch. Explicit Start plus its linked validation receipt share one
physical Claim budget. A partial terminal write is receipt-only retry work;
typed input uncertainty Pauses and cannot fall through to another source alias
or replay Claim.

A durable owner accepted between heartbeat selection and the next mutation is
also visible at the final guard. In particular, an uninstalled BlueStacks
maintenance request or newly requested confirmed Tournament launch blocks an
unrelated route before input, and Start-Tournament acquisition shares the
cross-process dispatch boundary. If an unacknowledged maintenance request races
an already exact validation owner, it terminates without host mutation instead
of stacking two actionable owners.

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
rechecked before every tap. Both `OPEN` and `BATTLE` retain the typed dispatch
outcome from the final ADB boundary. An uncertain result Pauses, retains the
post-dispatch suppressive boundary, and is never treated as a proven miss or
replayed. Timeout, process replacement, owner mismatch,
supersession, unexpected battle identity, or any ambiguous transition fails
closed without further input. Once the verified `BATTLE` tap has been
dispatched, a timeout or supersession on `UNKNOWN`, Home with an unknown or
resumable battle control, `RUNNING`, or any other non-authoritative screen
retains suppressive `EXCLUSIVE_OWNERSHIP`; only fresh Game Over, Tournament
Results, Workshop, verified Home `NEW_BATTLE`, or the Tournament entry screen
proves release safe. If the operator starts the Tournament manually while the
offer is pending, the runtime consumes the offer as a manual start and
continues observing normally.

The genuine Tournament run performs the standard run-initialization route at
its fresh boundary, maxing EHLS first and EALS second. Its battle-only session
rules enforce Damage Slider `100%` and apply the variant's Orb Distance policy
before the remaining observer check completes. It then becomes passive except
for game-speed maintenance, ad-gem collection, and terminal-result handling.
An ad gem starts the same bounded floating-gem sweep used by Farm; the profile
does not run an independent or continuous floating-gem handler. No Tournament
battle gains validation-battle Surrender authority.

Automatic validation of an already-running Tournament does not use the
exclusive validation receipt. Any still-pending pre-Tournament request is
cancelled before attachment work begins. The same guarded attachment
acquisition supplies one exact-bound, one-use carrier for every complete
validated configuration fact projected from that forced save. Process, target
generation, canonical active-round identity, and active-battle ownership are
rechecked at each consumption. Temporal class determines mismatch handling; it
no longer makes a complete fact ineligible for this attachment check.

An exact saved match omits the corresponding Cards, Workshop, Bot, Guardian,
Module, Free Upgrade-lock, Auto Pick, Card Recharge, Perk configuration, Target
Priority, or Ultimate Weapon UI route. A missing, incomplete, unsupported, or
unparseable fact retains only that field's supported UI fallback; a field with
no current-battle UI route remains explicitly deferred or unavailable, as does
an unresolved Home-only Workshop fact. A complete
saved mismatch is not sent to UI merely for confirmation. Workshop, Free
Upgrade locks, selected Bot preset, equipped Guardians, equipped Modules,
First Perk Choice, Perk Bans, and Perk Auto Pick order are immutable for the
active battle, so their mismatch is
logged as nonblocking session evidence and the pass continues. The same rule
applies when a fallback UI produces a fully observed mismatch for one of those
immutable fields. A mutable mismatch is also observational at this attachment
boundary. Auto Pick, Poison Swamp Stun, Damage Slider, Orb Distance, and every
other configuration action are downshifted to measurement; an unavailable
result, validator exception, or mismatch records degraded evidence and
completes the one-shot check. Profile and run waivers are applied before
attachment reconciliation; a waived fact is not consumed, warned, or made
blocking. Ultimate Weapon component
evidence records its save/UI source explicitly.

Every process attachment stays in the current battle. It gains no game-Home
route, Android lifecycle action, second save read, Home-repair request, or
Surrender authority. Once that selective inventory pass reaches a conclusive
result, the explicitly
`run_when_attached` battle-only rules observe Damage Slider and the configured
Orb Distance pair for an authoritative configured Attack Range; they do not
change either value. A bound save can skip the Range/Distance panels only when
its independently calculated Attack Range is complete, max-stable, and bound
to that active round, and its two Orb raw fields uniquely match the same
Range-bound tuple. Preset labels and the selected lab level alone are never
live Range evidence. A
readable unconfigured Range is preserved without opening Distance Adjuster.
The attachment path never selects a Home preset or equips a loadout. The
separate guarded process-reload
workflow retains its explicit `next_run` compatibility policy; it is not the
user-facing validation choice.
Observation-only and immutable mismatches complete the one-shot pass without
an operator decision or run-scoped waiver; they cannot make the inventory
traversal repeat or block result capture.

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

Process-local run evidence has an additional terminal binding boundary. The
current process must force-bind an active `RUNNING` battle, and the canonical
active-round identity retained at the terminal must still match that binding.
Starting directly on Game Over, or reaching a terminal after an intervening
battle that was not force-bound, is `unbound`.
Terminal UI capture remains valid, but the record omits selected Strategy,
resolved run configuration, last-wave and coin samples, game-speed history,
session-preflight evidence (including an identity-restored report snapshot),
Perk timeline, and survival-ability observations. Any restored process-local
Perk or activation state is reset so it cannot leak into a later record. The
JSON and Markdown retain an explicit warning and a versioned
`runtime.run_binding` reason.

This boundary is independent of a readable activity-scope ID. A process can be
blocked inside an earlier Game Over `WAIT` handler while an operator manually
leaves that screen, runs another battle during Pause, and returns to Game Over;
the detector did not observe that intervening run even if the persisted scope
never changed. An unbound standard Game Over therefore remains `unknown`
unless independent terminal evidence proves a more specific type. The distinct
Tournament Results terminal state still supplies Tournament identity, while
its process-local Strategy evidence is omitted under the same binding rule.

## No Strategy observation profile

`No Strategy` supplies no configured run intent and owns no upgrade actions,
startup initialization, or session-preflight gate. It is nevertheless an
observation profile. At a `save_first` running attachment, the forced battle-
identity acquisition supplies complete exact-mapping configuration checks
through a typed process/target/round binding. The exclusive
read-only route then visits only unresolved
Cards, Bots, Guardians, Modules, Target Priority, Damage Slider, Perks, or
Ultimate Weapon fields; a fully resolved save plus passive Dissonance evidence
causes zero game-UI input. Every remaining action is source-state guarded,
every destination is verified, and Pause is synchronized before each input.
Workshop remains a Home-boundary observation when unresolved. Missing screens
remain explicitly `not_observed`; authoritatively inaccessible controls are
recorded as `unavailable` with a reason. Values are never copied from a Farm or
Tournament profile. Save values retain their mapping/check provenance. This
evidence is stored under
`observed_run_configuration`, separately from the configured-intent
`run_configuration` field. Configured profiles do not populate
`observed_run_configuration`; an empty observation is omitted rather than
implying that a No Strategy inventory pass occurred. Their declared intent and
verified values remain under `run_configuration` and
`runtime.session_preflight_evidence`, respectively.

The actual-loadout merge classifies Workshop, Free Upgrade locks, equipped
Guardians, selected Bot preset, equipped Modules, First Perk Choice, Perk Bans,
and Perk Auto Pick order as round-invariant. It keeps a complete save claim
when later Guardian or Module UI evidence is partial, and any differing
complete same-round invariant fails
closed as `unavailable` instead of using the latest value. Cards remain
point-in-time, while Bot progression remains a separate fact from the selected
preset. Every published save field carries
redacted exact mapping, target-generation, final-scope, round, capture, and
temporal provenance.

The fixed purple modifier badge next to Tier is localized Dissonance-family
identity evidence. Its white icon supplies a separately validated subtype: the
sword is Attack and the star is Utility. Shape comparison uses the existing
Attack, Defense, Utility, and Ultimate Weapon tab icons as references, but an
unvalidated subtype remains generic Dissonance rather than being guessed.
Standard Game Over plus family evidence supports a high-confidence
`dissonance` classification; Tier without the badge still remains `unknown`.
Only a recognized Attack sword records Damage Slider as unavailable before
UI-route planning. Utility Dissonance disables Utility systems, so its star
must not suppress the guarded Attack-menu Damage Slider read.

Home-only facts use a second phase after natural completion. No Strategy forces
full structured Game Over capture and the Home terminal action, even if the
process was launched with fast Game Over capture. At verified Home
`NEW_BATTLE`, it skips any Workshop, Free Upgrade-lock, Cards, First Perk, Ban
Perks, or Auto Pick fields already resolved by the guarded attachment save. For
unresolved fields, the runtime reads the three supported Free Upgrade lock
details with `enforce=False`, records the Workshop preset, and uses the existing
read-only Cards/Perks tabs. The same battle record is atomically updated after
each required phase. A save-complete record finalizes directly at verified
Home; it does not reopen those configuration screens. Each field retains its
source, confidence, phase, observation timestamp, and save provenance where
applicable; uncertain UI parsing retains raw page images instead of
manufacturing a structured value.

Pause continues to block every inventory input. An interrupted pass resumes
from a known read-only screen or restores verified Home before retrying its
stage. If an optional capture or configuration stage remains incomplete, the
runtime persists the partial observation with explicit unresolved fields and
releases verified Home to normal terminal policy instead of holding automation
indefinitely. Only the exact route back to Home may remain pending, and an
explicit `WAIT` still holds its requested boundary. Game Over `WAIT` must first
receive an actionable direction; No Strategy overrides `NEXT_BATTLE`'s direct
Retry route to Home so its bounded Home-only work has an opportunity to run
before the next battle, without making successful completion a prerequisite
for continued battle retry.

## State and battle lifecycle

- The persistent post-run dispositions are `NEXT_BATTLE`, `WAIT`, and `HOME`.
  `NEXT_BATTLE` normally takes the next authorized direct Retry, Battle, or
  Resume Battle route; repairable configuration degradation first inserts the
  global Home-setup boundary described below. `WAIT` holds the current
  terminal/Home boundary; `HOME` returns Home after Game Over and never
  authorizes automatic Battle or Resume Battle input. Legacy `RETRY` control
  values normalize to `NEXT_BATTLE` at the persistence boundary.
- Visible navigation and battle lifecycle are separate. Home
  `RESUME_BATTLE` preserves the current battle identity; `GAME_OVER`,
  `TOURNAMENT_RESULTS`, or a verified Home `NEW_BATTLE` ends it.
- Lifecycle and guarded Home actions share one Home classifier. A handler must
  not infer a new run independently from navigation alone.
- `HOME_AD_GEMS_AVAILABLE` schedules the five-gem Home claim before Home can
  start or resume a battle. When Automation is Enabled and no immediate battle
  workflow exists, the initial Start/Attach wait grants only the typed
  `home_ad_gem` auxiliary collector; it does not grant Home navigation, setup,
  Strategy, or lifecycle authority. The handler requires a fresh visible
  button match, synchronizes current control and operator-workflow ownership,
  rechecks typed authority at the final input boundary, verifies dismissal,
  and never starts the in-battle floating-gem tapper.
- `FREE_TICKET` is a blocking-primary state with priority over the obscured
  Home or battle screen. Capture and detection continue, but no collector,
  mission, Strategy, lifecycle, or ordinary recovery route runs behind it.
  Only the exact dispatched source described in the Tournament and emulator
  contracts may borrow one bounded Claim transaction under that source's typed
  owner; an unowned or conflicting modal remains suppressive without input.
- Transient `UNKNOWN` observations preserve an owned, incomplete startup gate.
  Initialization completion depends on the strategy assertion, not merely the
  current primary screen.
- While paused or exclusively gated, capture, detection, lifecycle observation,
  and read-only status reporting continue. Strategy, handler, mission,
  recovery, and blind-tapper actions remain blocked. The exact validation
  terminal claim above is the narrow exception: detection continues, but
  successor lifecycle observation waits until the old boundary finalizes.
- A compound Strategy route binds its selected typed action guard for the
  entire synchronous route without holding the mutation lock. Each nested tap
  or swipe opens its own short dispatch transaction and rechecks global
  control, durable workflow ownership, and the scoped guard at the final input
  boundary. A newly accepted operator workflow therefore stops the next input,
  even when a helper does not expose a separate guard parameter.

## Completed-run records and evidence

- Structured Battle/Tournament JSON is the canonical completed-run artifact;
  Markdown and the control-surface report are views over that same record.
- The record retains copied Stats rows, compact Game Stats-only fields, perks,
  resolved configured intent, separately sourced observed run configuration,
  observed preflight/runtime evidence, and derived metrics. Consumers must read
  these fields instead of relying on a terminal screenshot. The observed run
  configuration exists only for a non-empty No Strategy observation; configured
  runs never serialize an empty placeholder there.
- Tournament records also retain a normalized `battle_conditions` inventory.
  On exact version 1073, the terminal handler reads a stable save without UI
  input, cross-checks current event number, registry date, and Legend league,
  then applies the versioned seeded generator. Missing, conflicting, unknown-
  version, or unvalidated-league evidence is explicit and nonblocking; it does
  not weaken result capture, and the Heat/Overheat UI path remains the audit
  and fallback source. Schema-version-2 Tournament records render the same
  condition evidence in their Markdown view.
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

### Global runtime failure policy

Recoverable runtime problems never own a global Pause, Stop, Strategy Gate, or
indefinite authority hold. This includes configuration mismatch, unavailable
validation or evidence, exhausted bounded repair, reporting failure, and stale
workflow evidence. At a boundary where correction is already safe, the runtime
tries the bounded repair immediately. Otherwise it skips only the unsafe or
unsupported action, records the exact problem as degraded evidence, and keeps
unrelated strategy, handler, collector, and lifecycle automation eligible.
An Attach request follows that policy after it has safely restored and adopted
the exact battle: attachment-validation or receipt-reporting failure cannot
retain the global workflow hold. The exact process-local claim remains
authoritative while status reporting retries.

Configuration degradation carried by a running battle has one global terminal
rule. When `NEXT_BATTLE` is already in force as its Game Over is handled,
the runtime snapshots the current mismatch/unavailable-validation/Home-setup
failure, returns Home instead of tapping Retry, applies any pending Strategy
boundary change, and rearms that next profile's complete no-battle setup. A
terminal-navigation failure retains both the Home route and the degradation for
a fresh-evidence retry. Verified Home then runs the ordinary bounded repair
before consuming the exact terminal-bound one-shot continuation. Successful
setup clears the carried degradation and requires fresh in-battle validation;
exhausted setup records its new exact failure and still releases the next
battle degraded. `WAIT` is not overridden, `HOME` grants no automatic launch,
and reporting-only or other non-configuration warnings do not manufacture Home
repair work.

Only four catastrophic classes may automatically persist `PAUSED`:

- control authority is lost or corrupt;
- ownership of the exact ADB target is lost;
- a lifecycle input was attempted and the original source cannot be proved
  restored; or
- an input was dispatched but its result cannot be determined safely.

Explicit operator Pause, Stop, and Take Manual Control remain separate intent,
not failure handling. A bounded exclusive owner may still hold input while its
currently executing action is being repaired or reconciled, but a recoverable
result must release that owner. Legacy session-preflight blocks and gates are
migrated to completed degraded evidence when encountered.

Durable control changes and Android mutation dispatch use one companion
cross-process boundary beside the control file. A mutation refreshes the exact
control request while holding that boundary immediately before its first
input. Pause, Stop, Take Manual Control, terminal-policy changes, and requests
that acquire input ownership take the same boundary for their atomic write.
Consequently, a control write and an input have a single order: an input that
already crossed its final guard may finish, but after the control write is
accepted no later input or compound-action step can begin. Passive save,
capture, and watchdog prechecks do not hold this boundary, so Pause can persist
while they run. Once a lifecycle transaction has sent input, it retains the
boundary only through mandatory source restoration; this prevents Pause from
stranding the game backgrounded or on Android Home.
The final runtime guard also synthesizes a newly durable maintenance or
confirmed-launch owner before its normal heartbeat hold is installed, so those
transactions cannot acquire authority in the gap between a route check and its
next input.

All low-level ADB subprocesses used by runtime observation or mutation are
bounded. Mutations return typed `attempted` and `uncertain` outcomes. A plain
mutation timeout is an uncertain-input catastrophe; a transaction owner that
can still prove its required final restoration defers that judgment until the
transaction completes. Forced-save serialization and watchdog restart use
that rule, retry their bounded restoration/launch where defined, and report a
catastrophic hold only when the final game source remains unproved. Diagnostic
logging for an already-classified recoverable failure is best effort and
cannot itself terminate the main loop.

### Typed runtime action authority

`core/action_authority.py` is the central runtime owner for action decisions.
It returns immutable decisions for `observation`, `auxiliary_collection`,
`strategy_action`, and `lifecycle_action`; each decision carries an explicit
allowed flag and operator-facing reason. Observation is intentionally not
routed through an input guard.

| Runtime condition | Observation | Auxiliary collection | Strategy action | Lifecycle action |
| --- | --- | --- | --- | --- |
| Normal | Always continues | Existing screen, handler, and scheduler policy | Existing strategy policy | Existing lifecycle policy |
| Global Pause or Stop | Continues | Blocked | Blocked | Blocked |
| Enabled initial Start/Attach wait at fresh Home | Continues | Only the visible `home_ad_gem` claim | Blocked | Blocked |
| Legacy running-battle Strategy Gate | Continues | Only the explicit safe allowlist | Released after migration to degraded evidence | Released after migration to degraded evidence |
| Battle-identity, initialization, validation, or exclusive screen hold | Continues | Blocked | Matching bounded owner only | Matching bounded owner only |
| `external_development` hold | Continues | Blocked | Blocked for every owner | Blocked for every owner |

An exclusive hold may carry an explicit collector allowlist. Global Pause,
Stop, and shutdown still precede that list, and every simultaneous hold must
allow the same collector. The only initial-intent exception is
`home_ad_gem`, granted while there is no active Start/Attach workflow and used
only from a freshly detected Home frame. A requested or acknowledged battle
workflow, Take/Return Control, Setup Capture, interactive development, or any
second exclusive owner removes that exception. It cannot authorize Battle,
Resume Battle, configuration, recovery, or another collector.

`external_development` is the one intentionally suppressive hold. Unlike
initialization, preflight, battle-identity, and exclusive-validation ownership, it
has no matching in-process bypass: even `owner=external_development` is denied.
It therefore stops normal strategy, handler, auxiliary, initialization,
validation, recovery, lifecycle, and blind/background input without changing
`AUTOMATION.state` or representing itself as Pause. Watchdog connection,
process, and foreground observations continue, but its restart and foreground
paths recheck lifecycle authority under the mutation guard shared with hold
installation. An already-authorized recovery retains that guard through its
last mutation; production cannot install the hold or acknowledge quiescence
until it finishes.

Structured Strategy Gate state remains readable for compatibility with older
receipts and clients, but current configuration, validation, evidence, repair,
and reporting failures do not activate it. If the runtime encounters a legacy
session-preflight gate, it clears that gate and converts the retained failure
to completed degraded evidence. The gate never authorizes Surrender, Exit
Battle, Go Home, restart, or New Battle.

The gate's auxiliary allowlist is explicit: in-battle ad-gem collection and its
bounded floating-gem scan, Daily Gem Store, Daily and Weekly Mission rewards,
Event Mission rewards, and Guild chest rewards. Authority is necessary but not
sufficient: all existing badge, rollover, claim-limit, Sunday-hold, eligibility,
cooldown, and scheduler rules still decide whether a collector is due. A
collector cannot navigate to Home or cross a battle boundary merely to collect.
Home ad gems retain ordinary Home handling outside this running-battle gate.

Minimum continuity is deliberate. Recoverable setup-capture, data-collection,
configuration-validation, repair, and receipt failures are flagged without
changing global authority. Game Over statistics collection is best effort;
the selected Retry/Home route is still attempted and a failed terminal tap is
retried from fresh terminal evidence with action authority unchanged.
Tournament Results dismissal follows the same retry rule. Explicit Pause,
Take Manual Control, and Stop remain operator boundaries. Changed workflow or
target ownership, unproved restoration after lifecycle input, and uncertain
input results are catastrophic safety boundaries and may Pause automatically.

Daily Gem and mission collectors claim an exclusive auxiliary-route lease from
a freshly detected same-battle `RUNNING` frame before their first input. While
that lease exists, no other collector, background floating-gem tap, strategy
handler, lifecycle handler, or generic recovery owns the screen. Every input
checks a fresh screen precondition and then rechecks control state, typed
authority, route identity, and canonical battle identity at the final dispatch boundary.
Pause or lost authority retains the exact collector cleanup state without
sending more input. After authority returns, only that collector's bounded
cleanup may resume. Game Over, Home boundary, run-identity change, or an
unexpected state abandons the route without cleanup input so the authoritative
boundary handler can take ownership.

`RuntimeActionAuthorityPublisher` atomically refreshes
`logs/strategy_action_gate.json`. Schema version 1 includes runtime/ADB/PID
ownership, observation time, runtime-active flag, staleness threshold, active
gate and canonical battle identity, current battle-active/identity evidence,
optional report-scope telemetry, strategy,
source/phase, failed checks, reason, activation and update times,
Pause/Stop/hold context, an optional collector allowlist on a hold, all four
authority decisions, currently allowed collectors, any auxiliary-route lease,
and the separate interactive-development runtime acknowledgement when
applicable. The control surface accepts the channel only while its timestamp
is fresh, `runtime_active` is true, and its PID/target owner matches the active
runtime lock. It never derives gate or development-lease authority from
warning text in `actions.log`.

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
  exception: the moving gem cannot be reacquired reliably. Its dedicated
  tapper rechecks the central floating-gem auxiliary decision immediately
  before every tap and stops cooperatively on Pause, Stop, gate replacement,
  exclusive ownership, shutdown, canonical battle-identity change, or loss of
  the detected
  `RUNNING` precondition. Each tap is synchronous within that already
  backgrounded worker, so the worker remains active until accepted input has
  completed and cannot leave a queued tap behind. That hot guard performs no
  capture, OCR, detector, or status-publication work, and its wall-clock
  schedule prevents guard latency from accumulating across the one-second
  sweep cadence. Operator-invoked gesture tuning may use its separately named
  unchecked tooling path with a recorded reason.
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
  fall back to single-step feedback; unknown or incomplete evidence skips that
  correction and completes the check degraded.
- Orb Distance enforcement first requires authoritative Attack Range evidence.
  A complete bound save fact may supply it under the calculation and lifecycle
  rules above; otherwise the workflow locates the tile and uses OCR, with one
  adaptive-threshold retry for the dim value on a Maxed tile. The observed
  Range selects a matching entry from the complete
  generated preset set. A readable unconfigured Range records
  `unconfigured_range_preserved` and completes without Distance Adjuster input;
  unreadable evidence is flagged and skips Distance Adjuster. For a configured
  Range, the runtime opens the freshly matched in-run Distance Adjuster, OCRs
  both values, and matches each direction arrow immediately before one tap. Every step
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
  First Perk Choice, Perk Bans and Auto Pick priority, Poison Swamp Stun, Bots,
  Guardians, and Modules. The save-first coordinator may omit exact
  allowlisted matches and complete observation-policy Module evidence. Exact
  Farm assignments remain enforced; mapped Tournament assignments are reported
  only. Any canonical same-family Module or explicit-empty assignment and
  calibrated exact Damage/Orb values may omit duplicate UI; unknown names/IDs,
  family or structure failures, ambiguous visual absence, mismatches,
  unsupported values, and contexts remain UI-backed. Navigation alone does not
  discard another accepted component. An actual repair removes only its
  affected check and closes pre-action mapping correlation; unrelated accepted
  decisions remain authoritative. Card recharge traversal checks
  both unresolved Cards on the initial inventory frame and after every bounded
  upward or downward swipe, validates
  whichever is visible in any order, and stops without another swipe as soon as
  both have authoritative evidence. Inspection opens each exact inventory card
  through a verified long press, requires the matching detail identity and an
  authoritative checkbox state, changes only a mismatched checkbox, rechecks
  the requested state, and returns to the Cards inventory. Missing cards and
  ambiguous details fail closed. Perk configuration is changed only for
  requirements independently declared by the selected strategy. First Perk
  Choice has its own tab and exact semantic comparison; Ban and Auto Pick
  repairs retain their independent profile-skip policy. Ban repair completes
  before Auto Pick. It requires two matching authoritative Selected Perks
  snapshots, performs one action, returns to the fixed Selected block, and
  replans from another stable pair. A recognized extra is always removed before
  an Available-list search; a missing ban is never selected into a full block.
  One stable no-op receives at most one newly authorized local retry, and every
  transition remains inside a bounded action budget. Each Ban toggle and Auto
  Pick move recaptures the panel immediately before input, uniquely reacquires
  the same semantic row at its settled coordinates, and requires strict
  transition evidence. Auto Pick repair starts from one complete authoritative
  ranked-prefix capture, returns once to a confirmed top, and keeps that
  semantic order only as planning state. It scans forward for each currently
  misplaced key, skips ranks already made correct by prior moves, and updates
  the ranked prefix only after the target reaches its guarded predecessor
  through locally verified adjacent swaps; a key below the prefix can enter
  while the displaced tail leaves. The Home semantic vocabulary covers every
  perk ID in the current exact-version save mapping. A still-unrecognized local
  predecessor receives bounded fresh local OCR retries and cannot trigger a
  futile ranked-prefix rescan. Guarded predecessor reacquisition uses a
  required semantic boundary: visually stable or ignored reverse swipes and a
  target at the top of one OCR viewport do not prove the global list top. A
  recognized adjacency contradiction retains the bounded semantic
  resynchronization path, which restarts planning at rank one. Cached frames
  never authorize an input. Final authoritative Ban and Auto Pick readbacks
  remain mandatory and are reused by their callers. An unavailable perk,
  unresolved ambiguity, or non-progress ends only that bounded repair, records
  the exact failure, and releases the Home route to continue degraded. Other
  recoverable Home setup failures retain the complete fresh-Home retry policy
  before receiving the same degraded result.
  Persistent control is synchronized before every Home setup tap or swipe.
  The first denied input boundary yields the synchronous setup immediately so
  Pause, Stop, Take Manual Control, capture/detection, and acknowledgements do
  not wait behind that route. It performs no cleanup input while yielding. A
  later explicit Enable may restore verified Home and start a fresh setup pass
  only when the same runtime, target, original workflow operation, and visible
  boundary still match; Stop or a manual-control handoff discards that recovery.
  A Return-Control setup that exhausts its bounded repair completes Return in
  degraded mode with the exact failed check and reason; it does not restore the
  manual Pause or wait for another Enable. The setup retains exact
  UI- or save-derived configuration evidence for session preflight.
  Save-derived sections, including the exact current eight-slot Farm Module
  assignment, are accepted there only after their typed carry binds to the
  exact launched battle; supplied UI screens still override omission
  and detect contradictions. Target Priority has no Home control. Without an
  exact bound save order it records `battle_only_control` and the generated
  `RUNNING` action observes or enforces it; with bound exact ten-item evidence
  that same action consumes the single-use order without opening Target
  Priority. Attaching to an existing battle without boundary proof retains the
  guarded read-only compatibility route. Home-only Free Upgrade locks remain
  deferred there: they record `unavailable_deferred` without a pass, failure,
  Home repair, or Surrender request; Poison Swamp Stun falls back to its guarded
  in-battle detail check; Home `RESUME_BATTLE` preserves the attachment, and
  the Home-owned gates rearm at the next genuine `NEW_BATTLE` boundary.
- A numeric `run_configuration.tier` is a generic launch requirement, not a
  strategy-name special case. Before any ordinary runtime-owned New Battle,
  the Home handler requires fresh `HOME_SCREEN` / `NEW_BATTLE` evidence and an
  exact OCR tier. It moves the selector one arrow at a time, rechecking action
  authority before each input and observing the exact one-tier postcondition
  before the next. The Battle control is recaptured and the requested tier is
  reverified at its final input boundary. Unknown tier evidence, a stable
  no-change result, or lost authority blocks Battle; an unexpected selector
  result remains typed uncertainty and is never replayed. Strategies without
  a numeric tier, Resume Battle, and the separate Tournament launch workflow
  retain their existing behavior. Tier inputs and the final Battle tap remain
  within the launch workflow's single correlated `ACTION` / `RESULT` pair.
- A confident mismatch is repaired only at an already-safe boundary such as
  verified Home `NEW_BATTLE`. An active-battle or otherwise unsafe mismatch is
  recorded once as completed degraded validation; it cannot request Surrender,
  stop/repair/restart, a Strategy Gate, or an operator decision. The matcher
  reports evidence but never directly authorizes an equipment action.
- Running-session validation stores normalized expected/observed evidence and
  the failed requirement IDs. A conclusive mismatch or unavailable validator
  completes the one-shot pass in degraded mode so it cannot repeat every
  heartbeat. Later successful validation clears the degraded state. Legacy
  blocking decisions are consumed rather than re-published.
- A guarded Home repair uses fresh detail/name and action guards and reapplies
  the complete profile-owned no-battle setup. Success records verified
  evidence. Exhaustion records the exact failure and continues; it does not
  retain global action authority merely because configuration is imperfect.

## Process and control ownership

- The persistent control file is authoritative operator intent.
- A missing control file materializes as `PAUSED`. A legacy `RUNNING` record
  without a valid state request ID is also converted to a fresh `PAUSED`
  request. Malformed present identities remain visible as invalid evidence;
  they are never repaired into implicit input authority.
- A non-blocking OS lock keyed by ADB target prevents competing runtimes from
  acting on the same device. Its metadata is `held` with an owner PID while
  acquired and is rewritten to `released` with no PID on a clean release. A
  crash can leave `held` metadata after the OS lock disappears, so metadata
  alone is not proof that its PID is still alive.
- Pause blocks every strategy and handler action while allowing observation and
  status reporting.
- One schema-1 `interactive_development_lease` directive may bind a bounded
  owner label and ordinary lease ID to the fresh production runtime/session,
  PID, exact ADB target, and starting screen/battle evidence. The request is
  not authority. The matching runtime installs `external_development` at a
  safe main-loop boundary shared with watchdog mutation dispatch, stops
  background input, obtains a fresh known observation, and only then publishes
  the separate acknowledgement. The watchdog may continue passive observation,
  but restart and foreground recovery make their final typed lifecycle check
  under that shared guard. A 120-second heartbeat deadline, Pause/Stop,
  runtime/PID/target replacement, or an authoritative battle boundary normally
  makes status inactive and terminates the lease. Resume never revives the
  terminal request. One explicit `owned_battle_start=true` request is accepted
  only from fresh exact Home `NEW_BATTLE` with force-proven inactive state and
  a positive target generation. The Home preclaim is provisional: activity
  scope is irrelevant, and guarded terminal cleanup additionally requires a
  force-bound non-Tournament `ActiveRoundIdentity` matching the terminal. If
  the suppressive lease prevents that checkpoint, production declines cleanup.
  At Game Over the lease itself terminalizes, while the proven claim may
  authorize only the supported minimal return-to-Home terminal route. Target,
  battle-identity, runtime/PID, terminal-type, Pause, or Stop replacement
  performs no cleanup input. The development input helper consumes that
  composite `active`
  decision instead of duplicating the production authority calculation, while
  separately binding its one command to the matching lease, runtime, exact
  target, and acknowledged expiry. Its final pre-input status check reserves
  the complete selected subprocess timeout plus timestamp/dispatch margin from
  production `server_time`.
- A normal development release remains held through the first post-release
  capture and detection. A known same-battle screen permits the runtime to
  publish the terminal result and remove the hold. An ambiguous screen or
  failed terminal write retains the hold; natural Game Over terminates the
  lease. Ordinary leases restore normal production terminal authority without
  claiming the battle. Only the exact preclaimed owned-battle variant retains
  the narrow process-local minimal-Home cleanup claim described above.
- Control synchronization precedes capture, so an ADB outage cannot prevent a
  Pause acknowledgement or a paused target-handoff request. Connection recovery
  may continue while paused but may not foreground or restart the game.
- The persistent Linux control service owns normal ADB TCP registration for a
  systemd-managed runtime. Its thread-safe, target-keyed coordinator follows
  bounded retries, refreshes only the selected transport, and remains alive
  across automation stop/start cycles. The installed automation unit explicitly
  selects observer mode, preventing two reconnect owners. A direct manual
  runtime combines registration and observation in its own coordinator.
- Managed frame capture and the watchdog share an observe-only coordinator. A
  known disconnection suppresses screenshot commands and repeated low-level
  failure entries while the main loop continues its short control-poll cadence.
  Registration recovery requires the exact target to freshly report `device`;
  command text such as `connected` or `already connected` is not authority.
  Offline and unauthorized rows are outages. Runtime recovery is complete only
  after a supported fresh frame succeeds, and malformed captures while the
  transport remains connected retain their normal diagnostics.

### Emulator maintenance and restart replay

One schema-1 `emulator_maintenance` directive represents a BlueStacks restart
requested either by the Windows control surface's conservative, default-off
[degradation detector](control_surface.md#automatic-bluestacks-degradation-recovery)
or its confirmed operator command. The initiator is durable provenance; it
does not change recovery input semantics. Before the hold is installed, the
request is bound to the exact Windows executable, instance, listener port,
host, PID, and process start time plus the exact runtime ID, PID, ADB target,
positive target generation, authorizing state-request ID, and canonical battle
identity. Request creation atomically rechecks that Enabled control identity. It
is not host authority. At the next fresh `RUNNING` boundary, the matching runtime
installs the exclusive `emulator_maintenance` hold, stops background input,
captures its last trusted wave and confirmed Intro Sprint state, and publishes
a separate runtime acknowledgement. Windows may mutate the host only while
that acknowledgement is fresh, exact-owner-matched, battle-identity-matched, and
explicitly `host_restart_authorized`. The atomic host acknowledgement rechecks
that the same state request is still `RUNNING`; a Pause that commits first
therefore prevents host mutation.
A canonical battle-identity change before that host acknowledgement terminates
the request without mutation; a request for one battle can never transfer to
its successor. Activity-log scope rotation has no effect.
If Windows never acknowledges an old process identity, the pre-mutation request
expires after three minutes and the runtime releases its hold. There is no such
guess after durable host acknowledgement: a lost Windows result may mean the
process already stopped, so the hold remains until exact reconciliation.

The hold precedes capture on every later loop and, until the game source is
restored, suppresses ordinary Strategy, handler, auxiliary,
watchdog-foreground, and lifecycle actions. Capture, detection, control
synchronization, status, and exact ADB reconnection may continue. Once fresh
evidence proves that Resume returned to the same `RUNNING` battle, the hold
continues to exclude run-progression and lifecycle work but allows the existing
typed independent-collector lane: in-battle ad gems, floating-gem scans, daily
gems, and Daily/Weekly/Event/Guild rewards. Every collector retains its normal
fresh-screen, canonical battle-identity, route-ownership, and final Pause
rechecks. Pause and
Stop cannot be bypassed by the hold. Pause before host acknowledgement prevents
authorization; after an accepted Windows restart it blocks Linux game input
while the durable host result remains available for later reconciliation.

After Windows proves a different exact `HD-Player.exe` listener owner, Linux
waits for the configured ADB target, permits at most three bounded Android
launcher attempts for `com.TechTreeGames.TheTower`, and requires fresh UI
evidence. An uncertain launcher dispatch is never replayed; an accepted launch
retains a 45-second source-restoration receipt. The distinct post-process
**Welcome Back** modal is the blocking-primary `GAME_RESTARTED` state, not Home
`RESUME_BATTLE`. From freshly rematched complete frames, recovery runs one
bounded **Resume** transaction. If fresh post-input evidence proves that the
modal remains, it runs a separate bounded **End run** transaction and continues
through natural Game Over/Home handling; it does not stop at a notification
while leaving a known non-resumable run in place. Missing post-input evidence
is an uncertain result that Pauses without replaying the input.

BlueStacks Home may expose the configured framebuffer as the exact landscape
transpose of a supported game resolution (`1920x1080` or `1280x720`). That
frame is retained as typed native-geometry evidence but is never normalized,
published as a canonical screenshot, classified as game UI, or used for mapped
coordinates. Only while an exact-target maintenance hold is active in durable
`host_restarted` may a fresh transpose from that request's ADB target enter the
existing bounded package-launch transaction. It authorizes no tap or Home
control; every later Welcome Back, Resume, fallback, and completion decision
still requires fresh supported portrait UI evidence.

The official [v28.0.6 patch note](https://www.techtreegames.com/post/v28-0-6-patch-notes)
documents that process-restart resume returns five waves, or fifty waves while
Intro Sprint is active. Those counts are retained as diagnostic expected
floors, not trusted completion boundaries; production has also observed a
fifty-wave rollback without confirmed Intro Sprint. The runtime instead keeps
every resumed `RUNNING` frame out of ordinary run-progression observers until
wave OCR reaches the captured pre-restart high-water. During that window,
wave-monotonic state, Coins/min, Perk/mission observations, strategy actions,
and activation accounting do not see the replayed non-earning waves, while the
typed independent collectors above may continue operating. If no trusted
pre-restart wave exists, the first fresh numeric `RUNNING` wave is the strongest
available completion boundary.

When the old battle is positively non-resumable, **End run** is followed by the
ordinary full terminal collector with an `interrupted`, nonrepresentative,
analytics-excluded disposition. Recovery then permits only verified Home
`NEW_BATTLE`, runs the configured new-battle preflight and action guards, and
keeps its hold after the Battle tap until a fresh replacement `RUNNING` frame.
An accepted Home control has a process-local postcondition receipt and is not
repeated on the next heartbeat. If New Battle exposes the known Free Ticket
blocker, its exact receipt owns one bounded Claim transaction; after two stable
Home observations, at most one final verified Battle retry is allowed. Home
`RESUME_BATTLE` remains an allowed alternative only while fallback has not
committed to a new battle. Unknown or transient screens retain the hold and
retry only the bounded app launcher; they never infer a Surrender, Resume, or
Battle target from absence.

A resumed record carries the request ID, canonical battle identity, high-water, expected
floor, lowest observed wave, request initiator, and catch-up disposition under
`runtime.emulator_recovery`. The interrupted old record receives the same
provenance, while its replacement battle does not. Any record with that
provenance is excluded from later degradation calibration so restart downtime
and non-earning replay cannot train the trigger. The complete workflow emits
one `ACTION`/`RESULT` pair plus an `INPUT` entry for each Android launch or
verified UI action. Once fresh evidence proves the old battle caught up or the
replacement battle is running, semantic recovery releases the local input hold
before persisting the terminal receipt. A receipt-write failure is reporting
degradation only: later heartbeats retry that receipt without reacquiring the
hold or replaying input.

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
  process replacement. The control service persists and refreshes the new
  registration before publishing the directive. The runtime acquires the new
  per-target lock, temporarily selects that endpoint, requires exact `device`
  state and a supported capture, then releases the old lock and acknowledges
  the directive. Failure restores the previous runtime target and Pause while
  bounded registration retries continue for the saved next-start target.
  Existing mission, strategy, and gate state stays in memory throughout.
- Process startup has an explicit gate policy. `immediate` retains the normal
  behavior in which the first observed active battle is a new-run boundary.
  `next_run` adopts the first active/resumable battle and structurally
  suppresses plan rules tagged `run_initialization` or `session_preflight`,
  except explicitly declared `run_when_attached` checks. Tournament stages its
  read-only inventory check first, then measures its battle-only Damage Slider
  and Orb Distance rules without changing the attached battle.
  It does not seed their completion variables. Game Over, Tournament Results,
  or Home `NEW_BATTLE` arms the gates, and the next `RUNNING` observation emits
  the normal run-start hooks. Home `RESUME_BATTLE` and transient Unknown states
  preserve the attachment. Managed same-battle Stop/Start uses `next_run` only
  as a transient launch marker when a durable handoff is pending. That runtime
  waits for a fresh Attach rather than treating the retained identity as
  authority, and the normal persisted startup policy is restored immediately.
- Save-backed battle identity is independent of process-local log/report
  segmentation. Home `NEW_BATTLE` forces a save and must prove an inactive
  round before launch. The first stable `RUNNING` after Start, direct Retry,
  Enable, or a terminal Home continuation forces another save and binds the
  exact `ActiveRoundIdentity` before any battle-bound work. Active Attach uses
  that transaction directly. Home Resume is deliberately two-proof: a first
  forced save identifies the Resume target before the tap; dispatch then makes
  that proof non-authoritative, and the first stable `RUNNING` frame forces
  again before adoption. The serializer binds the
  exact runtime operation, target generation, visible source, control, and
  source restoration; log-scope creation or rotation is irrelevant.
- `BattleIdentityStore` retains the last force-proven identity across process
  restart. `SAME_BATTLE` permits only exact identity-bound receipt reuse;
  `LATER_BATTLE` discards old battle-local state before adopting the successor;
  a forced inactive Home proof closes the old identity. Pause or Stop makes
  retained identity comparison-only. It never becomes current again without a
  new forced serialization.
- A battle-identity read has no Battle History, elapsed-time, visual, or
  activity-scope fallback. A safely restored transient acquisition failure is
  retried only through bounded forced serialization; the active source remains
  input-blocked. Loss of target/control/owner or unproved restoration remains
  catastrophic. Direct Retry never polls History or waits for a natural save:
  its terminal bundle is reused for terminal projections and its successor ID
  is forced at first stable `RUNNING`.
- Completion of a session configuration gate records an identity-bound
  receipt. An exact matching receipt may be reused after restart only when a
  forced attachment proves `SAME_BATTLE`; it does not restore action-authority
  state. A malformed receipt, missing identity, or `LATER_BATTLE` runs the
  declared attachment checks normally. Activity-log scope may carry a
  report-only copy, but it neither permits nor suppresses reuse.
- Attach owns an immutable Strategy request snapshot through terminalization.
  A later Strategy selection cannot overwrite it; the later request remains a
  next-safe-boundary change. After Attach completes, an incompatible or
  unprovable degraded observer cannot be converted by an active-battle request;
  the exact durable request is downshifted to the next boundary. A separate
  explicit mid-run adoption for an intentional observer or an already
  Strategy-run battle may replace normal strategy behavior and report identity
  without a restart, but run initialization, session preflight, and Home-only
  checks stay deferred. Any current-battle degradation remains attached to the
  run. Its explicitly declared
  `run_when_attached` configuration checks are observational. A request
  encountered at Home `NEW_BATTLE` follows normal boundary replacement instead
  and runs the complete startup-gate sequence.
- Process replacement must verify the existing owner and safe UI boundary,
  then verify the replacement PID, refreshed lock, startup log, control
  consumption, and first state report.
- The guarded same-battle replacement makes that contract executable across
  the ordinary complete Stop and later Start calls. Stop records a handoff only
  from a fresh `RUNNING` observation, exact systemd MainPID and held ADB lock,
  force-proven battle identity, and an already owned active-battle lifecycle.
  Ownership has no origin distinction here: a battle started by automation and
  one attached later produce the same eligible lifecycle. The replacement
  launches once with `next_run`; the persistent next-start policy is restored
  immediately after systemd copies its launch environment. The new runtime
  consumes no old input authority. It creates a normal fresh Attach bound to
  the new PID and lock, and forced serialization must equal the handoff battle
  identity before lifecycle adoption and ordinary actions resume. Waves may
  advance throughout because they do not change that identity. A later or
  ended battle, changed target, unavailable proof, or reporting failure leaves
  the replacement Paused and records a terminal handoff result.
- Remote lifecycle control is limited to the configured
  `thetower-automation.service` systemd user unit. A start crosses the process
  boundary under persisted `PAUSED`. Without a same-battle handoff it waits for
  explicit intent. With one, the control service publishes `RUNNING` only after
  the replacement unit is active and then waits for the fresh Attach to finish.
  A stop persists `STOPPED` and any eligible exact-battle handoff before systemd
  signals the unit.
  A stopped request may persist one validated localhost ADB TCP port and one
  validated startup-gate policy for the next start; an acknowledged paused
  runtime may apply that same restricted port as a live target handoff. Remote
  requests cannot supply a PID, unit name, executable, host, path, or shell
  command.

## Planned evolution

Production/development coordination, shared screenshots, and the planned
interactive lease are governed by the canonical
[development coordination architecture](development_isolation.md). Production
remains the sole long-lived runtime and normal input owner. Cooperative workers
may read or copy production artifacts and, after the live startup inspection,
run bounded exact-target ADB reads or captures without an interactive lease.
Connection management and routine continuous capture remain production-owned.
One explicitly operator-authorized, task-bounded no-control stream is the
documented exception for passive observation; it follows the
[passive-stream procedure](../operations/passive_stream.md) and grants no input
authority.

Interactive development now uses the existing control-surface/directive path,
one cooperative expiring request, and the distinct suppressive
`external_development` hold that production acknowledges through its existing
runtime-owned authority snapshot. It is a coordination boundary, not a
same-user security boundary: no source attestation, secret token, authenticated
peer protocol, semantic action catalog, or cryptographic audit is planned. A
worktree-local lock or screenshot never grants input authority.

The development-side `tools/development_adb_input.py` helper now consumes that
composite status for one canonical tap or swipe. It establishes exact-target
native geometry through a bounded read, rechecks the unchanged lease, runtime,
and target binding immediately before one finite-timeout input attempt, writes
the existing `ACTION`/`INPUT`/`RESULT` audit sequence, and never retries
uncertainty.
It adds no in-process runtime authority route; ad-hoc worker input remains
unsupported.

An app-owned frame source and short-lived UI-state action lease are the intended
direction for multi-frame decisions and latency-sensitive scheduled actions.
The observer should publish frame sequence, observation time, state, and
invalidation state; an action should make an O(1) freshness check immediately
before input. Navigation, non-running evidence, pause, capture failure, and
staleness invalidate the lease.

This planned package is required for automation decisions, shared low-latency
frame publication, or unattended capture ownership. A bounded passive viewer
does not implement that package and may not bypass current screenshot or input
guards. Requirements and benchmarks live in
[`../backlog/state-and-detection.md`](../backlog/state-and-detection.md#capture-and-action-architecture).
