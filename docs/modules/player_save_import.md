# Player Save Import

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
three-lock Farm set; equipped Guardians; Demon Mode/Nuke recharge behavior;
Auto Pick enabled for the required value `true`; complete Target Priority ID
and ordering semantics; Poison Swamp Stun in both calibrated polarities; all
nine Ultimate Weapon primaries for the all-on requirement; and Spotlight
Missiles for the on requirement. This adoption reused the prior calibration
evidence atomically; it did not require or perform another live campaign.

The monolithic Ultimate Weapon check is deliberately not allowlisted. Poison
Swamp Stun, the all-primary-on aggregate, and Spotlight-Missiles-on are separate
normalized components with independent fallback. A mixed/off primary request
or Spotlight Missiles off still opens the existing UI route. Free Upgrade lock
authority is likewise limited to exactly Shockwave Size, Bounce Shot Targets,
and Bounce Shot Range: any other requested set, additional set bit, changed
shape, unknown index, or non-boolean value restores the complete lock UI path.

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
- Auto Pick Perks, bans, first choice, and the mapped Auto Pick priority
  prefix (the visible ranked block is distinct from the save's unranked tail);
- Ultimate Weapon primary toggles, Poison Swamp Stun, and Spotlight Missiles;
- current Legend Tournament identity and version-derived Battle Conditions.

Card recharge modes are now mapped and validated. Damage Slider, Modules, and
Orb Distance remain explicitly unmapped and always use the UI. More fields can
be added only with semantic and polarity calibration, not merely because a
plausible raw field exists.

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

One snapshot reconciles every requested check. A complete allowlisted match can
omit only that redundant UI observation. A mismatch, unsupported requirement,
unknown version or ID, changed shape, unequal/pull/decode failure, or forced
audit restores the existing per-check UI implementation after the exact target
and Home boundary have been safely restored. A failure to restore foreground,
target ownership, control authority, or the Home boundary blocks every later
input instead. `force_ui` performs no save lifecycle; `comparison_audit`
collects normalized comparison evidence but keeps UI authoritative.

The first actual UI repair invalidates every remaining pre-action save decision.
The repair is still verified in the UI, and later checks use UI evidence. A
read-only inspection does not invalidate unrelated decisions, while an actual
UI contradiction fails closed with its provenance. The V1073-RUNTIME-013
collector remains a separate optional observation stream and supplies no Home
preflight authority.

Accepted session-only values are single-use evidence for the exact next
runtime-owned `NEW_BATTLE` launch and its first stable `RUNNING` boundary. The
binding covers Auto Pick enabled `true`, exact complete Target Priority order,
all nine primaries on, Spotlight Missiles on, Poison Swamp Stun, and exact Home
sections needed by the later consistency check. Restart, attachment, Retry
without this Home preflight, strategy/configuration/target change, manual or
ambiguous launch, WAIT/Pause/Stop, repair, or an unrelated later battle rejects
the complete carry. Save evidence never authorizes a tap, repair, launch,
lifecycle transition, attachment, terminal binding, dispatch, or strategy
action.

Snapshot schema 2 contains the repository-local save-first runtime foundation;
its runtime projection is schema 2. For the exact version-1073 mapping it
publishes capture metadata, `saveRevision`, `roundActiveBool`, `currentWave`,
the active identity tuple, and independent normalized Perk and Battle History
tail components. Perk ID `0` is `max_health` (Max Health). Perks are emitted
only when every ordered pick, count, and level agrees. The 50-entry level
array defines numeric storage capacity; it does not prove that all 50 indices
are live Perk identities. Version 1073 currently has 33 cross-channel-mapped
semantic IDs, including all six configured Farm bans. The other 17 positions
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

The active-round/terminal projection foundation does not itself bind a process,
suppress terminal navigation, or build/persist a normal battle record. The
configuration coordinator above is a separate exact-Home consumer; the
V1073-RUNTIME-013 sidecar separately polls and retains only audit state. A
semantic terminal failure publishes an explicit UI fallback without exposing a
partial completed entry. A malformed newest entry publishes neither structural
nor semantic tail evidence. The authoritative ownership and later slice
boundaries are in
[`runtime.md`](../architecture/runtime.md#save-first-active-round-and-terminal-evidence).

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

This proves a natural-boundary candidate relationship; it does not implement
polling, same-round retention, history attachment, record construction, or UI
suppression. In particular, the terminal-only replay remains `unbound` under
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

### Versioned audit matrix: `data-9-game-1073` / revision 2

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
| `V1073-RAW-001` raw-field disposition manifest | Exact root field names; no raw values | **Structural.** Root identity, selected array dimensions, and the current redacted summary are known. | Classify every raw field as structural, automation-gating, profile observation, private, ignored-with-reason, or unknown. Until complete, unclassified fields are unpublished. |
| `V1073-CFG-001` Cards preset | `presetName`, `currentPreset` | **Shortcut-ready.** UI slot 2/1/2 caused raw `1/0/1`; restoration and unrelated controls were verified. | Audit runtime acquisition, then retain scheduled UI samples. |
| `V1073-CFG-002` card recharge modes | `demonModeAutomateToggle`, `nukeAutomateToggle` | **Shortcut-ready.** Both booleans were independently flipped and restored; `true` means auto-reactivate. | Audit runtime acquisition, then retain scheduled UI samples. |
| `V1073-CFG-003` Workshop preset | `workshopPresetName`, `currentWorkshopPreset` | **Shortcut-ready.** Exact selected-name/index agreement at a verified boundary; causality is not required for this non-polarity claim. | Do not manufacture a switch; force UI again on mapping/version audit. |
| `V1073-CFG-004` Bots preset | `botPresetName`, `currentBotPreset` | **Shortcut-ready.** Exact selected-name/index agreement at a verified boundary. | Never spend medals for causality; validate future values through naturally selected presets. |
| `V1073-CFG-005` First Perk Choice | `firstPerkIndex`, versioned Perk IDs | **Shortcut-ready** for the mapped IDs. It is an independent profile requirement, not the first Auto Pick row. | Perk-capable Farm profiles currently require `perk_wave_requirement`; Tournament declares no Perk requirement. Extend only from authoritative visible evidence; any unknown selected ID keeps the whole check in UI. |
| `V1073-CFG-006` Ban Perks | `bannedPerksIndex`, versioned Perk IDs | **Shortcut-ready** for a complete mapped selected set. | Validate each newly encountered ID; any unknown selected ID keeps the whole check in UI. |
| `V1073-CFG-007` Guardian chips | `guardianChipSlot`, `guardianSlotsUnlocked`, versioned Guardian IDs | **Shortcut-ready** for the mapped Farm/Tournament chips. | Extend through read-only equipped evidence; never equip merely to identify an ID. |
| `V1073-CFG-008` Auto Pick enabled | `autoPickPerk` | **Shortcut-ready, value-scoped** for exact boolean `true`. | The current required enabled state may skip UI. A false requirement, missing field, non-boolean, or future unsupported semantic remains UI-required. |
| `V1073-CFG-009` ranked Auto Pick order | `autoPickOrder`, versioned Perk IDs | **Shortcut-ready.** The exact 34-entry structure contains 18 visible ranked entries, 16 unranked inventory-tail slots, all 33 mapped IDs exactly once, and one tail sentinel. Only the mapped ranked block is published. | A configured list may be a shorter required prefix. Unknown IDs, duplicates, changed shape/membership, or an unresolved semantic value restore UI; the unranked tail is never compared as priority. |
| `V1073-CFG-010` exact Farm Free Upgrade locks | Three `*LockedFreeUpgrades` arrays | **Shortcut-ready, set-scoped** only for Shockwave Size, Bounce Shot Targets, and Bounce Shot Range with exact boolean shapes. | Any different request, extra/missing set bit, unknown index, changed length, or non-boolean restores the complete UI lock path. No claim is made about other possible lock sets. |
| `V1073-CFG-011` Target Priority | `targetPriorityList`, complete versioned ten-ID mapping | **Shortcut-ready** for an exact complete enforced order: ten unique known IDs with complete membership exactly once. | `enforce` requires full ordered equality; `preserve` creates no assertion. Observe the distinct Farm T18 full order naturally as confirmation of generic sequence serialization; testing all permutations is neither required nor planned. |
| `V1073-CFG-012` monolithic Ultimate Weapon controls | Combined primary/detail fields | **Structural only.** The aggregate is intentionally not allowlisted because its components have different value scopes. | UI remains available for every unsupported component/value; use the independently failing rows below. |
| `V1073-CFG-012A` Poison Swamp Stun | `poisonSwampStunOff` plus exact unlocked structure | **Shortcut-ready for both calibrated polarities.** Raw `false` means on; raw `true` means off. | Require an exact boolean and unlocked Poison Swamp. Either current on/off requirement may skip UI; malformed or changed structure restores UI. |
| `V1073-CFG-012B` all nine Ultimate Weapon primaries on | Exact nine-element `ultimateWeaponUnlocked` and `ultimateWeaponOn` arrays | **Shortcut-ready, value-scoped** only when all nine exact booleans are unlocked and on. | Any subset, mixed/off request, false value, non-boolean, name/length change, or locked weapon restores UI. Validate each individual off/on index before supporting future mixed requirements. |
| `V1073-CFG-012C` Spotlight Missiles on | `spotlightSmartMissilesOff` plus exact unlocked Spotlight structure | **Shortcut-ready, value-scoped** only for raw exact `false` / required on. | Off, raw true, malformed, missing, locked, or changed structure remains UI-required until one reversible off transition and restoration are reviewed. |
| `V1073-CFG-013` Legend Tournament conditions | Tournament identity fields plus exact-version generator | **Shortcut-ready.** Seventeen consecutive event sets agreed with historical/live UI evidence. | Retain Heat/Overheat audits; validate every additional league and new exact game version independently. |
| `V1073-CFG-014` Modules | Equipped/inventory records | **Structural.** Array dimensions are known; semantics are deliberately unpublished. | Map slot order, stable identity, Primary/Assist roles, rarity, level ownership, and substats through naturally occurring read-only comparisons; UI remains required. |
| `V1073-CFG-015` Damage Slider | No accepted field | **Structural.** The absence of an accepted normalized source is explicit. | In an explicitly authorized test battle, correlate at least two values and restoration, percentage encoding, and save timing; UI remains required. |
| `V1073-CFG-016` Orb Distance | Candidate distance/preset fields not accepted | **Structural.** Candidate fields are deliberately unpublished. | In an explicitly authorized battle, cycle known Extra/Workshop presets, prove units and selected-preset semantics, and restore the original pair; UI remains required. |
| `V1073-PROFILE-001` card ownership, levels, and five 28-slot decks | `cardUnlocked`, `cardLevel`, `slotPresetCardInt`, `slotPresetCardAssignedBool`, `slotsUnlocked` | **Structural.** Dimensions and base/effective width distinction are known. | Build the complete card-ID map, compare ownership/levels, and inventory every preset membership before publication. |
| `V1073-PROFILE-002` Workshop and Enhancements | Attack/Defense/Utility workshop and enhancement arrays | **Structural.** Dimensions only. | Map every index; verify zero, nonzero, maxed, and unlocked states; account for special upgrades with different level semantics. |
| `V1073-PROFILE-003` Research and Labs | `researchLevel` plus candidate queue/timing fields | **Structural.** Dimensions only. | Map research IDs/levels; give active lab, duration, and completion timestamps independent volatile-freshness rules. |
| `V1073-PROFILE-004` Ultimate Weapon progression | Unlock/level arrays plus candidate cooldown/quantity fields | **Structural.** Dimensions and candidate tuple layout only. | Prove weapon order and every three-level tuple before publishing names or levels. |
| `V1073-PROFILE-005` Guardian and Bot progression | Unlock/level arrays and candidate Bot fields | **Structural.** Dimensions and selected preset/chip subset only. | Map every stable ID, slot count, level tuple, and preset field through read-only evidence; no cost-bearing calibration. |
| `V1073-PROFILE-006` Module inventory/equipped loadout | Equipped and module-record structures | **Structural.** Dimensions only. | Decode stable IDs, uniqueness, slot/role, rarity, levels, ancestral stars, and substats across naturally occurring loadouts. |
| `V1073-RUNTIME-001` guarded save-first Home acquisition | Proven Android-Home flush, two identical exact-target reads, exact decoder, stable restored `NEW_BATTLE` | **Shortcut-ready and implemented.** One runtime/preflight/configuration/target generation owns the lifecycle workflow and retains only normalized redacted provenance. | `save_first` uses this path; acquisition/decode uncertainty safely restored to Home runs UI, while restoration/ownership/control/boundary uncertainty blocks input. The optional audit collector is not an authority source. |
| `V1073-RUNTIME-002` atomic per-check suppression and exact-next-battle carry | Resolved configuration fingerprint, per-component decisions, runtime-owned launch, first stable `RUNNING` | **Shortcut-ready and implemented** for all currently allowlisted Home/session components together. | `force_ui` preserves complete UI behavior; `comparison_audit` collects normalized comparison evidence while UI remains authoritative. Any repair invalidates remaining snapshot decisions; discontinuity rejects all carry. Future comparisons never self-promote a manifest. |
| `V1073-RUNTIME-003` active round identity | `(versionNumber, currentTier, roundsStartedThisTier[currentTier], roundSeed)` | **Causal.** A known Home boundary preceded the first stable Tier 22 active projection with a new per-tier counter and round seed; subsequent stable revisions retained that exact identity through wave 710. No finer wall-clock latency is claimed. | The natural boundary semantics are proven. `V1073-RUNTIME-013` scopes audit receipts to the tuple and rejects stale/different-round projections without binding a process; any later attachment consumer must preserve the independent current-process terminal-binding rule. |
| `V1073-RUNTIME-004` approximately five-minute save freshness | `saveRevision`, capture time, stable source hash, active identity/wave | **Structural.** Multiple ordinary-foreground stable revisions advanced under the same Tier 22 identity through wave 710, corroborating periodic usable writes without retaining exact timestamps. The whole row is not promoted because UI-to-save lag, jitter, unchanged intervals, write-collision behavior, and a runtime staleness threshold were not measured. | The default-300-second observation-only polling cadence is implemented with 30–3600-second bounds. Pause/background behavior and tighter freshness characterization may be measured during ordinary future use; no receipt timing authorizes navigation or claims an exact write time. |
| `V1073-RUNTIME-005` in-battle Perk inventory | `perkLevel[50]`, `perksPickedCount`, ordered `PerkPick(wave, perk)` list, versioned Perk IDs | **Shortcut-ready** for a complete exact-version snapshot. The final Tier 22 active projection contained 15 internally exact picks; all mapped picks, levels, and order agreed with the terminal UI's 11 collapsed rows. During the first enabled Tier 19 sequence, seven additional IDs were cross-channel calibrated from stable pick waves/levels and the same-round UI timeline; the repaired decoder then accepted all 56 active picks spanning 27 semantic keys. Synthetic unknown-ID, shape, count, level, and non-monotonic-order inconsistencies still publish no snapshot. | The normalized decoder/reconciler claim is complete. `V1073-RUNTIME-013` records session-local prefix deltas and fails non-prefix progress closed. If a structurally valid but unmapped ID appears, the collector now tries the exact same-wave UI/save resolver described below; only a unique allowlisted assignment restores the whole projection. Ambiguity or inconsistency still publishes no inventory and keeps UI evidence. The privacy-safe static calibration is retained in `test/fixtures/player_save_perk_id_calibration_v1073.json`. |
| `V1073-RUNTIME-006` post-run Perk clearing and same-round retention | Active Perk snapshots followed by the post-run zero/empty fields | **Causal.** The last complete Tier 22 active snapshot agreed with the terminal Perks inventory, and the immediate stable post-death save was inactive with cleared Perk fields. This proves final-snapshot completeness and the clearing boundary without an intermediate raw save. | `V1073-RUNTIME-013` retains the newest complete same-identity audit checkpoint and records the first cleared projection within its new process-local session. No normal terminal consumer or navigation shortcut is promoted. |
| `V1073-RUNTIME-007` structural tail identity and complete `BattleHistory` More Stats projection | Source-ordered capped `battleHistory[<=30]`; exact newest 148-field `BattleHistoryEntry` shape | **Shortcut-ready** for an exact-version entry whose cause is mapped. Retained saves prove mixed UTC/local DateTime kinds and capped rollover. The prior 21 UI-captured battles plus the Tier 22 terminal confirm the complete ordered 144-row projection within UI precision; malformed entries and unknown semantic causes fail closed independently. `adGemsThisRound` supplies Ad Gems. | The structural identity and semantic decoder/reconciler are complete. Trust source order rather than cross-kind ticks; keep raw entries, arbitrary fields, and account data unpublished. Runtime tail attachment and normal record construction remain later work. |
| `V1073-RUNTIME-008` Game Over history serialization timing | Pre-run history tail, Game Over observation, post-run stable save | **Causal.** The known pre-battle tail changed in the immediate stable post-death save while the natural Tier 22 terminal was preserved, proving publication at the Game Over boundary without an exact timestamp. The first enabled Tier 19 run independently recorded clearing and tail publication before normal Retry. | Immediate observation requests at Game Over and Tournament terminal labels are implemented. `b137ea4` adds guarded same-session direct-Retry baseline rollover; its next ordinary end-to-end receipt confirmation remains pending. Receipt timing remains a latency bound, and terminal capture stays on the UI path until attachment is implemented and proven. |
| `V1073-RUNTIME-009` terminal history-tail attachment | Pre-boundary structural tail fingerprint plus newest post-boundary entry tier/time/wave | **Causal** for a candidate only. The pre-battle baseline changed at capped rollover to a newest Tier 22, wave 751, Boss entry whose complete semantic projection agreed with terminal tier/time/wave evidence. This proves tail causality, not runtime attachment. | No consumer attaches the candidate. `V1073-RUNTIME-013` records it only after a same-session pre-boundary tail and active identity, including capacity-30 rollover, while ambiguity/no-change/malformed evidence fails closed. A later attachment consumer must not let a terminal-only restart inherit Strategy or process-local tracker context. |
| `V1073-RUNTIME-010` complete `killedBy` enum | `BattleHistoryEntry.killedBy` | **Cross-channel** only for `1=Fast`, `2=Tank`, `3=Boss`, `6=Vampire`, `8=Scatter`, and `99=Surrender`; Tier 22 reconfirmed `3=Boss`, but the whole enum claim remains incomplete. Surrender identifies only the terminal cause, not its initiator. | Extend the allowlist only from naturally observed values. Any future unknown value preserves structural tail evidence but makes the semantic entry unavailable and requires UI evidence; this fail-closed extension does not block `V1073-RUNTIME-013`, and `Enemy N` is never synthesized. |
| `V1073-RUNTIME-011` passive base/ad coin split augmentation | Compact Game Stats screenshot/OCR; `battleHistory.coinsEarned` total | **Cross-channel.** The Tier 22 compact panel showed `28.56T` base plus `14.28T` ad equaling the `42.84T` total. `battleHistory` still contains only total coins, so the whole row remains a UI-supplied optional augmentation rather than a save claim. | Keep one passive compact capture when available. A missing or invalid split remains explicitly optional and never invalidates otherwise authoritative save-derived stats or blocks `V1073-RUNTIME-013`. |
| `V1073-RUNTIME-012` forced terminal UI audit/fallback | Existing Game Stats, Perks, clipboard/OCR More Stats, and verified terminal controls | **Shortcut-ready** as maintained fallback behavior, not as a save shortcut. | Keep the complete path forced on audit, unknown version/shape/ID, incomplete final Perks, history-binding failure, or save-record mismatch. Wait/Retry/Home and mutation/transition confirmation always remain verified UI actions. |
| `V1073-RUNTIME-013` natural-boundary audit collector | Stable privacy-safe runtime projections plus passive boundary observations | **Structural.** The default-disabled collector, append-only schema, exact-target/session guards, core state machine, and privacy/nonblocking regressions are implemented. The first enabled ordinary Tier 19 run recorded exact Home, first active identity, revisions `46418`–`46465`, terminal clearing, and the wave-5182 Tank tail candidate while the complete UI pipeline remained authoritative and unchanged. Its direct Retry exposed fail-closed old-identity retention and seven missing Perk IDs; `b137ea4` repairs both. The deployed fresh session then accepted revision `46521`, counter 232, wave 290, and a complete mapped two-pick checkpoint without inheriting the terminal-only process's unavailable baseline. | The collector emits audit candidates only: no attachment, record construction, Strategy fact, Perks-navigation decision, input, lifecycle/dispatch change, or UI suppression. Its new Perk-ID resolver consumes only current-process exact-wave timeline batches, cancels statically mapped picks, and accepts a session overlay only when constraint propagation leaves one assignment. It writes an allowlisted calibration receipt first, retains the overlay across ordinary same-target Retries, and clears it on target-generation or process change. Ambiguity, visibility gaps, interval aggregates, low confidence, unknown semantic families, conflicts, or receipt failure remain fail-closed. No special battle is required. Upgrade and survival save components remain unavailable; strict confirmed visual metadata is independent and approximate. |
| `V1073-RUNTIME-014` in-battle upgrade levels and gold-box state | `upgradeLevel[20]`, `upgradeDefenseLevel[20]`, `upgradeUtilityLevel[20]` plus the three Workshop-level arrays | **Structural.** Array shapes and current-versus-Workshop deltas are observed, but the complete index, cap, and special-level semantics are not retained validation evidence. | Create a versioned index/cap manifest and validate non-maxed, round-purchased, Workshop-maxed, locked, and special upgrades against canonical UI evidence. Publish current level, baseline, delta, and `maxed` only as one independently failing component. Never infer Max from magnitude alone. |
| `V1073-RUNTIME-015` survival-ability checkpoint state | Demon Mode, Nuke, and Second Wind `*UsedThisRound`, use-count, cooldown, `*WavesUntilRefresh`, active/effect-timeout, and timer fields | **Structural.** The fields exist in an active round and clear after the round, but boolean polarity, sentinel values, units, exact-wave relationships, and write timing are not calibrated. | At natural activations, retain stable before/during/rearmed/terminal snapshots and matching visual events. Prove each ability independently, including auto versus manual behavior and multiple activations. Publish counts and state first; publish an exact activation wave only where a causal timer formula is proven, otherwise a save-wave interval. |
| `V1073-RUNTIME-016` save-checkpoint and visual-tail event merge | Same-round stable revisions, normalized survival checkpoints, passive visual activation events, and terminal Battle History counts | **Structural.** Source precedence and fail-closed merge policy are specified; no cache or merger exists. | Merge monotonically by guarded round identity. Count deltas define event intervals; matching visual transitions may refine them. Retain confirmed visual events after the last stable active save through Game Over and reconcile against terminal counts. Never double count, discard an unexplained count, or synthesize an exact wave. Conflict or missing binding forces the full UI audit. |
| `V1073-RUNTIME-017` active-round battle tallies | Version-allowlisted `*ThisRound`/`*ThisWave` counters and current round totals | **Structural.** The root contains broad live damage, enemy, currency, skip, free-upgrade, survival, and subsystem tallies; only their completed-history counterparts are semantically normalized today. | Prioritize fields that replace current OCR/navigation or strengthen terminal reconciliation. Validate monotonicity, units, reset/clear timing, exceptional decreases, and correspondence to completed-history rows. Publish separate components and provenance; stale tallies remain observational and cannot authorize an input. |
| `V1073-RUNTIME-018` transient control and cooldown candidates | `gameSpeedMemory`, buy multipliers, candidate Damage Slider/Orb Distance fields, Card activity, and UW/Bot/Guardian cooldown arrays | **Structural.** Plausible fields exist but are deliberately unpublished and may lag the visible game by a complete save interval. | Rank by current observation cost, then calibrate each claim separately across changed/restored values and stable writes. Current-state enforcement and post-action verification remain visual unless the use case explicitly tolerates checkpoint staleness. |
| `V1073-TOURNEY-001` Tournament condition profile/history coverage | Exact-version generator, event identity fields, and Heat/Overheat UI | **Shortcut-ready** for Legend condition identity only. | Complete UI inventory, effective descriptions, lower leagues, and unknown-condition preservation in the separate [Tournament condition plan](../backlog/runtime-and-validation.md#tournament-battle-condition-evidence). |

The complete currently eligible configuration set is adopted atomically by
`V1073-RUNTIME-001`/`002`; this is not a promotion of unrelated profile or
active-round rows. `V1073-RUNTIME-013` remains observation-only polling and a
session-local audit cache, and its first ordinary Home-to-terminal deployment
pass is complete. The deployed direct-Retry repair still awaits one passive
ordinary rollover receipt. It does not attach a runtime tail or construct a
normal save-derived record. Foreground freshness extensions, active upgrades,
survival timing and repeated-event merging, live tallies, and future unknown
`killedBy` values remain independently fail-closed work rather than blockers
for configuration preflight.

Profile groups broaden the diagnostic view but cannot influence automation
until their own row is Shortcut-ready. Every published group carries mapping
ID, source fields, capture time, and validation status.

### Runtime adoption and future calibration

The guarded acquisition, atomic allowlisted adoption, per-check fallback,
mutation invalidation, exact launch carry, and `save_first` / `force_ui` /
`comparison_audit` policy are implemented. Prior version-1073 evidence was
reused; no duplicate live-preflight campaign was performed. This code-only
validation is not deployment or live validation. Deployment and observation at
the first ordinary production boundary remain coordinator work.

Future evidence comes from naturally occurring UI fallbacks or explicit/
periodic comparison audits. A candidate remains privacy-safe and observational
until a reviewed mapping/documentation change promotes it; no receipt or
runtime comparison edits its own authority manifest.

1. **Target Priority:** compare the distinct Farm T18 full order during an
   ordinary future T18 start. A second nontrivial order confirms generic
   sequence serialization; do not attempt all ten-factorial permutations.
2. **Ultimate Weapons:** for future mixed primaries, change and restore one
   weapon at a time and validate that index's off/on polarity. Validate
   Spotlight Missiles off through one explicitly authorized reversible
   transition. Retain normalized before/change/restore comparisons; never
   enumerate every boolean combination.
3. **Orb Distance:** prefer ordinary Farm (`30.00m`, Extra `30.00m`, Workshop
   `39.00m`) → Tournament (`98.38m`, Extra `87.16m`, Workshop `80.37m`) → Farm
   transitions. Pair authoritative UI evidence with guarded stable saves to
   establish candidate fields, units/rounding, selected-preset versus derived
   semantics, serialization timing, Home versus active behavior, and
   restoration. Only if natural transitions are insufficient may a later
   coordinator authorize one agent-owned bounded calibration.
4. **Unknown versions, shapes, IDs, and values:** continue through UI. The
   existing exact-evidence resolver may attempt only a unique fail-closed
   mapping; ambiguity or conflict remains UI-required, and the observation-only
   collector never becomes preflight authority.
5. Add the versioned raw-field disposition manifest and validation metadata for
   other profile groups. Unknown or unclassified fields remain unpublished.

## Authority and fallback

| Situation | Required behavior |
| --- | --- |
| Unknown exact version | Decode only safe identity metadata; use UI for every check. |
| Exact version but changed structure | Reject all mapped values; use UI for every check. |
| Candidate mapping, check not explicitly validated | Report comparison results and run the existing UI check. |
| Explicitly validated check, complete exact match, and verified serialization boundary | The caller may accept save evidence for that check unless an audit is due. |
| No verified serialization boundary | Treat the pull as potentially stale and use the existing UI check. |
| Missing, incomplete, stale, or mismatched value | Use the existing UI check for that setting. |
| UI automation changes a setting | Verify the result in the UI; do not treat the pre-action save as confirmation. |

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
round, Perk, and completed-battle evidence; non-report history fields have
explicit dispositions and remain unpublished. It includes SHA-256 source and
canonical component fingerprints so observations can be correlated without
retaining the save. The operator-owned
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

After completing the live-runtime checklist in
[`../new_thread.md`](../new_thread.md), the same report can be built from the
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
