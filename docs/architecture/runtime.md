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
    complete + allowlisted + matching -> accept the saved state; skip that UI route
    otherwise                         -> run the existing UI check
                                           |-- match: continue
                                           `-- repair: verify in UI and invalidate the snapshot
```

`PlayerSavePreflightCoordinator` owns that decision at an ordinary exact Home
boundary. The default `save_first` policy records the current runtime,
preflight/activity scope, exact ADB target and generation, selected strategy,
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
can still contradict carried evidence. Module decoding is value-scoped by
exact slot rather than a generic inventory map. Farm's four Primary and four
typed Assist assignments remain enforced. The Tournament reference is also
fully mapped: Primary Amplifying Strike (`45`), Orbital Augment (`46`), Project
Funding (`43`), and Dimension Core (`38`); Assist Being Annihilator (`9`),
Anti-Cube Portal (`20`), Singularity Harness (`30`), and Harmony Conductor
(`39`). A same-run stable-save/UI pairing additionally established armor
Primary Anti-Cube Portal (`20`) and armor Assist Space Displacer (`19`).

Exact slot, family, role, mapped name, unlocked Assist state, and complete
structure must agree before save evidence can replace Modules observation.
Tournament's `observe` policy records a fully decoded difference from
`tournament_standard` as `save_observation`; it neither fails the gate nor
authorizes a repair. An `enforce` policy still requires exact equality.
Magnetic Hook, any other unsupported requested name or unknown slot value, and
malformed or partial structures retain the complete Modules UI path. These
facts do not map rarity, levels, stars, effects, substats, inventory semantics,
GUIDs, or private record values. Orb Distance and Damage Slider remain
UI-authoritative.

Session-only accepted decisions become typed, single-use carry for exactly the
next runtime-owned `NEW_BATTLE` launch and its first stable `RUNNING` boundary. The
current carry covers Auto Pick enabled `true`, a complete exact ten-ID Target
Priority order, an exact enforced Farm Module assignment or a complete
observation-only mapped Tournament assignment, the all-nine-primary-on
aggregate, Spotlight-Missiles-on, Poison Swamp Stun, and exact Home sections
needed by the later consistency check. The version-1073
Target Priority map is `0=Closest (Default)`, `1=Basic`, `2=Fast`, `3=Tank`,
`4=Ranged`, `5=Boss`, `6=In Spotlight`, `7=Protector`, `8=Elites`, and
`9=Fleets`; complete membership, uniqueness, and ordered policy comparison
remain mandatory. Runtime/preflight/activity identity, target generation,
strategy and configuration fingerprint, action/control authority, launch dispatch, and
first-RUNNING transition must all remain unchanged. Restart, attachment,
unrelated Retry, manual/ambiguous launch, WAIT/Pause/Stop, target/configuration
change, repair, or a later battle rejects every carried decision.

A pre-action snapshot never confirms the result of an input. The first UI
repair is verified through fresh UI evidence and invalidates every remaining
save decision; subsequent checks use UI unless a complete new guarded
serialization is deliberately performed. Read-only inspection does not erase
unrelated matches, while UI evidence from any screen that was actually opened
still detects contradictions. Missing later-session screenshots are accepted
only for the exact section/component carrying bound accepted provenance:
`save_match`, or `save_observation` solely for observation-policy Modules.
Neither disposition authorizes a tap, repair, lifecycle transition, battle
start, attachment, terminal binding, dispatch, or strategy action.

ADB acquisition requires two identical consecutive reads before decoding. The
container size, gzip integrity, NRBF root, exact version identity, and
structural signature are validated before mapped values are published.
Preflight evidence retains only a redacted source fingerprint, exact version
and mapping metadata, normalized allowlisted decisions, the configuration
fingerprint, and redacted session/target-generation provenance. Account
identifiers, raw saves, decoded roots, arbitrary history, private values, and
raw exception text are not copied into preflight evidence. The component
contract and version-update procedure are
in [`../modules/player_save_import.md`](../modules/player_save_import.md).

#### Save-first active-round and terminal evidence

The normalized runtime-save model is a second privacy boundary inside the
exact-version decoder. Snapshot schema 2 exposes only the fields allowlisted by
`data-9-game-1073-runtime-audit-v2`; it never publishes the decoded root or an
arbitrary `BattleHistoryEntry`. A root-level version or structural failure
publishes no runtime model. Perks and the history tail fail independently so an
unknown Perk ID cannot leak a partial inventory, while an unknown `killedBy`
ID blocks only the semantic completed entry and preserves structural
tail-change evidence. The same authoritative Home snapshot now also supplies
the initial activity-continuity baseline before the UI route is eligible; it
is not acquired a second time.

For an active save, the guarded identity is exactly
`(versionNumber, currentTier, roundsStartedThisTier[currentTier], roundSeed)`.
It is accompanied by `roundActiveBool`, `currentWave`, `saveRevision`, capture
time, source fingerprint, and bounded container metadata. The identity has a
canonical fingerprint. The authorized Tier 22 natural boundary proved that a
known Home state preceded a new seed and per-tier counter, then that the exact
identity stayed stable while revisions and waves advanced through the last
active snapshot. The decoder's active-round projection remains observation
only. The separate activity-continuity consumer uses only the source-tagged
structural newest-tail identity: it may omit the initial Home Battle History
read and close a runtime-owned direct-Retry baseline, but it does not authorize
attachment, terminal record construction, lifecycle input, or Strategy facts.

The in-battle Perk projection requires exact agreement among the 50-entry
`perkLevel` array, `perksPickedCount`, and every ordered `PerkPick(wave, perk)`
entry. It publishes canonical Perk IDs, selection waves, level-after values,
and a stable snapshot fingerprint. Perk ID `0` is `max_health` (Max Health).
The 50 positions are storage capacity, not evidence that every index names a
possible Perk; the exact-version table currently maps all 34 defined semantic
IDs, including ID `11` as `unlock_random_ultimate_weapon`.
Changed entry shape/class, non-monotonic waves, or any count/list/level
inconsistency publishes no Perk snapshot. A structurally consistent unknown ID
retains a private numeric calibration projection for the audit sidecar, but it
does not appear in the public runtime dictionary and does not create a partial
semantic snapshot. An inactive zero/empty projection is explicitly `cleared`;
later runtime ownership must retain the newest complete same-round snapshot
rather than treating that cleared save as final-Perk evidence. At the Tier 22
boundary, the last complete active snapshot contained 15 internally consistent
ordered picks that exactly represented the terminal UI's 11 collapsed rows,
and the immediate stable post-death projection was inactive/cleared. The
normalized exact-version snapshot is therefore ready for a future fail-closed
consumer, but that same-round cache and navigation decision do not exist yet.

The same privacy boundary will expose additional active-round components only
through exact-version manifests. In-battle Attack, Defense, and Utility levels
are stored separately from their Workshop baselines. The save does not carry a
literal gold-box flag; a normalized `maxed` claim therefore requires a
versioned index and maximum-level table and publishes the current level,
Workshop baseline, and round delta with that claim. An unknown index, special
level rule, or cap makes the complete upgrade component unavailable rather
than guessing from a large value.

Survival abilities are checkpoint state, not an event log. Demon Mode, Nuke,
and Second Wind each expose round counts plus candidate active, cooldown,
recharge-wave, and effect-timeout fields. Those fields may establish that an
activation occurred and, after causal calibration, may identify the latest
activation wave from a countdown or absolute-wave relationship. A single late
snapshot cannot by itself reconstruct every earlier activation. Exact waves
must never be inferred until the versioned mapping proves the field units,
sentinels, reset behavior, recharge length, and serialization timing.

The history component accepts the game's source-ordered list of at most 30
entries and exact-shape-validates only its newest 148-field entry. UTC and local
.NET DateTime ticks use different clock bases, so they are normalized
individually and never compared across kinds; source order owns the tail. A
privacy-safe structural identity/fingerprint uses battle date kind/ticks,
tier, wave, game/real time, numeric `killedBy`, and Tournament identity. It is
independent from the canonical 16-section, 144-row More Stats projection and
its semantic fingerprint. The two hourly rows and effect-active percentages
are explicit derivations. `adGemsThisRound` supplies Ad Gems; base/ad coins are
absent.

Cross-channel-validated cause values are `1=Fast`, `2=Tank`, `3=Boss`,
`6=Vampire`, `8=Scatter`, and `99=Surrender`. Surrender is a display value for
the terminal cause and carries no claim about whether the operator or
authorized automation initiated it. A future unknown numeric value remains in
structural tail identity so rollover/change detection works, but the semantic
completed entry is unavailable and terminal capture stays on UI evidence.

The same Tier 22 audit changed its known pre-battle capped-tail baseline to a
Tier 22, wave 751, Boss candidate whose complete 144-row projection agreed with
the terminal UI. This validates the natural-boundary causality and projection,
not runtime attachment. The dated evidence and exact row-level promotions are
recorded in
[`player_save_import.md`](../modules/player_save_import.md#2026-08-02-tier-22-natural-boundary-audit).

Runtime adoption proceeds in bounded vertical slices with these ownership
rules:

1. A future normal-runtime Perk checkpoint cache may consume naturally
   serialized stable revisions without navigation or input and independently
   of collector opt-in. It must bind each complete checkpoint to the exact
   active identity.
2. The completed new-round audit permits an implemented consumer to bind only
   an exact active identity to a round without Battle History navigation; no
   such consumer is implemented yet.
3. Perk strategy facts may advance only from a newer complete snapshot carrying
   that same identity; a stale, different-round, or incomplete snapshot cannot
   drive strategy. The saved `PerkPick` wave remains the exact event wave even
   when the stable revision is observed later.
4. Upgrade and survival components advance independently. A malformed or
   unvalidated ability timer cannot erase valid Perks or upgrade evidence, and
   none of these components grants UI action authority.
5. The observer retains the newest complete same-round Perk, upgrade, survival,
   and allowlisted tally snapshots across post-run clearing.
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
8. Normal completed records may be built from a newly serialized history entry
   only after the pre-boundary structural tail fingerprint changed at this Game
   Over, the semantic entry is complete, and tier/time/wave evidence matches the
   bound round.
9. Terminal Battle History counts reconcile the cached save checkpoints and
   visual tail. Missing event timing remains explicitly unknown or bounded;
   count disagreement, impossible ordering, or an unbound snapshot forces the
   full UI audit rather than fabricating events.
10. One passive compact Game Stats capture remains for optional base/ad coin
    split augmentation. Its absence never invalidates otherwise authoritative
    save-derived battle stats.
11. The future cache obtains a terminal stable save to close the final Perk
    prefix. Game Over opens the Perks panel only when that complete final
    same-round prefix has not been proven, or when an ID, acquisition,
    continuity, audit, or final-state condition requires UI.
12. The complete existing Game Stats/Perks/More Stats path remains the forced
    audit and fallback for every missing, unknown, stale, changed, inconsistent,
    or unbound save claim.
13. Wait, Retry, Home, every setting mutation, post-action verification, and
    terminal transition confirmation remain owned by verified UI controls.

That future Perk-timeline phase is documented only; it is not implemented by
the save-first configuration/history change. The runtime does not background
an active battle merely to accelerate serialization. Any optional forced
active-battle serialization requires a separate explicit runtime policy and
must preserve control and lifecycle authority.

Save-tail causality does not relax the independent current-process
`runtime.run_binding` boundary. A process that starts only on a terminal remains
unbound and cannot inherit Strategy, run configuration, Perk history, survival
events, or other process-local evidence. A future save-derived attachment may
identify the completed round only through its own guarded evidence; it cannot
manufacture active-process continuity.

The normalization foundation itself does not poll, cache, bind a process,
build a persisted battle record, or alter `App`/Game Over dispatch. The
implemented audit sidecar below polls and keeps only process-local audit state;
it still does not bind a battle, construct or attach a record, or alter
dispatch. Any normal-runtime consumer remains a later slice gated by the
versioned audit matrix in
[`player_save_import.md`](../modules/player_save_import.md#versioned-audit-matrix-data-9-game-1073--revision-2).

##### Implemented natural-boundary audit collector

`V1073-RUNTIME-013` is implemented as an explicitly enabled,
observation-only sidecar, not a normal-runtime evidence consumer. It consumes
stable exact-version reads and passive boundary observations without pausing or
backgrounding the game, navigating, tapping, dispatching a handler, changing a
lifecycle decision, or suppressing UI. Its one daemon worker allows at most one
bounded stable-read acquisition at a time, so a slow or failed read never
delays the App heartbeat. It reads only the exact target owned by the current
`AdbTargetSession`; a handoff/release generation change discards the result.
Capture and detection continue to feed it while global Pause blocks actions.

Each collector start creates new runtime and collector session identities and
appends canonical JSONL records without reading or rewriting an earlier
session. Its exact-version manifest and receipt schema retain only:

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

The existing Perk timeline may also feed a stripped calibration batch after it
has independently accepted a complete exact selection boundary. Display text,
OCR output, colors, pixels, and restored timeline checkpoints never cross this
queue. When a stable save is structurally complete but contains an unmapped
numeric ID, a pure resolver groups save picks and UI selections by exact wave,
cancels already mapped semantics, and applies singleton constraint propagation.
It restores the semantic Perk projection only if every needed assignment is
unique, uses an already allowlisted Perk family at at least 80% confidence, and
successfully appends the calibration receipt. Multi-wave aggregates, visibility
gaps, count differences, duplicate semantics, conflicting later evidence, and
ambiguous assignments stay unavailable. The static exact-version manifest is
never rewritten.

The collector begins with a pre-round structural-tail baseline, records the
first naturally serialized active identity, samples only stable revision
changes at the audit cadence, and observes the natural terminal transition. It
records the last complete same-round Perks, the first inactive/cleared save,
and any structural tail change—including 30-entry rollover—as candidates. It
may calculate candidate tier/wave/time agreement, but it cannot call that entry
attached, update a battle record, decide whether to open Perks, or suppress any
UI route. Raw saves, decoded roots, account identifiers, arbitrary history
fields, screenshot pixels, and OCR text are outside its retained schema. The
existing visual activation tracker continues to retain only its confirmed
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

The authorized Tier 22 boundary supplies the collector's core natural-round
evidence: new identity, ordinary-foreground revision progress, final
Perk/clearing behavior, and Game Over tail serialization. The first explicitly
enabled ordinary Tier 19 run subsequently validated exact Home through stable
active revisions and natural terminal clearing/tail publication without
changing the UI path. Its first direct Retry exposed fail-closed old-identity
retention and seven missing exact-version Perk IDs; `b137ea4` repairs both and
is deployed. Its fresh-session revision-46521 checkpoint accepted the new
counter-232 identity and complete mapped Perk progression while correctly
reporting that a terminal-only restart supplied no pre-round baseline. The next
ordinary same-process Retry remains the passive rollover confirmation. No
replacement purpose-built battle is required. Upgrade, survival-ability, and
other candidate components remain independently unavailable until their own
matrix rows are promoted, but they do not gate the core collector. The complete
Game Stats, Perks, More Stats, continuity, terminal-binding, and terminal
lifecycle paths remain authoritative and unchanged.

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
its fresh boundary, maxing EHLS first and EALS second. Its battle-only session
rules enforce Damage Slider `100%` and the configured Orb Distance pair before
the remaining observer check completes. It then becomes passive except for
game-speed maintenance, ad-gem collection, and terminal-result handling. An ad
gem starts the same bounded floating-gem sweep used by Farm; the profile does
not run an independent or continuous floating-gem handler. No Tournament
battle gains validation-battle Surrender authority.

Automatic validation of an already-running Tournament does not use the
exclusive validation receipt. Without Home boundary evidence, it first
inspects Cards, Ultimate Weapons, Modules, Bots, and Guardians in battle.
Workshop is the only check that takes resumable Exit Battle → Go Home. Once
that inventory pass reaches a conclusive result, the explicitly
`run_when_attached` battle-only rules enforce Damage Slider `100%` and the
configured Orb Distance for an authoritative configured Attack Range; a
readable unconfigured Range is preserved without opening Distance Adjuster.
The attachment path never selects a Home preset, equips a loadout, requests
Home repair, or gains Surrender authority, and it must verify that Resume
returns to the same Tournament. The separate guarded process-reload workflow
retains its explicit `next_run` compatibility policy; it is not the user-facing
validation choice.
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

Process-local run evidence has an additional terminal binding boundary. The
current process must observe an active `RUNNING` battle after activity
continuity has settled, and the activity-scope ID at the terminal must still
match that observation. Starting directly on Game Over, or reaching a terminal
after the scope changes without another active observation, is `unbound`.
Terminal UI capture remains valid, but the record omits selected Strategy,
resolved run configuration, last-wave and coin samples, game-speed history,
session-preflight evidence, Perk timeline, and survival-ability observations.
Any restored process-local Perk or activation state is reset so it cannot leak
into a later record. The JSON and Markdown retain an explicit warning and a
versioned `runtime.run_binding` reason.

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
| Running-battle Strategy Gate | Continues | Only the explicit safe allowlist | Blocked | Blocked |
| Continuity, initialization, validation, or exclusive screen hold | Continues | Blocked | Matching bounded owner only | Matching bounded owner only |
| `external_development` hold | Continues | Blocked | Blocked for every owner | Blocked for every owner |

`external_development` is the one intentionally suppressive hold. Unlike
initialization, preflight, continuity, and exclusive-validation ownership, it
has no matching in-process bypass: even `owner=external_development` is denied.
It therefore stops normal strategy, handler, auxiliary, initialization,
validation, recovery, lifecycle, and blind/background input without changing
`AUTOMATION.state` or representing itself as Pause. Watchdog connection,
process, and foreground observations continue, but its restart and foreground
paths recheck lifecycle authority under the mutation guard shared with hold
installation. An already-authorized recovery retains that guard through its
last mutation; production cannot install the hold or acknowledge quiescence
until it finishes.

An active Strategy Gate is distinct from Pause and never mutates the control
file or `AUTOMATION.state`. It is scoped to the current activity/run identity
when available and persists while the same battle visits Store, Mission,
Event, Guild, reward-reveal, side-menu, and other temporary screens. Entry and
changed evidence are logged once per transition rather than once per frame.
The gate is released only by successful validation, accepted retry, a
run-scoped waiver, an explicit active-battle strategy change, an explicitly
authorized repair transition, changed authoritative run identity, or a natural
battle boundary. Notification-only/`observe` mismatches retain evidence without
activating the blocking gate. Natural Game Over releases it before normal
terminal handling, but the gate can neither authorize nor dispatch Surrender,
Exit Battle, Go Home, restart, or New Battle.

The gate's auxiliary allowlist is explicit: in-battle ad-gem collection and its
bounded floating-gem scan, Daily Gem Store, Daily and Weekly Mission rewards,
Event Mission rewards, and Guild chest rewards. Authority is necessary but not
sufficient: all existing badge, rollover, claim-limit, Sunday-hold, eligibility,
cooldown, and scheduler rules still decide whether a collector is due. A
collector cannot navigate to Home or cross a battle boundary merely to collect.
Home ad gems retain ordinary Home handling outside this running-battle gate.

Daily Gem and mission collectors claim an exclusive auxiliary-route lease from
a freshly detected same-battle `RUNNING` frame before their first input. While
that lease exists, no other collector, background floating-gem tap, strategy
handler, lifecycle handler, or generic recovery owns the screen. Every input
checks a fresh screen precondition and then rechecks control state, typed
authority, route identity, and battle scope at the final dispatch boundary.
Pause or lost authority retains the exact collector cleanup state without
sending more input. After authority returns, only that collector's bounded
cleanup may resume. Game Over, Home boundary, run-identity change, or an
unexpected state abandons the route without cleanup input so the authoritative
boundary handler can take ownership.

`RuntimeActionAuthorityPublisher` atomically refreshes
`logs/strategy_action_gate.json`. Schema version 1 includes runtime/ADB/PID
ownership, observation time, runtime-active flag, staleness threshold, active
gate and run scope, current battle-active/scope evidence, strategy,
source/phase, failed checks, reason, activation and update times,
Pause/Stop/hold context, all four authority decisions, currently allowed
collectors, any auxiliary-route lease, and the separate interactive-development
runtime acknowledgement when applicable. The control surface accepts the
channel only while its timestamp is fresh, `runtime_active` is true, and its
PID/target owner matches the active runtime lock. It never derives gate or
development-lease authority from warning text in `actions.log`.

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
  exclusive ownership, shutdown, battle-scope change, or loss of the detected
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
  First Perk Choice, Perk Bans and Auto Pick priority, Poison Swamp Stun, Bots,
  Guardians, and Modules. The save-first coordinator may omit exact
  allowlisted matches and complete observation-policy Module evidence. Exact
  Farm assignments remain enforced; mapped Tournament assignments are reported
  only. Unsupported Module names, Damage Slider, and Orb Distance remain
  UI-only. Navigation required for one fallback does
  not discard an unrelated accepted component. Card recharge traversal checks
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
  before Auto Pick: extra selections are removed from the fixed Selected Perks
  block,
  while only missing required bans search the Available list. Each Ban toggle
  and Auto Pick move recaptures the panel immediately before input, uniquely
  reacquires the same semantic row at its settled coordinates, and requires
  strict transition evidence. Auto Pick then rebuilds semantic rank from the
  top and requires exactly one-rank upward progress after every tap. A final
  exact comparison remains mandatory; ambiguous OCR, an unavailable perk, or
  non-progress blocks New Battle. Persistent control is synchronized before
  every Home setup tap or swipe. Pause holds the workflow action-free, and
  Resume restores verified Home before a fresh setup pass. The setup retains
  exact UI- or save-derived configuration evidence for session preflight.
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
- One schema-1 `interactive_development_lease` directive may bind a bounded
  owner label and ordinary lease ID to the fresh production runtime/session,
  PID, exact ADB target, and starting screen/battle evidence. The request is
  not authority. The matching runtime installs `external_development` at a
  safe main-loop boundary shared with watchdog mutation dispatch, stops
  background input, obtains a fresh known observation, and only then publishes
  the separate acknowledgement. The watchdog may continue passive observation,
  but restart and foreground recovery make their final typed lifecycle check
  under that shared guard. A 30-second heartbeat deadline, Pause/Stop,
  runtime/PID/target replacement, or an authoritative battle boundary makes
  status inactive and terminates the lease. Resume never revives the terminal
  request.
- A normal development release remains held through the first post-release
  capture and detection. A known same-battle screen permits the runtime to
  publish the terminal result and remove the hold. An ambiguous screen or
  failed terminal write retains the hold; natural Game Over instead terminates
  the lease and restores normal production terminal authority on that fresh
  observation.
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
  read-only inventory check first, then permits its guarded battle-only Damage
  Slider and Orb Distance rules.
  It does not seed their completion variables. Game Over, Tournament Results,
  or Home `NEW_BATTLE` arms the gates, and the next `RUNNING` observation emits
  the normal run-start hooks. Home `RESUME_BATTLE` and transient Unknown states
  preserve the attachment.
- Current-run activity continuity is verified independently of process and
  strategy attachment. In `save_first`, the authoritative Home snapshot feeds
  its source-tagged structural newest history-tail identity into the new scope
  before the guarded UI route may run; acquisition/shape failure safely falls
  back after Home setup, while target/control/scope/restoration loss authorizes
  no input. `force_ui` and `comparison_audit` retain the UI route. A replacement
  process still compares a UI-derived persisted baseline at `RUNNING`, Home
  `RESUME_BATTLE`, or a Battle History screen left open by an interrupted
  inspection. Equality preserves the scope; a changed report creates a new
  scope whose log boundary includes the continuity action. A readable identity
  is persisted with a run-ID compare-and-set so a stale inspection cannot
  overwrite a newer lifecycle boundary.
- A successful runtime-owned direct Retry passively polls fresh stable
  two-identical-read exact-target saves until the structural tail advances.
  Unchanged tails schedule another poll without UI input; one append or a
  capacity-30 rollover closes the new scope. Acquisition, shape, or invalid
  transition evidence restores the guarded UI route only while its source,
  target, scope, and action authority remain proven. Source-specific UI and
  save fingerprints are never compared. Fallback records a new source-tagged
  baseline conservatively, and legacy schema-1 activity metadata is recognized
  only as the historical UI source. Unknown `killedBy` preserves structural
  continuity while semantic completed-record publication remains unavailable.
- Completion of a session configuration gate writes a receipt into that same
  run scope. The receipt identifies the strategy and fingerprints its exact
  session assertions, requirements, fallbacks, and generated gate rules. Only
  a run-ID-stable continuity result proving unchanged Battle History may reuse
  an exact matching receipt on process attachment. Reuse structurally
  suppresses the attached session rules without fabricating their in-memory
  completion variables; a missing or mismatched receipt, later battle,
  unreadable identity, or failed scope compare runs the declared attachment
  checks normally.
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
  explicitly declared `run_when_attached` check. A request encountered at Home
  `NEW_BATTLE` follows normal boundary replacement instead and runs the
  complete startup-gate sequence.
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

Production/development coordination, shared screenshots, and the planned
interactive lease are governed by the canonical
[development coordination architecture](development_isolation.md). Production
remains the sole long-lived runtime and normal input owner. Cooperative workers
may read or copy production artifacts and, after the live startup inspection,
run bounded exact-target ADB reads or captures without an interactive lease.
Connection management and continuous capture remain production-owned.

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

This is a planned package, not authority to add a second competing
`screenrecord` process or bypass current screenshot guards. Requirements and
benchmarks live in
[`../backlog/state-and-detection.md`](../backlog/state-and-detection.md#capture-and-action-architecture).
