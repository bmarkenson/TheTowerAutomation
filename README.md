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

- `farm` and `farm_t18` select the Tier 18 profile. Its configured Orb Distance
  and Target Priority presets are verified and enforced after EHLS/EALS.
- `farm_t19` selects the Tier 19 Farm profile. It uses the shared Farm perk
  profile, runs EHLS/EALS, enforces the Range-selected Orb Distance preset,
  and preserves Target Priority without inspecting or changing it.

See [`docs/game_strategy.md`](docs/game_strategy.md) for the account's
GC-with-Hybrid-aspects model, Damage Slider economics, T18/T19 Heat differences,
Dissonant coin multipliers, Spotlight targeting economics, and the evidence
standard for comparing Coins, Reroll Dice, module shards, Cells, and battle
results.

For example:

```bash
.venv/bin/python main.py --adb-port 5555 --strategy farm_t19
```

Startup now classifies the first fresh game view automatically. At verified
Home **New Battle**, normal pre-battle checks always run. If the first view is
an active battle or Home **Resume Battle**, the default policy attaches to that
battle and runs a read-only strategy validation:

```bash
.venv/bin/python main.py --adb-port 5555 --strategy farm_t18 --startup-gates auto_validate
```

Validation never repairs or restarts the attached battle on its own. A
Home-repairable mismatch publishes an operator decision that can authorize the
existing guarded restart/repair path. When the Current-run ledger contains a
matching completed-check receipt and Battle History proves that attachment
returned to that same battle, automation reuses the receipt instead of
repeating its session configuration checks. A missing or configuration-
mismatched receipt still runs validation. To attach and skip all strategy
setup checks for only that current battle, use `--startup-gates auto`. Game
Over, Tournament Results, or verified Home `NEW_BATTLE` evidence clears either
attachment choice and re-arms the complete gates for the following battle.
`immediate` remains an advanced forced-first-battle policy, while `next_run`
is reserved for the guarded process-reload workflow.

For a gate-free experiment, select the no-strategy mode explicitly:

```bash
.venv/bin/python main.py --adb-port 5555 --strategy none
```

This keeps the regular capture, detection, lifecycle, Game Over, Home, ad-gem,
Daily Gem, Daily/Event Mission reward, Guild chest, floating-gem, status, and
recovery handlers running. It loads no Farm strategy, so there are no strategy
upgrade actions, new-run initialization gate, or session-preflight gate. While
an active battle is visible, it runs one guarded read-only traversal of the
accessible configuration panels and records their actual values; it never
substitutes a familiar Farm profile for missing evidence. At natural Game Over
it captures the full battle record, returns to verified no-battle Home, reads
the Workshop preset and supported Free Upgrade locks without changing them,
opens the guarded Home Perks configuration control itself, captures First Perk,
Ban Perks, and Auto Pick order, updates the same battle JSON/Markdown, and
releases Home. See
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
  config/strategies/farm_t19.source.yaml
```

Every Farm profile inherits the same `Farm` Cards, Workshop, and Bots presets;
Shockwave Size, Bounce Shot Targets, and Bounce Shot Range Free Upgrade locks;
Fetch/Summon/Scout Guardian chips; Auto Pick Perks; and Ultimate Weapon
requirements. At a genuine `NEW_BATTLE` Home boundary, an authoritatively
unchecked Free Upgrade lock blocks Battle until the Home route corrects and
reverifies it; ambiguous lock evidence blocks without Surrendering. Poison
Swamp Stun is also verified and, when necessary, switched off from Workshop
Ultimate Upgrades before Battle. The in-battle detail route remains a
compatibility fallback only when a run lacks fresh Home-boundary proof.
Before a new battle starts, the verified no-battle Home route completes every
profile-owned check available from `NEW_BATTLE`: Cards `Farm`, Workshop `Farm`,
the three Free Upgrade locks, Poison Swamp Stun, Bots `Farm`, the supported
Attack/Ally/Scout → Fetch/Summon/Scout Guardian transition, and Modules. It
retains screen-derived configuration evidence for session preflight, which
consumes that boundary proof instead of starting the battle and returning Home
to repeat those checks. Damage Slider, Orb Distance, Target Priority, Auto Pick
Perks, Ultimate Weapon primary toggles, and game speed remain battle-only.
Unknown no-battle layouts fail closed.

Modules, Damage Slider, Orb Distance, and Target Priority are the only per-Tier
or experimental loadout fields. Each compact profile declares `enforce`,
`observe`, or `preserve` for all four. `enforce` blocks on mismatch and may use
an explicit safe repair path; `observe` requires authoritative evidence but
accepts confident differences without blocking or changing the setting;
`preserve` neither inspects nor changes it. Modules, Orb Distance,
and Target Priority resolve named presets at build time. Orb Distance presets
bind an Extra/Workshop pair to an expected Attack Range; automation refuses to
apply the pair unless fresh Range OCR matches that basis. Tier 18 and the
Tier 19 Farm profile both select the configured pair for observed
Range `30.00m` or `98.38m` and preserve any other readable experimental
Range. Damage Slider profiles use an explicit percentage; Tier 18 enforces
`1E-22%` during every new-run initialization after EHLS/EALS. The fully
resolved configuration is embedded in the generated plan and copied into each
battle's JSON record.

When an enforced module repair replaces an occupied Primary or Assist module,
automation always accepts the presented level transfer and requires the dialog
to dismiss before continuing. This preserves the slot's existing level instead
of leaving the incoming module at its previous level.

While a battle is active, the game-speed guard periodically reads the visible
control and enforces the persistent numeric target. Targets from `x0.0` through
`x6.0` are exact. The `x6.3` selection means maximum available: at `x5.0` the
guard verifies the current ceiling with one `+` tap rather than assuming that
the perk is absent; no change proves the no-perk maximum, while an active perk
advances the control toward `x6.3`. Every tap is followed by fresh OCR, and a
target change or Pause is rechecked before the next input. Farm gives the
urgent EHLS/EALS purchases first action priority; other profiles may enforce
speed immediately after `RUNNING` is verified.

### Validating Tournament setup

Select the `tournament` strategy while the game is at verified Home **New
Battle** to run the pre-start Tournament setup. The Home route selects
Tournament Cards, Tourney Workshop, Amplify Bots, Attack/Ally/Scout Guardians,
and the Tournament module inventory. Modules are observed against the
`tournament_standard` reference without being changed or blocking a confident
variation; the other settings remain enforced. It retains that evidence,
leaves Tournament entry manual, and never presses the normal Battle control.
Once the Tournament starts, session preflight consumes the Home evidence and
checks only all nine Ultimate Weapons plus Spotlight missiles. Tournaments
have no Perks.

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
Resume evidence. A confidently identified Module variation is recorded against
the reference and passes; an enforced-setting mismatch or incomplete Module
identity evidence exits nonzero. Pass
`--capture-only --output-dir PATH` to retain the same guarded screens without
evaluating them.

To keep observing that Tournament after the check, start the passive profile:

```bash
.venv/bin/python main.py --adb-port 5555 --strategy tournament --no-restart
```

The Tournament profile attempts the same read-only validation once, records a
conclusive pass or mismatch as session evidence, including when attaching to
an already-running Tournament. A confident Module variation is named in the
successful result and retained without warning. An attached invariant mismatch
is logged and retained
without publishing a gate decision, blocking observation, or repeating the
inventory pass. The profile then limits runtime action authority to ad gems and
the natural terminal handler. It does not buy upgrades, repair configuration,
Surrender, auto-return Home, enter a Tournament, or start a normal battle.
Terminal records identify Tournament from the distinct Tournament Results
screen and retain the Tier observed in terminal stats even when no reliable
strategy identity was attached. A standard Game Over with no strategy remains
type `unknown` when Tier is the only identity evidence; Tier alone is not used
to invent Farm or Milestone identity. A
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

Select the persistent game-speed target independently of Pause:

```bash
.venv/bin/python tools/automation_ctl.py game-speed 4.0
.venv/bin/python tools/automation_ctl.py game-speed max
```

Exact targets are available in `x0.5` increments from `x0.0` through `x6.0`;
`max` selects the `x6.3` maximum-available policy. A custom exact target applies
to current and future battles until changed. The runtime warns immediately and
every 15 minutes while one remains active. Completed records retain the target,
its semantics, and the per-battle target timeline alongside derived effective
game speed.

A standalone native Windows GUI now exposes these controls, managed automation
start/stop, runtime health, recent activity, filters, and structured completed
Battle/Tournament records through a loopback Linux service. It can own
independent passwordless OpenSSH processes for the API tunnel and the
loopback-only ADB reverse forward; the browser client remains available as a
fallback. See the
[`native Windows control surface`](docs/runtime_operations.md#native-windows-control-surface)
procedure.

## Development backlog

Current planned work is tracked in [`PENDING_DEVELOPMENT.md`](PENDING_DEVELOPMENT.md).
