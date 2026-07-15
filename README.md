# TheTower Automation

This repository drives automated gameplay loops for *The Tower: Idle Tower Defense*. Runtime entry point is `main.py`, which wires detection, missions, and strategies.

## Running with YAML strategies

Strategies can now be authored in YAML and loaded at runtime. The Blender upgrade loop has been ported to `config/strategies/blender.strategy.yaml`.

### Editing the Blender plan

The runtime YAML is generated from a compact source file so you don’t have to hand-edit the large rule set.

1. Update `config/strategies/blender.source.yaml` (ordered list of targets, settings).
2. Regenerate the expanded strategy:

   ```
   python tools/strategy_builders/blender.py
   ```

   (Use `--source` / `--output` flags to override paths if needed.)
3. Commit both the source and regenerated `config/strategies/blender.strategy.yaml`.

The builder lives in `tools/strategy_builders/lib.py` if you want to script other plans.

Strategies can optionally declare `settings.ultimate_targets` (array of `{label, toggles}`) to specify which Ultimate Weapons should be enforced when the run starts; each toggle entry may be a string (implying `on`) or an object `{name, state: on|off}`. Individual phases may also include `ultimate_targets` to override the list once their conditions are met.

Example command:

```
python main.py \
  --strategy-config config/strategies/blender.strategy.yaml \
  --mission-log logs/blender_mission.log
```

Bundled GC farming strategies are generated from one shared family builder and
compact Tier profiles:

- `gc` and `gc_farm_t18` select the Tier 18 profile. Its configured Target
  Priority order is verified and enforced after EHLS/EALS.
- `gc_farm_t19_experiment` selects the experimental Tier 19 profile. It runs
  EHLS/EALS but preserves the current Target Priority order without inspecting
  it or including it in the startup completion gate.

For example:

```bash
.venv/bin/python main.py --adb-port 5555 --strategy gc_farm_t19_experiment
```

The former `gc_manual_target_priority` name remains a compatibility alias for
`gc_farm_t19_experiment` during migration; it no longer seeds a completion
variable. Edit the matching `.source.yaml` profile and regenerate its explicit
`.strategy.yaml` plan with:

```bash
.venv/bin/python tools/strategy/build_strategy.py \
  config/strategies/gc_farm_t19_experiment.source.yaml
```

Both GC profiles also declare a once-per-process session preflight. After the
current run's startup gate, it uses guarded read-only navigation to verify the
GC Cards deck, Farm Workshop and Bots presets, Fetch/Summon/Scout Guardian
chips, Auto Pick Perks, and the profile's required Ultimate Weapon toggles.
Success is logged once and persists across run boundaries in that process. A
mismatch blocks normal automation and logs its evidence; it does not change a
preset, toggle, equipment, or Surrender the run.

## Battle statistics

At Game Over, the normal handler copies the complete **More Stats** battle
report through Android's clipboard service and combines it with narrowly scoped
OCR into one durable record. Each battle produces:

- `logs/battles/Battle*.json` — the authoritative versioned source record,
  including named sections/rows, exact copied text, OCR evidence, ordered
  perks, strategy/runtime context, and derived values;
- `logs/battles/Battle*.md` — a human-readable view of the same battle.

Every copied label/value row is retained by section; the schema does not limit
capture to a fixed shortlist. Validation currently requires all 16 known
sections and all 14 known Currencies rows, so a partial clipboard report cannot
silently become a valid record. The compact Game Stats dialog is OCRed only for
values absent from the copy report, including Highest Wave and the base/ad coin
breakdown. Its wave, tier, killed-by, and copied total provide cross-source
identity and coin-suffix checks.

The ordered Selected Perks list is OCRed separately. Its stored order is latest
selection first. Blue perks are recorded as leveled perks; green and purple
perks are recorded as single-instance perks. When a blue perk gains another
level, its newest observation moves that complete perk back to the top.

Every numeric row in **Currencies** gets a calculated real-time hourly rate
unless the page already provides one, as it does for Cells. Derived values also
include combined Reroll Dice/hour (earned plus fetched), total module
Shards/hour (Cannon, Armor, Generator, and Core), effective game speed, waves
per real hour, real seconds per wave, coins and cells per wave, base/ad coin
shares, death defies, estimated start time, and any discrepancy between
final-wave OCR and the runtime wave hint. Game values keep both their original
text and parsed case-sensitive magnitude (`q`, `Q`, `D`, `aa`, `ab`, and later
suffixes).

If clipboard acquisition or validation fails, the handler falls back to
overlapping, guarded OCR viewports of the scrolling More Stats page. Source
screenshots are written to `screenshots/matches/` only when capture, parsing,
or OCR validation needs evidence.
`--fast-game-over` is the explicit opt-out when a run intentionally should not
create a record.

Use `--mission-config` alongside `--strategy-config` to pair custom YAML plans.
The `--strategy` option selects a bundled named strategy, while
`--strategy-config` overrides it with an explicit YAML file. Logs for rule
firings and executor actions are written to `logs/actions.log` and mirrored to
the optional mission log path.

## Runtime pause control

Pause and resume the running process through its persistent control file:

```bash
.venv/bin/python tools/automation_ctl.py pause
.venv/bin/python tools/automation_ctl.py pause --minutes 15
.venv/bin/python tools/automation_ctl.py status
.venv/bin/python tools/automation_ctl.py resume
```

A plain `pause` is indefinite and survives automation restarts. Pass
`--minutes N` to request a timed pause instead. Its deadline is stored in the
same authoritative control file, so the supervisor persists `RUNNING` before
resuming and cannot race against a stale `PAUSED` directive.

## Development backlog

Current planned work is tracked in [`PENDING_DEVELOPMENT.md`](PENDING_DEVELOPMENT.md).
