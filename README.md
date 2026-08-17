# TheTower Automation

This repository drives automated gameplay loops for *The Tower: Idle Tower Defense*. Runtime entry point is `main.py`, which wires detection, missions, and strategies.

## Starting a development thread

Codex loads [`AGENTS.md`](AGENTS.md) automatically. Every TheTower thread reads
the [`docs/new_thread.md`](docs/new_thread.md) router and selects its smallest
applicable path. Use a handoff only when responsibility moves to another
top-level chat; stable state stays in its canonical repository owner.

## Running with YAML strategies

Strategies can now be authored in YAML and loaded at runtime. The Blender upgrade loop has been ported to `config/strategies/blender.strategy.yaml`.

### Editing the Blender plan

The runtime YAML is generated from a compact source file so you don’t have to hand-edit the large rule set.

1. Update `config/strategies/blender.source.yaml` (ordered list of targets, settings).
2. Regenerate the expanded strategy:

   ```bash
   .venv/bin/python tools/strategy/build_strategy.py \
     config/strategies/blender.source.yaml
   ```

   The source is positional; use optional `--output` to override the generated
   plan path.
3. Commit both the source and regenerated `config/strategies/blender.strategy.yaml`.

The supported CLI is `tools/strategy/build_strategy.py`; its implementation is
`tools/strategy_builders/build_strategy.py`, and reusable generation lives in
`tools/strategy_builders/lib.py`. See the
[YAML strategy reference](docs/reference/yaml_strategy.md) for plan ownership.

Strategies can optionally declare `settings.ultimate_targets` (array of `{label, toggles}`) to specify which Ultimate Weapons should be enforced when the run starts; each toggle entry may be a string (implying `on`) or an object `{name, state: on|off}`. Individual phases may also include `ultimate_targets` to override the list once their conditions are met.

Example command:

```bash
.venv/bin/python main.py \
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

Validation never repairs, restarts, or Surrenders the attached battle on its
own. A mismatch is logged as degraded evidence and the battle continues. If
**Continue automatically** is already selected when that degraded battle ends,
terminal handling returns Home instead of tapping Retry, runs the next
profile's normal bounded setup, and then continues. Exhausted Home repair is
flagged and the next battle still starts degraded; it never becomes a global
Pause. When the Current-run ledger
contains a matching completed-check receipt and Battle History proves that
attachment returned to that same battle, automation reuses the receipt instead
of repeating its session configuration checks. A missing or configuration-
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
[`docs/operations/no_strategy.md`](docs/operations/no_strategy.md)
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
A productive sweep can inspect again after two minutes so newly exposed
rewards are not stranded. A sweep that finds nothing claimable backs persistent
unrelated alerts off for 30 minutes; failures retry after five minutes.

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
unchecked Free Upgrade lock is corrected and reverified when possible;
ambiguous or exhausted repair is flagged and releases Battle launch in
degraded mode without Surrendering. Poison
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
Unknown no-battle layouts skip unsupported correction, retain diagnostic
evidence, and continue degraded.

Modules, Damage Slider, Orb Distance, and Target Priority are the only per-Tier
or experimental loadout fields. Each compact profile declares `enforce`,
`observe`, or `preserve` for all four. `enforce` repairs at an already-safe
boundary or records a degraded mismatch; `observe` requires authoritative
evidence but accepts confident differences without blocking or changing the
setting; `preserve` neither inspects nor changes it. Modules, Orb Distance,
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

### Tournament setup and observation

The `tournament` strategy enforces its Home setup, leaves Tournament entry
manual, then performs read-only session validation and passive observation.
It never buys upgrades, repairs an attached battle, Surrenders, auto-returns
Home, or starts a battle. Use the
[Tournament operation](docs/operations/tournament_validation.md) for a live
validation or attachment and the
[runtime architecture](docs/architecture/runtime.md#tournament-exclusive-validation-and-observer-profile)
for the authority and evidence contract.

## Battle statistics

At Game Over, the normal handler reuses the same stable exact-target player-save
read that captures profile progression. When the newest `battleHistory` tail is
one valid append or capped rollover beyond this run's same-source baseline, the
terminal is bound to the current process, and compact terminal identity does
not contradict it, the save's complete **More Stats** projection is primary.
No More Stats panel is opened on that path. Each battle produces:

- `logs/battles/Battle*.json` — the authoritative versioned source record,
  including named sections/rows, exact versioned save values or explicit UI
  fallback provenance, OCR evidence, ordered perks, strategy/runtime context,
  resolved run configuration, separately sourced observed run configuration,
  derived values, and
  timestamped/wave-indexed Coins/min progression samples;
- `logs/battles/Battle*.md` — a human-readable view of the same battle,
  including the resolved loadout policies, presets, and values.

The exact versioned projection requires all 16 known sections, all 144 report
rows, and all 14 known Currencies rows; partial or changed shapes cannot
silently become a valid record. The compact Game Stats dialog remains a passive
augmentation for values absent from the save, including Highest Wave and the
base/ad coin breakdown. Its available wave, tier, and killed-by values are also
cross-source checks. Missing augmentation does not invalidate an otherwise
authoritative save-derived report, while a contradiction forces the UI route.

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

The native Windows status also consumes the latest already-accepted passive
save checkpoint without causing another acquisition. It keeps whole-run and
interval realized CPH distinct from OCR Coins/min and shows whole-run
cells/hour, waves/hour, effective speed, plus checkpoint provenance only while
fresh exact runtime ownership and the same forced-save round identity still
prove the active battle. A mismatched save source, identity transition, or
lost status connection hides the live row instead of retaining old values.

An unbound terminal, absent or UI-sourced baseline, unsupported version,
unchanged or invalid tail transition, unknown death cause, changed shape, or
identity contradiction preserves the existing More Stats fallback. That route
copies and validates the complete report through Android's clipboard service;
if clipboard acquisition also fails, it uses overlapping guarded OCR viewports.
Tournament Results uses the same save-first/fallback policy. Source screenshots
are written to `screenshots/matches/` only when capture, parsing, or OCR
validation needs evidence.
`--fast-game-over` is the explicit opt-out when a run intentionally should not
create a record.

Use `--mission-config` alongside `--strategy-config` to pair custom YAML plans.
The `--strategy` option selects a bundled named strategy, while
`--strategy-config` overrides it with an explicit YAML file. Logs for rule
firings and executor actions are written to `logs/actions.log` and mirrored to
the optional mission log path.

## Runtime control

Pause, terminal mode, game speed, and process replacement use the persistent
control boundary described in
[`docs/operations/process_control.md`](docs/operations/process_control.md).
Managed start, target, and Strategy operations are routed through
[`docs/runtime_operations.md`](docs/runtime_operations.md). The
[native Windows control surface](windows/TheTower.ControlSurface/README.md)
provides the primary GUI; the browser remains a fallback.

## Development backlog

Current planned work is tracked in [`PENDING_DEVELOPMENT.md`](PENDING_DEVELOPMENT.md).
