# Player-Save Architecture and Versioned Evidence

`core/player_save.py` decodes The Tower's `playerInfo.dat` as an independent,
read-only view of persistent profile configuration. It is intentionally not a
replacement for action verification or a universal parser for unknown game
versions.

## Current status

The first mapping is `data-9-game-1073`, selected by the exact save fields
`dataVersion: 9` and `versionNumber: 1073`. Its overall maturity is `candidate`,
with an explicit per-check validation allowlist. It was derived from the
repository-root operator sample and recognizes the sample's five 28-slot
card-preset records, including the distinction between its stored base slot
count and the effective preset width. The same exact mapping now includes the
validated Legend Tournament condition generator.

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
These are slot-scoped equipped-value facts, not a generic Module ID or inventory
map. The currently observed armor variation is evidence only; it does not
replace the Tournament reference.

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
Modules and complete mapped observation-only Tournament Modules; Demon
Mode/Nuke recharge behavior;
Auto Pick enabled for the required value `true`; complete Target Priority ID
and ordering semantics; Poison Swamp Stun in both calibrated polarities; all
nine Ultimate Weapon primaries for the all-on requirement; and Spotlight
Missiles for the on requirement. This adoption reused the prior calibration
evidence atomically; it did not require or perform another live campaign.

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
- current Legend Tournament identity and version-derived Battle Conditions.

Card recharge modes are now mapped and validated. Damage Slider and Orb
Distance remain explicitly unmapped and always use the UI. A complete mapped
Tournament loadout may supply observation without opening Modules; a difference
from `tournament_standard` is reported, never enforced or repaired. Magnetic
Hook, any unsupported requested name or unknown slot `infoIndex`, and any
nonexact structure or partial loadout retain the full Modules UI route. More
fields can be added only with cross-channel calibration, not merely because a
plausible raw field exists.

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

Normal battle records use schema 5 and Tournament records use schema 3. The
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
authorization. A complete allowlisted exact mismatch in an enforced check is
`save_mismatch`: it queues only that check's existing guarded UI path while
unrelated accepted decisions remain authoritative. Unsupported requirements,
unknown IDs, incomplete per-check structure, and forced audit are ordinary
`ui_required` dispositions rather than trusted mismatches.

Unknown version or globally incompatible structure, unequal/pull/decode or
freshness failure, target/ownership/context/requirement change, interrupted
control, or failed foreground/Home restoration invalidates the snapshot or
blocks every later input according to the existing failure class. `force_ui`
performs no save lifecycle; `comparison_audit` collects normalized comparison
evidence but keeps UI authoritative.

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
snapshot and fails closed. The V1073-RUNTIME-013 collector remains a separate
optional observation stream and supplies no Home preflight authority.

Accepted session-only values are single-use evidence for the exact next
runtime-owned `NEW_BATTLE` launch and its first stable `RUNNING` boundary. The
binding covers Auto Pick enabled `true`, exact complete Target Priority order,
an exact enforced Farm Module assignment or complete observation-only mapped
Tournament assignment, all nine primaries on, Spotlight Missiles on, Poison
Swamp Stun, and exact Home sections needed by the later consistency check.
Restart, attachment, Retry without this Home preflight,
strategy/configuration/target change, manual or ambiguous launch,
WAIT/Pause/Stop, a save/UI contradiction, requirement change, or an unrelated
later battle rejects the complete carry. A verified Home, Target Priority,
Poison Swamp Stun, Damage Slider, Orb Distance, or other independent UI-only
repair preserves unrelated carry. The repaired check stays supported only by
its UI evidence unless a genuinely new authoritative snapshot is acquired.
Save evidence never authorizes a tap, repair, launch, lifecycle transition,
attachment, terminal binding, dispatch, or strategy action.

The same authoritative Home snapshot supplies the source-tagged structural
newest history-tail identity to Activity Continuity before its UI baseline may
run. It remains usable when `killedBy` lacks a semantic mapping. Direct Retry
uses a fresh stable two-identical-read exact-target acquisition, passively
polls an unchanged tail, accepts one append or capacity-30 rollover, and falls
back to guarded UI only when acquisition, structure, transition, and source
binding permit it. It never consumes a collector receipt or performs a second
Home acquisition. UI and save fingerprints are compared only within the same
source/mapping contract; legacy schema-1 scopes are conservatively recognized
as UI-derived.

At an attachment already showing `RUNNING`, `save_first` always uses a fresh
guarded save and never opens Battle History UI. This includes a missing
baseline, an old UI-derived baseline, and a replacement-process comparison.
The shared guarded Android-Home serializer requires the exact runtime session,
activity scope, lifecycle-owned active-battle state, and target/generation to
survive two stable pre-background `RUNNING` frames, `KEYCODE_HOME`, two
byte-identical save reads, launcher restoration, and two stable post-restore
`RUNNING` frames. The fresh exact-version snapshot must contain an active-round
identity. With no prior identity, its newest completed tail becomes the
baseline. A same-source unchanged tail preserves the scope; a changed tail
starts a later scope. A UI baseline may migrate only through independently
normalized Tier/Wave/Battle Date, whose save date must be unambiguous .NET
`Local` wall-clock evidence matching at minute precision. An explicit mismatch
proves a later scope; an ambiguous or insufficient cross-source comparison
starts a clearly marked conservative scope from the fresh save. Source
fingerprints are never compared across mappings. An unusable save waits and
retries without UI navigation. Process, scope, target, control, source,
active-identity, or restoration ambiguity blocks all later input.

The same accepted active-attachment snapshot projects complete normalized
checks from the exact mapping's validation allowlist. No Strategy consumes
those values as source-tagged observations, not as repair or action authority,
and visits only fields that remain unresolved. A validated Attack Dissonance
sword resolves its inaccessible Damage Slider without probing the disabled
menu; a Utility star does not. Save-resolved Workshop, Free Upgrade-lock,
Cards, and Perk configuration also remove their post-run Home detail traversal;
finalization still requires verified Home. No raw save, private field,
incomplete check, or unvalidated candidate value enters the observation. Home
`RESUME_BATTLE`, interrupted History, `force_ui`, and `comparison_audit` retain
their declared UI behavior. This path neither uses the unmapped Force Cloud
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

Snapshot schema 2 contains the repository-local save-first runtime foundation;
its runtime projection is schema 2. For the exact version-1073 mapping it
publishes capture metadata, `saveRevision`, `roundActiveBool`, `currentWave`,
the active identity tuple, and independent normalized Perk and Battle History
tail components. Perk ID `0` is `max_health` (Max Health). Perks are emitted
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
These observations broaden the validation plan but do not publish those raw
fields or promote their semantics. In particular, the save has no literal
gold-box flag and one snapshot is not a complete survival-activation history.

The source-ordered `battleHistory` list may contain at most 30 entries. Only
the newest entry is part of the tail contract, so it must retain the exact
148-field shape and exact field types. Its privacy-safe structural identity and
fingerprint use battle date kind/ticks, tier, wave, game/real time, numeric
`killedBy`, and Tournament identity without interpreting the cause enum. A
separate semantic projection is emitted only when the newest cause ID is
mapped; it contains all 144 current More Stats rows and its own canonical
fingerprint. An unknown future cause therefore blocks completed-record
publication but does not erase tail-change evidence. UTC and local DateTime
ticks are never ordered against each other; the game's established source list
order owns which entry is newest.

The active-round/terminal projection foundation does not itself bind a process
or grant navigation authority. The terminal consumer now reuses one stable
exact-target read and builds a normal or Tournament report only after a bound
activity scope, compatible save-sourced pre-terminal baseline, exact append or
capacity rollover, inactive save, semantic entry, and terminal-kind proof. A
failure publishes an explicit UI fallback without exposing a partial completed
entry. The configuration coordinator remains a separate exact-Home consumer,
and the V1073-RUNTIME-013 sidecar still polls and retains only audit state. A
malformed newest entry publishes neither structural nor semantic tail evidence.
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

## Complete validation program

The validation target is not every opaque counter in the save. It is every
normalized claim that the importer publishes, every value used to compare a
resolved profile, and every value that could suppress an existing UI check.
For each exact game version, a field-disposition manifest must classify every
raw field name without retaining its value as one of:

- structural identity or shape;
- automation-gating configuration;
- profile observation;
- private and always redacted;
- deliberately ignored with a reason; or
- unknown and therefore unpublished.

This makes coverage auditable without copying a real save or leaking account,
currency, history, or other private values. The current redacted
`profile_summary` is diagnostic until each semantic count or level group below
is separately validated; array length alone proves structure, not meaning.

The version-1073 manifest now inventories all 739 exact decoded-root keys: 13
structural, 31 automation-gating, 51 profile-observation, 34 private, 69
ignored-with-reason, and 541 unknown. The mapping loader validates the exact
categories, disjoint membership, declared count, and canonical field-name
hash. A decoded root must then match that complete name set before any mapped
value is published. An added or removed member therefore invalidates the
exact-version shape and restores the existing UI fallback; it never silently
inherits a disposition or semantic claim.

### Evidence and promotion standard

A versioned claim progresses through four evidence levels:

1. **Structural** — exact version, root class, field type, and complete array
   dimensions are known.
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
   exact mapping.

Every causal test must record the before, mutated, restored, and unrelated
control fields; flush through a proven lifecycle boundary; visually verify the
restoration; and finish at the original safe boundary. A new exact game
version starts again at structural status even when its fields look unchanged.

### Versioned audit matrix: `data-9-game-1073` / revision 4

This is the single authoritative matrix for every normalized claim published
or proposed for exact game version 1073. The evidence level names the highest
complete level for the **whole row**, not an encouraging partial result:
`Structural`, `Cross-channel`, `Causal`, or `Shortcut-ready`. A row can be
implemented as a privacy-safe observation at Structural or Cross-channel level
when its stated evidence is complete, but it cannot suppress its UI route
until it is Shortcut-ready. `Shortcut-ready` here describes the
decoder/reconciler; runtime navigation adoption is a separate row. Every new
exact game version starts a new matrix at Structural level.

| Audit ID and normalized claim | Version-1073 source | Evidence level and retained evidence | Required work / current runtime disposition |
| --- | --- | --- | --- |
| `V1073-RAW-001` raw-field disposition manifest | Exact decoded-root keys; no raw values | **Structural and complete for version-1073 field coverage.** Stable exact-target reads established the `SaveLoad+PlayerData` root and all 739 decoded keys. The versioned manifest classifies each name exactly once and is protected by count, canonical hash, loader validation, and strict decoded-root equality. | Disposition coverage is closed, but it is not semantic promotion: private, ignored, and unknown values remain unpublished, and indexed meanings, formulas, caps, and effective values remain in their separate rows. Every new exact version starts a new manifest; any field drift fails closed until reviewed. |
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
| `V1073-CFG-014` Modules | Four `moduleEquipped` `ModuleItem` entries plus four typed `assistModuleSlots` | **Shortcut-ready, exact-slot/value-scoped.** Farm maps cannon Primary Amplifying Strike (`45`), armor Primary Orbital Augment (`46`), generator Primary Black Hole Digestor (`27`), core Primary Multiverse Nexus (`37`); cannon Assist Being Annihilator (`9`), armor Assist Anti-Cube Portal (`20`), generator Assist Singularity Harness (`30`), and core Assist Dimension Core (`38`). Tournament evidence adds generator Primary Project Funding (`43`), core Primary Dimension Core (`38`), core Assist Harmony Conductor (`39`), plus observed alternatives armor Primary Anti-Cube Portal (`20`) and armor Assist Space Displacer (`19`). | Require exact slot/family/role/mapped name, four unlocked exact-boolean Assist slots, and complete structure. `enforce` requires equality; `observe` reports any complete mapped assignment without repair. Unknown IDs, nil/missing/locked/partial entries, Magnetic Hook or another unsupported request retain the full UI path. No generic Module-ID, rarity, level, stars, effects, substats, inventory, GUID, or private-value semantics are claimed. |
| `V1073-CFG-015` Damage Slider | No accepted field | **Structural.** The absence of an accepted normalized source is explicit. | In an explicitly authorized test battle, correlate at least two values and restoration, percentage encoding, and save timing; UI remains required. |
| `V1073-CFG-016` Orb Distance | Candidate distance/preset fields not accepted | **Structural.** Candidate fields are deliberately unpublished. | In an explicitly authorized battle, cycle known Extra/Workshop presets, prove units and selected-preset semantics, and restore the original pair; UI remains required. |
| `V1073-PROFILE-001` card ownership, levels, mastery unlocks, and five 28-slot decks | `cardUnlocked`, `cardLevel`, `cardMasteryUnlocked`, `slotPresetCardInt`, `slotPresetCardAssignedBool`, `slotsUnlocked` | **Structural and implemented for completed-run comparison.** Exact vectors, source fields, and changed indices are retained; base/effective width remains distinct. | Build the complete card-ID and mastery-effect map before assigning names or effects to indices. The snapshot never suppresses Cards UI. |
| `V1073-PROFILE-002` Workshop and Enhancements | Active Attack/Defense/Utility Workshop and Enhancement level/unlock arrays | **Structural and implemented for completed-run comparison.** Exact source-index levels and changed indices are retained. | Map every index; verify zero, nonzero, maxed, unlocked, and special-level semantics before naming an index or claiming an effective multiplier. |
| `V1073-PROFILE-003` Research and Labs | `researchLevel`, `labLevel`, `labsUnlocked` | **Structural and implemented for completed-run comparison.** Exact level vectors and changed indices are retained. | Map Research IDs/levels; keep active queue, duration, completion time, and effective-value formulas independent until validated. |
| `V1073-PROFILE-004` Ultimate Weapon progression | Unlock/level/Plus arrays and current primary/Plus toggles | **Structural and implemented for completed-run comparison.** Exact tuples and changed indices are retained without naming level positions. | Prove weapon order and every three-level tuple before publishing semantic stat names or effective values; configuration UI authority remains in the separate CFG rows. |
| `V1073-PROFILE-005` Guardian, Bot, and Harmony progression | Guardian chip arrays; typed Bot preset structures and cooldown-lab selections; Harmony/Power-node arrays | **Structural and implemented for completed-run comparison.** Naturally observed Farm Bot values agree with their source structures, while the record preserves indices rather than derived durations/ranges/bonuses. | Map every stable ID, Bot tuple position, node, cost, cap, and selected-lab effect through read-only/cross-channel evidence; no cost-bearing calibration. |
| `V1073-PROFILE-006` equipped Module progression | Eight equipped Primary/Assist items with `infoIndex`, rarity, level, indexed effects/locks, and Assist efficiency levels | **Structural and implemented for completed-run comparison.** GUIDs, costs, reroll counters, inventory records, and the 150-item inventory remain excluded. | Decode effect IDs, rarity/stars, levels, and efficiency formulas across naturally occurring loadouts before claiming effective values. The slot-name CFG row remains independently value-scoped. |
| `V1073-PROFILE-007` passive account, Theme, and relic progression | Pack/ad unlock booleans; `towerUnlocked[100]`, `backgroundUnlocked[100]`, `menuUnlocked[100]`, matching Dice vectors, `totalSkinsBought`; `relicsUnlocked[305]`, `profileRelics[5]` | **Structural and implemented for completed-run comparison.** Exact ownership vectors, counts, and changed indices are retained. The three Theme ownership counts are not forced to equal `totalSkinsBought`. | Map individual Theme/relic IDs and effective coin/health/damage bonuses before attributing a run delta to a specific formula. Account balances and purchase histories remain excluded. |
| `V1073-RUNTIME-001` guarded save-first Home acquisition | Proven Android-Home flush, two identical exact-target reads, exact decoder, stable restored `NEW_BATTLE` | **Shortcut-ready and implemented.** One runtime/preflight/configuration/target generation owns the lifecycle workflow and retains only normalized redacted provenance. | `save_first` uses this path; acquisition/decode uncertainty safely restored to Home runs UI, while restoration/ownership/control/boundary uncertainty blocks input. The optional audit collector is not an authority source. |
| `V1073-RUNTIME-002` atomic per-check suppression and exact-next-battle carry | Resolved configuration fingerprint, per-component decisions, runtime-owned launch, first stable `RUNNING` | **Shortcut-ready and implemented** for all currently allowlisted Home/session components together. | `force_ui` preserves complete UI behavior; `comparison_audit` collects normalized comparison evidence while UI remains authoritative. A trusted exact mismatch queues only its own guarded UI path and a verified repair preserves unrelated accepted decisions/carry. Trust, continuity, requirement, and save/UI-contradiction failures reject all carry. Future comparisons never self-promote a manifest. |
| `V1073-RUNTIME-003` active round identity | `(versionNumber, currentTier, roundsStartedThisTier[currentTier], roundSeed)` | **Causal.** A known Home boundary preceded the first stable Tier 22 active projection with a new per-tier counter and round seed; subsequent stable revisions retained that exact identity through wave 710. No finer wall-clock latency is claimed. | The guarded replacement-process Current-run comparison requires this identity after forced serialization and stable `RUNNING` restoration; it does not manufacture terminal binding or process-local evidence. `V1073-RUNTIME-013` still uses the tuple only for observation receipts. |
| `V1073-RUNTIME-004` approximately five-minute save freshness | `saveRevision`, capture time, stable source hash, active identity/wave | **Structural.** Multiple ordinary-foreground stable revisions advanced under the same Tier 22 identity through wave 710, corroborating periodic usable writes without retaining exact timestamps. The whole row is not promoted because UI-to-save lag, jitter, unchanged intervals, write-collision behavior, and a runtime staleness threshold were not measured. | The default-300-second observation-only shared polling cadence is implemented with 30–3600-second bounds independently of audit opt-in. It can retain positive lag-tolerant facts but does not make a freshness claim. Pause/background behavior and tighter characterization may be measured during ordinary future use; no capture time, source hash, receipt timing, or `saveRevision` authorizes navigation or claims an exact write time. |
| `V1073-RUNTIME-005` in-battle Perk inventory | `perkLevel[50]`, `perksPickedCount`, ordered `PerkPick(wave, perk)` list, versioned Perk IDs | **Shortcut-ready** for a complete exact-version snapshot. The final Tier 22 active projection contained 15 internally exact picks; all mapped picks, levels, and order agreed with the terminal UI's 11 collapsed rows. During the first enabled Tier 19 sequence, seven additional IDs were cross-channel calibrated from stable pick waves/levels and the same-round UI timeline; the repaired decoder then accepted all 56 active picks spanning 27 semantic keys. Synthetic unknown-ID, shape, count, level, and non-monotonic-order inconsistencies still publish no snapshot. | The normalized decoder/reconciler claim is complete. `V1073-RUNTIME-013` records session-local prefix deltas and fails non-prefix progress closed. If a structurally valid but unmapped ID appears, the collector now tries the exact same-wave UI/save resolver described below; only a unique allowlisted assignment restores the whole projection. Ambiguity or inconsistency still publishes no inventory and keeps UI evidence. The privacy-safe static calibration is retained in `test/fixtures/player_save_perk_id_calibration_v1073.json`. |
| `V1073-RUNTIME-006` post-run Perk clearing and same-round retention | Active Perk snapshots followed by the post-run zero/empty fields | **Causal and implemented.** The last complete Tier 22 active snapshot agreed with the terminal Perks inventory, and the immediate stable post-death save was inactive with cleared Perk fields. The normal monitor now retains a bound monotonic positive prefix and treats a bound natural terminal clear only as window closure. | Game Over omits Perks navigation only with stable exhaustion evidence, a nonempty later active checkpoint whose saved wave includes that boundary, exact round/scope/target binding, and a still-later natural terminal clear. Empty, absent, malformed, regressed, reordered, conflicted, failed, or unbound evidence preserves the existing Perks UI route. Cleared fields never represent final inventory or prove absence. |
| `V1073-RUNTIME-007` structural tail identity and complete `BattleHistory` More Stats projection | Source-ordered capped `battleHistory[<=30]`; exact newest 148-field `BattleHistoryEntry` shape | **Shortcut-ready and implemented** for structural activity continuity and causally bound terminal report construction when the cause is mapped. Retained saves prove mixed UTC/local DateTime kinds and capped rollover. The prior 21 UI-captured battles plus the Tier 22 terminal confirm the complete ordered 144-row projection within UI precision; malformed entries and unknown semantic causes fail closed independently. `adGemsThisRound` supplies Ad Gems. | Source-tagged structural identity supplies Home, direct-Retry, guarded replacement-process `RUNNING` continuity, and same-source terminal tail proof. Trust source order rather than cross-kind ticks; keep raw entries, arbitrary fields, and account data unpublished. Unknown `killedBy` still permits structural continuity but forces More Stats for the report. |
| `V1073-RUNTIME-008` Game Over history serialization timing | Pre-run history tail, Game Over observation, post-run stable save | **Causal and implemented.** The known pre-battle tail changed in the immediate stable post-death save while the natural Tier 22 terminal was preserved, proving publication at the Game Over boundary without an exact timestamp. The first enabled Tier 19 run independently recorded clearing and tail publication before normal Retry. | One immediate stable read at Game Over or Tournament Results supplies profile progression, available Tournament conditions, and the candidate report. An unchanged or unavailable tail preserves the UI fallback. `b137ea4` separately adds guarded same-session direct-Retry baseline rollover; its next ordinary end-to-end receipt confirmation remains pending. |
| `V1073-RUNTIME-009` terminal history-tail attachment | Pre-boundary structural tail fingerprint plus newest post-boundary entry tier/time/wave | **Causal and implemented.** The pre-battle baseline changed at capped rollover to a newest Tier 22, wave 751, Boss entry whose complete semantic projection agreed with terminal tier/time/wave evidence. | Normal and Tournament report attachment requires a bound current-process terminal, matching activity-scope ID, compatible player-save baseline, exactly one valid append or capped rollover, inactive save, complete semantic entry, matching terminal kind, and no available compact-identity contradiction. A terminal-only restart, UI-sourced or absent baseline, invalid transition, unknown cause, mismatch, or acquisition failure forces More Stats. The independent collector remains observation-only. |
| `V1073-RUNTIME-010` complete `killedBy` enum | `BattleHistoryEntry.killedBy` | **Cross-channel** only for `1=Fast`, `2=Tank`, `3=Boss`, `6=Vampire`, `8=Scatter`, and `99=Surrender`; Tier 22 reconfirmed `3=Boss`, but the whole enum claim remains incomplete. Surrender identifies only the terminal cause, not its initiator. | Extend the allowlist only from naturally observed values. Any future unknown value preserves structural tail evidence but makes the semantic entry unavailable and requires UI evidence; this fail-closed extension does not block `V1073-RUNTIME-013`, and `Enemy N` is never synthesized. |
| `V1073-RUNTIME-011` passive base/ad coin split augmentation | Compact Game Stats screenshot/OCR; `battleHistory.coinsEarned` total | **Cross-channel and implemented as optional augmentation.** The Tier 22 compact panel showed `28.56T` base plus `14.28T` ad equaling the `42.84T` total. `battleHistory` still contains only total coins. | Keep one passive compact capture when available. Missing compact OCR never invalidates an otherwise authoritative save report; available wave/tier/cause contradictions force UI fallback. The split remains UI-supplied rather than a save claim. |
| `V1073-RUNTIME-012` forced terminal UI audit/fallback | Existing Game Stats, Perks, clipboard/OCR More Stats, and verified terminal controls | **Shortcut-ready and preserved.** Compact Game Stats remains first and passive; More Stats retains its conditional clipboard/OCR fallback; Perks navigation is conditional only on a fully proven save-backed final prefix. | Force Perks UI unless all `V1073-RUNTIME-006` rules pass. Force More Stats on audit, unknown version/shape/cause, absent or incompatible baseline, unbound terminal, invalid tail transition, terminal-kind mismatch, or save-record contradiction. Wait/Retry/Home and mutation/transition confirmation always remain verified UI actions. |
| `V1073-RUNTIME-013` natural-boundary audit collector | Stable privacy-safe runtime projections plus passive boundary observations | **Structural.** The default-disabled collector, append-only schema, exact-target/session guards, core state machine, and privacy/nonblocking regressions are implemented. The first enabled ordinary Tier 19 run recorded exact Home, first active identity, revisions `46418`–`46465`, terminal clearing, and the wave-5182 Tank tail candidate while the complete UI pipeline remained authoritative and unchanged. Its direct Retry exposed fail-closed old-identity retention and seven missing Perk IDs; `b137ea4` repairs both. The deployed fresh session then accepted revision `46521`, counter 232, wave 290, and a complete mapped two-pick checkpoint without inheriting the terminal-only process's unavailable baseline. | In normal App runtime the collector projects the same typed passive, forced-attachment, and natural-terminal bundles used by other consumers; it no longer owns a duplicate cadence/read. It emits audit candidates only: no attachment, record construction, Strategy fact, Perks-navigation decision, input, lifecycle/dispatch change, or UI suppression. Its Perk-ID resolver consumes only current-process exact-wave timeline batches and fails ambiguity or inconsistency closed. Target/process changes clear correlation state. No special battle is required; upgrade and survival components remain unavailable. |
| `V1073-RUNTIME-014` in-battle upgrade levels and gold-box state | `upgradeLevel[20]`, `upgradeDefenseLevel[20]`, `upgradeUtilityLevel[20]` plus the three Workshop-level arrays | **Structural.** Array shapes and current-versus-Workshop deltas are observed, but the complete index, cap, and special-level semantics are not retained validation evidence. | Create a versioned index/cap manifest and validate non-maxed, round-purchased, Workshop-maxed, locked, and special upgrades against canonical UI evidence. Publish current level, baseline, delta, and `maxed` only as one independently failing component. Never infer Max from magnitude alone. |
| `V1073-RUNTIME-015` survival-ability checkpoint state | Demon Mode, Nuke, and Second Wind `*UsedThisRound`, use-count, cooldown, `*WavesUntilRefresh`, active/effect-timeout, and timer fields | **Structural.** The fields exist in an active round and clear after the round, but boolean polarity, sentinel values, units, exact-wave relationships, and write timing are not calibrated. | At natural activations, retain stable before/during/rearmed/terminal snapshots and matching visual events. Prove each ability independently, including auto versus manual behavior and multiple activations. Publish counts and state first; publish an exact activation wave only where a causal timer formula is proven, otherwise a save-wave interval. |
| `V1073-RUNTIME-016` save-checkpoint and visual-tail event merge | Same-round stable revisions, normalized survival checkpoints, passive visual activation events, and terminal Battle History counts | **Structural.** Source precedence and fail-closed merge policy are specified; no cache or merger exists. | Merge monotonically by guarded round identity. Count deltas define event intervals; matching visual transitions may refine them. Retain confirmed visual events after the last stable active save through Game Over and reconcile against terminal counts. Never double count, discard an unexplained count, or synthesize an exact wave. Conflict or missing binding forces the full UI audit. |
| `V1073-RUNTIME-017` active-round battle tallies | Version-allowlisted `*ThisRound`/`*ThisWave` counters and current round totals | **Structural.** The root contains broad live damage, enemy, currency, skip, free-upgrade, survival, and subsystem tallies; only their completed-history counterparts are semantically normalized today. | Prioritize fields that replace current OCR/navigation or strengthen terminal reconciliation. Validate monotonicity, units, reset/clear timing, exceptional decreases, and correspondence to completed-history rows. Publish separate components and provenance; stale tallies remain observational and cannot authorize an input. |
| `V1073-RUNTIME-018` transient control and cooldown candidates | `gameSpeedMemory`, buy multipliers, candidate Damage Slider/Orb Distance fields, Card activity, and UW/Bot/Guardian cooldown arrays | **Structural.** Plausible fields exist but are deliberately unpublished and may lag the visible game by a complete save interval. | Rank by current observation cost, then calibrate each claim separately across changed/restored values and stable writes. Current-state enforcement and post-action verification remain visual unless the use case explicitly tolerates checkpoint staleness. |
| `V1073-RUNTIME-019` completed-run profile progression attachment and delta | Same-target-generation terminal stable save; versioned `profile_progression`; newest earlier normal battle snapshot | **Structural and implemented.** Exact-version synthetic coverage validates malformed-field isolation, private-field exclusion, source-index diffs, first-run baselines, prior-record selection, Markdown rendering, and target-generation discard. One bounded read-only live save normalized all 12 current components without retaining the raw save. | The same terminal read now also feeds report attachment, but profile progression stays global and nonblocking while the report independently requires current-process binding and tail causality. A terminal-only process may attach global profile state but cannot inherit Strategy, a save-derived report, or process-local trackers. |
| `V1073-TOURNEY-001` Tournament condition profile/history coverage | Exact-version generator, event identity fields, and Heat/Overheat UI | **Shortcut-ready** for Legend condition identity only. | Complete UI inventory, effective descriptions, lower leagues, and unknown-condition preservation in the separate [Tournament condition plan](../backlog/runtime-and-validation.md#tournament-battle-condition-evidence). |

The complete currently eligible configuration set is adopted atomically by
`V1073-RUNTIME-001`/`002`; this is not a promotion of unrelated profile or
active-round rows. `V1073-RUNTIME-013` remains an observation-only projector
over explicitly supplied shared bundles and a session-local audit cache; its
first ordinary Home-to-terminal deployment pass is complete. The deployed
direct-Retry repair still awaits one passive ordinary rollover receipt. The
independent activity-continuity path now uses
the structural tail for initial Home, runtime-owned direct Retry, and a guarded
replacement-process Current-run comparison already at `RUNNING`. The separate
terminal consumer constructs a report only after current-process binding and
same-source tail advancement. Foreground freshness extensions, active upgrades,
survival timing and repeated-event merging, live tallies, and future unknown
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
provenance from the exact-version mapping.

### Runtime adoption and active work

Guarded acquisition, allowlisted per-check adoption, independent fallback and
repair, contradiction invalidation, exact launch carry, and the `save_first`,
`force_ui`, and `comparison_audit` policies are implemented. The matrix above
owns evidence maturity and current disposition; it is not a work queue.

All remaining rollout, comparison, active-upgrade, survival, tally, and
future-version work is owned by the
[runtime backlog](../backlog/runtime-and-validation.md#current-validation-gates).
Every new exact game version starts with its own complete raw-field disposition
manifest and per-component validation metadata; a newly observed field needs a
reviewed classification and count/hash update. Unknown versions, shapes, IDs,
values, ambiguity, or conflict continue through the UI. Runtime evidence and
receipts never edit or promote their own authority manifest.

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
| `forced_serialization` | A guarded Home or active-attachment lifecycle backgrounds the game, obtains one stable exact-target snapshot, restores the source, and revalidates every guard. | Authoritative mapped current configuration at that boundary; same-round facts whose separate temporal class permits a broader claim. | The result of a later UI input, an unvalidated mapping, or facts from another activity scope. |
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

Forced Home and attachment serialization, ordinary History reads, natural
terminal capture, shared passive monitoring/audit, and standalone Tournament
acquisition now use this owner. `GuardedPlayerSaveSerializer` retains the full
lock transaction across backgrounding and restoration and publishes a
`forced_serialization` bundle only after target, source, context, and control
revalidation. Terminal capture issues typed natural-boundary evidence and fans
one bundle out to profile progression, structural History, completed-report,
Perk-window, audit, and Tournament-condition projectors; all existing
consumer-specific UI fallback and blocking classes remain intact.

### Better Control freshness consumers

The Better Control Model never turns a recent or byte-stable pull into a claim
that a save was newly requested. Its current-save consumers use these exact
boundaries:

| Workflow | Acquisition and binding | Failure/authority result |
| --- | --- | --- |
| Active or resumable **Attach to Battle** | After exact intent and explicit Enable where needed, the guarded serializer backgrounds the verified active battle, acquires one stable `forced_serialization` bundle for the exact target generation, restores `RUNNING`, validates process/session/source activity scope and active-round identity, then lets Activity Continuity bind the final persisted scope. | Before-background loss is unavailable/pending with no input. Any restoration, temporal-identity, owner, or authority loss after backgrounding interrupts/fails that exact workflow and leaves Automation Paused. Optional allowlisted fact projection may be absent, but required round identity may not. |
| **Return Control** from active or Home Resume | Return first refreshes passive observation while the acknowledged indefinite Pause remains authoritative. A later explicit Enable grants only the guarded Return hold; Home Resume is restored to the same battle, then the running attachment serializer and exact binding above are used. | Ordinary input stays blocked. Trusted mismatches Pause for operator review; only genuinely unresolved allowlisted checks may enter the existing UI verifier, and only after the forced-save receipt is durable. Another explicit Enable requests a new serialization rather than reusing the former bundle. |
| **Return Control** at Home New | The existing Home preflight owner requests a new forced serialization and proves the save reports no active round for the same target and activity scope. | A mismatch remains Paused. UI fallback is limited to unresolved allowlisted configuration checks after the forced-save receipt. Blocked, incomplete, or post-background binding loss terminalizes the exact Return once; later heartbeats do not repeat serialization. |
| **Return Control** at Game Over, including manual Surrender | The lifecycle-issued current-process `natural_boundary` bundle binds exact target generation, activity scope, terminal observation, structural History transition, and mapped cause. | Missing or conflicting evidence performs no terminal UI input and keeps Return blocked/Paused. Tournament Results and unknown evidence are not advertised as Return boundaries. |
| **Capture current setup as…** | One guarded `forced_serialization` at verified Home New, Home Resume, or active battle produces a redacted provenance receipt plus runtime/scope/target-generation/active-round binding. The preview is derived from that in-memory bundle only. An active-battle Return Control that has Paused on a trusted mismatch may pass its still-retained exact typed acquisition to Capture, labelled `retained_return_control_refresh`; that path requests no second serialization and cannot be recovered from the receipt alone. | Ordinary Automation Paused reports `automation_paused`; it never substitutes cached evidence. Exact retained Return evidence remains Paused, does not resolve Return, and is interrupted if its process/scope/target/battle binding is gone. Loss after backgrounding fails a new capture and leaves Automation Paused. A failed ready-ledger write retains the exact process-local preview, Pauses, and retries only the atomic receipt; a restarted/orphaned `capturing` ledger fails closed without another serialization. Saving uses the immutable preview fingerprint and performs no device input. |
| Ordinary continuity/audit/monitor reads | `passive_stable_read` remains explicitly passive and may be shared by multiple projectors as one immutable in-memory bundle. | It may retain permitted positive temporal facts, but cannot satisfy Attach, Return, capture, negative current-configuration, or completeness claims. |
| Offline import | The command's explicit operator assertion remains outside runtime authority. | It cannot issue a Better Control receipt or become process-local workflow evidence. |

The exact running Attach/Return claim is process-local and is not reconstructed
from redacted ledger fields after restart. A saved receipt describes what was
proved; it is not a reusable snapshot handle. Configuration UI always consumes
the retained typed acquisition and rechecks the live context first. If the
context or private claim is gone, the workflow stops safely instead of opening
UI from ledger or cached data.

The terminal History projector now proves the structural append or capped
rollover once, independently of completed-report semantics. A successful
projection is retained as a bounded, redacted, one-use activity-scope handoff
for Game Over → Home, Game Over → direct Retry, and Tournament Results → Home.
The destination validates the same process, exact target generation, source
scope, mapping, transition, and natural-boundary timing before adopting the
tail as its baseline. Unknown `killedBy` or another semantic report failure
therefore preserves More Stats fallback while leaving structural continuity
usable. A process restart, target handoff, scope mismatch, malformed payload,
or failed atomic update rejects the handoff and restores the established
acquisition or Battle History UI route.

At Home, a valid handoff satisfies only structural History continuity. Current
configuration still requires a new forced serialization when requirements are
present. If `save_first` has no handoff and the configuration requirement set
is empty, the same guarded Home owner now acquires one baseline-only forced
bundle instead of opening Battle History first. Tournament Results always
passes either complete or explicitly unavailable conditions from its terminal
bundle; its handler performs no second player-save read.

### Temporal classes and merge rules

Acquisition type and temporal meaning are separate dimensions. In particular,
a passive read cannot prove that a checkpoint is current, but an append-only
same-round projection can still prove the positive prefix already serialized.

| Temporal class | Current examples | Required interpretation |
| --- | --- | --- |
| `current_configuration` | A mapped Home configuration check | Requires `forced_serialization`; it says nothing about a later input. |
| `round_invariant` | Workshop preset, equipped Guardians, selected Bot preset, and equipped Modules | Once exact mapping and round binding prove the value belonged to the round, it applies to the whole round. Bot progression or medal-funded upgrades are separate point-in-time facts. |
| `point_in_time` | Cards preset and Cards at an active attachment | Describes only the acquisition boundary. Different later evidence may represent a legal change rather than a contradiction. |
| `monotonic_round_prefix` | Ordered saved Perk picks and their saved waves | Every published pick is historical positive evidence. A passive checkpoint cannot prove that no later pick exists. |
| `terminal_final` | Causally attached Battle History / More Stats fields | Requires a bound natural terminal and a valid append or capped rollover. |
| `boundary_clear` | Inactive, cleared terminal Perk fields | Closes the active checkpoint window; it is not the final Perk inventory and cannot erase the newest complete active prefix. |

Every temporal claim requires the exact mapping plus compatible target
generation, activity scope, and, where applicable, round identity. Differing
`round_invariant` values for one round invalidate that claim instead of using
last-write-wins. Point-in-time Card observations retain their individual
boundaries. Perk checkpoints must be identical or strict prefix extensions;
regression or reordering preserves already proved historical picks but makes
current/final completeness unavailable. An authoritative UI/save
contradiction still fails closed. UI owns applying and verifying a change, and
save-authoritative current state after that change requires another forced
serialization.

Running-attachment configuration projection now produces typed facts under a
private exact process/target-generation/source-scope/round binding. Activity
Continuity withholds those facts until it has atomically persisted the final
activity scope; a replacement scope is rebound only by that continuity result.
App revalidates the current target, generation, process session, active battle,
and final scope before dispatch. Redacted actual-loadout provenance retains the
exact mapping, target-generation fingerprint, scope fingerprint, round
fingerprint, capture boundary, and temporal class without retaining the raw
target or scope identifier.

No Strategy treats Workshop preset, equipped Guardians, selected Bot preset,
and equipped Modules as sticky round-invariant facts. Identical observations
merge, but a differing complete save value or authoritative complete preset UI
observation marks that field unavailable for the round; a later value cannot
restore it. Partial Guardian or Module UI evidence cannot replace a complete
save claim. Cards retain their point-in-time boundary and may legally differ at
a later capture. Bot progression is neither projected into nor compared with
the selected Bot preset. The resulting facts populate the existing
`observed_run_configuration` actual loadout, never configured intent.

The same typed attachment object supplies Tournament's existing in-battle
Workshop evidence seam. Its one-use consumer accepts only a round-invariant
fact and rechecks process, target generation, activity scope, and active-battle
ownership at consumption time. A valid `Tourney` match therefore closes the
Home-only deferral without a game-Home route, another Android lifecycle action,
or another save read; missing, mismatched, or rebound evidence preserves the
established explicit deferral.

The normal Perk monitor consumes the same forced-attachment bundle, scheduled
`passive_stable_read` bundles, and the lifecycle-issued natural terminal
bundle. Its scheduler is active independently of audit opt-in; when audit is
enabled, the same immutable object is queued for audit projection rather than
pulled again. The monitor owns no ADB read, acquisition or synchronization
lock, lifecycle action, or audit schedule; App serializes cross-thread domain
calls and the separate passive scheduler owns cadence. The optional audit
collector remains neither an acquisition service nor an authority source.

Each complete active Perk projection is bound to the exact process session,
activity scope, target generation, mapping, and active-round identity. An
identical later checkpoint or a strict prefix extension advances evidence;
capture-time regression, prefix regression, reordering, identity change,
mapping change, or conflicting exhaustion evidence makes final completeness
unavailable for that round. `saveRevision` remains diagnostic and is never a
freshness signal. Acquisition or projection failure retains the already proved
positive prefix but restores the terminal UI fallback until a valid active
checkpoint recovers it.

Stable high-confidence `View Perks` observations are persisted with their
activity scope, wave, capture time, and source fingerprint, then bound to the
exact active identity by the monitor. Game Over may omit Perks-panel navigation
only when a nonempty complete active checkpoint was captured after that
exhaustion observation, its saved wave includes the exhaustion wave, and a
later matching natural Game Over bundle contains the inactive cleared Perk
projection. The clear closes the checkpoint window; it never becomes final
inventory or proves absence. Every missing, empty, malformed, lagging,
conflicting, unbound, or terminal-only case takes the established Perks UI
route. Compact Game Stats, More Stats fallback, Wait/Retry/Home, and all other
UI action authority remain unchanged.

## Authority and fallback

| Situation | Required behavior |
| --- | --- |
| Unknown exact version | Decode only safe identity metadata; use UI for every check. |
| Exact version but changed structure | Reject all mapped values; use UI for every check. |
| Candidate mapping, check not explicitly validated | Report comparison results and run the existing UI check. |
| Explicitly validated check, complete exact match, and verified serialization boundary | The caller may accept save evidence for that check unless an audit is due. |
| Explicitly validated check, complete exact mismatch, and globally trusted snapshot | Queue only that check's existing guarded UI verification/repair; preserve unrelated accepted decisions. |
| No verified serialization boundary for a current-configuration claim | Treat the pull as potentially stale and use the existing UI check. Positive facts with a separately declared lag-tolerant temporal class remain governed by that class. |
| Missing, incomplete, stale, unsupported, or forced-audit value | Use the existing UI check for that setting without treating it as a trusted mismatch. |
| UI automation changes a setting | Verify the result in the UI, record UI provenance, preserve unrelated carry, and do not treat the pre-action save as confirmation. |
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

## Acquisition and privacy

The local reader never modifies the input file. A device pull reads the default
operator-confirmed path
`/sdcard/Android/data/com.TechTreeGames.TheTower/files/playerInfo.dat` through
ADB and accepts a payload only after two consecutive reads are byte-identical.
Decode then applies compressed and decompressed size limits, checks gzip
integrity, parses the NRBF root, selects the exact version mapping, and
validates its structural signature.

The normalized report deliberately omits `playerID`, `userName`, and every
unmapped raw field. Its runtime projection contains only version-allowlisted
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

For every released game version that changes either identity field:

1. Keep the previous mapping immutable and add a new exact mapping file under
   `config/player_save_versions/` with maturity `candidate`.
2. Capture a stable save through two identical reads. Confirm gzip/NRBF decode,
   root class, required fields, all array lengths, and the effective card-slot
   width before assigning semantic names.
3. Run the focused decoder and reconciliation tests. Add fixtures synthesized
   from the structural contract; do not commit a real player save.
4. Compare each mapped value with authoritative UI evidence from the same
   profile and game version. Record agreements, differences, and fields that
   remain unmapped.
5. Add a check to the mapping's per-check validation allowlist only after every
   value that check can suppress has passed cross-channel validation. Promote
   the whole exact mapping to `validated` only when all observed complete
   checks meet that standard. A partial mapping leaves unsupported or
   incomplete checks explicitly UI-required.
6. Run a forced UI audit after promotion and retain periodic or release-boundary
   audits. Any later discrepancy demotes the mapping or the affected field and
   immediately restores UI navigation.

Runtime adoption should begin in audit-only mode: pull once at a safe preflight
boundary, reconcile the snapshot with the resolved profile, and compare it
with the normal UI inventory. Only a later validated mapping may turn an exact
per-check match into a navigation shortcut.
