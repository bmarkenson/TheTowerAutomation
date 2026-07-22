# TheTower Automation

This repository drives automated gameplay loops for *The Tower: Idle Tower Defense*. Runtime entry point is `main.py`, which wires detection, missions, and strategies.

## Starting a development thread

Agents and developers should begin with [`AGENTS.md`](AGENTS.md) and the
canonical [`docs/new_thread.md`](docs/new_thread.md) entrypoint. It links the
runtime runbook, observed-issue ledger, architecture direction, and current
backlog. Stable operational guidance belongs there; every handoff should add
only freshly inspected volatile process and battle state.

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

Bundled Farm strategies are generated from a shared invariant baseline, named
loadout catalogs, and compact Tier profiles:

- `farm` and `farm_t18` select the Tier 18 profile. Its configured Target
  Priority order is verified and enforced after EHLS/EALS.
- `farm_t19_experiment` selects the experimental Tier 19 profile. It runs
  EHLS/EALS but preserves the current Target Priority order without inspecting
  it or including it in the startup completion gate.

For example:

```bash
.venv/bin/python main.py --adb-port 5555 --strategy farm_t19_experiment
```

To replace automation while retaining the battle already in progress, use the
explicit next-run startup policy:

```bash
.venv/bin/python main.py --adb-port 5555 --strategy farm_t18 --startup-gates next_run
```

This attaches to the existing/resumable battle and suppresses only tagged
new-run initialization and session-preflight rules. Normal automation remains
active. The Tournament observer is the narrow exception: its explicitly
read-only preflight runs on attachment so mismatches can be reported without
repair authority. Game Over, Tournament Results, or verified Home `NEW_BATTLE`
evidence re-arms the gates for the following battle. The default `immediate`
policy retains normal first-battle startup gating.

For a gate-free experiment, select the no-strategy mode explicitly:

```bash
.venv/bin/python main.py --adb-port 5555 --strategy none
```

This keeps the regular capture, detection, lifecycle, Game Over, Home, ad-gem,
Daily Gem, Daily/Event Mission reward, Guild chest, floating-gem, status, and
recovery handlers running. It loads no Farm strategy, so there are no strategy
upgrade actions, new-run initialization gate, or session-preflight gate. While
an active battle is visible, it passively records actual settings from any
configuration panel the operator opens; it never substitutes a familiar Farm
profile for missing evidence. At natural Game Over it captures the full battle
record, returns to verified no-battle Home, reads the supported Free Upgrade
locks without changing them, and holds the next-battle path on Cards until the
operator opens Perks configuration. It then captures First Perk, Ban Perks, and
Auto Pick order, updates the same battle JSON/Markdown, and releases Home. See
[`docs/runtime_operations.md`](docs/runtime_operations.md#no-strategy-run-inventory)
for the exact workflow. The default remains `farm` when `--strategy` is
omitted.

Mission and Guild reward collection uses the in-run menu's attention dot only
as a reason to inspect. After opening the menu, the handler independently
measures the Daily Missions and Event badge slots and anchors the Guild badge
region to the freshly matched Guild icon, including when an active Tournament
moves that icon. Every reward tap then requires its parent screen plus exact
available artwork: Daily `CLAIM`, an unclaimed weekly chest, Event `CLAIM`, or
a glowing Guild contribution chest.
Claimed and locked chests are negatives. Reward reveals are dismissed through
their verified `SKIP` control, Event Missions are searched with bounded guarded
scrolling, and the handler returns to the active battle and closes the menu.
Persistent unrelated alerts are rate-limited to one inspection every 30
minutes; failures retry after five minutes.

Ordinary Daily Mission claims are banked on local Sunday until the server's
Monday 00:00 UTC weekly reset (17:00 PDT / 16:00 PST). Glowing weekly mission
chests, Event rewards, and Guild chests remain collectible during that hold.

The former `gc`, `gc_farm_*`, `glass_cannon`, `gc_skipper`, and
`gc_manual_target_priority` names remain compatibility aliases. Edit the
matching compact Farm source and regenerate its explicit `.strategy.yaml` plan
with:

```bash
.venv/bin/python tools/strategy/build_strategy.py \
  config/strategies/farm_t19_experiment.source.yaml
```

Every Farm profile inherits the same `Farm` Cards, Workshop, and Bots presets;
Shockwave Size, Bounce Shot Targets, and Bounce Shot Range Free Upgrade locks;
Fetch/Summon/Scout Guardian chips; Auto Pick Perks; and Ultimate Weapon
requirements. During session preflight, an authoritatively unchecked Free
Upgrade lock is a Home-only mismatch that requests the guarded
Surrender/repair/restart sequence; ambiguous lock evidence blocks without
Surrendering. Poison Swamp Stun is the narrow in-battle repair exception:
when its detail screen authoritatively shows Stun on, preflight switches it off
and verifies the result without Surrendering or leaving the active run.
Before a new battle starts, the verified no-battle Home route completes every
profile-owned check available from `NEW_BATTLE`: Cards `Farm`, Workshop `Farm`,
the three Free Upgrade locks, Bots `Farm`, the supported
Attack/Ally/Scout → Fetch/Summon/Scout Guardian transition, Modules, and
Target Priority. It retains screen-derived configuration evidence for session
preflight, which consumes that boundary proof instead of starting the battle
and returning Home to repeat those checks. Unknown no-battle layouts fail
closed.

Modules, Damage Slider, and Target Priority are the only per-Tier or
experimental loadout fields. Each compact profile declares `enforce`, `observe`,
or `preserve` for all three. `enforce` blocks on mismatch and may use an
explicit safe repair path; `observe` records evidence without blocking or
changing it; `preserve` neither inspects nor changes it. Modules and Target
Priority resolve named presets at build time. Damage Slider profiles use an
explicit percentage; Tier 18 enforces `1E-22%` during every new-run
initialization after EHLS/EALS. The fully resolved configuration is embedded in
the generated plan and copied into each battle's JSON record.

### Validating Tournament setup

Select the `tournament` strategy while the game is at verified Home **New
Battle** to run the pre-start Tournament setup. The Home route selects
Tournament Cards, Tourney Workshop, Amplify Bots, Attack/Ally/Scout Guardians,
and the Tournament module loadout. It retains that evidence, leaves Tournament
entry manual, and never presses the normal Battle control. Once the Tournament
starts, session preflight consumes the Home evidence and checks only all nine
Ultimate Weapons plus Spotlight missiles. Tournaments have no Perks.

The standalone Tournament validator remains a read-only live test for an
already active Tournament. It checks the same contract without changing it.

Pause persistent automation intent before running the test, then restore the
previous control state after it returns to the same battle:

```bash
.venv/bin/python tools/automation_ctl.py pause
.venv/bin/python tools/validate_tournament_preflight.py
.venv/bin/python tools/automation_ctl.py resume
```

The validator refuses to navigate unless the control file declares `PAUSED`
and a fresh complete screenshot proves `RUNNING/TOURNAMENT`. It never selects a
preset, equips a module, or Surrenders. Cards, Ultimate Weapons, Modules, Bots,
and Guardians are inspected from the active battle. Only Workshop uses the
guarded Exit Battle → Go Home route; the validator then requires verified
Resume evidence and exits nonzero on any mismatch or incomplete evidence. Pass
`--capture-only --output-dir PATH` to retain the same guarded screens without
evaluating them.

To keep observing that Tournament after the check, start the passive profile:

```bash
.venv/bin/python main.py --adb-port 5555 --strategy tournament --no-restart
```

The Tournament profile attempts the same read-only validation once, records a
conclusive pass or mismatch as session evidence, including when attaching to
an already-running Tournament. An attached mismatch publishes a non-blocking
warning with choices to pause for manual changes, retry the read-only check, or
continue observing with only that mismatch waived. Leaving the warning pending
does not block natural Tournament Results/Game Over capture. The profile then
limits runtime action authority to ad gems and the natural terminal handler. It
does not buy upgrades, repair configuration, Surrender, auto-return Home, enter
a Tournament, or start a normal battle. Terminal records identify Tournament
from the distinct Tournament Results screen and retain the Tier observed in
terminal stats even when no reliable strategy identity was attached. A standard
Game Over with no strategy remains type `unknown` when Tier is the only identity
evidence; Tier alone is not used to invent Farm or Milestone identity. A
localized Attack Dissonance sword badge is independent observed identity
evidence, so a no-strategy Game Over carrying that evidence is classified as
`dissonance`.

## Battle statistics

At Game Over, the normal handler copies the complete **More Stats** battle
report through Android's clipboard service and combines it with narrowly scoped
OCR into one durable record. Each battle produces:

- `logs/battles/Battle*.json` — the authoritative versioned source record,
  including named sections/rows, exact copied text, OCR evidence, ordered
  perks, strategy/runtime context, resolved run configuration, separately
  sourced observed run configuration, derived values, and
  timestamped/wave-indexed Coins/min progression samples;
- `logs/battles/Battle*.md` — a human-readable view of the same battle,
  including the resolved loadout policies, presets, and values.

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

The periodic live status sample remains the source for Coins/min in the
operational display. Valid numeric samples are accumulated for the current
battle and folded into its JSON and Markdown record at the terminal boundary,
so there is no separate per-run Coins CSV or scheduled lifetime-total display
toggle. An attached replacement process records the portion of the battle it
observes.

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

A standalone native Windows GUI now exposes these controls, managed automation
start/stop, runtime health, recent activity, filters, and structured completed
Battle/Tournament records through a loopback Linux service. It can own the
passwordless OpenSSH tunnel itself; the browser client remains available as a
fallback. See the
[`native Windows control surface`](docs/runtime_operations.md#native-windows-control-surface)
procedure.

## Development backlog

Current planned work is tracked in [`PENDING_DEVELOPMENT.md`](PENDING_DEVELOPMENT.md).
