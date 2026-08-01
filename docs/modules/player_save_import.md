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
count and the effective preset width.

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

This is deliberately a partial validation, not a global promotion. The exact
mapping now marks Cards, Workshop, and Bots preset selection; First Perk; Ban
Perks; equipped Guardians; and Demon Mode/Nuke recharge behavior as validated
checks. Target Priority,
the complete set of possible Free Upgrade locks, Ultimate Weapon toggle
polarities, and the unranked Auto Pick tail still need same-version UI
calibration. Poison Swamp Stun polarity is confirmed, but the combined Ultimate
Weapon check remains UI-required until every value that check could suppress
has been calibrated.

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
- Ultimate Weapon primary toggles, Poison Swamp Stun, and Spotlight Missiles.

Card recharge modes are now mapped and validated. Damage Slider, Modules, and
Orb Distance remain explicitly unmapped and always use the UI. More fields can
be added only with semantic and polarity calibration, not merely because a
plausible raw field exists.

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

### Automation-gating matrix

`Shortcut-ready` below describes the decoder/reconciler. Runtime preflight has
not yet adopted the navigation shortcut.

| Normalized check | Version-1073 source | Current evidence | Remaining work before runtime adoption |
| --- | --- | --- | --- |
| Cards preset | `presetName`, `currentPreset` | Shortcut-ready; UI slot 2/1/2 caused raw `1/0/1`. | Audit the runtime acquisition path, then retain scheduled UI samples. |
| Card recharge modes | `demonModeAutomateToggle`, `nukeAutomateToggle` | Shortcut-ready; both booleans were independently flipped and restored. `true` means auto-reactivate. | Audit the runtime acquisition path, then retain scheduled UI samples. |
| Workshop preset | `workshopPresetName`, `currentWorkshopPreset` | Shortcut-ready from exact selected-name/index agreement. | Do not manufacture a switch; force UI again on mapping/version audit. |
| Bots preset | `botPresetName`, `currentBotPreset` | Shortcut-ready from exact selected-name/index agreement. | Never spend medals for causality; validate future values through naturally selected presets. |
| First Perk | `firstPerkIndex` plus `perk_ids` | Shortcut-ready for mapped IDs; an unknown ID fails closed. | Extend only when a new ID is authoritatively visible. |
| Ban Perks | `bannedPerksIndex` plus `perk_ids` | Shortcut-ready for a complete mapped selected set. | Validate any newly encountered ID; unknown selected IDs keep the whole check in UI. |
| Guardian chips | `guardianChipSlot`, `guardianSlotsUnlocked`, `guardian_chip_ids` | Shortcut-ready for the mapped Farm/Tournament chips; unknown IDs fail closed. | Extend through read-only equipped evidence; never equip merely to identify an ID. |
| Auto Pick enabled | `autoPickPerk` | Observed but not allowlisted. | Toggle off/on at a safe no-battle boundary, flush each state, restore, and prove polarity. |
| Auto Pick order | `autoPickOrder` plus `perk_ids` | Ranked prefix agrees, but the save contains an unranked tail and unknown IDs; evidence is deliberately incomplete. | Map every reachable ID and encode ranked-count/tail semantics so only the visible ranked block is compared. |
| Free Upgrade locks | Three `*LockedFreeUpgrades` arrays | The three Farm locks agree; the full index-to-upgrade map is not validated. | Either validate every supported index and polarity or narrow the mapping to proven indices and fail closed whenever another bit is set. |
| Target Priority | `targetPriorityList` plus `target_priority_ids` | Plausible ordered mapping, not allowlisted. | Compare a fresh in-battle list and use one reversible adjacent reorder/restore to prove index order and serialization. |
| Ultimate Weapons | `ultimateWeaponUnlocked`, `ultimateWeaponOn`, `poisonSwampStunOff`, `spotlightSmartMissilesOff` | Weapon order and current values agree; Poison Swamp Stun polarity is causal. The combined check remains unvalidated. | Prove all nine primary-toggle booleans and Spotlight Missiles independently, or split the combined check into smaller atomic evidence before allowlisting. |
| Modules | Module equipped/inventory records | Explicitly unmapped. | Map slot order, stable module identity, Primary/Assist roles, rarity, level ownership, and substats through read-only UI comparisons; do not swap modules solely for calibration. |
| Damage Slider | Field not identified | Explicitly unmapped. | In an explicitly authorized test battle, correlate at least two distinct values and restoration, including percentage encoding and save timing. |
| Orb Distance | Candidate distance/preset fields not accepted | Explicitly unmapped. | In an explicitly authorized battle, cycle known Extra/Workshop presets, prove units and selected-preset semantics, and restore the original pair. |

### Full-profile matrix

These groups broaden the profile view but cannot influence automation until
their own normalized evidence is validated. Each published group must carry
mapping ID, source fields, capture time, and validation status.

| Profile group | Candidate source | Required validation |
| --- | --- | --- |
| Card ownership, levels, and all five 28-slot decks | `cardUnlocked`, `cardLevel`, `slotPresetCardInt`, `slotPresetCardAssignedBool`, `slotsUnlocked` | Build the complete card-ID map, compare ownership/levels, inventory every preset membership, and distinguish base slots from the 28-entry stored width. |
| Workshop and Enhancements | Attack/Defense/Utility workshop and enhancement arrays | Map every index to its UI label, verify representative zero, nonzero, maxed, and unlocked states, and account for special upgrades that do not share ordinary level semantics. |
| Research and Labs | `researchLevel` plus candidate lab queue/timing fields | Map research IDs and levels first; treat active lab, duration, and completion timestamps as volatile observations with their own freshness rules. |
| Ultimate Weapon progression | unlock and level arrays plus candidate cooldown/quantity fields | Prove weapon order and the three-level tuple layout for all nine weapons before reporting names or levels. |
| Guardian and Bot progression | unlock/level arrays and candidate Bot fields | Map every stable ID, slot count, level tuple, and preset field through read-only UI evidence; keep cost-bearing changes outside calibration. |
| Module inventory and equipped loadout | equipped and module-record structures | Decode stable IDs, uniqueness, slot/role, rarity, levels, ancestral stars, and substats; cross-check multiple naturally occurring loadouts. |
| Tournament conditions | Field discovery pending | Follow the separate [Tournament condition plan](../backlog/runtime-and-validation.md#tournament-battle-condition-evidence); unknown conditions must remain lossless and nonblocking. |

### Runtime rollout sequence

1. Add the per-version field-disposition manifest and validation metadata for
   profile groups. Unknown or unclassified fields remain unpublished.
2. Integrate one audit-only snapshot acquisition at verified
   `HOME_SCREEN / NEW_BATTLE`: perform the proven short app-pause flush, resume
   to the same boundary, require two identical reads, then reconcile. Continue
   running every UI check and record only normalized agreement/disagreement.
3. Run one clean forced audit for each resolved Farm/Tournament configuration
   fingerprint. Any discrepancy demotes only the affected check and keeps its
   UI route active.
4. Enable navigation suppression one allowlisted check at a time. A complete,
   fresh exact match skips that check; mismatch, missing evidence, unknown
   version, changed shape, or forced audit runs the existing UI implementation.
5. The first UI repair invalidates the pre-action snapshot. Verify the repair
   visually and either finish the remaining checks through UI or perform a new
   bounded flush/pull; never use the old save to confirm an action.
6. Force audits on first use of a new exact mapping, after a discrepancy or
   repair, for the first use of a new configuration fingerprint, and on a
   configurable periodic cadence. The UI implementations remain maintained and
   tested even after most ordinary navigation is skipped.

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
unmapped raw field. It includes a SHA-256 source fingerprint so two observations
can be correlated without retaining the save. The operator-owned
`playerInfo.dat` must remain untracked and must never be copied into tests,
logs, commits, or retained runtime evidence.

## Inspection tool

Install the optional decoder into the project environment:

```bash
.venv/bin/python -m pip install -r requirements-save-import.txt
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
