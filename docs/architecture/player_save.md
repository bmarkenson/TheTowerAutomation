# Player-Save Architecture and Semantic Evidence

`PlayerSaveParser` in `core/player_save.py` is the global in-process API for
decoding The Tower's `playerInfo.dat` once into a recursively read-only,
normalized snapshot. It is an independent observation channel, not a
replacement for action verification and not a raw-field query API. Semantic
capabilities are resolved from declared contracts and bindings. An unknown
forward revision may reuse a contract only when its provider explicitly
declares additive dependencies; additional root or nested fields are ignored
and unpublished. Legacy configuration, profile, and completed-report
projections remain exact or explicitly revision-compatible until they are
migrated to their own semantic contracts.

## Current status

`ActiveRoundIdentity`—`(versionNumber, currentTier,
roundsStartedThisTier[currentTier], roundSeed)`—is the only durable same-battle
key. Runtime obtains it by forcing serialization at every lifecycle boundary
that needs current identity; it never waits for a natural save or substitutes
Battle History, elapsed time, visual similarity, or `activity_scope_run_id`.
That scope remains optional log/report/presentation metadata. The sole runtime
path allowed to request a passive save is an explicit stable Perk selection or
exhaustion checkpoint; audit and metrics may only consume the resulting shared
bundle.

The first mapping is `data-9-game-1073`, selected by the exact save fields
`dataVersion: 9` and `versionNumber: 1073`. Its overall maturity is `candidate`,
with an explicit per-check validation allowlist. It was derived from the
repository-root operator sample and recognizes the sample's five 28-slot
card-preset records, including the distinction between its stored base slot
count and the effective preset width. The same exact mapping now includes the
validated Legend Tournament condition generator.

Game `28.3.2` introduced exact save identity `dataVersion: 9` and
`versionNumber: 1101`. The separate `data-9-game-1101` mapping is an
structural candidate with a declared revision-compatibility authority of
`data-9-game-1073`. A stable two-read inspection found the same
`SaveLoad+PlayerData` root, all 739 prior fields, unchanged required array
lengths, and exactly two additional integer fields:
`enemiesKilledThisWave` and `enemiesSpawnedThisWave`. Both counters remain in
the `unknown` raw-field disposition and are never published. The compatibility
gate therefore reuses the 17 previously validated configuration checks and the
runtime normalizer; every check and runtime component still validates its own
type, length, ID domain, and internal structure independently. The
version-derived Tournament-condition generator is deliberately excluded and
continues through UI because its algorithm remains exact to version 1073. The
12 exact-1101 profile-progression components passed their structural contracts
and remain observation-only.

The version-1101 mapping is the authority origin and raw binding provider for
`thetower.player_save.active_run_tallies.v1`: 29 cumulative active-round
claims covering 14 coin sources, eight economy/time values, and seven progress
values. Each direct leaf has its own semantic ID, semantics fingerprint, raw
binding, status, and reason; derived rates declare their dependencies. Unknown
forward revisions inherit only this capability while its
`additive_dependencies` policy remains in force, even if their observed data
lineage advances. Unknown additions remain unpublished. Exact 1073 does not
inherit the capability because it predates its provider. A later exact mapping
may preserve, rebind, replace, or revoke an individual claim without changing
unrelated claims. When a data-lineage-forward resolution cannot inherit legacy
History, the capability separately projects only its declared source-ordered
battle-date/Tier/wave/kind tail identity. The active-run monitor binds that
identity before and after the natural boundary; it grants no general History,
continuity, navigation, or completed-report authority.

A bound monitor derives whole-run and leaf-local checkpoint-interval CPH,
cells/hour, cash/hour, waves/hour, effective speed, and rates for each coin
source. These realized save rates remain distinct from OCR
`coin_rate_samples`; a displayed Coins/min value is never multiplied by 60 and
relabeled as realized CPH.

Each capability result records the observed save identity, provider mapping,
authority/audit origin, resolution mode, and two distinct fingerprints. The
semantic fingerprint covers meaning such as unit, cumulative-round temporal
scope, monotonicity, and derived dependencies; it excludes the version number
and raw path. The binding fingerprint covers run, source, and terminal field
bindings; provider mapping and observed identity remain adjacent provenance.
Rebinding an unchanged semantic claim therefore changes only the
binding fingerprint, while changing a unit or formula requires a new semantic
fingerprint. Compatibility is declared and auditable—it is never inferred from
a coincidentally unchanged field name.

`saveRevision` is a diagnostic per-write counter and may advance many times
within one game version; it does not select or invalidate a capability.
`dataVersion` and `versionNumber` remain observed provenance and select legacy
version bindings. They do not by themselves prove or disprove semantic
equivalence for a declared capability.

Live cross-channel calibration on game `28.3.1` confirmed that
`versionNumber: 1073` is the installed application's `versionCode`. At one
verified new-battle Home boundary, stable save reads agreed with authoritative
UI evidence for the selected Cards, Workshop, and Bots presets; First Perk;
the six selected Ban Perks; all 18 ranked Auto Pick rows; equipped Guardians;
and the three Free Upgrade locks managed by automation. That pass corrected
the candidate perk IDs after Auto Pick rank 9 and identified perk ID `21` as
Swamp Radius. The Home list reader now identifies selected Ban rows by their
tile outlines and terminates Auto Pick ranks at the visible Rankings Unlocked
divider, instead of interpreting perk-category fill colors as selection state.

The first deployed `save_first` boundary then exposed four conservative but
unnecessary fallbacks. Auto Pick still expected a nonexistent sentinel and its
33-ID table omitted ID `11`; Target Priority used an incorrect ID permutation;
Free Upgrade reconciliation treated the unmanaged `Health` lock as an exact-set
mismatch; and Modules had only structural, not value-scoped, authority. The
same boundary also opened Battle History before the already-required Home save
could supply its newest-tail identity. Coordinator-retained same-run UI
comparisons proved the corrected contracts below. This corrective work reuses
that accepted evidence and does not repeat a calibration campaign.

On 2026-08-05 the operator authorized a narrowly bounded read-only follow-up
against the already-running Tournament solely to extend Module decoding. Two
stable exact-target save reads before UI inspection and another stable pair
after restoration exposed the same slot values. The paired Modules overview
identified all eight names, and the equipped core Assist detail explicitly
identified Harmony Conductor; the operator withdrew the initial Magnetic Hook
identification. No Module was equipped, unequipped, transferred, or changed,
the game was not backgrounded to force a save, and the existing Tournament was
returned to its running screen. No raw save, decoded object, screenshot, GUID,
effect, level, substat, inventory record, or private value was retained.

The resulting exact slot mappings are cannon Primary Amplifying Strike (`45`),
armor Primary Anti-Cube Portal (`20`), generator Primary Project Funding
(`43`), and core Primary Dimension Core (`38`); cannon Assist Being Annihilator
(`9`), armor Assist Space Displacer (`19`), generator Assist Singularity
Harness (`30`), and core Assist Harmony Conductor (`39`). Combined with the
prior Farm evidence, every value in `tournament_standard` is now decodable.
The top-level `module_info_indices` table owns each observed global
`infoIndex`/name/family identity. The separate `module_loadout` structure owns
the four exact Primary indices, four typed Assist slots, family/role labels,
and retained calibrated placement examples. Its explicit
`assignment_authority_scope=canonical_global_same_family` contract permits any
canonical identity in either exact slot of its mapped family. The versioned
`empty_assignment_scope=explicit_nil` and `assist_item_field=equippedModule`
contract also maps a Primary array `null` or an unlocked typed Assist slot
whose exact `equippedModule` field is `null` to the canonical assignment
`empty`. A matching save
may omit duplicate Modules observation; it never authorizes a loadout change.
Neither table is a Module inventory map. The currently observed armor
variation is evidence only; it does not replace the Tournament reference.

On 2026-08-14 an operator-authorized no-battle campaign completed the global
identity catalog on the compatible version-1101 structure. Three exact
four-Primary UI loadouts were paired with fresh stable saves, then the original
eight-slot loadout was restored, reserialized, and returned to verified Home
`NEW_BATTLE`. Existing Farm/Tournament identities were reused; the campaign
calibrated only the twelve gaps. A first Havoc Bringer search failed because an
inertial inventory scroll settled between fixed row centers, recovered the
baseline without accepting evidence, and led to a variable-row guarded search
regression. The successful pass required exact Ancestral detail OCR before
equipping Havoc Bringer. The collector was neither enabled nor consulted.

The current global catalog contains all 24 Modules, six in each family:

| Family | Exact `infoIndex` identities |
| --- | --- |
| cannon | `7` Havoc Bringer; `8` Death Penalty; `9` Being Annihilator; `10` Astral Deliverance; `41` Shrink Ray; `45` Amplifying Strike |
| armor | `17` Wormhole Redirector; `18` Negative Mass Projector; `19` Space Displacer; `20` Anti-Cube Portal; `42` Sharp Fortitude; `46` Orbital Augment |
| generator | `27` Black Hole Digestor; `28` Pulsar Harvester; `29` Galaxy Compressor; `30` Singularity Harness; `43` Project Funding; `47` Restorative Bonus |
| core | `37` Multiverse Nexus; `38` Dimension Core; `39` Harmony Conductor; `40` Om Chip; `44` Magnetic Hook; `48` Primordial Collapse |

The version-1073 authority and version-1101 exact structural mirror carry the
same catalog. All 24 identities are authoritative for exact name-only
reconciliation in either Primary or Assist slot of the same family. Complete
matching loadouts may therefore omit Modules UI without collecting every
possible role permutation. A complete loadout accounts for all eight slots as
either one canonical same-family identity or explicit `empty`; multiple empty
slots are valid while an installed identity remains unique. Unknown identities,
cross-family placements, duplicate installed names, malformed or partial
structures, and enforced mismatches retain the complete existing Modules UI
path.

Bounded mutation testing then established direct causality and the save-write
boundary. Changing the visible Cards preset from slot 2 to slot 1 and back
produced raw `currentPreset` values `1 -> 0 -> 1`; the raw field is zero-based
even though the UI labels slots from one. Changing Poison Swamp Stun from on to
off and back produced `poisonSwampStunOff` values `false -> true -> false`.
Changing Demon Mode from auto-reactivate to ready-after-recharge and back
produced `demonModeAutomateToggle` values `true -> false -> true`; changing
Nuke in the opposite direction and back produced `nukeAutomateToggle` values
`false -> true -> false`. The other card's flag, Missile Barrage, and the Cards
preset remained stable during each isolated mutation. Waiting or returning
Home alone did not flush the tested preset change. These settings serialized
when Android Home backgrounded the game, without force-stop, and each
restoration was verified through both the UI and a second app-pause flush.

Tournament calibration established a different persistence boundary. During
an active Tournament the game stores the Tournament number as its condition
seed, but after the run it clears `tourneyConditionsSeed` and
`tournamentNumber` to zero. The post-run save still binds
`tournamentCheckedNumber` to a same-number `tournamentRecords` entry with its
UTC event date and Legend league. The installed version-1073 game code seeds
the compatible `System.Random` algorithm with that Tournament number, chooses
one of Energy Shields Down or Death Defy Down, chooses five distinct entries
from the Legend pool, and adds the fixed Legend conditions. Tournaments
271–287 reproduced all 16 operator-supplied historical rows plus the current
Heat panel with no mismatch. Unsupported versions, unvalidated leagues,
conflicting identities, and stale registry dates publish no conditions and
require the retained UI path.

This remains a per-check promotion, not a global mapping promotion. The exact
mapping now allowlists every configuration claim covered by the accepted
version-1073 calibration: Cards, Workshop, and Bots preset selection; independent
First Perk Choice; Ban Perks; the ranked Auto Pick prefix; the exact current
three-lock Farm required subset; equipped Guardians; exact enforced Farm
Modules, complete mapped observation-only Tournament Modules, and any other
canonical same-family Module name or explicit-empty assignment; Demon Mode/Nuke
recharge behavior;
Auto Pick enabled for the required value `true`; complete Target Priority ID
and ordering semantics; Poison Swamp Stun in both calibrated polarities; all
nine Ultimate Weapon primaries for the all-on requirement; and Spotlight
Missiles for the on requirement. This adoption reused the prior calibration
evidence atomically; it did not require or perform another live campaign. A
later, separately authorized 2026-08-14 campaign mapped Damage Slider, Orb
Distance, and the remaining global Module identities. Those later observations
are recorded independently below and do not retroactively turn the original
coordinator evidence into live validation.

The monolithic Ultimate Weapon check is deliberately not allowlisted. Poison
Swamp Stun, the all-primary-on aggregate, and Spotlight-Missiles-on are separate
normalized components with independent fallback. A mixed/off primary request
or Spotlight Missiles off still opens the existing UI route. Free Upgrade lock
authority is a required subset: Shockwave Size, Bounce Shot Targets, and Bounce
Shot Range must all be set, while additional normalized set bits such as
`Health` are reported as unmanaged evidence and left unchanged. A missing
requested bit, changed shape, unknown requested index, or non-boolean value
restores the complete lock UI path. Omission never authorizes an unlock.

Candidate status remains fail-closed per check. A matching check can become
save-authoritative only when it is explicitly validated, its evidence is
complete, and the caller certifies a known save-serialization boundary for the
snapshot. Every other result still names the existing UI checker as its
fallback. Capture time and two identical reads prove transport stability, not
that recent in-app changes were flushed.

The currently mapped profile checks are:

- active Cards, Workshop, and Bots presets;
- Free Upgrade locks;
- Guardian chips and Target Priority order;
- the enforced Farm and observation-only Tournament Primary/Assist Module
  values by exact typed slot;
- Auto Pick Perks, bans, first choice, and the mapped Auto Pick priority
  prefix (the visible ranked block is distinct from the save's unranked tail);
- Ultimate Weapon primary toggles, Poison Swamp Stun, and Spotlight Missiles;
- exact calibrated Damage Slider values, calculated effective Attack Range,
  and Range-bound Orb Distance tuples;
- current Legend Tournament identity and version-derived Battle Conditions.

Card recharge modes are mapped and validated. Damage Slider has exact authority
for raw `6=1E-22%`, `9=1E-19%`, `10=1E-18%`, and `30=1E2%`. Effective Attack
Range is a reusable versioned calculation for game versions 1073 and 1101.
`rangeLevelSelected` is the selected Range lab level, never displayed Range.
The calculator selects `upgradeLevel[4]` as the active total level or
`upgradeWorkshopLevel[4]` outside a battle, applies the selected lab and live
Range-card state at index 4, adds exact Cannon Primary/Assist Range effects,
runs the native binary32 compression pipeline, and formats the final meter
value with the game's fixed-two-decimal rule. The completed lab level, field
domains, module effect IDs and activation gates, formula constants, supported
versions, and raw bindings are all part of the mapping fingerprint.

Orb Distance has Range-bound authority for Farm
`30.00m/30.00m/39.00m`, Farm `30.00m/31.80m/37.20m`, and Tournament
`98.38m/87.16m/80.37m`. Its `innerOrbDistance` and
`workshopOrbDistance` mapping centers use one decimal place and each accept an
absolute raw variance of `0.1`. A tuple can suppress live UI only when its
semantic Range equals one complete `current_active_round` calculation. Active
Range must already be level 79 so no purchase or free upgrade can stale the
snapshot before consumption. Preset names do not participate. The decoder
requires one unique semantic match, so incomplete or out-of-round Range,
overlapping tuple windows, unsupported versions, and malformed dependencies
retain UI.
The [two version-1101 Tournament pairings](../issues/evidence/tournament-orb-distance-save-alias-2026-08-15.md)
both fall within the canonical Workshop center `8.0 ± 0.1`. This bounded raw
noise policy does not infer a distance formula, interpolate a new semantic
tuple, or relax context matching. The current Farm and `tournament_standard`
eight-slot Module assignments are fully supported.
Any other canonical Module is also supported in either exact same-family role,
and an explicit nil assignment is supported as canonical `empty`. An unknown
requested name or slot `infoIndex`, a family mismatch, duplicate installed
assignment, ambiguous absence, or nonexact/partial loadout retains the complete
Modules UI route.
New identities can be added only with cross-channel calibration, not merely
because a plausible raw field exists.

### Implemented completed-run profile progression snapshots

Completed battle records now carry a separate `profile_progression` projection
for run-to-run analysis. At `GAME_OVER` or `TOURNAMENT_RESULTS`, the runtime
uses the exact target and ownership generation already held by
`AdbTargetSession`, accepts only two byte-identical reads, decodes entirely in
memory, and discards the result if target ownership changes. It does not
background the game, navigate, or send input. Capture or mapping failure is
nonblocking and produces an explicit `unavailable` snapshot while the existing
UI stats path continues.

The exact-version manifest records 12 structural components: account pack/ad
unlocks; Bots and their cooldown-lab selections; cards/decks; Enhancements;
Guardian progression; Harmony/Power nodes; equipped Primary and Assist Module
levels, rarity, indexed effects, and Assist efficiencies; relics; Research;
Tower, Background, and Menu Theme ownership; Ultimate Weapons; and the active
Workshop levels. Only allowlisted primitive fields are retained. Module GUIDs,
inventory records, account identifiers, balances, arbitrary raw fields, and
the raw save remain excluded.

These values preserve exact source field and array-index identity. A numeric
Research, Workshop, Bot, Module-effect, relic, or Theme index is not assigned a
name, formula, cap, or gameplay effect unless its separate audit row supports
that semantic claim. The snapshot is observational and cannot suppress UI,
authorize a setting change, classify a battle, or repair a loadout. It is safe
to include on a terminal-only process because it describes global profile
state and makes no process-local Strategy or run-binding claim.

#### Bot preset representation and interpretation

Version 1073 stores Bot configuration in parallel per-Bot preset arrays. The
profile snapshot normalizes their shape but deliberately does not translate
their four-position level vectors into UI stats:

| Save field | Snapshot field | Supported interpretation |
| --- | --- | --- |
| `botPresetName` | `bots.preset_names` | Visible preset labels in source order. |
| `currentBotPreset` | `bots.current_preset` | Zero-based selected preset position. |
| `<bot>BotPresets[i]` | `bots.<bot>_presets[i]` | The same source-indexed preset record; each mapped Bot has four records. |
| `levels[0..3]` | `levels[0..3]` | Four raw Bot-specific upgrade positions. They are not a universal tuple and must not be assumed to follow UI tile order. |
| `selectedLevels[0..3]` | `selected_levels[0..3]` | Four raw Bot-specific selected positions. They are not effective durations, cooldowns, bonuses, reductions, or ranges. |
| `unlocked`, `active` | same names | Structural per-Bot/per-preset booleans. |
| `plusUnlocked`, `plusLevel` | `plus_unlocked`, `plus_level` | Structural Bot+ ownership and level fields, separate from the four-position vectors. |
| `<bot>BotLevelCooldownSelected` | `bots.<bot>_cooldown_lab_level_selected` | A separate raw cooldown-lab selection; it does not by itself publish an effective cooldown. |

The vector positions can differ between Bots and can differ from the visible
tile sequence. One version-1073 cross-channel observation made the failure
mode explicit: Flame Bot stored `levels=[0, 15, 15, 25]` while the UI showed
Damage Reduction `95%` (Max), Cooldown `5s` (Max), Damage `x50.00` with no
Damage upgrades, and Range `117.00m` (Max). Reading that vector as the visible
order `[Damage Reduction, Cooldown, Damage, Range]` therefore reverses the
base Damage and max Damage Reduction conclusion. Because both middle positions
were `15`, that observation does not distinguish their individual meanings or
publish any level-to-value formula.

Treat a tuple mapping inferred for any one Bot as Bot-specific evidence, not a
reusable ordering rule. Before naming any raw position or calculating an
effective value, require authoritative UI evidence from the same version and
boundary, and isolate the position when possible. Otherwise report the raw
vector and visible UI values separately, with the position semantics explicitly
unresolved.

Normal battle records use schema 6 and Tournament records use schema 4. The
JSON stores the complete normalized snapshot and component fingerprints; the
Markdown view summarizes Theme/relic ownership and structural completeness.
When a normal battle is persisted, its `profile_progression_delta` compares
against the newest earlier battle that also has a snapshot. Deltas preserve
exact paths such as `themes.menu_unlocked[11]` or
`bots.bot_bot_presets[0].plus_level`, include before/after values, and identify
the baseline battle. The first captured run records an explicit missing
baseline rather than treating every owned item as newly acquired.

### Implemented save-first configuration preflight

At a freshly verified Home `NEW_BATTLE` boundary, the ordinary runtime policy
defaults to `save_first`. One guarded coordinator records the exact owned ADB
target and generation, runtime/preflight/activity identities, selected strategy,
and resolved configuration fingerprint; backgrounds the game to Android Home;
accepts only the existing two-byte-identical-read acquisition; decodes in
memory; restores the game; and revalidates the same ownership plus two stable
Home `NEW_BATTLE` observations. It retains only normalized decisions, exact
version and mapping identity, redacted source/target/session provenance, and
safe reason codes.

One snapshot reconciles every requested check and freezes the complete plan
before setup input. A complete allowlisted match can omit only that redundant
UI observation. Modules under an `observe` policy may also omit duplicate UI
when all eight actual assignments and all requested reference names are
supported; a difference is carried as `save_observation`, not a match or repair
authorization. A complete allowlisted exact or compatible mismatch in an
enforced check is `save_mismatch`: it queues only that check's existing guarded
UI path while the plan remains read-only. If that path actually mutates UI, its
first input closes the pre-action mapping-candidate window and removes only the
affected check from carry; unrelated accepted decisions remain authoritative.
If it only verifies a match, unrelated decisions likewise remain authoritative.
Unsupported requirements, unknown IDs, incomplete per-check structure, and
forced audit are ordinary `ui_required` dispositions rather than trusted
mismatches.

Operator diagnostics emit one privacy-safe record per requested check with the
mapping ID, evidence completeness, requirement-support status, disposition,
and normalized reason. Accepted scalar/mapping/list evidence is rendered from
its normalized save projection, so Card Recharge Modes and Perk Bans no longer
appear as `observed=unavailable` after acceptance. Logs never include raw
discriminators, save values, decoded objects, account/private fields, Module
records, or GUIDs.

Resolution always tries an exact mapping first. A same-lineage mapping may
separately opt into legacy version-wide compatibility, while a semantic
provider may declare an individual capability stable under additive dependency
changes. The root manifest is diagnostic: additional fields stay unpublished,
and a missing, malformed, or extended dependency affects only the leaf or
projection that consumes it. A capability-only resolution never inherits
legacy configuration, profile, Perk, or structural History authority. A root
class/envelope failure remains global. Runtime never writes a mapping, infers
semantic compatibility from coincidentally unchanged names, or promotes
authority from its own observation.

Mapping, pull, decode, or projection incompatibility is recoverable for a
configuration/report consumer once the original source is safely restored:
that consumer may use its guarded UI implementation. Battle identity has no UI
fallback; an active/resumable source remains input-blocked until a forced save
provides a valid `ActiveRoundIdentity`. Only target/ownership/context change,
interrupted control, or failed source restoration is catastrophic. `force_ui`
performs no configuration-save lifecycle;
`comparison_audit` collects normalized comparison evidence but keeps UI
authoritative.

Version 1073 defines exactly 34 Auto Pick IDs: 18 ranked entries followed by
16 unranked inventory entries, with every mapped ID present exactly once and no
sentinel. ID `11` is `unlock_random_ultimate_weapon`. Only the ranked prefix is
published and compared. Unknown IDs, duplicates, changed length, or changed
membership fail closed before the dynamic unknown-ID resolver is considered.
The complete Target Priority map is `0=Closest (Default)`, `1=Basic`, `2=Fast`,
`3=Tank`, `4=Ranged`, `5=Boss`, `6=In Spotlight`, `7=Protector`, `8=Elites`,
and `9=Fleets`; the accepted saved permutation `[2, 7, 9, 5, 8, 6, 3, 0, 4,
1]` therefore matches the complete Farm order exactly.

A trusted mismatch selects work but does not authorize input. The existing UI
path must observe the current value, prove that it differs, perform its guarded
repair, and verify the resulting value. That check is reported as
`ui_verified_repair`; the pre-action save is never proof of the repair and the
repaired value is not inserted into save carry. If UI already matches a trusted
saved mismatch before this coordinator repaired it, or an inspected UI section
disagrees with a `save_match`, the save/UI contradiction invalidates the whole
snapshot and fails closed. The default-disabled V1073-RUNTIME-013 auditor is a
separate campaign-only observation stream and supplies no Home preflight
authority.

Accepted session-only values are single-use evidence for two exact transition
types: a runtime-owned Home `NEW_BATTLE` launch, and a same-process natural
Game Over -> direct Retry successor. They cover Cards, Workshop, Bot and
Guardian selections, Free Upgrade locks, Modules, Auto Pick enabled `true`,
exact complete Target Priority order, all nine primaries on, Spotlight Missiles
on, Poison Swamp Stun, and the other supported Home checks. A Home carrier is
armed only by verified authorized dispatch. Every direct owner of that
dispatch, including the exclusive Tournament-validation launcher, must advance
the same carrier before the first `RUNNING` observation; the observed battle
cannot retroactively supply the missing launch proof. A conclusive miss retires
a no-retry owner's carrier, while an uncertain dispatch or missing durable
owner suspends it and keeps UI fallback authoritative. No dispatch remains
pending, and an unstable first `RUNNING` frame defers instead of rejecting the
evidence. `WAIT` is only the next-terminal policy and has no effect on binding.

The direct-Retry carrier reuses the already acquired natural terminal bundle
without another read. It requires the typed Game Over boundary to name the
same runtime, predecessor active-round identity, target, and target generation;
the runtime projection, when present, must be inactive. After the verified
Retry tap, the successor's first stable `RUNNING` observation forces a new
serialization and binds the new active-round identity before carry is applied.

Pause blocks input and suspends unconsumed carry pending fresh save or UI
evidence; it does not quarantine the snapshot. Restart/Stop, attachment,
strategy/configuration/target change, a competing workflow, manual or ambiguous
launch, wrong transition, or an unrelated later battle discards the carrier.
A requirement change, unsupported mapping, or incomplete component routes only
the affected check to UI before input. An actual configuration mutation at
Home, Target Priority, Poison Swamp Stun, Damage Slider, or Orb Distance closes
the pre-action mapping-candidate window and removes only that setting's save
authority before the tap. Level-skip input closes the same correlation window
without discarding independent configuration decisions. Home setup continues
with unrelated accepted decisions instead of reopening their UI sections.
Independently UI-verified sections retain explicit UI provenance; they are never
relabeled as save-backed. A read-only UI match preserves carry, while a save/UI
contradiction fails closed globally. The repaired check stays supported only by
its UI evidence unless a genuinely new authoritative snapshot is acquired.
Save evidence never authorizes a tap, repair, launch, lifecycle transition,
attachment, terminal binding, dispatch, or strategy action.

The same authoritative Home snapshot supplies an inactive-round proof,
configuration evidence, and a source-tagged structural History baseline. The
History projection remains usable when `killedBy` lacks a semantic mapping,
but it is report metadata only. At direct Retry, configuration reconciliation
reuses the natural terminal bundle with zero additional acquisition, and the
successor identity is forced once at first stable `RUNNING`. Runtime never
polls for a History-tail change or opens Battle History to decide whether the
battle is the same.

At an attachment already showing `RUNNING`, the runtime always uses a fresh
guarded save, including after process replacement or with no prior record.
The shared guarded Android-Home serializer requires the exact runtime session,
workflow operation, lifecycle-owned active-battle state, and target/generation to
survive two stable pre-background `RUNNING` frames, `KEYCODE_HOME`, two
byte-identical save reads, launcher restoration, and two stable post-restore
`RUNNING` frames. Launcher-command acceptance is dispatch evidence, not proof
that the source has rendered. After its initial half-second settle, the shared
serializer therefore retries stable-source verification while a 12-second
convergence budget remains, capped at six attempts. Exact target binding,
caller context, and action authority are rechecked before and after every
attempt; any loss blocks immediately with a distinct diagnostic reason, while
an unchanged but still-transitioning source blocks only after convergence
times out. The fresh resolved snapshot must contain an active-round identity.
With no prior durable identity it is adopted as the first observation; an
equal fingerprint proves the same battle; a different fingerprint proves a
later battle and invalidates old battle-local state. The store never compares
History tails, dates, elapsed time, UI-derived fingerprints, or activity scope
for this decision. If the identity projection is unusable after successful
restoration, bounded forced serialization may be retried, but no History/UI
route substitutes for it. Process, target, control, source, active-identity, or
restoration ambiguity blocks all later battle-bound input.

The same accepted active-attachment snapshot projects complete normalized
checks from the resolved mapping's validation allowlist. No Strategy consumes
those values as source-tagged observations, not as repair or action authority,
and visits only fields that remain unresolved. A validated Attack Dissonance
sword resolves its inaccessible Damage Slider without probing the disabled
menu; a Utility star does not. Save-resolved Workshop, Free Upgrade-lock,
Cards, and Perk configuration also remove their post-run Home detail traversal;
finalization still requires verified Home. No raw save, private field,
incomplete check, or unvalidated candidate value enters the observation. Home
`RESUME_BATTLE`, `force_ui`, and `comparison_audit` retain their declared
configuration-UI behavior after identity is established. This path neither uses the unmapped Force Cloud
Save control nor grants authority to passive polling, terminal record
construction, Strategy, trackers, or any battle-lifecycle input.

The exact-version raw root contains `dissonanceActive` and
`dissonanceSelected`, and completed History entries contain `dissonanceType`.
Their selector/type enum is not in the validation allowlist, so the save may
corroborate a UI calibration but does not currently publish run subtype or
replace the badge. A completed production confirmation on 2026-08-07 provides
one cross-channel calibration for Utility Dissonance: the white-star UI
observation coincided with active `dissonanceSelected=3`, and the same battle's
completed History entry contained `dissonanceType=3`. Value `3` and every
other selector/type value remain outside runtime authority until a separate
mapping implementation and review promotes them; no semantics are inferred
for the other values.

Snapshot schema 7 contains observed identity, manifest diagnostics, legacy
mapping provenance, and independently resolved semantic-capability evidence;
its runtime projection is schema 3. Parser-wide failure is limited to transport,
container/decompression, NRBF, non-object root, or an invalid checked-in
registry. Missing or malformed mapped leaves produce local statuses instead of
discarding the document. `saveRevision`, `roundActiveBool`, and `currentWave`
are separate claims: a failure affects only consumers that depend on that
claim. For the exact version-1073 mapping the snapshot also publishes the
active identity tuple and independent normalized Perk and Battle History tail
components; its active-tally capability remains unavailable. Perk ID `0` is
`max_health` (Max Health). Perks are emitted
only when every ordered pick, count, and level agrees. The 50-entry level
array defines numeric storage capacity; it does not prove that all 50 indices
are live Perk identities. Version 1073 currently has 34 cross-channel-mapped
semantic IDs, including all six configured Farm bans. The other 16 positions
remain unclaimed rather than being assigned invented names.

A read-only mid-round inspection also established the structural scope for the
next runtime components. The decoded root contains separate 20-entry current
and Workshop-level arrays for Attack, Defense, and Utility; round-scoped
survival-ability counts and recharge/active fields; and broad `ThisRound`
tallies. It also contains candidate values for current game speed, Damage
Slider, Orb Distance, buy quantities, Card activity, and subsystem cooldowns.
Except for the 29-claim active-tally capability originating at 1101, these
observations broaden the validation plan but do not publish those raw fields or
promote their semantics.
In particular, the save has no literal
gold-box flag and one snapshot is not a complete survival-activation history.

The source-ordered `battleHistory` list may contain at most 30 entries. Only
the newest entry is part of the tail contract. Added nested fields are ignored;
required structural identity leaves and each allowlisted More Stats row are
validated independently. Battle date kind/ticks, tier, wave, and Tournament
identity establish the causal tail. Game/real time and numeric `killedBy` are
optional semantic leaves, so a malformed cause or time does not erase the tail
or unrelated terminal tally claims. The full completed projection still
requires every current row and a mapped cause before it may suppress the UI.
UTC and local DateTime ticks are never ordered against each other; the game's
established source list order owns which entry is newest.

The active-round/terminal projection foundation does not itself bind a process
or grant navigation authority. The terminal consumer now reuses one stable
exact-target read and builds a normal or Tournament report only after a bound
canonical active-round identity, compatible save-sourced pre-terminal baseline,
exact append or capacity rollover, inactive save, semantic entry, and terminal-
kind proof. A
failure publishes an explicit UI fallback without exposing a partial completed
entry. The configuration coordinator remains a separate exact-Home consumer.
The normal active-run metric monitor is another guarded consumer of the shared
typed passive and natural-terminal bundles; it does not acquire a duplicate
save or use the campaign auditor's cache.
Only during an explicitly enabled campaign, the V1073-RUNTIME-013 auditor may
project the same shared bundle into its session-local audit state. A
malformed leaf affects only the structural or semantic facts that depend on it.
The authoritative ownership and slice boundaries are in
[`runtime.md`](runtime.md#save-first-active-round-and-terminal-evidence).

### 2026-08-02 Tier 22 natural-boundary audit

The authorized Tier 22 audit completed the core natural-boundary observation
pass for `V1073-RUNTIME-003` through `V1073-RUNTIME-011`. A known Home
boundary preceded a new round identity, per-tier counter, and round seed.
Multiple stable revisions then advanced under that identity through the last
active snapshot at wave 710. That snapshot's 15 ordered Perk picks were
internally consistent, and the terminal UI's 11 collapsed rows represented
exactly the same picks, levels, and order. The immediate stable post-death save
was inactive and cleared the active-round, Perk, and survival fields.

The pre-battle structural-tail baseline changed to a capacity-30 newest entry
for Tier 22, wave 751, killed by ID `3` (`Boss`). Its save-derived projection
agreed with all 144 terminal More Stats rows. The passive compact Game Stats
capture supplied the optional `28.56T` base / `14.28T` ad split and its
`42.84T` total. The corrected terminal record is
`logs/battles/Battle20260802T141027-0700.json`.

This proves the natural-boundary candidate relationship used by the later
guarded terminal consumer; the observation itself did not implement polling,
same-round Perk retention, history attachment, record construction, or UI
suppression. In particular, a terminal-only replay remains `unbound` under
the [current-process terminal-binding rule](../issues/resolved-2026.md#terminal-only-restart-attached-stale-strategy-and-perk-history-to-a-manual-battle),
and it cannot supply Strategy or process-local tracker evidence. Unknown future
`killedBy` values still fail semantic publication closed. Foreground save
progress does not claim pause/background cadence, and the observed survival
checkpoints and visual transitions do not prove repeated-event completeness or
exact timer formulas; those remain `V1073-RUNTIME-015` and
`V1073-RUNTIME-016` extensions. No retained intermediate raw save, exact
timestamp, source hash, timer semantics, or special replacement battle is
required for the completed core audit.

### 2026-08-12 version-1101 active-tally audit

Two passive stable reads of an already-running Tier 19 battle were reduced in
memory to selected allowlisted values at saved waves 5,480/revision 49,556 and
5,560/revision 49,557. No raw save or decoded root was retained. The natural
Game Over path then captured revision 49,558 and causally attached the exact
version-1101 Battle History entry at wave 5,584. This boundary was not created,
accelerated, delayed, or surrendered for the audit.

The second active checkpoint advanced to the following terminal values:

| Component | Active wave 5,560 | Terminal wave 5,584 |
| --- | ---: | ---: |
| Real / game seconds | 15,988.3486 / 79,689.4688 | 16,073.4854 / 80,114.8672 |
| Coins / cells / cash | 7.24871Q / 2,347,729 / 2.988823T | 7.28465Q / 2,362,087 / 2.988834T |
| Highest coins/minute save tally | 43.5354q | 43.5354q |
| Attack / health level skips | 2,915 / 3,194 | 2,928 / 3,208 |
| Free Attack / Defense / Utility upgrades | 598 / 500 / 11 | 598 / 500 / 11 |
| Enemies destroyed / waves skipped | 709,856 / 2,911 | 713,416 / 2,920 |

All 14 allowlisted coin-source counters were nondecreasing into their mapped
terminal rows. Rounded active-to-terminal pairs were: Golden Tower Plus
4.2553Q→4.2729Q, Wave Skip 2.5338Q→2.5493Q, total coin bonuses
2.9872Q→3.0055Q, Coin/Kill 450.48q→453.19q, Golden Tower
440.55q→443.21q, Black Hole 412.12q→414.60q, Golden Bot
401.97q→404.41q, Spotlight 297.04q→298.83q, Death Wave
243.69q→245.14q, Orbs 106.56q→107.25q, Critical Coin
13.000q→13.058q, Coins/Wave 140.88B→141.73B, Guardian Fetch
6.173q→6.256q, and Guardian Stolen 0→0. At the active checkpoint,
`coinsEarnedThisRoundWithoutFetch + totalCoinsFetchedByGuardianThisRound`
equaled `coinsEarnedThisRound`; the terminal compact Game Stats ad total also
advanced beyond the active ad-bonus counter.

The cumulative checkpoint CPH was 1.6321Q/hour and the terminal CPH was
1.6316Q/hour. The two active reads independently yielded a 1.447Q/hour
interval; the contemporaneous UI displayed 24.0–24.6q Coins/min. That separate
OCR scale corroborates the interval without becoming the realized-rate
calculation. This evidence promotes only the 29 named fields in
`V1101-RUNTIME-017`. Damage, survival, `*ThisWave`, reroll, gem, Guardian
resource, shard, Module, and other unvalidated counters remain unavailable.

### 2026-08-13 ordinary-runtime boundary confirmation

Retained normal-runtime records close the ordinary Game Over handoff and
save-backed Perk rollout checks without extending the Tier 22 semantic claims.
At 04:32 PDT, Game Over → Home accepted the terminal History handoff with
`save_reads=0` and `history_navigation=0`. At 14:26 PDT, Game Over → direct
Retry did the same, and the terminal bundle staged Cards, Workshop, Bots,
Guardians, Modules, Free Upgrade locks, Auto Pick configuration, Target
Priority, and Ultimate Weapons with no UI fallback. In each case the terminal
consumer retained its current-process binding; the activity-scope handoff was
report metadata and does not authorize a terminal-only replacement process.

The ordinary Tier 19 record `Battle20260813T142643-0700` contains a valid
56-pick exact saved inventory spanning 27 semantic keys. Its bound exhaustion
evidence followed 1,079 stable top-bar observations, the action interval from
run handoff through Game Over contains no `navigation.open_perks` input, and
the later active checkpoint plus natural terminal clear allowed Game Over to
omit Perks navigation. The same single terminal acquisition supplied the
causally attached 144-row Stats projection. This confirms the normal passive
timeline and finality route; it does not confirm the independent Tournament
Results → Home handoff or the replacement-attachment mismatch/fallback matrix.

The guarded fallback also exposed a narrower value-domain gap. Exact 144-row
clipboard reports for `Battle20260810T141801-0700`,
`Battle20260810T203606-0700`, and `Battle20260811T005344-0700` contain negative
`Damage Dealt` and `Chain Lightning` values. The save projector now retains any
finite signed report statistic as exact source evidence while keeping Battle
Date, tier, wave, game time, and real time in their positive identity domains.
This preserves the independent History identity and report, but does not claim
whether a negative value represents overflow, a sentinel, or meaningful
negative damage. A type change or non-finite value still fails the semantic
report without erasing the structural tail.

## Complete validation program

The validation target is not every opaque counter in the save. It is every
normalized claim that the importer publishes, every value used to compare a
resolved profile, and every value that could suppress an existing UI check.
For each exact game version, a field-disposition manifest must classify every
raw field name without retaining its value as one of:

- structural identity or shape;
- automation-gating configuration;
- profile observation;
- versioned runtime observation;
- `private` (the retained disposition name for excluded, unpublished data);
- deliberately ignored with a reason; or
- unknown and therefore unpublished.

This makes coverage auditable without copying a real save or coupling reports
to account, currency, history, or other excluded values. The current redacted
`profile_summary` is diagnostic until each semantic count or level group below
is separately validated; array length alone proves structure, not meaning.

The version-1073 manifest inventories all 739 observed decoded-root keys: 13
structural, 40 automation-gating, 48 profile-observation, 34 private, 69
ignored-with-reason, and 535 unknown. The mapping loader validates the checked-in
categories, disjoint membership, declared count, and canonical field-name hash.
At runtime, comparison with that inventory is diagnostic rather than an
all-or-nothing authority gate. Added names are counted as drift but never
published. A missing, malformed, or changed dependency makes only the claims
bound to it unavailable, plus their derived dependents. Array length policy is
claim-local: a complete-set contract may require an exact length while an
indexed or prefix contract can survive an unrelated append.

### Evidence and promotion standard

A versioned claim progresses through four evidence levels:

1. **Structural** — root class, required raw binding, field type, and the
   claim-local array policy are known for an observed provider revision.
2. **Cross-channel** — one stable save and authoritative UI evidence agree at
   the same configuration and serialization boundary.
3. **Causal where needed** — boolean polarity, enum direction, units, and
   write timing are proven by one isolated mutation and restoration. Costly or
   destructive settings instead require independent known configurations; the
   validation plan never spends medals or risks Module levels merely to create
   evidence.
4. **Shortcut-ready** — the entire normalized check is complete, every value
   it could suppress has passed the required evidence, mismatch and malformed
   cases fail to UI in tests, and the check is explicitly allowlisted for the
   semantic contract and its current binding.

Every causal test must record the before, mutated, restored, and unrelated
control fields; flush through a proven lifecycle boundary; visually verify the
restoration; and finish at the original safe boundary. A new or changed
semantic claim starts again at structural status. A later or unknown revision
may reuse only a prior provider's explicitly declared compatible claims. An
`additive_dependencies` policy tolerates unknown additions but never proves
that a changed dependency retained its meaning. Version-derived algorithms,
newly observed fields, and undeclared capabilities do not inherit authority.

### Versioned audit matrix: `data-9-game-1073` / revision 4

This is the single authoritative matrix for every normalized claim published
or proposed for exact game version 1073. The evidence level names the highest
complete level for the **whole row**, not an encouraging partial result:
`Structural`, `Cross-channel`, `Causal`, or `Shortcut-ready`. A row can be
implemented as a bounded normalized observation at Structural or Cross-channel
level
when its stated evidence is complete, but it cannot suppress its UI route
until it is Shortcut-ready. `Shortcut-ready` here describes the
decoder/reconciler; runtime navigation adoption is a separate row. New exact
versions record new bindings and any preserve/rebind/replace/revoke decisions;
inherited claims continue to cite their originating authority.

| Audit ID and normalized claim | Version-1073 source | Evidence level and retained evidence | Required work / current runtime disposition |
| --- | --- | --- | --- |
| `V1073-RAW-001` raw-field disposition manifest | Observed decoded-root keys; no raw values | **Structural and complete for the version-1073 inventory.** Stable exact-target reads established the `SaveLoad+PlayerData` root and 739 decoded keys. The checked-in manifest classifies each known name exactly once and is protected by count, canonical hash, and loader validation. Runtime drift is diagnostic. | Disposition coverage is not semantic promotion: private, ignored, unknown, and newly added values remain unpublished. A missing or malformed dependency fails only its claims and derived dependents; unrelated claims survive. Review a new exact inventory for durable classification without making additions a global parse failure. |
| `V1073-CFG-001` Cards preset | `presetName`, `currentPreset` | **Shortcut-ready.** UI slot 2/1/2 caused raw `1/0/1`; restoration and unrelated controls were verified. | Audit runtime acquisition, then retain scheduled UI samples. |
| `V1073-CFG-002` card recharge modes | `demonModeAutomateToggle`, `nukeAutomateToggle` | **Shortcut-ready.** Both booleans were independently flipped and restored; `true` means auto-reactivate. | Audit runtime acquisition, then retain scheduled UI samples. |
| `V1073-CFG-003` Workshop preset | `workshopPresetName`, `currentWorkshopPreset` | **Shortcut-ready.** Exact selected-name/index agreement at a verified boundary; causality is not required for this non-polarity claim. | Do not manufacture a switch; force UI again on mapping/version audit. |
| `V1073-CFG-004` Bots preset | `botPresetName`, `currentBotPreset` | **Shortcut-ready.** Exact selected-name/index agreement at a verified boundary. | Never spend medals for causality; validate future values through naturally selected presets. |
| `V1073-CFG-005` First Perk Choice | `firstPerkIndex`, versioned Perk IDs | **Shortcut-ready** for the mapped IDs. It is an independent profile requirement, not the first Auto Pick row. | Perk-capable Farm profiles currently require `perk_wave_requirement`; Tournament declares no Perk requirement. Extend only from authoritative visible evidence; any unknown selected ID keeps the whole check in UI. |
| `V1073-CFG-006` Ban Perks | `bannedPerksIndex`, versioned Perk IDs | **Shortcut-ready** for a complete mapped selected set. | Validate each newly encountered ID; any unknown selected ID keeps the whole check in UI. |
| `V1073-CFG-007` Guardian chips | `guardianChipSlot`, `guardianSlotsUnlocked`, versioned Guardian IDs | **Shortcut-ready** for the mapped Farm/Tournament chips. | Extend through read-only equipped evidence; never equip merely to identify an ID. |
| `V1073-CFG-008` Auto Pick enabled | `autoPickPerk` | **Shortcut-ready, value-scoped** for exact boolean `true`. | The current required enabled state may skip UI. A false requirement, missing field, non-boolean, or future unsupported semantic remains UI-required. |
| `V1073-CFG-009` ranked Auto Pick order | `autoPickOrder`, versioned Perk IDs | **Shortcut-ready.** The exact 34-entry structure contains 18 visible ranked entries and 16 unranked inventory-tail entries, with all 34 mapped IDs exactly once and no sentinel. ID `11` is `unlock_random_ultimate_weapon`; only the mapped ranked block is published. | A configured list may be a shorter required prefix. Unknown IDs, duplicates, changed shape/membership, or an unresolved semantic value restore UI; the unranked tail is never compared as priority. |
| `V1073-CFG-010` Farm Free Upgrade lock subset | Three `*LockedFreeUpgrades` arrays | **Shortcut-ready, required-subset-scoped** for Shockwave Size, Bounce Shot Targets, and Bounce Shot Range with exact boolean shapes. The accepted additional `Health` bit is normalized unmanaged evidence. | Every requested lock must be set. Extra set bits neither invalidate the subset nor authorize unlock input. A missing requested bit, unknown requested index, changed length, or non-boolean restores the complete UI lock path. |
| `V1073-CFG-011` Target Priority | `targetPriorityList`, complete versioned ten-ID mapping | **Shortcut-ready** for an exact complete enforced order: `0=Closest (Default)`, `1=Basic`, `2=Fast`, `3=Tank`, `4=Ranged`, `5=Boss`, `6=In Spotlight`, `7=Protector`, `8=Elites`, `9=Fleets`. The accepted raw permutation maps the same-run Farm UI order exactly. | `enforce` requires full ordered equality; `preserve` creates no assertion. A future distinct Farm T18 order is ordinary generic serialization confirmation, not an implementation blocker; testing all permutations is neither required nor planned. |
| `V1073-CFG-012` monolithic Ultimate Weapon controls | Combined primary/detail fields | **Structural only.** The aggregate is intentionally not allowlisted because its components have different value scopes. | UI remains available for every unsupported component/value; use the independently failing rows below. |
| `V1073-CFG-012A` Poison Swamp Stun | `poisonSwampStunOff` plus exact unlocked structure | **Shortcut-ready for both calibrated polarities.** Raw `false` means on; raw `true` means off. | Require an exact boolean and unlocked Poison Swamp. Either current on/off requirement may skip UI; malformed or changed structure restores UI. |
| `V1073-CFG-012B` all nine Ultimate Weapon primaries on | Exact nine-element `ultimateWeaponUnlocked` and `ultimateWeaponOn` arrays | **Shortcut-ready, value-scoped** only when all nine exact booleans are unlocked and on. | Any subset, mixed/off request, false value, non-boolean, name/length change, or locked weapon restores UI. Validate each individual off/on index before supporting future mixed requirements. |
| `V1073-CFG-012C` Spotlight Missiles on | `spotlightSmartMissilesOff` plus exact unlocked Spotlight structure | **Shortcut-ready, value-scoped** only for raw exact `false` / required on. | Off, raw true, malformed, missing, locked, or changed structure remains UI-required until one reversible off transition and restoration are reviewed. |
| `V1073-CFG-013` Legend Tournament conditions | Tournament identity fields plus exact-version generator | **Shortcut-ready.** Seventeen consecutive event sets agreed with historical/live UI evidence. | Retain Heat/Overheat audits; validate every additional league and new exact game version independently. |
| `V1073-CFG-014` Modules | Four-entry `moduleEquipped` plus four typed `assistModuleSlots`; each exact assignment is one `ModuleItem` or explicit nil | **Shortcut-ready, exact-slot/family-scoped.** The canonical `module_info_indices` catalog contains all 24 current ID/name/family identities, six per family. `module_loadout` binds four exact Primary indices and four typed Assist slots to their families, identifies the Assist `equippedModule` field, grants `canonical_global_same_family` requirement authority, and maps only explicit nil to `empty`. The retained Farm, Tournament, and armor-variation placements are calibration evidence, not per-role allowlists. | Require all eight exact slot/family/role assignments, four unlocked exact-boolean Assist slots, and complete structure. Each assignment must be one canonical same-family name or explicit `empty`; installed names remain unique while `empty` may repeat. `enforce` requires equality; `observe` publishes any complete supported assignment without repair. Unknown IDs/names, cross-family values, duplicate installed names, missing arrays/slots/Assist item fields, locked Assist slots, changed types, ambiguous visual absence, unsupported authority scope, or enforced mismatch retain the full UI path. A new unknown ID generates global identity-review evidence; a local confirmation remains diagnostic until canonical integration. No rarity, level, stars, effects, substats, inventory, GUID, or private-value semantics are claimed. |
| `V1073-CFG-015` Damage Slider | `damageAdjustmentLog`, exact calibrated value table | **Shortcut-ready, exact-value-scoped** for raw `6=1E-22%`, `9=1E-19%`, `10=1E-18%`, and `30=1E2%`. | Matching canonical requirements may omit UI. Unknown raw values, unsupported requested values, malformed data, invalid action modes, `force_ui`, and audit retain the complete slider route. No logarithmic formula, neighboring value, or tolerance is inferred. |
| `V1073-V1101-RANGE-001` effective Attack Range | Active total `upgradeLevel[4]` or inactive `upgradeWorkshopLevel[4]`; selected/researched Range lab; `cardActive[4]`/`cardLevel[4]`; exact Cannon Primary/Assist Range effects and efficiency inputs | **Shortcut-ready calculation for exact versions 1073 and 1101.** The versioned contract reproduces native binary32 stage order, Range-card levels 1–7, module effect IDs 19–24 and gates, the `0x3e23d70c` compression constant, final meter scaling, and `Single.ToString("f2")` half-up formatting. | Publish the normalized value independently for reuse. Outside a battle it is complete with `configured_out_of_round` scope. During a battle, a nonmax current Range remains diagnostic but incomplete; only level 79 is stable enough for shortcut carry. Selected lab above researched level, malformed arrays/types/modules, duplicate or out-of-domain effects, and unlisted forward versions fail only this claim and dependents. |
| `V1073-CFG-016` Orb Distance | Calculated effective Attack Range plus one-decimal `innerOrbDistance` and `workshopOrbDistance` centers with absolute raw tolerance `0.1` | **Shortcut-ready, live-Range/unique-tuple-scoped** for the configured Farm and Tournament tuples. The retained `98.38m/87.16m/80.37m` tuple is currently eligible for active shortcut authority when the calculated Range is stable. | One unique raw-tolerance match may omit UI only when its semantic Range exactly equals complete `current_active_round` Attack Range evidence. Preset names and `rangeLevelSelected` alone grant no authority. Incomplete/out-of-round Range, overlap, out-of-range raw values, malformed dependencies, unsupported versions/requirements, `force_ui`, or audit retains the complete route. `savedWorkshopOrbDistance` is not authoritative, and tolerance never invents a new tuple. |
| `V1073-PROFILE-001` card ownership, levels, mastery unlocks, and five 28-slot decks | `cardUnlocked`, `cardLevel`, `cardMasteryUnlocked`, `slotPresetCardInt`, `slotPresetCardAssignedBool`, `slotsUnlocked` | **Structural and implemented for completed-run comparison.** Exact vectors, source fields, and changed indices are retained; base/effective width remains distinct. | Build the complete card-ID and mastery-effect map before assigning names or effects to indices. The snapshot never suppresses Cards UI. |
| `V1073-PROFILE-002` Workshop and Enhancements | Active Attack/Defense/Utility Workshop and Enhancement level/unlock arrays | **Structural and implemented for completed-run comparison.** Exact source-index levels and changed indices are retained. | Map every index; verify zero, nonzero, maxed, unlocked, and special-level semantics before naming an index or claiming an effective multiplier. |
| `V1073-PROFILE-003` Research and Labs | `researchLevel`, `labLevel`, `labsUnlocked` | **Structural and implemented for completed-run comparison.** Exact level vectors and changed indices are retained. | Map Research IDs/levels; keep active queue, duration, completion time, and effective-value formulas independent until validated. |
| `V1073-PROFILE-004` Ultimate Weapon progression | Unlock/level/Plus arrays and current primary/Plus toggles | **Structural and implemented for completed-run comparison.** Exact tuples and changed indices are retained without naming level positions. | Prove weapon order and every three-level tuple before publishing semantic stat names or effective values; configuration UI authority remains in the separate CFG rows. |
| `V1073-PROFILE-005` Guardian, Bot, and Harmony progression | Guardian chip arrays; typed Bot preset structures and cooldown-lab selections; Harmony/Power-node arrays | **Structural and implemented for completed-run comparison.** Naturally observed Farm Bot values agree with their source structures, while the record preserves indices rather than derived durations/ranges/bonuses. | Map every stable ID, Bot tuple position, node, cost, cap, and selected-lab effect through read-only/cross-channel evidence; no cost-bearing calibration. |
| `V1073-PROFILE-006` equipped Module progression | Eight equipped Primary/Assist items with `infoIndex`, rarity, level, indexed effects/locks, and Assist efficiency levels | **Structural and implemented for completed-run comparison.** GUIDs, costs, reroll counters, inventory records, and the 150-item inventory remain excluded. | Decode effect IDs, rarity/stars, levels, and efficiency formulas across naturally occurring loadouts before claiming effective values. The slot-name CFG row remains independently value-scoped. |
| `V1073-PROFILE-007` passive account, Theme, and relic progression | Pack/ad unlock booleans; `towerUnlocked[100]`, `backgroundUnlocked[100]`, `menuUnlocked[100]`, matching Dice vectors, `totalSkinsBought`; `relicsUnlocked[305]`, `profileRelics[5]` | **Structural and implemented for completed-run comparison.** Exact ownership vectors, counts, and changed indices are retained. The three Theme ownership counts are not forced to equal `totalSkinsBought`. | Map individual Theme/relic IDs and effective coin/health/damage bonuses before attributing a run delta to a specific formula. Account balances and purchase histories remain excluded. |
| `V1073-RUNTIME-001` guarded save-first Home acquisition | Proven Android-Home flush, two identical exact-target reads, exact decoder, stable restored `NEW_BATTLE` | **Shortcut-ready and implemented.** One runtime/preflight/configuration/target generation owns the lifecycle workflow and retains only normalized redacted provenance. | `save_first` uses this path; acquisition/decode uncertainty safely restored to Home runs UI, while restoration/ownership/control/boundary uncertainty blocks input. The optional audit collector is not an authority source. |
| `V1073-RUNTIME-002` atomic per-check suppression and exact-next-battle carry | Resolved configuration fingerprint, per-component decisions, runtime-owned launch, first stable `RUNNING` | **Shortcut-ready and implemented** for all currently allowlisted Home/session components together, including Damage Slider and Orb Distance. | `force_ui` preserves complete UI behavior; `comparison_audit` collects normalized comparison evidence while UI remains authoritative. A trusted exact mismatch queues only its guarded UI path; an actual repair removes only that check and closes pre-action mapping correlation, while unrelated accepted decisions remain authoritative. Independently UI-verified sections retain explicit UI provenance. Global trust, battle-identity, and save/UI-contradiction failures reject carry; a requirement-specific failure rejects only its check. Future comparisons never self-promote a manifest. |
| `V1073-RUNTIME-003` active round identity | `(versionNumber, currentTier, roundsStartedThisTier[currentTier], roundSeed)` | **Causal.** A known Home boundary preceded the first stable Tier 22 active projection with a new per-tier counter and round seed; subsequent stable revisions retained that exact identity through wave 710. No finer wall-clock latency is claimed. | This is the only durable same-battle key. Home must force an inactive proof before Start; active/Resume boundaries force this identity before adoption. Equal means same battle, unequal means later battle, and no History/UI/log-scope fallback exists. |
| `V1073-RUNTIME-004` approximately five-minute save freshness | `saveRevision`, capture time, stable source hash, active identity/wave | **Structural.** Multiple ordinary-foreground stable revisions advanced under the same Tier 22 identity through wave 710, corroborating periodic usable writes without retaining exact timestamps. The whole row is not promoted because UI-to-save lag, jitter, unchanged intervals, write-collision behavior, and a runtime staleness threshold were not measured. | An independent 300-second passive-read timer opportunistically observes naturally serialized revisions; forced and Perk-requested observations do not postpone its deadline. Metric and optional audit projection may consume the shared bundle. Pause/background behavior and tighter characterization may be measured during ordinary future use; no timer, capture time, source hash, receipt timing, or `saveRevision` authorizes navigation or claims an exact write time. |
| `V1073-RUNTIME-005` in-battle Perk inventory | `perkLevel[50]`, `perksPickedCount`, ordered `PerkPick(wave, perk)` list, versioned Perk IDs | **Shortcut-ready** for a complete exact-version snapshot. The final Tier 22 active projection contained 15 internally exact picks; all mapped picks, levels, and order agreed with the terminal UI's 11 collapsed rows. During the first enabled Tier 19 sequence, seven additional IDs were cross-channel calibrated from stable pick waves/levels and the same-round UI timeline; the repaired decoder then accepted all 56 active picks spanning 27 semantic keys. Synthetic unknown-ID, shape, count, level, and non-monotonic-order inconsistencies still publish no snapshot. | The normal run timeline consumes the monitor's exact bound prefix from periodic passive reads and may also request a prompt passive read on stable selection/exhaustion transitions. Neither path forces serialization. A later failure retains already-proved positive picks; an identical or strict prefix extension may advance the timeline, while regression, mutation, identity, mapping, or target conflict cannot. Activity scope is display metadata only. |
| `V1073-RUNTIME-006` post-run Perk clearing and same-round retention | Active Perk snapshots followed by the post-run zero/empty fields | **Causal and implemented.** The last complete Tier 22 active snapshot agreed with the terminal Perks inventory, and the immediate stable post-death save was inactive with cleared Perk fields. A later ordinary Tier 19 run retained all 56 exact picks across 1,079 stable top-bar observations, used no in-battle Perks input, and omitted terminal Perks navigation only after the complete bound finality chain. The normal monitor retains a bound monotonic positive prefix and treats a bound natural terminal clear only as window closure. | Game Over omits Perks navigation only with stable exhaustion evidence, a nonempty later active checkpoint whose saved wave includes that boundary, exact round/target binding, and a still-later natural terminal clear. If finality is absent, the guarded terminal UI path remains. Cleared fields never represent final inventory or prove absence. |
| `V1073-RUNTIME-007` structural tail identity and complete `BattleHistory` More Stats projection | Source-ordered capped `battleHistory[<=30]`; required newest-entry identity and 144 allowlisted More Stats rows | **Shortcut-ready and implemented** for report metadata and causally bound terminal report construction when the cause and value domain are mapped. Retained saves prove mixed UTC/local DateTime kinds and capped rollover. The prior 21 UI-captured battles plus the Tier 22 terminal confirm the complete ordered 144-row projection within UI precision. | Structural History identity is report-only and never determines active battle identity. Optional time/cause changes cannot manufacture a rollover. Unknown `killedBy` preserves structural report metadata but forces More Stats for the report. |
| `V1073-RUNTIME-008` Game Over history serialization timing | Pre-run history tail, Game Over observation, post-run stable save | **Causal and implemented.** The known pre-battle tail changed in the immediate stable post-death save while the natural Tier 22 terminal was preserved, proving publication at the Game Over boundary without an exact timestamp. The first enabled Tier 19 run independently recorded clearing and tail publication before normal Retry. | One immediate stable read at Game Over or Tournament Results supplies profile progression, available Tournament conditions, and the candidate report. An unchanged or unavailable tail preserves the UI fallback. `b137ea4` separately adds guarded same-session direct-Retry baseline rollover; state-machine coverage is complete. A future ordinary receipt is optional campaign evidence, not a standing rollout gate. |
| `V1073-RUNTIME-009` terminal history-tail attachment | Pre-boundary structural tail fingerprint plus newest post-boundary entry tier/time/wave | **Causal and implemented.** The pre-battle baseline changed at capped rollover to a newest Tier 22, wave 751, Boss entry whose complete semantic projection agreed with terminal tier/time/wave evidence. | Normal and Tournament report attachment requires a bound current-process terminal, matching canonical active-round identity, compatible player-save baseline, exactly one valid append or capped rollover, inactive save, complete semantic entry, matching terminal kind, and no available compact-identity contradiction. A terminal-only restart, invalid transition, unknown cause, mismatch, or acquisition failure forces More Stats. |
| `V1073-RUNTIME-010` complete `killedBy` enum | `BattleHistoryEntry.killedBy` | **Cross-channel** only for `1=Fast`, `2=Tank`, `3=Boss`, `6=Vampire`, `8=Scatter`, and `99=Surrender`; Tier 22 reconfirmed `3=Boss`, but the whole enum claim remains incomplete. Surrender identifies only the terminal cause, not its initiator. | An unknown numeric cause preserves structural tail evidence, keeps the semantic report on UI fallback, and may create a durable review receipt only after the same bound terminal supplies a normalized Game Stats/More Stats value. Reviewed canonical integration extends `runtime_save.battle_history.killed_by_ids`; `Enemy N` is never synthesized. |
| `V1073-RUNTIME-011` passive base/ad coin split augmentation | Compact Game Stats screenshot/OCR; `battleHistory.coinsEarned` total | **Cross-channel and implemented as optional augmentation.** The Tier 22 compact panel showed `28.56T` base plus `14.28T` ad equaling the `42.84T` total. `battleHistory` still contains only total coins. | Keep one passive compact capture when available. Missing compact OCR never invalidates an otherwise authoritative save report; available wave/tier/cause contradictions force UI fallback. The split remains UI-supplied rather than a save claim. |
| `V1073-RUNTIME-012` forced terminal UI audit/fallback | Existing Game Stats, Perks, clipboard/OCR More Stats, and verified terminal controls | **Shortcut-ready and preserved.** Compact Game Stats remains first and passive; More Stats retains its conditional clipboard/OCR fallback. Perks has three explicit routes: no navigation for proven save finality, saved-recency-bounded reconciliation for a usable nonfinal saved prefix, and complete traversal when no usable prefix exists. | The bounded route always proves the newest/top edge first. Its first frame is tested before any downward gesture; if necessary, it then captures toward older rows until the first unchanged saved-recency marker or the actual list edge. It may promote a tail row to an exact pick only when complete passive boundaries and terminal recency have one unique correspondence; otherwise the record keeps exact saved picks plus bounded aggregates and unresolved fields. Force the complete Perks traversal on absent, unbound, malformed, or round-conflicted prefix evidence. Force More Stats on its independent audit/fallback conditions. Wait/Retry/Home and mutation/transition confirmation always remain verified UI actions. |
| `V1073-RUNTIME-013` natural-boundary temporal auditor | Bounded normalized runtime projections plus passive boundary observations | **Structural and implemented.** The append-only schema, exact-target/session guards, core state machine, and bounded-evidence/nonblocking regressions are available for explicitly named campaigns. `V1073` identifies the originating semantic-evidence authority, not a literal runtime version lock: the decoder must publish the matching normalized runtime audit capability from an exact or declared-compatible, shape-valid mapping. The actual mapping/version remains in every receipt, and a session fails closed on capability, mapping, version, identity, or progression discontinuity. Past Tier 19 campaigns recorded Home-to-terminal progression and exposed direct-Retry identity retention plus missing Perk IDs, which were repaired without changing the authoritative UI pipeline. | Default off and campaign-only. In normal App runtime it projects shared typed periodic/Perk passive, forced-attachment, and natural-terminal bundles; it has no acquisition or cadence authority of its own. Receipts have no automated consumer and are reviewed by a human against the campaign question. This is not unknown-field discovery; targeted mapping calibration gathers its own purpose-specific evidence, while the narrow exact-wave Perk resolver remains version-specific. The auditor emits candidates only: no attachment, record construction, Strategy fact, input, lifecycle/dispatch change, Perks-navigation decision, or UI suppression. Target/process or mapping-context changes fail closed. Upgrade and survival components remain unavailable. |
| `V1073-RUNTIME-014` in-battle upgrade levels and gold-box state | `upgradeLevel[20]`, `upgradeDefenseLevel[20]`, `upgradeUtilityLevel[20]` plus the three Workshop-level arrays | **Structural except for Attack Range index 4.** The effective-Range contract proves `upgradeLevel[4]` is the active total level, `upgradeWorkshopLevel[4]` is the inactive baseline, and 79 is max. Other upgrade indices, caps, and special-level semantics remain unclaimed. | Reuse the Range contract only through its independent calculator. Create versioned index/cap evidence before publishing any other current level, baseline, delta, or `maxed` value. Never generalize index 4 or infer Max from magnitude alone. |
| `V1073-RUNTIME-015` survival-ability checkpoint state | Demon Mode, Nuke, and Second Wind `*UsedThisRound`, use-count, cooldown, `*WavesUntilRefresh`, active/effect-timeout, and timer fields | **Structural.** The fields exist in an active round and clear after the round, but boolean polarity, sentinel values, units, exact-wave relationships, and write timing are not calibrated. | At natural activations, retain stable before/during/rearmed/terminal snapshots and matching visual events. Prove each ability independently, including auto versus manual behavior and multiple activations. Publish counts and state first; publish an exact activation wave only where a causal timer formula is proven, otherwise a save-wave interval. |
| `V1073-RUNTIME-016` save-checkpoint and visual-tail event merge | Same-round stable revisions, normalized survival checkpoints, passive visual activation events, and terminal Battle History counts | **Structural.** Source precedence and fail-closed merge policy are specified; no cache or merger exists. | Merge monotonically by guarded round identity. Count deltas define event intervals; matching visual transitions may refine them. Retain confirmed visual events after the last stable active save through Game Over and reconcile against terminal counts. Never double count, discard an unexplained count, or synthesize an exact wave. Conflict or missing binding forces the full UI audit. |
| `V1073-RUNTIME-017` active-round battle tallies | Version-allowlisted `*ThisRound`/`*ThisWave` counters and current round totals | **Structural for version 1073.** The root contains broad live damage, enemy, currency, skip, free-upgrade, survival, and subsystem tallies; version 1073 publishes only their completed-history counterparts. | Prioritize fields that replace current OCR/navigation or strengthen terminal reconciliation. Validate monotonicity, units, reset/clear timing, exceptional decreases, and correspondence to completed-history rows. Publish leaf contracts and provenance; stale tallies remain observational and cannot authorize an input. The capability originating at 1101 does not back-propagate to 1073. |
| `V1073-RUNTIME-018` remaining transient control and cooldown candidates | `gameSpeedMemory`, buy multipliers, non-Range Card activity, and UW/Bot/Guardian cooldown arrays | **Structural.** Plausible fields exist but are deliberately unpublished and may lag the visible game by a complete save interval. Damage Slider, the Range-card dependency, calculated Attack Range, and bounded Orb tuple fields are owned by their separate narrow contracts rather than this candidate row. | Rank remaining fields by current observation cost, then calibrate each claim separately across changed/restored values and stable writes. Current-state enforcement and post-action verification remain visual unless the use case explicitly tolerates checkpoint staleness. |
| `V1073-RUNTIME-019` completed-run profile progression attachment and delta | Same-target-generation terminal stable save; versioned `profile_progression`; newest earlier normal battle snapshot | **Structural and implemented.** Exact-version synthetic coverage validates malformed-field isolation, private-field exclusion, source-index diffs, first-run baselines, prior-record selection, Markdown rendering, and target-generation discard. One bounded read-only live save normalized all 12 current components without retaining the raw save. | The same terminal read now also feeds report attachment, but profile progression stays global and nonblocking while the report independently requires current-process binding and tail causality. A terminal-only process may attach global profile state but cannot inherit Strategy, a save-derived report, or process-local trackers. |
| `V1073-TOURNEY-001` Tournament condition profile/history coverage | Exact-version generator, event identity fields, and Heat/Overheat UI | **Shortcut-ready** for Legend condition identity only. | Complete UI inventory, effective descriptions, lower leagues, and unknown-condition preservation in the separate [Tournament condition plan](../backlog/runtime-and-validation.md#tournament-battle-condition-evidence). |

### Versioned audit addition: `data-9-game-1101`

| Audit ID and normalized claim | Version-1101 source | Evidence level and retained evidence | Current runtime disposition |
| --- | --- | --- | --- |
| `V1073-V1101-RANGE-001` effective Attack Range parity | Independently disassembled version-1073 and version-1101 IL2CPP methods, constants, Card table, Module effect definitions, assist efficiency, and display formatter | **Shortcut-ready for both listed versions.** Instruction sequences and binary constants are identical; the mapping mirrors one semantic/binding contract while retaining each structural version's provenance. | The capability's own `supported_game_versions` is exact and does not inherit `allow_forward_game_versions`. Version 1102 or later may retain unrelated structurally compatible checks, but Attack Range and dependent Orb Distance remain unavailable until their mechanics are reviewed. |
| `V1101-RUNTIME-017` active economy, progress, and coin-source tallies | Declared semantic capability with 29 cumulative leaf bindings; inherited completed-history claims; compact terminal Game Stats for ad coins | **Cross-channel and implemented.** Two same-identity active checkpoints were monotonic, their interval CPH agreed with the contemporaneous UI scale, the Guardian Fetch algebra reconciled, and every mapped terminal counter was nondecreasing at the causally attached natural boundary. Only normalized allowlisted evidence is retained above. | Version 1101 is the authority origin and binding provider for `thetower.player_save.active_run_tallies.v1`, not a literal consumer gate. Exact 1073 remains unavailable; unknown additive forward revisions inherit only this declared capability. Unknown fields remain unpublished. Each leaf and derived dependency fails or conflicts independently. `ActiveRunMetricMonitor` only consumes forced, natural, periodic-passive, or Perk-requested bundles; it retains process/target/round binding and reconciles each eligible terminal claim. Log scope is presentation metadata. No metric grants input, lifecycle, navigation, or Strategy authority. |

The complete currently eligible configuration set is adopted atomically by
`V1073-RUNTIME-001`/`002`; this is not a promotion of unrelated profile or
active-round rows. `V1073-RUNTIME-013` remains a default-disabled,
campaign-only observation projector over explicitly supplied shared bundles
and a session-local audit cache. Its past Home-to-terminal campaign is
complete; there is no standing rollout reason to leave it enabled. The
independent battle-identity path forces `ActiveRoundIdentity` at active
lifecycle boundaries; structural History tail evidence is report-only. The
separate terminal consumer constructs a report only after current-process
binding and same-source tail advancement. Foreground freshness extensions, active upgrades,
survival timing and repeated-event merging, remaining live tallies, and future unknown
`killedBy` values remain independently fail-closed work rather than blockers
for configuration preflight.

`V1073-RUNTIME-019` remains a separate global structural projection even though
it shares the single terminal save read. It does not use the audit collector's
session cache or weaken terminal run binding. Its snapshot/delta can explain
which save-backed account fields changed between recorded runs; causal
attribution to CPH, cells, or survival still requires the relevant semantic
mapping and run evidence.

Profile groups broaden the diagnostic view but cannot influence automation
until their own row is Shortcut-ready. Every published group carries mapping
ID, source fields, capture time, audit-row ID, evidence level, and explicit
provenance from the resolved structural and authority mappings.

### Runtime adoption and active work

Guarded acquisition, allowlisted per-check adoption, independent fallback and
repair, contradiction invalidation, exact launch carry, and the `save_first`,
`force_ui`, and `comparison_audit` policies are implemented. The matrix above
owns evidence maturity and current disposition; it is not a work queue.

All remaining rollout, comparison, active-upgrade, survival, tally, and
future-version work is owned by the
[runtime backlog](../backlog/runtime-and-validation.md#current-validation-gates).
Every new exact game version receives its own complete raw-field disposition
manifest and per-component validation metadata even when the runtime's
temporary forward-compatibility gate succeeds. A newly observed field needs a
reviewed classification and count/hash update before entering that exact
manifest. Incompatible versions or shapes, unknown IDs or values, ambiguity,
and conflict continue through the UI. Runtime evidence and receipts never edit
or promote their own authority manifest.

### Runtime mapping discovery and local confirmation

An exact or `compatible_exact_revision` snapshot may retain only a bounded,
semantic-neutral discriminator for an otherwise unmapped Perk, Guardian,
Module, Target Priority, orb-calibration, terminal `killedBy`, or Tournament
league value. The existing UI fallback remains authoritative. Before its first
repair input, that fallback may pair the discriminator with complete normalized
UI evidence from the same process, target generation, canonical round identity, and
Home, active-round, or completed-tail boundary. Raw save objects, account data,
raw OCR text, partial inventories, and evidence observed after mutation are not
eligible.

Every resulting record is an append-only, mode-0600 candidate receipt under
`logs/player_save_mapping_candidates/`. A receipt is review evidence only: it
cannot suppress UI, authorize input or repair, change Strategy/configuration,
or edit a tracked mapping. Home and attachment observers close their pairing
window on repair or continuity loss. Game Over and Tournament Results reuse the
single typed natural-boundary acquisition and its already-proved structural
tail; candidate collection never requests another save read or blocks terminal
routing. An unknown Tournament league name remains review-only because the
condition generator has no general league-name mapping owner.

One narrow case receives local future-decode **identity evidence**
automatically: a deterministic pre-mutation `module_info_index` pairing from
an exact-version boundary. The runtime first durably rereads its candidate
receipt, then appends an accept event to the ignored
`config/player_save_versions/local/data_<data>_game_<game>.confirmed.json`
document. Accept and revoke events are locked, atomic, private, append-only,
generation-counted, and capacity-reserved so every active acceptance remains
revokable. The global Module identity mapping is a bijection of
`infoIndex -> (name, family)` within one version. The slot recorded on an event
is pairing provenance, not an enforcement target; several different identities
may therefore be learned through the same slot over time. A conflicting raw
ID, name, or family is rejected.

Local identity evidence applies only during a later fresh decode whose exact
identity, root class, mapping resolution, authority/structural IDs, validation
policy, revision-compatibility declaration, and canonical Module dependency
still match. It never changes the snapshot whose UI produced the receipt, and
it adds only the global identity for diagnostics. It never appends a
`module_loadout` value, makes Modules observed, suppresses UI, or authorizes an
equip/repair. Each snapshot publishes the exact canonical authority/structural
mapping-set fingerprint used by its decoder and an effective-mapping
fingerprint after the local identity overlay;
all carry, setup-capture, attachment, History, and terminal provenance binds to
that fingerprint. Dependency drift, conflict, malformed local state, or a
read/write failure leaves canonical values authoritative and the unknown value
on its existing UI fallback.

The control surface publishes the combined local-confirmation and candidate
review queue as a persistent nonmodal warning. A `compatible_exact_revision`
proposal is an atomic review artifact for both the authority owner and the
exact structural mirror, with a base hash and scoped operation for each. The
runtime decoder never applies that proposal. Server revision 42 instead offers
the same narrow operator workflow in both control-surface GUIs. It has no
feature-worktree selection: the operator reviews one exact candidate and its
mapping target hashes, then separately confirms creation of one verified child
of current `main` under `refs/thetower/save-mapping-candidate`. The client
cannot supply a path, ref, target, operation, value, commit message, or Git
identity.

Review binds the candidate, every canonical base/result hash and file mode, the
prospective canonical mapping-set fingerprint, and the standardized commit
contract. It records but does not fingerprint the whole `main` commit, so an
unrelated advance does not stale otherwise identical mapping inputs. Staging
rechecks the proposal against current `main` under a process-shared lock,
holds one final candidate-receipt snapshot, constructs the commit with a private
Git index, and durably records its exact identity. One atomic Git ref
transaction verifies the current parent while creating only the fixed private
ref. Production `main`, its index, and its worktree are never changed.
Relevant Git crash locks make recovery unconfirmed and are never removed
automatically. Ref, target, mode, journal, or unrelated-state ambiguity is
preserved for inspection; no automatic reset or unconfirmed retry occurs.

The warning remains `promotion_pending` while the commit exists under the
private ref and becomes `production_validation_pending` after production
contains it. If `main` advances first, exact unchanged target before-hashes
permit an explicitly confirmed retirement and restaging on the new tip;
changed targets fail closed. A later complete stable save acquisition records a
bounded confirmation receipt only when the running decoder's mapping identity
and canonical mapping-set fingerprint match the deployed commit. The receipt also
binds acquisition start time and the production commit captured when that
runtime loaded; an acquisition begun before staging cannot clear the
checkpoint. Receipt work is deferred until the outer save-operation and
mutation boundaries have released, so it cannot delay Android foreground
restoration. The observer is advisory: failure cannot invalidate the save or
block automation, but it leaves the warning visible. Only the matching
post-deployment decode durably records the receipt and then retires the exact
private ref and transaction. Candidate and local-store failures remain
diagnostic and do not become startup gates.

## Acquisition provenance and temporal authority

This section defines the staged contract for the acquisition-consolidation
stack. The acquisition foundation is implemented on
`feature/player-save-acquisition-foundation`, the lifecycle handoff on
`feature/player-save-boundary-handoff`, typed attachment temporal authority on
`feature/player-save-temporal-authority`, and shared Perk monitoring on
`feature/save-backed-perk-monitoring-v2`.

`StablePlayerSaveAcquirer` owns one in-flight exact-target runtime
read under the ADB target-operation lock: owned target/generation checks before
and after the read, the quiet stable transport, decode, immediate payload
disposal, capture timing, sanitized reason codes, and redacted provenance. It
returns one immutable in-memory acquisition bundle that any number of
independent projectors may consume without another pull. The bundle is passed
explicitly at its coherent lifecycle boundary; there is no global "latest
snapshot" cache whose age or binding a later caller must guess.

The acquisition owner does not own Android input, lifecycle policy, semantic
projection, or UI fallback. `GuardedPlayerSaveSerializer` continues to own the
background/restore sequence and may publish a forced bundle only after the
original source, context, control authority, target, and generation are all
reverified. A lifecycle owner, rather than a reader-selected string, issues a
bound Game Over or Tournament Results token for natural-boundary evidence.
Consumers retain their distinct fail-open, retry, UI-fallback, or input-blocking
behavior.

### Acquisition types

| Type | How it is established | What it can establish | What it cannot establish |
| --- | --- | --- | --- |
| `forced_serialization` | A guarded Home or active-attachment lifecycle backgrounds the game, obtains one stable exact-target snapshot, restores the source, and revalidates every guard. | Active/inactive round identity at that boundary, authoritative mapped current configuration, and same-round facts whose separate temporal class permits a broader claim. | The result of a later UI input, an unvalidated mapping, or facts from another active-round identity. |
| `natural_boundary` | A current-process lifecycle token binds one stable read to Game Over or Tournament Results, where the game naturally publishes terminal state. | Mapped terminal facts, a causally advanced structural History tail, and other explicitly validated terminal projections. | The next battle's current configuration or an arbitrary read that merely happens to occur on a terminal-looking screen. |
| `passive_stable_read` | One exact-target stable read occurs without forcing a game flush. | A transport-stable checkpoint, including positive same-round facts whose temporal class tolerates unknown observation lag. | Snapshot freshness, current configuration, negative evidence, or completeness merely from two identical reads, capture time, or `saveRevision`. |

The typed bundle replaces free-form runtime acquisition labels and the runtime
`freshness_verified=True` assertion. The explicit offline import command keeps
its separate operator assertion and does not enter runtime authority. The
bundle carries type, status, safe reason,
private exact target/generation binding, a redacted binding fingerprint,
acquisition/capture/completion times, transport stability, optional boundary
kind/time, and the normalized `PlayerSaveSnapshot`. A target handoff discards
the snapshot. Unsupported mappings, changed shapes, and unavailable semantic
components are successful acquisitions followed by projection failures; they
are not transport failures.

Forced Home and attachment serialization, natural terminal capture, and the
periodic/explicit-Perk passive paths use this owner. Audit, metrics, History reporting,
and Tournament projection consume a bundle acquired by the actual boundary
owner; they do not initiate runtime reads. `GuardedPlayerSaveSerializer` retains the full
lock transaction across backgrounding and restoration and publishes a
`forced_serialization` bundle only after target, source, context, and control
revalidation. Terminal capture issues typed natural-boundary evidence and fans
one bundle out to profile progression, structural History, completed-report,
Perk-window, audit, and Tournament-condition projectors; all existing
consumer-specific UI fallback and blocking classes remain intact.

### Better Control freshness consumers

The Better Control Model never turns a recent or byte-stable pull into a claim
that a save was newly requested. Its current-save consumers use these exact
boundaries. A missing, unsupported-revision, structurally incompatible, or
unprojectable save is unusable data, not an unsafe UI boundary: after the
guarded owner proves source restoration, every consumer with an established UI
equivalent automatically uses that complete UI route after canonical battle
identity is established. Identity itself has no UI fallback. Only source-
restoration, target, process, owner, or action-authority loss is catastrophic. A
trusted mapped mismatch remains valid save evidence, but it is recoverable:
repair it at an already-safe Home boundary or complete the consumer with exact
degraded evidence.

| Workflow | Acquisition and binding | Failure/authority result |
| --- | --- | --- |
| Active or resumable **Attach to Battle** | The request freezes the accepted selected Strategy definition. After exact intent and explicit Enable where needed, the guarded serializer forces one exact-target save and restores the visible source. | A valid `ActiveRoundIdentity` is mandatory. With no prior ID it is adopted; equal proves the same battle; different proves a later battle and discards old battle-local state. History/UI cannot substitute for identity. After identity succeeds, configuration/report projection may complete degraded or use its established UI equivalent. Ownership, target, control, restoration, or uncertain-input loss is catastrophic. |
| **Return Control** from active or Home Resume | Return records no save while Pause remains authoritative. A later explicit Enable grants the guarded Return owner and forces serialization. | Equal identity resumes the same battle; a different identity adopts the manually started successor before configuration reconciliation. After identity succeeds, unavailable mapped configuration may use supported UI checks and complete degraded. No History/UI identity fallback exists. |
| **Return Control** at Home New | The Home preflight owner forces serialization for the exact workflow and target. | The save must prove `round_active=false`, which closes the retained battle identity. Configuration may then reconcile or use supported Home UI. The operator starts a separate new battle through Start; Return does not infer or launch one. |
| **Return Control** at Game Over, including manual Surrender | The preferred route consumes the lifecycle-issued current-process `natural_boundary` bundle, binding exact target generation, terminal active-round identity, terminal observation, structural History transition, and mapped cause. | If terminal projections are unavailable at the still-bound Game Over boundary, Return may use Game Stats, Perks, and More Stats UI. Conflicting owner, target, or canonical identity blocks input. Tournament Results and unknown evidence are not advertised as Return boundaries. |
| **Capture current setup as…** | One guarded `forced_serialization` at verified Home New, Home Resume, or active battle produces a redacted provenance receipt plus runtime-operation/target-generation/active-round binding. The preview is derived from that in-memory bundle only. | This authoring preview has no complete supported UI equivalent, so unusable mapping/projection/identity reports `unavailable` and opens no configuration UI. Failure to restore the source after lifecycle input, loss of owner/target, or an uncertain dispatched input is catastrophic and may Pause. Activity scope is optional receipt metadata. |
| Periodic passive observation | The scheduler attempts one `passive_stable_read` every 300 seconds against an exact current battle binding. Forced serialization and prompt reads do not reset that deadline. | It may retain permitted positive temporal facts. It cannot prove when the game wrote, satisfy a current-evidence workflow, or authorize input. |
| Perk checkpoint reads | A stable Perk selection or exhaustion event may request one `passive_stable_read`, shared by multiple projectors as one immutable in-memory bundle. Audit and metrics cannot request a read. | It may retain permitted positive temporal facts, but cannot satisfy Attach, Return, capture, negative current-configuration, or completeness claims. |
| Offline import | The command's explicit operator assertion remains outside runtime authority. | It cannot issue a Better Control receipt or become process-local workflow evidence. |

The exact running Attach/Return claim is process-local and is not reconstructed
from redacted ledger fields after restart. Battle identity is always
save-backed; only downstream configuration/report reconciliation may carry
bound UI provenance. Save-partial configuration UI consumes the retained typed
acquisition and rechecks the live runtime, target generation, canonical round
identity, observation, and process-local claim before each continuation. If
that context or private claim is gone, the workflow stops safely instead of
opening UI from ledger or cached data.
A terminal Attach record with `reporting_status: unavailable` is not a replay
receipt. It records that the exact process-local adoption succeeded while the
durable receipt could not be written; a restarted process cannot reconstruct
input authority from that marker.

The terminal History projector proves the structural append or capped rollover
once, independently of completed-report semantics. A successful projection may
be retained as a bounded, redacted, one-use activity-log handoff for reporting.
A missing scope, scope rotation, malformed payload, or failed atomic update may
drop that metadata but cannot affect lifecycle or input. Unknown `killedBy`
still preserves structural report evidence while retaining More Stats fallback.

At Home, current round state and configuration require a new forced
serialization even when the requirement set is empty; no report handoff
satisfies them. Tournament Results always
passes either complete or explicitly unavailable conditions from its terminal
bundle; its handler performs no second player-save read.

### Temporal classes and merge rules

Acquisition type and temporal meaning are separate dimensions. In particular,
a passive read cannot prove that a checkpoint is current, but an append-only
same-round projection can still prove the positive prefix already serialized.

| Temporal class | Current examples | Required interpretation |
| --- | --- | --- |
| `current_configuration` | A mapped Home configuration check | Requires `forced_serialization`; it says nothing about a later input. |
| `round_invariant` | Workshop preset, Free Upgrade locks, equipped Guardians, selected Bot preset, equipped Modules, First Perk Choice, Perk Bans, and Perk Auto Pick order | Once exact mapping and round binding prove the value belonged to the round, it applies to the whole round. Bot progression or medal-funded upgrades are separate point-in-time facts. |
| `point_in_time` | Cards preset and Cards at an active attachment | Describes only the acquisition boundary. Different later evidence may represent a legal change rather than a contradiction. |
| `monotonic_round_prefix` | Ordered saved Perk picks and their saved waves | Every published pick is historical positive evidence. A passive checkpoint cannot prove that no later pick exists. |
| `terminal_final` | Causally attached Battle History / More Stats fields | Requires a bound natural terminal and a valid append or capped rollover. |
| `boundary_clear` | Inactive, cleared terminal Perk fields | Closes the active checkpoint window; it is not the final Perk inventory and cannot erase the newest complete active prefix. |

Every temporal claim requires an exact, explicitly compatible, or
capability-resolved mapping plus compatible target generation and, where
applicable, round identity. Activity scope may accompany a claim for reporting
but never participates in equality or authority. Differing
`round_invariant` values for one round invalidate that claim instead of using
last-write-wins. Point-in-time Card observations retain their individual
boundaries. Perk checkpoints must be identical or strict prefix extensions;
regression or reordering preserves already proved historical picks but makes
current/final completeness unavailable. An authoritative UI/save
contradiction still fails closed. UI owns applying and verifying a change, and
save-authoritative current state after that change requires another forced
serialization.

Running-attachment configuration projection produces typed facts under a
private exact process/target-generation/round binding. App revalidates the
current target, generation, process session, active battle, and canonical round
identity before dispatch. Redacted provenance may retain a log-scope fingerprint
for reporting, but changing or losing it cannot invalidate the fact.

No Strategy treats Workshop preset, Free Upgrade locks, equipped Guardians,
selected Bot preset, equipped Modules, First Perk Choice, Perk Bans, and Perk
Auto Pick order as sticky round-invariant facts. Identical observations merge,
but a differing complete save value or authoritative complete preset UI
observation marks that field
unavailable for the round; a later value cannot restore it. Partial Guardian or
Module UI evidence cannot replace a complete save claim. Cards retain their
point-in-time boundary and may legally differ at a later capture. Bot
progression is neither projected into nor compared with the selected Bot
preset. The resulting facts populate the existing
`observed_run_configuration` actual loadout, never configured intent.

The same typed attachment object supplies configured in-battle preflight with
every complete projected fact, including Cards, Workshop, Free Upgrade locks,
selected Bot preset, equipped Guardians and Modules, Auto Pick, Card Recharge,
Perk configuration, Target Priority, and the supported Ultimate Weapon
components. Its one-use consumer rechecks process, target generation, activity
scope, and active-battle ownership at every consumption. A forced attachment
save is authoritative current evidence for all of those facts at its exact
capture boundary; temporal class controls what a mismatch means rather than
whether the fact may be consumed.

An attachment-initialization `unavailable_deferred` Free Upgrade-lock record is
only a diagnostic placeholder. The exact-bound attachment consumer is consulted
before that placeholder is retained; a complete match replaces it, a complete
mismatch is reported under the normal round-invariant policy, and only a missing
or unusable fact remains deferred.

An exact match omits that check's redundant UI. Missing, incomplete,
unsupported, unparseable, or rebound evidence retains the supported per-field
UI fallback. A field with no current-battle UI route remains explicitly
deferred or unavailable, while unresolved Workshop preserves its Home-only
deferral. A
complete saved mismatch never opens UI merely to confirm the save. Workshop,
Free Upgrade locks, selected Bot preset, equipped Guardians, equipped Modules,
First Perk Choice, Perk Bans, and Perk Auto Pick order retain their
round-invariant class: a mismatch is
recorded and reported but is nonblocking because it cannot be repaired for the
active battle. A fully observed UI fallback mismatch for one of those fields
has the same result. Point-in-time and current-configuration mismatches are
also observational during Attach: supported validators measure but do not
change the current battle. Every profile or run waiver is applied before
attachment reconciliation, so a waived fact is neither consumed nor reported
as a mismatch. Every source and disposition remains explicit in session
evidence. No attachment
path adds game-Home repair, another Android lifecycle action, another save
read, or Surrender authority.

The normal Perk monitor consumes the same forced-attachment bundle, periodic
and explicitly requested Perk-checkpoint `passive_stable_read` bundles, and the
lifecycle-issued natural terminal bundle. Its checkpoint scheduler is active
independently of audit opt-in; when audit is enabled, the same immutable object
is queued for audit projection rather than pulled again. The monitor owns no
ADB read, acquisition or synchronization lock, lifecycle action, or audit
schedule; App serializes cross-thread domain calls while the scheduler owns an
independent 300-second timer and coalesces stable Perk selection/exhaustion
requests without resetting that timer. The optional audit
collector remains neither an acquisition service nor an authority source.

The run timeline is now a consumer of that monitor rather than a second Perks
UI observer. Stable top-bar schedule or `View Perks` transitions request one
coalesced `passive_stable_read`; they do not require or request forced
serialization and grant no input authority. Numeric schedule observations use
the independently detected battle wave as their current-wave anchor. The full
OCR next-wave token must satisfy the bounded schedule lead; otherwise only the
longest suffix produced by removing at most two leading separator-artifact
digits may qualify. Split, substituted, trailing, or still-implausible OCR
remains an invalid read-only retry. The ordinary top-bar OCR uses its existing
raw-color fast path; an unreadable, implausible, or low-confidence terminal
label receives one Otsu-isolated retry so outlined `View Perks` text remains
detectable. Stable high-confidence observations are still required. The worker
retains a detached exact prefix
under the monitor lock, and App applies it to the persisted same-battle timeline
on the serialized main thread. Attachment's already-owned forced bundle seeds
the same path without reacquisition. Every timeline row therefore carries the
save's exact oldest-first sequence, pick wave, semantic ID/key, and level-after.
A pending visual boundary means only that no later positive prefix has yet
serialized; it is never evidence of absence.

Each complete active Perk projection is bound to the exact process session,
target generation, mapping, and active-round identity. Activity scope may be
retained for presentation but is ignored for equality and authority. An
identical later checkpoint or a strict prefix extension advances evidence;
capture-time regression, prefix regression, reordering, identity change,
mapping change, or conflicting exhaustion evidence makes final completeness
unavailable for that round. `saveRevision` remains diagnostic and is never a
freshness signal. Acquisition or projection failure retains the already proved
positive prefix but restores the terminal UI fallback until a valid active
checkpoint recovers it.

Stable high-confidence `View Perks` observations are persisted with their
log scope, wave, capture time, and source fingerprint, then bound to the exact
active identity by the monitor. Log scope is display metadata. Game Over may omit Perks-panel navigation
only when a nonempty complete active checkpoint was captured after that
exhaustion observation, its saved wave includes the exhaustion wave, and a
later matching natural Game Over bundle contains the inactive cleared Perk
projection. The clear closes the checkpoint window; it never becomes final
inventory or proves absence.

When finality is not proved, a still-bound, non-conflicted exact prefix owns a
bounded terminal repair: open Perks, always prove the newest/top edge, and test
that first viewport before dispatching any downward gesture. If the unchanged
saved-recency marker is not yet visible, capture consecutive older viewports
until the marker or actual list edge, then return to Game Stats. Rows before
the marker are positive tail evidence. A complete set of passive selection
boundaries may give them one unique exact sequence/wave correspondence;
repeated leveled families, a missing marker, an unchanged recency head despite
later boundaries, or an incomplete boundary interval remain aggregate or
unresolved evidence. A later
passive acquisition failure does not erase the retained exact prefix. Missing,
malformed, unbound, or round-conflicted prefix evidence uses the complete
terminal Perks traversal. Optional Perk data failure never changes the selected
Wait/Retry/Home route. Compact Game Stats, More Stats fallback, and all other UI
action authority remain unchanged.

## Authority and fallback

| Situation | Required behavior |
| --- | --- |
| Unknown forward revision with a declared additive semantic provider | Publish only capabilities whose dependency contracts still validate; retain actual version/provider provenance and ignore additions. |
| Unknown revision or data lineage with no declared provider | Publish safe envelope diagnostics only; use UI for legacy checks. |
| One missing, malformed, renamed, or changed dependency | Mark that leaf and its derived dependents unavailable; preserve unrelated claims. |
| Candidate mapping, check not explicitly validated | Report comparison results and run the existing UI check. |
| Explicitly validated check, complete exact match, and verified serialization boundary | The caller may accept save evidence for that check unless an audit is due. |
| Explicitly validated check, complete exact mismatch, and globally trusted snapshot | Queue only that check's existing guarded UI verification/repair before input; a read-only UI match preserves other decisions, while an actual repair removes only the affected check and closes pre-action mapping correlation before its first mutation. |
| No verified serialization boundary for a current-configuration claim | Treat the pull as potentially stale and use the existing UI check. Positive facts with a separately declared lag-tolerant temporal class remain governed by that class. |
| Missing, incomplete, stale, unsupported, or forced-audit value | Use the existing UI check for that setting without treating it as a trusted mismatch. |
| UI automation changes a setting | Remove that setting's save authority and close pre-action mapping correlation before the first mutation, verify the result in UI, preserve unrelated accepted decisions, and never treat the pre-action save as confirmation of the repair. |
| Authoritative UI contradicts a save match or finds a trusted mismatch already matching | Invalidate the complete snapshot and fail closed. |

Tournament identity is not a recently changed profile setting. Its terminal
record attachment may use a stable read without an app-pause flush only when an
active seed is present or the checked number, same-number registry entry,
league, and UTC event date all agree. That exception derives immutable event
identity; it never confirms an input or suppresses a configuration repair.

The save is suitable for persistent state that the game has finished writing.
Fresh UI evidence remains authoritative for the current screen, temporary
state, transition completion, controls that are not mapped, and the result of
an input. Runtime integration must preserve every current UI checker rather
than deleting it.

## Acquisition and bounded evidence

The local reader never modifies the input file. `PlayerSaveParser.parse_bytes`
and `parse_file` are the sole public decode/projection API; the older decode and
file helpers are compatibility wrappers. The parser eagerly builds all
allowlisted projections while the decoded root is in scope, recursively freezes
the normalized snapshot, and discards the root and input bytes. It holds no
global "latest save" cache.

`App` owns one parser and one `StablePlayerSaveAcquirer`. Preflight, History,
guarded serialization, passive scheduling, terminal projection, Tournament
projection, and the optional campaign auditor require that injected acquirer;
none may construct another acquisition owner. A device pull reads the default
operator-confirmed path
`/sdcard/Android/data/com.TechTreeGames.TheTower/files/playerInfo.dat` through
ADB and accepts a payload only after two consecutive reads are byte-identical.
Decode then applies compressed and decompressed size limits, checks gzip
integrity, and parses an object NRBF root. It then resolves legacy bindings and
semantic capabilities independently. Unknown additions affect only manifest
diagnostics. An exact or compatible legacy mapping may supply its existing
projections; a semantic-only forward provider supplies only the capabilities it
declares, never legacy checks by implication.

This is a trusted-single-user project. The normalized report's allowlist is an
evidence-hygiene, log-size, and subsystem-coupling boundary, not an
authentication or hostile-user security control. It deliberately omits
`playerID`, `userName`, and every unmapped raw field. Its runtime projection
contains only authority-allowlisted
round, Perk, and completed-battle evidence; its profile-progression projection
contains only the exact-version structural allowlist described above.
Non-report history fields, balances, purchase histories, Module GUIDs, and
arbitrary inventory records remain unpublished. It includes SHA-256 source and
canonical component fingerprints so observations can be correlated without
retaining the save. Completed battle records retain the normalized allowlist
and exact deltas, not the raw NRBF root. The operator-owned
`playerInfo.dat` must remain untracked and must never be copied into tests,
logs, commits, or retained runtime evidence.

## Inspection tool

The optional decoder is owned by the `player-save` group in `pyproject.toml`
and is already present in the complete locked development environment. Do not
install packages ad hoc into a completed shared environment; update the tracked
dependency contract and locks instead. From a development worktree whose
`.venv` is absent, provision it with:

```bash
/usr/bin/python3.12 tools/development.py bootstrap
```

Inspect a local save and compare it with the Farm requirements:

```bash
.venv/bin/python tools/import_player_save.py \
  --file playerInfo.dat \
  --requirements config/run_profiles/farm.yaml
```

After completing [`../live_preflight.md`](../live_preflight.md), the same
report can be built from the
configured device:

```bash
.venv/bin/python tools/import_player_save.py \
  --adb-target localhost:5555 \
  --requirements config/run_profiles/farm.yaml
```

`--force-ui-audit` is available to make the audit requirement explicit for a
validated mapping. `--freshness-verified` is an explicit caller assertion that
the game completed a known serialization boundary before the pull; the tool
does not infer that fact from a recent capture timestamp. `--output` writes
only the normalized JSON report, never the raw save.

## Tournament record attachment

At `TOURNAMENT_RESULTS`, the terminal handler makes one bounded stable save
read before persistence. Complete exact-version evidence becomes the
schema-version-2 record's `battle_conditions` field and Markdown section.
Failure remains explicit, does not invalidate the result, and leaves the UI
fallback required. A duplicate terminal capture can enrich a recent record
without reopening its detail controls.

Historical records use explicit UTC event-date mappings rather than assuming
the Tournament calendar continues forever. The backfill command is dry-run by
default and refuses to replace conflicting complete evidence:

```bash
.venv/bin/python tools/backfill_tournament_conditions.py \
  --event 2026-08-01=287
```

Add `--apply` only after reviewing the complete plan. JSON and Markdown are
then regenerated atomically from the same normalized record.

## Version update and promotion procedure

At runtime an unrecognized higher identity receives an automatic, read-only
resolution attempt. Exact mapping wins. Legacy version-wide reuse still
requires its same-lineage compatibility declaration. Independently, a lower
provider with an `additive_dependencies` policy may supply only the semantic
capability it declares, with the observed identity and provider recorded
separately. Every dependency is projected independently: additions remain
unpublished, while a missing or malformed leaf disables only that leaf and its
derived dependents. Undeclared legacy projections immediately retain their UI
routes. `saveRevision` is checkpoint evidence, not a mapping selector.

For every released game version that changes either mapping identity field:

1. Keep the previous mapping immutable and add a new exact mapping file under
   `config/player_save_versions/` with maturity `candidate`.
2. Capture a stable save through two identical reads. Confirm gzip/NRBF decode,
   root class, required fields, all array lengths, and the effective card-slot
   width before assigning semantic names.
3. Run the focused decoder and reconciliation tests. Add fixtures synthesized
   from the structural contract; do not commit a real player save.
4. If the new root is a strict additive extension, it may declare a prior exact
   authority plus a narrower compatibility allowlist. The loader requires all
   added exact-manifest fields to remain `unknown`, all authority fields and
   required arrays to survive, and every inherited check to have been
   validated by that authority. Runtime projection inheritance must be
   explicit. Never inherit a version-derived algorithm such as Tournament
   conditions without its own version validation.
5. Compare new, changed, or version-derived values with authoritative UI
   evidence from the same profile and game version. Record agreements,
   differences, and fields that remain unmapped. Add a new check to the exact
   mapping only after every value it can suppress has passed cross-channel
   validation. A partial mapping leaves unsupported or incomplete checks
   explicitly UI-required.
6. Run a forced UI audit after promotion and retain periodic or release-boundary
   audits. Any later discrepancy demotes the mapping or the affected field and
   immediately restores UI navigation.

Runtime adoption should begin in audit-only mode: pull once at a safe preflight
boundary, reconcile the snapshot with the resolved profile, and compare it
with the normal UI inventory. Only an exact validated check or a check inherited
through the declared compatibility gate may turn a complete per-check match
into a navigation shortcut.
