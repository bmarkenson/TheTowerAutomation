# Player Save Import

`core/player_save.py` decodes The Tower's `playerInfo.dat` as an independent,
read-only view of persistent profile configuration. It is intentionally not a
replacement for action verification or a universal parser for unknown game
versions.

## Current status

The first mapping is `data-9-game-1073`, selected by the exact save fields
`dataVersion: 9` and `versionNumber: 1073`. Its maturity is `candidate`. It was
derived from the repository-root operator sample and recognizes the sample's
five 28-slot card-preset records, including the distinction between its stored
base slot count and the effective preset width.

Candidate status is fail-closed: every reconciliation decision still names the
existing UI check as required. The mapping can expose agreements and likely
drift now, but it cannot suppress navigation until it is validated against UI
evidence from the same game version.

The currently mapped profile checks are:

- active Cards, Workshop, and Bots presets;
- Free Upgrade locks;
- Guardian chips and Target Priority order;
- Auto Pick Perks, bans, first choice, and the mapped Auto Pick priority
  prefix;
- Ultimate Weapon primary toggles, Poison Swamp Stun, and Spotlight Missiles.

Card recharge modes, Damage Slider, Modules, and Orb Distance are explicitly
unmapped and always use the UI. More fields can be added only with semantic and
polarity calibration, not merely because a plausible raw field exists.

## Authority and fallback

| Situation | Required behavior |
| --- | --- |
| Unknown exact version | Decode only safe identity metadata; use UI for every check. |
| Exact version but changed structure | Reject all mapped values; use UI for every check. |
| Candidate mapping | Report comparison results and run the complete UI audit. |
| Validated mapping and exact value match | The caller may accept save evidence for that check unless an audit is due. |
| Missing, incomplete, stale, or mismatched value | Use the existing UI check for that setting. |
| UI automation changes a setting | Verify the result in the UI; do not treat the pre-action save as confirmation. |

The save is suitable for persistent state that the game has finished writing.
Fresh UI evidence remains authoritative for the current screen, temporary
state, transition completion, controls that are not mapped, and the result of
an input. Runtime integration must preserve every current UI checker rather
than deleting it.

## Acquisition and privacy

The local reader never modifies the input file. A device pull reads the default
path through ADB and accepts a payload only after two consecutive reads are
byte-identical. Decode then applies compressed and decompressed size limits,
checks gzip integrity, parses the NRBF root, selects the exact version mapping,
and validates its structural signature.

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
validated mapping. `--output` writes only the normalized JSON report, never the
raw save.

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
5. Promote only that exact mapping to `validated` after every value it can
   suppress has passed cross-channel validation. A partial mapping leaves its
   unsupported checks explicitly unmapped.
6. Run a forced UI audit after promotion and retain periodic or release-boundary
   audits. Any later discrepancy demotes the mapping or the affected field and
   immediately restores UI navigation.

Runtime adoption should begin in audit-only mode: pull once at a safe preflight
boundary, reconcile the snapshot with the resolved profile, and compare it
with the normal UI inventory. Only a later validated mapping may turn an exact
per-check match into a navigation shortcut.
